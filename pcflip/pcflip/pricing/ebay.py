"""eBay Browse API price feed (official, free, application token only).

We can't get SOLD prices from the public API (Marketplace Insights is partner-only), so we
pull ACTIVE fixed-price USED listings, filter titles hard, trim outliers, and treat the
lower quartile of asks as a proxy for the sold median (asks sit above clears). The factor
is `economics.active_to_sold_factor`. Override with Terapeak numbers in reference_prices.yaml.

Docs: https://developer.ebay.com/api-docs/buy/browse/resources/item_summary/methods/search
"""
from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from typing import Any, Iterable

import httpx

from ..config import Economics, Settings
from ..db import Database
from ..watchlist import Sku, Watchlist, normalize
from .stats import summarize

HOSTS = {
    "production": "https://api.ebay.com",
    "sandbox": "https://api.sandbox.ebay.com",
}
SCOPE = "https://api.ebay.com/oauth/api_scope"


class EbayError(RuntimeError):
    pass


@dataclass
class EbayCredentials:
    client_id: str
    client_secret: str
    env: str = "production"

    @classmethod
    def from_settings(cls, settings: Settings) -> "EbayCredentials | None":
        cid, sec = settings.env("EBAY_CLIENT_ID"), settings.env("EBAY_CLIENT_SECRET")
        if not cid or not sec:
            return None
        return cls(cid, sec, (settings.env("EBAY_ENV", "production") or "production").lower())


class EbayBrowse:
    def __init__(self, creds: EbayCredentials, marketplace_id: str = "EBAY_US", client: httpx.Client | None = None):
        self.creds = creds
        self.marketplace_id = marketplace_id
        self.base = HOSTS.get(creds.env, HOSTS["production"])
        self.client = client or httpx.Client(timeout=30.0)
        self._token: str | None = None
        self._token_expiry = 0.0

    # ---- auth --------------------------------------------------------------------
    def token(self) -> str:
        if self._token and time.time() < self._token_expiry - 60:
            return self._token
        basic = base64.b64encode(f"{self.creds.client_id}:{self.creds.client_secret}".encode()).decode()
        r = self.client.post(
            f"{self.base}/identity/v1/oauth2/token",
            headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials", "scope": SCOPE},
        )
        if r.status_code != 200:
            raise EbayError(f"token request failed: {r.status_code} {r.text[:200]}")
        body = r.json()
        self._token = body["access_token"]
        self._token_expiry = time.time() + float(body.get("expires_in", 7200))
        return self._token

    # ---- search ------------------------------------------------------------------
    def search(self, query: str, category_id: str | None = None, price_range: tuple[float, float] | None = None,
               limit: int = 100, condition: str = "USED") -> list[dict[str, Any]]:
        filters = [f"conditions:{{{condition}}}", "buyingOptions:{FIXED_PRICE}", "priceCurrency:USD",
                   "deliveryCountry:US", "itemLocationCountry:US"]
        if price_range:
            lo, hi = price_range
            filters.append(f"price:[{int(lo)}..{int(hi)}]")
        params: dict[str, Any] = {"q": query, "limit": str(min(limit, 200)), "filter": ",".join(filters), "sort": "price"}
        if category_id:
            params["category_ids"] = str(category_id)
        items: list[dict[str, Any]] = []
        offset = 0
        while len(items) < limit:
            params["offset"] = str(offset)
            r = self.client.get(
                f"{self.base}/buy/browse/v1/item_summary/search",
                params=params,
                headers={"Authorization": f"Bearer {self.token()}", "X-EBAY-C-MARKETPLACE-ID": self.marketplace_id,
                         "Accept": "application/json"},
            )
            if r.status_code == 429:
                raise EbayError("rate limited by eBay (429); try again later")
            if r.status_code != 200:
                raise EbayError(f"search failed ({r.status_code}): {r.text[:300]}")
            body = r.json()
            page = body.get("itemSummaries", []) or []
            items.extend(page)
            if not page or len(page) < int(params["limit"]) or "next" not in body:
                break
            offset += len(page)
        return items[:limit]


# ---------------------------------------------------------------------------- filtering
def title_ok(title: str, sku: Sku) -> bool:
    t = normalize(title)
    for m in sku.ebay.get("must", []):
        if normalize(m) not in t:
            return False
    for m in sku.ebay.get("must_not", []):
        if normalize(m) in t:
            return False
    # the SKU's own recognizer must fire (keeps '3090 ti' out of '3090', etc.)
    if sku.patterns and not sku.matches(t):
        return False
    return True


def item_total_price(item: dict[str, Any]) -> float | None:
    """Item price + cheapest shipping (0 if free). Returns None when unparseable."""
    try:
        price = float(item["price"]["value"])
    except (KeyError, TypeError, ValueError):
        return None
    ship = 0.0
    for opt in item.get("shippingOptions", []) or []:
        cost = (opt or {}).get("shippingCost", {}) or {}
        try:
            c = float(cost.get("value", 0))
        except (TypeError, ValueError):
            c = 0.0
        ship = c if ship == 0.0 else min(ship, c)
    return price + ship


def prices_for_sku(items: Iterable[dict[str, Any]], sku: Sku) -> tuple[list[float], list[str]]:
    prices, kept_titles = [], []
    for it in items:
        title = it.get("title") or ""
        if not title_ok(title, sku):
            continue
        p = item_total_price(it)
        if p is None:
            continue
        prices.append(p)
        kept_titles.append(title)
    return prices, kept_titles


def estimate_sold(stats: dict[str, Any], econ: Economics) -> float | None:
    """Active p25 x factor. With a thin sample we lean on the median instead (p25 of 3 asks is noise)."""
    if not stats.get("n"):
        return None
    if stats["n"] >= 8 and stats.get("p25"):
        return round(stats["p25"] * econ.active_to_sold_factor, 2)
    if stats.get("median"):
        return round(stats["median"] * econ.active_to_sold_factor * 0.97, 2)
    return None


# ---------------------------------------------------------------------------- job
def refresh_prices(settings: Settings, watchlist: Watchlist, db: Database, api: EbayBrowse | None = None,
                   only: Iterable[str] | None = None, log=print) -> dict[str, Any]:
    """Pull every SKU's active comps and store a snapshot. Returns {sku: est_sold|None}."""
    econ = settings.economics
    if api is None:
        creds = EbayCredentials.from_settings(settings)
        if creds is None:
            raise EbayError("EBAY_CLIENT_ID / EBAY_CLIENT_SECRET not set")
        api = EbayBrowse(creds, settings.ebay.get("marketplace_id", "EBAY_US"))
    limit = int(settings.ebay.get("limit_per_query", 100))
    wanted = set(only) if only else None
    results: dict[str, Any] = {}
    for sku in watchlist.skus:
        if wanted and sku.key not in wanted:
            continue
        q = sku.ebay.get("query")
        if not q:
            continue
        pr = sku.ebay.get("price_range")
        try:
            items = api.search(q, sku.ebay.get("ebay_category_id"), tuple(pr) if pr else None, limit=limit)
        except EbayError as e:
            log(f"  {sku.key}: {e}")
            results[sku.key] = None
            if "429" in str(e):
                break
            continue
        prices, titles = prices_for_sku(items, sku)
        stats = summarize(prices)
        est = estimate_sold(stats, econ)
        db.insert_price(sku.key, "ebay_active", stats, est,
                        {"query": q, "raw_count": len(items), "kept": len(prices), "sample_titles": titles[:5]})
        results[sku.key] = est
        log(f"  {sku.key:24s} n={stats['n']:3d} p25={stats['p25']!s:>9} median={stats['median']!s:>9} -> est sold {est}")
        time.sleep(0.2)  # be polite; app tokens get 5k calls/day
    return results


def export_snapshot(db: Database, watchlist: Watchlist, resolver, path) -> dict[str, Any]:
    """Write data/prices.json: one resolved price per SKU plus the latest raw eBay stats."""
    from ..db import utcnow
    latest = {(r["sku"], r["source"]): dict(r) for r in db.latest_prices()}
    out: dict[str, Any] = {"generated_at": utcnow(), "skus": {}}
    for sku in watchlist.skus:
        pp = resolver.resolve(sku.key)
        raw = latest.get((sku.key, "ebay_active"))
        entry: dict[str, Any] = {
            "name": sku.name, "category": sku.category,
            "est_sold": pp.est_sold if pp else None, "source": pp.source if pp else None,
            "confidence": pp.confidence if pp else None, "as_of": pp.as_of if pp else None,
        }
        if raw:
            entry["ebay_active"] = {k: raw.get(k) for k in ("observed_at", "median", "p25", "p75", "low", "high", "sample_size")}
            try:
                entry["ebay_active"]["detail"] = json.loads(raw.get("detail") or "{}")
            except json.JSONDecodeError:
                pass
        out["skus"][sku.key] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2, sort_keys=False), encoding="utf-8")
    return out

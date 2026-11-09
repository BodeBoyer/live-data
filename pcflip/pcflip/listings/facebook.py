"""Facebook Marketplace via Playwright, using YOUR logged-in browser session.

Facebook has no public Marketplace API and its terms prohibit automated collection. This
module exists for personal, low-volume monitoring of searches you would otherwise refresh
by hand. Poll slowly (5+ minutes), keep radius tight, and expect to re-run `pcflip fb-login`
when the session expires. Nothing here bypasses authentication.

Two parsers, because Facebook's HTML changes constantly:
  1. GraphQL interception: Marketplace loads results through /api/graphql/ responses that
     contain `marketplace_listing_title`, `listing_price`, `id`, etc. We walk every JSON
     payload recursively and collect anything listing-shaped. Robust to layout changes.
  2. DOM fallback: anchors whose href contains /marketplace/item/<id>/, with the card's
     text lines (price, title, location). Used when interception yields nothing.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.parse import quote_plus

from ..config import SearchSpec, Settings
from .models import Listing, parse_price

ITEM_HREF_RE = re.compile(r"/marketplace/item/(\d+)")

# One line of text per leaf element, so "$1,100 / title / city" never run together like innerText can.
CARD_JS = """() => Array.from(document.querySelectorAll('a[href*="/marketplace/item/"]')).map(a => {
  const parts = [];
  a.querySelectorAll('*').forEach(el => {
    const t = Array.from(el.childNodes).filter(n => n.nodeType === 3).map(n => n.textContent.trim()).filter(Boolean).join(' ');
    if (t) parts.push(t);
  });
  const img = a.querySelector('img');
  return { href: a.getAttribute('href'), text: parts.length ? parts.join('\\n') : a.innerText, img: img ? img.src : null };
})"""
MARKETPLACE_BASE = "https://www.facebook.com/marketplace"


def search_url(spec: SearchSpec) -> str:
    loc = spec.location.strip("/") or "category"
    url = f"{MARKETPLACE_BASE}/{loc}/search?query={quote_plus(spec.query)}&sortBy=creation_time_descend&exact=false"
    if spec.min_price is not None:
        url += f"&minPrice={int(spec.min_price)}"
    if spec.max_price is not None:
        url += f"&maxPrice={int(spec.max_price)}"
    if spec.radius_km:
        url += f"&radius={int(spec.radius_km)}"
    return url


# ---------------------------------------------------------------------------- parsers (pure, testable)
def _walk(obj: Any) -> Iterator[dict[str, Any]]:
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _first(d: dict[str, Any], *paths: str) -> Any:
    for path in paths:
        cur: Any = d
        ok = True
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        if ok and cur not in (None, ""):
            return cur
    return None


def listings_from_graphql(payload: Any) -> list[Listing]:
    """Collect listing-shaped nodes from any Marketplace GraphQL JSON."""
    out: dict[str, Listing] = {}
    for node in _walk(payload):
        if "marketplace_listing_title" not in node and "listing_price" not in node:
            continue
        lid = str(node.get("id") or _first(node, "listing.id") or "")
        if not lid.isdigit():
            continue
        title = _first(node, "marketplace_listing_title", "custom_title", "listing_title") or ""
        price_raw = _first(node, "listing_price.amount", "listing_price.formatted_amount", "listing_price.amount_with_offset")
        price = parse_price(price_raw)
        if price is not None and isinstance(price_raw, str) and price_raw.isdigit() and "amount_with_offset" in json.dumps(node)[:0]:
            price = price / 100.0
        location = _first(node, "location.reverse_geocode.city_page.display_name", "location.reverse_geocode.city",
                          "location_text.text", "location_vanity_or_id")
        image = _first(node, "primary_listing_photo.image.uri", "primary_listing_photo.listing_image.uri", "listing_photos.0.image.uri")
        desc = _first(node, "redacted_description.text", "description.text", "marketplace_listing_description")
        created = _first(node, "creation_time")
        if not title and not price_raw:
            continue
        out[lid] = Listing(
            source="facebook", listing_id=lid, title=str(title), price=price, description=desc,
            url=f"{MARKETPLACE_BASE}/item/{lid}/", location=str(location) if location else None,
            image_url=image, posted_at=str(created) if created else None,
            raw={k: node.get(k) for k in ("marketplace_listing_title", "listing_price", "creation_time") if k in node},
        )
    return list(out.values())


def listings_from_cards(cards: Iterable[dict[str, Any]]) -> list[Listing]:
    """cards: [{'href': ..., 'text': 'line\\nline\\n...', 'img': ...}] scraped from search-result anchors."""
    out: dict[str, Listing] = {}
    for card in cards:
        m = ITEM_HREF_RE.search(card.get("href") or "")
        if not m:
            continue
        lid = m.group(1)
        lines = [ln.strip() for ln in (card.get("text") or "").split("\n") if ln.strip()]
        price = None
        title = None
        location = None
        for ln in lines:
            if price is None and (ln.startswith("$") or ln.lower() == "free" or re.match(r"^\$?\d[\d,]*$", ln)):
                price = parse_price(ln)
                continue
            if title is None and price is not None:
                title = ln
                continue
            if title is not None and location is None and len(ln) < 60:
                location = ln
                break
        if title is None and lines:
            title = lines[0]
        if not title:
            continue
        out[lid] = Listing("facebook", lid, title, price, None, f"{MARKETPLACE_BASE}/item/{lid}/", location, card.get("img"))
    return list(out.values())


def description_from_item_text(body_text: str) -> str | None:
    """Best-effort: the longest paragraph on an item page is almost always the description."""
    if not body_text:
        return None
    paras = [p.strip() for p in re.split(r"\n{2,}|\n", body_text) if len(p.strip()) > 40]
    junk = re.compile(r"(see more|message|send seller|facebook|marketplace|log in|sign up|cookies|privacy|terms)", re.I)
    cands = [p for p in paras if not junk.search(p[:40])]
    if not cands:
        return None
    cands.sort(key=len, reverse=True)
    return cands[0][:4000]


# ---------------------------------------------------------------------------- browser
class FacebookScraper:
    def __init__(self, settings: Settings, headless: bool | None = None, debug_dir: Path | None = None):
        self.settings = settings
        self.state_path = settings.fb_state_path
        self.headless = settings.facebook.get("headless", True) if headless is None else headless
        self.debug_dir = debug_dir
        self.scan_item_pages = bool(settings.facebook.get("scan_item_pages", True))
        self.max_item_pages = int(settings.facebook.get("max_item_pages_per_scan", 25))

    def _launch(self, p, headless: bool):
        browser = p.chromium.launch(headless=headless, args=["--disable-blink-features=AutomationControlled"])
        ctx_kwargs: dict[str, Any] = {
            "viewport": {"width": 1366, "height": 900},
            "locale": "en-US",
            "user_agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/130.0.0.0 Safari/537.36"),
        }
        if self.state_path.exists():
            ctx_kwargs["storage_state"] = str(self.state_path)
        context = browser.new_context(**ctx_kwargs)
        return browser, context

    def login(self) -> None:
        """Open a visible browser, let the user log in, then persist cookies to state_path."""
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser, context = self._launch(p, headless=False)
            page = context.new_page()
            page.goto("https://www.facebook.com/login", wait_until="domcontentloaded")
            print("Log in to Facebook in the browser window (complete any 2FA). Waiting up to 5 minutes...")
            deadline = time.time() + 300
            while time.time() < deadline:
                if "login" not in page.url and context.cookies("https://www.facebook.com") and \
                        any(c["name"] == "c_user" for c in context.cookies("https://www.facebook.com")):
                    break
                time.sleep(2)
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=str(self.state_path))
            print(f"Session saved to {self.state_path}")
            browser.close()

    def logged_in(self, context) -> bool:
        return any(c.get("name") == "c_user" for c in context.cookies("https://www.facebook.com"))

    def scan(self, specs: list[SearchSpec], known_ids: set[str] | None = None) -> list[Listing]:
        """Run every search once; returns listings (new ones get descriptions when scan_item_pages is on)."""
        from playwright.sync_api import sync_playwright
        known_ids = known_ids or set()
        found: dict[str, Listing] = {}
        with sync_playwright() as p:
            browser, context = self._launch(p, headless=bool(self.headless))
            page = context.new_page()
            captured: list[Any] = []

            def on_response(resp):
                try:
                    if "/api/graphql" in resp.url and resp.request.method == "POST":
                        text = resp.text()
                        for chunk in text.split("\n"):
                            chunk = chunk.strip()
                            if chunk.startswith("{"):
                                captured.append(json.loads(chunk))
                except Exception:
                    pass

            page.on("response", on_response)
            for spec in specs:
                captured.clear()
                url = search_url(spec)
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(2500)
                    if "login" in page.url or not self.logged_in(context):
                        raise RuntimeError("not logged in: run `pcflip fb-login`")
                    page.mouse.wheel(0, 2500)
                    page.wait_for_timeout(1500)
                except Exception as e:
                    if "not logged in" in str(e):
                        browser.close()
                        raise
                    print(f"[facebook] {spec.query!r}: {e}")
                    continue
                batch = listings_from_graphql(captured)
                if not batch:
                    cards = page.evaluate(CARD_JS)
                    batch = listings_from_cards(cards)
                if self.debug_dir:
                    self.debug_dir.mkdir(parents=True, exist_ok=True)
                    stamp = time.strftime("%Y%m%d-%H%M%S")
                    (self.debug_dir / f"{stamp}-{quote_plus(spec.query)}.json").write_text(
                        json.dumps({"url": url, "graphql_payloads": len(captured), "listings": [l.__dict__ for l in batch]}, default=str, indent=1))
                    if not batch:
                        (self.debug_dir / f"{stamp}-{quote_plus(spec.query)}.html").write_text(page.content())
                for l in batch:
                    l.raw["query"] = spec.query
                    found.setdefault(l.listing_id, l)
                time.sleep(1.0)

            if self.scan_item_pages:
                todo = [l for l in found.values() if l.listing_id not in known_ids][: self.max_item_pages]
                for l in todo:
                    try:
                        page.goto(l.url, wait_until="domcontentloaded", timeout=45000)
                        page.wait_for_timeout(2000)
                        try:
                            page.get_by_text("See more", exact=True).first.click(timeout=1500)
                            page.wait_for_timeout(500)
                        except Exception:
                            pass
                        body = page.evaluate("() => document.body.innerText")
                        l.description = description_from_item_text(body)
                    except Exception as e:
                        print(f"[facebook] item {l.listing_id}: {e}")
                    time.sleep(0.8)
            browser.close()
        return list(found.values())

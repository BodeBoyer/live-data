"""Turn a SKU key into one number we trust: the estimated used sold price.

Priority: manual Terapeak entry (fresh) > eBay active-listing feed (fresh) > watchlist seed.
Unknown capacity keys (e.g. ddr5-24gb) are interpolated from their family by price-per-GB.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from ..config import Economics
from ..db import Database
from ..watchlist import Sku, Watchlist

EBAY_MAX_AGE_DAYS = 10


@dataclass
class PricePoint:
    key: str
    name: str
    category: str
    est_sold: float
    source: str          # manual_sold | ebay_active | seed | interpolated
    confidence: str      # high | medium | low
    as_of: str | None = None
    n: int | None = None
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_date(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


class PriceResolver:
    def __init__(self, db: Database | None, watchlist: Watchlist, reference_path: Path | None,
                 economics: Economics | None = None, now: datetime | None = None):
        self.db = db
        self.watchlist = watchlist
        self.economics = economics or Economics()
        self.now = now or datetime.now(timezone.utc)
        self.manual = self._load_manual(reference_path)
        self._cache: dict[str, PricePoint | None] = {}

    # ---- sources ----------------------------------------------------------------
    def _load_manual(self, path: Path | None) -> dict[str, dict[str, Any]]:
        if not path or not Path(path).exists():
            return {}
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        max_age = int(raw.get("max_age_days", 21))
        out: dict[str, dict[str, Any]] = {}
        for key, entry in (raw.get("prices") or {}).items():
            if not isinstance(entry, dict) or entry.get("sold_median") in (None, ""):
                continue
            as_of = _parse_date(entry.get("as_of"))
            if as_of and self.now - as_of > timedelta(days=max_age):
                continue
            out[key] = {**entry, "as_of_dt": as_of}
        return out

    def _ebay_row(self, key: str) -> Any | None:
        if not self.db:
            return None
        rows = [r for r in self.db.latest_prices(key) if r["source"] == "ebay_active" and r["est_sold"]]
        if not rows:
            return None
        row = rows[0]
        observed = _parse_date(row["observed_at"])
        if observed and self.now - observed > timedelta(days=EBAY_MAX_AGE_DAYS):
            return None
        return row

    # ---- resolution -------------------------------------------------------------
    def resolve(self, key: str) -> PricePoint | None:
        if key in self._cache:
            return self._cache[key]
        pp = self._resolve_uncached(key)
        self._cache[key] = pp
        return pp

    def _resolve_uncached(self, key: str) -> PricePoint | None:
        sku = self.watchlist.by_key.get(key)
        name = sku.name if sku else key
        category = sku.category if sku else _guess_category(key)

        m = self.manual.get(key)
        if m:
            return PricePoint(key, name, category, float(m["sold_median"]), "manual_sold", "high",
                              as_of=str(m.get("as_of")), n=m.get("n"), note=m.get("note"))

        row = self._ebay_row(key)
        if row is not None:
            n = row["sample_size"] or 0
            return PricePoint(key, name, category, float(row["est_sold"]), "ebay_active",
                              "medium" if n >= 8 else "low", as_of=row["observed_at"], n=n,
                              note=f"eBay active p25 x {self.economics.active_to_sold_factor}")

        if sku and sku.seed_price:
            return PricePoint(key, name, category, sku.seed_price, "seed", "low",
                              as_of=str(self.watchlist.meta.get("updated")), note=sku.seed.get("note"))

        return self._interpolate(key, category)

    def _interpolate(self, key: str, category: str) -> PricePoint | None:
        """ddr5-24gb -> scale between ddr5-16gb and ddr5-32gb by capacity."""
        probe = Sku(key=key, name=key, category=category)
        cap = probe.capacity_gb
        if not cap:
            return None
        members = [s for s in self.watchlist.family_members(probe.family) if s.key != key]
        points: list[tuple[int, float]] = []
        for s in members:
            pp = self.resolve(s.key)
            if pp and s.capacity_gb:
                points.append((s.capacity_gb, pp.est_sold))
        if not points:
            return None
        points.sort()
        lower = [p for p in points if p[0] <= cap]
        upper = [p for p in points if p[0] >= cap]
        if lower and upper and lower[-1][0] != upper[0][0]:
            (c0, v0), (c1, v1) = lower[-1], upper[0]
            est = v0 + (v1 - v0) * (cap - c0) / (c1 - c0)
        else:
            c, v = (lower[-1] if lower else upper[0])
            est = v * cap / c  # per-GB scaling off the nearest neighbour
        return PricePoint(key, f"{probe.family} {cap}GB (interpolated)", category, round(est, 2),
                          "interpolated", "low", note=f"from {probe.family} family")

    def all_points(self) -> list[PricePoint]:
        out = []
        for s in self.watchlist.skus:
            pp = self.resolve(s.key)
            if pp:
                out.append(pp)
        return out


def _guess_category(key: str) -> str:
    if key.startswith(("ddr", "sodimm")):
        return "ram"
    if key.startswith(("nvme", "sata", "ssd")):
        return "ssd"
    if key.startswith(("rtx", "rx", "gtx", "tesla", "arc")):
        return "gpu"
    return "other"

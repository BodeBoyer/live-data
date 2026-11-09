"""Watchlist: the SKUs we care about, how to spot them, and how to price them."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .config import DEFAULT_WATCHLIST_PATH

CATEGORIES = ("gpu", "cpu", "ram", "ssd")


@dataclass
class Sku:
    key: str
    name: str
    category: str
    patterns: list[re.Pattern] = field(default_factory=list)
    ebay: dict[str, Any] = field(default_factory=dict)
    seed: dict[str, Any] = field(default_factory=dict)
    vram_gb: int | None = None
    platform: str | None = None

    @property
    def seed_price(self) -> float | None:
        v = self.seed.get("est_sold")
        return float(v) if v is not None else None

    @property
    def capacity_gb(self) -> int | None:
        """For derived RAM/SSD keys: ddr5-32gb -> 32, nvme-2tb -> 2048, sata-ssd-512gb -> 512."""
        m = re.search(r"-(\d+)(gb|tb)$", self.key)
        if not m:
            return None
        n, unit = int(m.group(1)), m.group(2)
        return n * 1024 if unit == "tb" else n

    @property
    def family(self) -> str:
        """Prefix shared by SKUs that differ only by capacity: 'ddr5', 'ddr5-sodimm', 'nvme', 'sata-ssd'."""
        return re.sub(r"-\d+(gb|tb)$", "", self.key)

    def matches(self, normalized_text: str) -> bool:
        return any(p.search(normalized_text) for p in self.patterns)


class Watchlist:
    def __init__(self, skus: list[Sku], meta: dict[str, Any]):
        self.skus = skus
        self.meta = meta
        self.by_key = {s.key: s for s in skus}

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Watchlist":
        p = Path(path) if path else DEFAULT_WATCHLIST_PATH
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        defaults = raw.get("defaults", {}) or {}
        skus: list[Sku] = []
        for item in raw.get("skus", []):
            cat = item["category"]
            cat_defaults = dict(defaults.get(cat, {}) or {})
            ebay = dict(item.get("ebay", {}) or {})
            merged = dict(cat_defaults)
            merged.update(ebay)
            # must_not = category default list (unless the SKU supplies its own) + sku extras
            must_not = list(ebay.get("must_not", cat_defaults.get("must_not", [])) or [])
            must_not += list(ebay.get("must_not_extra", []) or [])
            merged["must_not"] = [m.lower() for m in must_not]
            merged["must"] = [m.lower() for m in (ebay.get("must", []) or [])]
            merged.pop("must_not_extra", None)
            skus.append(Sku(
                key=item["key"],
                name=item.get("name", item["key"]),
                category=cat,
                patterns=[re.compile(pat, re.I) for pat in item.get("patterns", []) or []],
                ebay=merged,
                seed=dict(item.get("seed", {}) or {}),
                vram_gb=item.get("vram_gb"),
                platform=item.get("platform"),
            ))
        meta = {k: v for k, v in raw.items() if k != "skus"}
        return cls(skus, meta)

    def patterned(self) -> list[Sku]:
        return [s for s in self.skus if s.patterns]

    def in_category(self, category: str) -> list[Sku]:
        return [s for s in self.skus if s.category == category]

    def family_members(self, family: str) -> list[Sku]:
        return sorted((s for s in self.skus if s.family == family and s.capacity_gb), key=lambda s: s.capacity_gb or 0)


def normalize(text: str) -> str:
    """Lowercase, hyphens/underscores/slashes to spaces, collapse whitespace."""
    t = text.lower()
    t = t.replace(" ", " ")
    t = re.sub(r"[,;•·|+&/\n\r]+", " | ", t)   # keep clause boundaries as a visible separator
    t = re.sub(r"[-_]+", " ", t)
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"(\s*\|\s*)+", " | ", t)
    return t.strip(" |")

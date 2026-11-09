from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class Listing:
    source: str                 # 'facebook' | 'apify' | 'manual' | ...
    listing_id: str
    title: str
    price: float | None = None
    description: str | None = None
    url: str | None = None
    location: str | None = None
    image_url: str | None = None
    posted_at: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.source}:{self.listing_id}"

    @property
    def text(self) -> str:
        return f"{self.title or ''}\n{self.description or ''}"

    def to_db(self) -> dict[str, Any]:
        d = asdict(self)
        return d


_PRICE_RE = re.compile(r"(?<![\d.])\$?\s*(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?")


def parse_price(text: Any) -> float | None:
    """'$1,250' -> 1250.0; 'Free' -> 0.0; None/garbage -> None."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    s = str(text).strip().lower()
    if not s:
        return None
    if s in ("free", "$0", "0"):
        return 0.0
    m = _PRICE_RE.search(s.replace("usd", ""))
    if not m:
        return None
    whole = m.group(1).replace(",", "")
    cents = m.group(2) or "0"
    try:
        return float(f"{whole}.{cents}")
    except ValueError:
        return None

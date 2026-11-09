"""Turn pasted text or a URL into a Listing so `pcflip evaluate` works with zero setup."""
from __future__ import annotations

import hashlib
import re

from .models import Listing, parse_price

PRICE_LINE = re.compile(r"(?m)^\s*(?:price\s*[:\-]?\s*)?\$\s?(\d[\d,]*(?:\.\d{1,2})?)\s*(?:obo|firm|or best offer|each)?\s*$", re.I)
INLINE_PRICE = re.compile(r"\$\s?(\d[\d,]*(?:\.\d{1,2})?)")


def from_text(text: str, price: float | None = None, url: str | None = None) -> Listing:
    text = text.strip()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    title = lines[0][:140] if lines else "(untitled)"
    body = "\n".join(lines[1:]) if len(lines) > 1 else None
    if price is None:
        m = PRICE_LINE.search(text) or INLINE_PRICE.search(text)
        if m:
            price = parse_price(m.group(1))
    lid = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
    m_url = re.search(r"/marketplace/item/(\d+)", url or text)
    if m_url:
        lid = m_url.group(1)
        url = url or f"https://www.facebook.com/marketplace/item/{lid}/"
    return Listing("manual", lid, title, price, body, url)

"""Hosted alternative to the local Playwright scraper: run an Apify Marketplace actor.

Set APIFY_TOKEN and APIFY_ACTOR_ID (e.g. "raidr-api/facebook-marketplace-scraper"). Actor
input schemas differ; `[apify] input_template` in config.toml is merged with the search terms,
and the normalizer below is forgiving about field names. Costs a few cents per run.
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from ..config import SearchSpec, Settings
from .models import Listing, parse_price


def normalize_item(item: dict[str, Any]) -> Listing | None:
    lid = str(item.get("id") or item.get("listingId") or item.get("listing_id") or "")
    url = item.get("url") or item.get("listingUrl") or item.get("link")
    if not lid and url:
        import re
        m = re.search(r"/item/(\d+)", url)
        lid = m.group(1) if m else ""
    if not lid:
        return None
    title = item.get("title") or item.get("marketplace_listing_title") or item.get("name") or ""
    price = parse_price(item.get("price") or item.get("listing_price") or item.get("priceAmount") or item.get("price_amount"))
    loc = item.get("location") or item.get("city") or item.get("locationText")
    if isinstance(loc, dict):
        loc = loc.get("name") or loc.get("city") or str(loc)
    img = item.get("image") or item.get("imageUrl") or item.get("primary_listing_photo") or (item.get("images") or [None])[0]
    if isinstance(img, dict):
        img = img.get("uri") or img.get("url")
    return Listing("facebook", lid, str(title), price, item.get("description"), url or f"https://www.facebook.com/marketplace/item/{lid}/",
                   str(loc) if loc else None, img if isinstance(img, str) else None, raw={"via": "apify"})


class ApifyScraper:
    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.settings = settings
        self.token = settings.env("APIFY_TOKEN")
        self.actor = settings.env("APIFY_ACTOR_ID") or settings.apify.get("actor_id")
        self.client = client or httpx.Client(timeout=60.0)
        if not self.token or not self.actor:
            raise RuntimeError("APIFY_TOKEN and APIFY_ACTOR_ID are required for the apify source")

    def scan(self, specs: list[SearchSpec], known_ids: set[str] | None = None) -> list[Listing]:
        template = dict(self.settings.apify.get("input_template", {}) or {})
        payload = {**template, "searchQueries": [s.query for s in specs], "location": specs[0].location if specs else ""}
        actor_path = self.actor.replace("/", "~")
        r = self.client.post(f"https://api.apify.com/v2/acts/{actor_path}/runs", params={"token": self.token}, json=payload)
        r.raise_for_status()
        run = r.json()["data"]
        run_id, dataset_id = run["id"], run["defaultDatasetId"]
        deadline = time.time() + 600
        while time.time() < deadline:
            s = self.client.get(f"https://api.apify.com/v2/actor-runs/{run_id}", params={"token": self.token}).json()["data"]
            if s["status"] in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
                break
            time.sleep(5)
        items = self.client.get(f"https://api.apify.com/v2/datasets/{dataset_id}/items", params={"token": self.token, "clean": "true"}).json()
        out: dict[str, Listing] = {}
        for it in items:
            l = normalize_item(it)
            if l:
                out.setdefault(l.listing_id, l)
        return list(out.values())

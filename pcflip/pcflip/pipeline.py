"""Glue: listings in -> stored -> valued -> alerted. Shared by `scan`, `watch`, and `evaluate`."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .alerts.dispatch import dispatch
from .config import Settings
from .db import Database
from .listings.models import Listing
from .pricing.resolver import PriceResolver
from .valuation.extract import Extraction, extract
from .valuation.llm import LlmExtractor, extract_with_llm
from .valuation.value import VERDICT_ORDER, Valuation, value_listing
from .watchlist import Watchlist


@dataclass
class Processed:
    listing: Listing
    extraction: Extraction
    valuation: Valuation
    is_new: bool
    previous_price: float | None
    alerted: list[str]


class Pipeline:
    def __init__(self, settings: Settings, db: Database, watchlist: Watchlist | None = None,
                 resolver: PriceResolver | None = None, llm: LlmExtractor | None = None, quiet: bool = False):
        self.settings = settings
        self.db = db
        self.watchlist = watchlist or Watchlist.load(settings.watchlist_path)
        self.resolver = resolver or PriceResolver(db, self.watchlist, settings.reference_path, settings.economics)
        self.llm = llm if llm is not None else self._maybe_llm()
        self.quiet = quiet

    def _maybe_llm(self) -> LlmExtractor | None:
        cfg = self.settings.llm
        if not cfg.get("enabled") or not self.settings.env("ANTHROPIC_API_KEY"):
            return None
        try:
            return LlmExtractor(model=cfg.get("model", "claude-opus-5-5"), effort=cfg.get("effort", "low"), db=self.db)
        except Exception as e:  # missing optional dependency etc.
            print(f"[llm] disabled: {e}")
            return None

    # ---- single listing ------------------------------------------------------------
    def evaluate(self, listing: Listing) -> tuple[Extraction, Valuation]:
        ex = extract_with_llm(listing.title, listing.description, self.watchlist, self.llm,
                              only_for_prebuilts=bool(self.settings.llm.get("only_for_prebuilts", True)))
        v = value_listing(listing.price, ex, self.resolver, self.settings.economics)
        return ex, v

    def should_alert(self, listing: Listing, v: Valuation, is_new: bool, previous_price: float | None) -> bool:
        min_verdict = str(self.settings.alerts.get("min_verdict", "WATCH")).upper()
        if v.rank < VERDICT_ORDER.get(min_verdict, 1):
            return False
        last = self.db.last_alert(listing.key)
        if last is None:
            return True
        drop_pct = float(self.settings.alerts.get("realert_on_price_drop_pct", 10)) / 100.0
        if listing.price is not None and last["price"] and listing.price <= last["price"] * (1 - drop_pct):
            return True
        if VERDICT_ORDER.get(v.verdict, 0) > VERDICT_ORDER.get(last["verdict"] or "PASS", 0):
            return True
        return False

    def process(self, listings: Iterable[Listing], alert: bool = True, store: bool = True) -> list[Processed]:
        out: list[Processed] = []
        for listing in listings:
            is_new, prev = (True, None)
            if store:
                is_new, prev = self.db.upsert_listing(listing.to_db())
            ex, v = self.evaluate(listing)
            if store:
                self.db.insert_valuation(listing.key, {**v.to_dict(), "extraction": ex.to_dict()})
            sent: list[str] = []
            if alert and self.should_alert(listing, v, is_new, prev):
                sent = dispatch(listing, v, self.settings)
                if sent and store:
                    self.db.record_alert(listing.key, listing.price, v.verdict, sent)
            out.append(Processed(listing, ex, v, is_new, prev, sent))
        return out


def summarize_run(results: list[Processed]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for r in results:
        counts[r.valuation.verdict] = counts.get(r.valuation.verdict, 0) + 1
    return {"total": len(results), "new": sum(1 for r in results if r.is_new), "alerted": sum(1 for r in results if r.alerted),
            "verdicts": counts}

"""Turn an extraction + reference prices into a buy/pass decision with the math shown."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

from ..config import Economics
from ..pricing.resolver import PriceResolver, PricePoint
from .extract import Extraction, Component

VERDICT_ORDER = {"PASS": 0, "UNKNOWN": 0, "UNPRICED": 0, "WATCH": 1, "BUY": 2, "STRONG_BUY": 3}
CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}


@dataclass
class LineItem:
    category: str
    key: str
    label: str
    quantity: int
    unit_value: float | None
    value: float | None
    ebay_net: float | None
    source: str | None
    confidence: str | None
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Valuation:
    ask: float | None
    kind: str
    verdict: str
    reasons: list[str]
    est_value: float | None          # parts-sum at used sold prices (incl. other-parts allowance for prebuilts)
    best_exit: float | None          # what we expect to net, choosing the best exit
    exit_strategy: str | None        # 'local' | 'ebay' | 'part_out'
    exits: dict[str, float] = field(default_factory=dict)
    max_buy: float | None = None
    target_profit: float | None = None
    spread: float | None = None
    spread_pct: float | None = None
    offer: float | None = None
    confidence: str = "low"
    items: list[LineItem] = field(default_factory=list)
    flags: dict[str, list[str]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    @property
    def rank(self) -> int:
        return VERDICT_ORDER.get(self.verdict, 0)


def _round5(x: float) -> float:
    return float(int(x // 5) * 5)


def _ebay_net(gross: float, category: str, econ: Economics) -> float:
    ship = econ.shipping_estimate.get(category, econ.shipping_estimate.get("other", 12.0))
    return gross * (1 - econ.ebay_fee_rate) - econ.ebay_per_order_fee - ship


def value_listing(ask: float | None, extraction: Extraction, resolver: PriceResolver, econ: Economics) -> Valuation:
    v = Valuation(ask=ask, kind=extraction.kind, verdict="UNKNOWN", reasons=[], est_value=None, best_exit=None,
                  exit_strategy=None, flags={"hard": list(extraction.hard_flags), "soft": list(extraction.soft_flags)},
                  notes=list(extraction.notes))

    # ---- disqualifiers that need no math
    if extraction.is_wanted_ad:
        v.verdict, v.reasons = "PASS", ["looks like a want-to-buy post, not a sale"]
        return v
    if extraction.is_laptop:
        v.verdict, v.reasons = "PASS", ["laptop: not valued (mobile GPUs/RAM price differently)"]
        return v
    if extraction.is_accessory:
        v.verdict, v.reasons = "PASS", ["accessory listing (backplate/cable/box), not the part"]
        return v
    if not extraction.components:
        v.verdict, v.reasons = "UNKNOWN", ["no recognizable components in the text"]
        return v

    # ---- line items
    worst_conf = 3
    discount = 1.0
    if "mining history" in extraction.soft_flags and "seller says never mined" not in extraction.soft_flags:
        discount *= 0.85
        v.notes.append("mining history: valued at 85%")
    if "untested" in extraction.soft_flags or "sold as-is" in extraction.soft_flags:
        discount *= 0.92
        v.notes.append("untested/as-is: valued at 92%")

    parts_sum = 0.0
    ebay_sum = 0.0
    priced_any = False
    for comp in extraction.components:
        pp: PricePoint | None = resolver.resolve(comp.key)
        if pp is None:
            v.items.append(LineItem(comp.category, comp.key, comp.label, comp.quantity, None, None, None, None, None,
                                    "no reference price"))
            v.notes.append(f"no reference price for {comp.key}")
            continue
        unit = pp.est_sold * (discount if comp.category == "gpu" or extraction.kind != "prebuilt" else 1.0)
        value = unit * comp.quantity
        net = _ebay_net(unit, comp.category, econ) * comp.quantity
        parts_sum += value
        ebay_sum += max(net, 0.0)
        priced_any = True
        worst_conf = min(worst_conf, CONFIDENCE_RANK.get(pp.confidence, 1))
        v.items.append(LineItem(comp.category, comp.key, comp.label, comp.quantity, round(unit, 2), round(value, 2),
                                round(net, 2), pp.source, pp.confidence, pp.note))

    if not priced_any:
        v.verdict, v.reasons = "UNKNOWN", ["components found but none have a reference price"]
        return v

    # ---- exits
    cats = extraction.categories()
    if extraction.kind == "prebuilt":
        ram = next((c for c in extraction.components if c.category == "ram"), None)
        ddr5 = bool(ram and str(ram.detail.get("type")) == "ddr5")
        other = econ.prebuilt_other_parts_ddr5 if ddr5 else econ.prebuilt_other_parts_ddr4
        if "case only" in extraction.hard_flags:
            other = 0.0
        parts_sum_total = parts_sum + other
        v.items.append(LineItem("other", "other-parts", "Motherboard/PSU/case/cooler allowance", 1, other, other,
                                round(other * 0.5, 2), "allowance", "low", "not itemized; assumed from RAM generation"))
        whole_local = parts_sum_total * econ.prebuilt_whole_factor
        part_out = ebay_sum + other * 0.5
        v.exits = {"local_whole": round(whole_local, 2), "part_out": round(part_out, 2)}
        if "gpu" not in cats:
            v.notes.append("no GPU identified: whole-PC resale is weak; part-out exit used")
            whole_local *= 0.85
            v.exits["local_whole"] = round(whole_local, 2)
        if "cpu" not in cats:
            v.notes.append("no CPU identified: confidence reduced")
            worst_conf = min(worst_conf, 1)
        v.est_value = round(parts_sum_total, 2)
        if whole_local >= part_out:
            v.best_exit, v.exit_strategy = round(whole_local, 2), "local_whole"
        else:
            v.best_exit, v.exit_strategy = round(part_out, 2), "part_out"
    else:
        local = parts_sum * econ.local_sale_factor
        v.exits = {"local": round(local, 2), "ebay": round(ebay_sum, 2)}
        v.est_value = round(parts_sum, 2)
        if local >= ebay_sum:
            v.best_exit, v.exit_strategy = round(local, 2), "local"
        else:
            v.best_exit, v.exit_strategy = round(ebay_sum, 2), "ebay"

    # ---- decision
    target = max(econ.target_profit_floor, econ.target_margin * v.best_exit)
    v.target_profit = round(target, 2)
    v.max_buy = round(max(v.best_exit - target, 0.0), 2)
    v.confidence = {3: "high", 2: "medium", 1: "low"}[worst_conf]

    hard = [f for f in extraction.hard_flags]
    if ask is None or ask <= 0:
        v.verdict = "UNPRICED"
        v.reasons.append("no price on listing; message seller" if ask is None else "listed as free/$0: verify, then grab it")
        v.offer = _round5(v.max_buy * econ.strong_buy_ratio)
        if hard:
            v.verdict = "PASS"
            v.reasons.append("hard red flags: " + ", ".join(hard))
        return v

    v.spread = round(v.best_exit - ask, 2)
    v.spread_pct = round(v.spread / ask, 4) if ask else None
    if hard:
        v.verdict = "PASS"
        v.reasons.append("hard red flags: " + ", ".join(hard))
        v.notes.append(f"if parts-only pricing applies, working-part value would be ~${v.best_exit:,.0f}")
        return v
    if ask <= v.max_buy * econ.strong_buy_ratio:
        v.verdict = "STRONG_BUY"
        v.reasons.append(f"ask ${ask:,.0f} is {ask / v.max_buy:.0%} of max buy ${v.max_buy:,.0f}")
    elif ask <= v.max_buy:
        v.verdict = "BUY"
        v.reasons.append(f"ask ${ask:,.0f} is under max buy ${v.max_buy:,.0f}")
    elif ask <= v.max_buy * econ.watch_ratio:
        v.verdict = "WATCH"
        v.reasons.append(f"ask ${ask:,.0f} is within {econ.watch_ratio - 1:.0%} of max buy ${v.max_buy:,.0f}; negotiate")
    else:
        v.verdict = "PASS"
        v.reasons.append(f"ask ${ask:,.0f} exceeds max buy ${v.max_buy:,.0f}")
    if v.verdict in ("STRONG_BUY", "BUY", "WATCH"):
        v.offer = min(_round5(ask), _round5(v.max_buy * econ.strong_buy_ratio))
        if v.confidence == "low":
            v.reasons.append("low-confidence prices (seed/interpolated): verify eBay sold comps before paying")
    return v


def format_summary(v: Valuation, title: str = "") -> str:
    """Plain-text block used by the console alert channel and `pcflip evaluate`."""
    lines = []
    head = f"[{v.verdict}] {title}".strip()
    lines.append(head)
    if v.ask is not None:
        lines.append(f"  ask ${v.ask:,.0f}" + (f" | exit ${v.best_exit:,.0f} via {v.exit_strategy}" if v.best_exit else ""))
    elif v.best_exit:
        lines.append(f"  unpriced | exit ${v.best_exit:,.0f} via {v.exit_strategy}")
    if v.max_buy is not None:
        extra = f" | spread ${v.spread:,.0f} ({v.spread_pct:+.0%})" if v.spread is not None and v.spread_pct is not None else ""
        lines.append(f"  max buy ${v.max_buy:,.0f} | target profit ${v.target_profit:,.0f}{extra}")
    if v.offer is not None:
        lines.append(f"  open at ${v.offer:,.0f}")
    for it in v.items:
        if it.value is None:
            lines.append(f"  - {it.label}: no reference price")
        else:
            q = f" x{it.quantity}" if it.quantity > 1 else ""
            lines.append(f"  - {it.label}{q}: ${it.value:,.0f} ({it.source}, {it.confidence})")
    if v.flags.get("hard"):
        lines.append("  RED FLAGS: " + ", ".join(v.flags["hard"]))
    if v.flags.get("soft"):
        lines.append("  notes: " + ", ".join(v.flags["soft"]))
    for r in v.reasons:
        lines.append(f"  > {r}")
    for n in v.notes:
        lines.append(f"  . {n}")
    lines.append(f"  confidence: {v.confidence}")
    return "\n".join(lines)

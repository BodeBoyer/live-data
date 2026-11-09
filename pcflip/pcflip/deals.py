"""Buy/sell ledger. Every flip gets a row: this is your COGS record for taxes."""
from __future__ import annotations

import csv
import io
from typing import Any

from .db import Database


def add(db: Database, cost: float, title: str, listing_id: str | None = None, extra_cost: float = 0.0,
        bought_at: str | None = None, notes: str | None = None, test_results: str | None = None) -> int:
    return db.add_deal(listing_id=listing_id, title=title, cost=cost, extra_cost=extra_cost, bought_at=bought_at,
                       notes=notes, test_results=test_results)


def sold(db: Database, deal_id: int, sale_price: float, platform: str = "local", fees: float = 0.0,
         shipping: float = 0.0, sold_at: str | None = None) -> dict[str, Any]:
    db.update_deal(deal_id, sale_price=sale_price, platform=platform, sale_fees=fees, sale_shipping=shipping, sold_at=sold_at)
    row = next(d for d in db.deals() if d["id"] == deal_id)
    return as_dict(row)


def as_dict(row) -> dict[str, Any]:
    d = dict(row)
    if d.get("sale_price") is not None:
        d["profit"] = round(d["sale_price"] - (d["sale_fees"] or 0) - (d["sale_shipping"] or 0) - d["cost"] - (d["extra_cost"] or 0), 2)
        d["roi"] = round(d["profit"] / (d["cost"] + (d["extra_cost"] or 0)), 4) if d["cost"] else None
    else:
        d["profit"] = None
        d["roi"] = None
    return d


def table(db: Database) -> str:
    rows = [as_dict(r) for r in db.deals()]
    if not rows:
        return "no deals yet"
    out = [f"{'id':>3} {'bought':10} {'cost':>8} {'sold':>8} {'profit':>8} {'roi':>6} {'where':6} title"]
    total = 0.0
    for d in rows:
        p = d["profit"]
        total += p or 0
        out.append(f"{d['id']:>3} {(d['bought_at'] or d['created_at'])[:10]:10} {d['cost']:>8.0f} "
                   f"{(d['sale_price'] if d['sale_price'] is not None else 0):>8.0f} {(p if p is not None else 0):>8.0f} "
                   f"{(f'{d['roi']:.0%}' if d['roi'] is not None else '-'):>6} {(d['platform'] or '-'):6} {d['title']}")
    out.append(f"realized profit: ${total:,.0f} across {sum(1 for d in rows if d['profit'] is not None)} sold / {len(rows)} bought")
    return "\n".join(out)


def to_csv(db: Database) -> str:
    rows = [as_dict(r) for r in db.deals()]
    buf = io.StringIO()
    cols = ["id", "created_at", "bought_at", "sold_at", "title", "listing_id", "cost", "extra_cost", "sale_price", "sale_fees",
            "sale_shipping", "platform", "profit", "roi", "notes", "test_results"]
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue()

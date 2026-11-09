"""Static reports: PRICES.md (renders on GitHub) and report.html (local dashboard)."""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from .db import Database, utcnow
from .pricing.resolver import PriceResolver
from .watchlist import Watchlist

CATEGORY_ORDER = ["gpu", "cpu", "ram", "ssd"]
CATEGORY_TITLES = {"gpu": "Graphics cards", "cpu": "CPUs", "ram": "Memory", "ssd": "SSDs"}


def _fmt(x: Any, money: bool = True) -> str:
    if x is None:
        return "—"
    if money:
        return f"${float(x):,.0f}"
    return str(x)


def prices_markdown(watchlist: Watchlist, resolver: PriceResolver, db: Database | None) -> str:
    latest = {(r["sku"], r["source"]): r for r in db.latest_prices()} if db else {}
    lines = ["# Reference prices", "",
             f"Generated {utcnow()}. `est_sold` is the number the valuation engine uses: the estimated eBay sold median for a USED, working unit.",
             "Sources, best first: `manual_sold` (Terapeak, reference_prices.yaml) > `ebay_active` (Browse API lower quartile x factor) > `seed` (hand-set estimate).",
             ""]
    for cat in CATEGORY_ORDER:
        skus = watchlist.in_category(cat)
        if not skus:
            continue
        lines += [f"## {CATEGORY_TITLES.get(cat, cat)}", "", "| SKU | Name | est. sold | source | conf. | eBay active n / p25 / median | as of |", "|---|---|---:|---|---|---|---|"]
        for s in skus:
            pp = resolver.resolve(s.key)
            row = latest.get((s.key, "ebay_active"))
            ebay = f"{row['sample_size']} / {_fmt(row['p25'])} / {_fmt(row['median'])}" if row else "—"
            lines.append(f"| `{s.key}` | {s.name} | {_fmt(pp.est_sold) if pp else '—'} | {pp.source if pp else '—'} | "
                         f"{pp.confidence if pp else '—'} | {ebay} | {(pp.as_of or '')[:10] if pp else ''} |")
        lines.append("")
    return "\n".join(lines)


def report_html(settings_title: str, watchlist: Watchlist, resolver: PriceResolver, db: Database) -> str:
    vals = db.recent_valuations(limit=200)
    deals = db.deals()
    latest = {(r["sku"], r["source"]): r for r in db.latest_prices()}

    def esc(x: Any) -> str:
        return html.escape("" if x is None else str(x))

    rows_v = []
    for r in vals:
        detail = json.loads(r["detail"] or "{}")
        rows_v.append(
            f"<tr class='v-{esc(r['verdict'])}'><td>{esc(r['valued_at'][:16])}</td><td><a href='{esc(r['url'])}' target='_blank' rel='noopener'>{esc(r['title'])}</a>"
            f"<div class='sub'>{esc(r['location'])} · {esc(r['source'])}</div></td><td class='num'>{_fmt(r['ask'])}</td>"
            f"<td class='num'>{_fmt(r['best_exit'])}</td><td class='num'>{_fmt(r['max_buy'])}</td>"
            f"<td class='num'>{_fmt(r['spread'])}</td><td><span class='badge'>{esc(r['verdict'])}</span></td><td>{esc(r['confidence'])}</td>"
            f"<td class='sub'>{esc('; '.join(detail.get('reasons', [])[:1]))}</td></tr>")
    rows_p = []
    for cat in CATEGORY_ORDER:
        for s in watchlist.in_category(cat):
            pp = resolver.resolve(s.key)
            row = latest.get((s.key, "ebay_active"))
            rows_p.append(
                f"<tr><td>{esc(cat)}</td><td>{esc(s.name)}<div class='sub'>{esc(s.key)}</div></td><td class='num'>{_fmt(pp.est_sold) if pp else '—'}</td>"
                f"<td>{esc(pp.source) if pp else '—'}</td><td>{esc(pp.confidence) if pp else '—'}</td>"
                f"<td class='num'>{esc(row['sample_size']) if row else '—'}</td><td class='num'>{_fmt(row['p25']) if row else '—'}</td><td class='num'>{_fmt(row['median']) if row else '—'}</td></tr>")
    rows_d = []
    total_profit = 0.0
    for d in deals:
        profit = None
        if d["sale_price"] is not None:
            profit = d["sale_price"] - (d["sale_fees"] or 0) - (d["sale_shipping"] or 0) - d["cost"] - (d["extra_cost"] or 0)
            total_profit += profit
        rows_d.append(f"<tr><td>{d['id']}</td><td>{esc(d['title'])}</td><td class='num'>{_fmt(d['cost'])}</td><td class='num'>{_fmt(d['sale_price'])}</td>"
                      f"<td class='num'>{_fmt(profit)}</td><td>{esc(d['platform'])}</td><td class='sub'>{esc(d['notes'])}</td></tr>")

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(settings_title)}</title>
<style>
:root{{--bg:#f6f7f9;--fg:#1b1f24;--muted:#6b7280;--card:#fff;--line:#e5e7eb;--sb:#16a34a;--b:#22c55e;--w:#eab308;--p:#9ca3af;--u:#3b82f6}}
@media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{--bg:#0f1115;--fg:#e5e7eb;--muted:#9ca3af;--card:#171a21;--line:#2a2f3a}}}}
:root[data-theme=dark]{{--bg:#0f1115;--fg:#e5e7eb;--muted:#9ca3af;--card:#171a21;--line:#2a2f3a}}
body{{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;padding:16px}}
h1{{font-size:20px;margin:0 0 4px}} h2{{font-size:16px;margin:24px 0 8px}} .sub{{color:var(--muted);font-size:12px}}
table{{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:hidden}}
th,td{{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}} th{{font-weight:600;color:var(--muted);font-size:12px}}
td.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}} a{{color:inherit}}
.badge{{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600;color:#fff;background:var(--p)}}
.v-STRONG_BUY .badge{{background:var(--sb)}} .v-BUY .badge{{background:var(--b)}} .v-WATCH .badge{{background:var(--w);color:#111}} .v-UNPRICED .badge{{background:var(--u)}}
.wrap{{overflow-x:auto}} .kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:12px 0}}
.kpi{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px}} .kpi b{{display:block;font-size:22px}}
</style></head><body>
<h1>{esc(settings_title)}</h1><div class="sub">Generated {esc(utcnow())}. Prices are estimates; verify eBay sold comps before buying.</div>
<div class="kpis"><div class="kpi"><span class="sub">Listings valued</span><b>{len(vals)}</b></div>
<div class="kpi"><span class="sub">Buy-grade right now</span><b>{sum(1 for r in vals if r['verdict'] in ('BUY', 'STRONG_BUY'))}</b></div>
<div class="kpi"><span class="sub">Deals logged</span><b>{len(deals)}</b></div><div class="kpi"><span class="sub">Realized profit</span><b>{_fmt(total_profit)}</b></div></div>
<h2>Recent valuations</h2><div class="wrap"><table><thead><tr><th>When</th><th>Listing</th><th>Ask</th><th>Exit</th><th>Max buy</th><th>Spread</th><th>Verdict</th><th>Conf.</th><th>Why</th></tr></thead>
<tbody>{''.join(rows_v) or '<tr><td colspan=9 class=sub>No listings yet. Run <code>pcflip scan</code> or <code>pcflip evaluate</code>.</td></tr>'}</tbody></table></div>
<h2>Reference prices</h2><div class="wrap"><table><thead><tr><th>Cat.</th><th>SKU</th><th>Est. sold</th><th>Source</th><th>Conf.</th><th>eBay n</th><th>p25</th><th>Median</th></tr></thead><tbody>{''.join(rows_p)}</tbody></table></div>
<h2>Deals</h2><div class="wrap"><table><thead><tr><th>#</th><th>Item</th><th>Cost</th><th>Sold</th><th>Profit</th><th>Platform</th><th>Notes</th></tr></thead>
<tbody>{''.join(rows_d) or '<tr><td colspan=7 class=sub>No deals logged. <code>pcflip deals add --cost 600 --title "..."</code></td></tr>'}</tbody></table></div>
</body></html>"""


def write_reports(data_dir: Path, watchlist: Watchlist, resolver: PriceResolver, db: Database, title: str = "pcflip") -> dict[str, Path]:
    data_dir.mkdir(parents=True, exist_ok=True)
    md = data_dir / "PRICES.md"
    md.write_text(prices_markdown(watchlist, resolver, db), encoding="utf-8")
    htm = data_dir / "report.html"
    htm.write_text(report_html(title, watchlist, resolver, db), encoding="utf-8")
    return {"markdown": md, "html": htm}

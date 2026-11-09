"""pcflip command line.

  pcflip init                    create config.toml + .env from the examples
  pcflip evaluate [-p PRICE] TEXT|-   value a pasted listing (no setup needed)
  pcflip prices [--only SKU ...] refresh reference prices from eBay, write data/prices.json + PRICES.md
  pcflip fb-login                open a browser, log in to Facebook once, save the session
  pcflip scan [--source ...]     run every search once, value new listings, alert
  pcflip watch [--interval S]    scan forever
  pcflip report                  write data/report.html + data/PRICES.md
  pcflip deals add|sold|list|export
  pcflip test-alerts             send a hello to every configured channel
  pcflip skus                    list watchlist SKUs and their current reference price
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

from . import __version__
from .config import DEFAULT_CONFIG_PATH, EXAMPLE_CONFIG_PATH, PACKAGE_ROOT, load_settings
from .db import Database
from .listings.manual import from_text
from .pipeline import Pipeline, summarize_run
from .pricing.resolver import PriceResolver
from .valuation.value import format_summary
from .watchlist import Watchlist


def _settings(args):
    return load_settings(args.config)


def _open(args):
    settings = _settings(args)
    db = Database(settings.db_path)
    watchlist = Watchlist.load(settings.watchlist_path)
    resolver = PriceResolver(db, watchlist, settings.reference_path, settings.economics)
    return settings, db, watchlist, resolver


# ---------------------------------------------------------------------------- commands
def cmd_init(args) -> int:
    cfg = Path(args.config) if args.config else DEFAULT_CONFIG_PATH
    if cfg.exists() and not args.force:
        print(f"{cfg} already exists (use --force to overwrite)")
    else:
        shutil.copy(EXAMPLE_CONFIG_PATH, cfg)
        print(f"wrote {cfg}")
    env = cfg.parent / ".env"
    if not env.exists():
        shutil.copy(PACKAGE_ROOT / ".env.example", env)
        print(f"wrote {env}  <- add your keys here")
    print("next: edit [facebook].location and [[searches]] in config.toml, then `pcflip evaluate` / `pcflip scan`")
    return 0


def cmd_evaluate(args) -> int:
    settings, db, watchlist, resolver = _open(args)
    text = sys.stdin.read() if args.text == ["-"] else " ".join(args.text)
    if args.description:
        text = text + "\n" + args.description
    listing = from_text(text, price=args.price, url=args.url)
    pipe = Pipeline(settings, db, watchlist, resolver, quiet=True)
    results = pipe.process([listing], alert=False, store=not args.no_store)
    r = results[0]
    print(format_summary(r.valuation, listing.title))
    if args.verbose:
        import json
        print(json.dumps({"extraction": r.extraction.to_dict(), "valuation": r.valuation.to_dict()}, indent=2, default=str))
    return 0


def cmd_prices(args) -> int:
    from .pricing.ebay import EbayError, export_snapshot, refresh_prices
    from .report import write_reports
    settings, db, watchlist, resolver = _open(args)
    if not args.skip_fetch:
        try:
            print("refreshing eBay active comps...")
            refresh_prices(settings, watchlist, db, only=args.only or None)
        except EbayError as e:
            print(f"eBay feed unavailable: {e}")
            if args.strict:
                return 2
            print("continuing with manual/seed prices")
    resolver = PriceResolver(db, watchlist, settings.reference_path, settings.economics)  # fresh cache
    snap = export_snapshot(db, watchlist, resolver, settings.data_dir / "prices.json")
    paths = write_reports(settings.data_dir, watchlist, resolver, db)
    print(f"wrote {settings.data_dir / 'prices.json'} ({len(snap['skus'])} skus), {paths['markdown']}, {paths['html']}")
    return 0


def cmd_fb_login(args) -> int:
    from .listings.facebook import FacebookScraper
    settings = _settings(args)
    FacebookScraper(settings).login()
    return 0


def _sources(settings, args):
    names = args.source or (["apify"] if settings.apify.get("enabled") else ["facebook"])
    out = []
    for n in names:
        if n == "facebook":
            from .listings.facebook import FacebookScraper
            out.append(FacebookScraper(settings, headless=None if not args.headful else False,
                                       debug_dir=(settings.data_dir / "debug") if args.debug else None))
        elif n == "apify":
            from .listings.apify import ApifyScraper
            out.append(ApifyScraper(settings))
        else:
            raise SystemExit(f"unknown source {n!r}")
    return out


def _scan_once(settings, db, pipe, sources, log=print) -> dict:
    if not settings.searches:
        raise SystemExit("no [[searches]] in config.toml; run `pcflip init` and add some")
    known = {r["listing_id"] for r in db.recent_listings(limit=5000)}
    listings = []
    for src in sources:
        try:
            listings.extend(src.scan(settings.searches, known_ids=known))
        except Exception as e:
            log(f"[{src.__class__.__name__}] scan failed: {e}")
    results = pipe.process(listings, alert=True, store=True)
    summary = summarize_run(results)
    log(f"scan: {summary['total']} listings, {summary['new']} new, {summary['alerted']} alerted, verdicts={summary['verdicts']}")
    return summary


def cmd_scan(args) -> int:
    settings, db, watchlist, resolver = _open(args)
    pipe = Pipeline(settings, db, watchlist, resolver)
    _scan_once(settings, db, pipe, _sources(settings, args))
    return 0


def cmd_watch(args) -> int:
    settings, db, watchlist, resolver = _open(args)
    pipe = Pipeline(settings, db, watchlist, resolver)
    sources = _sources(settings, args)
    interval = args.interval or settings.poll_interval_seconds
    print(f"watching {len(settings.searches)} searches every {interval}s (ctrl-c to stop)")
    while True:
        started = time.time()
        try:
            _scan_once(settings, db, pipe, sources)
        except SystemExit:
            raise
        except Exception as e:
            print(f"scan error: {e}")
        if args.once:
            break
        time.sleep(max(30.0, interval - (time.time() - started)))
    return 0


def cmd_report(args) -> int:
    from .report import write_reports
    settings, db, watchlist, resolver = _open(args)
    paths = write_reports(settings.data_dir, watchlist, resolver, db)
    print(f"wrote {paths['html']} and {paths['markdown']}")
    return 0


def cmd_deals(args) -> int:
    from . import deals
    settings, db, watchlist, resolver = _open(args)
    if args.deals_cmd == "add":
        did = deals.add(db, args.cost, args.title, args.listing, args.extra or 0.0, args.date, args.notes, args.tests)
        print(f"deal #{did} logged: {args.title} @ ${args.cost:,.0f}")
    elif args.deals_cmd == "sold":
        d = deals.sold(db, args.id, args.price, args.platform, args.fees or 0.0, args.shipping or 0.0, args.date)
        print(f"deal #{d['id']} sold for ${d['sale_price']:,.0f} on {d['platform']}: profit ${d['profit']:,.0f} ({d['roi']:.0%} ROI)")
    elif args.deals_cmd == "export":
        out = Path(args.out) if args.out else settings.data_dir / "deals.csv"
        out.write_text(deals.to_csv(db), encoding="utf-8")
        print(f"wrote {out}")
    else:
        print(deals.table(db))
    return 0


def cmd_test_alerts(args) -> int:
    from .alerts.dispatch import test_channels
    settings = _settings(args)
    res = test_channels(settings)
    for k, ok in res.items():
        print(f"{k}: {'ok' if ok else 'FAILED (missing env var or request error)'}")
    return 0 if all(res.values()) else 1


def cmd_skus(args) -> int:
    settings, db, watchlist, resolver = _open(args)
    for s in watchlist.skus:
        pp = resolver.resolve(s.key)
        print(f"{s.category:4} {s.key:24} {s.name:36} {('$%.0f' % pp.est_sold) if pp else '-':>8}  {pp.source if pp else '-':12} {pp.confidence if pp else ''}")
    return 0


# ---------------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pcflip", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", help="path to config.toml (default: pcflip/config.toml)")
    p.add_argument("--version", action="version", version=f"pcflip {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create config.toml and .env from examples")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("evaluate", help="value a listing from text ('-' reads stdin)")
    s.add_argument("text", nargs="+")
    s.add_argument("-p", "--price", type=float)
    s.add_argument("-d", "--description")
    s.add_argument("-u", "--url")
    s.add_argument("--no-store", action="store_true", help="don't save to the database")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_evaluate)

    s = sub.add_parser("prices", help="refresh eBay comps and write data/prices.json + reports")
    s.add_argument("--only", nargs="*", help="limit to these SKU keys")
    s.add_argument("--skip-fetch", action="store_true", help="just rebuild outputs from stored data")
    s.add_argument("--strict", action="store_true", help="exit non-zero when the eBay feed is unavailable")
    s.set_defaults(fn=cmd_prices)

    s = sub.add_parser("fb-login", help="log in to Facebook once and save the session")
    s.set_defaults(fn=cmd_fb_login)

    for name, fn, helptxt in (("scan", cmd_scan, "run searches once"), ("watch", cmd_watch, "run searches on a loop")):
        s = sub.add_parser(name, help=helptxt)
        s.add_argument("--source", action="append", choices=["facebook", "apify"])
        s.add_argument("--headful", action="store_true", help="show the browser")
        s.add_argument("--debug", action="store_true", help="dump raw payloads/HTML to data/debug for parser fixes")
        if name == "watch":
            s.add_argument("--interval", type=int)
            s.add_argument("--once", action="store_true")
        s.set_defaults(fn=fn)

    s = sub.add_parser("report", help="write data/report.html and data/PRICES.md")
    s.set_defaults(fn=cmd_report)

    s = sub.add_parser("deals", help="buy/sell ledger")
    ds = s.add_subparsers(dest="deals_cmd")
    a = ds.add_parser("add")
    a.add_argument("--cost", type=float, required=True)
    a.add_argument("--title", required=True)
    a.add_argument("--listing")
    a.add_argument("--extra", type=float, help="gas, parts, paste...")
    a.add_argument("--date")
    a.add_argument("--notes")
    a.add_argument("--tests", help="test results (GPU stress, memtest, SMART)")
    so = ds.add_parser("sold")
    so.add_argument("id", type=int)
    so.add_argument("--price", type=float, required=True)
    so.add_argument("--platform", default="local")
    so.add_argument("--fees", type=float)
    so.add_argument("--shipping", type=float)
    so.add_argument("--date")
    ds.add_parser("list")
    e = ds.add_parser("export")
    e.add_argument("--out")
    s.set_defaults(fn=cmd_deals)

    s = sub.add_parser("test-alerts", help="send a test alert to configured channels")
    s.set_defaults(fn=cmd_test_alerts)
    s = sub.add_parser("skus", help="list SKUs with current reference prices")
    s.set_defaults(fn=cmd_skus)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.fn(args) or 0)
    except KeyboardInterrupt:
        print("\nstopped")
        return 130


if __name__ == "__main__":
    sys.exit(main())

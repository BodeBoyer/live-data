from datetime import datetime, timezone, timedelta

from pcflip.pricing.stats import summarize, trim_outliers
from pcflip.pricing.resolver import PriceResolver
from pcflip.db import Database


def test_summarize_trims_outliers():
    s = summarize([100, 110, 120, 115, 118, 900, 105, 108, 112, 10])
    assert s["n"] == 8 and s["low"] == 100 and s["high"] == 120
    assert 105 <= s["p25"] <= 110 and 110 <= s["median"] <= 115


def test_summarize_small_sample_untrimmed():
    assert trim_outliers([5, 1000]) == [5, 1000]
    assert summarize([]) == {"n": 0, "median": None, "p25": None, "p75": None, "low": None, "high": None}


def test_seed_then_interpolation(watchlist, resolver):
    assert resolver.resolve("rtx-3090").source == "seed"
    pp = resolver.resolve("ddr5-24gb")
    assert pp.source == "interpolated"
    lo, hi = resolver.resolve("ddr5-16gb").est_sold, resolver.resolve("ddr5-32gb").est_sold
    assert lo < pp.est_sold < hi
    assert resolver.resolve("not-a-thing") is None


def test_manual_beats_ebay_beats_seed(tmp_path, watchlist):
    db = Database(tmp_path / "t.db")
    db.insert_price("rtx-3090", "ebay_active", {"n": 20, "median": 1300, "p25": 1250}, est_sold=1150)
    ref = tmp_path / "ref.yaml"
    ref.write_text("max_age_days: 21\nprices:\n  rtx-3090: {sold_median: 1180, as_of: '%s'}\n"
                   % datetime.now(timezone.utc).date().isoformat())
    r = PriceResolver(db, watchlist, ref)
    pp = r.resolve("rtx-3090")
    assert pp.source == "manual_sold" and pp.est_sold == 1180 and pp.confidence == "high"
    # stale manual entry is ignored -> eBay feed wins
    ref.write_text("max_age_days: 21\nprices:\n  rtx-3090: {sold_median: 1180, as_of: '2020-01-01'}\n")
    r2 = PriceResolver(db, watchlist, ref)
    pp2 = r2.resolve("rtx-3090")
    assert pp2.source == "ebay_active" and pp2.est_sold == 1150 and pp2.confidence == "medium"
    # stale eBay row is ignored -> seed
    r3 = PriceResolver(db, watchlist, ref, now=datetime.now(timezone.utc) + timedelta(days=30))
    assert r3.resolve("rtx-3090").source == "seed"


def test_db_listing_upsert(tmp_path):
    db = Database(tmp_path / "t.db")
    new, prev = db.upsert_listing({"source": "facebook", "listing_id": "1", "title": "x", "price": 100})
    assert new and prev is None
    new, prev = db.upsert_listing({"source": "facebook", "listing_id": "1", "title": "x", "price": 90})
    assert not new and prev == 100
    row = db.get_listing("facebook:1")
    assert row["price"] == 90 and row["last_price"] == 100

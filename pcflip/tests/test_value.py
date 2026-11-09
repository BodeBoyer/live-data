from pcflip.valuation.extract import extract
from pcflip.valuation.value import value_listing, format_summary


def val(watchlist, resolver, econ, ask, title, desc=None):
    return value_listing(ask, extract(title, desc, watchlist), resolver, econ)


def test_prebuilt_strong_buy(watchlist, resolver, econ):
    v = val(watchlist, resolver, econ, 900, "Gaming PC - RTX 4070 Super, Ryzen 7 7800X3D, 32GB DDR5, 2TB NVMe",
            "Specs: Ryzen 7 7800X3D, RTX 4070 Super 12GB, 32GB (2x16GB) DDR5 6000MHz CL30, 2TB 990 Pro NVMe, B650, 750W")
    assert v.kind == "prebuilt"
    assert v.verdict == "STRONG_BUY"
    assert v.est_value > 1800
    assert v.max_buy > 900
    assert v.exit_strategy in ("local_whole", "part_out")
    assert any(i.key == "other-parts" for i in v.items)
    assert v.offer == 900  # never offer above ask


def test_single_part_math_matches_rule(watchlist, resolver, econ):
    # 32GB DDR5 seed 380 -> local exit 361 -> max buy 261 (floor $100 profit)
    v = val(watchlist, resolver, econ, 300, "Corsair Vengeance 32GB DDR5 6000", "2x16GB CL36 kit")
    assert v.exits["local"] == round(380 * econ.local_sale_factor, 2)
    expected_ebay = round(380 * (1 - econ.ebay_fee_rate) - econ.ebay_per_order_fee - econ.shipping_estimate["ram"], 2)
    assert v.exits["ebay"] == expected_ebay
    assert v.best_exit == v.exits["local"]
    assert v.max_buy == round(v.best_exit - 100, 2)
    assert v.verdict == "WATCH"
    assert v.offer <= 300 and v.offer % 5 == 0


def test_verdict_ladder(watchlist, resolver, econ):
    t = "Corsair Vengeance 32GB DDR5 6000"
    assert val(watchlist, resolver, econ, 200, t).verdict == "STRONG_BUY"
    assert val(watchlist, resolver, econ, 255, t).verdict == "BUY"
    assert val(watchlist, resolver, econ, 290, t).verdict == "WATCH"
    assert val(watchlist, resolver, econ, 350, t).verdict == "PASS"


def test_hard_flags_force_pass(watchlist, resolver, econ):
    v = val(watchlist, resolver, econ, 400, "Gaming PC", "rtx 3080 ti, i7 13700k, 64gb ram, 2tb ssd. doesnt turn on, for parts")
    assert v.verdict == "PASS"
    assert "for parts" in v.flags["hard"]
    assert v.best_exit and v.best_exit > 1000  # still shows what working value would be


def test_disqualifiers(watchlist, resolver, econ):
    assert val(watchlist, resolver, econ, 500, "ISO RTX 4090", "cash in hand").verdict == "PASS"
    assert val(watchlist, resolver, econ, 50, "RTX 3090 backplate", "backplate only").verdict == "PASS"
    assert val(watchlist, resolver, econ, 900, "Razer Blade 15 RTX 3070", "laptop").verdict == "PASS"
    assert val(watchlist, resolver, econ, 100, "Nice desk lamp", "works").verdict == "UNKNOWN"


def test_unpriced(watchlist, resolver, econ):
    v = val(watchlist, resolver, econ, None, "Ryzen 9 7950X", "message for price")
    assert v.verdict == "UNPRICED"
    assert v.offer is not None and v.offer < v.max_buy


def test_mining_discount(watchlist, resolver, econ):
    base = val(watchlist, resolver, econ, 700, "RTX 3090", "works")
    mined = val(watchlist, resolver, econ, 700, "RTX 3090", "used for mining")
    assert mined.est_value < base.est_value
    never = val(watchlist, resolver, econ, 700, "RTX 3090", "never mined")
    assert never.est_value == base.est_value


def test_no_gpu_prebuilt_uses_part_out(watchlist, resolver, econ):
    v = val(watchlist, resolver, econ, 300, "Custom PC no gpu", "i7 12700k 32gb ddr4 1tb nvme b660 650w")
    assert v.kind == "prebuilt"
    assert "gpu" not in {i.category for i in v.items}
    assert v.verdict in ("BUY", "STRONG_BUY", "WATCH", "PASS")


def test_format_summary_runs(watchlist, resolver, econ):
    v = val(watchlist, resolver, econ, 900, "RTX 3090 FE", "never mined")
    s = format_summary(v, "RTX 3090 FE")
    assert "RTX 3090" in s and "max buy" in s

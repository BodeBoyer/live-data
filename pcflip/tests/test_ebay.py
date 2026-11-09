import json

import httpx

from pcflip.pricing.ebay import EbayBrowse, EbayCredentials, prices_for_sku, title_ok, estimate_sold, item_total_price, refresh_prices
from pcflip.pricing.stats import summarize
from pcflip.config import Economics, load_settings
from pcflip.db import Database


def fake_item(title, price, ship=None):
    it = {"itemId": "v1|%d|0" % (abs(hash(title)) % 10**9), "title": title, "price": {"value": str(price), "currency": "USD"},
          "condition": "Used", "itemWebUrl": "https://www.ebay.com/itm/1"}
    if ship is not None:
        it["shippingOptions"] = [{"shippingCost": {"value": str(ship), "currency": "USD"}}]
    return it


def test_title_filter(watchlist):
    sku = watchlist.by_key["rtx-3090"]
    assert title_ok("EVGA GeForce RTX 3090 FTW3 Ultra 24GB", sku)
    assert not title_ok("EVGA RTX 3090 Ti FTW3", sku)            # must_not_extra 'ti' + recognizer lookahead
    assert not title_ok("RTX 3090 Backplate only", sku)
    assert not title_ok("MSI RTX 3090 - BOX ONLY no card", sku)
    assert not title_ok("ASUS ROG Laptop RTX 3090 extreme", sku)
    ram = watchlist.by_key["ddr5-32gb"]
    assert title_ok("Corsair Vengeance 32GB (2x16GB) DDR5 6000MHz", ram)
    assert not title_ok("Corsair Vengeance 32GB DDR5 SODIMM laptop", ram)


def test_total_price_uses_cheapest_shipping():
    it = fake_item("x", 100)
    it["shippingOptions"] = [{"shippingCost": {"value": "12.00"}}, {"shippingCost": {"value": "0.00"}}]
    assert item_total_price(it) == 100.0
    assert item_total_price(fake_item("x", 100, 9.5)) == 109.5
    assert item_total_price({"title": "bad"}) is None


def test_estimate_sold_rules():
    econ = Economics()
    stats = summarize([1000, 1050, 1100, 1150, 1200, 1250, 1300, 1400, 1500, 1600])
    est = estimate_sold(stats, econ)
    assert est == round(stats["p25"] * econ.active_to_sold_factor, 2)
    thin = summarize([1000, 1200, 1400])
    assert estimate_sold(thin, econ) == round(1200 * econ.active_to_sold_factor * 0.97, 2)
    assert estimate_sold(summarize([]), econ) is None


def test_refresh_with_mock_transport(tmp_path, watchlist):
    calls = {"token": 0, "search": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            calls["token"] += 1
            assert request.headers["Authorization"].startswith("Basic ")
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 7200})
        assert request.url.path.endswith("/item_summary/search")
        assert request.headers["Authorization"] == "Bearer tok"
        assert request.headers["X-EBAY-C-MARKETPLACE-ID"] == "EBAY_US"
        calls["search"] += 1
        q = request.url.params["q"]
        if "3090" in q:
            items = [fake_item(f"EVGA RTX 3090 FTW3 #{i}", 1000 + i * 40, 0) for i in range(10)]
            items.append(fake_item("RTX 3090 Ti Suprim", 1800))           # filtered: Ti
            items.append(fake_item("RTX 3090 backplate", 40))             # filtered: accessory
            items.append(fake_item("RTX 3090 FE", 3900))                  # outlier trimmed
            return httpx.Response(200, json={"itemSummaries": items, "total": len(items)})
        return httpx.Response(200, json={"itemSummaries": [], "total": 0})

    api = EbayBrowse(EbayCredentials("id", "secret"), client=httpx.Client(transport=httpx.MockTransport(handler)))
    settings = load_settings(tmp_path / "config.toml")
    db = Database(tmp_path / "t.db")
    out = refresh_prices(settings, watchlist, db, api=api, only=["rtx-3090", "ddr5-32gb"], log=lambda *_: None)
    assert calls["token"] == 1 and calls["search"] == 2
    assert out["ddr5-32gb"] is None
    est = out["rtx-3090"]
    assert est is not None and 900 < est < 1100
    rows = [r for r in db.latest_prices("rtx-3090") if r["source"] == "ebay_active"]
    assert rows and rows[0]["sample_size"] == 10
    detail = json.loads(rows[0]["detail"])
    assert detail["raw_count"] == 13 and detail["kept"] == 11


def test_prices_for_sku_filters(watchlist):
    sku = watchlist.by_key["rtx-4070"]
    items = [fake_item("MSI RTX 4070 Ventus 12GB", 650), fake_item("RTX 4070 Ti Super Gaming X", 1100),
             fake_item("RTX 4070 SUPER FE", 850), fake_item("RTX 4070 Laptop GPU board", 300)]
    prices, titles = prices_for_sku(items, sku)
    assert prices == [650.0] and titles == ["MSI RTX 4070 Ventus 12GB"]

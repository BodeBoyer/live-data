import json
from pathlib import Path

from pcflip.config import SearchSpec
from pcflip.listings.facebook import listings_from_graphql, listings_from_cards, description_from_item_text, search_url
from pcflip.listings.apify import normalize_item
from pcflip.listings.manual import from_text
from pcflip.listings.models import parse_price

FIX = Path(__file__).parent / "fixtures"


def test_parse_price():
    assert parse_price("$1,250") == 1250.0
    assert parse_price("$899.99") == 899.99
    assert parse_price("Free") == 0.0
    assert parse_price(None) is None
    assert parse_price("call for price") is None
    assert parse_price(450) == 450.0


def test_graphql_parser():
    payload = json.loads((FIX / "fb_graphql_sample.json").read_text())
    ls = sorted(listings_from_graphql(payload), key=lambda l: l.listing_id)
    assert [l.listing_id for l in ls] == ["1234567890123456", "2234567890123456", "3234567890123456"]
    a = ls[0]
    assert a.title.startswith("Gaming PC RTX 4070") and a.price == 900.0 and a.location == "Austin, TX"
    assert a.url == "https://www.facebook.com/marketplace/item/1234567890123456/"
    assert a.image_url.endswith("photo1.jpg")
    assert ls[1].price == 950.0 and ls[1].location == "Round Rock"
    assert ls[2].price == 0.0
    # garbage / other payload shapes yield nothing rather than crashing
    assert listings_from_graphql({"data": {"viewer": {"id": "1"}}}) == []
    assert listings_from_graphql([1, "x", None]) == []


def test_dom_card_parser():
    cards = [
        {"href": "/marketplace/item/555/?ref=search", "text": "$1,100\nRTX 4080 Super ASUS TUF\nAustin, TX", "img": "https://x/1.jpg"},
        {"href": "/marketplace/item/556/", "text": "Free\nOld desk\nPflugerville, TX", "img": None},
        {"href": "/marketplace/profile/9/", "text": "someone", "img": None},
        {"href": "/marketplace/item/557/", "text": "RTX 3060 no price listed\nCedar Park", "img": None},
    ]
    ls = {l.listing_id: l for l in listings_from_cards(cards)}
    assert set(ls) == {"555", "556", "557"}
    assert ls["555"].price == 1100.0 and ls["555"].title == "RTX 4080 Super ASUS TUF" and ls["555"].location == "Austin, TX"
    assert ls["556"].price == 0.0
    assert ls["557"].price is None and ls["557"].title.startswith("RTX 3060")


def test_description_picker():
    body = "Marketplace\nLog in\n$900\nGaming PC\nAustin, TX\nSeller information\n" \
           "Specs: Ryzen 7 7800X3D, RTX 4070 Super, 32GB DDR5 6000, 2TB NVMe, B650, 750W gold. Runs everything at 1440p.\n" \
           "Message\nSend seller a message"
    d = description_from_item_text(body)
    assert d.startswith("Specs: Ryzen 7 7800X3D")
    assert description_from_item_text("") is None


def test_search_url():
    u = search_url(SearchSpec("gaming pc", "austin", 40, 300, 2500))
    assert u.startswith("https://www.facebook.com/marketplace/austin/search?query=gaming+pc")
    assert "sortBy=creation_time_descend" in u and "minPrice=300" in u and "maxPrice=2500" in u and "radius=40" in u


def test_apify_normalizer():
    l = normalize_item({"id": "777", "title": "RTX 3090", "price": "$950", "location": {"name": "Austin"}, "images": ["https://x/1.jpg"]})
    assert l.listing_id == "777" and l.price == 950.0 and l.location == "Austin" and l.image_url == "https://x/1.jpg"
    l2 = normalize_item({"listingUrl": "https://www.facebook.com/marketplace/item/888/", "marketplace_listing_title": "x", "listing_price": 100})
    assert l2.listing_id == "888" and l2.price == 100.0
    assert normalize_item({"title": "no id"}) is None


def test_manual_from_text():
    l = from_text("Gaming PC RTX 4070 Super 7800X3D\n$900 OBO\n32GB DDR5, 2TB NVMe\nhttps://www.facebook.com/marketplace/item/42/")
    assert l.title == "Gaming PC RTX 4070 Super 7800X3D" and l.price == 900.0 and l.listing_id == "42"
    assert "32GB DDR5" in l.description
    l2 = from_text("RTX 3090 FE works great", price=800)
    assert l2.price == 800 and l2.source == "manual"

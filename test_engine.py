from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from comp_engine import check_offer, comp_parcel, comps_from_csv

TODAY = date(2026, 10, 13)
SUBJ = {"acres": 5, "lat": 35.0, "lon": -82.0}
EX = Path(__file__).parent.parent / "examples"
SOLD_MODE = {"valuation_mode": "sold_weighted"}

COMPS = [
    {"price": 40000, "acres": 5.2, "status": "Sold", "date": "2026-05-01", "distance_mi": 1.2},
    {"price": 36000, "acres": 4.5, "status": "RECENTLY_SOLD", "date": "2026-02-10", "distance_mi": 2.0},
    {"price": 52000, "acres": 7, "status": "sold", "date": "2025-11-01", "distance_mi": 2.8},
    {"price": 45000, "acres": 5, "status": "Pending", "date": "2026-08-01", "distance_mi": 1.5, "address": "Hwy 9"},
    {"price": 59000, "acres": 5, "status": "FOR_SALE", "date": "2026-07-01", "distance_mi": 1.0, "address": "Mill Creek"},
    {"price": 85000, "acres": 6, "status": "for sale", "date": "2026-09-01", "distance_mi": 2.2},
    {"price": 30000, "acres": 5, "status": "Off Market", "distance_mi": 0.5},
]


# ---------------- your method: cheapest matching lot on the market

def test_value_is_cheapest_on_market_lot():
    r = comp_parcel(SUBJ, COMPS, today=TODAY)
    assert r["valuation_mode"] == "cheapest_on_market"
    assert r["price_to_beat"]["address"] == "Hwy 9"       # $45k pending is the cheapest on-market lot
    assert r["market_value"] == 45000
    assert r["decision"] == "OK_TO_OFFER"


def test_offer_is_40_to_60_pct():
    r = comp_parcel(SUBJ, COMPS, today=TODAY)
    o = r["offer"]
    assert o["low"] == 18000 and o["high"] == 27000
    assert 0.40 <= o["pct_of_value"] <= 0.60


def test_off_market_lot_never_used():
    r = comp_parcel(SUBJ, COMPS, today=TODAY)
    off = [c for c in r["comps"] if c["price"] == 30000][0]
    assert not off["included"] and "off-market" in off["reason"]


def test_offer_based_on_off_market_is_blocked():
    bad = 30000 * 0.5                     # bot comped off the $30k off-market lot
    c = check_offer(SUBJ, COMPS, bad, today=TODAY)
    assert c["verdict"] == "BLOCK"
    assert any("OFF-MARKET" in p for p in c["problems"])


def test_good_offer_passes():
    r = comp_parcel(SUBJ, COMPS, today=TODAY)
    assert check_offer(SUBJ, COMPS, r["offer"]["recommended"], today=TODAY)["verdict"] == "PASS"


def test_offer_over_60_pct_blocked():
    assert check_offer(SUBJ, COMPS, 45000 * 0.7, today=TODAY)["verdict"] == "BLOCK"


def test_suspiciously_cheap_listing_skipped():
    comps = COMPS + [{"price": 12000, "acres": 5, "status": "for sale", "date": "2026-09-01",
                      "distance_mi": 1, "address": "Landlocked lot"}]
    r = comp_parcel(SUBJ, comps, today=TODAY)
    assert r["price_to_beat"]["address"] == "Hwy 9"
    assert any("problem" in w for w in r["warnings"])


def test_bigger_listing_is_size_adjusted():
    comps = [
        {"price": 60000, "acres": 10, "status": "for sale", "date": "2026-09-01", "distance_mi": 1},
        {"price": 45000, "acres": 5, "status": "for sale", "date": "2026-09-01", "distance_mi": 1},
    ]
    r = comp_parcel(SUBJ, comps, today=TODAY)
    # 10ac at $6k/ac -> a 5ac lot is worth MORE per acre (~$7.4k/ac), not $6k
    assert 7000 < r["market_price_per_acre"] < 7800


def test_no_listings_falls_back_to_sold_and_needs_human():
    sold_only = [c for c in COMPS if "sold" in c["status"].lower()]
    r = comp_parcel(SUBJ, sold_only, today=TODAY)
    assert r["decision"] == "NEEDS_HUMAN_REVIEW"
    assert any("NO matching lots on the market" in w for w in r["warnings"])


def test_no_comps_do_not_offer():
    assert comp_parcel(SUBJ, [], today=TODAY)["decision"] == "DO_NOT_OFFER"


def test_stale_listing_ignored():
    comps = COMPS + [{"price": 20000, "acres": 5, "status": "active", "date": "2025-01-01", "distance_mi": 1}]
    r = comp_parcel(SUBJ, comps, today=TODAY)
    assert "stale" in [c for c in r["comps"] if c["price"] == 20000][0]["reason"]


# ---------------- optional sold-based mode still works

def test_sold_mode_values_off_sales():
    r = comp_parcel(SUBJ, COMPS, settings=SOLD_MODE, today=TODAY)
    assert 6500 <= r["market_price_per_acre"] <= 9500


# ---------------- imports + API

def test_csv_imports():
    z, zs = comps_from_csv((EX / "zillow_export.csv").read_text(), "zillow")
    lp, ls = comps_from_csv((EX / "landportal_export.csv").read_text(), "landportal")
    assert len(z) == 5 and not zs
    assert abs(z[1]["acres"] - 4.5) < 0.01          # sqft converted
    assert len(lp) == 3 and all(c["status"] == "sold" for c in lp)
    r = comp_parcel(SUBJ, z + lp, today=TODAY)
    assert r["decision"] == "OK_TO_OFFER"


def test_redfin_style_csv():
    text = ("SALE TYPE,SOLD DATE,PROPERTY TYPE,ADDRESS,PRICE,LOT SIZE,STATUS,LATITUDE,LONGITUDE\n"
            "MLS Listing,,Vacant Land,Lot 1 Oak Rd,52000,217800,Active,35.01,-82.0\n"
            "MLS Listing,,Vacant Land,Lot 2 Oak Rd,61000,217800,Active,35.02,-82.0\n"
            "PAST SALE,May-01-2026,Vacant Land,Lot 3 Oak Rd,38000,217800,Sold,35.0,-82.01\n")
    c, skipped = comps_from_csv(text, "redfin")
    assert len(c) == 3 and not skipped and abs(c[0]["acres"] - 5) < 0.01
    r = comp_parcel(SUBJ, c, today=TODAY)
    assert r["market_value"] == 52000


def test_api():
    from api import app
    cl = TestClient(app)
    assert cl.post("/comp", json={"subject": SUBJ, "comps": COMPS}).json()["market_value"] == 45000
    files = [("files", ("zillow_export.csv", (EX / "zillow_export.csv").read_bytes(), "text/csv")),
             ("files", ("landportal_export.csv", (EX / "landportal_export.csv").read_bytes(), "text/csv"))]
    r = cl.post("/comp/csv", data={"acres": 5, "lat": 35.0, "lon": -82.0}, files=files).json()
    assert r["counts"]["for_sale"] >= 2

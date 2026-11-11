"""The made-up upcoming "Saratoga Sale (2026)": catalog list, filters, stars/notes and every hip page.

Every hip's detail page is fetched once (session fixture); each panel test then
lists the hips where that panel came back empty.
"""

import pytest

from conftest import SARATOGA_ID, q

BASE = f"/api/sales/upcoming/{q(SARATOGA_ID)}"


def fmt(hips, limit=25):
    hips = sorted(hips, key=lambda h: int(h) if str(h).isdigit() else 10**9)
    more = f" (+{len(hips) - limit} more)" if len(hips) > limit else ""
    return ", ".join(map(str, hips[:limit])) + more


@pytest.fixture(scope="session")
def lots(api):
    data = api.ok(f"{BASE}/lots")
    return data.get("lots") or []


@pytest.fixture(scope="session")
def details(api, lots):
    out = {}
    for lot in lots:
        status, body = api.get(f"{BASE}/lots/{q(lot['hip'])}")
        out[lot["hip"]] = body if status == 200 else {"_status": status, "_body": str(body)[:200]}
    return out


def missing(details, check):
    return [hip for hip, d in details.items() if "_status" in d or not check(d)]


# --- catalog ----------------------------------------------------------------

def test_listed_as_upcoming(api, saratoga_catalog):
    cats = api.ok("/api/sales/upcoming").get("catalogs") or []
    hit = next((c for c in cats if c.get("id") == SARATOGA_ID), None)
    assert hit, f"{SARATOGA_ID} not in upcoming catalogs ({[c.get('id') for c in cats]})"
    assert hit.get("source") == "upcoming"
    assert hit.get("hips") == len(saratoga_catalog), f"catalog says {hit.get('hips')} hips, file has {len(saratoga_catalog)}"
    assert str(hit.get("year")) == "2026"


def test_all_hips_listed(lots, saratoga_catalog):
    assert len(lots) == len(saratoga_catalog), f"{len(lots)} lots listed, catalog has {len(saratoga_catalog)}"
    hips = [lot["hip"] for lot in lots]
    assert hips == sorted(hips, key=lambda h: int(h) if h.isdigit() else 10**9), "lots not in hip order"


@pytest.mark.parametrize("field", ["hip", "name", "sire", "dam", "damsire", "sex_letter", "sex_label",
                                   "color_label", "consignor", "barn", "birth_year", "sold_as"])
def test_list_fields(lots, field):
    bad = [lot["hip"] for lot in lots if not lot.get(field)]
    assert not bad, f"lot list: '{field}' empty on hips {fmt(bad)}"


def test_list_names_are_readable(lots):
    bad = [lot["hip"] for lot in lots if lot["name"].startswith("2025-")]
    assert not bad, f"unnamed yearlings not shown as 'Sire x Dam': hips {fmt(bad)}"


def test_sold_as_yearling(lots):
    bad = [lot["hip"] for lot in lots if lot.get("sold_as") != "Yearling"]
    assert not bad, f"hips not classed as yearlings: {fmt(bad)}"


def test_list_expected_market_price(lots):
    bad = [lot["hip"] for lot in lots if not lot.get("filter_price")]
    assert not bad, f"no Expected Market price (filter_price) on hips {fmt(bad)}"
    src = {lot.get("filter_price_source") for lot in lots}
    assert src <= {"expected_market_mid"}, f"unexpected price sources {src}"


# --- filters ----------------------------------------------------------------

def test_filter_sex(api, lots):
    for sex in ("C", "F"):
        got = api.ok(f"{BASE}/lots?sex={sex}").get("lots") or []
        expected = [lot["hip"] for lot in lots if lot.get("sex_letter") == sex]
        assert got, f"sex={sex}: no lots"
        assert [lot["hip"] for lot in got] == expected, f"sex={sex}: filter returned the wrong hips"


def test_filter_color(api, lots):
    got = api.ok(f"{BASE}/lots?color=ch").get("lots") or []
    assert got and all("ch" in (lot.get("color") or "").lower() for lot in got), "color=ch filter"


def test_filter_max_price(api, lots):
    prices = sorted(lot["filter_price"] for lot in lots if lot.get("filter_price"))
    cap = prices[len(prices) // 2]
    got = api.ok(f"{BASE}/lots?max_price={cap}").get("lots") or []
    assert got and all(lot["filter_price"] <= cap for lot in got), f"max_price={cap} filter"


def test_filter_only_and_exclude_hips(api, lots):
    first = [lot["hip"] for lot in lots[:3]]
    only = api.ok(f"{BASE}/lots?only_hips={','.join(first)}").get("lots") or []
    assert [lot["hip"] for lot in only] == first, "only_hips"
    rest = api.ok(f"{BASE}/lots?exclude_hips={','.join(first)}").get("lots") or []
    assert len(rest) == len(lots) - 3 and not set(first) & {lot["hip"] for lot in rest}, "exclude_hips"


def test_star_and_note_round_trip(api, lots):
    hip = lots[0]["hip"]
    status, body = api.post(f"{BASE}/lots/{q(hip)}", {"starred": True, "notes": "e2e test"}, cache=False)
    assert status == 200 and body["lot"]["starred"] and body["lot"]["notes"] == "e2e test", f"star hip {hip}: {body}"
    starred = api.client.get(f"{BASE}/lots?starred=true").json().get("lots") or []
    assert [lot["hip"] for lot in starred] == [hip], f"starred filter returned {[lot['hip'] for lot in starred]}"
    status, body = api.post(f"{BASE}/lots/{q(hip)}", {"starred": False, "notes": ""}, cache=False)
    assert status == 200 and not body["lot"]["starred"] and body["lot"]["notes"] == "", f"unstar hip {hip}: {body}"
    assert api.client.post(f"{BASE}/lots/99999", json={"starred": True}).status_code == 404


# --- every hip page -----------------------------------------------------------

def test_every_hip_page_loads(details):
    bad = {hip: d for hip, d in details.items() if "_status" in d}
    assert not bad, f"hip pages failing: {[(h, d['_status']) for h, d in list(bad.items())[:10]]}"


def test_hip_pedigree(details):
    bad = missing(details, lambda d: (d.get("pedigree") or {}).get("sire", {}).get("name")
                  and (d.get("pedigree") or {}).get("dam", {}).get("name"))
    assert not bad, f"no sire/dam pedigree on hips {fmt(bad)}"


def test_hip_pedigree_depth(details):
    def deep(d):
        ped = d.get("pedigree") or {}
        return all((((ped.get(side) or {}).get("sire") or {}).get("sire") or {}).get("name") for side in ("sire", "dam"))
    bad = missing(details, deep)
    assert not bad, f"pedigree under 3 generations on hips {fmt(bad)}"


def test_hip_female_family(details):
    bad = missing(details, lambda d: d.get("female_family"))
    assert not bad, f"no female family on hips {fmt(bad)}"


def test_hip_expected_market(details):
    def ok(d):
        em = d.get("expected_market") or {}
        return em.get("low") and em.get("high") and em.get("mid") and em["low"] <= em["mid"] <= em["high"]
    bad = missing(details, ok)
    assert not bad, f"no Expected Market range on hips {fmt(bad)}"


def test_hip_sales_model(details):
    bad = missing(details, lambda d: (d.get("expected_market") or {}).get("sales_model"))
    assert not bad, f"no sales-model projection on hips {fmt(bad)}"


def test_hip_stud_fee_at_conception(details):
    bad = missing(details, lambda d: (d.get("expected_market") or {}).get("sire_stud_fee"))
    assert not bad, f"no sire stud fee resolved on hips {fmt(bad)}"


def test_hip_market_prediction(details):
    bad = missing(details, lambda d: (d.get("market_prediction") or {}).get("ready")
                  and (d.get("market_prediction") or {}).get("boom_pct") is not None)
    assert not bad, f"no Boom % / market prediction on hips {fmt(bad)}"


def test_hip_similar_crosses(details):
    bad = missing(details, lambda d: d.get("similar_crosses"))
    assert not bad, f"no successful similar crosses on hips {fmt(bad)}"


def test_hip_auction_history(details):
    bad = missing(details, lambda d: d.get("auction_history"))
    assert not bad, f"no auction history on hips {fmt(bad)}"


def test_hip_lot_matches_catalog(details, lots):
    by_hip = {lot["hip"]: lot for lot in lots}
    bad = [hip for hip, d in details.items() if "_status" not in d and
           ((d.get("lot") or {}).get("sire"), (d.get("lot") or {}).get("dam")) != (by_hip[hip]["sire"], by_hip[hip]["dam"])]
    assert not bad, f"hip page shows a different horse than the list on hips {fmt(bad)}"


# --- the same sale on the past-results side ------------------------------------

@pytest.fixture(scope="session")
def past_catalog(api):
    cats = api.ok("/api/sales/catalogs?year=2026").get("catalogs") or []
    hit = next((c for c in cats if "saratoga" in c.get("title", "").lower()), None)
    assert hit, f"2026 Saratoga not among past catalogs ({[c.get('title') for c in cats]})"
    return hit


def test_past_catalog_lots(api, past_catalog, saratoga_catalog):
    data = api.ok(f"/api/sales/lots?catalog_id={past_catalog['id']}&limit=200")
    assert data.get("total") == len(saratoga_catalog), f"past results: {data.get('total')} lots vs {len(saratoga_catalog)}"
    assert data.get("stats"), "past results: no sale stats"
    assert all(lot.get("price_raw") for lot in data.get("lots") or []), "past results: lots without a result"


def test_past_catalog_nav(api, past_catalog, saratoga_catalog):
    data = api.ok(f"/api/sales/catalogs/{past_catalog['id']}/nav")
    nav = data.get("lots") or []
    assert len(nav) == len(saratoga_catalog), f"nav: {len(nav)} lots"
    assert all(lot.get("filter_price") for lot in nav), "nav: lots without a price"


def test_past_lot_pages(api, past_catalog):
    lots = api.ok(f"/api/sales/lots?catalog_id={past_catalog['id']}&limit=10&sort=price_desc").get("lots") or []
    for lot in lots[:5]:
        d = api.ok(f"/api/sales/lots/{lot['id']}")
        assert d.get("lot", {}).get("hip") == lot["hip"]
        assert d.get("comparables"), f"past hip {lot['hip']}: no comparables"
        assert d.get("sire_stats"), f"past hip {lot['hip']}: no sire stats"
        assert d.get("pedigree"), f"past hip {lot['hip']}: no pedigree"
        assert d.get("expected_market"), f"past hip {lot['hip']}: no expected market"


def test_sales_years_and_horse_search(api):
    years = api.ok("/api/sales/years").get("years") or []
    assert 2026 in years and min(years) <= 2000, f"sale years {years[:3]}..{years[-3:]}"
    data = api.ok(f"/api/sales/horse?name={q('FLIGHTLINE')}")
    assert data.get("lots"), "sales history for Flightline is empty"

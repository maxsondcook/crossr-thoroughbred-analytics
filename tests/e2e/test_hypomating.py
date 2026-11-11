"""Hypomating for the test broodmares: suggestions, fee / surface filters, and full foal reports."""

import pytest

from conftest import AMBIGUOUS_MARES, BROODMARES, REPORT_STALLIONS, STALLIONS, q

# Report stallions with stakes-winning progeny; the others (Flightline) rightly have no proven crosses.
PROVEN_STALLIONS = [s for s in REPORT_STALLIONS if STALLIONS[s]["progeny"]]

SUGGEST = "/api/hypomating/suggest"


def suggest(api, mare, **opts):
    status, data = api.post(SUGGEST, {"mare": mare.upper(), **opts})
    assert status == 200, f"{mare} {opts}: suggest -> {status}: {str(data)[:300]}"
    return data


@pytest.mark.parametrize("mare,years", AMBIGUOUS_MARES.items())
def test_ambiguous_mare_asks_for_year(api, mare, years):
    status, data = api.post(SUGGEST, {"mare": mare.upper()})
    assert status == 409, f"{mare}: expected a which-year prompt (409), got {status}"
    detail = data.get("detail") or {}
    assert detail.get("error") == "ambiguous_year" and sorted(map(str, detail.get("years") or [])) == years, detail


@pytest.mark.parametrize("mare", BROODMARES)
def test_broodmare_found(api, mare):
    data = suggest(api, mare)
    assert not data.get("error"), f"{mare}: {data.get('error')}"
    brood = data.get("broodmare") or {}
    ped = brood.get("pedigree") or {}
    assert (ped.get("sire") or {}).get("name") and (ped.get("dam") or {}).get("name"), f"{mare}: broodmare pedigree incomplete"
    assert data.get("damsire"), f"{mare}: damsire not resolved"


@pytest.mark.parametrize("mare", BROODMARES)
def test_broodmare_panels(api, mare):
    brood = suggest(api, mare).get("broodmare") or {}
    assert brood.get("female_family"), f"{mare}: female family empty"
    assert isinstance((brood.get("inbreeding") or {}).get("coefficient_pct"), (int, float)), f"{mare}: no inbreeding report"
    assert brood.get("race_record") or brood.get("progeny"), f"{mare}: neither race record nor produce record"


@pytest.mark.parametrize("mare", BROODMARES)
def test_suggestions(api, mare):
    sugg = suggest(api, mare).get("suggestions") or []
    assert len(sugg) >= 3, f"{mare}: only {len(sugg)} stallion suggestions"
    for row in sugg:
        assert row.get("name"), f"{mare}: suggestion without a name"
        assert row.get("fee_num") is not None, f"{mare}: {row.get('name')} has no stud fee"
        assert row.get("crosses") or any((row.get("reasons_by_tier") or {}).values()), (
            f"{mare}: {row.get('name')} suggested with no proven cross or reason"
        )


@pytest.mark.parametrize("mare", BROODMARES)
def test_alternatives(api, mare):
    data = suggest(api, mare)
    assert data.get("lineage_groups"), f"{mare}: no sons/ancestor alternatives (lineage_groups empty)"


@pytest.mark.parametrize("mare", BROODMARES)
@pytest.mark.parametrize("max_fee", [50000, 15000])
def test_fee_cap(api, mare, max_fee):
    data = suggest(api, mare, max_fee=max_fee)
    sugg = data.get("suggestions") or []
    over = [(s["name"], s["fee_num"]) for s in sugg if (s.get("fee_num") or 0) > max_fee]
    assert not over, f"{mare}: fee cap ${max_fee:,} let through {over}"
    assert sugg or data.get("lineage_groups"), f"{mare}: nothing at all under ${max_fee:,}"


@pytest.mark.parametrize("mare", BROODMARES)
@pytest.mark.parametrize("surface", ["dirt", "turf"])
def test_surface_preference(api, mare, surface):
    data = suggest(api, mare, surface=surface)
    assert data.get("suggestions") or data.get("lineage_groups"), f"{mare}: no {surface} suggestions"


@pytest.mark.parametrize("stallion", PROVEN_STALLIONS)
def test_stallion_has_proven_crosses(api, stallion):
    crosses = api.ok(f"/api/hypomating/crosses/{q(stallion)}").get("crosses") or []
    assert crosses, f"{stallion}: no proven crosses"


@pytest.mark.parametrize("mare", BROODMARES)
@pytest.mark.parametrize("stallion", REPORT_STALLIONS)
def test_stallion_x_mare_report(api, stallion, mare):
    status, rep = api.post("/api/hypomating/report", {"sire": stallion, "dam": mare.upper()})
    assert status == 200, f"{stallion} x {mare}: report -> {status}: {str(rep)[:300]}"
    ped = rep.get("pedigree") or {}
    assert (ped.get("sire") or {}).get("name") and (ped.get("dam") or {}).get("name"), f"{stallion} x {mare}: foal pedigree incomplete"
    assert isinstance((rep.get("inbreeding") or {}).get("coefficient_pct"), (int, float)), f"{stallion} x {mare}: no inbreeding"
    assert rep.get("female_family"), f"{stallion} x {mare}: female family empty"
    assert rep.get("similar_stallions"), f"{stallion} x {mare}: no similar stallions"
    # Similar crosses are the sire's stakes winners (and the auction history theirs).
    if STALLIONS[stallion]["progeny"]:
        assert rep.get("similar_crosses"), f"{stallion} x {mare}: no similar crosses"
        assert rep.get("auction_history"), f"{stallion} x {mare}: no auction history"


@pytest.mark.parametrize("mare", BROODMARES)
def test_foal_reports(api, mare):
    data = suggest(api, mare)
    stallions = [s["name"] for s in (data.get("suggestions") or [])[:3]]
    assert stallions, f"{mare}: no suggestions to report on"
    for sire in stallions:
        status, rep = api.post("/api/hypomating/report", {"sire": sire, "dam": mare.upper()})
        assert status == 200, f"{mare} x {sire}: report -> {status}: {str(rep)[:300]}"
        ped = rep.get("pedigree") or {}
        assert (ped.get("sire") or {}).get("name") and (ped.get("dam") or {}).get("name"), f"{sire} x {mare}: foal pedigree incomplete"
        assert isinstance((rep.get("inbreeding") or {}).get("coefficient_pct"), (int, float)), f"{sire} x {mare}: no inbreeding"
        assert rep.get("female_family"), f"{sire} x {mare}: female family empty"
        assert rep.get("similar_crosses"), f"{sire} x {mare}: no similar crosses"
        assert rep.get("similar_stallions"), f"{sire} x {mare}: no similar stallions"
        assert rep.get("auction_history"), f"{sire} x {mare}: no auction history for the similar crosses"

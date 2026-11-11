"""Stallion profile page, search, directory and stallion-level sales / hypomating panels."""

import pytest

from conftest import STALLIONS, q

NAMES = list(STALLIONS)
PROGENY = [n for n, f in STALLIONS.items() if f["progeny"]]
FULL_PROGENY = [n for n, f in STALLIONS.items() if f["progeny"] and not f.get("light")]
BMS = [n for n, f in STALLIONS.items() if f["bms"]]
LAST_SEASON = 2025  # latest breeding season with published mares-bred / stud-fee data


def profile(api, name):
    return api.ok(f"/api/horses/{q(name.upper())}")


def walk(node, *path):
    for step in path:
        node = (node or {}).get(step)
    return node


# --- identity and pedigree ---------------------------------------------------

@pytest.mark.parametrize("name", NAMES)
def test_profile_found(api, name):
    p = profile(api, name)
    assert p.get("has_profile"), f"{name}: no stud card (has_profile false)"
    assert p.get("has_pedigree"), f"{name}: no pedigree"
    assert str(p.get("year")) == str(STALLIONS[name]["foaled"]), f"{name}: foaled {p.get('year')!r}, expected {STALLIONS[name]['foaled']}"


@pytest.mark.parametrize("name", NAMES)
def test_pedigree_four_generations(api, name):
    ped = profile(api, name)["pedigree"]
    missing = [
        "/".join(path)
        for path in (("sire", "sire", "sire", "sire"), ("sire", "dam", "dam", "dam"),
                     ("dam", "sire", "sire", "sire"), ("dam", "dam", "dam", "dam"))
        if not walk(ped, *path, "name")
    ]
    assert not missing, f"{name}: pedigree gaps at {missing}"


@pytest.mark.parametrize("name", NAMES)
def test_inbreeding_report(api, name):
    inb = profile(api, name).get("inbreeding") or {}
    assert isinstance(inb.get("coefficient_pct"), (int, float)), f"{name}: no inbreeding coefficient"
    for key in ("cross_inbreeding", "sire_side_linebreeding", "dam_side_linebreeding"):
        assert isinstance(inb.get(key), list), f"{name}: inbreeding.{key} missing"


@pytest.mark.parametrize("name", NAMES)
def test_female_family(api, name):
    fam = profile(api, name).get("female_family") or []
    assert fam, f"{name}: female family is empty"
    assert any(row.get("produce") for row in fam), f"{name}: female family has no produce listed"


# --- race record -------------------------------------------------------------

@pytest.mark.parametrize("name", NAMES)
def test_race_record(api, name):
    p = profile(api, name)
    assert p.get("race_record"), f"{name}: race record is empty"
    totals = p.get("race_totals") or {}
    assert totals.get("starts", 0) > 0, f"{name}: race totals show no starts ({totals})"
    assert totals.get("firsts", 0) > 0, f"{name}: race totals show no wins ({totals})"
    assert totals.get("earnings", 0) > 0, f"{name}: race totals show no earnings ({totals})"


# --- stud card ---------------------------------------------------------------

@pytest.mark.parametrize("name", NAMES)
def test_stud_card(api, name):
    st = profile(api, name).get("stallion") or {}
    problems = []
    if not st.get("farm") or st.get("farm", "").lower() in {"unknown", "n/a"}:
        problems.append(f"farm {st.get('farm')!r}")
    if not st.get("stud_fee_num"):
        problems.append(f"stud fee {st.get('stud_fee')!r}")
    if str(st.get("entered_stud")) != str(STALLIONS[name]["entered_stud"]):
        problems.append(f"entered stud {st.get('entered_stud')!r}, expected {STALLIONS[name]['entered_stud']}")
    if not st.get("crop"):
        problems.append("no crop label")
    if not st.get("sire") or not st.get("dam"):
        problems.append(f"sire/dam {st.get('sire')!r}/{st.get('dam')!r}")
    # A general sire rank needs earners; sires without stakes-winning progeny may have none.
    if STALLIONS[name]["progeny"] and st.get("rank") in (None, ""):
        problems.append("no stud rank")
    assert not problems, f"{name}: " + "; ".join(problems)


@pytest.mark.parametrize("name", NAMES)
def test_mares_bred_last_season(api, name):
    if STALLIONS[name]["entered_stud"] > LAST_SEASON:
        pytest.skip(f"{name} entered stud after {LAST_SEASON}")
    p = profile(api, name)
    rows = {row.get("year"): row for row in p.get("stud_book") or []}
    row = rows.get(LAST_SEASON) or {}
    assert row.get("mares_bred"), f"{name}: no {LAST_SEASON} mares bred in the stud book (years: {sorted(rows)})"
    assert (p.get("stallion") or {}).get("mares_bred_2025"), f"{name}: stud card mares_bred_2025 is empty"


@pytest.mark.parametrize("name", NAMES)
def test_live_foal_rates(api, name):
    st = profile(api, name).get("stallion") or {}
    entered = STALLIONS[name]["entered_stud"]
    if entered <= 2023:
        assert st.get("live_foal_2024"), f"{name}: no 2024 live-foal %"
    if entered <= 2024:
        assert st.get("live_foal_2025"), f"{name}: no 2025 live-foal %"


@pytest.mark.parametrize("name", NAMES)
def test_stud_book_history(api, name):
    entered = STALLIONS[name]["entered_stud"]
    rows = {row.get("year"): row for row in profile(api, name).get("stud_book") or []}
    seasons = range(entered, LAST_SEASON + 1)
    missing = [y for y in seasons if y not in rows]
    assert not missing, f"{name}: stud book missing seasons {missing}"
    no_fee = [y for y in seasons if not (rows[y].get("stud_fee") or "").strip()]
    assert not no_fee, f"{name}: stud book seasons without a stud fee {no_fee}"
    no_mares = [y for y in seasons if not rows[y].get("mares_bred")]
    assert not no_mares, f"{name}: stud book seasons without mares bred {no_mares}"


# --- rankings ----------------------------------------------------------------

@pytest.mark.parametrize("name", NAMES)
def test_rankings_fee_and_book(api, name):
    rows = (profile(api, name).get("sire_rankings") or {}).get("rows") or {}
    assert rows.get("stud_fee"), f"{name}: no stud-fee ranking rows"
    assert rows.get("mares_bred"), f"{name}: no mares-bred ranking rows"


@pytest.mark.parametrize("name", PROGENY)
def test_rankings_sire_lists(api, name):
    rows = (profile(api, name).get("sire_rankings") or {}).get("rows") or {}
    sire_lists = [k for k, v in rows.items() if v and k not in {"stud_fee", "mares_bred"}]
    assert sire_lists, f"{name}: on no BloodHorse sire list"
    if not STALLIONS[name].get("light"):
        assert rows.get("leading_sire"), f"{name}: no Leading Sire ranking rows (has: {sorted(sire_lists)})"


@pytest.mark.parametrize("name", BMS)
def test_broodmare_sire_history(api, name):
    bms = profile(api, name).get("broodmare_sire") or []
    assert bms, f"{name}: broodmare-sire history is empty"
    assert any(row.get("rank") for row in bms), f"{name}: broodmare-sire rows have no rank"


# --- progeny -----------------------------------------------------------------

@pytest.mark.parametrize("name", PROGENY)
def test_top_progeny(api, name):
    top = profile(api, name).get("top_progeny") or []
    assert top, f"{name}: top progeny is empty"
    unnamed = [row for row in top if not row.get("name") or not row.get("class")]
    assert not unnamed, f"{name}: top progeny rows missing name/class: {unnamed[:3]}"
    if not STALLIONS[name].get("light"):
        assert len(top) >= 5, f"{name}: only {len(top)} top progeny"
        assert any(row.get("earnings") for row in top), f"{name}: no earnings on any top progeny"


@pytest.mark.parametrize("name", PROGENY)
def test_progeny_statistics(api, name):
    ps = (profile(api, name).get("stallion") or {}).get("progeny_stats") or {}
    career, season = ps.get("career") or {}, ps.get("season") or {}
    for field in ("Crops", "Foals", "Stakes Winners", "Graded Stakes Winners"):
        assert career.get(field), f"{name}: Progeny Statistics career has no {field} ({career})"
    for field in ("Runners", "Winners"):
        assert season.get(field), f"{name}: Progeny Statistics season has no {field} ({season})"
    assert (ps.get("earnings") or {}).get("AEI"), f"{name}: Progeny Statistics has no AEI"
    assert ps.get("by_year"), f"{name}: Progeny Statistics has no per-year rows"


@pytest.mark.parametrize("name", FULL_PROGENY)
def test_proven_crosses(api, name):
    data = api.ok(f"/api/hypomating/crosses/{q(name)}")
    crosses = data.get("crosses") or []
    assert crosses, f"{name}: no proven crosses"
    assert all(c.get("winners") for c in crosses), f"{name}: a cross has no winners"


@pytest.mark.parametrize("name", NAMES)
def test_lineage_options(api, name):
    data = api.ok(f"/api/hypomating/lineage/{q(name)}")
    assert isinstance(data.get("options"), list), f"{name}: lineage options missing"


@pytest.mark.parametrize("name", PROGENY)
def test_sales_by_sire(api, name):
    data = api.ok(f"/api/sales/sire/{q(name)}?limit=20")
    assert data.get("total", 0) > 0, f"{name}: no offspring in the sales index"
    assert data.get("lots"), f"{name}: sales-by-sire returned no lots"
    assert data.get("stats"), f"{name}: sales-by-sire returned no stats"


# --- search, directory, compare ----------------------------------------------

@pytest.mark.parametrize("name", NAMES)
def test_search_finds_stallion(api, name):
    results = api.ok(f"/api/search?q={q(name)}").get("results") or []
    names = [r.get("name", "").upper() for r in results[:5]]
    assert name.upper() in names, f"search {name!r}: not in top 5 ({names})"
    hit = next(r for r in results if r.get("name", "").upper() == name.upper())
    assert hit.get("has_profile"), f"search {name!r}: result has no stud card"


@pytest.mark.parametrize("name", NAMES)
def test_stallion_directory(api, name):
    rows = api.ok(f"/api/stallions?q={q(name)}").get("stallions") or []
    hit = next((r for r in rows if r.get("name", "").upper() == name.upper()), None)
    if STALLIONS[name].get("abroad"):
        assert not hit, f"{name} stands abroad and should not be in the stallion directory"
        return
    assert hit, f"stallion directory search {name!r}: not listed ({[r.get('name') for r in rows[:5]]})"
    assert str(hit.get("entered_stud")) == str(STALLIONS[name]["entered_stud"]), (
        f"{name}: directory says entered stud {hit.get('entered_stud')!r} / crop {hit.get('crop')!r}, "
        f"expected {STALLIONS[name]['entered_stud']}"
    )
    assert hit.get("stud_fee_num"), f"{name}: directory row has no stud fee"
    assert hit.get("farm"), f"{name}: directory row has no farm"


def test_directory_sorts_and_filters(api):
    full = api.ok("/api/stallions").get("stallions") or []
    assert len(full) > 100, f"stallion directory has only {len(full)} rows"
    by_fee = api.ok("/api/stallions?sort=fee&max_fee=50000").get("stallions") or []
    assert by_fee and all((r.get("stud_fee_num") or 0) <= 50000 for r in by_fee), "max_fee filter let pricier stallions through"
    farms = api.ok("/api/stallions/farms").get("farms") or []
    assert len(farms) > 10, f"only {len(farms)} farms"


@pytest.mark.parametrize("name", [n for n in NAMES if n != "Into Mischief"])
def test_compare_with_into_mischief(api, name):
    data = api.ok(f"/api/similarity?a={q(name.upper())}&b=INTO%20MISCHIEF")
    assert isinstance(data.get("percent"), (int, float)), f"compare {name} vs Into Mischief: no similarity % ({str(data)[:200]})"

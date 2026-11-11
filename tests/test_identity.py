"""Horse identity and classification rules (crossr_core.identity).

Pure-logic tests: the ranking identities and offspring counts are stubbed, so
these run without a database.
"""

import pytest

from crossr_core import identity


@pytest.fixture(autouse=True)
def stub_sources(monkeypatch):
    ids = {
        "ENGLISHMAN": {(2002, "GENTLEMEN")},
        "CURLIN": {(2004, "SMART STRIKE")},
        "STAGE MAGIC": set(),
    }
    offspring = {
        ("ENGLISHMAN", 2002): (12, 0),
        ("CURLIN", 2004): (900, 0),
        ("ZENYATTA", 2004): (0, 5),
        ("BEHOLDER", 2010): (0, 3),
    }
    monkeypatch.setattr(identity, "card_identities", lambda: ids)
    monkeypatch.setattr(
        identity, "offspring_counts", lambda facts: offspring.get((facts["name"], facts.get("year")), (0, 0))
    )
    monkeypatch.setattr(identity, "current_year", lambda: 2026)


def facts(name, year, sex=None, sire=""):
    return {"name": name, "year": year, "sex": sex, "sire": sire, "dam": ""}


ENGLISHMAN_CARD = {"Stud Duty Year": 2009, "Latest Season": 2015, "Year Born": 2002}
CURLIN_CARD = {"Stud Duty Year": 2009, "Latest Season": 2026, "Year Born": 2004}


def test_young_namesake_does_not_get_old_stallions_card():
    ok, reason = identity.card_matches("Englishman", ENGLISHMAN_CARD, facts("ENGLISHMAN", 2023, sire="MAXFIELD"))
    assert not ok
    assert "2023" in reason


def test_card_rejected_when_too_young_for_first_season_even_without_known_card_year():
    card = {"Stud Duty Year": 2009, "Latest Season": 2015, "Year Born": 0}
    ok, _ = identity.card_matches("Stage Magic", card, facts("STAGE MAGIC", 2015))
    assert not ok


def test_matching_stallion_keeps_card():
    ok, _ = identity.card_matches("Curlin", CURLIN_CARD, facts("CURLIN", 2004, sex="H", sire="SMART STRIKE"))
    assert ok


def test_different_sire_rejects_card():
    ok, reason = identity.card_matches("Curlin", CURLIN_CARD, facts("CURLIN", 2004, sire="TAPIT"))
    assert not ok
    assert "sire" in reason


@pytest.mark.parametrize("sex", ["F", "M"])
def test_females_never_get_a_stud_card(sex):
    ok, _ = identity.card_matches("Curlin", CURLIN_CARD, facts("CURLIN", 2004, sex=sex))
    assert not ok


def test_gelding_namesake_does_not_get_a_stud_card():
    ok, reason = identity.card_matches("Curlin", {"Stud Duty Year": 2009, "Year Born": 0}, facts("CURLIN", 2001, sex="G"))
    assert not ok


def test_exact_stud_record_beats_a_wrong_gelding_code():
    # McCraken: race data says "G", but the stud record is for the same horse (same foaling year).
    ok, _ = identity.card_matches("Curlin", CURLIN_CARD, facts("CURLIN", 2004, sex="G"))
    assert ok
    assert identity.classify(facts("CURLIN", 2004, sex="G"), card=CURLIN_CARD)["profile_type"] == "stallion"


def test_zenyatta_is_a_broodmare_not_a_stallion():
    c = identity.classify(facts("ZENYATTA", 2004, sex="F"), card=None)
    assert c["profile_type"] == "broodmare"
    assert c["sex"] == "Mare"
    assert not c["is_stallion"]


def test_englishman_2023_is_a_racing_colt():
    c = identity.classify(facts("ENGLISHMAN", 2023, sex="C"), card=None)
    assert c["profile_type"] == "racing_male"
    assert c["sex"] == "Colt"
    assert not c["is_stallion"]
    assert c["stallion_status"] is None


def test_active_and_inactive_stallions():
    active = identity.classify(facts("CURLIN", 2004, sex="H"), card=CURLIN_CARD)
    assert active["profile_type"] == "stallion" and active["stallion_status"] == "active"
    inactive = identity.classify(facts("ENGLISHMAN", 2002, sex="H"), card=ENGLISHMAN_CARD)
    assert inactive["stallion_status"] == "inactive"


def test_pensioned_and_deceased_stallions():
    pensioned = dict(CURLIN_CARD, **{"Stud Fee Display": "Pensioned"})
    assert identity.classify(facts("CURLIN", 2004, sex="H"), card=pensioned)["stallion_status"] == "pensioned"
    died = dict(CURLIN_CARD, **{"Stud Fee Display": "Died 2025"})
    assert identity.classify(facts("CURLIN", 2004, sex="H"), card=died)["stallion_status"] == "deceased"


def test_historical_sire_without_card_is_still_a_stallion_for_pedigree_research():
    c = identity.classify(facts("ENGLISHMAN", 2002), card=None)
    assert c["is_stallion"] and c["stallion_status"] == "historical"


def test_gelding_gets_no_breeding_profile():
    c = identity.classify(facts("FOREGO", 1970, sex="G"), card=None)
    assert c["profile_type"] == "gelding"
    assert not c["breeding_eligible"]


def test_male_without_stud_evidence_is_not_a_stallion():
    c = identity.classify(facts("SOME COLT", 2020, sex="C"), card=None)
    assert c["profile_type"] == "racing_male"
    assert c["sex"] == "Horse"  # six years old: no longer a colt


@pytest.mark.parametrize("card, expected", [
    ({"Stud Fee Currency": "USD", "Farm": "Spendthrift Farm, KY"}, True),
    ({"Stud Fee Currency": "CAD", "Farm": "Adena Springs North, ON"}, True),
    ({"Stud Fee Currency": "GBP", "Farm": "Dalham Hall Stud"}, False),
    ({"Stud Fee Currency": "EUR", "Farm": "Coolmore Stud"}, False),
    ({"Stud Fee Currency": "JPY", "Farm": "Shizunai Stallion Station"}, False),
    ({"Stud Fee Currency": "", "Farm": "Hill 'n' Dale Farms, Kentucky"}, True),
    ({"Stud Fee Currency": "", "Farm": "Banstead Manor Stud"}, False),
    # dollar-converted fee but a Japanese farm (American Pharoah, California Chrome)
    ({"Stud Fee Currency": "USD", "Farm": "Shizunai Stallion Station, JPN"}, False),
    ({"Stud Fee Currency": "USD", "Farm": "Arrow Stud, Japan"}, False),
    ({"Stud Fee Currency": "USD", "Farm": "Ocala Stud, FL"}, True),
    ({"Stud Fee Currency": "USD", "Farm": "Lane's End, Versailles, KY"}, True),
])
def test_stands_in_north_america(card, expected):
    assert identity.stands_in_north_america(card) is expected


def test_filly_becomes_mare_at_five():
    assert identity.classify(facts("A FILLY", 2023, sex="F"))["sex"] == "Filly"
    assert identity.classify(facts("A MARE", 2020, sex="F"))["sex"] == "Mare"

"""Valuation maths (Phase 16): deal value, comparables, fee parsing and zero denominators.

The stallion table and sons-at-stud map are stubbed, so these run without a database.
"""

import math

import pytest

import PriceProjection
import ValueProjector
from crossr_core import breeding_value as bv
from crossr_core import fees


def stallion(name, first_season, *, initial_fee=None, fee_now=None, foals=None, first_book=0.0, na=True):
    return {
        "name": name, "first_season": first_season, "first_crop": first_season + 1, "born": first_season - 4,
        "sire": "", "initial_fee": initial_fee, "initial_fee_year": first_season if initial_fee else None,
        "fee_now": fee_now, "fee_now_year": 2026, "fees": {}, "foals": foals or {},
        "first_book": first_book, "north_america": na,
    }


TABLE = {
    # Old sire: 200 foals in each of 2010-2022 (aged 3+ in 2026 are crops <= 2023)
    "OLDSIRE": stallion("Old Sire", 2009, initial_fee=50_000, fee_now=100_000,
                        foals={y: 100.0 for y in range(2010, 2026)}),
    "SON A": stallion("Son A", 2016, initial_fee=20_000),
    "SON B": stallion("Son B", 2017, initial_fee=10_000),
    "SON EU": stallion("Son Eu", 2018, initial_fee=99_000, na=False),
    "SON NOFEE": stallion("Son Nofee", 2019),
    # Young sire: first crop 2024, so under seven years old
    "YOUNG": stallion("Young", 2023, initial_fee=55_000, fee_now=55_000, foals={2024: 80.0}),
    # Stallion with a card but no foals on file
    "NOFOALS": stallion("No Foals", 2010, initial_fee=50_000, foals={}),
    # Young sire with no comparable fee
    "LONELY": stallion("Lonely", 2023, initial_fee=1_000_000, fee_now=1_000_000, foals={2024: 40.0}),
}
SONS = {"OLDSIRE": ["SON A", "SON B", "SON EU", "SON NOFEE"]}


@pytest.fixture(autouse=True)
def stub_tables(monkeypatch):
    monkeypatch.setattr(bv, "stallions", lambda: TABLE)
    monkeypatch.setattr(bv, "sons_at_stud", lambda: SONS)
    monkeypatch.setattr(bv, "_year_now", lambda: 2026)
    monkeypatch.setattr(bv, "isf_imputer", lambda: pytest.fail("imputer should not be needed"))


# ------------------------------------------------------------- Phase 10 deal value
def test_colts_of_racing_age_counts_half_of_foals_aged_three_plus():
    # crops 2010-2023 are aged 3+ in 2026: 14 crops x 100 foals / 2
    assert bv.colts_of_racing_age(TABLE["OLDSIRE"]) == 700.0
    assert bv.foals_aged_3_plus(TABLE["OLDSIRE"]) == 1400.0


def test_own_deal_value_uses_only_north_american_sons_with_fees():
    r = bv.own_deal_value("OLDSIRE")
    expected_total = (20_000 + 10_000) * bv.STUD_DEAL_MULTIPLIER
    assert r["total_deal_value"] == expected_total
    assert r["value"] == pytest.approx(expected_total / 700.0)
    assert [s["stallion"] for s in r["sons"]] == ["Son A", "Son B"]  # highest deal value first
    assert r["sons_without_fee"] == ["Son Nofee"]
    assert "Son Eu" not in {s["stallion"] for s in r["sons"]}


def test_stallion_with_no_foals_has_no_deal_value_instead_of_dividing_by_zero():
    assert bv.colts_of_racing_age(TABLE["NOFOALS"]) == 0
    assert bv.own_deal_value("NOFOALS") is None
    assert bv._colt_deal_value("No Foals") is None


def test_unknown_stallion_has_no_value():
    assert bv.own_deal_value("NOBODY") is None
    assert bv._colt_deal_value("Nobody") is None
    assert bv.comparables("NOBODY") == []


def test_young_sire_is_valued_from_fee_comparables_scaled_by_fee_ratio():
    # Young's initial fee 55k is within 10% of Old Sire's initial 50k
    r = bv._colt_deal_value("Young")
    assert r["method"] == "comparable_stallions"
    (comp,) = r["comparables"]
    assert comp["stallion"] == "Old Sire"
    assert comp["factor"] == pytest.approx(55_000 / 50_000)
    assert r["value"] == pytest.approx(bv.own_deal_value("OLDSIRE")["value"] * 1.1)


def test_young_sire_without_comparables_reports_no_value_not_zero():
    r = bv._colt_deal_value("Lonely")
    assert r["method"] == "no_comparables"
    assert r["value"] is None


def test_comparables_exclude_young_and_overseas_stallions():
    names = {c["stallion"] for c in bv.comparables("YOUNG")}
    assert "Lonely" not in names  # first crop under seven
    assert "Son Eu" not in names  # not North American


def test_initial_fee_missing_and_no_book_gives_no_estimate():
    assert bv.initial_fee_or_estimate(TABLE["SON NOFEE"]) == (None, False)
    assert bv.initial_fee_or_estimate(TABLE["SON A"]) == (20_000, False)


def test_quantile_handles_empty_and_single_values():
    assert bv._quantile([], 0.5) is None
    assert bv._quantile([7.0], 0.25) == 7.0
    assert bv._quantile([1.0, 3.0], 0.5) == 2.0


# ------------------------------------------------------------- Phase 11 helpers
def test_sire_fee_at_ignores_an_older_namesakes_fees():
    table = {"CURLIN": {"first_season": 2009}}
    fee_hist = {"CURLIN": {1990: 5_000, 2010: 50_000, 2012: 75_000}}
    assert bv._sire_fee_at(table, fee_hist, "CURLIN", 2012) == (75_000.0, False, 2012)
    assert bv._sire_fee_at(table, fee_hist, "CURLIN", 2011) == (50_000.0, True, 2010)
    assert bv._sire_fee_at(table, fee_hist, "CURLIN", 2005) == (None, False, None)


def test_num_and_truthy_tolerate_blank_and_text_values():
    assert bv._num("$1,250") == 1250.0
    assert bv._num("") == 0.0
    assert bv._num("Private") == 0.0
    assert bv._truthy("True") and bv._truthy("1") and not bv._truthy("0") and not bv._truthy(None)


# ------------------------------------------------------------- fee parsing / scaling
@pytest.mark.parametrize("text, expected", [
    ("$25,850", (25850, "USD")),
    ("25000", (25000, "USD")),
    (7500, (7500, "USD")),
    ("JPY 4,000,000", (4_000_000, "JPY")),
    ("EUR 50,000", (50_000, "EUR")),
    ("Private", (None, None)),
    ("$0", (None, None)),
    (0, (None, None)),
    ("", (None, None)),
    (None, (None, None)),
])
def test_parse_fee(text, expected):
    assert fees.parse_fee(text) == expected


def test_foreign_fee_is_never_shown_as_dollars():
    assert fees.format_fee("JPY 4,000,000") == "¥4,000,000 (~$25,850)"
    assert fees.format_fee("$225,000") == "$225,000"
    assert fees.format_fee("Private") == "Private"
    assert fees.format_fee("IRG 5,000") == "IRG 5,000"  # unknown currency: native only, no guess
    assert fees.format_fee("") is None
    assert fees.usd_estimate(None, "USD") is None
    assert fees.usd_estimate(100, "XYZ") is None


# ------------------------------------------------------------- legacy projectors
def test_added_value_multiplier_zero_denominators():
    assert ValueProjector.added_value_multiplier(100_000, 0) is None
    assert ValueProjector.added_value_multiplier(0, 10_000) is None
    assert ValueProjector.added_value_multiplier(100_000, 25_000) == 4.0


def test_price_fee_model_skips_zero_fees_and_constant_fees():
    rows = [
        {"sold_as": "Yearling", "stud_fee": 0, "price": 50_000},
        {"sold_as": "Yearling", "stud_fee": 10_000, "price": 0},
        {"sold_as": "Yearling", "stud_fee": 10_000, "price": 40_000},
        {"sold_as": "Yearling", "stud_fee": 10_000, "price": 60_000},
    ]
    m = PriceProjection.fit_price_fee_model(rows)
    assert m["n_matched"] == 2
    y = m["by_kind"]["Yearling"]
    assert y["median_multiple"] == 5.0
    assert y["log_slope"] is None  # every fee identical: no slope, not a divide-by-zero
    out = PriceProjection.project_price(20_000, m)
    assert out["proj_median_multiple"] == 100_000
    assert "proj_loglog" not in out


def test_ols_recovers_a_power_law():
    xs = [math.log(f) for f in (5_000, 10_000, 20_000, 40_000)]
    ys = [math.log(3 * f) for f in (5_000, 10_000, 20_000, 40_000)]
    slope, intercept = PriceProjection._ols(xs, ys)
    assert slope == pytest.approx(1.0)
    assert math.exp(intercept) == pytest.approx(3.0)
    assert PriceProjection._ols([1.0], [2.0]) == (None, None)


def test_project_price_without_a_model():
    assert "error" in PriceProjection.project_price(10_000, {"by_kind": {}})

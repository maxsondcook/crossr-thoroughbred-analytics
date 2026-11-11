"""Sale-price modeling dataset: one row per transaction, recorded exclusions, no look-ahead.

Runs on small made-up tables (no database). The real-data version of the leakage
check is `price_features.truncation_check`, run by scripts/price_model_preliminary.py.
"""

import pandas as pd
import pytest

from crossr_core import price_features as pf


def _frame(table, rows):
    return pd.DataFrame([{c: str(r.get(c, "")) for c in pf.TABLE_COLUMNS[table]} for r in rows],
                        columns=pf.TABLE_COLUMNS[table])


def _lot(year, hip, born, price, sire="Sire A", dam="Dam A", damsire="Bms X", rna=False, out=False,
         title="Keeneland September Yearling Sale", src=None, sex="C"):
    return {"catalog_year": year, "catalog_title": title, "source_file": src or f"kee_{year}.csv", "hip": hip,
            "name": f"{sire} x {dam} '{str(born)[2:]}", "name_key": f"{sire} x {dam} {born}".lower(), "sex_letter": sex,
            "sire": sire, "dam": dam, "damsire": damsire, "birth_year": born, "price_num": price,
            "is_rna": "True" if rna else "False", "is_out": "True" if out else "False"}


@pytest.fixture()
def sources():
    sales = [
        _lot(2016, "1", 2015, 30000),
        _lot(2017, "1", 2016, 40000),
        _lot(2017, "2", 2016, 25000, sire="Sire B", dam="Dam B"),
        _lot(2018, "1", 2017, 60000),
        _lot(2018, "1", 2017, 60000),                      # duplicate catalog row
        _lot(2018, "2", 2017, 15000, sire="Sire B", dam="Dam B", rna=True),   # RNA with a last bid
        _lot(2018, "3", 2017, 0, sire="Sire B", dam="Dam C", out=True),       # withdrawn
        _lot(2019, "1", 2018, 90000, src="kee_2019.csv"),
        _lot(2019, "9", 2017, 120000, title="Fasig Tipton Two Year Olds In Training", src="ft2yo_2019.csv",
             sire="Sire A", dam="Dam A"),                  # the 2017 colt resold as a 2-year-old
        _lot(2020, "1", 2019, 100000),
    ]
    fees = [
        {"Year": 2015, "Stallion": "Sire A", "Stud Fee": "$10,000"},
        {"Year": 2016, "Stallion": "Sire A", "Stud Fee": "$10,000"},
        {"Year": 2017, "Stallion": "Sire A", "Stud Fee": "$20,000"},
        {"Year": 2019, "Stallion": "Sire A", "Stud Fee": "$50,000"},   # announced after the 2018 sales
        {"Year": 2015, "Stallion": "Sire B", "Stud Fee": "$5,000"},
        {"Year": 2016, "Stallion": "Bms X", "Stud Fee": "$7,500"},
    ]
    horses = [
        {"id": "1", "name_key": "DAM A", "foal_year": 2008, "sex": "M", "sire": "Bms X", "dam": "Old Mare"},
        {"id": "2", "name_key": "STAR FOAL", "foal_year": 2014, "sex": "C", "sire": "Sire B", "dam": "Dam A"},
        # a duplicate Equibase record merged into id 2: must not count as a second foal
        {"id": "3", "name_key": "STAR FOAL", "foal_year": 2014, "sex": "C", "sire": "Sire B", "dam": "Dam A",
         "status": "merged"},
    ]
    seasons = [
        {"horse_id": "2", "year": 2016, "starts": 4, "firsts": 1, "earnings": 50000},
        {"horse_id": "2", "year": 2019, "starts": 3, "firsts": 2, "earnings": 900000},
        {"horse_id": "1", "year": 2011, "starts": 6, "firsts": 2, "earnings": 80000},
    ]
    starts = [
        {"horse_id": "2", "race_date": "2016-07-01", "place": 1, "grade": "", "race_name": "Maiden Special Weight"},
        {"horse_id": "2", "race_date": "2019-05-04", "place": 1, "grade": "G1", "race_name": "Big Stakes"},
    ]
    graded = [{"Horse Name": "Star Foal", "Place": 1, "Race Year": 2019, "Foaling Year": 2014, "Grade": "G1"}]
    frames = {
        "sales_lots": _frame("sales_lots", sales),
        "stud_fees": _frame("stud_fees", fees),
        "breeding_seasons": _frame("breeding_seasons", [{"breeding_year": 2014, "stallion": "Sire A", "mares_bred": 100,
                                                        "live_foals": 70}]),
        "race_horses": _frame("race_horses", horses),
        "race_seasons": _frame("race_seasons", seasons),
        "race_starts": _frame("race_starts", starts),
        "graded_stakes": _frame("graded_stakes", graded),
        "stallion_rankings": _frame("stallion_rankings", []),
        "pedigrees": _frame("pedigrees", []),
    }
    return pf.Sources(frames=frames)


def test_one_row_per_transaction_and_exclusions_recorded(sources):
    tx, ex = pf.build_transactions(sources)
    assert len(tx) == 10  # every catalog row stays in the dataset
    reasons = dict(zip(ex.hip.astype(str) + "@" + ex.sale_year.astype(int).astype(str), ex.model_exclusion))
    assert reasons["1@2018"] == "duplicate transaction (same catalog file, hip and horse)"
    assert reasons["3@2018"] == "withdrawn (out)"
    assert reasons["9@2019"] == "category not in the yearling/weanling model"
    rna = tx[(tx.hip == "2") & (tx.sale_year == 2018)].iloc[0]
    assert rna.status == "rna" and rna.model_exclusion == ""  # kept: a censored observation
    resold = tx[tx.horse_id == tx[(tx.hip == "1") & (tx.sale_year == 2018)].horse_id.iloc[0]]
    assert sorted(resold.category) == ["two_year_old_in_training", "yearling", "yearling"]  # never collapsed


def _row(feats, year, hip="1"):
    return feats[(feats.sale_year == year) & (feats.hip == hip)].iloc[0]


def test_no_future_stud_fees(sources):
    tx, _ = pf.build_transactions(sources)
    feats = pf.build_features(tx, sources)
    r = _row(feats, 2018)
    assert r.sire_fee_latest == 20000      # the 2019 fee of $50,000 was not known yet
    assert r.sire_fee_highest == 20000
    assert r.sire_fee_conception == 10000  # foaled 2017 -> covered 2016
    assert _row(feats, 2020).sire_fee_highest == 50000


def test_no_future_progeny_results(sources):
    tx, _ = pf.build_transactions(sources)
    feats = pf.build_features(tx, sources)
    assert _row(feats, 2018).dam_prog_g1 == 0  # her son's 2019 Grade 1 win came after this sale
    assert _row(feats, 2018).dam_prog_won == 1  # his 2016 maiden win was known
    assert _row(feats, 2020).dam_prog_g1 == 1
    assert _row(feats, 2018).dam_starts == 6  # the dam's own record (raced 2011)
    assert _row(feats, 2018).dam_foals_before == 1  # the merged duplicate is not a second foal


def test_truncation_check_finds_no_leakage(sources):
    for year in (2018, 2019, 2020):
        res = pf.truncation_check(sources, year, sample=50)
        assert res["mismatched_cells"] == 0, res


def test_price_is_never_a_feature(sources):
    tx, _ = pf.build_transactions(sources)
    pf.build_features(tx, sources)
    assert not {"price", "lp", "status", "rel"} & set(pf.feature_columns())

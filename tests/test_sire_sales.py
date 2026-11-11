"""Stallion sales panel rules (crossr_core.sales), with the sales index stubbed."""

import datetime

import pytest

from crossr_core import sales


def test_lot_type_by_age_at_sale():
    assert sales.lot_type(2026, 2026, "C") == "Weanling"
    assert sales.lot_type(2026, 2025, "F") == "Yearling"
    assert sales.lot_type(2026, 2024, "C") == "Two-Year-Old"
    assert sales.lot_type(2026, 2019, "M") == "Mare / Broodmare"
    assert sales.lot_type(2026, 2021, "H") == "Horse of Racing Age"
    assert sales.lot_type(2026, None, "C") == "Unknown"


def test_lot_status():
    assert sales.lot_status(250000, 0, 0) == "sold"
    assert sales.lot_status(180000, 1, 0) == "not sold"   # RNA
    assert sales.lot_status(0, 0, 0) == "not sold"
    assert sales.lot_status(None, 0, 1) == "out"


def _row(i, year, born, price, catalog=1, rna=0, out=0, sex="C", damsire="Tapit"):
    return {"id": i, "catalog_id": catalog, "year": year, "sale_title": f"Sale {catalog}", "month_index": 9,
            "hip": str(i), "name": f"Lot {i}", "sex_letter": sex, "sex": sex, "dam": "Dam", "damsire": damsire,
            "birth_year": str(born), "price_num": price, "price_raw": "", "is_rna": rna, "is_out": out,
            "purchaser": "", "consignor": ""}


@pytest.fixture
def stub_index(monkeypatch):
    rows = [
        _row(1, 2026, 2025, 500000, catalog=10),             # yearling, current year (incomplete)
        _row(2, 2025, 2024, 400000, catalog=9),              # yearling, latest completed
        _row(3, 2025, 2024, 200000, catalog=9),              # yearling
        _row(4, 2025, 2025, 100000, catalog=8),              # weanling
        _row(5, 2025, 2024, 300000, catalog=9, rna=1),       # RNA: offered, not sold
        _row(6, 2025, 2024, 0, catalog=9, out=1),            # out: excluded
        _row(7, 2023, 2022, 150000, catalog=7),              # yearling, in last 3
        _row(8, 2015, 2014, 90000, catalog=3),               # namesake era? stud entry below decides
        _row(9, 2010, 2003, 50000, catalog=2, sex="M"),      # foaled before he stood: namesake
    ]

    class Conn:
        def execute(self, sql, params=()):
            class R:
                def fetchall(_self):
                    return rows
            return R()

        def close(self):
            pass

    monkeypatch.setattr(sales, "connect", lambda: Conn())


def test_periods_categories_and_namesake_filter(stub_index):
    out = sales.sire_sales_summary("Into Mischief", entered_stud=2009, today=datetime.date(2026, 10, 31))
    assert out["latest_completed_year"] == 2025 and out["current_year"] == 2026
    assert out["lots_total"] == 8                                   # lot 9 (foaled 2003) dropped
    y = out["summary"]["yearlings"]
    assert y["latest"]["sold"] == 2 and y["latest"]["average"] == 300000
    assert y["latest"]["offered"] == 3                              # RNA offered, out excluded
    assert y["current"]["average"] == 500000                         # 2026 kept separate (year to date)
    assert y["last3"]["sold"] == 3                                   # 2023-2025
    assert out["summary"]["weanlings"]["latest"]["average"] == 100000
    years = [b["year"] for b in out["by_year"]]
    assert years == list(range(2015, 2027))                          # missing years present (no data)
    assert next(b for b in out["by_year"] if b["year"] == 2020)["all_avg"] is None
    assert out["sales"][0]["catalog_id"] == 10                       # most recent sale first
    assert out["selected_catalog_id"] == 10


def test_selected_sale_lists_its_lots(stub_index):
    out = sales.sire_sales_summary("Into Mischief", entered_stud=2009, catalog_id=9, today=datetime.date(2026, 10, 31))
    assert {l["status"] for l in out["sale_lots"]} == {"sold", "not sold", "out"}
    assert out["sale_stats"]["all"]["sold"] == 2

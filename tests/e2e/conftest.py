"""End-to-end checks of the whole Crossr API against a Postgres test database.

    export DATABASE_URL=postgresql://.../crossr_dev      # never the production `railway` db
    python -m pytest tests/e2e -q                         # everything
    python -m pytest tests/e2e -q -k stallion             # one area
    python -m pytest tests/e2e -q -rf > report.txt        # failure reasons only

The API runs in-process (FastAPI TestClient), so no server is needed. The suite
writes only to the test database: it creates the made-up "Saratoga Sale (2026)"
upcoming catalog and temporary stars/accounts, and removes them at the end.
"""

from __future__ import annotations

import os
import sys
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Test subjects (chosen by the user)
# ---------------------------------------------------------------------------

# name -> facts the profile must agree with. progeny: has stakes-winning offspring
# (top progeny, proven crosses, a general sire rank); bms: old enough that
# daughters' foals race (broodmare-sire lists); abroad: stands outside North
# America, so he has a profile but is not in the stallion directory (user's call).
STALLIONS = {
    "American Pharoah": {"foaled": 2012, "entered_stud": 2016, "progeny": True, "bms": True, "abroad": True},
    "Into Mischief": {"foaled": 2005, "entered_stud": 2009, "progeny": True, "bms": True},
    "Not This Time": {"foaled": 2014, "entered_stud": 2017, "progeny": True, "bms": False},
    "Justify": {"foaled": 2015, "entered_stud": 2019, "progeny": True, "bms": False},
    "Curlin": {"foaled": 2004, "entered_stud": 2009, "progeny": True, "bms": True},
    "Essential Quality": {"foaled": 2018, "entered_stud": 2022, "progeny": True, "bms": False},
    "Tapit": {"foaled": 2001, "entered_stud": 2005, "progeny": True, "bms": True},
    # Flightline's first crop are 2-year-olds with no stakes winners yet: empty progeny/crosses is right.
    "Flightline": {"foaled": 2018, "entered_stud": 2023, "progeny": False, "bms": False, "light": True},
    "Sierra Leone": {"foaled": 2021, "entered_stud": 2026, "progeny": False, "bms": False},
    "Fierceness": {"foaled": 2021, "entered_stud": 2026, "progeny": False, "bms": False},
}

# Stage Magic is two mares; plain "Stage Magic" must ask which year (409).
BROODMARES = ["Virginia Key", "Sippican Harbor", "Off Topic", "Stage Magic (2015)", "Stage Magic (2007)", "Crowned"]
AMBIGUOUS_MARES = {"Stage Magic": ["2007", "2015"]}
# Stallion x mare foal reports (live diagnostic set).
REPORT_STALLIONS = ["American Pharoah", "Curlin", "Flightline", "Not This Time", "Into Mischief"]

SARATOGA_ID = "Saratoga Sale (2026)"
SARATOGA_SOURCE_FILE = "Fasig-Tipton_The_Saratoga_Sale(2026).csv"


def q(text: str) -> str:
    return urllib.parse.quote(str(text), safe="")


# ---------------------------------------------------------------------------
# Database guard + app client
# ---------------------------------------------------------------------------

def _db_name(url: str) -> str:
    return urllib.parse.urlparse(url).path.lstrip("/")


@pytest.fixture(scope="session", autouse=True)
def test_database():
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        pytest.exit("DATABASE_URL is not set; point it at the crossr_dev database.", returncode=2)
    if _db_name(url) == "railway":
        pytest.exit("Refusing to run against the production `railway` database; use crossr_dev.", returncode=2)
    return url


class Api:
    """TestClient wrapper that caches GETs, so many checks can share one slow response."""

    def __init__(self, client):
        self.client = client
        self._cache: dict = {}

    def get(self, path: str):
        if path not in self._cache:
            resp = self.client.get(path)
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text
            self._cache[path] = (resp.status_code, body)
        return self._cache[path]

    def ok(self, path: str):
        status, body = self.get(path)
        assert status == 200, f"GET {path} -> {status}: {str(body)[:300]}"
        return body

    def post(self, path: str, body: dict, cache: bool = True):
        key = ("POST", path, repr(sorted(body.items())))
        if cache and key in self._cache:
            return self._cache[key]
        resp = self.client.post(path, json=body)
        out = (resp.status_code, resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text)
        if cache:
            self._cache[key] = out
        return out


@pytest.fixture(scope="session")
def api(test_database, saratoga_catalog):
    os.environ.setdefault("CROSSR_FRONTEND_ORIGIN", "http://localhost:3000")
    from fastapi.testclient import TestClient
    from crossr_api.app import app

    # Generous rate limits are for real users; the suite makes hundreds of calls.
    import crossr_api.app as app_module
    app_module.RATE_LIMITS.clear()
    with TestClient(app) as client:  # runs the startup event (profiles, search index, models)
        yield Api(client)


# ---------------------------------------------------------------------------
# The made-up upcoming sale
# ---------------------------------------------------------------------------

CATALOG_COLUMNS = [
    "SALE TITLE", "SESSION", "HIP", "COLOR", "NAME", "SEX", "SIRE", "DAM", "SIRE OF DAM",
    "PROPERTY LINE", "PURCHASER", "PRICE", "YEAR OF BIRTH", "FOALED", "BARN",
    "COVERING SIRE", "SOLD AS CODE", "STARRED", "NOTES",
]


@pytest.fixture(scope="session")
def saratoga_catalog(test_database):
    """Pretend the 2026 Fasig-Tipton Saratoga Sale is upcoming: its real hips, no results.

    Built from sales_lots (the sale's actual catalog) in the upcoming-catalog
    format; prices and purchasers are blanked. Removed again after the session.
    """
    from crossr_core import railway_db, upcoming

    with railway_db.connect() as conn:
        rows = conn.execute(
            "SELECT hip, name, sex_letter, sire, dam, damsire, color, consignor, barn, "
            "foaled_location, birth_year FROM sales_lots WHERE source_file = %s ORDER BY _rid",
            (SARATOGA_SOURCE_FILE,),
        ).fetchall()
    assert rows, f"no lots for {SARATOGA_SOURCE_FILE} in sales_lots"
    catalog = []
    for hip, name, sex, sire, dam, damsire, color, consignor, barn, foaled, birth_year in rows:
        catalog.append({
            "SALE TITLE": SARATOGA_ID, "SESSION": "2026-08-10", "HIP": str(int(hip)) if str(hip).isdigit() else hip,
            "COLOR": color or "", "NAME": f"{birth_year}-{dam}" if " x " in (name or "").lower() else (name or ""),
            "SEX": sex or "", "SIRE": sire or "", "DAM": dam or "", "SIRE OF DAM": damsire or "",
            "PROPERTY LINE": consignor or "", "PURCHASER": "", "PRICE": "", "YEAR OF BIRTH": birth_year or "",
            "FOALED": foaled or "", "BARN": barn or "", "COVERING SIRE": "", "SOLD AS CODE": "Y",
            "STARRED": "", "NOTES": "",
        })
    railway_db.replace_upcoming_catalog(SARATOGA_ID, "upcoming", CATALOG_COLUMNS, catalog, upcoming.count_hips(catalog))
    yield catalog
    railway_db.delete_upcoming_catalog(SARATOGA_ID)
    with railway_db.connect() as conn:
        conn.execute("DELETE FROM upcoming_lot_marks WHERE catalog_id = %s", (SARATOGA_ID,))

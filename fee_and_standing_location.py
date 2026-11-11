"""
Horse Fee Database

Stud-fee lookups from data/stud_fees.csv and the local stud_fee_database.json.
Nothing here fetches from the web.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "stud_fee_database.json"
CSV_FEE_PATHS = [
    ROOT / "data" / "stud_fees.csv",
    ROOT / "stallion_rankings_data" / "stud_fee_database.csv",
]
# Fees above this are almost always bad scrapes / FX artifacts (e.g. ¥ mislabeled as $).
MAX_PLAUSIBLE_USD_FEE = 1_000_000

_CSV_FEE_HISTORY: Optional[Dict[str, Dict[int, int]]] = None

def _norm_name(name: str) -> str:
    if not name:
        return ""
    cleaned = re.sub(r"\(.*?\)", "", str(name))
    cleaned = cleaned.replace(".", "").replace("'", "").replace(",", "")
    return " ".join(cleaned.lower().split()).strip()


def load_fee_database(path: Path = DB_PATH) -> Dict[str, dict]:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    # Normalize keys on load
    return {_norm_name(k): v for k, v in raw.items()}


def save_fee_database(db: Dict[str, dict], path: Path = DB_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = dict(sorted(db.items(), key=lambda kv: kv[0]))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ordered, f, indent=2, sort_keys=True)
    # Canonical sample CSV (atomic upsert) so :8000 can keep serving mid-scrape.
    try:
        from crossr_core import sample_db

        rows = []
        for stallion, entry in ordered.items():
            farm = (entry or {}).get("farm") or ""
            fees = (entry or {}).get("fees_by_year") or {}
            for year, fee in fees.items():
                rows.append({
                    "Year": str(year),
                    "Stallion": stallion,
                    "Stud Fee": str(fee),
                    "Farm": farm,
                    "Source List": "Racing Post",
                    "Notes": "",
                })
            current = (entry or {}).get("current_fee")
            if current and not fees:
                rows.append({
                    "Year": "",
                    "Stallion": stallion,
                    "Stud Fee": str(current),
                    "Farm": farm,
                    "Source List": "Racing Post",
                    "Notes": "current_fee",
                })
        if rows:
            sample_db.upsert_rows("stud_fees", rows)
    except Exception as exc:
        print(f"warning: could not upsert data/stud_fees.csv: {exc}")


def _ensure_entry(db: Dict[str, dict], stallion: str) -> dict:
    key = _norm_name(stallion)
    if key not in db:
        db[key] = {
            "farm": None,
            "status": "active",
            "fees_by_year": {},
            "current_fee": None,
            "racing_post_id": None,
            "racing_post_slug": None,
        }
    entry = db[key]
    entry.setdefault("fees_by_year", {})
    entry.setdefault("current_fee", None)
    entry.setdefault("racing_post_id", None)
    entry.setdefault("racing_post_slug", None)
    return entry


def parse_fee_value(raw: Any) -> Optional[int]:
    """Parse stud fee scalars; Private / YTBA / blank -> None."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return int(raw) if raw else None
    text = str(raw).strip()
    if not text:
        return None
    low = text.lower()
    if any(tok in low for tok in ("private", "n/a", "tba", "yet to", "undisclosed")):
        return None
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def _plausible_fee(fee: Optional[int]) -> Optional[int]:
    if fee is None or fee <= 0:
        return None
    if fee > MAX_PLAUSIBLE_USD_FEE:
        return None
    return int(fee)


def load_csv_fee_history(force: bool = False) -> Dict[str, Dict[int, int]]:
    """BloodHorse / rankings CSV: stallion -> {year: fee}. Cached in-process."""
    global _CSV_FEE_HISTORY
    if _CSV_FEE_HISTORY is not None and not force:
        return _CSV_FEE_HISTORY

    out: Dict[str, Dict[int, int]] = {}
    path = next((p for p in CSV_FEE_PATHS if p.exists()), None)
    if path is None:
        _CSV_FEE_HISTORY = {}
        return _CSV_FEE_HISTORY

    with open(path, encoding="utf-8-sig", errors="ignore") as f:
        for row in csv.DictReader(f):
            name = _norm_name(row.get("Stallion") or "")
            try:
                year = int(str(row.get("Year") or "").strip())
            except (TypeError, ValueError):
                continue
            fee = _plausible_fee(parse_fee_value(row.get("Stud Fee")))
            if not name or not year or not fee:
                continue
            out.setdefault(name, {})[year] = fee

    _CSV_FEE_HISTORY = out
    return out


def _fee_from_json_entry(entry: dict, year: int) -> Optional[int]:
    fees = entry.get("fees_by_year") or {}
    raw = fees.get(str(year), fees.get(year))
    return _plausible_fee(parse_fee_value(raw))


def _nearest_prior_fee(year_map: Dict[int, int], year: int) -> Optional[int]:
    priors = [y for y in year_map if y <= year]
    if not priors:
        return None
    return _plausible_fee(year_map[max(priors)])


def get_fee_for_year(
    stallion: str,
    year: int,
    db: Optional[Dict[str, dict]] = None,
    *,
    allow_nearest_prior: bool = False,
) -> Optional[int]:
    """Return stud fee for a stallion in a given year.

    Preference: BloodHorse CSV exact → JSON exact → (optional) nearest prior
    from CSV then JSON. Implausible USD amounts (> $1M) are ignored.
    """
    year = int(year)
    key = _norm_name(stallion)
    csv_hist = load_csv_fee_history().get(key) or {}
    if year in csv_hist:
        return csv_hist[year]

    db = db if db is not None else load_fee_database()
    entry = db.get(key)
    if entry:
        exact = _fee_from_json_entry(entry, year)
        if exact:
            return exact

    if not allow_nearest_prior:
        return None

    prior_csv = _nearest_prior_fee(csv_hist, year)
    if prior_csv:
        return prior_csv

    if entry:
        fees = entry.get("fees_by_year") or {}
        year_map: Dict[int, int] = {}
        for y, raw in fees.items():
            try:
                yi = int(y)
            except (TypeError, ValueError):
                continue
            fee = _plausible_fee(parse_fee_value(raw))
            if fee:
                year_map[yi] = fee
        return _nearest_prior_fee(year_map, year)
    return None


def resolve_fee_at_sale(
    stallion: str,
    sale_year: Optional[int],
    db: Optional[Dict[str, dict]] = None,
) -> Optional[int]:
    """Sire stud fee as of the sale year (exact, else nearest prior ≤ sale year)."""
    if not stallion or not sale_year:
        return get_current_fee(stallion, db)
    return get_fee_for_year(
        stallion, int(sale_year), db, allow_nearest_prior=True
    ) or get_current_fee(stallion, db)


def get_current_fee(stallion: str, db: Optional[Dict[str, dict]] = None) -> Optional[int]:
    db = db if db is not None else load_fee_database()
    entry = db.get(_norm_name(stallion))
    key = _norm_name(stallion)

    # Prefer explicitly stored current USD fee when present
    if entry:
        current = _plausible_fee(parse_fee_value(entry.get("current_fee")))
        usd_years = entry.get("usd_fee_years") or []
        fees = entry.get("fees_by_year") or {}
        if usd_years:
            newest_usd = max(int(y) for y in usd_years)
            usd_fee = _plausible_fee(parse_fee_value(fees.get(str(newest_usd))))
            if usd_fee:
                return usd_fee
        if current:
            return current
        years = []
        for y in fees:
            try:
                years.append(int(y))
            except (TypeError, ValueError):
                continue
        if years:
            fee = _plausible_fee(parse_fee_value(fees[str(max(years))]))
            if fee:
                return fee

    csv_hist = load_csv_fee_history().get(key) or {}
    if csv_hist:
        return csv_hist[max(csv_hist)]
    return None


def upsert_fee_history(
    stallion: str,
    fees_by_year: Dict[int, Optional[int]],
    *,
    farm: Optional[str] = None,
    status: Optional[str] = None,
    racing_post_id: Optional[str] = None,
    racing_post_slug: Optional[str] = None,
    usd_years: Optional[list] = None,
    db: Optional[Dict[str, dict]] = None,
    persist: bool = True,
) -> Dict[str, dict]:
    """Merge year->fee rows into the stud fee database and optionally save."""
    db = db if db is not None else load_fee_database()
    entry = _ensure_entry(db, stallion)
    for year, fee in fees_by_year.items():
        if fee is None:
            continue
        entry["fees_by_year"][str(int(year))] = int(fee)
    if farm:
        entry["farm"] = farm
    if status:
        entry["status"] = status
    if racing_post_id:
        entry["racing_post_id"] = str(racing_post_id)
    if racing_post_slug:
        entry["racing_post_slug"] = racing_post_slug
    if usd_years:
        existing = {int(y) for y in (entry.get("usd_fee_years") or [])}
        existing.update(int(y) for y in usd_years)
        entry["usd_fee_years"] = sorted(existing)

    # Refresh current_fee from newest USD year when known, else newest year
    usd = [int(y) for y in (entry.get("usd_fee_years") or [])]
    years = [int(y) for y in entry["fees_by_year"] if str(y).isdigit()]
    if usd:
        newest = max(usd)
        entry["current_fee"] = entry["fees_by_year"].get(str(newest))
    elif years:
        newest = max(years)
        entry["current_fee"] = entry["fees_by_year"][str(newest)]
    if years:
        entry["status"] = entry.get("status") or "active"

    if persist:
        save_fee_database(db)
    return db


def ensure_fee_for_year(
    stallion: str,
    year: int,
    *,
    db: Optional[Dict[str, dict]] = None,
    scrape_if_missing: bool = True,
    persist: bool = True,
) -> Tuple[Optional[int], Dict[str, dict], Optional[str]]:
    """
    Look up stud fee for stallion/year. ``scrape_if_missing`` is kept for
    callers but ignored: nothing is fetched from the web.

    Returns (fee_or_None, db, note)
    """
    db = db if db is not None else load_fee_database()
    fee = get_fee_for_year(stallion, year, db)
    if fee is not None:
        return fee, db, "cache"

    return None, db, "missing"


def get_stallion_info(user_wants: str) -> str:
    """Legacy interactive helper: describe current fee / farm / status."""
    stallions = load_fee_database()
    lookup_key = _norm_name(user_wants)
    data = stallions.get(lookup_key)
    if not data:
        return f"Error: '{user_wants}' not found in the stallion roster."

    fee = get_current_fee(lookup_key, stallions)
    if fee is None:
        formatted_fee = "a private fee"
    else:
        formatted_fee = f"a fee of ${fee:,}"

    farm = data.get("farm") or "an undisclosed farm"
    status = data.get("status") or "active"
    return f"{lookup_key.title()} is standing at {farm} for {formatted_fee} ({status})."


if __name__ == "__main__":
    q = input("Stallion name: ").strip()
    if q:
        print(get_stallion_info(q))

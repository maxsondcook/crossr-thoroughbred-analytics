"""Canonical runtime store: the 8 Crossr tables, read from Postgres.

All data lives in the database named by DATABASE_URL (crossr_core.railway_db);
there is no local-file fallback. Scrapers and the pipeline write here with
atomic replace, and the API reloads a table when its version changes so it can
keep serving while scrapes merge new rows.
"""

from __future__ import annotations

import datetime
import os
import re
import threading
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Source CSVs for scripts/load_railway.py and the pipeline's staging only; the app never reads them.
DATA_DIR = Path(os.environ.get("CROSSR_SAMPLE_DATA", str(ROOT / "data")))

# The eight runtime tables (one per former data/*.csv; columns are the CSV headers).
TABLES = {
    "stud_fees": ["Year", "Stallion", "Stud Fee", "Farm", "Source List", "Notes"],
    "race_horses": [
        "id", "pedigree_key", "name_key", "foal_year", "display_name", "foaled_in",
        "breed", "color", "sex", "foaled", "sire", "sire_refno", "dam", "dam_refno",
        "damsire", "damsire_refno", "jockey", "trainer", "owner", "breeder",
        "best_class", "equibase_refno", "registry", "profile_url", "status",
        "detail", "fetched_at", "queue_batch",
    ],
    "race_seasons": [
        "id", "horse_id", "year", "starts", "firsts", "seconds", "thirds",
        "highest_esf", "earnings", "earnings_per_start",
    ],
    "race_starts": [
        "id", "horse_id", "race_date", "track", "track_code", "country",
        "race_number", "race_name", "grade", "breed", "place", "esf",
        "stakes_id", "chart_url", "video_url",
    ],
    "breeding_seasons": [
        "breeding_year", "stallion", "mares_bred", "reports_received",
        "live_foals", "live_foal_pct", "state", "source",
    ],
    "stallion_rankings": [
        "year", "list", "table_name", "rank", "stallion", "sire", "foaling_year",
        "country", "stallion_raw", "source_file", "stud_fee", "stud_fee_season",
        "farm", "stud_fee_raw", "runners", "winners", "repeat_winners",
        "stakes_winners", "stakes_winners_percent", "stakes_winners_raw",
        "stakes_wins", "graded_stakes_winners", "graded_stakes_winners_percent",
        "graded_stakes_winners_raw", "grade1_stakes_winners",
        "grade1_stakes_winners_percent", "grade1_stakes_winners_raw",
        "stakes_horses", "current_2yos", "yearling_average", "chief_earner",
        "chief_earner_earnings", "chief_earner_raw", "year_earnings",
        "earnings_share", "dams", "cumulative_stakes_winners", "cumulative_foals",
        "aei", "comparable_index", "all_surfaces_runners", "all_surfaces_winners",
        "all_surfaces_stakes_winners", "turf_runners", "turf_winners",
        "turf_stakes_winners", "north_america_earnings", "europe_dubai_earnings",
        "japan_hong_kong_earnings", "region_earnings",
        "international_stakes_winners", "international_stakes_horses", "crops",
        "average_crop_size", "crops_raw", "foals", "foals_basis",
        "lifetime_yearlings_sold", "lifetime_yearling_average",
        "lifetime_yearlings_raw", "yearling_window", "window_yearlings_sold",
        "window_yearling_average", "window_yearlings_raw",
    ],
    "graded_stakes": [
        "Race Name", "Horse Name", "Year Foaled", "Sex", "Place", "Country",
        "Track", "Date", "Distance", "Age", "Purse", "Group", "Race Conditions",
        "Date (ISO)", "Purse Source", "Purse (Local)", "Purse Currency",
        "Distance (Metres)", "Colour", "Jockey", "Horse Country Suffix",
        "Foaling Year", "race_id", "Race Year", "Grade",
    ],
    "sales_lots": [
        "catalog_year", "catalog_title", "source_file", "hip", "name", "name_key",
        "sex", "sex_letter", "sire", "sire_key", "dam", "dam_key", "damsire",
        "damsire_key", "color", "consignor", "purchaser", "barn",
        "foaled_location", "birth_year", "price_raw", "price_num", "is_rna",
        "is_out", "is_fasig",
    ],
}

# App-state tables (formerly placeholderdatabase/*.csv), stored in Postgres.
APP_TABLES = {
    "industry_roles": ["role_name", "description"],
    "accounts": ["account_id", "display_name", "email", "industry_role", "created_at"],
    "clients": ["client_id", "account_id", "client_name"],
    "client_preferences": [
        "preference_id", "client_id", "sex", "foaled", "surface",
        "min_stud_fee", "max_stud_fee", "sire_name", "requires_producer_note",
    ],
    "notes": ["note_id", "account_id", "sale_title", "hip", "body", "source", "updated_at"],
    "hypothetical_matings": ["mating_key", "horse_name", "sire_name", "dam_name", "pedigree_json"],
}

# Upsert identity keys per table (columns that uniquely identify a row).
UPSERT_KEYS = {
    "stud_fees": ("Year", "Stallion", "Source List"),
    "race_horses": ("id",),
    "race_seasons": ("id",),
    "race_starts": ("id",),
    "breeding_seasons": ("breeding_year", "stallion"),
    "stallion_rankings": ("year", "list", "table_name", "rank", "stallion", "source_file"),
    "graded_stakes": ("race_id", "Horse Name", "Place", "Date (ISO)"),
    "sales_lots": ("source_file", "hip", "name_key"),
}

_LOCK = threading.RLock()
_ROWS: dict[str, list[dict]] = {}
_MTIME: dict[str, float] = {}


def csv_path(table: str) -> Path:
    if table not in TABLES:
        raise KeyError(f"Unknown sample table {table!r}; expected one of {sorted(TABLES)}")
    return DATA_DIR / f"{table}.csv"


def name_key(name: str) -> str:
    if not name or not isinstance(name, str):
        return ""
    cleaned = re.sub(r"\(.*?\)", "", name)
    cleaned = cleaned.replace(".", "").replace("'", "").replace(",", "").replace("’", "")
    return " ".join(cleaned.split()).strip().upper()


def broodmare_clean_horse_name(name: str) -> str:
    if not name or not isinstance(name, str):
        return ""
    cleaned = re.sub(r"\(.*?\)", "", name)
    cleaned = cleaned.replace(".", "")
    return " ".join(cleaned.split()).strip().lower()


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def _pg():
    from crossr_core import railway_db

    railway_db.require()
    return railway_db


def backend() -> str:
    return "postgres"


class Row(Mapping):
    """Read-only row: a tuple of cell strings plus the table's shared column index.

    A plain dict per row costs ~1 KB before its strings; with ~1.9M rows across
    the 8 tables that dominated the API's memory. Rows support everything callers
    use (``row.get``, ``row[col]``, ``dict(row)``, ``{**row}``, ``.items()``);
    copy with ``dict(row)`` before changing anything.
    """

    __slots__ = ("_v",)
    _cols: tuple = ()
    _idx: dict = {}

    def __init__(self, values):
        self._v = values

    def __getitem__(self, key):
        return self._v[self._idx[key]]

    def get(self, key, default=None):
        i = self._idx.get(key)
        return default if i is None else self._v[i]

    def __contains__(self, key):
        return key in self._idx

    def __iter__(self):
        return iter(self._cols)

    def __len__(self):
        return len(self._cols)

    def copy(self) -> dict:
        return dict(zip(self._cols, self._v))

    def __repr__(self):
        return f"Row({self.copy()!r})"


_ROW_TYPES: dict[str, type] = {}


def _row_type(table: str) -> type:
    cls = _ROW_TYPES.get(table)
    if cls is None:
        cols = tuple(TABLES[table])
        cls = type(f"{table}_row", (Row,), {"__slots__": (), "_cols": cols, "_idx": {c: i for i, c in enumerate(cols)}})
        _ROW_TYPES[table] = cls
    return cls


def _compact_rows(table: str, tuples) -> list:
    """Build Rows from value tuples, sharing repeated strings (sires, tracks, years)."""
    cls = _row_type(table)
    pool: dict[str, str] = {}
    share = pool.setdefault
    return [cls(tuple(share(v, v) for v in values)) for values in tuples]


def _read_tuples(table: str):
    """Yield one tuple of strings per row, in TABLES[table] column order."""
    yield from _pg().iter_table(table, TABLES[table])


def _mtime(table: str) -> float:
    return _pg().table_version(table)


def table_version(table: str) -> float:
    """Changes whenever the table is rewritten (cheap; cached for a few seconds)."""
    return _mtime(table)


def load_table(table: str, refresh: bool = False) -> list[dict]:
    """Return rows for one of the 8 tables. Reloads when the table version changes."""
    with _LOCK:
        mtime = _mtime(table)
        if refresh or table not in _ROWS or _MTIME.get(table) != mtime:
            _ROWS.pop(table, None)  # let the old copy go before building the new one
            _ROWS[table] = _compact_rows(table, _read_tuples(table))
            _MTIME[table] = mtime
        return list(_ROWS[table])


def invalidate(table: str | None = None) -> None:
    with _LOCK:
        if table is None:
            _ROWS.clear()
            _MTIME.clear()
        else:
            _ROWS.pop(table, None)
            _MTIME.pop(table, None)


def atomic_write_table(table: str, rows: list[dict]) -> int:
    """Replace the whole table atomically. Safe mid-scrape.

    One transaction (DELETE + COPY); rows are streamed, not copied into a list.
    """
    columns = TABLES[table]
    normalized = (
        {col: _cell(row.get(col) if isinstance(row, Mapping) else "") for col in columns}
        for row in rows
    )
    count = _pg().replace_table(table, columns, normalized)
    invalidate(table)
    return count


def _row_key(table: str, row: dict) -> tuple:
    keys = UPSERT_KEYS[table]
    return tuple(_cell(row.get(k)) for k in keys)


def upsert_rows(table: str, incoming: list[dict]) -> dict:
    """Merge incoming rows by UPSERT_KEYS; write atomically. Returns counts."""
    if not incoming:
        return {"inserted": 0, "updated": 0, "total": len(load_table(table))}
    columns = TABLES[table]
    existing = load_table(table, refresh=True)
    by_key = {_row_key(table, row): dict(row) for row in existing}
    inserted = updated = 0
    for raw in incoming:
        row = {col: _cell(raw.get(col)) for col in columns}
        key = _row_key(table, row)
        if key in by_key:
            prior = by_key[key]
            merged = dict(prior)
            for col in columns:
                val = row.get(col, "")
                if val != "":
                    merged[col] = val
            if merged != prior:
                updated += 1
            by_key[key] = merged
        else:
            by_key[key] = row
            inserted += 1
    total = atomic_write_table(table, list(by_key.values()))
    return {"inserted": inserted, "updated": updated, "total": total}


def merge_rows(table: str, incoming: list[dict]) -> dict:
    """upsert_rows done in Postgres: the incoming rows go up by COPY and are
    merged by UPSERT_KEYS there (blank cells keep the stored value). Nothing is
    read back and the table is not rewritten. Returns counts."""
    from crossr_core import pg_push, railway_db

    columns = TABLES[table]
    if not incoming:
        return {"inserted": 0, "updated": 0}
    rows = [{col: _cell(raw.get(col)) for col in columns} for raw in incoming]
    with railway_db.connect() as conn:
        out = pg_push.upsert(conn, table, columns, UPSERT_KEYS[table], rows, keep_blanks=True)
        if out["inserted"] or out["updated"]:
            _bump_after_write(conn, table)
    invalidate(table)
    return out


def replace_by_file(table: str, incoming: list[dict]) -> dict:
    """Replace the rows of each source_file present in ``incoming`` (other
    files' rows stay). One transaction, COPY in; nothing read back."""
    from crossr_core import pg_push, railway_db

    columns = TABLES[table]
    rows = [{col: _cell(raw.get(col)) for col in columns} for raw in incoming]
    files = {r.get("source_file") for r in rows if r.get("source_file")}
    with railway_db.connect() as conn:
        out = pg_push.replace_by(conn, table, columns, "source_file", files, rows)
        _bump_after_write(conn, table)
    invalidate(table)
    return out


def _bump_after_write(conn, table: str) -> None:
    from crossr_core import railway_db

    count = conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
    version = railway_db._bump(conn, table, int(count))
    if table == "sales_lots":
        railway_db._rebuild_sales_index(conn, version)


def replace_partition(table: str, match: dict, incoming: list[dict]) -> dict:
    """Drop rows matching all match fields, then append incoming; atomic write."""
    columns = TABLES[table]
    existing = load_table(table, refresh=True)
    kept = []
    removed = 0
    for row in existing:
        if all(_cell(row.get(k)) == _cell(v) for k, v in match.items()):
            removed += 1
        else:
            kept.append(row)
    normalized = [{col: _cell(raw.get(col)) for col in columns} for raw in incoming]
    total = atomic_write_table(table, kept + normalized)
    return {"removed": removed, "written": len(normalized), "total": total}


def graded_stakes_winners() -> list[dict]:
    """Winner rows from graded_stakes (Place == 1), shaped for hypomating."""
    records = []
    for row in load_table("graded_stakes"):
        place = str(row.get("Place") or "").strip()
        if place and place not in {"1", "1.0"}:
            continue
        name = (row.get("Horse Name") or "").strip()
        if not name or name.upper() in {"N/A", "NAN", "NONE"}:
            continue
        grade = (row.get("Grade") or row.get("Group") or "").strip()
        foal = (row.get("Foaling Year") or row.get("Year Foaled") or "").strip()
        records.append({
            "Horse Name": name,
            "Foaling Year": foal,
            "Year Foaled": foal,
            "Sex": (row.get("Sex") or "").strip(),
            "Race Name": (row.get("Race Name") or "").strip(),
            "Race Year": (row.get("Race Year") or "").strip(),
            "Grade": grade,
            "Group": grade,
            "Purse": (row.get("Purse") or "").strip(),
            "Distance": (row.get("Distance") or "").strip(),
            "Track": (row.get("Track") or "").strip(),
            "Date": (row.get("Date") or "").strip(),
            "Place": 1,
            "Country": (row.get("Country") or "").strip(),
            "Age": (row.get("Age") or "").strip(),
        })
    return records


_FEE_HISTORY: tuple[object, dict[str, dict[int, int]]] | None = None


def stud_fee_history() -> dict[str, dict[int, int]]:
    """stallion name_key -> {year: fee in US dollars} from stud_fees.

    Cached per table version: catalog pages call this once per lot. Foreign fees
    are converted (JPY 4,000,000 is ~$25,850, not $4,000,000); fees in currencies
    without a rate and "Private" rows are left out. Where two sources list the
    same season, a dollar fee wins.
    """
    global _FEE_HISTORY
    from crossr_core.fees import parse_fee, usd_estimate

    rows = load_table("stud_fees")
    version = _MTIME.get("stud_fees")
    if _FEE_HISTORY is not None and _FEE_HISTORY[0] == version:
        return _FEE_HISTORY[1]
    out: dict[str, dict[int, int]] = {}
    dollars: set[tuple[str, int]] = set()
    for row in rows:
        key = name_key(row.get("Stallion") or "")
        if not key:
            continue
        try:
            year = int(str(row.get("Year") or "").strip())
        except ValueError:
            continue
        amount, currency = parse_fee(row.get("Stud Fee"))
        fee = usd_estimate(amount, currency)
        if not fee or ((key, year) in dollars and currency != "USD"):
            continue
        if currency == "USD":
            dollars.add((key, year))
        out.setdefault(key, {})[year] = fee
    _FEE_HISTORY = (version, out)
    return out


_BREEDING_INDEX: tuple[object, dict[str, list[dict]]] | None = None


def breeding_for_stallion(stallion: str, year: int | None = None) -> list[dict]:
    global _BREEDING_INDEX
    rows_all = load_table("breeding_seasons")
    version = _MTIME.get("breeding_seasons")
    if _BREEDING_INDEX is None or _BREEDING_INDEX[0] != version:
        index: dict[str, list[dict]] = {}
        for row in rows_all:
            index.setdefault(name_key(row.get("stallion") or ""), []).append(row)
        _BREEDING_INDEX = (version, index)
    rows = []
    for row in _BREEDING_INDEX[1].get(name_key(stallion), []):
        if year is not None and str(row.get("breeding_year")) != str(year):
            continue
        rows.append(row)
    return rows


def broodmare_sire_rows(year: int | None = None) -> list[dict]:
    """Latest (or year-filtered) Broodmare list from stallion_rankings."""
    candidates = []
    for row in load_table("stallion_rankings"):
        if (row.get("list") or "").strip() != "Broodmare":
            continue
        try:
            y = int(str(row.get("year") or "").strip())
        except ValueError:
            continue
        if year is not None and y != year:
            continue
        candidates.append((y, row))
    if not candidates:
        return []
    if year is None:
        latest = max(y for y, _ in candidates)
        candidates = [(y, r) for y, r in candidates if y == latest]
    out = []
    for _y, row in candidates:
        def as_int(value):
            try:
                return int(float(re.sub(r"[^\d.-]", "", str(value or "0")) or 0))
            except ValueError:
                return 0

        def as_float(value):
            try:
                return float(re.sub(r"[^\d.-]", "", str(value or "0")) or 0)
            except ValueError:
                return 0.0

        out.append({
            "rank": as_int(row.get("rank")),
            "stallion": row.get("stallion") or "",
            "lineage": row.get("sire") or "",
            "dams": as_int(row.get("dams")),
            "rnrs": as_int(row.get("runners")),
            "wnrs": as_int(row.get("winners")),
            "chief_earner": row.get("chief_earner") or "",
            "chief_earners_earnings": as_int(row.get("chief_earner_earnings")),
            "earnings_2026": as_int(row.get("year_earnings")),
            "cum_swrs": as_int(row.get("cumulative_stakes_winners")),
            "aei": as_float(row.get("aei")),
            "comp_index": as_float(row.get("comparable_index")),
            "cum_foals": as_int(row.get("cumulative_foals")),
        })
    out.sort(key=lambda item: item["rank"] or 10**9)
    return out


_FOAL_YEARS: dict[str, list[int]] = {}
_FOAL_YEARS_VERSION: object = None


def _foal_years_for(names) -> dict[str, list[int]]:
    """name_key -> race_horses foaling years for just these names.

    One SQL query for the names not cached yet: loading all of race_horses at
    startup pushed the API past Railway's health-check window.
    """
    global _FOAL_YEARS_VERSION
    version = table_version("race_horses")
    if version != _FOAL_YEARS_VERSION:
        _FOAL_YEARS.clear()
        _FOAL_YEARS_VERSION = version
    keys = {name_key(n) for n in names} - {""}
    missing = [k for k in keys if k not in _FOAL_YEARS]
    if missing:
        found: dict[str, list[int]] = {k: [] for k in missing}
        with _pg().connect() as conn:
            rows = conn.execute(
                "SELECT upper(name_key), foal_year FROM race_horses "
                "WHERE upper(name_key) = ANY(%s) AND foal_year ~ '^[0-9]{4}$'",
                (missing,),
            ).fetchall()
        for key, year in rows:
            found.setdefault(name_key(key), []).append(int(year))
        _FOAL_YEARS.update(found)
    return {k: _FOAL_YEARS.get(k, []) for k in keys}


def _foal_year_for_stud(years: list[int], first_season: int) -> int:
    """The namesake old enough to stand in first_season (a sire is 3+ at his first season)."""
    fits = [y for y in set(years) if y <= first_season - 2]
    return max(fits) if fits else 0


def stallion_foal_year(name: str, first_season: int) -> int:
    """Foaling year of the stallion called name who stood by first_season (0 if unknown)."""
    return _foal_year_for_stud(_foal_years_for([name]).get(name_key(name), []), first_season)


_ROSTER_CACHE: dict = {}
_ROSTER_TABLES = ("stud_fees", "breeding_seasons", "stallion_rankings", "race_horses")


def stallion_roster_from_fees(year: int | None = None) -> dict[str, dict]:
    """Cached _stallion_roster_from_fees (rebuilt when a source table changes).

    Callers get their own copy of each card, so edits never leak into the cache.
    """
    key = (year, datetime.date.today().year, tuple(table_version(t) for t in _ROSTER_TABLES))
    hit = _ROSTER_CACHE.get(year)
    if hit is None or hit[0] != key:
        hit = (key, _stallion_roster_from_fees(year))
        _ROSTER_CACHE[year] = hit
    return {name: dict(card) for name, card in hit[1].items()}


# Without a known foaling year, seasons this far apart belong to different
# namesakes (Little Big Bear has a 1998 row from an older horse). Shorter gaps
# are just missing data years and must not move "entered stud".
_SEASON_GAP = 10


def _latest_run_start(seasons, foal_year: int = 0) -> int:
    """First stud season of the current stallion behind a card.

    With his foaling year known, seasons before he was 2 are a namesake's
    (Constitution, foaled 2011: the 2006 row is another horse). Otherwise drop
    seasons separated from the latest run by _SEASON_GAP or more.
    """
    years = sorted(y for y in seasons if y)
    if not years:
        return 0
    if foal_year:
        own = [y for y in years if y >= foal_year + 2]
        return own[0] if own else years[-1]
    start = years[-1]
    for y in reversed(years[:-1]):
        if start - y >= _SEASON_GAP:
            break
        start = y
    return start


def _stallion_roster_from_fees(year: int | None = None) -> dict[str, dict]:
    """Active stud cards derived from stud_fees (+ latest breeding_seasons).

    "Stud Duty Year" is the FIRST season in stud_fees or breeding_seasons (entered stud). The fee is
    from the latest season up to the current year: announced future seasons
    (Racing Post lists next year's fees) never count as the current fee.
    "Latest Season" is that season's year.
    """
    from crossr_core.fees import format_fee, parse_fee, usd_estimate

    current_year = datetime.date.today().year
    fees = load_table("stud_fees")
    by_stallion: dict[str, list] = {}
    for row in fees:
        key = name_key(row.get("Stallion") or "")
        if not key:
            continue
        by_stallion.setdefault(key, []).append(row)
    breeding = {}
    first_bred: dict[str, int] = {}
    bred_years: dict[str, set] = {}
    for row in load_table("breeding_seasons"):
        key = name_key(row.get("stallion") or "")
        try:
            y = int(str(row.get("breeding_year") or "").strip())
        except ValueError:
            continue
        first_bred[key] = min(y, first_bred.get(key, y))
        bred_years.setdefault(key, set()).add(y)
        prior = breeding.get(key)
        if prior is None or y > prior[0]:
            breeding[key] = (y, row)
    ranks = {}
    for row in load_table("stallion_rankings"):
        if (row.get("list") or "").strip() != "General":
            continue
        key = name_key(row.get("stallion") or "")
        try:
            y = int(str(row.get("year") or "").strip())
            r = int(str(row.get("rank") or "").strip())
        except ValueError:
            continue
        prior = ranks.get(key)
        if prior is None or y > prior[0]:
            ranks[key] = (y, r)
    foal_years = _foal_years_for(by_stallion.keys())

    profiles = {}
    for key, rows in by_stallion.items():
        dated = []
        for row in rows:
            try:
                y = int(str(row.get("Year") or "").strip())
            except ValueError:
                continue
            dated.append((y, row))
        if not dated:
            continue
        # A mares-bred report counts as a season even when no fee was listed.
        seasons = {y for y, _ in dated} | set(bred_years.get(key, ()))
        latest = max(seasons)
        from crossr_core.identity import card_foal_year

        # His own foaling year from the rankings (rows that describe stallions):
        # the youngest ranked namesake old enough to stand in the latest season.
        # race_horses is not used here: a young unraced namesake would fit too.
        own_year = card_foal_year((rows[0].get("Stallion") or ""), latest)
        first_season = _latest_run_start(seasons, own_year)
        if year is not None:
            pool = [pair for pair in dated if pair[0] == year]
        else:
            pool = [pair for pair in dated if pair[0] <= current_year]
        pool = pool or dated
        y = max(p[0] for p in pool)
        # Same season from two sources: prefer a row with a fee (and in dollars).
        season_rows = [r for yy, r in pool if yy == y]
        row = max(
            season_rows,
            key=lambda r: (parse_fee(r.get("Stud Fee"))[0] is not None, parse_fee(r.get("Stud Fee"))[1] == "USD"),
        )
        amount, currency = parse_fee(row.get("Stud Fee"))
        fee = usd_estimate(amount, currency) or 0
        mb = breeding.get(key)
        rank = ranks.get(key)
        display = (row.get("Stallion") or "").strip()
        # The rankings record the stallion's own foaling year; race_horses may only
        # know a younger namesake (e.g. Englishman 2002 vs Englishman 2023).
        year_born = card_foal_year(display, first_season) or _foal_year_for_stud(foal_years.get(key, []), first_season)
        profiles[display] = {
            "Farm": row.get("Farm") or "",
            "Year Born": year_born,
            "Stud Duty Year": first_season,
            "Latest Season": y,
            "Stud Fee": fee,
            "Stud Fee Display": format_fee(row.get("Stud Fee")) or "",
            "Stud Fee Currency": currency or "",
            "Status": "active",
            "Notes": row.get("Notes") or "",
            "Surface": "",
            "Height": "N/A",
            "Conformation & Physical Profile": "",
            "Major Wins": "",
            "Career Highlight": "",
            "Race Record": {},
            "sire_rank": rank[1] if rank else "",
            "mares_bred_2025": (mb[1].get("mares_bred") if mb and mb[0] >= 2025 else "") if mb else "",
            "_name_key": key,
        }
    return profiles

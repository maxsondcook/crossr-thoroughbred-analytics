"""Race wins for stakes status: race results first, graded_stakes to fill gaps.

Sources (all through crossr_core.sample_db, i.e. Railway Postgres):

1. ``race_starts`` joined to ``race_horses`` (Equibase results) - the main
   source. Each win (place 1) is classified by ``grade`` and ``race_name``:
   - G1/G2/G3: ``grade`` is 1, 2 or 3;
   - Listed: the name carries "(Listed)" or "(Black Type)";
   - Stakes: any other named US/Canadian race (e.g. "Phoenix Handicap",
     a 1950s Preakness, restricted state-bred stakes). Generic race types
     (Allowance, Claiming, Maiden..., Starter Handicap) and named races abroad
     (Japanese "Stakes"/"Tokubetsu", UK novice races...) are not stakes.
   Only stakes wins are returned from this source.
2. ``graded_stakes`` - IFHA graded/group results plus hand-entered wins for
   horses without an Equibase profile (allowance, maiden, ... marked in the
   Group/Grade column). A row is used only when race_starts has no start for
   that horse on that date, so it fills in horses not scraped yet and overseas
   Group races Equibase lacks, without counting a race twice.

Black type (see is_black_type): G1-G3 ("Grade 1", "Group 2", "Gr3"), Listed / L,
Stakes. Anything else is a win that shows in race lists but never counts as a
stakes win.

Records keep the old graded_stakes shape (Horse Name, Foaling Year, Sex, Race
Name, Race Year, Grade/Group, Place, Track, Country, Date (ISO), ...), so
hypomating_engine.build_horse_summaries and the API stakes index are unchanged.
"""

from __future__ import annotations

import re
import threading

from crossr_core import sample_db

MISSING_NAMES = {"", "N/A", "NAN", "NONE", "<NA>"}

_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
_GRADE_RE = re.compile(r"^(?:G|GR|GRADE|GROUP)\s*\.?\s*([123])\b")
_LISTED = {"L", "LR", "LISTED", "LISTED RACE", "STAKES", "STK", "BLACK TYPE", "BT"}

# Equibase's generic race types (the whole name is made of these words).
_GENERIC_RACE = re.compile(
    r"^(starter |waiver |optional |maiden |claiming |novice |selling |third class |rated |"
    r"index race.*|premio/.*|condiciona.*|condizionata.*|clasico|winner|trial|conditions?|"
    r"special weight|weight for age|handicap|allowance|claiming|maiden|plate|hurdle|juvenile|"
    r"optional claiming|chase|steeplechase|stakes| )+(\(.*\))?$",
    re.I,
)
_LISTED_TAG = re.compile(r"\((listed|black type)\)", re.I)
_NOT_STAKES_WORDS = re.compile(
    r"\b(starter|claiming|allowance|maiden|benchmark|novice|nursery|selling|rated|"
    r"class \d|tokubetsu|condicional|premio)\b",
    re.I,
)
_NORTH_AMERICA = {"USA", "CAN"}
_CLASS_TAG = re.compile(r"\s*\((?:gr\.?\s*[123]|grade\s*[123]|listed|black type)\)", re.I)

_lock = threading.Lock()
_cache: dict[bool, tuple[tuple, list[dict]]] = {}


def graded_level(grade) -> int | None:
    """1/2/3 for a graded or group race, else None."""
    match = _GRADE_RE.match(str(grade or "").strip().upper())
    return int(match.group(1)) if match else None


def is_black_type(grade) -> bool:
    """True for G1-G3, Listed and (ungraded) Stakes races; False for allowance etc."""
    text = str(grade or "").strip().upper()
    if not text:
        return False
    return graded_level(text) is not None or text in _LISTED


def classify_race(grade, race_name, country) -> str | None:
    """Race class of an Equibase win: 'G1'..'G3', 'Listed', 'Stakes', or None (not a stakes)."""
    grade = str(grade or "").strip()
    if grade in ("1", "2", "3"):
        return "G" + grade
    name = str(race_name or "").strip()
    if not name or name.upper() in MISSING_NAMES or _GENERIC_RACE.match(name):
        return None
    if _LISTED_TAG.search(name):
        return "Listed"
    if str(country or "").strip().upper() in _NORTH_AMERICA and not _NOT_STAKES_WORDS.search(name):
        return "Stakes"
    return None


def horse_key(name, foal_year="") -> tuple[str, str]:
    """(cleaned name, foal year) - same cleaning as main.clean_horse_name."""
    cleaned = re.sub(r"\(.*?\)", "", str(name or ""))
    cleaned = cleaned.replace(".", "").replace("'", "").replace(",", "")
    year = str(foal_year or "").strip().split(".")[0]
    return " ".join(cleaned.split()).strip().upper(), year


def _clean(value) -> str:
    text = "" if value is None else str(value).strip()
    return "" if text.upper() in {"N/A", "NAN", "NONE", "<NA>"} else text


def _int_or_blank(value):
    text = _clean(value)
    if not text:
        return ""
    try:
        return int(round(float(text)))
    except ValueError:
        return ""


def _race_year(row: dict):
    iso = _clean(row.get("Date (ISO)"))
    if len(iso) >= 4 and iso[:4].isdigit():
        return int(iso[:4])
    match = _YEAR_RE.search(_clean(row.get("Date")))
    if match:
        return int(match.group(1))
    return _int_or_blank(row.get("Race Year"))


def _normalize(row) -> dict | None:
    rec = {col: _clean(row.get(col)) for col in sample_db.TABLES["graded_stakes"]}
    if rec["Horse Name"].upper() in MISSING_NAMES:
        return None
    rec["Race Year"] = _race_year(rec)
    rec["Grade"] = rec["Group"] or rec["Grade"]
    rec["Place"] = _int_or_blank(rec["Place"])
    year = _int_or_blank(rec["Foaling Year"])
    rec["Foaling Year"] = str(year) if year != "" else ""
    return rec


# ---------------------------------------------------------------------------
# race_starts
# ---------------------------------------------------------------------------

_STARTS_SQL = """
    SELECT h.display_name, h.foal_year, h.sex, h.foaled_in,
           s.race_date, s.place, s.grade, s.race_name, s.country, s.track
    FROM race_starts s
    JOIN race_horses h ON h.id = s.horse_id
    ORDER BY s.race_date, s.id
"""
# The ORDER BY keeps results identical run to run: without it Postgres returns rows in
# any order, and which of two same-named horses' records wins changed hypomating output.


# Equibase chart names sometimes keep the state-bred tag and weight: "Cara's Time (NY) 57".
_CHART_SUFFIX = re.compile(r"\s*\([A-Z]{2,3}\)\s*\d+\s*$")


def clean_display_name(name: str) -> str:
    return _CHART_SUFFIX.sub("", str(name or "")).strip()


def _iter_starts():
    """(display_name, foal_year, sex, foaled_in, race_date, place, grade, race_name, country, track)."""
    with sample_db._pg().connect() as conn:
        with conn.cursor(name="stakes_starts") as cur:
            cur.itersize = 10000
            cur.execute(_STARTS_SQL)
            for rec in cur:
                row = tuple("" if v is None else str(v) for v in rec)
                yield (clean_display_name(row[0]),) + row[1:]


def _start_record(name, foal_year, sex, race_date, grade_class, race_name, country, track) -> dict:
    return {
        "Race Name": _CLASS_TAG.sub("", race_name).strip(),
        "Horse Name": name,
        "Year Foaled": foal_year,
        "Foaling Year": foal_year,
        "Sex": sex,
        "Place": 1,
        "Country": country,
        "Track": track,
        "Date": race_date,
        "Date (ISO)": race_date,
        "Race Year": int(race_date[:4]) if race_date[:4].isdigit() else "",
        "Distance": "",
        "Age": "",
        "Purse": "",
        "Group": grade_class,
        "Grade": grade_class,
        "Source": "race_starts",
    }


def _versions() -> tuple:
    return tuple(sample_db.table_version(t) for t in ("race_starts", "race_horses", "graded_stakes"))


def load_stakes_records(path=None, winners_only=True):
    """Merged race records (see module doc). ``path`` is ignored (kept for old callers).

    Reloads when race_starts, race_horses or graded_stakes change.
    """
    version = _versions()
    key = bool(winners_only)
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] == version:
            return hit[1]

    # Fill-in candidates first, so only their horses' start dates are kept while streaming.
    fill = []
    for row in sample_db.load_table("graded_stakes"):
        rec = _normalize(row)
        if rec is None or (winners_only and rec["Place"] != 1):
            continue
        fill.append(rec)
    names = {horse_key(rec["Horse Name"])[0] for rec in fill}

    records = []
    # race dates of fill-in horses' starts -> the stakes record built from that start (or None)
    started: dict[tuple, dict] = {}        # keyed by (name, foal_year)
    started_any_year: dict[str, dict] = {}  # by name, when race_horses has no foal year
    for name, foal_year, sex, _foaled, race_date, place, grade, race_name, country, track in _iter_starts():
        k = horse_key(name, foal_year)
        rec = None
        if not winners_only or place.strip() == "1":
            grade_class = classify_race(grade, race_name, country)
            if grade_class is not None:
                rec = _start_record(name, foal_year, sex, race_date, grade_class, race_name, country, track)
                records.append(rec)
        if k[0] in names:
            bucket = started.setdefault(k, {}) if k[1] else started_any_year.setdefault(k[0], {})
            bucket[race_date] = rec or bucket.get(race_date)

    for rec in fill:
        k = horse_key(rec["Horse Name"], rec["Foaling Year"])
        date = rec.get("Date (ISO)") or ""
        dates = started.get(k) or started_any_year.get(k[0]) or {}
        if date and date in dates:
            # Same race already counted from race_starts; borrow the details it lacks.
            twin = dates[date]
            if twin is not None:
                for col in ("Purse", "Distance", "Age"):
                    if not twin.get(col) and rec.get(col):
                        twin[col] = rec[col]
            continue
        rec["Source"] = "graded_stakes"
        records.append(rec)

    with _lock:
        _cache[key] = (version, records)
    return records


def load_stakes_data(path=None, winners_only=True):
    """Backward-compatible name: winners (see load_stakes_records)."""
    return load_stakes_records(path=path, winners_only=winners_only)

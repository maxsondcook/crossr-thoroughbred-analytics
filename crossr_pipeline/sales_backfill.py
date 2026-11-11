"""Fill blank sales_lots fields from our other data. Never overwrites a value.

Keeneland's results files carry no damsire, color, foaled location or foal
date, so those lots start blank. Fills, in order:

  damsire          other lots of the same dam (one damsire among them), then
                   the dam's race_horses row (a mare/filly with that name,
                   one sire), then her tree in pedigrees (one tree per name)
  birth_year       other lots of the same named horse (name + sire + dam),
                   then race_horses (same name and sire, one foal year)
  color            other lots of the same horse (sire + dam + birth year), then
  foaled_location  race_horses for a named horse (Equibase color / state)

Runs after every sales import (`sales_backfill(conn)`).
"""

from __future__ import annotations

import re
from collections import defaultdict

from crossr_pipeline import state
from crossr_pipeline.names import clean_name
from sales_history import clean_search_string

JOB = "sales-backfill"

# Equibase colors -> the sales_lots spelling most files use.
COLORS = {
    "B": "B", "BAY": "B", "CH": "CH", "DK B/BR": "DB/BR", "DK B": "DB/BR", "BR": "DB/BR",
    "GR/RO": "GR/RO", "GR": "GR/RO", "RO": "GR/RO", "BLK": "BLK", "WH": "WH", "PAL": "PAL",
}

PLACES = {
    "AL": "ALABAMA", "AZ": "ARIZONA", "AR": "ARKANSAS", "CA": "CALIFORNIA", "CO": "COLORADO",
    "CT": "CONNECTICUT", "DE": "DELAWARE", "FL": "FLORIDA", "GA": "GEORGIA", "ID": "IDAHO",
    "IL": "ILLINOIS", "IN": "INDIANA", "IA": "IOWA", "KS": "KANSAS", "KY": "KENTUCKY",
    "LA": "LOUISIANA", "ME": "MAINE", "MD": "MARYLAND", "MA": "MASSACHUSETTS", "MI": "MICHIGAN",
    "MN": "MINNESOTA", "MS": "MISSISSIPPI", "MO": "MISSOURI", "MT": "MONTANA", "NE": "NEBRASKA",
    "NV": "NEVADA", "NH": "NEW HAMPSHIRE", "NJ": "NEW JERSEY", "NM": "NEW MEXICO", "NY": "NEW YORK",
    "NC": "NORTH CAROLINA", "ND": "NORTH DAKOTA", "OH": "OHIO", "OK": "OKLAHOMA", "OR": "OREGON",
    "PA": "PENNSYLVANIA", "RI": "RHODE ISLAND", "SC": "SOUTH CAROLINA", "SD": "SOUTH DAKOTA",
    "TN": "TENNESSEE", "TX": "TEXAS", "UT": "UTAH", "VT": "VERMONT", "VA": "VIRGINIA",
    "WA": "WASHINGTON", "WV": "WEST VIRGINIA", "WI": "WISCONSIN", "WY": "WYOMING", "PR": "PUERTO RICO",
    "ON": "ONTARIO", "BC": "BRITISH COLUMBIA", "AB": "ALBERTA", "MB": "MANITOBA", "SK": "SASKATCHEWAN",
    "QC": "QUEBEC",
}

UNNAMED = re.compile(r"^(hip\b|\d{4}-)|\sx\s", re.I)
FIELDS = ("damsire", "damsire_key", "birth_year", "color", "foaled_location")


def is_named(name: str | None) -> bool:
    return bool(name) and not UNNAMED.search(name)


def map_color(value: str | None) -> str:
    return COLORS.get((value or "").strip().upper(), "")


def map_place(value: str | None) -> str:
    code = (value or "").strip().upper()
    return PLACES.get(code, code)


def counts(conn) -> dict[str, dict[str, int]]:
    """Blank counts by catalog_year (damsire, color, foaled_location, mare birth year)."""
    out: dict[str, dict[str, int]] = {}
    for row in conn.execute(
        """
        SELECT catalog_year,
               COUNT(*) AS lots,
               COUNT(*) FILTER (WHERE COALESCE(damsire, '') = '') AS damsire,
               COUNT(*) FILTER (WHERE COALESCE(color, '') = '') AS color,
               COUNT(*) FILTER (WHERE COALESCE(foaled_location, '') = '') AS foaled_location,
               COUNT(*) FILTER (WHERE COALESCE(birth_year, '') = '') AS birth_year_blank,
               COUNT(*) FILTER (WHERE sex_letter = 'M' AND birth_year ~ '^[0-9]{4}$'
                                AND birth_year::int >= catalog_year::int - 1) AS mare_bad_year
        FROM sales_lots WHERE catalog_year >= '2022' GROUP BY 1 ORDER BY 1
        """
    ):
        out[row["catalog_year"]] = {k: v for k, v in row.items() if k != "catalog_year"}
    return out


def _sql_fills(conn, run: state.Run) -> None:
    statements = {
        "damsire from other lots of the dam": """
            WITH d AS (
                SELECT dam_key, MIN(damsire) AS damsire, MIN(damsire_key) AS damsire_key
                FROM sales_lots WHERE COALESCE(damsire, '') <> '' AND COALESCE(dam_key, '') <> ''
                GROUP BY dam_key HAVING COUNT(DISTINCT damsire_key) = 1)
            UPDATE sales_lots s SET damsire = d.damsire, damsire_key = d.damsire_key
            FROM d WHERE s.dam_key = d.dam_key AND COALESCE(s.damsire, '') = ''""",
        "birth_year from other lots of the horse": """
            WITH h AS (
                SELECT name_key, sire_key, dam_key, MIN(birth_year) AS birth_year
                FROM sales_lots WHERE COALESCE(birth_year, '') <> '' AND COALESCE(name_key, '') <> ''
                GROUP BY 1, 2, 3 HAVING COUNT(DISTINCT birth_year) = 1)
            UPDATE sales_lots s SET birth_year = h.birth_year
            FROM h WHERE s.name_key = h.name_key AND s.sire_key = h.sire_key AND s.dam_key = h.dam_key
              AND COALESCE(s.birth_year, '') = ''""",
        "color from other lots of the horse": """
            WITH h AS (
                SELECT sire_key, dam_key, birth_year, MIN(color) AS color
                FROM sales_lots WHERE COALESCE(color, '') NOT IN ('', 'UNK') AND COALESCE(birth_year, '') <> ''
                GROUP BY 1, 2, 3 HAVING COUNT(DISTINCT color) = 1)
            UPDATE sales_lots s SET color = h.color
            FROM h WHERE s.sire_key = h.sire_key AND s.dam_key = h.dam_key AND s.birth_year = h.birth_year
              AND COALESCE(s.color, '') = ''""",
        "foaled_location from other lots of the horse": """
            WITH h AS (
                SELECT sire_key, dam_key, birth_year, MIN(foaled_location) AS place
                FROM sales_lots WHERE COALESCE(foaled_location, '') <> '' AND COALESCE(birth_year, '') <> ''
                GROUP BY 1, 2, 3 HAVING COUNT(DISTINCT foaled_location) = 1)
            UPDATE sales_lots s SET foaled_location = h.place
            FROM h WHERE s.sire_key = h.sire_key AND s.dam_key = h.dam_key AND s.birth_year = h.birth_year
              AND COALESCE(s.foaled_location, '') = ''""",
    }
    for label, sql in statements.items():
        n = conn.execute(sql).rowcount
        run.bump(label, n)
        print(f"  {label}: {n}", flush=True)


def _race_index(conn) -> dict[str, list[dict]]:
    index: dict[str, list[dict]] = defaultdict(list)
    for row in conn.execute(
        "SELECT name_key, display_name, foal_year, sex, sire, dam, color, foaled_in FROM race_horses "
        "WHERE status = 'ok' AND COALESCE(name_key, '') <> ''"
    ):
        index[clean_name(row["display_name"] or row["name_key"])].append(row)
    return index


def _pedigree_damsires(conn) -> dict[str, str]:
    """clean name -> sire, for names with exactly one tree."""
    seen: dict[str, set[str]] = defaultdict(set)
    for row in conn.execute("SELECT key, tree->'Sire'->>'Horse' AS sire FROM pedigrees"):
        name = clean_name(re.sub(r"\(\d{4}\)", "", row["key"] or ""))
        if name and row["sire"] and "unknown" not in row["sire"].lower():
            seen[name].add(re.sub(r"\s*\(\d{4}\)\s*$", "", row["sire"]).strip())
    return {name: sires.pop() for name, sires in seen.items() if len(sires) == 1}


def _one(rows: list[dict], field: str) -> str:
    values = {(row.get(field) or "").strip() for row in rows} - {""}
    return values.pop() if len(values) == 1 else ""


def _external_fills(conn, run: state.Run) -> None:
    races = _race_index(conn)
    tree_sires = _pedigree_damsires(conn)
    updates: list[tuple] = []
    for lot in conn.execute(
        """
        SELECT source_file, hip, name_key, name, sire, dam, damsire, birth_year, color, foaled_location
        FROM sales_lots
        WHERE COALESCE(damsire, '') = '' OR COALESCE(birth_year, '') = ''
           OR COALESCE(color, '') = '' OR COALESCE(foaled_location, '') = ''
        """
    ):
        fill: dict[str, str] = {}
        if not lot["damsire"] and lot["dam"]:
            dam_key = clean_name(lot["dam"])
            mares = [r for r in races.get(dam_key, []) if (r["sex"] or "") in ("M", "F", "")]
            year = int(lot["birth_year"]) if (lot["birth_year"] or "").isdigit() else None
            if year:
                # A dam is 3 to 25 years older than her foal.
                mares = [
                    r for r in mares
                    if not (r["foal_year"] or "").isdigit() or year - 25 <= int(r["foal_year"]) <= year - 3
                ]
            sire = _one(mares, "sire") or tree_sires.get(dam_key, "")
            if sire:
                fill["damsire"] = sire.upper()
                fill["damsire_key"] = clean_search_string(sire)
        if is_named(lot["name"]) and lot["sire"]:
            sire_key = clean_name(lot["sire"])
            dam_key = clean_name(lot["dam"] or "")
            same = [
                r for r in races.get(clean_name(lot["name"]), [])
                if clean_name(r["sire"] or "") == sire_key
                and (not dam_key or not r["dam"] or clean_name(r["dam"]) == dam_key)
            ]
            if same:
                if not lot["birth_year"] and _one(same, "foal_year"):
                    fill["birth_year"] = _one(same, "foal_year")
                if not lot["color"] and map_color(_one(same, "color")):
                    fill["color"] = map_color(_one(same, "color"))
                if not lot["foaled_location"] and _one(same, "foaled_in"):
                    fill["foaled_location"] = map_place(_one(same, "foaled_in"))
        if fill:
            updates.append((
                fill.get("damsire"), fill.get("damsire_key"), fill.get("birth_year"),
                fill.get("color"), fill.get("foaled_location"),
                lot["source_file"], lot["hip"], lot["name_key"],
            ))
            for key in fill:
                if key != "damsire_key":
                    run.bump(f"{key} from race_horses/pedigrees")
    with conn.cursor() as cur:
        cur.executemany(
            """
            UPDATE sales_lots SET
                damsire = CASE WHEN COALESCE(damsire, '') = '' THEN COALESCE(%s, damsire) ELSE damsire END,
                damsire_key = CASE WHEN COALESCE(damsire_key, '') = '' THEN COALESCE(%s, damsire_key) ELSE damsire_key END,
                birth_year = CASE WHEN COALESCE(birth_year, '') = '' THEN COALESCE(%s, birth_year) ELSE birth_year END,
                color = CASE WHEN COALESCE(color, '') = '' THEN COALESCE(%s, color) ELSE color END,
                foaled_location = CASE WHEN COALESCE(foaled_location, '') = '' THEN COALESCE(%s, foaled_location) ELSE foaled_location END
            WHERE source_file = %s AND hip = %s AND name_key IS NOT DISTINCT FROM %s
            """,
            updates,
        )
    print(f"  lots filled from race_horses/pedigrees: {len(updates)}", flush=True)


def sales_backfill(conn) -> dict:
    from crossr_pipeline import versions as pgdb

    state.ensure(conn)
    run = state.Run(conn, JOB)
    try:
        # Two rounds: a damsire or birth year found outside unlocks more
        # matches between lots.
        for _ in range(2):
            _sql_fills(conn, run)
            _external_fills(conn, run)
    except BaseException as exc:
        run.finish("failed", str(exc)[:300])
        raise
    pgdb.flush_versions(conn, ("sales_lots",))
    run.finish("ok")
    return run.counts

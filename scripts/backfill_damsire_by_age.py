"""Fill blank sales_lots damsires using the dam's age to tell namesakes apart.

crossr_pipeline/sales_backfill.py fills a blank damsire only when every mare
with the dam's name has the same sire, so a namesake from another era blocks
the fill. This script narrows the candidates to mares old enough to have
foaled the lot (foaled 3-27 years before the sale, or undated), from
race_horses (fillies/mares or unknown sex) and pedigree trees, and fills only
when exactly one sire remains. Existing values are never overwritten.

Every fill is recorded in sales_lots_fill_log (lot, field, value, source), so
it can be traced or undone:
    DELETE / UPDATE ... using sales_lots_fill_log WHERE source = 'damsire-by-age'

Usage:
    python3 scripts/backfill_damsire_by_age.py            # dry run (counts only)
    python3 scripts/backfill_damsire_by_age.py --apply    # write to DATABASE_URL
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crossr_core import railway_db  # noqa: E402

SOURCE = "damsire-by-age"
MIN_DAM_AGE, MAX_DAM_AGE = 3, 27


def N(x: str) -> str:
    return f"upper(regexp_replace(regexp_replace(coalesce({x},''), '\\(.*?\\)', '', 'g'), '[^A-Za-z0-9 ]', '', 'g'))"


PREP = [
    f"""CREATE TEMP TABLE b_blank AS
        SELECT DISTINCT {N('dam')} AS k, catalog_year::int AS y FROM sales_lots
        WHERE coalesce(damsire, '') = '' AND coalesce(dam, '') <> '' AND catalog_year ~ '^[0-9]{{4}}$'""",
    # Candidate mares: race_horses fillies/mares (or unknown sex) and pedigree trees, with their sire.
    f"""CREATE TEMP TABLE b_cand AS
        SELECT {N('display_name')} AS k, nullif(substring(foal_year from '^[0-9]{{4}}'), '')::int AS fy,
               upper(trim(sire)) AS sire_display, {N('sire')} AS s
        FROM race_horses WHERE upper(coalesce(sex, '')) IN ('F', 'M', '') AND coalesce(sire, '') <> ''
        UNION
        SELECT {N("regexp_replace(key, ' \\(\\d{{4}}\\)$', '')")}, substring(key from '\\((\\d{{4}})\\)$')::int,
               upper(trim(regexp_replace(tree #>> '{{Sire,Horse}}', ' \\(\\d{{4}}\\)$', ''))), {N("tree #>> '{{Sire,Horse}}'")}
        FROM pedigrees WHERE tree #>> '{{Sire,Horse}}' IS NOT NULL""",
    "CREATE INDEX ON b_blank (k)",
    "CREATE INDEX ON b_cand (k)",
    f"""CREATE TEMP TABLE b_fill AS
        SELECT b.k, b.y, min(c.sire_display) AS damsire, min(c.s) AS damsire_key_norm
        FROM b_blank b JOIN b_cand c ON c.k = b.k
         AND (c.fy IS NULL OR c.fy BETWEEN b.y - {MAX_DAM_AGE} AND b.y - {MIN_DAM_AGE})
        WHERE c.s <> ''
        GROUP BY b.k, b.y
        HAVING count(DISTINCT c.s) = 1""",
    "CREATE INDEX ON b_fill (k, y)",
]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the fills (default: dry run)")
    args = ap.parse_args()

    with railway_db.connect() as conn:
        conn.execute("SET statement_timeout = '600s'")
        for stmt in PREP:
            conn.execute(stmt)
        lots = conn.execute(
            f"""SELECT count(*) FROM sales_lots s JOIN b_fill f ON f.k = {N('s.dam')} AND f.y = s.catalog_year::int
                WHERE coalesce(s.damsire, '') = '' AND s.catalog_year ~ '^[0-9]{{4}}$'"""
        ).fetchone()[0]
        blank = conn.execute("SELECT count(*) FROM sales_lots WHERE coalesce(damsire, '') = ''").fetchone()[0]
        print(f"blank damsires: {blank:,}; fillable with one age-checked sire: {lots:,}")
        sample = conn.execute(
            f"""SELECT s.catalog_year, s.hip, s.dam, f.damsire FROM sales_lots s
                JOIN b_fill f ON f.k = {N('s.dam')} AND f.y = s.catalog_year::int
                WHERE coalesce(s.damsire, '') = '' AND s.catalog_year ~ '^[0-9]{{4}}$' ORDER BY random() LIMIT 8"""
        ).fetchall()
        for row in sample:
            print("  e.g.", row)
        if not args.apply:
            print("dry run: nothing written (use --apply)")
            return
        conn.execute(
            """CREATE TABLE IF NOT EXISTS sales_lots_fill_log (
                   source_file TEXT, hip TEXT, name_key TEXT, field TEXT, value TEXT,
                   source TEXT, filled_at TIMESTAMPTZ NOT NULL DEFAULT now())"""
        )
        conn.execute(
            f"""INSERT INTO sales_lots_fill_log (source_file, hip, name_key, field, value, source)
                SELECT s.source_file, s.hip, s.name_key, 'damsire', f.damsire, %s
                FROM sales_lots s JOIN b_fill f ON f.k = {N('s.dam')} AND f.y = s.catalog_year::int
                WHERE coalesce(s.damsire, '') = '' AND s.catalog_year ~ '^[0-9]{{4}}$'""",
            (SOURCE,),
        )
        updated = conn.execute(
            f"""UPDATE sales_lots s
                SET damsire = f.damsire,
                    -- same as sales_history.clean_search_string: drop (...), lower, collapse spaces
                    damsire_key = trim(regexp_replace(lower(regexp_replace(f.damsire, '\\(.*?\\)', '', 'g')), '\\s+', ' ', 'g'))
                FROM b_fill f
                WHERE f.k = {N('s.dam')} AND f.y = s.catalog_year::int
                  AND coalesce(s.damsire, '') = '' AND s.catalog_year ~ '^[0-9]{{4}}$'"""
        ).rowcount
        total = conn.execute("SELECT count(*) FROM sales_lots").fetchone()[0]
        railway_db._bump(conn, "sales_lots", int(total))
        print(f"filled {updated:,} damsires (logged as source '{SOURCE}'); sales index rebuilds on next read")


if __name__ == "__main__":
    main()

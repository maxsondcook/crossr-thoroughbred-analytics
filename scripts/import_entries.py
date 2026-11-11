"""Load race entries from a CSV into race_entries (Phase 7 weekly entries).

The CSV comes from a source we are permitted to use (a licensed feed export, a
track's published overnight, or entries typed in); DRF and Equibase entry pages
are not scraped (bot protection). Columns, case-insensitive:

    race_date (YYYY-MM-DD), track_code, race_number, horse   required
    track, country, horse_refno, foal_year, race_type, race_name, distance,
    surface, post_position, status (entered/scratched), source_id    optional

Writes go through crossr_core.entries (shared with the NYRA adapter): each horse
is linked by Equibase refno, else by name when exactly one non-merged horse has
that name (and foal year, if given). Unmatched horses
keep their entry (horse_id empty) and are re-matched on every import, so a
missing pedigree never holds up the weekly list. Re-importing the same race
updates it in place (key: race date, track, race number, horse).

Usage:
    python3 scripts/import_entries.py entries.csv --source "my-feed"           # dry run
    python3 scripts/import_entries.py entries.csv --source "my-feed" --apply
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crossr_core import entries, railway_db, weekly  # noqa: E402

REQUIRED = ("race_date", "track_code", "race_number", "horse")
OPTIONAL = ("track", "country", "horse_refno", "foal_year", "race_type", "race_name", "distance", "surface",
            "post_position", "status", "source_id")


def read_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        heads = {h.strip().lower(): h for h in reader.fieldnames or []}
        missing = [c for c in REQUIRED if c not in heads]
        if missing:
            raise SystemExit(f"missing column(s): {', '.join(missing)}")
        out = []
        for raw in reader:
            row = {c: (raw.get(heads[c]) or "").strip() for c in REQUIRED + OPTIONAL if c in heads}
            if all(row.get(c) for c in REQUIRED):
                row["horse_key"] = weekly.name_key(row["horse"])
                row["race_number"] = int(row["race_number"])
                row["status"] = (row.get("status") or "entered").lower()
                out.append(row)
        return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", type=Path)
    ap.add_argument("--source", required=True, help="where the entries came from (stored on each row)")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    rows = read_rows(args.csv)
    with railway_db.connect() as conn:
        counts = entries.upsert(conn, rows, args.source)
        unmatched = sorted({r["horse"] for r in rows if not entries.match(conn, entries.normalise(r))})
        print(f"{counts['rows']} entries, {counts['linked']} linked to a horse record, "
              f"{counts['rows'] - counts['linked']} not yet; re-linked {counts['relinked']} earlier entries")
        for name in unmatched[:30]:
            print("  not in database yet:", name)
        if not args.apply:
            conn.rollback()
            print("dry run: nothing written (use --apply)")
            return
        conn.commit()
        print(f"stored {counts['rows']} entries")


if __name__ == "__main__":
    main()

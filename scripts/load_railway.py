#!/usr/bin/env python3
"""Load Crossr's data files into Postgres (the database named by DATABASE_URL).

The app reads only from Postgres; this script is the one place the source
files are read. Each part is replaced in its own transaction, so it is safe to
re-run. Parts:

  the 8 tables   data/<table>.csv          (stud_fees, race_horses, ... sales_lots)
  pedigrees      pedigrees_database.py
  catalogs       "Upcoming Sales/*.csv" and "Analyzed Sales/analyzed_*.csv"
  models         Analyzed Sales/expected_market_fee_bands.json (fee bands) and
                 Analyzed Sales/sales_model.json.gz (sales model)
  hypofoals      hypofoals.py (saved hypothetical matings)
  app_state      placeholderdatabase/*.csv (accounts, clients, notes, ...)

    export DATABASE_URL="postgresql://..."     # Railway -> Postgres -> Connect (public URL)
    python scripts/load_railway.py                       # everything
    python scripts/load_railway.py --only catalogs       # just the sale catalogs
    python scripts/load_railway.py --only stud_fees sales_lots
    python scripts/load_railway.py --skip-pedigrees
    python scripts/load_railway.py --schema-only
    python scripts/load_railway.py --source ~/Desktop/Crossr   # read files from another checkout

Starred hips and notes (upcoming_lot_marks) are never touched.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EXTRA_PARTS = ("pedigrees", "catalogs", "models", "hypofoals", "app_state")


def _stream_csv(path: Path, columns: list[str]):
    """Yield rows one at a time so big tables (sales_lots, race_starts) never sit in RAM."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for raw in csv.DictReader(handle):
            yield {col: (raw.get(col) or "") for col in columns}


def _read_catalog(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = [c for c in (reader.fieldnames or []) if c is not None]
        rows = [{k: v for k, v in raw.items() if k is not None} for raw in reader]
    return columns, rows


def load_catalogs(source: Path, railway_db) -> None:
    from crossr_core import upcoming

    found = []
    for folder, source_kind, pattern in (
        ("Upcoming Sales", "upcoming", "*.csv"),
        ("Analyzed Sales", "analyzed", "analyzed_*.csv"),  # loaded last: wins on a shared id
    ):
        for path in sorted((source / folder).glob(pattern)):
            if path.name.startswith("."):
                continue
            columns, rows = _read_catalog(path)
            railway_db.replace_upcoming_catalog(path.stem, source_kind, columns, rows, upcoming.count_hips(rows))
            found.append(path.stem)
            print(f"  catalog {path.stem:<52} {len(rows):>6,} rows  ({source_kind})")
    stored = {row["id"] for row in railway_db.list_upcoming_catalogs()}
    from crossr_core import hip_cache

    for stale in sorted(stored - set(found)):
        railway_db.delete_upcoming_catalog(stale)
        hip_cache.delete_catalog(stale)
        print(f"  catalog {stale:<52} removed (no longer in the folders)")


def load_models(source: Path, railway_db) -> None:
    bands = source / "Analyzed Sales" / "expected_market_fee_bands.json"
    if bands.exists():
        railway_db.save_model("fee_bands", json.loads(bands.read_text(encoding="utf-8")))
        print(f"  model   fee_bands      from {bands.name}")
    else:
        print(f"  model   fee_bands      skipped (no {bands})")
    sales = source / "Analyzed Sales" / "sales_model.json.gz"
    if sales.exists():
        with gzip.open(sales, "rt", encoding="utf-8") as fh:
            railway_db.save_model("sales_model", json.load(fh))
        print(f"  model   sales_model    from {sales.name}")
    else:
        print(f"  model   sales_model    skipped (no {sales})")


def load_hypofoals(source: Path, railway_db) -> None:
    path = source / "hypofoals.py"
    if not path.exists():
        print(f"  hypofoals          skipped (no {path})")
        return
    ns: dict = {}
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), {"__name__": "hypofoals"}, ns)
    items = ns.get("HYPO_FOALS") or {}
    count = railway_db.replace_pedigrees(items, table="hypofoals")
    print(f"  {'hypofoals':<18} {count:>9,} matings")


def load_app_state(source: Path, railway_db, sample_db) -> None:
    for table, columns in sample_db.APP_TABLES.items():
        path = source / "placeholderdatabase" / f"{table}.csv"
        rows = list(_stream_csv(path, columns)) if path.exists() else []
        railway_db.replace_table(table, columns, rows)
        print(f"  {table:<18} {len(rows):>9,} rows" + ("" if path.exists() else "  (no file: emptied)"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", nargs="*", help=f"load just these parts: table names or {', '.join(EXTRA_PARTS)}")
    parser.add_argument("--skip-pedigrees", action="store_true")
    parser.add_argument("--schema-only", action="store_true")
    parser.add_argument(
        "--precompute", action="store_true",
        help="after loading catalogs/models, precompute stale upcoming hip pages (scripts/precompute_catalogs.py)",
    )
    parser.add_argument("--source", default=str(ROOT), help="folder holding data/, the catalogs, hypofoals.py ...")
    args = parser.parse_args()
    source = Path(args.source).expanduser().resolve()

    from crossr_core import railway_db, sample_db

    if not railway_db.database_url():
        print("DATABASE_URL is not set. Copy it from Railway -> Postgres -> Connect, then:")
        print('  export DATABASE_URL="postgresql://..."')
        return 1

    railway_db.ensure_schema()
    print("Schema ready.")
    if args.schema_only:
        return 0

    wanted = set(args.only or [])
    unknown = wanted - set(sample_db.TABLES) - set(EXTRA_PARTS)
    if unknown:
        print(f"Unknown part(s): {', '.join(sorted(unknown))}")
        return 1

    def want(part):
        return not wanted or part in wanted

    for table, columns in sample_db.TABLES.items():
        if not want(table):
            continue
        path = source / "data" / f"{table}.csv"
        if not path.exists():
            print(f"  {table:<18} skipped (no {path})")
            continue
        started = time.time()
        count = railway_db.replace_table(table, columns, _stream_csv(path, columns))
        print(f"  {table:<18} {count:>9,} rows    ({time.time() - started:.1f}s)")

    if want("pedigrees") and not args.skip_pedigrees:
        path = source / "pedigrees_database.py"
        if path.exists():
            started = time.time()
            ns: dict = {}
            exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), {"__name__": "pedigrees_database"}, ns)
            pedigree_map = ns.get("pedigree_map") or {}
            del ns
            count = railway_db.replace_pedigrees(pedigree_map)
            del pedigree_map
            print(f"  {'pedigrees':<18} {count:>9,} horses  ({time.time() - started:.1f}s)")
        else:
            print(f"  pedigrees          skipped (no {path})")
    if want("catalogs"):
        load_catalogs(source, railway_db)
    if want("models"):
        load_models(source, railway_db)
    if want("hypofoals"):
        load_hypofoals(source, railway_db)
    if want("app_state"):
        load_app_state(source, railway_db, sample_db)
    if args.precompute:
        import subprocess

        print("\nPrecomputing upcoming hip pages (stale hips only)...")
        subprocess.run([sys.executable, str(ROOT / "scripts" / "precompute_catalogs.py")], check=False)

    print("\nRow counts:")
    for name, count in railway_db.row_counts().items():
        print(f"  {name:<22} {count:>9,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

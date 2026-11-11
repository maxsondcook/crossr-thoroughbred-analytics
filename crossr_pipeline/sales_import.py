"""Load new or changed sales_data/*.csv into sales_lots, one file at a time.

Each CSV is hashed. A file whose hash matches crossr_pipeline_files is skipped.
A changed or new file has its rows (source_file = the file name) replaced in
one transaction, so the rest of sales_lots is untouched.

First run: a file with no recorded hash whose rows are already in sales_lots
(the initial Railway load) is adopted, i.e. its hash is recorded without a
reload. Pass ``force`` names to reload those files anyway.
"""

from __future__ import annotations

import re

import hashlib
from pathlib import Path

from crossr_pipeline import notify, state

REPO = Path(__file__).resolve().parents[1]
SALES_DIR = REPO / "sales_data"
JOB = "sales-import"


def sales_rows(path: Path) -> list[dict]:
    """sales_lots rows for one sale-catalog CSV."""
    import sales_history

    year_match = re.search(r"\d{4}", path.name)
    catalog_year = year_match.group(0) if year_match else ""
    title = sales_history.clean_title(path.name)
    rows = []
    for lot in sales_history.load_and_process_csv(path):
        rows.append({
            "catalog_year": catalog_year,
            "catalog_title": title,
            "source_file": path.name,
            "hip": lot.get("hip"),
            "name": lot.get("name"),
            "name_key": sales_history.clean_search_string(lot.get("name")),
            "sex": lot.get("sex"),
            "sex_letter": lot.get("sex_letter"),
            "sire": lot.get("sire"),
            "sire_key": sales_history.clean_search_string(lot.get("sire")),
            "dam": lot.get("dam"),
            "dam_key": sales_history.clean_search_string(lot.get("dam")),
            "damsire": lot.get("damsire"),
            "damsire_key": sales_history.clean_search_string(lot.get("damsire")),
            "color": lot.get("color"),
            "consignor": lot.get("consignor"),
            "purchaser": lot.get("purchaser"),
            "barn": lot.get("barn"),
            "foaled_location": lot.get("foaled_location"),
            "birth_year": lot.get("birth_year"),
            "price_raw": lot.get("price_raw"),
            "price_num": lot.get("price_num"),
            "is_rna": lot.get("is_rna"),
            "is_out": lot.get("is_out"),
            "is_fasig": lot.get("is_fasig"),
        })
    return rows




def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def replace_file_rows(conn, source_file: str, rows: list[dict]) -> None:
    from crossr_core.sample_db import TABLES

    columns = TABLES["sales_lots"]
    col_sql = ", ".join(f'"{c}"' for c in columns)
    with conn.transaction():
        conn.execute("DELETE FROM sales_lots WHERE source_file = %s", (source_file,))
        with conn.cursor() as cur:
            with cur.copy(f"COPY sales_lots ({col_sql}) FROM STDIN") as copy:
                for row in rows:
                    copy.write_row([
                        None if row.get(c) in ("", None) else str(row.get(c)) for c in columns
                    ])


def record(conn, name: str, digest: str, count: int | None) -> None:
    conn.execute(
        "INSERT INTO crossr_pipeline_files (path, sha256, row_count, loaded_at) VALUES (%s, %s, %s, now()) "
        "ON CONFLICT (path) DO UPDATE SET sha256 = EXCLUDED.sha256, row_count = EXCLUDED.row_count, loaded_at = now()",
        (name, digest, count),
    )


def sales_import(conn, sales_dir: Path = SALES_DIR, force: set[str] | None = None, dry_run: bool = False) -> dict:
    from crossr_pipeline import versions as pgdb

    force = force or set()
    state.ensure(conn)
    known = {row["path"]: row["sha256"] for row in conn.execute("SELECT path, sha256 FROM crossr_pipeline_files")}
    loaded = {
        row["source_file"]: row["n"]
        for row in conn.execute("SELECT source_file, COUNT(*) AS n FROM sales_lots GROUP BY source_file")
    }
    run = None if dry_run else state.Run(conn, JOB)
    changed = 0
    failures = []
    for path in sorted(sales_dir.glob("*.csv")):
        name = path.name
        digest = sha256(path)
        if name not in force and known.get(name) == digest:
            continue
        if name not in force and name not in known and loaded.get(name):
            print(f"adopt  {name} ({loaded[name]} rows already loaded)", flush=True)
            if run:
                record(conn, name, digest, loaded[name])
                run.bump("adopted")
            continue
        try:
            rows = sales_rows(path)
        except Exception as exc:
            print(f"FAILED {name}: {exc}", flush=True)
            failures.append(name)
            if run:
                run.bump("failed")
            continue
        print(f"load   {name}: {len(rows)} rows (was {loaded.get(name, 0)})", flush=True)
        if run:
            replace_file_rows(conn, name, rows)
            record(conn, name, digest, len(rows))
            run.bump("files loaded")
            run.bump("rows", len(rows))
        changed += 1
    if run:
        if changed:
            pgdb.flush_versions(conn, ("sales_lots",))
        status = "failed" if failures and not changed else ("partial" if failures else "ok")
        run.finish(status, ", ".join(failures) or None)
        if failures:
            notify.send("Sales import failed", ", ".join(failures)[:200])
        elif changed:
            notify.send("Sales import", f"Loaded {changed} sale file(s)")
    return {"changed": changed, "failed": failures}

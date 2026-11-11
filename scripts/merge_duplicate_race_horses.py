#!/usr/bin/env python3
"""Merge race_horses rows that hold the same Equibase horse (equibase_refno).

The catalog queue stored one horse under several keys (``CALL PAUL | FRIESAN
FIRE | AVANI``, a bare ``STAGE MAGIC``, the dated ``STAGE MAGIC (2007)``) and
each copy got its own race_seasons / race_starts, so stakes wins were counted
two to four times. Per refno this keeps one row:

* kept row: status ok, has a foal_year, pedigree_key ``NAME (YEAR)``, then the
  lowest id;
* its record (profile fields, seasons, starts) comes from the most recently
  fetched copy, moved onto the kept id when that copy is another row;
* every other copy keeps its pedigree_key (so the catalog does not queue it
  again) but loses its record: status ``merged``, detail ``merged into
  race_horses <kept id>`` (what equibase_scraper.run writes for new duplicates);
* crossr_pipeline_queue details ``race_horses <copy id>`` point at the kept id.

Refnos whose copies disagree on foal_year are reported and left alone.

    scripts/pipeline/railway_env.sh crossr_dev python scripts/merge_duplicate_race_horses.py           # dry run
    scripts/pipeline/railway_env.sh crossr_dev python scripts/merge_duplicate_race_horses.py --apply

--apply writes a backup of every touched row first (db-backups/) and does all
writes in one transaction. Push or discard the pipeline's local stage before
applying: dirty stage rows pushed afterwards would bring the copies back.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MERGED = "merged"
MERGED_PREFIX = "merged into race_horses "

PROFILE_FIELDS = (
    "foaled_in", "breed", "color", "sex", "foaled", "sire", "sire_refno", "dam",
    "dam_refno", "damsire", "damsire_refno", "jockey", "trainer", "owner",
    "breeder", "best_class", "equibase_refno", "registry", "profile_url",
)
CHILD_TABLES = ("race_seasons", "race_starts")
BACKUP_DIR = Path(os.environ.get("CROSSR_BACKUPS", Path.home() / "Desktop" / "Crossr" / "db-backups"))

_GROUPS_SQL = """
SELECT h.id, h.pedigree_key, h.name_key, h.foal_year, h.display_name, h.status,
       h.fetched_at, h.equibase_refno, h.queue_batch,
       (SELECT count(*) FROM race_seasons s WHERE s.horse_id = h.id) AS seasons,
       (SELECT count(*) FROM race_starts s WHERE s.horse_id = h.id) AS starts
FROM race_horses h
WHERE h.equibase_refno IN (
    SELECT equibase_refno FROM race_horses
    WHERE COALESCE(equibase_refno, '') <> ''
    GROUP BY 1 HAVING count(*) > 1
)
ORDER BY h.equibase_refno, h.id::bigint
"""


def _dated_key(row: dict) -> bool:
    year = (row.get("foal_year") or "").strip()
    name = (row.get("name_key") or "").strip()
    return bool(year and name) and (row.get("pedigree_key") or "").strip() == f"{name} ({year})"


def _keep_rank(row: dict) -> tuple:
    """Higher is better; ties go to the lowest id."""
    return (
        row.get("status") == "ok",
        bool((row.get("foal_year") or "").strip()),
        _dated_key(row),
        not re.search(r"[|\[]", row.get("pedigree_key") or ""),  # not a catalog / sale-parent variant
        -int(row["id"]),
    )


def _source_rank(row: dict) -> tuple:
    return (
        row.get("status") == "ok",
        row.get("fetched_at") or "",
        int(row.get("starts") or 0) + int(row.get("seasons") or 0),
        -int(row["id"]),
    )


def plan_merges(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """rows from _GROUPS_SQL -> (merges, conflicts). One merge per refno:
    {refno, keep, source, drop: [rows]}; conflicts are refnos with 2+ foal years."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[str(row["equibase_refno"])].append(row)
    merges, conflicts = [], []
    for refno, group in sorted(groups.items()):
        if len(group) < 2:
            continue
        years = {(r.get("foal_year") or "").strip() for r in group} - {""}
        if len(years) > 1:
            conflicts.append({"refno": refno, "rows": group})
            continue
        keep = max(group, key=_keep_rank)
        source = max(group, key=_source_rank)
        merges.append({
            "refno": refno,
            "keep": keep,
            "source": source,
            "drop": [r for r in group if r["id"] != keep["id"]],
        })
    return merges, conflicts


def summarize(merges: list[dict]) -> dict:
    out = {"refnos": len(merges), "rows merged away": 0, "records moved": 0,
           "race_starts deleted": 0, "race_seasons deleted": 0}
    for m in merges:
        out["rows merged away"] += len(m["drop"])
        moved = m["source"]["id"] != m["keep"]["id"]
        out["records moved"] += moved
        # Rows whose children are deleted: every copy but the source, which ends up on the kept id.
        for row in [m["keep"], *m["drop"]]:
            if row["id"] == m["source"]["id"]:
                continue
            out["race_starts deleted"] += int(row.get("starts") or 0)
            out["race_seasons deleted"] += int(row.get("seasons") or 0)
    return out


def _label(row: dict) -> str:
    return f"{row['id']} {row['pedigree_key']!r} ({row.get('status')}, {row.get('starts')} starts)"


def write_report(path: Path, merges: list[dict], conflicts: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["refno", "action", "id", "pedigree_key", "foal_year", "status", "fetched_at",
                         "seasons", "starts", "kept_id"])
        for m in merges:
            for row in [m["keep"], *m["drop"]]:
                if row["id"] == m["keep"]["id"]:
                    action = "keep" + (f" (record from {m['source']['id']})" if m["source"]["id"] != row["id"] else "")
                else:
                    action = "merge"
                writer.writerow([m["refno"], action, row["id"], row["pedigree_key"], row.get("foal_year"),
                                 row.get("status"), row.get("fetched_at"), row.get("seasons"), row.get("starts"),
                                 m["keep"]["id"]])
        for c in conflicts:
            for row in c["rows"]:
                writer.writerow([c["refno"], "conflict: several foal years", row["id"], row["pedigree_key"],
                                 row.get("foal_year"), row.get("status"), row.get("fetched_at"),
                                 row.get("seasons"), row.get("starts"), ""])


def _json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def backup(conn, merges: list[dict], dbname: str) -> Path:
    ids = sorted({r["id"] for m in merges for r in [m["keep"], *m["drop"]]})
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    path = BACKUP_DIR / f"race_horses-merge-{dbname}-{datetime.now():%Y-%m-%dT%H%M%S}.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for table in ("race_horses", *CHILD_TABLES):
            col = "id" if table == "race_horses" else "horse_id"
            for row in conn.execute(f"SELECT * FROM {table} WHERE {col} = ANY(%s)", (ids,)):
                handle.write(json.dumps({"table": table, "row": row}, default=_json_default) + "\n")
        details = [f"race_horses {i}" for i in ids]
        for row in conn.execute("SELECT * FROM crossr_pipeline_queue WHERE detail = ANY(%s)", (details,)):
            handle.write(json.dumps({"table": "crossr_pipeline_queue", "row": row}, default=_json_default) + "\n")
    return path


def apply(conn, merges: list[dict]) -> dict:
    """All writes in one transaction, set-based through a TEMP table."""
    pairs = [(r["id"], m["keep"]["id"], m["source"]["id"]) for m in merges for r in m["drop"]]
    moves = [(m["keep"]["id"], m["source"]["id"]) for m in merges if m["source"]["id"] != m["keep"]["id"]]
    out = {}
    with conn.transaction():
        conn.execute("CREATE TEMP TABLE _merge (dup_id TEXT PRIMARY KEY, keep_id TEXT, source_id TEXT) ON COMMIT DROP")
        conn.execute("CREATE TEMP TABLE _move (keep_id TEXT PRIMARY KEY, source_id TEXT) ON COMMIT DROP")
        with conn.cursor() as cur:
            with cur.copy("COPY _merge FROM STDIN") as copy:
                for row in pairs:
                    copy.write_row(row)
            with cur.copy("COPY _move FROM STDIN") as copy:
                for row in moves:
                    copy.write_row(row)
        # The freshest record moves onto the kept id.
        sets = ", ".join(f"{f} = s.{f}" for f in PROFILE_FIELDS)
        out["profiles copied"] = conn.execute(
            f"UPDATE race_horses k SET {sets}, fetched_at = s.fetched_at, status = s.status, "
            "detail = s.detail, foal_year = COALESCE(k.foal_year, s.foal_year) "
            "FROM _move m JOIN race_horses s ON s.id = m.source_id WHERE k.id = m.keep_id"
        ).rowcount
        for table in CHILD_TABLES:
            conn.execute(f"DELETE FROM {table} t USING _move m WHERE t.horse_id = m.keep_id")
            out[f"{table} moved"] = conn.execute(
                f"UPDATE {table} t SET horse_id = m.keep_id FROM _move m WHERE t.horse_id = m.source_id"
            ).rowcount
            out[f"{table} deleted"] = conn.execute(
                f"DELETE FROM {table} t USING _merge d WHERE t.horse_id = d.dup_id"
            ).rowcount
        nulls = ", ".join(f"{f} = NULL" for f in PROFILE_FIELDS)
        out["rows merged"] = conn.execute(
            f"UPDATE race_horses h SET {nulls}, status = %s, detail = %s || d.keep_id "
            "FROM _merge d WHERE h.id = d.dup_id",
            (MERGED, MERGED_PREFIX),
        ).rowcount
        out["queue details repointed"] = conn.execute(
            "UPDATE crossr_pipeline_queue q SET detail = 'race_horses ' || d.keep_id "
            "FROM _merge d WHERE q.detail = 'race_horses ' || d.dup_id"
        ).rowcount
        for table in ("race_horses", *CHILD_TABLES):
            conn.execute(
                "INSERT INTO crossr_table_versions (table_name, version, row_count, updated_at) "
                f"SELECT %s, %s, count(*), now() FROM {table} ON CONFLICT (table_name) DO UPDATE SET "
                "version = EXCLUDED.version, row_count = EXCLUDED.row_count, updated_at = now()",
                (table, time.time()),
            )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="write the merge (default: dry run)")
    ap.add_argument("--show", type=int, default=12, help="example merges to print")
    ap.add_argument("--report", type=Path, help="CSV of every row in the plan")
    args = ap.parse_args(argv)

    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        print("DATABASE_URL is not set (use scripts/pipeline/railway_env.sh <db> ...)", file=sys.stderr)
        return 2
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(url, row_factory=dict_row, autocommit=True, connect_timeout=20) as conn:
        dbname = conn.execute("SELECT current_database() AS db").fetchone()["db"]
        rows = [dict(r) for r in conn.execute(_GROUPS_SQL).fetchall()]
        merges, conflicts = plan_merges(rows)
        merged_ids = {r["id"] for m in merges for r in m["drop"]}
        queue_hits = conn.execute(
            "SELECT count(*) AS n FROM crossr_pipeline_queue WHERE detail = ANY(%s)",
            ([f"race_horses {i}" for i in sorted(merged_ids)],),
        ).fetchone()["n"]
        print(f"database {dbname}: {len(rows)} rows share {len(merges) + len(conflicts)} refnos")
        for key, value in summarize(merges).items():
            print(f"  {key}: {value}")
        print(f"  crossr_pipeline_queue details repointed to the kept id: {queue_hits}")
        print(f"  conflicts (several foal years, left alone): {len(conflicts)}")
        for m in merges[: args.show]:
            moved = f", record from {m['source']['id']}" if m["source"]["id"] != m["keep"]["id"] else ""
            print(f"\n  refno {m['refno']}: keep {_label(m['keep'])}{moved}")
            for row in m["drop"]:
                print(f"      merge {_label(row)}")
        for c in conflicts[: args.show]:
            print(f"\n  CONFLICT refno {c['refno']}: " + "; ".join(_label(r) for r in c["rows"]))
        if args.report:
            write_report(args.report, merges, conflicts)
            print(f"\nreport: {args.report}")
        if not args.apply:
            print("\nDry run; pass --apply to write.")
            return 0
        if not merged_ids:
            print("Nothing to merge.")
            return 0
        path = backup(conn, merges, dbname)
        print(f"\nbackup: {path}")
        for key, value in apply(conn, merges).items():
            print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Load The Jockey Club breeding reports in live_foal_statistics/ into breeding_seasons.

Files: "Live Foal Statistics --- YYYY Breeding Season _ ...csv" or
"Report of Mares Bred --- YYYY Breeding Season.csv". When a year has both, the
Live Foal Statistics file wins (it adds reports received and live foals).

Like sales-import: each file is hashed into crossr_pipeline_files (path
prefixed "live_foal_statistics/"); a new or changed file replaces that year's
rows in one transaction; a year already loaded before hashes were kept is
adopted without a reload.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from crossr_pipeline import state
from crossr_pipeline.sales_import import record, sha256

REPO = Path(__file__).resolve().parents[1]
FOLDER = REPO / "live_foal_statistics"
PREFIX = "live_foal_statistics/"
JOB = "breeding-import"
YEAR_RE = re.compile(r"(\d{4}) Breeding Season")
SOUTHERN_RE = re.compile(r"\s*\(\d+\)\s*$")


def stallion_name(raw: str) -> str:
    """'Gun Runner (6)' -> 'Gun Runner'. The (n) counts Southern Hemisphere mares;
    a country suffix such as '(IRE)' is kept."""
    return SOUTHERN_RE.sub("", (raw or "").strip())


def files_by_year(folder: Path = FOLDER) -> dict[str, Path]:
    chosen: dict[str, Path] = {}
    for path in sorted(folder.glob("*.csv")):
        match = YEAR_RE.search(path.name)
        if not match:
            continue
        year = match.group(1)
        if year not in chosen or path.name.startswith("Live Foal"):
            chosen[year] = path
    return chosen


def rows_for(path: Path, year: str) -> list[dict]:
    out: dict[str, dict] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for record_ in csv.DictReader(handle):
            name = stallion_name(record_.get("Stallion") or "")
            if not name:
                continue
            out[name.upper()] = {
                "breeding_year": year,
                "stallion": name,
                "mares_bred": (record_.get("Mares Bred") or "").strip() or None,
                "reports_received": (record_.get("Reports Received") or "").strip() or None,
                "live_foals": (record_.get("Live Foals") or "").strip() or None,
                "live_foal_pct": (record_.get("Live Foal %") or "").strip() or None,
                "state": (record_.get("State") or "").strip() or None,
                "source": path.name,
            }
    return list(out.values())


def breeding_import(conn, folder: Path = FOLDER, dry_run: bool = False) -> dict:
    from crossr_pipeline import versions as pgdb

    state.ensure(conn)
    known = {
        row["path"]: row["sha256"]
        for row in conn.execute("SELECT path, sha256 FROM crossr_pipeline_files WHERE path LIKE %s", (PREFIX + "%",))
    }
    loaded = {
        row["breeding_year"]: (row["n"], row["source"])
        for row in conn.execute(
            "SELECT breeding_year, COUNT(*) AS n, MAX(source) AS source FROM breeding_seasons GROUP BY 1"
        )
    }
    run = None if dry_run else state.Run(conn, JOB)
    changed = []
    for year, path in sorted(files_by_year(folder).items()):
        key = PREFIX + path.name
        digest = sha256(path)
        if known.get(key) == digest:
            continue
        have = loaded.get(year)
        if key not in known and have and have[1] == path.name:
            if run:
                record(conn, key, digest, have[0])
                run.bump("adopted")
            continue
        rows = rows_for(path, year)
        print(f"load {year}: {len(rows)} stallions from {path.name} (was {have[0] if have else 0})", flush=True)
        changed.append(year)
        if not run:
            continue
        with conn.transaction():
            conn.execute("DELETE FROM breeding_seasons WHERE breeding_year = %s", (year,))
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO breeding_seasons (breeding_year, stallion, mares_bred, reports_received, "
                    "live_foals, live_foal_pct, state, source) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    [tuple(r.values()) for r in rows],
                )
        record(conn, key, digest, len(rows))
        run.bump("years loaded")
        run.bump("rows", len(rows))
    if run:
        if changed:
            pgdb.flush_versions(conn, ("breeding_seasons",))
        run.finish("ok", ", ".join(changed) or None)
    return {"years": changed}

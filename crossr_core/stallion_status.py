"""Stallion status corrections and the registry's geographic scope.

Stud cards rarely say a stallion has died or been pensioned (Wootton Bassett's
card still lists a fee), so known status changes are recorded in
``stallion_status_overrides`` and win over the card:

    python3 scripts/stallion_status.py set "Wootton Bassett" deceased --note "died 2026" --source user
    python3 scripts/stallion_status.py list

The stallion registry covers North America by default. Set the environment
variable CROSSR_STALLION_SCOPE=world (on the Railway api service) to list every
stallion on a current card worldwide; nothing else changes.
"""

from __future__ import annotations

import os
import re
import threading

TABLE = "stallion_status_overrides"
OFF_DUTY = {"deceased", "pensioned", "retired", "exported"}
SCOPES = ("north_america", "world")

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    name_key TEXT NOT NULL,
    foal_year INTEGER,
    status TEXT NOT NULL,
    note TEXT,
    source TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (name_key)
)
"""

SUGGESTIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS stallion_status_suggestions (
    name_key TEXT NOT NULL,
    foal_year INTEGER,
    status TEXT NOT NULL,
    headline TEXT NOT NULL,
    url TEXT NOT NULL,
    published TEXT,
    found_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    decision TEXT,
    PRIMARY KEY (name_key, url)
)
"""

_lock = threading.Lock()
_cache: tuple = (object(), {})  # (table version, rows); the sentinel never matches


def name_key(name: str) -> str:
    s = re.sub(r"\s*\([^)]*\)\s*$", "", str(name or "")).upper()
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]", "", s)).strip()


def scope() -> str:
    value = os.environ.get("CROSSR_STALLION_SCOPE", "north_america").strip().lower()
    return value if value in SCOPES else "north_america"


def overrides() -> dict:
    """name key -> {"status", "foal_year", "note"}; reloaded when the table changes."""
    global _cache
    from crossr_core import sample_db

    try:
        version = sample_db.table_version(TABLE)
    except Exception:  # no database (unit tests, offline tools): no corrections
        return _cache[1]
    if _cache[0] == version:
        return _cache[1]
    rows = {}
    try:
        from crossr_core import railway_db

        with railway_db.connect() as conn:
            if conn.execute("SELECT to_regclass(%s)", (TABLE,)).fetchone()[0]:
                for key, year, status, note in conn.execute(
                        f"SELECT name_key, foal_year, status, note FROM {TABLE}").fetchall():
                    rows[key] = {"status": status, "foal_year": year, "note": note}
    except Exception:
        return _cache[1]
    with _lock:
        _cache = (version, rows)
    return rows


def override_for(name: str, foal_year=None, table=None) -> dict | None:
    """The recorded status change for this stallion (same foaling year when both are known)."""
    hit = (table if table is not None else overrides()).get(name_key(name))
    if not hit:
        return None
    if hit.get("foal_year") and foal_year and int(foal_year) != int(hit["foal_year"]):
        return None  # a namesake
    return hit


def off_duty(name: str, foal_year=None, table=None) -> bool:
    hit = override_for(name, foal_year, table)
    return bool(hit and hit["status"] in OFF_DUTY)

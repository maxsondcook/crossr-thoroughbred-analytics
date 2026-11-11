"""
Precomputed upcoming-sale hip pages, keyed by (catalog, hip) in Postgres.

Opening a hip is one read. A catalog never waits on this: a hip with no stored
page is computed live, stored, and returned; a stored page whose inputs changed
(catalog, models or source tables) is returned at once and refreshed in the
background.

    python scripts/precompute_catalogs.py                 # every upcoming catalog, stale hips only
    python scripts/precompute_catalogs.py --full "Keeneland November Breeding Stock Sale (2026)"
    python scripts/precompute_catalogs.py --rescraped     # hips whose pedigree scrape finished since

Per-hip scrape status comes from the data pipeline's tables (STATUS_TABLE,
CATALOG_STATUS_TABLE) when they exist; it is read live, never baked into the
stored page, so "pedigree still updating" notes clear as soon as a hip is done.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Iterable, List, Optional

# Bump when the hip payload's shape or the code computing it changes.
CACHE_VERSION = "4"  # 4: pedigree gaps filled from ancestors' own records

TABLE = "upcoming_hip_cache"
# Owned by the data pipeline: per-hip scrape state and the "catalog complete" signal.
STATUS_TABLE = "upcoming_hip_status"  # (catalog_id, hip, status, updated_at)
CATALOG_STATUS_TABLE = "upcoming_catalog_status"  # (catalog_id, complete, updated_at)

# Every table whose change can alter a hip page.
INPUT_TABLES = (
    "upcoming_catalogs",
    "sales_lots",
    "stud_fees",
    "breeding_seasons",
    "race_horses",
    "race_seasons",
    "race_starts",
    "graded_stakes",
    "stallion_rankings",
    "pedigrees",
    "model:sales_model",
    "model:fee_bands",
)

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    catalog_id  text NOT NULL,
    hip         text NOT NULL,
    payload     jsonb NOT NULL,
    inputs      text NOT NULL,
    computed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (catalog_id, hip)
)
"""

_schema_ready = False
_lock = threading.Lock()
_refreshing: set = set()
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hip-cache")


def _db():
    from crossr_core import railway_db

    return railway_db


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with _lock:
        if not _schema_ready:
            with _db().connect() as conn:
                conn.execute(_SCHEMA)
            _schema_ready = True


def _fingerprint(versions: Dict[str, float]) -> str:
    parts = [CACHE_VERSION] + [f"{t}={float(versions.get(t) or 0.0)}" for t in INPUT_TABLES]
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def _versions(conn) -> Dict[str, float]:
    return {
        name: version
        for name, version in conn.execute(
            "SELECT table_name, version FROM crossr_table_versions WHERE table_name = ANY(%s)",
            (list(INPUT_TABLES),),
        )
    }


def inputs_fingerprint() -> str:
    """Hash of the cache version and every input table's version."""
    with _db().connect() as conn:
        return _fingerprint(_versions(conn))


# ------------------------------------------------------------------ status
def _exists(conn, table: str) -> bool:
    return conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0] is not None


# ------------------------------------------------------------------ store
def read(catalog_id: str, hip: str) -> Optional[Dict[str, Any]]:
    ensure_schema()
    with _db().connect() as conn:
        rec = conn.execute(
            f"SELECT payload, inputs, computed_at FROM {TABLE} WHERE catalog_id = %s AND hip = %s",
            (catalog_id, str(hip)),
        ).fetchone()
    if not rec:
        return None
    return {"payload": rec[0], "inputs": rec[1], "computed_at": rec[2]}


def write(catalog_id: str, items: Iterable[tuple], inputs: str) -> int:
    """items: (hip, payload) pairs."""
    ensure_schema()
    from psycopg.types.json import Jsonb  # type: ignore

    rows = [(catalog_id, str(hip), Jsonb(payload), inputs) for hip, payload in items]
    if not rows:
        return 0
    with _db().connect() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                f"INSERT INTO {TABLE} (catalog_id, hip, payload, inputs, computed_at) "
                "VALUES (%s, %s, %s, %s, now()) ON CONFLICT (catalog_id, hip) DO UPDATE SET "
                "payload = EXCLUDED.payload, inputs = EXCLUDED.inputs, computed_at = now()",
                rows,
            )
    return len(rows)


def delete_catalog(catalog_id: str) -> None:
    ensure_schema()
    with _db().connect() as conn:
        conn.execute(f"DELETE FROM {TABLE} WHERE catalog_id = %s", (catalog_id,))


def _jsonable(payload: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads(json.dumps(payload, default=str))


# ------------------------------------------------------------------ read path
def _overlay(conn, catalog_id: str, hip: str, payload: Dict[str, Any]) -> None:
    """Live fields never baked into a stored page: stars / notes and scrape status."""
    lot = payload.get("lot") or {}
    rec = conn.execute(
        "SELECT starred, notes FROM upcoming_lot_marks WHERE catalog_id = %s AND hip = %s",
        (catalog_id, str(lot.get("hip") or hip)),
    ).fetchone()
    if rec:
        if rec[0] is not None:
            lot["starred"] = bool(rec[0])
        if rec[1] is not None:
            lot["notes"] = rec[1]
    status: Dict[str, Any] = {"status": None, "catalog_complete": None, "updated_at": None}
    if _exists(conn, STATUS_TABLE):
        srec = conn.execute(
            f"SELECT status, updated_at FROM {STATUS_TABLE} WHERE catalog_id = %s AND hip = %s",
            (catalog_id, str(lot.get("hip") or hip)),
        ).fetchone()
        if srec:
            status["status"] = srec[0]
            status["updated_at"] = srec[1].isoformat() if srec[1] else None
    if _exists(conn, CATALOG_STATUS_TABLE):
        crec = conn.execute(
            f"SELECT complete FROM {CATALOG_STATUS_TABLE} WHERE catalog_id = %s", (catalog_id,)
        ).fetchone()
        if crec:
            status["catalog_complete"] = bool(crec[0])
    payload["data_status"] = status


def get(
    catalog_id: str,
    hip: str,
    compute: Callable[[str, str], Optional[Dict[str, Any]]],
) -> Optional[Dict[str, Any]]:
    """Stored hip page (refreshed in the background when stale), else computed live and stored.

    One connection: stored page, input versions, stars / notes and scrape status.
    """
    ensure_schema()
    with _db().connect() as conn:
        rec = conn.execute(
            f"SELECT payload, inputs, computed_at FROM {TABLE} WHERE catalog_id = %s AND hip = %s",
            (catalog_id, str(hip)),
        ).fetchone()
        current = _fingerprint(_versions(conn))
        if rec is not None:
            payload = dict(rec[0])
            stale = rec[1] != current
            payload["cache"] = {"computed_at": rec[2].isoformat(), "stale": stale}
            _overlay(conn, catalog_id, hip, payload)
            if stale:
                _refresh_later(catalog_id, hip, compute)
            return payload
    payload = compute(catalog_id, hip)
    if payload is None:
        return None
    payload = _jsonable(payload)
    try:
        write(catalog_id, [(hip, payload)], current)
    except Exception:
        pass
    payload["cache"] = {"computed_at": None, "stale": False}
    with _db().connect() as conn:
        _overlay(conn, catalog_id, hip, payload)
    return payload


def _refresh_later(catalog_id: str, hip: str, compute) -> None:
    key = (catalog_id, str(hip))
    with _lock:
        if key in _refreshing:
            return
        _refreshing.add(key)

    def run():
        try:
            payload = compute(catalog_id, hip)
            if payload is not None:
                write(catalog_id, [(hip, _jsonable(payload))], inputs_fingerprint())
        except Exception:
            pass
        finally:
            with _lock:
                _refreshing.discard(key)

    _pool.submit(run)


# ------------------------------------------------------------------ batch build
def stale_hips(catalog_id: str, hips: List[str], current: str) -> List[str]:
    ensure_schema()
    with _db().connect() as conn:
        fresh = {
            hip
            for (hip,) in conn.execute(
                f"SELECT hip FROM {TABLE} WHERE catalog_id = %s AND inputs = %s", (catalog_id, current)
            )
        }
    return [h for h in hips if h not in fresh]


def rescraped_hips(catalog_id: str) -> List[str]:
    """Hips whose scrape status changed after their page was stored (or never stored)."""
    ensure_schema()
    with _db().connect() as conn:
        if not _exists(conn, STATUS_TABLE):
            return []
        return [
            hip
            for (hip,) in conn.execute(
                f"SELECT s.hip FROM {STATUS_TABLE} s LEFT JOIN {TABLE} c "
                "ON c.catalog_id = s.catalog_id AND c.hip = s.hip "
                "WHERE s.catalog_id = %s AND (c.computed_at IS NULL OR s.updated_at > c.computed_at)",
                (catalog_id,),
            )
        ]


def build(
    catalog_id: str,
    lots: List[Dict[str, Any]],
    compute_lot: Callable[[str, Dict[str, Any]], Dict[str, Any]],
    *,
    hips: Optional[List[str]] = None,
    batch: int = 50,
    progress: Optional[Callable[[int, int, float], None]] = None,
) -> Dict[str, Any]:
    """Compute and store pages for `hips` (default: every lot) of one catalog."""
    current = inputs_fingerprint()
    wanted = set(hips) if hips is not None else None
    todo = [lot for lot in lots if lot.get("hip") and (wanted is None or lot["hip"] in wanted)]
    started = time.time()
    pending: List[tuple] = []
    done = failed = 0
    for lot in todo:
        try:
            pending.append((lot["hip"], _jsonable(compute_lot(catalog_id, lot))))
        except Exception:
            failed += 1
        done += 1
        if len(pending) >= batch:
            write(catalog_id, pending, current)
            pending = []
        if progress and done % batch == 0:
            progress(done, len(todo), time.time() - started)
    write(catalog_id, pending, current)
    return {"catalog": catalog_id, "hips": len(todo), "failed": failed, "seconds": round(time.time() - started, 1)}

"""Postgres backend for Crossr - the only data source.

DATABASE_URL must be set (Railway injects it into any service that references
its Postgres database; locally, point it at the `crossr_dev` database or a copy).
Fill a fresh database with scripts/load_railway.py.

Tables
------
* The 8 runtime tables, one per former CSV, with the CSV headers as TEXT columns
  (see crossr_core.sample_db.TABLES) plus ``_rid``, a serial that keeps the
  original row order.
* The app-state tables (sample_db.APP_TABLES: accounts, clients, notes, ...).
* pedigrees(key, tree JSONB) - the pedigree map.
* hypofoals(key, tree JSONB) - saved hypothetical matings.
* upcoming_catalogs / upcoming_catalog_rows - Upcoming and Analyzed sale catalogs
  (one JSONB object per catalog row, keyed by the catalog's CSV headers).
* upcoming_lot_marks - starred hips and notes on those catalogs.
* crossr_models(name, data BYTEA) - fitted models as gzipped JSON (fee bands, sales model).
* sales_catalogs / sales_lot_index - typed index over sales_lots for the sales
  hub, rebuilt from sales_lots whenever that table changes.
* crossr_table_versions - bumped on every write so the API reloads a table only
  when it changed.
* users / sessions / preferences - accounts and saved barns (crossr_core.auth).
"""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from typing import Iterable

_VERSION_TTL = float(os.environ.get("CROSSR_DB_VERSION_TTL", "15"))
_version_cache: dict[str, tuple[float, float]] = {}
_lock = threading.RLock()
_schema_ready = False


class DatabaseNotConfigured(RuntimeError):
    pass


def database_url() -> str:
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def enabled() -> bool:
    return bool(database_url())


def require() -> None:
    if not database_url():
        raise DatabaseNotConfigured(
            "DATABASE_URL is not set. Crossr reads all data from Postgres: point it at "
            "Railway's crossr_dev database (or a copy)."
        )


def _psycopg():
    try:
        import psycopg  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("psycopg is missing: pip install 'psycopg[binary]'") from exc
    return psycopg


@contextmanager
def connect(row_factory=None):
    require()
    psycopg = _psycopg()
    kwargs = {"connect_timeout": 10}
    if row_factory is not None:
        kwargs["row_factory"] = row_factory
    conn = psycopg.connect(database_url(), **kwargs)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _text_table_sql(table: str, columns: Iterable[str], keys: Iterable[str] = ()) -> list[str]:
    cols = ",\n".join(f"    {_q(c)} TEXT" for c in columns)
    parts = [
        f"CREATE TABLE IF NOT EXISTS {_q(table)} (\n{cols}\n);",
        f"ALTER TABLE {_q(table)} ADD COLUMN IF NOT EXISTS _rid BIGSERIAL;",
    ]
    keys = tuple(keys)
    if keys:
        idx_cols = ", ".join(_q(k) for k in keys)
        parts.append(f"CREATE INDEX IF NOT EXISTS {_q('idx_' + table + '_key')} ON {_q(table)} ({idx_cols});")
    return parts


def schema_sql() -> str:
    from crossr_core.sample_db import APP_TABLES, TABLES, UPSERT_KEYS

    parts = [
        "CREATE TABLE IF NOT EXISTS crossr_table_versions (\n"
        "    table_name TEXT PRIMARY KEY,\n"
        "    version DOUBLE PRECISION NOT NULL,\n"
        "    row_count BIGINT,\n"
        "    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()\n);",
    ]
    for table, columns in TABLES.items():
        parts.extend(_text_table_sql(table, columns, UPSERT_KEYS.get(table) or ()))
    for table, columns in APP_TABLES.items():
        parts.extend(_text_table_sql(table, columns))
    parts.append(
        "CREATE INDEX IF NOT EXISTS idx_stallion_rankings_upper ON stallion_rankings (upper(stallion));"
    )
    parts.append(
        "CREATE TABLE IF NOT EXISTS pedigrees (\n"
        "    key TEXT PRIMARY KEY,\n"
        "    tree JSONB NOT NULL,\n"
        "    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()\n);\n"
        "CREATE TABLE IF NOT EXISTS hypofoals (\n"
        "    key TEXT PRIMARY KEY,\n"
        "    tree JSONB NOT NULL,\n"
        "    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()\n);"
    )
    parts.append(
        "CREATE TABLE IF NOT EXISTS users (\n"
        "    id BIGSERIAL PRIMARY KEY,\n"
        "    email TEXT NOT NULL UNIQUE,\n"
        "    username TEXT NOT NULL UNIQUE,\n"
        "    password_hash TEXT NOT NULL,\n"
        "    created_at BIGINT NOT NULL\n);\n"
        "CREATE TABLE IF NOT EXISTS sessions (\n"
        "    token TEXT PRIMARY KEY,\n"
        "    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,\n"
        "    created_at BIGINT NOT NULL,\n"
        "    expires_at BIGINT NOT NULL\n);\n"
        "CREATE TABLE IF NOT EXISTS preferences (\n"
        "    user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,\n"
        "    payload TEXT NOT NULL,\n"
        "    updated_at BIGINT NOT NULL\n);"
    )
    parts.append(
        "CREATE TABLE IF NOT EXISTS upcoming_catalogs (\n"
        "    id TEXT PRIMARY KEY,\n"
        "    source TEXT NOT NULL,\n"
        "    columns JSONB NOT NULL,\n"
        "    hip_count INTEGER NOT NULL DEFAULT 0,\n"
        "    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()\n);\n"
        "CREATE TABLE IF NOT EXISTS upcoming_catalog_rows (\n"
        "    catalog_id TEXT NOT NULL REFERENCES upcoming_catalogs(id) ON DELETE CASCADE,\n"
        "    row_no INTEGER NOT NULL,\n"
        "    data JSONB NOT NULL,\n"
        "    PRIMARY KEY (catalog_id, row_no)\n);\n"
        "CREATE TABLE IF NOT EXISTS upcoming_lot_marks (\n"
        "    catalog_id TEXT NOT NULL,\n"
        "    hip TEXT NOT NULL,\n"
        "    starred BOOLEAN,\n"
        "    notes TEXT,\n"
        "    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),\n"
        "    PRIMARY KEY (catalog_id, hip)\n);"
    )
    parts.append(
        "CREATE TABLE IF NOT EXISTS crossr_models (\n"
        "    name TEXT PRIMARY KEY,\n"
        "    data BYTEA NOT NULL,\n"
        "    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()\n);"
    )
    parts.append(_SALES_INDEX_SCHEMA)
    return "\n\n".join(parts) + "\n"


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with _lock:
        if _schema_ready:
            return
        with connect() as conn:
            # Several API workers may start at once; serialize the DDL.
            conn.execute("SELECT pg_advisory_xact_lock(812345001)")
            conn.execute(_pending_schema_sql(conn))
        _schema_ready = True


def _pending_schema_sql(conn) -> str:
    """schema_sql() minus ALTER/CREATE INDEX statements that are already applied.

    Postgres locks the table for those even when the column or index exists, which
    would make API startup wait behind a long pipeline write.
    """
    indexes = {name for (name,) in conn.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")}
    rid_tables = {
        name for (name,) in conn.execute(
            "SELECT table_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND column_name = '_rid'"
        )
    }
    kept = []
    for stmt in schema_sql().split(";\n"):
        text = stmt.strip()
        if not text:
            continue
        head = text.split("(")[0]
        if head.startswith("CREATE INDEX IF NOT EXISTS "):
            name = head[len("CREATE INDEX IF NOT EXISTS "):].split(" ON ")[0].strip().strip('"')
            if name in indexes:
                continue
        if text.startswith("ALTER TABLE ") and "ADD COLUMN IF NOT EXISTS _rid" in text:
            table = text[len("ALTER TABLE "):].split(" ADD COLUMN")[0].strip().strip('"')
            if table in rid_tables:
                continue
        kept.append(text)
    return ";\n".join(kept) + (";\n" if kept else "")


# ---------------------------------------------------------------------------
# Row tables (the 8 runtime tables and the app-state tables)
# ---------------------------------------------------------------------------

def read_table(table: str, columns: Iterable[str]) -> list[dict]:
    ensure_schema()
    columns = list(columns)
    col_sql = ", ".join(_q(c) for c in columns)
    with connect() as conn:
        cur = conn.execute(f"SELECT {col_sql} FROM {_q(table)} ORDER BY _rid")
        return [{c: ("" if v is None else str(v)) for c, v in zip(columns, rec)} for rec in cur]


def iter_table(table: str, columns: Iterable[str], batch: int = 5000):
    """Stream rows as tuples of strings in load order (server-side cursor)."""
    ensure_schema()
    columns = list(columns)
    col_sql = ", ".join(_q(c) for c in columns)
    with connect() as conn:
        with conn.cursor(name=f"iter_{table}") as cur:
            cur.itersize = batch
            cur.execute(f"SELECT {col_sql} FROM {_q(table)} ORDER BY _rid")
            for rec in cur:
                yield tuple("" if v is None else str(v) for v in rec)


def table_version(table: str, max_age: float | None = None) -> float:
    """Version stamp for a table (0.0 when never loaded). Cached for a few seconds."""
    max_age = _VERSION_TTL if max_age is None else max_age
    now = time.monotonic()
    with _lock:
        hit = _version_cache.get(table)
        if hit and now - hit[0] < max_age:
            return hit[1]
    ensure_schema()
    with connect() as conn:
        rec = conn.execute(
            "SELECT version FROM crossr_table_versions WHERE table_name = %s", (table,)
        ).fetchone()
    version = float(rec[0]) if rec else 0.0
    with _lock:
        _version_cache[table] = (now, version)
    return version


def _bump(conn, table: str, row_count: int | None, version: float | None = None) -> float:
    version = time.time() if version is None else version
    conn.execute(
        "INSERT INTO crossr_table_versions (table_name, version, row_count, updated_at) "
        "VALUES (%s, %s, %s, now()) ON CONFLICT (table_name) DO UPDATE SET "
        "version = EXCLUDED.version, row_count = EXCLUDED.row_count, updated_at = now()",
        (table, version, row_count),
    )
    with _lock:
        _version_cache[table] = (time.monotonic(), version)
    return version


def replace_table(table: str, columns: Iterable[str], rows: Iterable[dict]) -> int:
    """Swap a whole table in one transaction (readers never see it half-written).

    ``rows`` may be a list or a generator (streamed straight into COPY).
    """
    ensure_schema()
    columns = list(columns)
    col_sql = ", ".join(_q(c) for c in columns)
    count = 0
    with connect() as conn:
        conn.execute(f"DELETE FROM {_q(table)}")
        with conn.cursor() as cur:
            with cur.copy(f"COPY {_q(table)} ({col_sql}) FROM STDIN") as copy:
                for row in rows:
                    copy.write_row([(None if row.get(c, "") in ("", None) else str(row.get(c))) for c in columns])
                    count += 1
        version = _bump(conn, table, count)
        if table == "sales_lots":
            _rebuild_sales_index(conn, version)
    return count


def row_counts() -> dict[str, int]:
    ensure_schema()
    with connect() as conn:
        return {
            name: int(count or 0)
            for name, count in conn.execute(
                "SELECT table_name, row_count FROM crossr_table_versions ORDER BY table_name"
            )
        }


# ---------------------------------------------------------------------------
# Pedigrees and saved hypo-foals ({key: tree} maps)
# ---------------------------------------------------------------------------

def read_pedigree_map(compact: bool = False, table: str = "pedigrees") -> dict:
    """All trees. ``compact`` returns a CompactPedigreeMap of JSON bytes, which
    streams rows as text so the full set is never parsed into dicts at once."""
    ensure_schema()
    with connect() as conn:
        if not compact:
            return {key: tree for key, tree in conn.execute(f"SELECT key, tree FROM {_q(table)}")}
        from crossr_core.pedigree_store import CompactPedigreeMap

        out = CompactPedigreeMap()
        with conn.cursor(name=f"{table}_stream") as cur:
            cur.itersize = 2000
            cur.execute(f"SELECT key, tree::text FROM {_q(table)}")
            for key, text in cur:
                out.set_raw(key, text.encode())
        return out


def upsert_pedigrees(items: dict, batch: int = 2000, table: str = "pedigrees") -> int:
    """Insert or replace trees by key (scrapers, saved hypo-foals)."""
    if not items:
        return 0
    ensure_schema()
    _psycopg()
    from psycopg.types.json import Jsonb  # type: ignore

    pairs = list(items.items())
    with connect() as conn:
        with conn.cursor() as cur:
            for i in range(0, len(pairs), batch):
                cur.executemany(
                    f"INSERT INTO {_q(table)} (key, tree, updated_at) VALUES (%s, %s, now()) "
                    "ON CONFLICT (key) DO UPDATE SET tree = EXCLUDED.tree, updated_at = now()",
                    [(str(k), Jsonb(v)) for k, v in pairs[i:i + batch]],
                )
        count = conn.execute(f"SELECT count(*) FROM {_q(table)}").fetchone()[0]
        _bump(conn, table, int(count))
    return len(pairs)


def replace_pedigrees(items: dict, table: str = "pedigrees") -> int:
    """Load a full {key: tree} map with COPY (initial load)."""
    ensure_schema()
    with connect() as conn:
        conn.execute(f"DELETE FROM {_q(table)}")
        with conn.cursor() as cur:
            with cur.copy(f"COPY {_q(table)} (key, tree) FROM STDIN") as copy:
                for key, tree in items.items():
                    copy.write_row([str(key), json.dumps(tree)])
        _bump(conn, table, len(items))
    return len(items)


# ---------------------------------------------------------------------------
# Upcoming / Analyzed sale catalogs
# ---------------------------------------------------------------------------

def list_upcoming_catalogs() -> list[dict]:
    ensure_schema()
    with connect() as conn:
        return [
            {"id": cid, "source": source, "hip_count": int(hips or 0)}
            for cid, source, hips in conn.execute(
                "SELECT id, source, hip_count FROM upcoming_catalogs ORDER BY id"
            )
        ]


def read_upcoming_catalog(catalog_id: str) -> tuple[list[str], list[dict]] | None:
    """(columns, rows) for one catalog id (case-sensitive, like the old file names)."""
    ensure_schema()
    with connect() as conn:
        rec = conn.execute("SELECT columns FROM upcoming_catalogs WHERE id = %s", (catalog_id,)).fetchone()
        if rec is None:
            return None
        rows = [
            data
            for (data,) in conn.execute(
                "SELECT data FROM upcoming_catalog_rows WHERE catalog_id = %s ORDER BY row_no",
                (catalog_id,),
            )
        ]
    return list(rec[0] or []), rows


def replace_upcoming_catalog(catalog_id: str, source: str, columns: list[str], rows: list[dict], hip_count: int) -> int:
    ensure_schema()
    from psycopg.types.json import Jsonb  # type: ignore

    with connect() as conn:
        conn.execute("DELETE FROM upcoming_catalogs WHERE id = %s", (catalog_id,))
        conn.execute(
            "INSERT INTO upcoming_catalogs (id, source, columns, hip_count) VALUES (%s, %s, %s, %s)",
            (catalog_id, source, Jsonb(list(columns)), hip_count),
        )
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO upcoming_catalog_rows (catalog_id, row_no, data) VALUES (%s, %s, %s)",
                [(catalog_id, i, Jsonb(row)) for i, row in enumerate(rows)],
            )
        _bump(conn, "upcoming_catalogs", None)
    return len(rows)


def delete_upcoming_catalog(catalog_id: str) -> None:
    ensure_schema()
    with connect() as conn:
        conn.execute("DELETE FROM upcoming_catalogs WHERE id = %s", (catalog_id,))
        _bump(conn, "upcoming_catalogs", None)


def read_lot_marks(catalog_id: str) -> dict[str, dict]:
    """hip -> {"starred": bool|None, "notes": str|None} for one catalog."""
    ensure_schema()
    with connect() as conn:
        return {
            hip: {"starred": starred, "notes": notes}
            for hip, starred, notes in conn.execute(
                "SELECT hip, starred, notes FROM upcoming_lot_marks WHERE catalog_id = %s",
                (catalog_id,),
            )
        }


def set_lot_mark(catalog_id: str, hip: str, starred: bool | None = None, notes: str | None = None) -> dict:
    """Upsert a mark; a None field keeps its stored value. Returns the stored mark."""
    ensure_schema()
    with connect() as conn:
        starred_out, notes_out = conn.execute(
            "INSERT INTO upcoming_lot_marks (catalog_id, hip, starred, notes, updated_at) "
            "VALUES (%s, %s, %s, %s, now()) ON CONFLICT (catalog_id, hip) DO UPDATE SET "
            "starred = COALESCE(EXCLUDED.starred, upcoming_lot_marks.starred), "
            "notes = COALESCE(EXCLUDED.notes, upcoming_lot_marks.notes), updated_at = now() "
            "RETURNING starred, notes",
            (catalog_id, hip, starred, notes),
        ).fetchone()
    return {"starred": starred_out, "notes": notes_out}


# ---------------------------------------------------------------------------
# Models (JSON blobs)
# ---------------------------------------------------------------------------

def read_model(name: str):
    """A stored model (any JSON value), or None. Stored as gzipped JSON."""
    import gzip

    ensure_schema()
    with connect() as conn:
        rec = conn.execute("SELECT data FROM crossr_models WHERE name = %s", (name,)).fetchone()
    return json.loads(gzip.decompress(bytes(rec[0]))) if rec else None


def save_model(name: str, payload) -> None:
    import gzip

    ensure_schema()
    data = gzip.compress(json.dumps(payload, separators=(",", ":")).encode())
    with connect() as conn:
        conn.execute(
            "INSERT INTO crossr_models (name, data, updated_at) VALUES (%s, %s, now()) "
            "ON CONFLICT (name) DO UPDATE SET data = EXCLUDED.data, updated_at = now()",
            (name, data),
        )
        _bump(conn, "model:" + name, None)


def model_version(name: str) -> float:
    return table_version("model:" + name)


# ---------------------------------------------------------------------------
# Sales hub index (typed copy of sales_lots, queried with SQL)
# ---------------------------------------------------------------------------

_SALES_INDEX_SCHEMA = """
CREATE TABLE IF NOT EXISTS sales_catalogs (
    id INTEGER PRIMARY KEY,
    year INTEGER NOT NULL,
    title TEXT NOT NULL,
    month_index INTEGER,
    source_file TEXT
);
CREATE TABLE IF NOT EXISTS sales_lot_index (
    id INTEGER PRIMARY KEY,
    catalog_id INTEGER NOT NULL,
    hip TEXT, name TEXT, name_key TEXT, sex TEXT, sex_letter TEXT,
    sire TEXT, sire_key TEXT, dam TEXT, dam_key TEXT, damsire TEXT, damsire_key TEXT,
    color TEXT, consignor TEXT, purchaser TEXT, barn TEXT, foaled_location TEXT,
    birth_year TEXT, price_raw TEXT, price_num DOUBLE PRECISION,
    is_rna INTEGER, is_out INTEGER, is_fasig INTEGER,
    -- sample_db.name_key of the raw name / sire / dam, for auction-history matching
    nk TEXT, sk TEXT, dk TEXT
);
CREATE INDEX IF NOT EXISTS idx_sli_sire ON sales_lot_index (sire_key);
CREATE INDEX IF NOT EXISTS idx_sli_name ON sales_lot_index (name_key);
CREATE INDEX IF NOT EXISTS idx_sli_dam ON sales_lot_index (dam_key);
CREATE INDEX IF NOT EXISTS idx_sli_damsire ON sales_lot_index (damsire_key);
CREATE INDEX IF NOT EXISTS idx_sli_catalog ON sales_lot_index (catalog_id);
CREATE INDEX IF NOT EXISTS idx_sli_nk ON sales_lot_index (nk);
CREATE INDEX IF NOT EXISTS idx_sli_skdk ON sales_lot_index (sk, dk);
"""

# sample_db.name_key in SQL: drop (...) groups and . ' , ’ then squeeze spaces, upper-case.
_NAME_KEY_SQL = (
    "upper(btrim(regexp_replace(regexp_replace(regexp_replace(coalesce({c}, ''), "
    "'\\(.*?\\)', '', 'g'), '[.'',’]', '', 'g'), '\\s+', ' ', 'g')))"
)


def _key_sql(col: str) -> str:
    return f"COALESCE(NULLIF(s.{col}_key, ''), {_NAME_KEY_SQL.format(c='s.' + col)})"


def _truthy_sql(col: str) -> str:
    return f"(CASE WHEN lower(btrim(coalesce(s.{col}, ''))) IN ('1','true','yes','y','t') THEN 1 ELSE 0 END)"


def _rebuild_sales_index(conn, version: float) -> None:
    """Rebuild sales_catalogs / sales_lot_index from sales_lots inside ``conn``'s transaction.

    Ids follow load order, matching the old in-memory SQLite index: lots are
    numbered by row, catalogs by first appearance.
    """
    import sales_history

    conn.execute("DELETE FROM sales_lot_index")
    conn.execute("DELETE FROM sales_catalogs")
    conn.execute(
        """
        CREATE TEMP TABLE _lots ON COMMIT DROP AS
        SELECT row_number() OVER (ORDER BY _rid) AS id, s.*,
               CASE WHEN btrim(coalesce(catalog_year, '')) ~ '^[0-9]+$'
                    THEN btrim(catalog_year)::int ELSE 0 END AS _year,
               COALESCE(NULLIF(btrim(coalesce(catalog_title, '')), ''), coalesce(source_file, '')) AS _title
        FROM sales_lots s
        """
    )
    conn.execute(
        """
        INSERT INTO sales_catalogs (id, year, title, source_file)
        SELECT row_number() OVER (ORDER BY first_id), _year, _title, source_file
        FROM (
            SELECT DISTINCT ON (_year, _title) _year, _title, id AS first_id, coalesce(source_file, '') AS source_file
            FROM _lots ORDER BY _year, _title, id
        ) c
        """
    )
    titles = conn.execute("SELECT id, title FROM sales_catalogs").fetchall()
    with conn.cursor() as cur:
        cur.executemany(
            "UPDATE sales_catalogs SET month_index = %s WHERE id = %s",
            [(sales_history.get_sale_month_index(title), cid) for cid, title in titles],
        )
    price = (
        "(CASE WHEN replace(btrim(coalesce(s.price_num, '')), ',', '') ~ '^[-+]?[0-9]*\\.?[0-9]+([eE][-+]?[0-9]+)?$' "
        "THEN replace(btrim(s.price_num), ',', '')::float8 ELSE 0 END)"
    )
    conn.execute(
        f"""
        INSERT INTO sales_lot_index (
            id, catalog_id, hip, name, name_key, sex, sex_letter, sire, sire_key,
            dam, dam_key, damsire, damsire_key, color, consignor, purchaser, barn,
            foaled_location, birth_year, price_raw, price_num, is_rna, is_out, is_fasig,
            nk, sk, dk
        )
        SELECT s.id, c.id, coalesce(s.hip, ''), coalesce(s.name, ''), {_key_sql('name')},
               coalesce(s.sex, ''), coalesce(s.sex_letter, ''), coalesce(s.sire, ''), {_key_sql('sire')},
               coalesce(s.dam, ''), {_key_sql('dam')}, coalesce(s.damsire, ''), {_key_sql('damsire')},
               coalesce(s.color, ''), coalesce(s.consignor, ''), coalesce(s.purchaser, ''),
               coalesce(s.barn, ''), coalesce(s.foaled_location, ''), coalesce(s.birth_year, ''),
               coalesce(s.price_raw, ''), {price},
               {_truthy_sql('is_rna')}, {_truthy_sql('is_out')}, {_truthy_sql('is_fasig')},
               {_NAME_KEY_SQL.format(c='s.name')}, {_NAME_KEY_SQL.format(c='s.sire')},
               {_NAME_KEY_SQL.format(c='s.dam')}
        FROM _lots s
        JOIN sales_catalogs c ON c.year = s._year AND c.title = s._title
        """
    )
    count = conn.execute("SELECT count(*) FROM sales_lot_index").fetchone()[0]
    _bump(conn, "sales_lot_index", int(count), version=version)


def ensure_sales_index() -> bool:
    """Rebuild the sales index if sales_lots changed since it was built. False when empty."""
    source = table_version("sales_lots")
    if not source:
        return False
    if table_version("sales_lot_index") == source:
        return True
    with _lock:
        with connect() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(812345002)")
            rec = conn.execute(
                "SELECT table_name, version FROM crossr_table_versions "
                "WHERE table_name IN ('sales_lots', 'sales_lot_index')"
            ).fetchall()
            versions = dict(rec)
            if versions.get("sales_lot_index") != versions.get("sales_lots"):
                _rebuild_sales_index(conn, float(versions["sales_lots"]))
            else:
                _version_cache["sales_lot_index"] = (time.monotonic(), float(versions["sales_lots"]))
    return True

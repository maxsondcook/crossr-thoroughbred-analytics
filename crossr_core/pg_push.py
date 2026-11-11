"""Batched writes to Railway Postgres: one COPY into a TEMP table per table, then
one set-based statement, so a push costs a few round trips however many rows.

    upsert(conn, table, columns, keys, rows)            insert new keys, update changed rows
    replace_by(conn, table, columns, key_col, keys, rows, id_seq=None)
                                                        delete rows for these keys, insert rows

Most target tables are all-TEXT (crossr_core.sample_db.TABLES) without unique
constraints, so keys are matched with COALESCE(col::text, '') equality (hash
joins, NULL = blank) rather than ON CONFLICT. Nothing is read back but counts,
and no table is rewritten whole. Callers bump crossr_table_versions once per
push (pgdb.flush_versions), not per row.
"""

from __future__ import annotations

from typing import Iterable


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _cell(value):
    if value is None:
        return None
    text = str(value)
    return text if text.strip() != "" else None


def _match(a: str, b: str, keys: Iterable[str]) -> str:
    return " AND ".join(f"COALESCE({a}.{_q(k)}::text, '') = COALESCE({b}.{_q(k)}::text, '')" for k in keys)


def _copy_temp(conn, table: str, columns: list[str], rows: Iterable) -> str:
    """TEMP table with ``columns`` typed as in ``table`` (no constraints, so
    _rid and the like stay out), dropped at commit, filled by COPY."""
    tmp = f"_push_{table}"
    cols = ", ".join(_q(c) for c in columns)
    conn.execute(f"DROP TABLE IF EXISTS {_q(tmp)}")
    conn.execute(f"CREATE TEMP TABLE {_q(tmp)} ON COMMIT DROP AS SELECT {cols} FROM {_q(table)} WITH NO DATA")
    with conn.cursor() as cur:
        with cur.copy(f"COPY {_q(tmp)} ({cols}) FROM STDIN") as copy:
            for row in rows:
                copy.write_row([_cell(row.get(c)) for c in columns])
    return tmp


def upsert(conn, table: str, columns: list[str], keys: tuple[str, ...], rows: list[dict],
           keep_blanks: bool = False) -> dict:
    """Insert rows whose key is new; update rows whose values changed.

    keep_blanks: a blank incoming cell leaves the stored value alone (the
    sample_db.upsert_rows rule). Otherwise the incoming row is the whole truth.
    Must run inside a transaction (the TEMP table drops at commit).
    """
    if not rows:
        return {"inserted": 0, "updated": 0}
    latest = {tuple(_cell(r.get(k)) or "" for k in keys): r for r in rows}  # last one wins
    tmp = _copy_temp(conn, table, columns, latest.values())
    values = [c for c in columns if c not in keys]
    updated = 0
    if values:
        new = {c: (f"COALESCE(t.{_q(c)}, x.{_q(c)})" if keep_blanks else f"t.{_q(c)}") for c in values}
        sets = ", ".join(f"{_q(c)} = {new[c]}" for c in values)
        old_vals = ", ".join(f"x.{_q(c)}" for c in values)
        new_vals = ", ".join(new[c] for c in values)
        cur = conn.execute(
            f"UPDATE {_q(table)} x SET {sets} FROM {_q(tmp)} t WHERE {_match('x', 't', keys)} "
            f"AND ROW({old_vals}) IS DISTINCT FROM ROW({new_vals})"
        )
        updated = cur.rowcount
    cols = ", ".join(_q(c) for c in columns)
    cur = conn.execute(
        f"INSERT INTO {_q(table)} ({cols}) SELECT {', '.join('t.' + _q(c) for c in columns)} FROM {_q(tmp)} t "
        f"WHERE NOT EXISTS (SELECT 1 FROM {_q(table)} x WHERE {_match('x', 't', keys)})"
    )
    return {"inserted": cur.rowcount, "updated": updated}


def replace_by(conn, table: str, columns: list[str], key_col: str, keys: Iterable, rows: list[dict],
               id_seq: str | None = None) -> dict:
    """Delete every row whose ``key_col`` is in ``keys``, then insert ``rows``.

    id_seq: give each inserted row ``id = nextval(id_seq)`` (race_seasons,
    race_starts). Must run inside a transaction.
    """
    keys = sorted({str(k) for k in keys if k not in (None, "")})
    if not keys:
        return {"deleted": 0, "inserted": 0}
    deleted = conn.execute(
        f"DELETE FROM {_q(table)} WHERE {_q(key_col)}::text = ANY(%s)", (keys,)
    ).rowcount
    if not rows:
        return {"deleted": deleted, "inserted": 0}
    tmp = _copy_temp(conn, table, columns, rows)
    cols = [c for c in columns if not (id_seq and c == "id")]
    select = ", ".join(f"t.{_q(c)}" for c in cols)
    if id_seq:
        inserted = conn.execute(
            f"INSERT INTO {_q(table)} (id, {', '.join(_q(c) for c in cols)}) "
            f"SELECT nextval(%s)::text, {select} FROM {_q(tmp)} t", (id_seq,)
        ).rowcount
    else:
        inserted = conn.execute(
            f"INSERT INTO {_q(table)} ({', '.join(_q(c) for c in cols)}) SELECT {select} FROM {_q(tmp)} t"
        ).rowcount
    return {"deleted": deleted, "inserted": inserted}


def table_versions(conn, tables: Iterable[str]) -> dict[str, str]:
    return {
        r["table_name"]: str(r["version"])
        for r in conn.execute(
            "SELECT table_name, version FROM crossr_table_versions WHERE table_name = ANY(%s)", (list(tables),)
        )
    }

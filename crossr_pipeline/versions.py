"""Bump crossr_table_versions so the API reloads tables a pipeline job changed."""

from __future__ import annotations

import time


def flush_versions(conn, tables) -> None:
    """Record the new row count and a fresh version for each table."""
    for table in tables:
        count = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        conn.execute(
            "INSERT INTO crossr_table_versions (table_name, version, row_count, updated_at) "
            "VALUES (%s, %s, %s, now()) ON CONFLICT (table_name) DO UPDATE SET "
            "version = EXCLUDED.version, row_count = EXCLUDED.row_count, updated_at = now()",
            (table, time.time(), int(count)),
        )

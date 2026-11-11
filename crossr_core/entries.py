"""race_entries writes shared by every entries source (Phase 7 weekly entries).

Sources: ``crossr_pipeline.nyra_entries`` (NYRA's published entries, daily) and
``scripts/import_entries.py`` (a CSV from any other permitted source).

Every row is keyed by (race_date, track_code, race_number, horse_key), so
re-reading a card updates it in place and never duplicates it. A horse is linked
to race_horses by Equibase refno first (exact identity), then by name only when
the name is one horse (foal year honoured), so same-named horses are never
confused. Unlinked entries keep horse_id NULL and are listed in the view
``race_entries_unlinked`` for the Equibase pipeline to enrich; every later
upsert re-links them once the horse exists.
"""

from __future__ import annotations

import datetime as dt
from typing import Iterable, Optional

from crossr_core import weekly

EXTRA_SCHEMA = """
ALTER TABLE race_entries ADD COLUMN IF NOT EXISTS first_seen_at TIMESTAMPTZ DEFAULT now();
ALTER TABLE race_entries ADD COLUMN IF NOT EXISTS foal_year INTEGER;
CREATE INDEX IF NOT EXISTS idx_race_entries_refno ON race_entries (horse_refno);
CREATE OR REPLACE VIEW race_entries_unlinked AS
    SELECT horse_refno, min(horse_name) AS horse_name, min(foal_year) AS foal_year,
           min(race_date) AS first_race_date, max(race_date) AS last_race_date,
           count(*) AS entries, string_agg(DISTINCT source, ',') AS sources
    FROM race_entries
    WHERE horse_id IS NULL AND status <> 'scratched'
    GROUP BY horse_refno, CASE WHEN horse_refno IS NULL THEN horse_key END;
"""

FIELDS = ("source_id", "track", "country", "horse_refno", "foal_year", "race_type", "race_name", "distance",
          "surface", "post_position", "status")


def ensure(conn) -> None:
    conn.execute(weekly.SCHEMA)
    conn.execute(EXTRA_SCHEMA)


def _year(v) -> Optional[int]:
    try:
        y = int(str(v or "")[:4])
    except ValueError:
        return None
    return y if 1900 < y < 2100 else None


def match(conn, row: dict) -> Optional[str]:
    """race_horses id for an entry, or None when it is not one known horse."""
    refno = str(row.get("horse_refno") or "").strip()
    if refno:
        hit = conn.execute("SELECT id FROM race_horses WHERE equibase_refno = %s AND coalesce(status, '') <> 'merged'",
                           (refno,)).fetchall()
        if len(hit) == 1:
            return hit[0][0]
    hits = conn.execute(
        """SELECT id, substring(foal_year from '^[0-9]{4}'), coalesce(equibase_refno, '') FROM race_horses
           WHERE name_key = %s AND coalesce(status, '') <> 'merged'""", (row["horse_key"],)).fetchall()
    fy = _year(row.get("foal_year"))
    if fy:  # an undated placeholder may be an older namesake: only a dated record with the same year counts
        hits = [h for h in hits if h[1] == str(fy)]
    if refno:  # a namesake with a different refno is a different horse
        hits = [h for h in hits if h[2] in ("", refno)]
    return hits[0][0] if len(hits) == 1 else None


def normalise(row: dict) -> dict:
    r = dict(row)
    r["horse"] = str(r["horse"]).strip()
    r["horse_key"] = weekly.name_key(r["horse"])
    r["race_number"] = int(r["race_number"])
    r["track_code"] = str(r["track_code"]).strip().upper()
    r["status"] = (r.get("status") or "entered").lower()
    r["foal_year"] = _year(r.get("foal_year"))
    if isinstance(r["race_date"], dt.date):
        r["race_date"] = r["race_date"].isoformat()
    return r


def upsert(conn, rows: Iterable[dict], source: str) -> dict:
    """Insert/update entries (key: date, track, race, horse). Returns counts."""
    ensure(conn)
    rows = [normalise(r) for r in rows]
    linked = 0
    for r in rows:
        r["horse_id"] = match(conn, r)
        linked += bool(r["horse_id"])
        conn.execute(
            """INSERT INTO race_entries (source, source_id, race_date, track_code, track, country, race_number,
                   horse_name, horse_key, horse_refno, horse_id, foal_year, race_type, race_name, distance, surface,
                   post_position, status)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (race_date, track_code, race_number, horse_key) DO UPDATE SET
                   source = EXCLUDED.source, source_id = EXCLUDED.source_id,
                   track = coalesce(EXCLUDED.track, race_entries.track),
                   country = coalesce(EXCLUDED.country, race_entries.country),
                   horse_name = EXCLUDED.horse_name,
                   horse_refno = coalesce(EXCLUDED.horse_refno, race_entries.horse_refno),
                   horse_id = CASE WHEN EXCLUDED.horse_refno IS NOT NULL OR EXCLUDED.foal_year IS NOT NULL
                                   THEN EXCLUDED.horse_id ELSE coalesce(EXCLUDED.horse_id, race_entries.horse_id) END,
                   foal_year = coalesce(EXCLUDED.foal_year, race_entries.foal_year),
                   race_type = coalesce(EXCLUDED.race_type, race_entries.race_type),
                   race_name = coalesce(EXCLUDED.race_name, race_entries.race_name),
                   distance = coalesce(EXCLUDED.distance, race_entries.distance),
                   surface = coalesce(EXCLUDED.surface, race_entries.surface),
                   post_position = coalesce(EXCLUDED.post_position, race_entries.post_position),
                   status = EXCLUDED.status, fetched_at = now()""",
            (source, r.get("source_id") or None, r["race_date"], r["track_code"], r.get("track") or None,
             r.get("country") or None, r["race_number"], r["horse"], r["horse_key"], r.get("horse_refno") or None,
             r["horse_id"], r["foal_year"], r.get("race_type") or None, r.get("race_name") or None,
             r.get("distance") or None, r.get("surface") or None, r.get("post_position") or None, r["status"]))
    return {"rows": len(rows), "linked": linked, "relinked": relink(conn)}


def mark_missing_scratched(conn, source: str, race_date: str, track_code: str, race_number: int,
                           present_keys: set[str]) -> int:
    """A runner that drops off a re-read card (same source) has been scratched."""
    if not present_keys:  # an empty read is a failed read, never a mass scratch
        return 0
    return conn.execute(
        """UPDATE race_entries SET status = 'scratched', fetched_at = now()
           WHERE source = %s AND race_date = %s AND track_code = %s AND race_number = %s
             AND status <> 'scratched' AND NOT (horse_key = ANY(%s))""",
        (source, race_date, track_code.upper(), race_number, sorted(present_keys))).rowcount


def relink(conn) -> int:
    """Link earlier unmatched entries whose horse has since been scraped (refno first, then a unique name)."""
    n = conn.execute(
        """UPDATE race_entries e SET horse_id = h.id FROM race_horses h
           WHERE e.horse_id IS NULL AND e.horse_refno IS NOT NULL AND h.equibase_refno = e.horse_refno
             AND coalesce(h.status, '') <> 'merged'
             AND (SELECT count(*) FROM race_horses x WHERE x.equibase_refno = e.horse_refno
                  AND coalesce(x.status, '') <> 'merged') = 1""").rowcount
    n += conn.execute(
        """UPDATE race_entries e SET horse_id = h.id FROM race_horses h
           WHERE e.horse_id IS NULL AND h.name_key = e.horse_key AND coalesce(h.status, '') <> 'merged'
             AND (e.horse_refno IS NULL OR coalesce(h.equibase_refno, '') IN ('', e.horse_refno))
             AND (e.foal_year IS NULL OR substring(h.foal_year from '^[0-9]{4}') = e.foal_year::text)
             AND (SELECT count(*) FROM race_horses x WHERE x.name_key = e.horse_key
                  AND coalesce(x.status, '') <> 'merged') = 1""").rowcount
    return n


def refresh_week_report(conn, today: Optional[dt.date] = None) -> int:
    """Re-store this week's entries list (no emails) so cards drawn midweek appear on it."""
    today = today or dt.date.today()
    start, end = weekly.week_windows(today)["entries"]
    rows = weekly.load_entries(conn, start, end)
    weekly.store_report(conn, "entries", start, rows)
    return len(rows)

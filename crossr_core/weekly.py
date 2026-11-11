"""Weekly racing reports and starred-horse emails (master prompt Phase 7).

Monday job (``python -m crossr_core.weekly run``):

* entries list for this week (Monday through Sunday, Monday's races included),
  from ``race_entries``;
* results list for last week, from ``race_starts`` (the Equibase scraper's
  starts, refreshed weekly for active horses);
* one email per opted-in user covering their starred horses, the progeny of
  starred stallions and broodmares, and the progeny of starred broodmare sires'
  daughters.

A race belongs to exactly one week (by its date), and every emailed item is
logged in ``weekly_notified``, so nothing is sent twice. Results that reach the
database late (the scraper refreshes a horse up to a week after it runs) are
picked up by the next report: results look back ``RESULTS_LOOKBACK_DAYS`` and
skip what was already sent. A failed email stays 'failed' in ``weekly_emails``
and is retried on the next run.

Entries: ``race_entries`` is filled daily from NYRA's published entries
(crossr_pipeline.nyra_entries) and by ``scripts/import_entries.py`` from any
other permitted source. DRF and Equibase forbid automated access in their terms,
so neither is scraped.

Email is sent over SMTP (Gmail with an app password by default):
    CROSSR_SMTP_USER, CROSSR_SMTP_PASSWORD   required to send
    CROSSR_SMTP_HOST=smtp.gmail.com CROSSR_SMTP_PORT=465 CROSSR_EMAIL_FROM=<user>
    CROSSR_SITE_URL=https://your-crossr-site.example
Without credentials the job builds and stores the reports and logs the emails
it would have sent ('skipped: no SMTP credentials'), so nothing is lost.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import smtplib
import ssl
import sys
import time
from email.message import EmailMessage
from typing import Iterable, Optional
from urllib.parse import quote

WEEK_START = int(os.environ.get("CROSSR_WEEK_START", "0"))  # 0 = Monday
RESULTS_LOOKBACK_DAYS = int(os.environ.get("CROSSR_RESULTS_LOOKBACK_DAYS", "28"))
SEND_RETRIES = 3
JOB = "weekly-reports"
LOCK_ID = 812345077

SECTIONS = (
    ("horse", "Starred horses"),
    ("broodmare", "Starred broodmares and their progeny"),
    ("stallion", "Starred stallions and their progeny"),
    ("bms", "Starred broodmare sires and their daughters' progeny"),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS race_entries (
    source TEXT NOT NULL,
    source_id TEXT,
    race_date DATE NOT NULL,
    track_code TEXT NOT NULL,
    track TEXT,
    country TEXT,
    race_number INTEGER NOT NULL,
    horse_name TEXT NOT NULL,
    horse_key TEXT NOT NULL,
    horse_refno TEXT,
    horse_id TEXT,
    race_type TEXT,
    race_name TEXT,
    distance TEXT,
    surface TEXT,
    post_position TEXT,
    status TEXT NOT NULL DEFAULT 'entered',
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (race_date, track_code, race_number, horse_key)
);
CREATE INDEX IF NOT EXISTS idx_race_entries_horse ON race_entries (horse_id);
CREATE TABLE IF NOT EXISTS weekly_reports (
    kind TEXT NOT NULL,
    week_start DATE NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    row_count INTEGER NOT NULL,
    payload JSONB NOT NULL,
    PRIMARY KEY (kind, week_start)
);
CREATE TABLE IF NOT EXISTS weekly_emails (
    user_id BIGINT NOT NULL,
    week_start DATE NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    item_count INTEGER NOT NULL DEFAULT 0,
    detail TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, week_start)
);
CREATE TABLE IF NOT EXISTS weekly_notified (
    user_id BIGINT NOT NULL,
    kind TEXT NOT NULL,
    item_key TEXT NOT NULL,
    week_start DATE NOT NULL,
    PRIMARY KEY (user_id, kind, item_key)
);
"""


# ----------------------------------------------------------------- dates
def week_start(day: dt.date, start_weekday: int = WEEK_START) -> dt.date:
    """First day of the week containing `day` (Monday by default)."""
    return day - dt.timedelta(days=(day.weekday() - start_weekday) % 7)


def week_windows(today: dt.date, start_weekday: int = WEEK_START) -> dict:
    """This week's entries window and last week's results window, as [start, end) dates."""
    this = week_start(today, start_weekday)
    return {
        "entries": (this, this + dt.timedelta(days=7)),
        "results": (this - dt.timedelta(days=7), this),
    }


# ----------------------------------------------------------------- names
def name_key(name: str) -> str:
    """Upper-case, no country suffix or punctuation: 'Curlin (USA)' -> 'CURLIN'."""
    s = re.sub(r"\s*\([^)]*\)\s*$", "", str(name or "")).upper()
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]", "", s)).strip()


def parse_star(raw: str) -> tuple[str, Optional[int]]:
    """'Curlin (2004)' -> ('CURLIN', 2004); 'Curlin' -> ('CURLIN', None)."""
    m = re.search(r"\((\d{4})\)\s*$", str(raw or ""))
    return name_key(raw), (int(m.group(1)) if m else None)


def item_key(row: dict) -> str:
    return f"{row['horse_id'] or name_key(row['horse'])}|{row['race_date']}|{row['track_code']}|{row['race_number']}"


# ----------------------------------------------------------------- preferences
DEFAULT_NOTIFICATIONS = {"email": False, "entries": True, "results": True}


def notification_prefs(prefs: dict) -> dict:
    out = dict(DEFAULT_NOTIFICATIONS)
    raw = (prefs or {}).get("notifications")
    if isinstance(raw, dict):
        for k in out:
            if k in raw:
                out[k] = bool(raw[k])
    return out


# ----------------------------------------------------------------- matching
def match_items(stars: list[dict], races: Iterable[dict]) -> dict[str, list[dict]]:
    """Group races into report sections for one user.

    `stars`: resolved starred horses, each {"label", "ids": set of race_horses ids,
    "refnos": set, "key": name key, "year": foal year or None, "unique": bool}.
    `races`: rows with the raced horse's id, its sire / dam / damsire (refno and
    name key) and foal year. Progeny are matched by Equibase refno; by name only
    when the starred name is unique, and never to a horse foaled before the
    starred horse could have bred (foal year + 3).
    """
    out: dict[str, list[dict]] = {k: [] for k, _ in SECTIONS}
    seen: set[tuple[str, str]] = set()

    def parent_is(star: dict, refno: str, key: str, child_year) -> bool:
        if star["year"] and child_year and child_year < star["year"] + 3:
            return False
        if refno:
            return refno in star["refnos"]
        return star["unique"] and bool(key) and key == star["key"]

    for r in races:
        for star in stars:
            hits = []
            if r["horse_id"] in star["ids"] or (not r["horse_id"] and r["horse_key"] == star["key"] and star["unique"]):
                hits.append(("horse", None))
            if parent_is(star, r.get("dam_refno") or "", r.get("dam_key") or "", r.get("foal_year")):
                hits.append(("broodmare", None))
            if parent_is(star, r.get("sire_refno") or "", r.get("sire_key") or "", r.get("foal_year")):
                hits.append(("stallion", None))
            if parent_is(star, r.get("damsire_refno") or "", r.get("damsire_key") or "", r.get("foal_year")):
                hits.append(("bms", r.get("dam") or ""))
            for section, daughter in hits:
                k = (section, item_key(r))
                if k in seen:
                    continue
                seen.add(k)
                out[section].append({**r, "starred": star["label"], "daughter": daughter})
    for rows in out.values():
        rows.sort(key=lambda x: (str(x["race_date"]), x["track"] or "", _int(x["race_number"])))
    return out


def _int(v) -> int:
    try:
        return int(str(v).strip())
    except ValueError:
        return 0


def drop_sent(sections: dict[str, list[dict]], sent: set[tuple[str, str]], kind: str) -> dict[str, list[dict]]:
    """Remove items already emailed (kind = 'entry' or 'result')."""
    return {s: [r for r in rows if (kind, item_key(r)) not in sent] for s, rows in sections.items()}


# ----------------------------------------------------------------- email
def _place(r: dict) -> str:
    p = str(r.get("place") or "").strip()
    if not p:
        return ""
    if not p.isdigit():
        return p
    n = int(p)
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _details(r: dict) -> str:
    bits = [r.get("race_name") or r.get("race_type") or "", r.get("grade") and f"G{r['grade']}" or "",
            r.get("distance") or "", r.get("surface") or ""]
    if r.get("post_position"):
        bits.append(f"post {r['post_position']}")
    return ", ".join(b for b in bits if b)


def render_email(username: str, week: dt.date, entries: dict, results: dict, site_url: str) -> Optional[dict]:
    """Subject, text and HTML for one user's report, or None when there is nothing to report."""
    blocks = []
    for section, title in SECTIONS:
        rows = [("Entered", r) for r in entries.get(section, [])] + [("Result", r) for r in results.get(section, [])]
        if rows:
            blocks.append((title, rows))
    if not blocks:
        return None
    n = sum(len(rows) for _, rows in blocks)
    subject = f"Crossr weekly racing report: {n} update{'s' if n != 1 else ''} (week of {week:%b %-d})"
    text = [f"Hi {username},", "", f"Racing activity for your starred horses, week of {week:%B %-d, %Y}.", ""]
    parts = [f"<p>Hi {html.escape(username)},</p><p>Racing activity for your starred horses, "
             f"week of {week:%B %-d, %Y}.</p>"]
    for title, rows in blocks:
        text.append(title.upper())
        parts.append(f"<h3 style='margin:18px 0 6px'>{html.escape(title)}</h3><table cellpadding='4' "
                     "style='border-collapse:collapse;font-size:14px'><tr style='text-align:left'><th>Horse</th>"
                     "<th>Status</th><th>Date</th><th>Track</th><th>Race</th><th>Details</th><th>Finish</th></tr>")
        for status, r in rows:
            link = f"{site_url.rstrip('/')}/horses/{quote(r['horse'])}"
            rel = ""
            if r["starred"] and name_key(r["starred"]) != name_key(r["horse"]):
                rel = f" (out of {r['daughter']}, by your {r['starred']})" if r.get("daughter") else f" ({r['starred']})"
            finish = _place(r) if status == "Result" else ""
            text.append(f"- {r['horse']}{rel}: {status.lower()} {r['race_date']} {r['track']} race {r['race_number']}"
                        f"{' - ' + _details(r) if _details(r) else ''}{' - finished ' + finish if finish else ''}  {link}")
            parts.append(
                "<tr>" + "".join(f"<td>{c}</td>" for c in (
                    f"<a href='{html.escape(link)}'>{html.escape(r['horse'])}</a>{html.escape(rel)}",
                    status, html.escape(str(r["race_date"])), html.escape(r["track"] or r["track_code"]),
                    html.escape(str(r["race_number"])), html.escape(_details(r)), html.escape(finish))) + "</tr>")
        text.append("")
        parts.append("</table>")
    footer = f"Change what you receive in your barn: {site_url.rstrip('/')}/barn"
    text.append(footer)
    parts.append(f"<p style='color:#666;font-size:12px'>{html.escape(footer)}</p>")
    return {"subject": subject, "text": "\n".join(text), "html": "".join(parts), "count": n}


def smtp_settings() -> Optional[dict]:
    user, password = os.environ.get("CROSSR_SMTP_USER"), os.environ.get("CROSSR_SMTP_PASSWORD")
    if not user or not password:
        return None
    return {"host": os.environ.get("CROSSR_SMTP_HOST", "smtp.gmail.com"),
            "port": int(os.environ.get("CROSSR_SMTP_PORT", "465")),
            "user": user, "password": password, "from": os.environ.get("CROSSR_EMAIL_FROM", user)}


def send_email(settings: dict, to: str, mail: dict, retries: int = SEND_RETRIES, smtp_cls=smtplib.SMTP_SSL,
               sleep=time.sleep) -> None:
    """Send one email, retrying temporary failures (connection errors, 4xx replies)."""
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = settings["from"], to, mail["subject"]
    msg.set_content(mail["text"])
    msg.add_alternative(mail["html"], subtype="html")
    for attempt in range(1, retries + 1):
        try:
            with smtp_cls(settings["host"], settings["port"], context=ssl.create_default_context(), timeout=30) as s:
                s.login(settings["user"], settings["password"])
                s.send_message(msg)
            return
        except smtplib.SMTPResponseException as exc:
            if not 400 <= exc.smtp_code < 500 or attempt == retries:
                raise
        except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, OSError):
            if attempt == retries:
                raise
        sleep(2 ** attempt)


# ----------------------------------------------------------------- database
RACE_COLS = """h.id AS horse_id, h.display_name AS horse, h.name_key AS horse_key,
    nullif(substring(h.foal_year from '^[0-9]{4}'), '')::int AS foal_year,
    h.sire, coalesce(h.sire_refno, '') AS sire_refno, h.dam, coalesce(h.dam_refno, '') AS dam_refno,
    h.damsire, coalesce(h.damsire_refno, '') AS damsire_refno"""


def _rows(cur) -> list[dict]:
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def clean_display(name) -> str:
    """'Katonah (KY) 56' -> 'Katonah (KY)': drop a figure the scraper left after the country code."""
    return re.sub(r"(\([A-Z]{2,3}\))\s+\d+$", r"\1", str(name or "").strip())


def _with_keys(rows: list[dict]) -> list[dict]:
    for r in rows:
        r["horse"] = clean_display(r.get("horse"))
        r["sire_key"], r["dam_key"], r["damsire_key"] = name_key(r.get("sire")), name_key(r.get("dam")), name_key(r.get("damsire"))
        r["horse_key"] = name_key(r.get("horse_key") or r.get("horse"))
    return rows


def load_results(conn, start: dt.date, end: dt.date) -> list[dict]:
    cur = conn.execute(
        f"""SELECT {RACE_COLS}, s.race_date, s.track, s.track_code, s.race_number, s.race_name, s.grade,
                   s.place, NULL AS distance, NULL AS surface, NULL AS post_position
            FROM race_starts s JOIN race_horses h ON h.id = s.horse_id
            WHERE s.race_date >= %s AND s.race_date < %s AND coalesce(h.status, '') <> 'merged'""",
        (start.isoformat(), end.isoformat()))
    return _with_keys(_rows(cur))


def load_entries(conn, start: dt.date, end: dt.date) -> list[dict]:
    cur = conn.execute(
        f"""SELECT {RACE_COLS.replace('h.id AS horse_id', 'e.horse_id').replace('h.display_name AS horse', 'e.horse_name AS horse')
                   .replace('h.name_key AS horse_key', 'e.horse_key')},
                   e.race_date::text AS race_date, coalesce(e.track, e.track_code) AS track, e.track_code,
                   e.race_number, coalesce(e.race_name, e.race_type) AS race_name, NULL AS grade, NULL AS place,
                   e.distance, e.surface, e.post_position
            FROM race_entries e LEFT JOIN race_horses h ON h.id = e.horse_id
            WHERE e.race_date >= %s AND e.race_date < %s AND e.status <> 'scratched'""",
        (start, end))
    return _with_keys(_rows(cur))


def resolve_stars(conn, starred: list[str]) -> list[dict]:
    """Starred names -> race_horses identities (refnos), honouring '(year)' when given."""
    out = []
    for raw in starred:
        key, year = parse_star(raw)
        if not key:
            continue
        cur = conn.execute(
            """SELECT id, coalesce(equibase_refno, '') AS refno,
                      nullif(substring(foal_year from '^[0-9]{4}'), '')::int AS fy
               FROM race_horses WHERE name_key = %s AND coalesce(status, '') <> 'merged'""", (key,))
        rows = [r for r in _rows(cur) if year is None or r["fy"] in (None, year)]
        # Undated rows are unscraped placeholders; the name is one horse when every dated row agrees.
        years = {r["fy"] for r in rows if r["fy"]}
        out.append({"label": re.sub(r"\s*\(\d{4}\)\s*$", "", raw).strip(), "key": key,
                    "year": year or (next(iter(years)) if len(years) == 1 else None),
                    "ids": {r["id"] for r in rows}, "refnos": {r["refno"] for r in rows if r["refno"]},
                    "unique": len(years) <= 1})
    return out


def _ensure(conn) -> None:
    from crossr_pipeline import state

    conn.execute(SCHEMA)
    state.ensure(conn)


def store_report(conn, kind: str, week: dt.date, rows: list[dict]) -> None:
    payload = [{k: (str(v) if isinstance(v, (dt.date, dt.datetime)) else v) for k, v in r.items()} for r in rows]
    conn.execute(
        """INSERT INTO weekly_reports (kind, week_start, row_count, payload) VALUES (%s, %s, %s, %s)
           ON CONFLICT (kind, week_start) DO UPDATE SET payload = EXCLUDED.payload,
               row_count = EXCLUDED.row_count, generated_at = now()""",
        (kind, week, len(rows), json.dumps(payload, default=str)))


def run(today: Optional[dt.date] = None, *, send: bool = True, only_user: Optional[int] = None) -> dict:
    """The Monday job. Safe to re-run: reports are upserted, emails already sent are skipped."""
    from crossr_core import railway_db
    from crossr_pipeline import state

    today = today or dt.date.today()
    win = week_windows(today)
    week = win["entries"][0]
    settings = smtp_settings() if send else None
    summary = {"week_start": str(week), "emails": {}}
    with railway_db.connect() as conn:
        _ensure(conn)
        if not conn.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_ID,)).fetchone()[0]:
            return {**summary, "skipped": "another weekly run is in progress"}
        job = state.Run(conn, JOB)
        conn.commit()
        try:
            entries = load_entries(conn, *win["entries"])
            weekly_results = load_results(conn, *win["results"])
            store_report(conn, "entries", week, entries)
            store_report(conn, "results", win["results"][0], weekly_results)
            conn.commit()
            summary.update(entries=len(entries), results=len(weekly_results))
            # results for emails: look back further so late-scraped starts are not missed
            lookback = load_results(conn, win["results"][1] - dt.timedelta(days=RESULTS_LOOKBACK_DAYS), win["results"][1])
            users = _rows(conn.execute(
                """SELECT u.id, u.email, u.username, p.payload FROM users u JOIN preferences p ON p.user_id = u.id
                   WHERE %s::bigint IS NULL OR u.id = %s::bigint""", (only_user, only_user)))
            for u in users:
                status = _email_user(conn, u, week, entries, lookback, settings, send)
                summary["emails"][status] = summary["emails"].get(status, 0) + 1
                job.bump(status)
                conn.commit()  # each user's email and log land together, before the next send
            job.finish("ok", json.dumps(summary["emails"]))
            conn.commit()
        except BaseException as exc:
            conn.rollback()
            job.finish("failed", f"{exc.__class__.__name__}: {exc}"[:500])
            conn.commit()
            raise
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
    return summary


def _email_user(conn, u: dict, week: dt.date, entries: list[dict], results: list[dict], settings, send: bool) -> str:
    try:
        prefs = json.loads(u["payload"] or "{}")
    except ValueError:
        prefs = {}
    note = notification_prefs(prefs)
    starred = list(dict.fromkeys((prefs.get("starred") or []) + (prefs.get("favorites") or [])))
    if not note["email"] or not starred:
        return "not subscribed"
    done = conn.execute("SELECT status FROM weekly_emails WHERE user_id = %s AND week_start = %s",
                        (u["id"], week)).fetchone()
    if done and done[0] in ("sent", "nothing to report"):
        return "already " + done[0]
    stars = resolve_stars(conn, starred)
    sent = {(k, i) for k, i in conn.execute("SELECT kind, item_key FROM weekly_notified WHERE user_id = %s",
                                            (u["id"],)).fetchall()}
    ent = drop_sent(match_items(stars, entries), sent, "entry") if note["entries"] else {}
    res = drop_sent(match_items(stars, results), sent, "result") if note["results"] else {}
    mail = render_email(u["username"], week, ent, res, os.environ.get("CROSSR_SITE_URL", "https://your-crossr-site.example"))
    if mail is None:
        status, detail = "nothing to report", None
    elif not send:
        return "dry run"
    elif not settings:
        status, detail = "skipped", "no SMTP credentials"
    else:
        try:
            send_email(settings, u["email"], mail)
            status, detail = "sent", None
        except Exception as exc:  # recorded and retried next run
            status, detail = "failed", f"{exc.__class__.__name__}: {exc}"[:500]
    if status == "sent":
        for kind, sections in (("entry", ent), ("result", res)):
            for rows in sections.values():
                for r in rows:
                    conn.execute("INSERT INTO weekly_notified VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                                 (u["id"], kind, item_key(r), week))
    conn.execute(
        """INSERT INTO weekly_emails (user_id, week_start, status, attempts, item_count, detail)
           VALUES (%s, %s, %s, 1, %s, %s)
           ON CONFLICT (user_id, week_start) DO UPDATE SET status = EXCLUDED.status, item_count = EXCLUDED.item_count,
               detail = EXCLUDED.detail, attempts = weekly_emails.attempts + 1, updated_at = now()""",
        (u["id"], week, status, mail["count"] if mail else 0, detail))
    return status


def main(argv: Optional[list[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="Weekly entries/results lists and starred-horse emails.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="build this week's lists and send emails (safe to re-run)")
    r.add_argument("--date", help="pretend today is YYYY-MM-DD (to recover a missed week)")
    r.add_argument("--dry-run", action="store_true", help="build and store the lists; send nothing")
    r.add_argument("--user", type=int, help="only this user id")
    args = ap.parse_args(argv)
    today = dt.date.fromisoformat(args.date) if args.date else None
    print(json.dumps(run(today, send=not args.dry_run, only_user=args.user), indent=2, default=str))


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    main()

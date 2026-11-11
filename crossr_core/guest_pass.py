"""One-time guest links: the owner makes a link, one browser opens it, and gets 24 hours of access.

How it works:
* ``create`` makes a random link token. Only its SHA-256 is stored, so the database alone can't
  rebuild a working link. An unopened link stops working after LINK_DAYS.
* ``redeem`` claims the pass in one atomic UPDATE (so two people can't both use it), then creates
  a guest account (guest_<id>, an email ending in @guest.invalid, an unusable password), approves
  it in access_requests, and starts a session that expires after GUEST_HOURS. Everything else
  (the API lock, the web 404 gate, /api/auth/me) treats the guest like any approved user until
  the session expires.
* ``revoke`` ends a pass early: the link dies, the guest's sessions are deleted and their access
  is revoked.

The link page asks the visitor to click "Open CROSSR" before redeeming, so link previews (iMessage,
Slack, email scanners) that fetch the URL don't use up the pass.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time

GUEST_HOURS = 24
LINK_DAYS = 7
GUEST_DOMAIN = "guest.invalid"

SCHEMA = """
CREATE TABLE IF NOT EXISTS guest_passes (
    id BIGSERIAL PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    label TEXT,
    created_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    link_expires_at TIMESTAMPTZ NOT NULL,
    claimed_at TIMESTAMPTZ,
    user_id BIGINT,
    pass_expires_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ
)
"""

USED_OR_EXPIRED = "This guest link has already been used or has expired. Ask for a new one."


def hash_token(token: str) -> str:
    return hashlib.sha256((token or "").encode()).hexdigest()


def guest_email(pass_id: int) -> str:
    return f"guest-{int(pass_id)}@{GUEST_DOMAIN}"


def guest_username(pass_id: int) -> str:
    return f"guest_{int(pass_id)}"


def is_guest_email(email: str | None) -> bool:
    return bool(email) and email.strip().lower().endswith("@" + GUEST_DOMAIN)


def looks_like_token(token: str | None) -> bool:
    return bool(token) and 20 <= len(token) <= 100 and all(c.isalnum() or c in "-_" for c in token)


def _connect():
    from crossr_core import railway_db

    return railway_db.connect()


_schema_ready = False


def _ensure(conn) -> None:
    global _schema_ready
    from crossr_core import access, railway_db

    railway_db.ensure_schema()  # users / sessions / preferences
    access.ensure_schema(conn)
    if not _schema_ready:
        conn.execute(SCHEMA)
        _schema_ready = True


def create(label: str = "", owner: str = "") -> dict:
    """A new pass. The raw token is returned once (for the link) and never stored."""
    token = secrets.token_urlsafe(24)
    with _connect() as conn:
        _ensure(conn)
        row = conn.execute(
            """INSERT INTO guest_passes (token_hash, label, created_by, link_expires_at)
               VALUES (%s, %s, %s, now() + make_interval(days => %s))
               RETURNING id, link_expires_at::text""",
            (hash_token(token), (label or "").strip()[:120] or None, owner or None, LINK_DAYS),
        ).fetchone()
    return {"id": row[0], "token": token, "link_path": f"/guest/{token}", "link_expires_at": row[1]}


def redeem(token: str) -> tuple[dict | None, str | None]:
    """({'session_token', 'max_age'}, None) for a fresh pass, else (None, error)."""
    if not looks_like_token(token):
        return None, USED_OR_EXPIRED
    from crossr_core import auth

    now = int(time.time())
    expires = now + GUEST_HOURS * 3600
    session_token = secrets.token_urlsafe(32)
    with _connect() as conn:
        _ensure(conn)
        with conn.transaction():
            row = conn.execute(
                """UPDATE guest_passes SET claimed_at = now(),
                          pass_expires_at = now() + make_interval(hours => %s)
                   WHERE token_hash = %s AND claimed_at IS NULL AND revoked_at IS NULL
                     AND link_expires_at > now()
                   RETURNING id""",
                (GUEST_HOURS, hash_token(token)),
            ).fetchone()
            if not row:
                return None, USED_OR_EXPIRED
            pass_id = row[0]
            email, username = guest_email(pass_id), guest_username(pass_id)
            # an unusable password: nobody can sign in as a guest, only use the session
            user_id = conn.execute(
                """INSERT INTO users (email, username, password_hash, created_at) VALUES (%s, %s, %s, %s)
                   ON CONFLICT (email) DO UPDATE SET created_at = EXCLUDED.created_at RETURNING id""",
                (email, username, auth.hash_password(secrets.token_urlsafe(32)), now),
            ).fetchone()[0]
            conn.execute(
                """INSERT INTO preferences (user_id, payload, updated_at) VALUES (%s, %s, %s)
                   ON CONFLICT (user_id) DO NOTHING""",
                (user_id, json.dumps(auth.empty_prefs()), now),
            )
            conn.execute(
                """INSERT INTO access_requests (email, name, reason, status, decided_at, decided_by)
                   VALUES (%s, %s, 'one-time guest link', 'approved', now(), 'guest-pass')
                   ON CONFLICT (email) DO UPDATE SET status = 'approved', decided_at = now(),
                     decided_by = 'guest-pass'""",
                (email, f"Guest {pass_id}"),
            )
            conn.execute(
                "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (%s, %s, %s, %s)",
                (session_token, user_id, now, expires),
            )
            conn.execute("UPDATE guest_passes SET user_id = %s WHERE id = %s", (user_id, pass_id))
    return {"session_token": session_token, "max_age": GUEST_HOURS * 3600}, None


def revoke(pass_id: int) -> bool:
    """End a pass: the link stops working, the guest is signed out and loses access."""
    from crossr_core import access

    with _connect() as conn:
        _ensure(conn)
        with conn.transaction():
            row = conn.execute(
                "UPDATE guest_passes SET revoked_at = coalesce(revoked_at, now()) WHERE id = %s RETURNING user_id",
                (int(pass_id),),
            ).fetchone()
            if not row:
                return False
            if row[0] is not None:
                conn.execute("DELETE FROM sessions WHERE user_id = %s", (row[0],))
            conn.execute(
                "UPDATE access_requests SET status = 'revoked', decided_at = now(), decided_by = 'guest-pass' "
                "WHERE email = %s",
                (guest_email(pass_id),),
            )
    access._allowed_cache.clear()
    return True


def status(row: dict) -> str:
    if row.get("revoked_at"):
        return "revoked"
    if row.get("claimed_at"):
        return "active" if row.get("active") else "expired"
    return "unused" if row.get("link_live") else "link expired"


def list_passes(limit: int = 100) -> list[dict]:
    with _connect() as conn:
        _ensure(conn)
        cur = conn.execute(
            """SELECT id, label, created_by, created_at::text, link_expires_at::text, claimed_at::text,
                      pass_expires_at::text, revoked_at::text,
                      (pass_expires_at > now()) AS active, (link_expires_at > now()) AS link_live
               FROM guest_passes ORDER BY created_at DESC LIMIT %s""",
            (limit,),
        )
        cols = [d.name for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in rows:
        r["status"] = status(r)
    return rows

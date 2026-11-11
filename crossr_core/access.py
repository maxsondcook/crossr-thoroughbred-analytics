"""Private access: only people the owner has approved can use Crossr.

* The app is hidden: without an approved session every page and API route answers 404.
  The owner approves (invites) emails on the owner-only admin page and shares a private
  sign-in address (CROSSR_ENTRY_PATH on the web service); ``request_access`` remains for
  a signed-in flow but is not open to visitors.
* Only an approved email (or an owner) can create an account or sign in; revoking an
  email locks its account out at the next request.
* Every /api route needs a signed-in, approved account (``api_path_is_open`` lists the
  few that don't), or the service token for the site's own jobs (nightly checks,
  weekly report) in the ``X-Crossr-Service-Token`` header.

Settings (Railway api service):
    CROSSR_OWNER_EMAILS     comma-separated owner emails (always allowed; see the admin page)
    CROSSR_SERVICE_TOKEN    optional; by default the jobs' token is derived from the database password
    CROSSR_LOCK=0           turns the lock off (default: on whenever CROSSR_OWNER_EMAILS is set)
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import threading
import time

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
STATUSES = ("pending", "approved", "denied", "revoked")

# API paths a visitor without an approved session may call: the health check and sign-in / sign-up
# (used from the owner's private sign-in address). Everything else answers 404 Not Found.
OPEN_PATHS = {
    "/api/health",
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/logout",
    "/api/auth/me",
    "/api/access/guest/redeem",  # one-time guest links (crossr_core/guest_pass.py)
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS access_requests (
    id BIGSERIAL PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    name TEXT,
    reason TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at TIMESTAMPTZ,
    decided_by TEXT
)
"""


def lock_enabled() -> bool:
    """On once an owner is configured (so the owner can always get in), unless CROSSR_LOCK=0."""
    if os.environ.get("CROSSR_LOCK", "1").strip().lower() in ("0", "false", "off", "no"):
        return False
    return bool(owner_emails())


def owner_emails() -> set[str]:
    return {e.strip().lower() for e in os.environ.get("CROSSR_OWNER_EMAILS", "").split(",") if e.strip()}


def is_owner(email: str | None) -> bool:
    return bool(email) and email.strip().lower() in owner_emails()


def service_token() -> str:
    """The token the site's own jobs send. CROSSR_SERVICE_TOKEN if set; otherwise derived from the
    database password, which the API and the jobs both already have (railway_env.sh), so no extra
    secret has to be created, stored or pasted anywhere. Anyone holding the database password
    already has full access, so this adds no new way in."""
    explicit = os.environ.get("CROSSR_SERVICE_TOKEN", "").strip()
    if explicit:
        return explicit
    from urllib.parse import unquote, urlparse

    try:
        password = unquote(urlparse(os.environ.get("DATABASE_URL", "")).password or "")
    except ValueError:
        password = ""
    if not password:
        return ""
    return hmac.new(password.encode(), b"crossr-service-token-v1", hashlib.sha256).hexdigest()


def service_token_ok(header_value: str | None) -> bool:
    expected = service_token()
    return bool(expected) and bool(header_value) and hmac.compare_digest(expected, header_value)


def api_path_is_open(path: str) -> bool:
    """Only the sign-in endpoints and the health check; /docs and /openapi.json are hidden too."""
    return path.rstrip("/") in OPEN_PATHS


def clean_email(email: str | None) -> str | None:
    value = (email or "").strip().lower()
    return value if EMAIL_RE.match(value) and len(value) <= 254 else None


# ------------------------------------------------------------------ storage
def _connect():
    from crossr_core import railway_db

    return railway_db.connect()


_schema_ready = False


def ensure_schema(conn) -> None:
    global _schema_ready
    if not _schema_ready:
        conn.execute(SCHEMA)
        _schema_ready = True


def status_of(email: str | None, conn=None) -> str | None:
    """'approved' / 'pending' / 'denied' / 'revoked' / None; owners are always 'approved'."""
    email = clean_email(email)
    if not email:
        return None
    if is_owner(email):
        return "approved"
    if conn is None:
        with _connect() as c:
            return status_of(email, c)
    ensure_schema(conn)
    row = conn.execute("SELECT status FROM access_requests WHERE email = %s", (email,)).fetchone()
    return row[0] if row else None


def is_allowed(email: str | None, conn=None) -> bool:
    return status_of(email, conn) == "approved"


def request_access(email: str, name: str = "", reason: str = "") -> tuple[str | None, str | None]:
    """(status, error). A new email becomes 'pending'; asking again never changes a decision."""
    email = clean_email(email)
    if not email:
        return None, "Enter a valid email address."
    name, reason = (name or "").strip()[:120], (reason or "").strip()[:1000]
    if is_owner(email):
        return "approved", None
    with _connect() as conn:
        ensure_schema(conn)
        conn.execute(
            """INSERT INTO access_requests (email, name, reason) VALUES (%s, %s, %s)
               ON CONFLICT (email) DO UPDATE SET
                 name = CASE WHEN access_requests.status = 'pending' THEN EXCLUDED.name ELSE access_requests.name END,
                 reason = CASE WHEN access_requests.status = 'pending' THEN EXCLUDED.reason ELSE access_requests.reason END""",
            (email, name, reason))
        row = conn.execute("SELECT status FROM access_requests WHERE email = %s", (email,)).fetchone()
    _allowed_cache.clear()
    return row[0], None


def decide(email: str, status: str, owner: str) -> tuple[bool, str | None]:
    """Owner sets an email's status (approve / deny / revoke, or invite a new email as approved)."""
    email = clean_email(email)
    if not email or status not in STATUSES:
        return False, "Bad email or status."
    with _connect() as conn:
        ensure_schema(conn)
        conn.execute(
            """INSERT INTO access_requests (email, status, decided_at, decided_by) VALUES (%s, %s, now(), %s)
               ON CONFLICT (email) DO UPDATE SET status = EXCLUDED.status, decided_at = now(),
                 decided_by = EXCLUDED.decided_by""",
            (email, status, owner))
    _allowed_cache.clear()
    return True, None


def list_requests() -> list[dict]:
    with _connect() as conn:
        ensure_schema(conn)
        cur = conn.execute(
            """SELECT email, name, reason, status, created_at::text, decided_at::text FROM access_requests
               ORDER BY (status = 'pending') DESC, created_at DESC""")
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


# ------------------------------------------------------------------ per-request check
_allowed_cache: dict = {}  # session token -> (checked at, user or None)
_cache_lock = threading.Lock()
CACHE_SECONDS = 60


def user_for_request(token: str | None, lookup_user) -> dict | None:
    """The signed-in, approved user behind a session cookie, or None. Cached for a minute so the
    lock costs one database read per visitor per minute, not one per request."""
    if not token:
        return None
    now = time.monotonic()
    with _cache_lock:
        hit = _allowed_cache.get(token)
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
    user = lookup_user(token)
    if user and not is_allowed(user.get("email")):
        user = None
    with _cache_lock:
        if len(_allowed_cache) > 5000:
            _allowed_cache.clear()
        _allowed_cache[token] = (now, user)
    return user

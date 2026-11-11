"""Private-access rules (crossr_core.access), without a database."""

import pytest

from crossr_core import access


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("CROSSR_OWNER_EMAILS", "Owner@Example.com, second@example.com")
    monkeypatch.setenv("CROSSR_SERVICE_TOKEN", "s3cret-token")
    monkeypatch.delenv("CROSSR_LOCK", raising=False)
    access._allowed_cache.clear()


def test_lock_is_on_unless_switched_off(monkeypatch):
    assert access.lock_enabled()
    monkeypatch.setenv("CROSSR_LOCK", "0")
    assert not access.lock_enabled()


def test_lock_stays_off_until_an_owner_is_configured(monkeypatch):
    monkeypatch.delenv("CROSSR_OWNER_EMAILS")
    assert not access.lock_enabled()  # nobody could approve anyone, so nobody is locked out


def test_owners_are_case_insensitive_and_always_approved():
    assert access.is_owner("owner@example.com") and access.is_owner(" OWNER@example.COM ")
    assert not access.is_owner("someone@example.com") and not access.is_owner(None)
    assert access.status_of("Second@Example.com") == "approved"  # no database needed for owners


def test_service_token_must_match_exactly(monkeypatch):
    assert access.service_token_ok("s3cret-token")
    assert not access.service_token_ok("s3cret-tokenX") and not access.service_token_ok(None)
    monkeypatch.delenv("CROSSR_SERVICE_TOKEN")
    assert not access.service_token_ok("")  # no token configured = no bypass


@pytest.mark.parametrize("path, open_", [
    ("/api/health", True), ("/api/auth/login", True), ("/api/auth/register", True),     ("/api/auth/me/", True), ("/api/stallions", False), ("/api/horses/Curlin", False),
    ("/api/access/requests", False), ("/api/auth/preferences", False), ("/docs", False), ("/openapi.json", False),
])
def test_open_paths(path, open_):
    assert access.api_path_is_open(path) is open_


@pytest.mark.parametrize("raw, cleaned", [
    (" A@B.co ", "a@b.co"), ("not-an-email", None), ("", None), (None, None), ("a@b", None),
])
def test_clean_email(raw, cleaned):
    assert access.clean_email(raw) == cleaned


def test_user_for_request_needs_an_approved_account_and_caches(monkeypatch):
    calls = []
    users = {"t-ok": {"email": "ok@example.com"}, "t-revoked": {"email": "gone@example.com"}}
    monkeypatch.setattr(access, "is_allowed", lambda email, conn=None: email == "ok@example.com")

    def lookup(token):
        calls.append(token)
        return users.get(token)

    assert access.user_for_request("t-ok", lookup) == {"email": "ok@example.com"}
    assert access.user_for_request("t-ok", lookup) == {"email": "ok@example.com"}
    assert calls == ["t-ok"]  # second call served from the cache
    assert access.user_for_request("t-revoked", lookup) is None
    assert access.user_for_request("t-unknown", lookup) is None
    assert access.user_for_request(None, lookup) is None


def test_request_access_rejects_bad_email():
    status, err = access.request_access("nope")
    assert status is None and "valid email" in err
    assert access.request_access("owner@example.com") == ("approved", None)  # owner needs no request


def test_service_token_derived_from_database_password(monkeypatch):
    monkeypatch.delenv("CROSSR_SERVICE_TOKEN")
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:pw%21x@internal.host:5432/railway")
    internal = access.service_token()
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:pw!x@proxy.example.net:41234/railway")
    assert access.service_token() == internal and len(internal) == 64  # same password, any host
    assert access.service_token_ok(internal) and not access.service_token_ok("guess")
    monkeypatch.setenv("DATABASE_URL", "")
    assert access.service_token() == "" and not access.service_token_ok("")

"""One-time guest links (crossr_core/guest_pass.py).

The database flow runs only against a real Postgres (DATABASE_URL set, e.g. crossr_dev):
    .venv/bin/python -m pytest tests/test_guest_pass.py
"""
import os
import re

import pytest

from crossr_core import auth, guest_pass as gp


def test_token_is_hashed_and_names_are_valid_accounts():
    assert gp.hash_token("abc") != "abc" and len(gp.hash_token("abc")) == 64
    assert auth.EMAIL_RE.match(gp.guest_email(12)) and gp.is_guest_email(gp.guest_email(12))
    assert auth.USERNAME_RE.match(gp.guest_username(123456789))
    assert not gp.is_guest_email("someone@gmail.com")


def test_malformed_tokens_are_refused_without_touching_the_database():
    for bad in ("", "short", "x" * 200, "../../etc/passwd-aaaaaaaaaaaaaaaaaaaaaa", "a b" * 10):
        assert gp.redeem(bad) == (None, gp.USED_OR_EXPIRED)


def test_guest_link_is_only_open_api_path_for_redeem():
    from crossr_core import access

    assert access.api_path_is_open("/api/access/guest/redeem")
    assert not access.api_path_is_open("/api/access/guest-passes")


needs_db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs a Postgres DATABASE_URL")


@needs_db
def test_link_works_once_then_revoke_signs_the_guest_out():
    from crossr_core import access

    made = gp.create("pytest guest pass", "pytest")
    assert re.fullmatch(r"/guest/[A-Za-z0-9_-]{20,}", made["link_path"])
    try:
        first, err = gp.redeem(made["token"])
        assert err is None and first["max_age"] == 24 * 3600
        user = auth.user_from_session(first["session_token"])
        assert user and gp.is_guest_email(user["email"]) and not user["is_owner"]
        assert access.is_allowed(user["email"])

        again, err = gp.redeem(made["token"])
        assert again is None and err == gp.USED_OR_EXPIRED

        row = next(r for r in gp.list_passes() if r["id"] == made["id"])
        assert row["status"] == "active"
    finally:
        assert gp.revoke(made["id"])
    assert auth.user_from_session(first["session_token"]) is None
    assert not access.is_allowed(gp.guest_email(made["id"]))
    assert next(r for r in gp.list_passes() if r["id"] == made["id"])["status"] == "revoked"


@needs_db
def test_revoked_link_cannot_be_used():
    made = gp.create("pytest revoked", "pytest")
    gp.revoke(made["id"])
    assert gp.redeem(made["token"]) == (None, gp.USED_OR_EXPIRED)

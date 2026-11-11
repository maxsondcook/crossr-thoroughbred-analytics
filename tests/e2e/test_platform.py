"""Health, accounts and the API's guard rails."""

import time

import pytest


def test_health(api):
    data = api.ok("/api/health")
    assert data == {"ok": True, "backend": "postgres", "sales_index": True}, data


def test_account_round_trip(api):
    from crossr_core import railway_db

    user = f"e2e_{int(time.time())}"
    client = api.client
    try:
        r = client.post("/api/auth/register", json={"email": f"{user}@example.com", "username": user, "password": "e2e-pass-123"})
        assert r.status_code == 200, r.text
        assert client.get("/api/auth/me").json()["user"]["username"] == user
        r = client.post("/api/auth/preferences", json={"starred": ["TAPIT"], "favorites": ["CURLIN"]})
        assert r.status_code == 200 and r.json()["preferences"]["starred"] == ["TAPIT"], r.text
        client.post("/api/auth/logout", json={})
        assert client.get("/api/auth/me").json()["user"] is None
        r = client.post("/api/auth/login", json={"identifier": user, "password": "e2e-pass-123"})
        assert r.status_code == 200 and r.json()["preferences"]["favorites"] == ["CURLIN"], r.text
        assert client.post("/api/auth/login", json={"identifier": user, "password": "wrong-pass-1"}).status_code == 401
        assert client.post("/api/auth/register", json={"email": f"{user}@example.com", "username": user + "x",
                                                       "password": "e2e-pass-123"}).status_code == 400
    finally:
        client.post("/api/auth/logout", json={})
        with railway_db.connect() as conn:
            conn.execute("DELETE FROM users WHERE username = %s", (user,))


@pytest.mark.parametrize("path", ["/sales_data/x.csv", "/api/pedigrees_database.py", "/data/sales_lots.csv"])
def test_data_files_never_served(api, path):
    assert api.client.get(path).status_code == 404


def test_unknown_horse_is_404(api):
    status, _ = api.get("/api/horses/ZZZ%20NOT%20A%20HORSE%20QQQ")
    assert status == 404

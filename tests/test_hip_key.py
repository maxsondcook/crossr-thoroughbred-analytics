"""Hip numbers match whatever the zero-padding (crossr_core.upcoming.hip_key)."""

import pytest

from crossr_core import upcoming


@pytest.mark.parametrize("a, b", [("50", "0050"), ("050", "50"), ("35a", "0035A"), ("0", "0000"), (" 7 ", "7")])
def test_same_hip(a, b):
    assert upcoming.hip_key(a) == upcoming.hip_key(b)


def test_different_hips():
    assert upcoming.hip_key("350") != upcoming.hip_key("0035")
    assert upcoming.hip_key("35A") != upcoming.hip_key("35")


def test_get_lot_ignores_padding(monkeypatch):
    lots = [{"hip": "0049"}, {"hip": "0050"}, {"hip": "0050A"}]
    monkeypatch.setattr(upcoming, "list_lots", lambda catalog_id: lots)
    assert upcoming.get_lot("x", "50")["hip"] == "0050"
    assert upcoming.get_lot("x", "0050")["hip"] == "0050"
    assert upcoming.get_lot("x", "50a")["hip"] == "0050A"
    assert upcoming.get_lot("x", "51") is None

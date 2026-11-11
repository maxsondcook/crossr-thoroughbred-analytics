"""Stallion status corrections and registry scope (crossr_core.stallion_status)."""

from crossr_core import stallion_status as ss

TABLE = {"WOOTTON BASSETT": {"status": "deceased", "foal_year": 2008, "note": "died"},
         "SOME SIRE": {"status": "pensioned", "foal_year": None, "note": None}}


def test_override_beats_the_card():
    assert ss.off_duty("Wootton Bassett (GB)", 2008, TABLE)
    assert ss.off_duty("Some Sire", None, TABLE)
    assert not ss.off_duty("Into Mischief", 2005, TABLE)


def test_namesake_with_another_year_is_not_affected():
    assert not ss.off_duty("Wootton Bassett", 2023, TABLE)
    assert ss.off_duty("Wootton Bassett", None, TABLE)  # no year: the recorded horse


def test_scope_defaults_to_north_america(monkeypatch):
    monkeypatch.delenv("CROSSR_STALLION_SCOPE", raising=False)
    assert ss.scope() == "north_america"
    monkeypatch.setenv("CROSSR_STALLION_SCOPE", "World")
    assert ss.scope() == "world"
    monkeypatch.setenv("CROSSR_STALLION_SCOPE", "mars")
    assert ss.scope() == "north_america"

"""scripts/merge_duplicate_race_horses.py: which row is kept per refno (no database)."""

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "merge_duplicate_race_horses.py"
_spec = importlib.util.spec_from_file_location("merge_duplicate_race_horses", _PATH)
merge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(merge)


def _row(id, key, year="2016", refno="10040120", status="ok", fetched="2026-10-20T07:04:43", starts=14):
    return {"id": id, "pedigree_key": key, "name_key": key.split(" |")[0].split(" (")[0].split(" [")[0],
            "foal_year": year, "status": status, "fetched_at": fetched, "equibase_refno": refno,
            "seasons": 3, "starts": starts}


def test_keeps_dated_key_and_takes_the_freshest_record():
    rows = [
        _row("57988", "CALL PAUL | FRIESAN FIRE | ", fetched="2026-10-20T07:04:43"),
        _row("57989", "CALL PAUL | FRIESAN FIRE | AVANI", fetched="2026-10-20T07:04:57"),
        _row("57991", "CALL PAUL (2016)", fetched="2026-10-19T00:00:00"),
        _row("57990", "CALL PAUL | FRIESAN FIRE | AVANI FORCE", fetched="2026-10-20T07:04:59"),
    ]
    (m,), conflicts = merge.plan_merges(rows)
    assert conflicts == []
    assert m["keep"]["id"] == "57991"
    assert m["source"]["id"] == "57990"
    assert sorted(r["id"] for r in m["drop"]) == ["57988", "57989", "57990"]
    # The source's starts move to the kept id; the other three copies' are deleted.
    assert merge.summarize([m])["race_starts deleted"] == 3 * 14
    assert merge.summarize([m])["records moved"] == 1


def test_bare_key_beats_catalog_variants_and_year_beats_bare():
    rows = [_row("2", "BENS SHOW [dam by SHOWS POCO]", year="1980"), _row("1", "BENS SHOW", year="1980"),
            _row("3", "STAGE MAGIC", year="", refno="9"), _row("4", "STAGE MAGIC | X | Y", refno="9", year="2007")]
    merges, _ = merge.plan_merges(rows)
    assert {m["refno"]: m["keep"]["id"] for m in merges} == {"10040120": "1", "9": "4"}


def test_ok_beats_unfetched_and_different_years_are_conflicts():
    rows = [_row("1", "A (2016)", status="pending", starts=0, fetched=""), _row("2", "A | B | C"),
            _row("5", "Z (2001)", refno="7", year="2001"), _row("6", "Z (2003)", refno="7", year="2003")]
    merges, conflicts = merge.plan_merges(rows)
    assert [m["keep"]["id"] for m in merges] == ["2"]
    assert [c["refno"] for c in conflicts] == ["7"]

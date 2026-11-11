"""Stallion profile statistics that need no database (pure functions over API rows)."""

from __future__ import annotations

import re

from crossr_core.identity import _clean
from crossr_core.sample_db import name_key

_BUCKETS = {"MG1W": "mg1w", "G1W": "g1w", "G2W": "g2w", "G3W": "g3w", "MSW": "sw", "SW": "sw"}
_GRADED = {"MG1W", "G1W", "MGSW", "G2W", "G3W"}
_COLOUR = re.compile(r"\s+(Gr/R|Gr/Ro|Dk B/Br|Dk B/|B/Br|Blk)\.?$", re.I)


def bms_crosses(top_progeny):
    """Best Broodmare Sire Crosses: a stallion's black-type winners grouped by broodmare sire.

    Each horse counts once, under its best class (MG1W, G1W, G2W, G3W; MGSW is a
    multiple graded winner without a G1; MSW/SW count as SW). "gsw" is every
    graded stakes winner (MG1W + G1W + MGSW + G2W + G3W).
    """
    rows: dict[str, dict] = {}
    seen = set()
    for p in top_progeny or []:
        horse = name_key(p.get("name") or "")
        bms = (p.get("broodmare_sire") or "").strip()
        key = _clean(bms)
        if not horse or not key or horse in seen:
            continue
        seen.add(horse)
        row = rows.setdefault(key, {
            "broodmare_sire": _COLOUR.sub("", bms).strip(),
            "mg1w": 0, "g1w": 0, "gsw": 0, "g2w": 0, "g3w": 0, "sw": 0,
        })
        cls = p.get("class")
        if cls in _BUCKETS:
            row[_BUCKETS[cls]] += 1
        if cls in _GRADED:
            row["gsw"] += 1
    out = list(rows.values())
    out.sort(key=lambda r: (-r["mg1w"], -r["g1w"], -r["gsw"], -r["sw"], r["broodmare_sire"].lower()))
    return out

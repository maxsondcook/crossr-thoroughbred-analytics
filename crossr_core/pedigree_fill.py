"""Fill gaps in a displayed pedigree from the ancestors' own stored pedigrees.

A sale horse's stored tree often comes from its catalog page and stops at four
generations on some branches (e.g. Classy Act's tree ends at Country Romance),
while her sire's own tree (Into Mischief) goes six deep. ``fill_gaps`` walks the
tree and, wherever a branch stops short of the generations shown, copies the
missing parents from that ancestor's own record. Existing entries are never
replaced, and a record is only used when it is the same horse (name, and
foaling year when both sides have one), so a namesake cannot slip in.
"""

from __future__ import annotations

import re
from typing import Callable, Dict, Optional

_YEAR = re.compile(r"\((\d{4})\)\s*$")
_PLACEHOLDERS = {"", "UNKNOWN", "NOT FOUND"}


def _year(name: str) -> Optional[str]:
    m = _YEAR.search(str(name or ""))
    return m.group(1) if m else None


def same_horse(a: str, b: str, clean: Callable[[str], str]) -> bool:
    na, nb = clean(a or ""), clean(b or "")
    if not na or na != nb:
        return False
    ya, yb = _year(a), _year(b)
    return not ya or not yb or ya == yb


def _has(node) -> bool:
    return isinstance(node, dict) and str(node.get("Horse") or "").strip().upper() not in _PLACEHOLDERS


def complete(node, levels: int) -> bool:
    """True when every line below `node` reaches `levels` generations."""
    if levels <= 0:
        return True
    return all(_has(node.get(side)) and complete(node[side], levels - 1) for side in ("Sire", "Dam"))


def _merge(node: dict, other: dict, clean) -> dict:
    out = dict(node)
    for side in ("Sire", "Dam"):
        mine, theirs = out.get(side), other.get(side)
        if not _has(theirs):
            continue
        if not _has(mine):
            out[side] = theirs
        elif same_horse(mine["Horse"], theirs["Horse"], clean):
            out[side] = _merge(mine, theirs, clean)
    return out


def fill_gaps(tree, generations: int, lookup: Callable[[str], dict], clean: Callable[[str], str]):
    """Return `tree` with missing ancestors (down to `generations` below it) filled from `lookup`.

    `lookup(name)` returns that horse's stored tree or {} (missing / ambiguous name).
    """
    cache: Dict[str, dict] = {}

    def own(name: str) -> dict:
        if name not in cache:
            found = lookup(name) or {}
            ok = isinstance(found, dict) and same_horse(found.get("Horse") or name, name, clean)
            cache[name] = found if ok else {}
        return cache[name]

    def walk(node, levels: int):
        if levels <= 0 or not _has(node):
            return node
        if not complete(node, levels):
            other = own(node["Horse"])
            if other:
                node = _merge(node, other, clean)
        out = dict(node)
        for side in ("Sire", "Dam"):
            if _has(out.get(side)):
                out[side] = walk(out[side], levels - 1)
        return out

    return walk(tree, generations)

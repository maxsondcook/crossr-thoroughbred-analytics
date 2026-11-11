"""Single entry point for the pedigree map: the ``pedigrees`` table in Postgres.

Callers get a {key: tree} dict (a CompactPedigreeMap in the API). The old
pedigrees_database.py file is only read by scripts/load_railway.py.
"""

from __future__ import annotations

import json
import os
import threading
from collections import OrderedDict

_lock = threading.RLock()


def compact_enabled() -> bool:
    """The API turns this on (crossr_api.app); the CLI keeps plain dicts it can edit."""
    return os.environ.get("CROSSR_COMPACT_PEDIGREES", "").lower() in {"1", "true", "yes"}


def encode_tree(tree) -> bytes:
    return json.dumps(tree, separators=(",", ":")).encode()


class CompactPedigreeMap(dict):
    """{key: tree} that stores each tree as compact JSON bytes.

    39k pedigrees are ~450 MB as Python dicts but ~46 MB as JSON. Trees are
    decoded on access and the most recent ones are kept decoded (LRU), so the
    hot horses cost nothing extra. Lookups, ``in``, ``len`` and key iteration
    behave like a dict; ``items()``/``values()`` decode one tree at a time.
    Treat returned trees as read-only: edits to an evicted tree are lost.
    """

    def __init__(self, encoded=None, cache_size: int = 2048):
        super().__init__()
        self._decoded: OrderedDict = OrderedDict()
        self._cache_size = cache_size
        self._cache_lock = threading.Lock()
        if encoded:
            for key, raw in encoded.items():
                dict.__setitem__(self, key, raw)

    def _decode(self, key, raw):
        with self._cache_lock:
            tree = self._decoded.get(key)
            if tree is not None:
                self._decoded.move_to_end(key)
                return tree
        tree = json.loads(raw)
        with self._cache_lock:
            self._decoded[key] = tree
            if len(self._decoded) > self._cache_size:
                self._decoded.popitem(last=False)
        return tree

    def raw(self, key):
        return dict.__getitem__(self, key)

    def set_raw(self, key, raw: bytes) -> None:
        dict.__setitem__(self, key, raw)
        with self._cache_lock:
            self._decoded.pop(key, None)

    def __getitem__(self, key):
        return self._decode(key, dict.__getitem__(self, key))

    def get(self, key, default=None):
        raw = dict.get(self, key)
        return default if raw is None else self._decode(key, raw)

    def __setitem__(self, key, tree):
        self.set_raw(key, encode_tree(tree))

    def setdefault(self, key, default=None):
        if key not in self:
            self[key] = default
        return self[key]

    def update(self, other=(), **kwargs):
        pairs = other.items() if hasattr(other, "items") else other
        for key, tree in pairs:
            self[key] = tree
        for key, tree in kwargs.items():
            self[key] = tree

    def pop(self, key, *default):
        with self._cache_lock:
            self._decoded.pop(key, None)
        if key not in self and default:
            return default[0]
        return self._decode(key, dict.pop(self, key))

    def __delitem__(self, key):
        with self._cache_lock:
            self._decoded.pop(key, None)
        dict.__delitem__(self, key)

    def values(self):
        return (self[key] for key in dict.keys(self))

    def items(self):
        return ((key, self[key]) for key in dict.keys(self))

    def copy(self):
        out = CompactPedigreeMap(cache_size=self._cache_size)
        for key in dict.keys(self):
            dict.__setitem__(out, key, dict.__getitem__(self, key))
        return out
_cache: dict | None = None
_cache_version: float | None = None


def get_pedigree_map(refresh: bool = False) -> dict:
    """Return the shared pedigree map (treat as read-only; copy before mutating).

    Reloads when the pedigrees table version changes.
    """
    global _cache, _cache_version
    from crossr_core import railway_db

    with _lock:
        version = railway_db.table_version("pedigrees", max_age=0 if refresh else None)
        if _cache is None or refresh or version != _cache_version:
            _cache = None  # let the old copy go before reading the new one
            _cache = railway_db.read_pedigree_map(compact=compact_enabled())
            _cache_version = version
        return _cache


def sync_to_railway(items: dict) -> int:
    """Upsert trees into the pedigrees table."""
    if not items:
        return 0
    from crossr_core import railway_db

    return railway_db.upsert_pedigrees(items)

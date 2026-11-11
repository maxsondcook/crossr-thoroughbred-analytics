"""Saved hypothetical matings ({cross key: combined pedigree tree}) in Postgres.

Stored in the ``hypofoals`` table (crossr_core.railway_db). The old hypofoals.py
file is only read once by scripts/load_railway.py to fill that table.
"""

import threading

_lock = threading.Lock()
_cache = None
_cache_version = None


def clean_hypo_key(sire_name: str, dam_name: str) -> str:
    """Normalizes the cross key names accurately for consistent dictionary mappings."""
    combined = f"{sire_name} x {dam_name}".lower()
    cleaned = combined.replace("’", "").replace("'", "").replace(".", "").replace("-", "")
    cleaned = cleaned.replace("(", " ").replace(")", " ")
    return " ".join(cleaned.split()).strip()


def load_hypo_database() -> dict:
    """All saved matings (treat as read-only). Reloads when the table changes."""
    global _cache, _cache_version
    from crossr_core import railway_db

    version = railway_db.table_version("hypofoals")
    with _lock:
        if _cache is None or version != _cache_version:
            _cache = railway_db.read_pedigree_map(table="hypofoals")
            _cache_version = version
        return _cache


def register_new_hypo_foal(sire_name: str, dam_name: str, combined_tree: dict):
    """Save (or replace) one mating."""
    from crossr_core import railway_db

    railway_db.upsert_pedigrees({clean_hypo_key(sire_name, dam_name): combined_tree}, table="hypofoals")

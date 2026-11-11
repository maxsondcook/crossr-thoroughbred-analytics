import re
import copy
import hypomating_engine
import placeholder_store

# NAME CLEANING
_COLOR_SUFFIX = re.compile(r'\s+(?:GR/RO|GR/R|DK B/BR|B/BR)\s*$', re.I)


def clean_horse_name(name: str) -> str:
    """
    Normalizes horse names accurately for absolute dictionary matches.
    Transforms things like 'A.P. Indy (1989)' -> 'A P INDY' and strips out punctuation/spaces.
    """
    if not name or not isinstance(name, str):
        return ""
    # Strip any trailing parenthetical years or country suffixes
    cleaned = re.sub(r'\(.*?\)', '', name)
    # Some pedigree labels carry the coat colour: 'DARK ANGEL GR/R (2005)', 'X DK B/BR'.
    if "/" in cleaned:
        cleaned = _COLOR_SUFFIX.sub('', cleaned)
    # Strip dots, punctuation, and enforce clean spacing
    cleaned = cleaned.replace(".", "").replace("'", "").replace(",", "")
    cleaned = " ".join(cleaned.split())
    # Standardize completely to upper-case
    return cleaned.strip().upper()


def foaling_year(name: str) -> str:
    """Four-digit year inside parentheses: 'VATICANA (2003)' -> '2003'."""
    if not name or not isinstance(name, str):
        return ""
    m = re.search(r"\((\d{4})\)", name)
    return m.group(1) if m else ""


def years_for_name(name, pedigree_db=None):
    """Distinct foaling years for a cleaned name from keys and Horse labels."""
    return lookup_pedigree(name, pedigree_db)["years"]


_CLEAN_KEY_INDEX = {}


def _keys_for_clean_name(pedigree_db, clean):
    """Index of clean_horse_name(key) -> [keys], rebuilt when the map's size changes.

    lookup_pedigree used to run clean_horse_name over all ~78k keys per call;
    a hypomating report makes thousands of lookups.
    """
    cached = _CLEAN_KEY_INDEX.get(id(pedigree_db))
    if cached is None or cached[0] is not pedigree_db or cached[1] != len(pedigree_db):
        index = {}
        for key in list(pedigree_db):
            index.setdefault(clean_horse_name(key), []).append(key)
        cached = (pedigree_db, len(pedigree_db), index)
        if len(_CLEAN_KEY_INDEX) >= 4:  # don't pin replaced or temporary maps in memory
            _CLEAN_KEY_INDEX.clear()
        _CLEAN_KEY_INDEX[id(pedigree_db)] = cached
    return cached[2].get(clean, ())


def lookup_pedigree(name, pedigree_db=None):
    """Resolve a horse name to a pedigree tree without prompting.

    Returns a dict:
      tree, key, years, ambiguous, query_year, clean_name
    Name-only queries stay unambiguous when only one year exists.
    A year already in the query (e.g. 'VATICANA (2003)') is used as-is.
    """
    if pedigree_db is None:
        pedigree_db = PEDIGREE_DATABASE if "PEDIGREE_DATABASE" in globals() else {}
    clean = clean_horse_name(name)
    query_year = foaling_year(name)
    empty = {
        "tree": None,
        "key": None,
        "years": [],
        "ambiguous": False,
        "query_year": query_year,
        "clean_name": clean,
    }
    if not clean or not isinstance(pedigree_db, dict):
        return empty

    by_year = {}
    yearless = []
    # Keys sharing this cleaned name, in dict order. Trees may be stored compactly
    # and decoded on access, so only matching keys are read.
    for key in _keys_for_clean_name(pedigree_db, clean):
        tree = pedigree_db.get(key)
        tree = tree if isinstance(tree, dict) else {}
        year = foaling_year(key) or foaling_year(tree.get("Horse", ""))
        if year:
            by_year.setdefault(year, (key, tree))
        else:
            yearless.append((key, tree))

    years = sorted(by_year)
    if query_year:
        if query_year in by_year:
            key, tree = by_year[query_year]
            return {
                "tree": tree,
                "key": key,
                "years": years,
                "ambiguous": False,
                "query_year": query_year,
                "clean_name": clean,
            }
        year_key = f"{clean} ({query_year})"
        if year_key in pedigree_db and isinstance(pedigree_db[year_key], dict):
            return {
                "tree": pedigree_db[year_key],
                "key": year_key,
                "years": years,
                "ambiguous": False,
                "query_year": query_year,
                "clean_name": clean,
            }
        return {**empty, "years": years}

    if len(years) >= 2:
        return {
            "tree": None,
            "key": None,
            "years": years,
            "ambiguous": True,
            "query_year": "",
            "clean_name": clean,
        }

    if len(years) == 1:
        key, tree = by_year[years[0]]
        return {
            "tree": tree,
            "key": key,
            "years": years,
            "ambiguous": False,
            "query_year": years[0],
            "clean_name": clean,
        }

    if yearless:
        key, tree = yearless[0]
        return {
            "tree": tree,
            "key": key,
            "years": [],
            "ambiguous": False,
            "query_year": "",
            "clean_name": clean,
        }

    if clean in pedigree_db and isinstance(pedigree_db[clean], dict):
        return {
            "tree": pedigree_db[clean],
            "key": clean,
            "years": [],
            "ambiguous": False,
            "query_year": "",
            "clean_name": clean,
        }
    return empty


def pedigree_tree(name, pedigree_db=None):
    """Silent tree lookup. Empty dict if missing or year-ambiguous."""
    hit = lookup_pedigree(name, pedigree_db)
    if hit["ambiguous"] or not hit["tree"]:
        return {}
    return hit["tree"]


def resolve_pedigree(name, pedigree_db=None, prompt=False):
    """Look up a pedigree (``prompt`` is ignored; the terminal menus are retired)."""
    if pedigree_db is None:
        pedigree_db = PEDIGREE_DATABASE if "PEDIGREE_DATABASE" in globals() else {}
    hit = dict(lookup_pedigree(name, pedigree_db))
    hit["cancelled"] = False
    return hit


# UPPERCASE NAMES
def load_and_sync_all_data():
    """
    Reads the pedigree map from Postgres (crossr_core.pedigree_store)
    and returns a clean dictionary optimized with uppercase keys.
    """
    combined_database = {}

    try:
        from crossr_core import pedigree_store

        db_map = pedigree_store.get_pedigree_map(refresh=True)
        if isinstance(db_map, pedigree_store.CompactPedigreeMap):
            # API: share the stored JSON bytes instead of deep-copying every tree.
            combined_database = pedigree_store.CompactPedigreeMap()
            for k in db_map:
                v = db_map[k]
                if not (isinstance(v, dict) and "Horse" in v):
                    continue
                raw = db_map.raw(k)
                clean = clean_horse_name(k)
                year = foaling_year(k) or foaling_year(v.get("Horse", ""))
                if year:
                    combined_database.set_raw(f"{clean} ({year})", raw)
                if clean not in combined_database:
                    combined_database.set_raw(clean, raw)
            return combined_database
        if isinstance(db_map, dict):
            for k, v in db_map.items():
                if not (isinstance(v, dict) and "Horse" in v):
                    continue
                clean = clean_horse_name(k)
                year = foaling_year(k) or foaling_year(v.get("Horse", ""))
                payload = copy.deepcopy(v)
                if year:
                    combined_database[f"{clean} ({year})"] = payload
                if clean not in combined_database:
                    combined_database[clean] = payload
    except Exception:
        pass

    return combined_database


# Initialize Global Pedigree Map Cache
PEDIGREE_DATABASE = load_and_sync_all_data()

# Inject common alias maps to guarantee flawless query resolutions
aliases = {
    "CANDY RIDE (ARG)": "CANDY RIDE",
    "LIAMS MAP": "LIAM'S MAP",
    "BOLT DORO": "BOLT D'ORO",
    "MACLEANS MUSIC": "MACLEAN'S MUSIC",
    "HONOR A P": "HONOR A. P.",
    "JACKIES WARRIOR": "JACKIE'S WARRIOR"
}
for alias_key, standard_key in aliases.items():
    norm_std = clean_horse_name(standard_key)
    if norm_std in PEDIGREE_DATABASE:
        PEDIGREE_DATABASE[clean_horse_name(alias_key)] = PEDIGREE_DATABASE[norm_std]


# ==============================================================================
# INBREEDING ANALYSIS
# ==============================================================================
def compute_inbreeding(horse_dict):
    """
    Parses a 5-generation pedigree dictionary to track ancestor locations,
    computes consolidated cross-matching (Sire x Dam) using an uppercase layout format,
    and flags single-side linebreeding (Sire-side or Dam-side duplicates).
    Returns a JSON-serializable report (no printing).
    """
    sire_occurrences = {}
    dam_occurrences = {}
    
    sire_paths = {}
    dam_paths = {}

    def traverse(node, current_depth, side_map, path_str, path_dict):
        if not node or not isinstance(node, dict) or current_depth > 5:
            return
        
        raw_name = node.get("Horse", "").strip()
        if not raw_name or "UNKNOWN" in raw_name.upper():
            return
            
        display_name = re.sub(r'\s*\(.*?\)', '', raw_name).strip().upper()
        
        if display_name not in side_map:
            side_map[display_name] = []
        side_map[display_name].append(current_depth)
        
        path_dict[path_str] = display_name
        
        db_lookup = pedigree_tree(raw_name, PEDIGREE_DATABASE)
        sire_branch = node.get("Sire") or db_lookup.get("Sire")
        dam_branch = node.get("Dam") or db_lookup.get("Dam")
        
        traverse(sire_branch, current_depth + 1, side_map, f"{path_str}.S", path_dict)
        traverse(dam_branch, current_depth + 1, side_map, f"{path_str}.D", path_dict)

    db_root = pedigree_tree(horse_dict.get("Horse", ""), PEDIGREE_DATABASE)
    sire_root = horse_dict.get("Sire") or db_root.get("Sire")
    dam_root = horse_dict.get("Dam") or db_root.get("Dam")

    traverse(sire_root, 1, sire_occurrences, "S", sire_paths)
    traverse(dam_root, 1, dam_occurrences, "D", dam_paths)

    cross_inbred_results = []
    sire_side_linebred = []
    dam_side_linebred = []
    coefficient_total = 0.0

    shared_ancestors = set(sire_occurrences.keys()) & set(dam_occurrences.keys())

    cross_inbred_sire_paths = {path for path, name in sire_paths.items() if name in shared_ancestors}
    cross_inbred_dam_paths = {path for path, name in dam_paths.items() if name in shared_ancestors}

    for ancestor_name, s_gens in sorted(sire_occurrences.items()):
        if ancestor_name in dam_occurrences:
            d_gens = dam_occurrences[ancestor_name]
            
            this_s_paths = [p for p, name in sire_paths.items() if name == ancestor_name]
            this_d_paths = [p for p, name in dam_paths.items() if name == ancestor_name]
            
            prune_sire_side = False
            for path in this_s_paths:
                child_path = ".".join(path.split(".")[:-1])
                if child_path in cross_inbred_sire_paths:
                    prune_sire_side = True
                    break
                    
            prune_dam_side = False
            for path in this_d_paths:
                child_path = ".".join(path.split(".")[:-1])
                if child_path in cross_inbred_dam_paths:
                    prune_dam_side = True
                    break
            
            if prune_sire_side and prune_dam_side:
                continue

            s_str = " x ".join([f"{g}S" for g in sorted(s_gens)])
            d_str = " x ".join([f"{g}D" for g in sorted(d_gens)])
            consolidated_layout = f"{s_str} x {d_str}"
            cross_inbred_results.append(f"{ancestor_name}: {consolidated_layout}")
            
            for s_gen in s_gens:
                for d_gen in d_gens:
                    coefficient_total += (0.5) ** (s_gen + d_gen)

    for ancestor_name, s_gens in sire_occurrences.items():
        if len(s_gens) > 1:
            gens_str = ", ".join([f"{g}S" for g in sorted(s_gens)])
            sire_side_linebred.append(f"{ancestor_name} ({gens_str})")

    for ancestor_name, d_gens in dam_occurrences.items():
        if len(d_gens) > 1:
            gens_str = ", ".join([f"{g}D" for g in sorted(d_gens)])
            dam_side_linebred.append(f"{ancestor_name} ({gens_str})")

    return {
        "cross_inbreeding": cross_inbred_results,
        "sire_side_linebreeding": sire_side_linebred,
        "dam_side_linebreeding": dam_side_linebred,
        "coefficient": coefficient_total,
        "coefficient_pct": round(coefficient_total * 100, 4),
    }


# ==============================================================================
# STALLION PROFILES
# ==============================================================================
def load_master_profiles():
    """Load stallion display cards from the stud_fees table (+ ranks/breeding)."""
    try:
        return placeholder_store.load_stallion_profiles()
    except Exception:
        return {}

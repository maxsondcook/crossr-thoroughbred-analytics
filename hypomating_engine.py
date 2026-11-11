import datetime
import sys
import copy
import re
import main
import hypofoals_manager
import placeholder_store
from graded_stakes import graded_level, is_black_type, load_stakes_data

# Broodmare sire ranks from data/stallion_rankings.csv (Broodmare list).
BROODMARE_DATABASE = placeholder_store.load_broodmare_database()
clean_horse_name = placeholder_store.broodmare_clean_horse_name

RAW_RANKING_ORDER = [
    "Not This Time",       # 1
    "Into Mischief",       # 2
    "Gun Runner",          # 3
    "Nyquist",             # 4
    "Curlin",              # 5
    "Constitution",        # 6
    "Practical Joke",      # 7
    "Munnings",            # 8
    "Liam's Map",          # 9
    "Uncle Mo",            # 10
    "Vekoma",              # 11
    "American Pharoah",    # 12
    "Tiz the Law",         # 13
    "Omaha Beach",         # 14
    "Maclean's Music",     # 15
    "Twirling Candy",      # 16
    "Justify",             # 17
    "Quality Road",        # 18
    "Mendelssohn",         # 19
    "Candy Ride (ARG)",    # 20
    "Oscar Performance",   # 21
    "Connect",             # 22
    "Hard Spun",           # 23
    "Bolt d'Oro",          # 24
    "Vino Rosso",          # 25
    "Speightstown",        # 26
    "Medaglia d'Oro",      # 27
    "Goldencents",         # 28
    "City of Light",       # 29
    "Mitole",              # 30
    "Frosted",             # 31
    "Street Sense",        # 32
    "Audible",             # 33
    "Volatile",            # 34
    "Authentic",           # 35
    "Mo Town",             # 36
    "Cairo Prince",        # 37
    "Yaupon",              # 38
    "Flameaway",           # 39
    "Honor A. P.",         # 40
    "Violence",            # 41
    "Collected",           # 42
    "Midshipman",          # 43
    "Ghostzapper",         # 44
    "Kantharos",           # 45
    "Exaggerator",         # 46
    "Tapit",               # 47
    "McKinzie",            # 48
    "Dialed In",           # 49
    "Tapiture",            # 50
    "Upstart",             # 51
    "Good Magic",          # 52
    "Maximus Mischief",    # 53
    "Essential Quality",   # 54
    "Charlatan",           # 55
    "Girvin",              # 56
    "The Big Beast",       # 57
    "Solomini",            # 58
    "Union Rags",          # 59
    "Blame",               # 60
    "Instagrand",          # 61
    "Runhappy",            # 62
    "Army Mule",           # 63
    "Maxfield",            # 64
    "Cloud Computing",     # 65
    "Bucchero",            # 66
    "The Factor",          # 67
    "More Than Ready",     # 68
    "Stay Thirsty",        # 69
    "Complexity",          # 70
    "Jimmy Creed",         # 71
    "Tacitus",             # 72
    "Great Notion",        # 73
    "Grazen",              # 74
    "Cupid",               # 75
    "Star Guitar",         # 76
    "Khozan",              # 77
    "Clubhouse Ride",      # 78
    "Awesome Slew",        # 79
    "War of Will",         # 80
    "Tonalist",            # 81
    "War Front",           # 82
    "Kitten's Joy",        # 83
    "Neolithic",           # 84
    "Take Charge Indy",    # 85
    "Golden Lad",          # 86
    "Blofeld",             # 87
    "Karakontie (JPN)",    # 88
    "Rock Your World",     # 89
    "Central Banker",      # 90
    "Enticed",             # 91
    "Beau Liam",           # 92
    "Paynter",             # 93
    "Aurelius Maximus",    # 94
    "Sergei Prokofiev",    # 95
    "Spun to Run",         # 96
    "Sir Prancealot (IRE)",# 97
    "Unified",             # 98
    "Adios Charlie",       # 99
    "Maximum Security",    # 100
    "Summer Front",        # 101
    "Independence Hall",   # 102
    "West Coast",          # 103
    "Accelerate",          # 104
    "Preservationist",     # 105
    "Echo Town",           # 106
    "Warrior's Reward",    # 107
    "Speightster",         # 108
    "American Freedom",    # 109
    "Uncle Lino",          # 110
    "Smiling Tiger",       # 111
    "Thousand Words",      # 112
    "Game Winner",         # 113
    "Improbable",          # 114
    "Coal Front",          # 115
    "Distorted Humor",     # 116
    "Mastery",             # 117
    "Outwork",             # 118
    "Mucho Macho Man",     # 119
    "Cajun Breeze",        # 120
    "Modernist",           # 121
    "Tapwrit",             # 122
    "Gormley",             # 123
    "Always Dreaming",     # 124
    "Om",                  # 125
    "Honest Mischief",     # 126
    "Keen Ice",            # 127
    "Good Samaritan",      # 128
    "Marking",             # 129
    "Stanford",            # 130
    "Pavel",               # 131
    "Flat Out",            # 132
    "Cinco Charlie",       # 133
    "Valiant Minister",    # 134
    "Souper Speedy",       # 135
    "Win Win Win",         # 136
    "Hoppertunity",        # 137
    "El Deal",             # 138
    "Divining Rod",        # 139
    "Cross Traffic",       # 140
    "Street Strategy",     # 141
    "Caracaro",            # 142
    "Weigelia",            # 143
    "Tom's d'Etat",        # 144
    "Catholic Boy",        # 145
    "Temple City",         # 146
    "Competitive Edge",    # 147
    "I'll Have Another",   # 148
    "Midnight Lute",       # 149
    "Lexitonian"           # 150
]

SIRE_PARENTS = {
    "storm bird": "northern dancer",
    "storm cat": "storm bird",
    "harlan": "storm cat",
    "harlan's holiday": "harlan",
    "into mischief": "harlan's holiday",
    "barnes": "into mischief",
    "instagrand": "into mischief",
    "newgate": "into mischief",
    "audible": "into mischief",
    "life is good": "into mischief",
    "authentic": "into mischief",
    "highly motivated": "into mischief",
    "timberlake": "into mischief",
    "patch adams": "into mischief",
    "citizen bull": "into mischief",
    "goldencents": "into mischief",
    "by my standards": "goldencents",
    "mystik dan": "goldencents",
    "maximus mischief": "into mischief",
    "raging torrent": "maximus mischief",
    "practical joke": "into mischief",
    "domestic product": "practical joke",
    "tale of the cat" : "storm cat",
    "lion heart": "tale of the cat",
    "kantharos": "lion heart",
    "tale of ekati": "tale of the cat",
    "girvin": "tale of ekati",
    "hennessy": "storm cat",
    "johannesburg": "hennessy",
    "scat daddy": "johannesburg",
    "justify": "scat daddy",
    "arabian lion": "justify",
    "heartland": "justify",
    "giant's causeway": "storm cat",
    "eskendereya" : "giant's causeway",
    "mitole": "eskendereya",
    "shmardal" : "giant's causeway",
    "lope de vega": "shmardal",
    "carl spackler": "lope de vega",    
    "not this time" : "giant's causeway",
    "epicenter" : "not this time",
    "up to the mark" : "not this time",
    "cogburn" : "not this time",
    "bernstein": "storm cat",
    "karakontie": "bernstein",
    "forestry": "storm cat",
    "shackleford": "forestry",
    "promises fulfilled": "shackleford",
    "dixieland band": "northern dancer",
    "union rags": "dixieland band",
    "sadler's wells": "northern dancer",
    "el prado": "sadler's wells",
    "kitten's joy": "el prado",
    "divisidero": "kitten's joy",
    "bolt d'oro": "medaglia d'oro",
    "violence": "medaglia d'oro",
    "forte": "violence",
    "volatile": "violence",
    "dr. schivel": "violence",
    "mullikin": "violence",
    "danzig": "northern dancer",
    "war front": "danzig",
    "war command": "war front", 
    "omaha beach": "war front",
    "annapolis": "war front",
    "hard spun": "danzig",
    "silver state": "hard spun",
    "two phil's": "hard spun",
    "vice regent": "northern dancer",
    "deputy minister": "vice regent",
    "ghostzapper": "deputy minister",
    "mystic guide": "ghostzapper",
    "loggins": "ghostzapper",
    "a.p. indy": "seattle slew",
    "pulpit": "a.p. indy",
    "tapit": "pulpit",
    "essential quality": "tapit",
    "tacitus": "tapit",
    "arthur's ride": "tapit",
    "greatest honor": "tapit",
    "tapit trice": "tapit",
    "charge it": "tapit",
    "proxy": "tapit",
    "flightline": "tapit",
    "tonalist": "tapit",
    "country grammer": "tonalist",
    "constitution": "tapit",
    "tiz the law": "constitution",
    "mindframe": "constitution",
    "independence hall": "constitution",
    "take charge indy": "a.p. indy",
    "flatter": "a.p. indy",
    "upstart": "flatter",
    "zandon": "upstart",
    "honor code": "a.p. indy",
    "goal oriented": "honor code",
    "honor a.p.": "honor code",
    "friesan fire": "a.p. indy",
    "army mule": "friesan fire",
    "mineshaft": "a.p. indy",
    "senor buscador": "mineshaft",
    "fappiano": "mr prospector",
    "cryptoclearance": "fappiano",
    "ride the rails": "cryptoclearance",
    "candy ride": "ride the rails",
    "twirling candy": "candy ride",
    "rocky your world": "candy ride",
    "vekoma": "candy ride",
    "jonathan's way": "vekoma",
    "gun runner": "candy ride",
    "taiba": "gun runner",
    "gunite": "gun runner",
    "sierra leone": "gun runner",
    "cyberknife": "gun runner",
    "early voting": "gun runner",
    "gun pilot": "gun runner",
    "locked": "gun runner",
    "unbridled": "fappiano",
    "empire maker": "unbridled",
    "pioneerof the nile": "empire maker",
    "classic empire": "pioneerof the nile",
    "angel of empire": "classic empire",
    "cairo prince": "pioneerof the nile",
    "thousand words": "pioneerof the nile",
    "American Pharoah": "pioneerof the nile",
    "unbridled's song": "unbridled",
    "midshipman": "unbridled's song",
    "arrogate": "unbridled's song",
    "seize the grey": "arrogate",
    "arcangelo": "arrogate",
    "liam's map": "unbridled's song",
    "beau liam": "liam's map",
    "basin": "liam's map",
    "smart strike": "mr prospector",
    "curlin": "smart strike",
    "idol": "curlin",
    "highland falls": "curlin",
    "bright future": "curlin",
    "cody's wish": "curlin",
    "connect": "curlin",
    "known agenda": "curlin",
    "vino rosso": "curlin",
    "good magic": "curlin",
    "mage": "good magic",
    "dornoch": "good magic",
    "muth": "good magic",
    "forty niner": "mr prospector",
    "gone west": "forty niner",
    "speightstown": "gone west",
    "charlatan": "speightstown",
    "prince of monaco": "speightstown",
    "nashville": "speightstown",
    "olympiad": "speightstown",
    "munnings": "speightstown",
    "jack christopher": "munnings",
    "speightster": "speightstown",
    "straight no chaser": "speightster",
    "elusive quality": "gone west",
    "quality road": "elusive quality",
    "corniche": "quality road",
    "national treasure": "quality road",
    "city of light": "quality road",
    "fierceness": "city of light",
    "distorted humor": "forty niner",
    "jimmy creed": "distorted humor",
    "maclean's music": "distorted humor",
    "complexity": "maclean's music",
    "drain the clock": "maclean's music",
    "jackie's warrior": "maclean's music",
    "machiavellian": "mr prospector",
    "street cry": "machiavellian",
    "street sense": "street cry",
    "maxfield": "street sense",
    "speaker's corner": "street sense",
    "first mission": "street sense",
    "mckinzie": "street sense",
    "chancer mcpatrick": "mckinzie"   
}

SEX_MAP = {
    'c': 'colt', 'f': 'filly', 'r': 'ridgling', 
    'g': 'gelding', 'm': 'mare', 'h': 'horse'
}

# SIRE_PARENTS keyed by cleaned name ("harlan's holiday" -> "HARLANS HOLIDAY"), the form every
# lookup uses. Matching the raw keys directly missed every name with an apostrophe or capitals.
# Built on first use: main imports this module before defining clean_horse_name.
_SIRE_MAPS = None


def _sire_maps():
    """(cleaned son -> cleaned sire, cleaned name -> name as written in SIRE_PARENTS)."""
    global _SIRE_MAPS
    if _SIRE_MAPS is None:
        clean = main.clean_horse_name
        _SIRE_MAPS = (
            {clean(son): clean(sire) for son, sire in SIRE_PARENTS.items()},
            {clean(name): name for pair in SIRE_PARENTS.items() for name in pair},
        )
    return _SIRE_MAPS


def get_sire_line_ancestry(horse_name, node=None):
    """Cleaned names up the male line: the horse, his sire, sire's sire, ...

    Walks the horse's own pedigree (or ``node``, e.g. the damsire's branch of a
    mare's tree when he has no record of his own), then continues through the
    hand-kept SIRE_PARENTS map past the tree's top.
    """
    ancestry = []
    own = main.pedigree_tree(horse_name)
    if not (isinstance(own, dict) and own.get("Sire")) and isinstance(node, dict) and node.get("Sire"):
        own = node
    node = own or {"Horse": horse_name}
    while isinstance(node, dict) and node.get("Horse"):
        clean = main.clean_horse_name(node["Horse"])
        if not clean or "UNKNOWN" in clean or clean in ancestry:
            break
        ancestry.append(clean)
        node = node.get("Sire")
        if isinstance(node, str):
            node = {"Horse": node}
    parents, _ = _sire_maps()
    current = ancestry[-1] if ancestry else main.clean_horse_name(horse_name)
    if not ancestry and current:
        ancestry.append(current)
    while current in parents:
        current = parents[current]
        if current in ancestry:
            break
        ancestry.append(current)
    return ancestry

def find_lineage_relatives(stallion_clean):
    """
    Finds the stallion's immediate sire, paternal half-brothers,
    and all generations of deep sons/descendants (names as written in SIRE_PARENTS).
    """
    parents, raw_names = _sire_maps()
    target = main.clean_horse_name(stallion_clean)
    relatives = set()

    # 1. Find the immediate Sire
    immediate_sire = parents.get(target, "")
    if immediate_sire:
        relatives.add(immediate_sire)

        # 2. Find Paternal Half-Brothers (Sires sharing the same father)
        for son, parent in parents.items():
            if parent == immediate_sire and son != target:
                relatives.add(son)

    # 3. Find Sons and Deep Descendants (Recursive helper)
    def get_all_sons(parent_name, seen):
        sons = []
        for son, parent in parents.items():
            if parent == parent_name and son not in seen:
                seen.add(son)
                sons.append(son)
                sons.extend(get_all_sons(son, seen))
        return sons

    relatives.update(get_all_sons(target, {target}))
    relatives.discard(target)
    return [raw_names.get(name, name.lower()) for name in relatives]

def get_mare_sire_from_pedigree(pedigree_dict):
    if not pedigree_dict or not isinstance(pedigree_dict, dict):
        return ""
    sire_node = pedigree_dict.get("Sire")
    if sire_node and isinstance(sire_node, dict):
        return sire_node.get("Horse", "")
    elif isinstance(sire_node, str):
        return sire_node
    return ""

def _is_winning_place(record):
    """Treat missing Place as a winner (old winner-only files). Otherwise require 1."""
    place = record.get("Place", 1)
    if place in ("", None):
        return True
    try:
        return int(float(place)) == 1
    except (TypeError, ValueError):
        return False


def _surface_from_distance(dist_str):
    upper = str(dist_str or "").upper()
    if "(D)" in upper:
        return "dirt"
    if "(T)" in upper:
        return "turf"
    if "(AW)" in upper:
        return "all-weather"
    return "track"


# (records list, summaries) pairs, newest last. load_stakes_data returns the same
# list until the race tables change, so identity is a safe cache key.
_SUMMARY_CACHE = []


def build_horse_summaries(records):
    """Cached _build_horse_summaries: several panels per request ask for the same summaries."""
    for cached_records, summaries in _SUMMARY_CACHE:
        if cached_records is records:
            return summaries
    summaries = _build_horse_summaries(records)
    _SUMMARY_CACHE.append((records, summaries))
    del _SUMMARY_CACHE[:-4]
    return summaries


_SURFACE_CACHE = {}
_PARENTS_CACHE = (None, None, 0, {})


def winner_parents(pedigree_db, summaries):
    """Cleaned winner name -> (sire, dam, damsire) labels from the pedigree DB.

    Pedigrees are stored compressed; decoding every stakes winner's tree in
    several loops per request was seconds of each suggestion and report.
    """
    global _PARENTS_CACHE
    cached_summaries, cached_db, size, parents = _PARENTS_CACHE
    if cached_summaries is summaries and cached_db is pedigree_db and size == len(pedigree_db):
        return parents
    parents = {}
    for h_name in summaries:
        clean_h = main.clean_horse_name(h_name)
        if not clean_h or clean_h not in pedigree_db:
            continue
        ped = pedigree_db[clean_h] or {}
        sire_node = ped.get("Sire") or {}
        sire = sire_node.get("Horse", "") if isinstance(sire_node, dict) else str(sire_node or "")
        dam_node = ped.get("Dam") or {}
        dam = dam_node.get("Horse", "") if isinstance(dam_node, dict) else str(dam_node or "")
        damsire = get_mare_sire_from_pedigree(dam_node) if isinstance(dam_node, dict) else ""
        parents[clean_h] = (sire, dam, damsire)
    _PARENTS_CACHE = (summaries, pedigree_db, len(pedigree_db), parents)
    return parents


def _records_on_surface(records, surface):
    """Stakes records run on dirt or turf (same list object per source list, so summaries cache)."""
    hit = _SURFACE_CACHE.get(surface)
    if hit and hit[0] is records:
        return hit[1]
    picked = []
    for r in records:
        dist_str = str(r.get("Distance", "")).upper()
        r_surf = "dirt" if "(D)" in dist_str else "turf" if "(T)" in dist_str else "track"
        if r_surf == surface:
            picked.append(r)
    _SURFACE_CACHE[surface] = (records, picked)
    return picked


def _build_horse_summaries(records):
    """Per-horse summaries of black-type winners. Only Place==1 rows are wins.

    The race table also holds non-stakes wins (allowance, maiden, ...). Those
    are listed in ``races`` but never earn a status, and a horse with no
    G1/G2/G3/Listed/Stakes win is left out (its status would be meaningless here).
    """
    horses = {}
    for r in records:
        h_name = str(r.get("Horse Name") or "").strip()
        if not h_name or h_name.upper() in {"N/A", "NAN", "NONE"}:
            continue
        if not _is_winning_place(r):
            continue

        if h_name not in horses:
            sex_val = str(r.get("Sex") or "").strip().lower()
            horses[h_name] = {
                'name': h_name,
                'foaling_year': r.get('Foaling Year') or "",
                'sex': sex_val,
                'races': [],
                'wins': [],
                'g1_count': 0, 'g2_count': 0, 'g3_count': 0, 'ungraded_count': 0,
                'other_wins': 0,
            }
        
        dist_str = str(r.get('Distance') or "")
        surface = _surface_from_distance(dist_str)
        clean_dist = re.sub(r'\s*\([^)]*\)', '', dist_str).strip()
        grade = str(r.get('Grade') or r.get('Group') or "").strip()
        purse = str(r.get('Purse') or "").strip()
        race_name = str(r.get('Race Name') or "").strip()
        race_year = r.get('Race Year') or ""
        
        label = "" if grade.upper() == "STAKES" else grade  # "the Sanford Stakes", not "the Stakes Sanford Stakes"
        race_line = f"{h_name} won the {race_year} edition of the {purse} {label} {race_name}"
        if clean_dist:
            race_line += f" over {clean_dist} on the {surface}"
        elif surface != "track":
            race_line += f" on the {surface}"
        horses[h_name]['races'].append(" ".join(race_line.split()))
        horses[h_name]['wins'].append({"year": str(race_year), "grade": grade, "race": race_name})
        
        level = graded_level(grade)
        if level == 1: horses[h_name]['g1_count'] += 1
        elif level == 2: horses[h_name]['g2_count'] += 1
        elif level == 3: horses[h_name]['g3_count'] += 1
        elif is_black_type(grade): horses[h_name]['ungraded_count'] += 1
        else: horses[h_name]['other_wins'] += 1

    for h_name in [n for n, h in horses.items()
                   if not (h['g1_count'] or h['g2_count'] or h['g3_count'] or h['ungraded_count'])]:
        del horses[h_name]

    for h_name, h in horses.items():
        if h['g1_count'] > 1: h['status'] = 'MG1W'
        elif h['g1_count'] == 1: h['status'] = 'G1W'
        elif (h['g2_count'] + h['g3_count']) > 1: h['status'] = 'MGSW'
        elif h['g2_count'] == 1: h['status'] = 'G2W'
        elif h['g3_count'] == 1: h['status'] = 'G3W'
        elif h['ungraded_count'] > 1: h['status'] = 'MSW'
        else: h['status'] = 'SW'
        
    return horses

def _collect_ancestors(node, depth, gathered, max_generation):
    if not node or not isinstance(node, dict) or depth > max_generation:
        return
    name = main.clean_horse_name(node.get("Horse", ""))
    if name and "unknown" not in name:
        gathered.add(name)
    _collect_ancestors(node.get("Sire"), depth + 1, gathered, max_generation)
    _collect_ancestors(node.get("Dam"), depth + 1, gathered, max_generation)


# (stallion label, generations) -> ancestor names. A label names one tree, so a
# stallion's set is built once instead of on every suggestion.
_ANCESTOR_SETS = {}


def ancestor_set(ped, max_generation=3):
    label = ped.get("Horse") if isinstance(ped, dict) else None
    key = (label, max_generation)
    if label and key in _ANCESTOR_SETS:
        return _ANCESTOR_SETS[key]
    gathered = set()
    _collect_ancestors(ped, 1, gathered, max_generation)
    gathered = frozenset(gathered)
    if label and (ped.get("Sire") or ped.get("Dam")):
        _ANCESTOR_SETS[key] = gathered
    return gathered


def check_inbreeding_failsafe(stallion_ped, mare_ped, max_generation=3):
    mare_anc = set()
    _collect_ancestors(mare_ped, 1, mare_anc, max_generation)
    return not ancestor_set(stallion_ped, max_generation).isdisjoint(mare_anc)

_RANK_BY_NAME = None
_PROFILE_INDEX = (None, 0, {})


def _rank_by_name():
    global _RANK_BY_NAME
    if _RANK_BY_NAME is None:
        _RANK_BY_NAME = {}
        for idx, r_name in enumerate(RAW_RANKING_ORDER, 1):
            _RANK_BY_NAME.setdefault(main.clean_horse_name(r_name), str(idx))
    return _RANK_BY_NAME


def _profiles_by_name(all_profiles):
    """Cleaned name -> first matching stud card. Rebuilt when a different book is passed in.

    get_profile_meta runs thousands of times per suggestion / report; scanning
    every card each time was most of a 60s request.
    """
    global _PROFILE_INDEX
    book_id, size, index = _PROFILE_INDEX
    if book_id != id(all_profiles) or size != len(all_profiles):
        index = {}
        for k, v in all_profiles.items():
            index.setdefault(main.clean_horse_name(k), v)
        _PROFILE_INDEX = (id(all_profiles), len(all_profiles), index)
    return index


def get_profile_meta(name, all_profiles):
    clean_name = main.clean_horse_name(name)
    rank_val = _rank_by_name().get(clean_name, "N/A")
    profile = _profiles_by_name(all_profiles).get(clean_name, {})

    raw_fee = profile.get("Stud Fee", 0)
    try:
        fee_num = int(str(raw_fee).replace(",", "").replace("$", ""))
        fee_str = profile.get("Stud Fee Display") or f"${fee_num:,.0f}"
    except ValueError:
        fee_num = 0
        fee_str = "$0" if not raw_fee else str(raw_fee)
        
    has_progeny = profile.get("HasProgeny", profile.get("Has Progeny", True))
    
    # SIMPLIFICATION RULE: Check if horse is in profiles AND status is not pensioned
    is_pensioned = str(profile.get("Status", "")).strip().lower() == "pensioned"
    # Only suggest stallions standing now: a published fee and a recent stud season.
    # Dead or retired sires keep "active" status but lose their fee / latest season.
    try:
        duty_year = int(str(profile.get("Latest Season") or profile.get("Stud Duty Year") or "").strip()[:4])
    except ValueError:
        duty_year = None
    is_standing = fee_num > 0 and (duty_year is None or duty_year >= datetime.date.today().year - 1)
    # Suggestions only use stallions standing in North America for now (user's call).
    from crossr_core.identity import stands_in_north_america

    from crossr_core import stallion_status

    is_dead_or_retired = stallion_status.off_duty(name, profile.get("Year Born"))
    is_active = (len(profile) > 0) and not is_pensioned and not is_dead_or_retired and is_standing \
        and stands_in_north_america(profile)

    return {"rank": rank_val, "fee_str": fee_str, "fee_num": fee_num, "has_progeny": has_progeny, "is_active": is_active}

def sort_ancestor_list(ancestor_names, all_profiles, budget_filter=None):
    meta_list = []
    for name in ancestor_names:
        meta = get_profile_meta(name, all_profiles)
        if meta["is_active"]:
            if budget_filter is not None and meta["fee_num"] > budget_filter:
                continue
            meta_list.append((name, meta))
        
    def sorting_key(item):
        name, m = item
        try:
            r_val = float(m["rank"])
        except (ValueError, TypeError):
            r_val = float('inf')
        f_val = m["fee_num"]
        return (r_val, -f_val, name)
        
    meta_list.sort(key=sorting_key)
    return [item[0] for item in meta_list]

_LINEAGE_CACHE = {}


def has_lineage_options(stallion_name, all_profiles, budget_filter=None):
    """Checks if a stallion actually has ancestors, sibling sires, or descendants available.

    Cached per (stallion, budget) for the stud book in use.
    """
    st_clean = main.clean_horse_name(stallion_name)
    key = (st_clean, budget_filter, id(all_profiles), len(all_profiles))
    if key not in _LINEAGE_CACHE:
        if len(_LINEAGE_CACHE) > 20000:
            _LINEAGE_CACHE.clear()
        _LINEAGE_CACHE[key] = _has_lineage_options(st_clean, all_profiles, budget_filter)
    return _LINEAGE_CACHE[key]


def _has_lineage_options(st_clean, all_profiles, budget_filter=None):
    lineage_options = find_lineage_relatives(st_clean)
    
    if sort_ancestor_list(lineage_options, all_profiles, budget_filter):
        return True
        
    return False

def _is_this_mare(dam_label, clean_mare, mare_year):
    """Dam label names this mare: same name and, when both carry one, the same foaling year
    (Stage Magic (2007) and Stage Magic (2015) are different mares)."""
    if main.clean_horse_name(dam_label) != clean_mare:
        return False
    dam_year = main.foaling_year(dam_label)
    return not (mare_year and dam_year and dam_year != mare_year)


def lookup_mare_pedigree(pedigree_db, mare_name):
    """Find a broodmare pedigree by cleaned or uppercase name. Returns (tree, matched_key) or (None, None)."""
    hit = main.lookup_pedigree(mare_name, pedigree_db)
    if hit["ambiguous"] or not hit["tree"]:
        return None, None
    return copy.deepcopy(hit["tree"]), hit["key"]


def suggest_matings(pedigree_db, all_profiles, mare_name, surface_pref="neither", min_fee=None, max_fee=None):
    """Non-interactive stallion suggestions for a broodmare already in the pedigree database."""
    hit = main.lookup_pedigree(mare_name, pedigree_db)
    if hit["ambiguous"]:
        return {"error": "ambiguous_year", "mare": hit["clean_name"], "years": hit["years"]}
    mare_pedigree, matched_key = lookup_mare_pedigree(pedigree_db, mare_name)
    if not mare_pedigree:
        return {"error": "not_found", "mare": mare_name}

    mare_display = mare_pedigree.get("Horse", mare_name)
    clean_mare = main.clean_horse_name(matched_key or mare_name)
    mare_year = main.foaling_year(matched_key or "") or main.foaling_year(mare_pedigree.get("Horse", ""))
    mating_damsire = get_mare_sire_from_pedigree(mare_pedigree)
    clean_display_damsire = main._COLOR_SUFFIX.sub('', re.sub(r'\s*\(\d{4}\)', '', mating_damsire)).strip()
    clean_display_mare = re.sub(r'\s*\(\d{4}\)', '', mare_display).strip()

    surface = (surface_pref or "neither").strip().lower()
    fee_cap = sys.maxsize if max_fee is None else int(max_fee)

    raw_records = load_stakes_data()
    if surface in ["dirt", "turf"]:
        filtered_records = _records_on_surface(raw_records, surface)
    else:
        filtered_records = raw_records

    horse_summaries = build_horse_summaries(filtered_records)
    proven_cross_sires = {}
    direct_progeny_clean_names = set()
    mare_sire_ancestry = get_sire_line_ancestry(mating_damsire, node=mare_pedigree.get("Sire"))

    parents = winner_parents(pedigree_db, horse_summaries)
    for h_name, h in horse_summaries.items():
        clean_h = main.clean_horse_name(h_name)
        if clean_h in parents:
            sire_name, dam_name, _ = parents[clean_h]
            if _is_this_mare(dam_name, clean_mare, mare_year):
                direct_progeny_clean_names.add(clean_h)
                sire_name = sire_name or "Unknown Stallion"
                clean_sire = main.clean_horse_name(sire_name)
                clean_display_sire = re.sub(r'\s*\(\d{4}\)', '', sire_name).strip()
                proven_cross_sires[clean_sire] = {
                    "foal_name": h_name,
                    "status": h["status"],
                    "sex": h["sex"],
                    "sire": clean_display_sire,
                    "dam": clean_display_mare,
                    "damsire": clean_display_damsire,
                    "wins": h["wins"],
                    "races_sentence": " ".join(h["races"]),
                    "bio_sentence": (
                        f"{h_name} was foaled in {h['foaling_year']} and is a "
                        f"{SEX_MAP.get(h['sex'], h['sex'])} by {clean_display_sire} "
                        f"out of the {clean_display_damsire} mare {clean_display_mare}."
                    ),
                    "actual_sire_name": clean_display_sire,
                }

    candidates = {}
    for st_name, p_data in all_profiles.items():
        if str(p_data.get("Status", "")).strip().lower() == "pensioned":
            continue
        meta_check = get_profile_meta(st_name, all_profiles)
        if not meta_check["is_active"]:
            continue
        st_fee = p_data.get("Stud Fee", 0)
        try:
            fee_val = int(str(st_fee).replace(",", "").replace("$", ""))
        except ValueError:
            fee_val = 0
        if min_fee is not None and fee_val < int(min_fee):
            continue
        if fee_cap != sys.maxsize and fee_val > fee_cap:
            continue
        clean_st = main.clean_horse_name(st_name)
        st_pedigree = pedigree_db.get(clean_st, {"Horse": st_name})
        if check_inbreeding_failsafe(st_pedigree, mare_pedigree, max_generation=3):
            continue
        candidates[clean_st] = {
            "name": st_name,
            "score": 0.0,
            "reasons_by_tier": {t: [] for t in ["MG1W", "G1W", "MGSW", "G2W", "G3W", "MSW", "SW"]},
            "direct_hit": False,
            "cross_meta": [],
            "crosses": [],
        }

    status_weights = {"MG1W": 500, "G1W": 400, "MGSW": 300, "G2W": 250, "G3W": 200, "MSW": 150, "SW": 100}
    for h_name, h in horse_summaries.items():
        clean_h = main.clean_horse_name(h_name)
        if clean_h in direct_progeny_clean_names:
            continue
        h_sire, h_dam, h_damsire = parents.get(clean_h, ("", "", ""))
        if h_damsire and main.clean_horse_name(h_damsire) in mare_sire_ancestry:
            clean_hsire = main.clean_horse_name(h_sire)
            base_points = status_weights.get(h["status"], 100)
            clean_display_hsire = re.sub(r'\s*\(\d{4}\)', '', h_sire).strip()
            clean_display_h_ds = re.sub(r'\s*\(\d{4}\)', '', h_damsire).strip()
            races_sentence = " ".join(h["races"])
            for clean_st, cand_dict in candidates.items():
                if clean_st == clean_hsire:
                    cand_dict["score"] += base_points
                    cand_dict["direct_hit"] = True
                    msg = (
                        f"Cross Success ({h['status']}): {clean_display_hsire} over a "
                        f"{clean_display_h_ds} mare produced {h_name}. {races_sentence}"
                    )
                    if msg not in cand_dict["reasons_by_tier"][h["status"]]:
                        cand_dict["reasons_by_tier"][h["status"]].append(msg)
                        cand_dict["crosses"].append({
                            "name": h_name,
                            "status": h["status"],
                            "sex": h["sex"],
                            "sire": clean_display_hsire,
                            "dam": re.sub(r'\s*\(\d{4}\)', '', h_dam).strip(),
                            "damsire": clean_display_h_ds,
                            "wins": h["wins"],
                        })

    for clean_st, cand_dict in candidates.items():
        for clean_cross_sire, meta in proven_cross_sires.items():
            if clean_st == clean_cross_sire:
                cand_dict["score"] += 10000
                cand_dict["direct_hit"] = True
                if meta not in cand_dict["cross_meta"]:
                    cand_dict["cross_meta"].append(meta)

    active_pool = [c for c in candidates.values() if c["score"] > 0 and c["direct_hit"]]
    sorted_pool = sorted(active_pool, key=lambda x: -x["score"])

    # Sires of graded winners whose damsire is in the mare's sire line. Computed
    # once instead of re-reading every winner's pedigree for each stallion.
    cross_hit_sires = set()
    for h_sire, _, h_damsire in parents.values():
        if h_damsire and main.clean_horse_name(h_damsire) in mare_sire_ancestry:
            cross_hit_sires.add(main.clean_horse_name(h_sire))

    unfiltered_structural_hits = []
    for st_name, p_data in all_profiles.items():
        clean_st = main.clean_horse_name(st_name)
        st_pedigree = pedigree_db.get(clean_st, {"Horse": st_name})
        if check_inbreeding_failsafe(st_pedigree, mare_pedigree, max_generation=3):
            continue
        has_cross_hit = clean_st in cross_hit_sires
        if has_cross_hit and clean_st not in [main.clean_horse_name(c["name"]) for c in active_pool]:
            unfiltered_structural_hits.append(st_name)

    suggestions = []
    for cand in sorted_pool:
        meta = get_profile_meta(cand["name"], all_profiles)
        suggestions.append({
            "name": cand["name"],
            "score": cand["score"],
            "rank": meta["rank"],
            "stud_fee": meta["fee_str"],
            "fee_num": meta["fee_num"],
            "has_lineage": has_lineage_options(cand["name"], all_profiles, budget_filter=fee_cap if fee_cap != sys.maxsize else None),
            "cross_meta": cand["cross_meta"],
            "crosses": cand.get("crosses", []),
            "reasons_by_tier": cand["reasons_by_tier"],
        })

    lineage_groups = []
    seen = set()
    budget = fee_cap if fee_cap != sys.maxsize else None
    for cand in sorted_pool:
        key = main.clean_horse_name(cand["name"])
        if key in seen:
            continue
        if has_lineage_options(cand["name"], all_profiles, budget_filter=budget):
            lineage_groups.append({"anchor": cand["name"], "variant": False})
            seen.add(key)
    # Cross hits left out of the suggestions. Standing sires that are only over the
    # fee cap (e.g. Into Mischief at a $100k max) come first, best-ranked first,
    # so their in-budget sons/relatives are easy to find.
    def _hit_order(name):
        meta = get_profile_meta(name, all_profiles)
        over = bool(meta["is_active"] and budget is not None and meta["fee_num"] > budget)
        rank = int(meta["rank"]) if str(meta["rank"]).isdigit() else 10 ** 6
        return (0 if over else 1, rank, name), over, meta
    ordered_hits = sorted((_hit_order(n) + (n,) for n in unfiltered_structural_hits), key=lambda t: t[0])
    for _, over, meta, unfil_name in ordered_hits:
        key = main.clean_horse_name(unfil_name)
        if key in seen:
            continue
        if has_lineage_options(unfil_name, all_profiles, budget_filter=budget):
            lineage_groups.append({
                "anchor": unfil_name,
                "variant": True,
                "over_budget": over,
                "stud_fee": meta["fee_str"] if over else None,
                "rank": meta["rank"] if over else None,
            })
            seen.add(key)

    return {
        "error": None,
        "mare": mare_display,
        "mare_key": matched_key,
        "damsire": clean_display_damsire or None,
        "surface": surface,
        "max_fee": None if fee_cap == sys.maxsize else fee_cap,
        "min_fee": min_fee,
        "suggestions": suggestions,
        "lineage_groups": lineage_groups,
        "broodmare": broodmare_context(pedigree_db, mare_name),
    }


def lineage_options_payload(stallion_name, all_profiles, budget_filter=None):
    st_clean = main.clean_horse_name(stallion_name)
    all_relatives = find_lineage_relatives(st_clean)
    sorted_relatives = sort_ancestor_list(all_relatives, all_profiles, budget_filter)
    options = []
    for relative in sorted_relatives:
        meta = get_profile_meta(relative, all_profiles)
        if meta["is_active"]:
            options.append({
                "name": relative,
                "rank": meta["rank"],
                "stud_fee": meta["fee_str"],
                "fee_num": meta["fee_num"],
            })
    return {"stallion": stallion_name, "options": options}


def build_hypo_report(pedigree_db, all_profiles, sire_name, dam_name, persist=False):
    dam_hit = main.lookup_pedigree(dam_name, pedigree_db)
    if dam_hit["ambiguous"]:
        return {"error": "ambiguous_year", "mare": dam_hit["clean_name"], "years": dam_hit["years"]}
    mare_pedigree, _ = lookup_mare_pedigree(pedigree_db, dam_name)
    if not mare_pedigree:
        return {"error": "not_found", "mare": dam_name}
    dam_display = re.sub(r'\s*\(\d{4}\)', '', mare_pedigree.get('Horse', dam_name)).strip()
    hypo_foal_name = f"{sire_name.title()} x {dam_display}"
    combined_pedigree = {
        "Horse": hypo_foal_name,
        "Sire": copy.deepcopy(main.pedigree_tree(sire_name, pedigree_db) or {"Horse": sire_name}),
        "Dam": copy.deepcopy(mare_pedigree),
    }
    if persist:
        hypofoals_manager.register_new_hypo_foal(
            sire_name=sire_name,
            dam_name=dam_name,
            combined_tree=combined_pedigree,
        )
    return {
        "error": None,
        "foal_name": hypo_foal_name,
        "sire": sire_name,
        "dam": mare_pedigree.get("Horse", dam_name),
        "pedigree": combined_pedigree,
        "inbreeding": main.compute_inbreeding(combined_pedigree),
    }


def top_crosses_payload(pedigree_db, stallion_name, limit=20):
    clean_target = main.clean_horse_name(stallion_name)
    horse_summaries = build_horse_summaries(load_stakes_data())
    parents = winner_parents(pedigree_db, horse_summaries)
    crosses = {}
    status_weights = {"MG1W": 500, "G1W": 400, "MGSW": 300, "G2W": 250, "G3W": 200, "MSW": 150, "SW": 100}
    tier_order = ["MG1W", "G1W", "MGSW", "G2W", "G3W", "MSW", "SW"]
    for h_name, h in horse_summaries.items():
        clean_h = main.clean_horse_name(h_name)
        if clean_h not in parents:
            continue
        h_sire, dam_name, h_damsire = parents[clean_h]
        if main.clean_horse_name(h_sire) != clean_target:
            continue
        if not h_damsire:
            continue
        clean_ds = re.sub(r"\s*\(\d{4}\)", "", h_damsire).strip().title()
        if clean_ds not in crosses:
            crosses[clean_ds] = {"score": 0, "winners": []}
        crosses[clean_ds]["score"] += status_weights.get(h["status"], 100)
        crosses[clean_ds]["winners"].append({
            "name": h_name,
            "status": h["status"],
            "dam": re.sub(r"\s*\(\d{4}\)", "", dam_name).strip(),
        })
    ranked = []
    for ds_name, data in sorted(crosses.items(), key=lambda x: -x[1]["score"]):
        lookup_key = main.clean_horse_name(ds_name)
        bms = BROODMARE_DATABASE.get(lookup_key) or {}
        ranked.append({
            "broodmare_sire": ds_name,
            "rank": bms.get("Rank"),
            "score": data["score"],
            "winners": sorted(data["winners"], key=lambda w: tier_order.index(w["status"]) if w["status"] in tier_order else 99),
        })
    return {"stallion": stallion_name, "crosses": ranked[:limit] if limit else ranked}


def broodmare_context(pedigree_db, mare_name):
    mare_pedigree, matched = lookup_mare_pedigree(pedigree_db, mare_name)
    if not mare_pedigree:
        return None
    clean_mare = main.clean_horse_name(matched or mare_name)
    mare_year = main.foaling_year(matched or "") or main.foaling_year(mare_pedigree.get("Horse", ""))
    summaries = build_horse_summaries(load_stakes_data())
    own = None
    for h_name, h in summaries.items():
        if main.clean_horse_name(h_name) == clean_mare:
            own = h
            break
    progeny = []
    parents = winner_parents(pedigree_db, summaries)
    for h_name, h in summaries.items():
        clean_h = main.clean_horse_name(h_name)
        if clean_h not in parents:
            continue
        sire_name, dam_name, _ = parents[clean_h]
        if not _is_this_mare(dam_name, clean_mare, mare_year):
            continue
        progeny.append({
            "name": h_name,
            "status": h["status"],
            "sire": re.sub(r"\s*\(\d{4}\)", "", sire_name).strip(),
            "races": h["races"],
        })
    return {
        "mare": mare_pedigree.get("Horse", mare_name),
        "own": own,
        "progeny": progeny,
        "pedigree": None,
    }
"""Horse identity and classification.

Horses share names (two Englishmans, two Stage Magics...), so a stud card,
ranking row or progeny list must never be attached to a horse by name alone.
This module decides, from corroborating evidence, whether stallion data
belongs to a given horse, and what kind of profile the horse should get.

Evidence used, strongest first:
  * foaling year and sire of the stud-card stallion (BloodHorse ranking lists
    record both) against the horse's own pedigree;
  * the horse's recorded sex (race_horses: C, H, R, G, F, M);
  * the horse's age, and whether it was old enough to stand in the card's
    first stud season;
  * recorded offspring in the pedigree database (sire or dam of a horse).
"""

from __future__ import annotations

import datetime
import re
import threading

from crossr_core import sample_db

_lock = threading.RLock()

FEMALE = {"F", "M"}          # filly, mare
GELDING = {"G"}
MALE = {"C", "H", "R"}       # colt, horse, ridgling
SEX_LABEL = {"C": "Colt", "H": "Horse", "R": "Ridgling", "G": "Gelding", "F": "Filly", "M": "Mare"}

# A Thoroughbred is a colt/filly through age four, a horse/mare from five.
YOUNG_MAX_AGE = 4
# A stallion stands at stud from age 3 at the earliest (first season at 3 or 4).
MIN_STUD_AGE = 2


def _year(text) -> int | None:
    m = re.search(r"\b(1[6-9]\d\d|20\d\d)\b", str(text or ""))
    return int(m.group(1)) if m else None


def _label_year(label) -> int | None:
    m = re.search(r"\((\d{4})\)", str(label or ""))
    return int(m.group(1)) if m else None


# Colour codes some pedigree sources append to names ("TAPIT GR/R", "SMART STRIKE DK B/").
# Only unambiguous codes (with a slash, or DK B / BLK): a bare " B" or " CH" can be
# part of a real name ("Plan B").
_COLOUR_SUFFIX = re.compile(r"\s+(GR/RO|GR/R|DK B/BR|DK B/|DK B|DKB/BR|B/BR|BLK)\.?$", re.I)


def _clean(name) -> str:
    text = re.sub(r"\(\d{4}\)", "", str(name or "")).strip()
    text = _COLOUR_SUFFIX.sub("", text)
    return sample_db.name_key(text)


def current_year() -> int:
    return datetime.date.today().year


# ---------------------------------------------------------------------------
# Stud-card identity: who the stallion behind a card actually is
# ---------------------------------------------------------------------------

_CARD_IDS: tuple[float, dict] | None = None


def card_identities() -> dict[str, set[tuple[int | None, str]]]:
    """name_key -> {(foaling_year, sire_key)} from the BloodHorse ranking lists."""
    global _CARD_IDS
    version = sample_db.table_version("stallion_rankings")
    with _lock:
        if _CARD_IDS and _CARD_IDS[0] == version:
            return _CARD_IDS[1]
        ids: dict[str, set] = {}
        for row in sample_db.load_table("stallion_rankings"):
            key = sample_db.name_key(row.get("stallion") or "")
            year = _year(row.get("foaling_year"))
            sire = _clean(row.get("sire"))
            if key and (year or sire):
                ids.setdefault(key, set()).add((year, sire))
        _CARD_IDS = (version, ids)
        return ids


def card_foal_year(name: str, first_season: int | None = None) -> int:
    """The card stallion's foaling year from the rankings, when it is unambiguous.

    With several namesakes on the lists, keep the one old enough to have stood
    in first_season.
    """
    years = {y for y, _ in card_identities().get(sample_db.name_key(name), set()) if y}
    if first_season:
        years = {y for y in years if y <= first_season - MIN_STUD_AGE}
    return max(years) if len(years) == 1 or (years and first_season) else 0


def card_sire(name: str, foal_year: int | None = None) -> str:
    sires = {s for y, s in card_identities().get(sample_db.name_key(name), set())
             if s and (not foal_year or not y or y == foal_year)}
    return next(iter(sires)) if len(sires) == 1 else ""


# ---------------------------------------------------------------------------
# Where a stallion stands
# ---------------------------------------------------------------------------

_NA_PLACES = re.compile(
    r"\b(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|"
    r"OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|AB|BC|MB|NB|NS|ON|PE|QC|SK|"
    r"Alabama|Arizona|Arkansas|California|Colorado|Florida|Georgia|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|"
    r"Louisiana|Maryland|Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Jersey|New Mexico|"
    r"New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|South Carolina|South Dakota|"
    r"Tennessee|Texas|Utah|Virginia|Washington|West Virginia|Wisconsin|Wyoming|Alberta|British Columbia|"
    r"Manitoba|Ontario|Quebec|Saskatchewan|USA|U\.S\.A|Canada)\b"
)


_ABROAD = re.compile(
    r"(?:^|,)\s*(JPN|Japan|IRE|Ireland|GB|ENG|England|UK|FR|FRA|France|GER|Germany|ITY|Italy|AUS|Australia|NZ|"
    r"New Zealand|ARG|Argentina|BRZ|Brazil|CHI|Chile|URU|Uruguay|PER|Peru|KOR|Korea|UAE|SAF|South Africa|"
    r"TUR|Turkey|IND|India)\s*$", re.I)


def stands_in_north_america(card: dict | None) -> bool:
    """True when the stud card's fee is in US/Canadian dollars, or (no fee
    currency) its farm location names a US state or Canadian province."""
    if not card:
        return False
    # BloodHorse lists some overseas stallions with a dollar conversion of the fee
    # (American Pharoah: "$25,850", Shizunai Stallion Station, JPN): the farm wins.
    if _ABROAD.search(str(card.get("Farm") or "")):
        return False
    currency = str(card.get("Stud Fee Currency") or "").upper()
    if currency:
        return currency in ("USD", "CAD")
    return bool(_NA_PLACES.search(str(card.get("Farm") or "")))


# ---------------------------------------------------------------------------
# Facts about one horse
# ---------------------------------------------------------------------------

def _race_horse_rows(name: str) -> list[tuple]:
    pg = sample_db._pg()
    with pg.connect() as conn:
        return conn.execute(
            "SELECT sex, foal_year, sire FROM race_horses WHERE upper(name_key) = %s",
            (sample_db.name_key(name),),
        ).fetchall()


def horse_facts(name: str, tree: dict | None = None, year: int | None = None) -> dict:
    """Year, sire, dam and recorded sex of the specific horse a profile is about."""
    tree = tree if isinstance(tree, dict) else {}
    year = year or _label_year(tree.get("Horse")) or _label_year(name)
    sire_node = tree.get("Sire")
    dam_node = tree.get("Dam")
    sire = sire_node.get("Horse") if isinstance(sire_node, dict) else sire_node
    dam = dam_node.get("Horse") if isinstance(dam_node, dict) else dam_node
    sex = None
    try:
        rows = _race_horse_rows(name)
    except Exception:
        rows = []
    # Only trust a race_horses row that is the same horse: same foaling year, or
    # same sire when the year is missing. A namesake's sex must not leak in.
    for r_sex, r_year, r_sire in rows:
        r_sex = (r_sex or "").strip().upper()[:1]
        if not r_sex:
            continue
        ry = _year(r_year)
        if year and ry and ry != year:
            continue
        if not ry and r_sire and sire and _clean(r_sire) != _clean(sire):
            continue
        if year and not ry and not (r_sire and sire):
            continue
        sex = r_sex
        if ry == year:
            break
    return {"name": _clean(name), "year": year, "sire": _clean(sire), "dam": _clean(dam), "sex": sex}


# ---------------------------------------------------------------------------
# Offspring evidence from the pedigree database
# ---------------------------------------------------------------------------

_PARENTS: tuple[int, dict, dict] | None = None


def _parent_index():
    """(sire counts, dam counts) keyed by "NAME (YEAR)" and by bare name for undated labels."""
    global _PARENTS
    import main  # the API's shared pedigree map

    db = main.PEDIGREE_DATABASE
    with _lock:
        if _PARENTS and _PARENTS[0] == len(db):
            return _PARENTS[1], _PARENTS[2]
        sires: dict[str, int] = {}
        dams: dict[str, int] = {}
        seen = set()
        for key in db:
            tree = db[key]
            if not isinstance(tree, dict):
                continue
            child = tree.get("Horse")
            if child in seen:
                continue
            seen.add(child)
            for role, bucket in (("Sire", sires), ("Dam", dams)):
                node = tree.get(role)
                label = node.get("Horse") if isinstance(node, dict) else node
                if label:
                    ident = _ident(label)
                    bucket[ident] = bucket.get(ident, 0) + 1
        _PARENTS = (len(db), sires, dams)
        return sires, dams


def _ident(label) -> str:
    y = _label_year(label)
    return f"{_clean(label)} ({y})" if y else _clean(label)


def offspring_counts(facts: dict) -> tuple[int, int]:
    """(times recorded as a sire, times recorded as a dam) for this specific horse."""
    try:
        sires, dams = _parent_index()
    except Exception:
        return 0, 0
    # Undated parent labels can't be told apart from a namesake's, so they only
    # count when this horse's own year is unknown (Sovereignty 2022 is not the
    # older SOVEREIGNTY recorded as a sire).
    keys = [f"{facts['name']} ({facts['year']})"] if facts.get("year") else [facts["name"]]
    return (sum(sires.get(k, 0) for k in keys), sum(dams.get(k, 0) for k in keys))


# ---------------------------------------------------------------------------
# Does a stud card belong to this horse?
# ---------------------------------------------------------------------------

def card_matches(card_name: str, card: dict, facts: dict) -> tuple[bool, str]:
    """(ok, reason). Rejects cards that belong to a namesake."""
    first = _year(card.get("Stud Duty Year"))
    card_years = {y for y, _ in card_identities().get(sample_db.name_key(card_name), set()) if y}
    yb = _year(card.get("Year Born"))
    if yb:
        card_years.add(yb)
    year = facts.get("year")
    if facts.get("sex") in FEMALE:
        return False, "horse is female"
    if facts.get("sex") in GELDING and not (year and year in card_years):
        # A recorded "G" loses to a stud record for the exact same horse (same
        # foaling year): race-record sex codes are sometimes wrong (McCraken).
        return False, "horse is a gelding"
    if year and card_years and year not in card_years:
        return False, f"foaled {year}; the stud-card stallion was foaled {sorted(card_years)}"
    if year and first and year > first - MIN_STUD_AGE:
        return False, f"foaled {year}, too young for the card's first season {first}"
    card_sires = {s for y, s in card_identities().get(sample_db.name_key(card_name), set())
                  if s and (not year or not y or y == year)}
    if facts.get("sire") and card_sires and facts["sire"] not in card_sires:
        return False, f"sire {facts['sire']} differs from the stud-card stallion's {sorted(card_sires)}"
    return True, ""


# ---------------------------------------------------------------------------
# Profile type
# ---------------------------------------------------------------------------

def classify(facts: dict, card: dict | None = None) -> dict:
    """Sex label, breeding roles and the profile type to render."""
    sired, dammed = offspring_counts(facts)
    code = facts.get("sex")
    if code in GELDING and card:
        code = "H"  # card_matches already confirmed the stud record is this horse
    if not code:
        if dammed:
            code = "M"
        elif sired or card:
            code = "H"
    age = current_year() - facts["year"] if facts.get("year") else None
    if code in MALE:
        label = "Ridgling" if code == "R" else ("Colt" if age is not None and age <= YOUNG_MAX_AGE else "Horse")
    elif code in FEMALE:
        label = "Filly" if age is not None and age <= YOUNG_MAX_AGE else "Mare"
    elif code in GELDING:
        label = "Gelding"
    else:
        label = None
    is_stallion = code in MALE and bool(card or sired)
    is_broodmare = code in FEMALE and dammed > 0
    stallion_status = None
    if is_stallion:
        latest = _year(card.get("Latest Season")) if card else None
        fee_text = " ".join(str(card.get(k) or "") for k in ("Stud Fee Display", "Status", "Notes")).lower() if card else ""
        from crossr_core import stallion_status as _status

        override = _status.override_for(facts.get("name") or "", facts.get("year"))
        if override and override["status"] in _status.OFF_DUTY:
            stallion_status = "deceased" if override["status"] == "deceased" else "pensioned"
        elif re.search(r"\b(died|dead|deceased|euthani[sz]ed)\b", fee_text):
            stallion_status = "deceased"
        elif re.search(r"\b(pensioned|retired)\b", fee_text):
            stallion_status = "pensioned"
        elif card and latest and latest >= current_year() - 1:
            stallion_status = "active"
        elif card:
            stallion_status = "inactive"   # no current advertised fee (pensioned, dead or exported)
        else:
            stallion_status = "historical"
    if code in GELDING:
        profile_type = "gelding"
    elif is_stallion:
        profile_type = "stallion"
    elif is_broodmare:
        profile_type = "broodmare"
    elif code in FEMALE:
        profile_type = "racing_female"
    elif code in MALE:
        profile_type = "racing_male"
    else:
        profile_type = "unknown"
    return {
        "sex": label,
        "sex_code": code,
        "age": age,
        "profile_type": profile_type,
        "is_stallion": is_stallion,
        "stallion_status": stallion_status,
        "is_broodmare": is_broodmare,
        "offspring_as_sire": sired,
        "offspring_as_dam": dammed,
        "breeding_eligible": code not in GELDING,
    }

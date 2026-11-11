"""Upcoming-sale catalogs (Upcoming and Analyzed), stored in Postgres.

scripts/load_railway.py loads each "Upcoming Sales/*.csv" and "Analyzed Sales/
analyzed_*.csv" file into upcoming_catalogs / upcoming_catalog_rows (id = file
name without .csv; one JSON object per CSV row). Starred hips and notes live in
upcoming_lot_marks and override the catalog's own STARRED / NOTES columns.
"""
import re
import time

from crossr_core import railway_db

_ID_RE = re.compile(r"[A-Za-z0-9._()\- ]+")
_PLACEHOLDER_NAME = re.compile(r"^(?:hip\s*\d+|\d{4}-)", re.I)


def _blank(value):
    text = "" if value is None else str(value).strip()
    if text.lower() in {"", "nan", "none", "null"}:
        return ""
    return text


def _year(value):
    match = re.search(r"(?:19|20)\d{2}", str(value or ""))
    return match.group(0) if match else ""


def _title(stem):
    name = stem[len("analyzed_"):] if stem.lower().startswith("analyzed_") else stem
    name = name.replace("_", " ").strip()
    return name if name else stem


def _hip_sort(hip):
    digits = "".join(ch for ch in str(hip or "") if ch.isdigit())
    return int(digits) if digits else 10**9


def _valid_id(catalog_id):
    if not catalog_id or not isinstance(catalog_id, str):
        return False
    return catalog_id == catalog_id.strip() and bool(_ID_RE.fullmatch(catalog_id)) and ".." not in catalog_id


def count_hips(rows):
    """Rows with a hip or a horse name (the loader stores this per catalog)."""
    count = 0
    for row in rows:
        if _blank(_col(row, "HIP", "HIP NUMBER", "LOT")) or _blank(_col(row, "HORSE NAME", "NAME", "HORSE")):
            count += 1
    return count


def _catalog_entry(stem, hips, source):
    return {
        "id": stem,
        "title": _title(stem),
        "year": _year(stem),
        "hips": hips,
        "source": source,
    }


def _yy_suffix(year):
    digits = _year(year)
    if not digits or len(digits) < 2:
        return ""
    return f" '{digits[-2:]}"


def _display_name(name, sire, dam, hip="", year=""):
    """Yearling placeholders like 2025-RAVER become Sire x Dam '25."""
    label = _blank(name)
    suffix = _yy_suffix(year)
    if not label or _PLACEHOLDER_NAME.match(label):
        if sire or dam:
            base = f"{sire} x {dam}".strip(" x")
            return f"{base}{suffix}" if base else (f"Hip {hip}" if hip else "Unnamed")
        return f"Hip {hip}" if hip else "Unnamed"
    if re.search(r"\s+x\s+", label, re.I):
        base = re.sub(r"\s*'\d{2}\s*$", "", label).strip()
        return f"{base}{suffix}" if suffix else base
    return label


def _sex_word(sex):
    letter = _blank(sex)[:1].upper()
    return {
        "C": "Colt",
        "F": "Filly",
        "M": "Mare",
        "H": "Horse",
        "G": "Gelding",
        "R": "Ridgling",
        "W": "Weanling",
    }.get(letter, _blank(sex))


def _color_word(color):
    raw = _blank(color)
    if not raw:
        return ""
    key = re.sub(r"\s+", " ", raw.upper().replace(".", "")).strip()
    key = key.replace("DKB/", "DK B/").replace("DKB ", "DK B ")
    mapping = {
        "B": "Bay",
        "BAY": "Bay",
        "CH": "Chestnut",
        "CHESTNUT": "Chestnut",
        "DK B/BR": "Dark Bay or Brown",
        "DK B/": "Dark Bay or Brown",
        "DK B": "Dark Bay or Brown",
        "DARK BAY/BROWN": "Dark Bay or Brown",
        "DK B / BR": "Dark Bay or Brown",
        "DK B BR": "Dark Bay or Brown",
        "DKB/BR": "Dark Bay or Brown",
        "DARK BAY OR BROWN": "Dark Bay or Brown",
        "GR/RO": "Gray/Roan",
        "GR / RO": "Gray/Roan",
        "G/R": "Gray/Roan",
        "GRAY/ROAN": "Gray/Roan",
        "GR": "Gray",
        "GRAY": "Gray",
        "GREY": "Gray",
        "RO": "Roan",
        "ROAN": "Roan",
        "BLK": "Black",
        "BLACK": "Black",
        "WH": "White",
        "WHITE": "White",
    }
    if key in mapping:
        return mapping[key]
    # Tolerate "Dk B/Br" style with mixed spacing.
    compact = key.replace(" ", "")
    compact_map = {
        "DKB/BR": "Dark Bay or Brown",
        "GR/RO": "Gray/Roan",
    }
    if compact in compact_map:
        return compact_map[compact]
    return raw


def _birth_date(raw):
    text = _blank(raw)
    if not text:
        return ""
    if re.search(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", text):
        return text
    return _year(text)


def _read_catalog(catalog_id):
    """(columns, rows) for a catalog id, or None."""
    if not _valid_id(catalog_id):
        return None
    return railway_db.read_upcoming_catalog(catalog_id)


def _col(row, *names):
    lookup = {(key or "").upper().strip(): key for key in row.keys() if key is not None}
    for name in names:
        key = lookup.get(name.upper())
        if key is not None:
            return row.get(key)
    return ""


def _price_num(raw):
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit() or ch == ".")
    try:
        return float(digits) if digits else 0
    except ValueError:
        return 0


def _sold_as(row, birth_year=None, sale_year=None):
    sold = _blank(_col(row, "SOLD AS", "SOLD AS DESCRIPTION", "DESCRIPTION"))
    if sold:
        return sold
    code = _blank(_col(row, "SOLD AS CODE")).upper()
    mapped = {
        "Y": "Yearling",
        "W": "Weanling",
        "B": "Broodmare",
        "H": "Horse of Racing Age",
        "2": "Two-Year-Old",
        "3": "Three-Year-Old",
    }.get(code, code)
    if mapped:
        return mapped
    # Foaling age vs catalog year when the CSV leaves sold-as blank.
    try:
        from crossr_core.projections import kind_from_foaling_age

        return kind_from_foaling_age(birth_year, sale_year) or ""
    except Exception:
        return ""


def _lot_from_row(row, sale_year=None):
    sex = _blank(_col(row, "SEX"))
    price_raw = _blank(_col(row, "PRICE", "PRICE_RAW", "SALE PRICE", "AUCTION PRICE"))
    starred = _blank(_col(row, "STARRED")).lower()
    raw_name = _blank(_col(row, "HORSE NAME", "NAME", "HORSE"))
    sire = _blank(_col(row, "SIRE"))
    dam = _blank(_col(row, "DAM"))
    hip = _blank(_col(row, "HIP", "HIP NUMBER", "LOT"))
    yob_raw = _blank(_col(row, "YEAR OF BIRTH", "YOB", "DOB"))
    birth_year = _year(yob_raw)
    color_raw = _blank(_col(row, "COLOR", "COLOUR"))
    display = _display_name(raw_name, sire, dam, hip, year=birth_year or yob_raw)
    return {
        "hip": hip,
        "name": display,
        "raw_name": raw_name,
        "sex": sex,
        "sex_letter": sex[:1].upper() if sex else "",
        "sex_label": _sex_word(sex),
        "sire": sire,
        "dam": dam,
        "damsire": _blank(_col(row, "SIRE OF DAM", "BMS", "DAMSIRE", "BROODMARE SIRE", "DAM SIRE")),
        "birth_year": birth_year,
        "birth_date": _birth_date(yob_raw),
        "foaled": _blank(_col(row, "FOALED", "STATE")),
        "sold_as": _sold_as(row, birth_year=birth_year, sale_year=sale_year),
        "covering_sire": _blank(_col(row, "COVERING SIRE")),
        "pregnant": _blank(_col(row, "PREGNANT", "PREGNANCY", "IN FOAL")).upper(),
        "session": _blank(_col(row, "SESSION")),
        "book": _blank(_col(row, "BOOK")),
        "supplement": _blank(_col(row, "SUPPLEMENT")).upper() in {"Y", "YES", "1", "TRUE", "S", "SUPPLEMENT"},
        "pedigree_pdf": _blank(_col(row, "PEDIGREE PDF", "PEDIGREE URL", "CATALOG PAGE")),
        "color": color_raw,
        "color_label": _color_word(color_raw),
        "consignor": _blank(_col(row, "CONSIGNOR", "CONSIGNED BY", "PROPERTY LINE")),
        "barn": _blank(_col(row, "BARN")),
        "notes": _blank(_col(row, "NOTES")),
        "starred": starred in {"*", "1", "true", "yes"},
        "price_raw": price_raw,
        "price_num": _price_num(price_raw),
    }


def list_catalogs():
    """Prefer analyzed catalogs; also surface raw Upcoming catalogs not analyzed yet."""
    stored = railway_db.list_upcoming_catalogs()
    by_key = {}
    for row in stored:
        if row["source"] == "analyzed":
            by_key[row["id"].lower()] = _catalog_entry(row["id"], row["hip_count"], "analyzed")
    for row in stored:
        if row["source"] != "analyzed":
            key = row["id"].lower()
            if f"analyzed_{key}" in by_key or key in by_key:
                continue
            by_key[key] = _catalog_entry(row["id"], row["hip_count"], "upcoming")
    return sorted(by_key.values(), key=lambda row: row["title"].lower())


def _apply_mark(lot, mark):
    if not mark:
        return lot
    if mark.get("starred") is not None:
        lot["starred"] = bool(mark["starred"])
    if mark.get("notes") is not None:
        lot["notes"] = mark["notes"]
    return lot


_LOTS_CACHE: dict = {}  # catalog id -> (stamp, lots without stars / notes)
_STAMP_CACHE: dict = {}  # catalog id -> (checked at, stamp)
_STAMP_TTL = 30.0  # seconds between "has this catalog been reloaded?" checks


def _catalog_stamp(catalog_id):
    """Changes whenever the catalog is replaced (replace_upcoming_catalog re-inserts its row)."""
    now = time.monotonic()
    hit = _STAMP_CACHE.get(catalog_id)
    if hit and now - hit[0] < _STAMP_TTL:
        return hit[1]
    with railway_db.connect() as conn:
        row = conn.execute("SELECT updated_at::text, hip_count FROM upcoming_catalogs WHERE id = %s",
                           (catalog_id,)).fetchone()
    stamp = tuple(row) if row else None
    _STAMP_CACHE[catalog_id] = (now, stamp)
    return stamp


def _base_lots(catalog_id):
    """Parsed lots for a catalog, built once per catalog version (reading and parsing 3,400 hips
    took about 0.6 s, and every hip page and the hip list paid it)."""
    if not _valid_id(catalog_id):
        return None
    stamp = _catalog_stamp(catalog_id)
    if stamp is None:
        return None
    hit = _LOTS_CACHE.get(catalog_id)
    if hit and hit[0] == stamp:
        return hit[1]
    catalog = _read_catalog(catalog_id)
    if catalog is None:
        return None
    sale_year = _year(catalog_id)
    _, rows = catalog
    lots = []
    for row in rows:
        lot = _lot_from_row(row, sale_year=sale_year)
        lot["sale_year"] = sale_year
        if lot["hip"] or lot["name"]:
            lots.append(lot)
    # Catalog modal age fills any remaining blank sold_as (mixed/untitled sales).
    try:
        from crossr_core import projections as proj_svc

        proj_svc.annotate_lot_kinds(
            lots, sale_year=sale_year, catalog_title=str(catalog_id or "")
        )
    except Exception:
        pass
    lots.sort(key=lambda lot: (_hip_sort(lot["hip"]), lot["hip"]))
    _LOTS_CACHE[catalog_id] = (stamp, lots)
    return lots


def list_lots(catalog_id):
    """Every lot in the catalog with its live stars / notes."""
    base = _base_lots(catalog_id)
    if base is None:
        return None
    marks = railway_db.read_lot_marks(catalog_id)
    out = []
    for lot in base:
        lot = dict(lot)
        _apply_mark(lot, marks.get(lot["hip"]))
        out.append(lot)
    return out


def hip_key(hip):
    """'0050', '050' and '50' are one hip (catalogs differ on zero-padding); '50a' == '0050A'."""
    return re.sub(r"^0+(?=\d)", "", str(hip or "").strip().upper())


def canonical_hip(catalog_id, hip):
    """The hip as this catalog writes it, for any padding of the number; None when absent."""
    want = str(hip or "").strip()
    lots = _base_lots(catalog_id) or []
    if any(lot["hip"] == want for lot in lots):
        return want
    key = hip_key(want)
    return next((lot["hip"] for lot in lots if hip_key(lot["hip"]) == key), None)


def get_lot(catalog_id, hip):
    lots = list_lots(catalog_id)
    if lots is None:
        return None
    want = str(hip).strip()
    for lot in lots:
        if lot["hip"] == want:
            return lot
    want = hip_key(want)
    return next((lot for lot in lots if hip_key(lot["hip"]) == want), None)


def update_lot(catalog_id, hip, starred=None, notes=None):
    catalog = _read_catalog(catalog_id)
    if catalog is None:
        return None
    _, rows = catalog
    want = canonical_hip(catalog_id, hip) or str(hip).strip()
    found = next((row for row in rows if _blank(_col(row, "HIP", "HIP NUMBER", "LOT")) == want), None)
    if found is None:
        return None
    mark = railway_db.set_lot_mark(catalog_id, want, starred=starred, notes=notes)
    return _apply_mark(_lot_from_row(found), mark)


def filter_lots(lots, sex=None, color=None, max_price=None, height=None, starred=None, exclude_hips=None, only_hips=None):
    excluded = {str(hip).strip() for hip in (exclude_hips or []) if str(hip).strip()}
    only = {str(hip).strip() for hip in (only_hips or []) if str(hip).strip()}
    kept = []
    for lot in lots:
        hip = str(lot.get("hip") or "")
        if only and hip not in only:
            continue
        if not only and hip in excluded:
            continue
        if sex and (lot.get("sex_letter") or "") != str(sex).upper()[:1]:
            continue
        if color:
            coat = (lot.get("color") or "").lower()
            if not coat or str(color).lower() not in coat:
                continue
        if max_price not in (None, ""):
            # Upcoming lots have no hammer price yet: use the Expected Market price the API attached.
            price = lot.get("price_num") or lot.get("filter_price") or 0
            if not price or price > float(max_price):
                continue
        if height:
            hands = (lot.get("height") or "").lower()
            if not hands or str(height).lower() not in hands:
                continue
        if starred and not lot.get("starred"):
            continue
        kept.append(lot)
    return kept

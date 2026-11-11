"""
Master-prompt breeding and racing value models (Phases 10-11). Method: docs/breeding-value.md.

Phase 10 — expected stallion deal value of a colt (one value):

    Σ (initial stud fee of each of his sire's sons that stood at stud × 400)
    / sire's colts of racing age,   colts of racing age = (total foals − foals of the last 3 crops) / 2

  A sire whose first crop is under seven years old has too few sons old enough to
  be at stud: his value is the mean of comparable North American stallions' values
  (initial or current fee within ±10%, first crop at least seven years old), each
  scaled by the fee ratio (target fee / comparable fee).

Phase 11 — broodmare added value (sale price / sire's fee at conception), the
broodmare-sire fee relationship, the future covering-fee estimate, filly / mare
breeding value, and racing value (earnings per runner × runners / foals aged 3+).

Stallions are identified by their stud cards (sample_db.stallion_roster_from_fees,
identity-checked first season and foaling year); a son is matched to his sire by
the sire and foaling year the rankings record for him, never by name alone.
Everything is built from Postgres once per table version and cached.
"""

from __future__ import annotations

import math
import re
import statistics
import threading
from typing import Any, Dict, List, Optional, Tuple

STUD_DEAL_MULTIPLIER = 400  # stallion deal value = initial stud fee × 400
RACING_AGE = 3  # a foal is of racing age (and counts as a colt/foal "aged 3+") from age 3
COMPARABLE_FEE_BAND = 0.10  # ±10% of the target's initial or current fee
# A sire's first son reaches stud a median 7 years after his first crop (730 sires), so a
# sire is valued from his own sons only once his first crop is 7, and comparables must be too.
COMPARABLE_MIN_CROP_AGE = 7
EXPECTED_LIVE_FOALS = 7.0  # project rule: 10 foals/broodmare × 0.70 live-foal rate
AV_SHRINK_K = None  # set from data in _added_value_tables (empirical Bayes)
MIN_BMS_LOTS = 20  # below this a broodmare sire's own averages are "insufficient"

_lock = threading.RLock()
_cache: Dict[str, Tuple[tuple, Any]] = {}


def _versions(*tables: str) -> tuple:
    from crossr_core import sample_db

    return tuple(sample_db.table_version(t) for t in tables)


def _cached(name: str, tables: Tuple[str, ...], build):
    version = _versions(*tables)
    with _lock:
        hit = _cache.get(name)
        if hit and hit[0] == version:
            return hit[1]
    value = build()
    with _lock:
        _cache[name] = (version, value)
    return value


def _key(name: Any) -> str:
    from crossr_core.sample_db import name_key

    return name_key(re.sub(r"\s*\(\d+\)\s*$", "", str(name or "")))


def _year_now() -> int:
    from crossr_core.identity import current_year

    return current_year()


def _num(raw: Any) -> float:
    try:
        return float(str(raw).replace(",", "").replace("$", "").strip() or 0)
    except ValueError:
        return 0.0


def _truthy(raw: Any) -> bool:
    return str(raw or "").strip().lower() in {"1", "true", "t", "yes", "y"}


def _quantile(sorted_vals: List[float], q: float) -> Optional[float]:
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


# =========================================================== stallion table
def stallions() -> Dict[str, dict]:
    """name_key -> stallion facts from his stud card, fee history and breeding reports."""
    return _cached("stallions", ("stud_fees", "breeding_seasons", "stallion_rankings", "race_horses"), _build_stallions)


def _build_stallions() -> Dict[str, dict]:
    from crossr_core import identity, sample_db

    cards = sample_db.stallion_roster_from_fees()
    fees = sample_db.stud_fee_history()
    foals: Dict[str, Dict[int, float]] = {}
    books: Dict[str, Dict[int, float]] = {}
    for row in sample_db.load_table("breeding_seasons"):
        key = _key(row.get("stallion"))
        try:
            season = int(str(row.get("breeding_year")).strip())
        except ValueError:
            continue
        if key:
            foals.setdefault(key, {})
            foals[key][season + 1] = foals[key].get(season + 1, 0.0) + _num(row.get("live_foals"))
            books.setdefault(key, {})
            books[key][season] = books[key].get(season, 0.0) + _num(row.get("mares_bred"))
    # race_horses fallback for a stallion's sire: unique (name, foaling year) rows only
    rh: Dict[Tuple[str, int], set] = {}
    for row in sample_db.load_table("race_horses"):
        try:
            fy = int(str(row.get("foal_year") or "").strip()[:4])
        except ValueError:
            continue
        k = _key(row.get("name_key") or row.get("display_name"))
        if k and row.get("sire"):
            rh.setdefault((k, fy), set()).add(_key(row["sire"]))

    out: Dict[str, dict] = {}
    for display, card in cards.items():
        key = card.get("_name_key") or _key(display)
        first = int(card.get("Stud Duty Year") or 0)
        if not first:
            continue
        born = int(card.get("Year Born") or 0)
        hist = {y: f for y, f in (fees.get(key) or {}).items() if y >= first}
        isf_year = min((y for y in hist if first <= y <= first + 2), default=None)
        sire = identity.card_sire(display, born or None) if born else ""
        if not sire and born and len(rh.get((key, born), ())) == 1:
            sire = next(iter(rh[(key, born)]))
        out[key] = {
            "name": display,
            "first_season": first,
            "first_crop": first + 1,
            "born": born,
            "sire": sire,
            "initial_fee": float(hist[isf_year]) if isf_year is not None else None,
            "initial_fee_year": isf_year,
            "fee_now": float(card.get("Stud Fee") or 0) or None,
            "fee_now_year": card.get("Latest Season"),
            "fees": hist,
            "foals": {c: n for c, n in (foals.get(key) or {}).items() if c >= first + 1},
            "first_book": (books.get(key) or {}).get(first, 0.0),
            "north_america": identity.stands_in_north_america(card),
        }
    return out


def sons_at_stud() -> Dict[str, List[str]]:
    def build():
        table = stallions()
        sons: Dict[str, List[str]] = {}
        for key, s in table.items():
            sire = table.get(s["sire"]) if s["sire"] else None
            # identity check: the son was foaled after his sire's first crop could be
            if sire and s["born"] and s["born"] >= sire["first_crop"] and s["first_season"] <= _year_now():
                sons.setdefault(s["sire"], []).append(key)
        return sons

    return _cached("sons", ("stud_fees", "breeding_seasons", "stallion_rankings", "race_horses"), build)


def colts_of_racing_age(s: dict, year: Optional[int] = None) -> float:
    """(Total foals − foals born in the last three years) / 2, i.e. half his foals aged 3+."""
    year = year or _year_now()
    return sum(n for c, n in s["foals"].items() if c <= year - RACING_AGE) / 2.0


def foals_aged_3_plus(s: dict, year: Optional[int] = None) -> float:
    year = year or _year_now()
    return sum(n for c, n in s["foals"].items() if c <= year - RACING_AGE)


def isf_imputer() -> dict:
    """log initial fee ~ log first-season book, fitted on stallions whose initial fee is on file.

    The fee table often starts only when a stallion first makes a ranking list, so
    many sons have no initial fee; leaving them out would value them at $0.
    """

    def build():
        pts = [(math.log(s["first_book"]), math.log(s["initial_fee"])) for s in stallions().values()
               if s["initial_fee"] and s["first_book"] > 0]
        xs, ys = zip(*pts)
        mx, my = statistics.mean(xs), statistics.mean(ys)
        b = sum((x - mx) * (y - my) for x, y in pts) / sum((x - mx) ** 2 for x in xs)
        a = my - b * mx
        sd = statistics.pstdev([y - (a + b * x) for x, y in pts])
        r = statistics.correlation(xs, ys)
        return {"a": a, "b": b, "sd": sd, "r": r, "n": len(pts)}

    return _cached("isf_imputer", ("stud_fees", "breeding_seasons", "stallion_rankings", "race_horses"), build)


def initial_fee_or_estimate(s: dict) -> Tuple[Optional[float], bool]:
    """(initial fee, estimated?): the fee on file, else the first-season-book estimate (mean of log-normal)."""
    if s["initial_fee"]:
        return s["initial_fee"], False
    if s["first_book"] > 0:
        m = isf_imputer()
        return math.exp(m["a"] + m["b"] * math.log(s["first_book"]) + m["sd"] ** 2 / 2), True
    return None, False


# =========================================================== Phase 10
def own_deal_value(key: str) -> Optional[dict]:
    """Phase 10.1 for a sire with racing-age progeny: Σ sons' ISF × 400 / colts of racing age."""
    table = stallions()
    s = table.get(key)
    if not s:
        return None
    colts = colts_of_racing_age(s)
    if colts <= 0:
        return None
    sons, unpriced = [], []
    for son_key in sons_at_stud().get(key, []):
        son = table[son_key]
        if not son["north_america"]:
            continue
        fee, estimated = initial_fee_or_estimate(son)
        if fee:
            sons.append({"stallion": son["name"], "initial_fee": fee, "estimated": estimated,
                         "initial_fee_year": son["initial_fee_year"] if not estimated else son["first_season"],
                         "first_book": son["first_book"], "deal_value": fee * STUD_DEAL_MULTIPLIER})
        else:
            unpriced.append(son["name"])
    sons.sort(key=lambda r: -r["deal_value"])
    total = sum(r["deal_value"] for r in sons)
    on_file = sum(r["deal_value"] for r in sons if not r["estimated"])
    return {"value": total / colts, "value_fees_on_file_only": on_file / colts, "total_deal_value": total,
            "colts_of_racing_age": colts, "sons": sons, "sons_estimated_fee": sum(r["estimated"] for r in sons),
            "sons_without_fee": unpriced}


def limited_progeny(s: dict, year: Optional[int] = None) -> bool:
    """First crop under COMPARABLE_MIN_CROP_AGE years old: too few sons old enough to be at stud."""
    year = year or _year_now()
    return year - s["first_crop"] < COMPARABLE_MIN_CROP_AGE


def comparables(key: str) -> List[dict]:
    """Phase 10.2: NA stallions with initial or current fee within ±10%, first crop ≥ 7 years old."""
    table = stallions()
    s = table.get(key)
    if not s:
        return []
    year = _year_now()
    out = []
    for ckey, c in table.items():
        if ckey == key or not c["north_america"] or year - c["first_crop"] < COMPARABLE_MIN_CROP_AGE:
            continue
        basis = None
        for label, mine, theirs in (("initial", s["initial_fee"], c["initial_fee"]),
                                    ("current", s["fee_now"], c["fee_now"])):
            if mine and theirs and abs(theirs - mine) <= COMPARABLE_FEE_BAND * mine:
                basis = (label, mine, theirs)
                break
        if not basis:
            continue
        own = own_deal_value(ckey)
        if not own:
            continue
        label, mine, theirs = basis
        factor = mine / theirs
        out.append({"stallion": c["name"], "matched_on": f"{label} fee", "target_fee": mine,
                    "comparable_fee": theirs, "factor": factor, "deal_value": own["value"],
                    "normalized_value": own["value"] * factor, "sons_at_stud": len(own["sons"]),
                    "colts_of_racing_age": own["colts_of_racing_age"]})
    out.sort(key=lambda r: abs(math.log(r["factor"])))
    return out


def _memo(name: str, key: str, build):
    table = _cached(f"memo_{name}", ("stud_fees", "breeding_seasons", "stallion_rankings", "race_horses"), dict)
    if key not in table:
        table[key] = build()
    return table[key]


def colt_deal_value(sire: str) -> Optional[dict]:
    """Expected stallion deal value of one colt by `sire` (one value), with its workings."""
    return _memo("colt", _key(sire), lambda: _colt_deal_value(sire))


def _colt_deal_value(sire: str) -> Optional[dict]:
    key = _key(sire)
    s = stallions().get(key)
    if not s:
        return None
    if not limited_progeny(s):
        own = own_deal_value(key)
        if own is None:
            return None
        return {"value": own["value"], "method": "sons_of_sire", "sire": s["name"],
                "formula": "Σ (sons' initial stud fee × 400) / colts of racing age",
                **own}
    comps = comparables(key)
    if not comps:
        return {"value": None, "method": "no_comparables", "sire": s["name"],
                "note": "First crop under seven years old and no North American stallion within ±10% of his fee."}
    value = statistics.mean(c["normalized_value"] for c in comps)
    return {"value": value, "method": "comparable_stallions", "sire": s["name"],
            "formula": "mean of comparable stallions' deal value per colt × (target fee / comparable fee)",
            "initial_fee": s["initial_fee"], "fee_now": s["fee_now"], "comparables": comps}


# =========================================================== Phase 11.1 added value
def _sire_fee_at(table: Dict[str, dict], fees: dict, sire_key: str, year: int) -> Tuple[Optional[float], bool, Optional[int]]:
    """(fee, historical?, fee year): fee in `year`, else his latest earlier fee (dead / pensioned / unlisted)."""
    hist = fees.get(sire_key) or {}
    s = table.get(sire_key)
    if s:  # only seasons of the stallion behind the card, not an older namesake
        hist = {y: f for y, f in hist.items() if y >= s["first_season"]}
    if year in hist:
        return float(hist[year]), False, year
    prior = [y for y in hist if y < year]
    if prior:
        y = max(prior)
        return float(hist[y]), True, y
    return None, False, None


def added_value_lots() -> List[dict]:
    """Every valid yearling / weanling sale with its added value (price / sire fee at conception)."""
    return _cached("av_lots", ("sales_lots", "stud_fees", "breeding_seasons", "stallion_rankings", "race_horses"),
                   _build_av_lots)


def _build_av_lots() -> List[dict]:
    from crossr_core import sample_db

    table = stallions()
    fees = sample_db.stud_fee_history()
    dam_sire = _dam_sires()
    rows = _sales_rows()
    seen = set()
    out = []
    for cy, title, src, hip, nk, sire, dam, damsire, by, price, rna, out_flag in rows:
        try:
            cy, by = int(cy), int(str(by)[:4])
        except (TypeError, ValueError):
            continue
        age = cy - by
        p = _num(price)
        if age not in (0, 1) or p <= 0 or _truthy(rna) or _truthy(out_flag):
            continue
        ident = (src, hip, nk)
        if ident in seen:  # duplicate transaction (same catalog file, hip and horse)
            continue
        seen.add(ident)
        sk = _key(sire)
        conception = by - 1  # covered the year before foaling (= sale year − 2 for yearlings)
        fee, historical, fee_year = _sire_fee_at(table, fees, sk, conception)
        if not fee:
            continue
        dk = _key(dam)
        bms = _key(damsire) or dam_sire.get(dk, "")
        out.append({"year": cy, "kind": "Yearling" if age == 1 else "Weanling", "sire": sk, "dam": dk,
                    "bms": bms, "price": p, "fee": fee, "fee_year": fee_year, "historical_fee": historical,
                    "added_value": p / fee})
    return out


def _sales_rows() -> List[tuple]:
    """(catalog_year, catalog_title, source_file, hip, name_key, sire, dam, damsire, birth_year,
    price_num, is_rna, is_out) for every sales lot."""
    from crossr_core import railway_db

    with railway_db.connect() as conn:
        return conn.execute(
            "SELECT catalog_year, catalog_title, source_file, hip, name_key, sire, dam, damsire, "
            "birth_year, price_num, is_rna, is_out FROM sales_lots"
        ).fetchall()


def _dam_sires() -> Dict[str, str]:
    from crossr_core import sample_db

    out: Dict[str, str] = {}
    for row in sample_db.load_table("race_horses"):
        if str(row.get("sex") or "").upper()[:1] in ("F", "M") and row.get("sire"):
            k = _key(row.get("name_key") or row.get("display_name"))
            if k:
                out.setdefault(k, _key(row["sire"]))
    return out


def lots_by(field: str) -> Dict[str, List[dict]]:
    """Added-value lots grouped by "bms" or "dam" (built once per data version)."""

    def build():
        idx: Dict[str, List[dict]] = {}
        for lot in added_value_lots():
            if lot[field]:
                idx.setdefault(lot[field], []).append(lot)
        return idx

    return _cached(f"lots_by_{field}", ("sales_lots", "stud_fees", "breeding_seasons", "stallion_rankings", "race_horses"), build)


def _summary(values: List[float]) -> dict:
    v = sorted(values)
    return {"n": len(v), "mean": statistics.mean(v) if v else None, "median": _quantile(v, 0.5),
            "p25": _quantile(v, 0.25), "p75": _quantile(v, 0.75)}


def added_value_tables() -> dict:
    """Overall, by broodmare sire, by sale year and by yearling/weanling (Phase 11.1)."""

    def build():
        lots = added_value_lots()
        by: Dict[str, Dict[Any, List[float]]] = {"bms": {}, "year": {}, "kind": {}}
        for lot in lots:
            for field in by:
                if lot[field if field != "bms" else "bms"]:
                    by[field].setdefault(lot[field], []).append(lot["added_value"])
        overall = _summary([lot["added_value"] for lot in lots])
        # empirical-Bayes shrinkage of a broodmare sire's mean log added value toward the overall mean
        logs = {k: [math.log(x) for x in v] for k, v in by["bms"].items() if len(v) >= 2}
        grand = statistics.mean(math.log(lot["added_value"]) for lot in lots)
        within = statistics.mean(statistics.variance(v) for v in logs.values())
        between = max(statistics.variance([statistics.mean(v) for v in logs.values()])
                      - statistics.mean(within / len(v) for v in logs.values()), 1e-6)
        return {
            "overall": overall,
            "by_bms": {k: _summary(v) for k, v in by["bms"].items()},
            "by_year": {k: _summary(v) for k, v in sorted(by["year"].items())},
            "by_kind": {k: _summary(v) for k, v in by["kind"].items()},
            "log_grand_mean": grand,
            "log_within_var": within,
            "shrink_k": within / between,
            "historical_fee_share": sum(lot["historical_fee"] for lot in lots) / max(len(lots), 1),
        }

    return _cached("av_tables", ("sales_lots", "stud_fees", "breeding_seasons", "stallion_rankings", "race_horses"), build)


def bms_added_value(bms: str) -> dict:
    """A broodmare sire's added value, shrunk toward the overall mean by how many lots back it."""
    t = added_value_tables()
    k = _key(bms)
    own = t["by_bms"].get(k) or {"n": 0}
    lots = [math.log(lot["added_value"]) for lot in lots_by("bms").get(k, [])]
    n = len(lots)
    w = n / (n + t["shrink_k"]) if n else 0.0
    log_mean = w * (statistics.mean(lots) if lots else 0.0) + (1 - w) * t["log_grand_mean"]
    # mean of a log-normal multiple: exp(mu + within_var / 2)
    return {"multiple": math.exp(log_mean + t["log_within_var"] / 2), "median_multiple": math.exp(log_mean),
            "n_lots": n, "own_weight": w, "own": own, "sufficient": n >= MIN_BMS_LOTS}


# =========================================================== Phase 11.2-3 fee relationship
def bms_fee_relationship() -> dict:
    """log(covering sire's fee at conception) ~ log(broodmare sire's highest fee), fitted on all lots."""

    def build():
        from crossr_core import sample_db

        fees = sample_db.stud_fee_history()
        table = stallions()
        top: Dict[str, float] = {}
        for k, s in table.items():
            if s["fees"]:
                top[k] = max(s["fees"].values())  # "latest or highest": highest is never lower than latest
        xs, ys = [], []
        for lot in added_value_lots():
            if lot["bms"] in top and not lot["historical_fee"]:
                xs.append(math.log(top[lot["bms"]]))
                ys.append(math.log(lot["fee"]))
        n = len(xs)
        mx, my = statistics.mean(xs), statistics.mean(ys)
        sxx = sum((x - mx) ** 2 for x in xs)
        b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
        a = my - b * mx
        resid = [y - (a + b * x) for x, y in zip(xs, ys)]
        s2 = sum(r * r for r in resid) / (n - 2)
        se_b = math.sqrt(s2 / sxx)
        r2 = 1 - sum(r * r for r in resid) / sum((y - my) ** 2 for y in ys)
        return {"a": a, "b": b, "se_b": se_b, "t": b / se_b, "r2": r2, "n": n, "s2": s2, "mx": mx, "sxx": sxx,
                "x_min": min(xs), "x_max": max(xs), "_fees_n": len(fees)}

    return _cached("bms_rel", ("sales_lots", "stud_fees", "breeding_seasons", "stallion_rankings", "race_horses"), build)


def expected_cover_fee(bms_fee: float) -> dict:
    """Estimated stud fee of the stallions a mare by this broodmare sire will be bred to (an estimate)."""
    r = bms_fee_relationship()
    x = math.log(bms_fee)
    clipped = min(max(x, r["x_min"]), r["x_max"])  # no extrapolation beyond the fees seen
    mu = r["a"] + r["b"] * clipped
    se_pred = math.sqrt(r["s2"] * (1 + 1 / r["n"] + (clipped - r["mx"]) ** 2 / r["sxx"]))
    z = 1.2815515655446004
    return {"estimate": math.exp(mu + r["s2"] / 2), "median": math.exp(mu), "low80": math.exp(mu - z * se_pred),
            "high80": math.exp(mu + z * se_pred), "clipped": clipped != x, "label": "estimate (not an advertised fee)",
            "method": f"log fee at conception = {r['a']:.2f} + {r['b']:.2f}·log broodmare sire's highest fee "
                      f"(n = {r['n']:,}, R² = {r['r2']:.2f}, slope t = {r['t']:.0f})"}


# =========================================================== Phase 11.4 broodmare breeding value
def broodmare_value(*, mare: str = "", mare_sire: str = "") -> Optional[dict]:
    """Filly / mare breeding value: realized foal sales + remaining foals × expected cover fee × added value."""
    table = stallions()
    mk = _key(mare)
    produced = list(lots_by("dam").get(mk, [])) if mk else []
    sire_key = _key(mare_sire)
    if not sire_key and produced:
        sire_key = produced[0]["bms"]
    av = bms_added_value(sire_key) if sire_key else None
    # the mare's own record, shrunk toward her sire's (or the overall) added value
    t = added_value_tables()
    base_log = math.log(av["median_multiple"]) if av else t["log_grand_mean"]
    own_logs = [math.log(lot["added_value"]) for lot in produced]
    w = len(own_logs) / (len(own_logs) + t["shrink_k"]) if own_logs else 0.0
    log_mult = w * (statistics.mean(own_logs) if own_logs else 0.0) + (1 - w) * base_log
    multiple = math.exp(log_mult + t["log_within_var"] / 2)
    if produced:
        recent = sorted(produced, key=lambda lot: -lot["year"])[:3]
        cover_fee = statistics.mean(lot["fee"] for lot in recent)
        fee_basis = f"mean covering fee of her last {len(recent)} sold foals"
        fee_est = None
    else:
        s = table.get(sire_key)
        top = max(s["fees"].values()) if s and s["fees"] else None
        if not top:
            return None
        fee_est = expected_cover_fee(top)
        cover_fee = fee_est["estimate"]
        fee_basis = "estimated from her sire's highest stud fee (broodmare-sire relationship)"
    realized = sum(lot["price"] for lot in produced)
    remaining = max(EXPECTED_LIVE_FOALS - len(produced), 0.0)
    per_foal = cover_fee * multiple
    value = realized + remaining * per_foal
    q = sorted(lot["added_value"] for lot in lots_by("bms").get(sire_key, [])) if sire_key else []
    low = high = None
    if len(q) >= MIN_BMS_LOTS:
        low = realized + remaining * cover_fee * _quantile(q, 0.25)
        high = realized + remaining * cover_fee * _quantile(q, 0.75)
    return {"value": value, "low": low, "high": high, "realized": realized, "foals_sold": len(produced),
            "remaining_foals": remaining, "cover_fee": cover_fee, "cover_fee_basis": fee_basis,
            "cover_fee_estimate": fee_est, "added_value_multiple": multiple, "own_weight": w,
            "broodmare_sire": table[sire_key]["name"] if sire_key in table else mare_sire,
            "broodmare_sire_added_value": av}


# =========================================================== Phase 11.5 racing value
def racing_value(sire: str) -> Optional[dict]:
    """Average earnings per runner × (runners / foals aged 3+), from the leading-sire tables."""
    return _memo("racing", _key(sire), lambda: _racing_value(sire))


def _racing_value(sire: str) -> Optional[dict]:
    key = _key(sire)
    s = stallions().get(key)
    if not s:
        return None
    own = _own_racing(key)
    if own:  # his own runners, however young his crops (the 7-year line is about sons at stud)
        return {**own, "method": "own_progeny"}
    if limited_progeny(s):
        comps = [c for c in comparables(key)]
        vals = []
        for c in comps:
            r = _own_racing(_key(c["stallion"]))
            if r:
                vals.append((r["value"] * c["factor"], c, r))
        if not vals:
            return None
        return {"value": statistics.mean(v for v, _, _ in vals), "method": "comparable_stallions",
                "comparables": [{**c, "racing_value": r["value"], "normalized": v} for v, c, r in vals]}
    return None


def _own_racing(key: str) -> Optional[dict]:
    seasons = _ranking_seasons().get(key)
    s = stallions().get(key)
    if not seasons or not s:
        return None
    earnings = sum(e for e, _ in seasons.values())
    runners = sum(r for _, r in seasons.values())
    foals3 = foals_aged_3_plus(s)
    if runners <= 0 or foals3 <= 0:
        return None
    per_runner = earnings / runners
    ratio = runners / foals3
    return {"value": per_runner * ratio, "earnings_per_runner": per_runner, "runners": runners,
            "foals_aged_3_plus": foals3, "runners_per_foal": ratio, "seasons": len(seasons),
            "currency": "USD"}


def _ranking_seasons() -> Dict[str, Dict[int, Tuple[float, float]]]:
    """name_key -> {year: (progeny earnings, runners)} from North American leading-sire lists.

    One row per stallion and season: the General list, else his crop list that year
    (First/Second/Third-Crop), so a season is never counted twice. Seasons in which
    he made no list are missing, which understates a lesser sire's earnings.
    """

    def build():
        from crossr_core import sample_db

        # Two-Year-Old rows cover only part of a sire's runners that season, so they are not used.
        prefer = {"General": 0, "First-Crop": 1, "Second-Crop": 1, "Third-Crop": 1}
        best: Dict[Tuple[str, int], Tuple[int, float, float]] = {}
        for row in sample_db.load_table("stallion_rankings"):
            lst = (row.get("list") or "").strip()
            if lst not in prefer:
                continue
            try:
                y = int(str(row.get("year")).strip())
            except ValueError:
                continue
            k = _key(row.get("stallion"))
            e, r = _num(row.get("year_earnings")), _num(row.get("runners"))
            if not k or r <= 0:
                continue
            cur = best.get((k, y))
            if cur is None or prefer[lst] < cur[0]:
                best[(k, y)] = (prefer[lst], e, r)
        out: Dict[str, Dict[int, Tuple[float, float]]] = {}
        for (k, y), (_, e, r) in best.items():
            out.setdefault(k, {})[y] = (e, r)
        return out

    return _cached("rank_seasons", ("stallion_rankings",), build)

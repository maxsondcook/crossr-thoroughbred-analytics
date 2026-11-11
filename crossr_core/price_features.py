"""
Sale-price modeling dataset and features (master prompt Phases 12 and 14). Method: docs/price-model.md.

One row per sale transaction (never one per horse). Every feature is computed as
of the sale: only information dated before the sale year, except stud fees, which
count through the sale year (a season's fee is advertised before that season's sales).
`truncation_check` proves it: features rebuilt from source tables cut off at the
sale year must equal the full build.

    sources = Sources()                                        # Postgres (DATABASE_URL)
    sources = Sources(csv_dir="data", csv_tables=("sales_lots",))   # a table from a local snapshot
    tx, exclusions = build_transactions(sources)
    feats = build_features(tx, sources)

Feature groups (FEATURE_GROUPS): A sire fees / progeny sales / progeny racing,
B dam production, C dam race record, D broodmare-sire fees and sales, E pedigree,
F sales-price progression, G horse and sale.

Training-time only (pandas / numpy); the app keeps the pure-Python sales_model.
"""

from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from crossr_core import sales_model as sm

DATASET_VERSION = "1"
MAX_VALID_PRICE = 20_000_000  # highest auction price on record is well under this
FEE_LOOKBACK = 2  # years a missing conception-year fee may borrow the latest earlier fee
MISSING_FEE_FILL = 10_000  # placeholder fee (USD) where none is known; flagged by fee_missing
FEE_RUN_GAP = 4  # years with no listed fee that separate two same-named stallions

# ------------------------------------------------------------------ sources
TABLE_COLUMNS = {
    "sales_lots": ["catalog_year", "catalog_title", "source_file", "hip", "name", "name_key", "sex_letter",
                   "sire", "dam", "damsire", "consignor", "birth_year", "price_num", "is_rna", "is_out"],
    "stud_fees": ["Year", "Stallion", "Stud Fee"],
    "breeding_seasons": ["breeding_year", "stallion", "mares_bred", "live_foals"],
    "race_horses": ["id", "name_key", "foal_year", "sex", "sire", "dam", "best_class", "status"],
    "race_seasons": ["horse_id", "year", "starts", "firsts", "earnings"],
    "race_starts": ["horse_id", "race_date", "place", "grade", "race_name", "esf"],
    "graded_stakes": ["Horse Name", "Place", "Race Year", "Foaling Year", "Grade"],
    "stallion_rankings": ["year", "list", "stallion", "runners", "winners", "stakes_winners",
                          "graded_stakes_winners", "year_earnings"],
    "pedigrees": ["key", "tree"],
}


class Sources:
    """Canonical source tables as DataFrames: Postgres by default, or a CSV snapshot per table."""

    def __init__(self, csv_dir: Optional[str] = None, csv_tables: Sequence[str] = (), frames: Optional[dict] = None):
        self.csv_dir = Path(csv_dir) if csv_dir else None
        self.csv_tables = set(csv_tables)
        self._frames: Dict[str, pd.DataFrame] = dict(frames or {})

    def provenance(self, table: str) -> str:
        if table in self._frames and table not in self.csv_tables and self.csv_dir is None:
            return "in-memory"
        return f"csv:{self.csv_dir / (table + '.csv')}" if table in self.csv_tables else "postgres"

    def table(self, name: str) -> pd.DataFrame:
        frame = self._table(name)
        if name == "race_horses" and "status" in frame:
            # duplicate Equibase records merged into another row (status 'merged') are not horses
            frame = frame[frame.status.astype(str) != "merged"]
        return frame

    def _table(self, name: str) -> pd.DataFrame:
        if name not in self._frames:
            cols = TABLE_COLUMNS[name]
            if name in self.csv_tables:
                frame = pd.read_csv(self.csv_dir / f"{name}.csv", usecols=lambda c: c in cols,
                                    dtype=str, keep_default_na=False)
                self._frames[name] = frame.reindex(columns=cols, fill_value="")
            else:
                from crossr_core import railway_db

                quoted = ", ".join('"' + c + '"' for c in cols)
                with railway_db.connect() as conn:
                    cur = conn.execute(f"SELECT {quoted} FROM {name}")
                    rows = cur.fetchall()
                frame = pd.DataFrame(rows, columns=cols)
                # jsonb trees stay objects; everything else is text, like the CSV snapshots
                text_cols = [c for c in cols if c != "tree"]
                frame[text_cols] = frame[text_cols].astype(str).replace("None", "")
                self._frames[name] = frame
        return self._frames[name]

    def truncated(self, year: int) -> "Sources":
        """What was known before sales in `year`: dated rows before it; year-`year` lots without outcomes."""
        out = {}
        s = self.table("sales_lots").copy()
        cy = pd.to_numeric(s.catalog_year, errors="coerce")
        s = s[cy <= year].copy()
        now = pd.to_numeric(s.catalog_year, errors="coerce") == year
        s.loc[now, ["price_num", "is_rna", "is_out"]] = ""  # the outcome of this year's sales is unknown
        out["sales_lots"] = s
        f = self.table("stud_fees")
        out["stud_fees"] = f[pd.to_numeric(f.Year, errors="coerce") <= year]
        b = self.table("breeding_seasons")
        out["breeding_seasons"] = b[pd.to_numeric(b.breeding_year, errors="coerce") < year]
        out["race_horses"] = self.table("race_horses")  # registry facts (parents, foaling year)
        rs = self.table("race_seasons")
        out["race_seasons"] = rs[pd.to_numeric(rs.year, errors="coerce") < year]
        st = self.table("race_starts")
        out["race_starts"] = st[st.race_date.astype(str) < f"{year}-01-01"]
        g = self.table("graded_stakes")
        out["graded_stakes"] = g[pd.to_numeric(g["Race Year"], errors="coerce") < year]
        r = self.table("stallion_rankings")
        out["stallion_rankings"] = r[pd.to_numeric(r.year, errors="coerce") < year]
        out["pedigrees"] = self.table("pedigrees")
        return Sources(frames=out)


# ------------------------------------------------------------------ transactions
_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
     "november", "december"], start=1)}
_SEASON_MONTH = (("winter", 2), ("spring", 4), ("summer", 7), ("saratoga", 8), ("fall", 10), ("autumn", 10))


def sale_month(title: str) -> Optional[int]:
    """Month inferred from the catalog title (no sale dates are stored)."""
    t = str(title or "").lower()
    for name, m in _MONTHS.items():
        if re.search(rf"\b{name}\b", t):
            return m
    for word, m in _SEASON_MONTH:
        if word in t:
            return m
    return None


def venue(title: str) -> str:
    t = str(title or "").lower()
    if "keeneland" in t:
        return "keeneland"
    if "fasig" in t:
        return "fasig_tipton"
    if "obs" in t or "ocala" in t:
        return "obs"
    return "other"


def _flag(raw) -> bool:
    return str(raw or "").strip().lower() in {"1", "true", "t", "yes"}


def category(age: float, sex: str, title: str) -> str:
    t = str(title or "").lower()
    if age == 0:
        return "weanling"
    if age == 1:
        return "yearling"
    if age == 2 and ("two year" in t or "2yo" in t or "in training" in t):
        return "two_year_old_in_training"
    if str(sex or "").upper()[:1] == "M" or ("broodmare" in t and age >= 3):
        return "broodmare"
    if age >= 2:
        return "horse_of_racing_age"
    return "unknown"


def build_transactions(sources: Sources) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """(transactions, exclusions): every sales_lots row once, with ids, category, status and quality flags."""
    s = sources.table("sales_lots").copy()
    s["sale_year"] = pd.to_numeric(s.catalog_year, errors="coerce")
    s["birth_year_n"] = pd.to_numeric(s.birth_year.astype(str).str[:4], errors="coerce")
    s["age"] = s.sale_year - s.birth_year_n
    s["sire_k"] = s.sire.map(sm.name_key)
    s["dam_k"] = s.dam.map(sm.name_key)
    s["damsire_k"] = s.damsire.map(sm.name_key)
    s["transaction_id"] = [
        hashlib.sha1(f"{a}|{b}|{c}".encode()).hexdigest()[:16] for a, b, c in zip(s.source_file, s.hip, s.name_key)
    ]
    # Unnamed youngstock change names later; sire + dam + foaling year identify the horse.
    s["horse_id"] = s.sire_k + "|" + s.dam_k + "|" + s.birth_year_n.fillna(-1).astype(int).astype(str)
    s["category"] = [category(a, x, t) for a, x, t in zip(s.age, s.sex_letter, s.catalog_title)]
    s["sale_month"] = s.catalog_title.map(sale_month)
    s["venue"] = s.catalog_title.map(venue)
    s["sale_key"] = s.catalog_title.map(sm.sale_key)
    s["price"] = pd.to_numeric(s.price_num, errors="coerce")
    s["rna"] = s.is_rna.map(_flag)
    s["out"] = s.is_out.map(_flag)
    s["currency"] = "USD"  # every catalog in sales_lots is a North American (USD) sale
    s["status"] = np.select(
        [s.out, s.rna & (s.price > 0), s.rna, s.price > 0], ["out", "rna", "rna_no_bid", "sold"], "no_price"
    )
    hipn = pd.to_numeric(s.hip.astype(str).str.extract(r"(\d+)")[0], errors="coerce")
    s["hip_pct"] = hipn.groupby(s.source_file).rank(pct=True)

    reasons = []
    dup = s.duplicated("transaction_id", keep="first")
    reasons.append((dup, "duplicate transaction (same catalog file, hip and horse)"))
    reasons.append((s.sale_year.isna() | s.birth_year_n.isna(), "missing sale or foaling year"))
    reasons.append(((s.age < 0) | (s.age > 30), "impossible age at sale"))
    reasons.append((s.out, "withdrawn (out)"))
    reasons.append((s.status.isin(["no_price", "rna_no_bid"]), "no price recorded"))
    reasons.append(((s.price > MAX_VALID_PRICE), "invalid price (above $20M)"))
    reasons.append((~s.category.isin(["weanling", "yearling"]), "category not in the yearling/weanling model"))
    reasons.append(((s.sire_k == "") | (s.dam_k == ""), "missing sire or dam"))
    s["model_exclusion"] = ""
    for mask, why in reasons:
        s.loc[mask & (s.model_exclusion == ""), "model_exclusion"] = why
    # quality flags (kept in the data, not exclusions)
    t = s.catalog_title.str.lower()
    s["flag_title_age_mismatch"] = (t.str.contains("yearling") & (s.age != 1)) | (t.str.contains("weanling") & (s.age != 0))
    s["flag_repeat_horse_same_sale"] = s.duplicated(["source_file", "horse_id"], keep=False) & ~dup
    exclusions = s.loc[s.model_exclusion != "", ["transaction_id", "source_file", "hip", "sale_year", "category",
                                                 "status", "model_exclusion"]]
    keep = ["transaction_id", "horse_id", "source_file", "catalog_title", "sale_key", "hip", "hip_pct", "sale_year",
            "sale_month", "venue", "category", "age", "birth_year_n", "sex_letter", "sire", "dam", "damsire",
            "sire_k", "dam_k", "damsire_k", "consignor", "price", "currency", "status", "model_exclusion",
            "flag_title_age_mismatch", "flag_repeat_horse_same_sale"]
    return s[keep].reset_index(drop=True), exclusions.reset_index(drop=True)


# ------------------------------------------------------------------ helpers
def _num(series) -> pd.Series:
    return pd.to_numeric(series.astype(str).str.replace(r"[^\d.\-]", "", regex=True), errors="coerce")


def _asof(rows: pd.DataFrame, table: pd.DataFrame, key: str, rows_key: str, rows_year: str,
          cols: List[str], strict: bool, prefix: str) -> pd.DataFrame:
    """For each row, the last `table` record for its key with year < (strict) or <= the row's year."""
    left = rows[[rows_key, rows_year]].reset_index().rename(columns={rows_key: "_k", rows_year: "_y"})
    left["_y"] = left["_y"].astype("float64")
    right = table[[key] + cols].rename(columns={key: "_k"}).copy()
    right["_y"] = table["year"].astype("float64").values
    left["_k"] = left["_k"].astype(object).astype(str)
    right["_k"] = right["_k"].astype(object).astype(str)
    left = left.dropna(subset=["_y"]).sort_values("_y")
    right = right.dropna(subset=["_y"]).sort_values("_y")
    merged = pd.merge_asof(left, right, on="_y", by="_k", allow_exact_matches=not strict)
    out = merged.set_index("index")[cols].reindex(rows.index)
    return out.add_prefix(prefix)


def _fee_table(sources: Sources) -> pd.DataFrame:
    """(k, year, fee) in USD, one row per stallion and season; seasons before his first run dropped."""
    from crossr_core.fees import parse_fee, usd_estimate

    f = sources.table("stud_fees")
    parsed = [usd_estimate(*parse_fee(v)) for v in f["Stud Fee"]]
    t = pd.DataFrame({"k": f.Stallion.map(sm.name_key), "year": pd.to_numeric(f.Year, errors="coerce"),
                      "fee": pd.to_numeric(pd.Series(parsed, index=f.index), errors="coerce")})
    t = t[(t.k != "") & (t.fee > 0)].dropna(subset=["year"])
    t = t.groupby(["k", "year"], as_index=False).fee.max().sort_values(["k", "year"]).reset_index(drop=True)
    # A same-named older stallion shows up as fees before a gap of FEE_RUN_GAP+ years: each run of
    # listed seasons is its own horse. Built from earlier rows only, so it is as of every sale.
    gap = t.groupby("k").year.diff()
    t["run"] = (gap.isna() | (gap >= FEE_RUN_GAP)).astype(int).groupby(t.k).cumsum()
    return t


def _fee_features(rows: pd.DataFrame, fees: pd.DataFrame, who: str, key_col: str, prefix: str) -> pd.DataFrame:
    """Fees of `who` as known at each sale: at conception, initial, highest and latest to date."""
    t = fees.copy()
    t["initial"] = t.groupby(["k", "run"]).fee.transform("first")
    t["initial_year"] = t.groupby(["k", "run"]).year.transform("first")
    t["highest"] = t.groupby(["k", "run"]).fee.cummax()
    t["latest"] = t.fee
    t["latest_year"] = t.year
    out = _asof(rows, t, "k", key_col, "sale_year", ["initial", "initial_year", "highest", "latest", "latest_year"],
                strict=False, prefix=prefix)
    rows2 = rows.assign(_cy=rows.birth_year_n - 1)
    conc = _asof(rows2, t.assign(conc=t.fee, conc_year=t.year), "k", key_col, "_cy", ["conc", "conc_year"],
                 strict=False, prefix=prefix)
    stale = (rows2._cy - conc[prefix + "conc_year"]) > FEE_LOOKBACK
    conc.loc[stale, [prefix + "conc", prefix + "conc_year"]] = np.nan
    out[prefix + "fee_conception"] = conc[prefix + "conc"]
    out[prefix + "fee_conception_historical"] = (conc[prefix + "conc_year"] < rows2._cy).astype(float)
    out = out.rename(columns={prefix + "initial": prefix + "fee_initial", prefix + "highest": prefix + "fee_highest",
                              prefix + "latest": prefix + "fee_latest"})
    out[prefix + "fee_years_listed"] = rows.sale_year - out[prefix + "initial_year"]
    return out.drop(columns=[prefix + "initial_year", prefix + "latest_year"])


# ------------------------------------------------------------------ feature groups
FEATURE_GROUPS: Dict[str, List[str]] = {}


def _register(group: str, cols: Iterable[str]) -> None:
    FEATURE_GROUPS.setdefault(group, [])
    for c in cols:
        if c not in FEATURE_GROUPS[group]:
            FEATURE_GROUPS[group].append(c)


def build_features(tx: pd.DataFrame, sources: Sources, rows: Optional[pd.Series] = None) -> pd.DataFrame:
    """Features for model-eligible transactions (or `rows`, a boolean mask over tx)."""
    rows_mask = (tx.model_exclusion == "") if rows is None else rows
    yw = tx[tx.category.isin(["weanling", "yearling"]) & tx.sale_year.notna() & tx.birth_year_n.notna()].copy()
    feats = _history_features(yw, sources)
    fees = _fee_table(sources)
    target = yw.index[rows_mask.reindex(yw.index, fill_value=False)]
    r = yw.loc[target].copy()
    r["hip_pct_raw"] = r.pop("hip_pct")  # features carry the model's hip_pct (0.5 when unknown)
    r = r.join(feats, how="left")
    r["damsire_k_used"] = r.pop("damsire_used")
    a = _fee_features(r, fees, "sire", "sire_k", "sire_")
    d = _fee_features(r, fees, "bms", "damsire_k_used", "bms_")
    r = r.join(a).join(d)
    r = r.join(_sire_racing(r, sources))
    r = r.join(_dam_production(r, sources))
    r = r.join(_dam_race_record(r, sources))
    r = r.join(_progression(r, yw))
    r = r.join(_pedigree_completeness(r, sources))
    _derived(r)
    return r


def _history_features(yw: pd.DataFrame, sources: Sources) -> pd.DataFrame:
    """sales_model.History replay: sale level, sire / dam / damsire / cross earlier-sale records, hip curve."""
    rh = sources.table("race_horses")
    rh_k = rh.name_key.map(sm.name_key)
    females = rh.sex.astype(str).str.upper().str[:1].isin(["F", "M"])
    dam_sire = dict(zip(rh_k[females], rh.sire[females].map(sm.name_key)))
    gs = sources.table("graded_stakes")
    g = pd.DataFrame({"k": gs["Horse Name"].map(sm.name_key), "fy": pd.to_numeric(gs["Foaling Year"], errors="coerce"),
                      "yr": pd.to_numeric(gs["Race Year"], errors="coerce"), "place": pd.to_numeric(gs.Place, errors="coerce")})
    g = g.dropna(subset=["fy", "yr"])
    # Dated events: a graded placing counts from its year even if the horse wins one later.
    won = g[g.place == 1].groupby(["k", "fy"]).yr.min().reset_index().assign(level=2)
    placed = g[g.place.isin([2, 3])].groupby(["k", "fy"]).yr.min().reset_index().assign(level=1)
    horses = pd.concat([won, placed]).rename(columns={"yr": "year"})
    horses["year"] = horses.year.astype(int)
    ped = pd.DataFrame({"k": rh_k, "fy": pd.to_numeric(rh.foal_year, errors="coerce"),
                        "sire_k": rh.sire.map(sm.name_key), "dam_k": rh.dam.map(sm.name_key)}).dropna(subset=["fy"])
    horses = horses.merge(ped.drop_duplicates(["k", "fy"]), on=["k", "fy"], how="inner")
    events = list(horses[["year", "sire_k", "dam_k", "level"]].sort_values("year").itertuples(index=False))

    fees = _fee_table(sources)
    hist = sm.History()
    hist.fees = {k: {int(y): float(f) for y, f in zip(g2.year, g2.fee)} for k, g2 in fees.groupby("k")}
    hist.fees = __import__("collections").defaultdict(dict, hist.fees)
    hist.own = {}  # the dam's own record is an as-of feature in _dam_race_record
    hist.dam_sire = dam_sire
    # Fixed fill for a missing fee (its own "fee_missing" flag carries the signal). A median of the
    # fee table would include fees announced after the sale.
    hist.lfee_fill = math.log(MISSING_FEE_FILL)

    yw = yw.copy()
    yw["kind"] = np.where(yw.age == 1, "Y", "W")
    sold = (yw.status == "sold") & (yw.price > 0) & (yw.price <= MAX_VALID_PRICE)
    yw["lp"] = np.where(sold, np.log(yw.price.where(yw.price > 0)), np.nan)
    sale_mean = yw.lp.groupby([yw.source_file, yw.kind]).transform("mean")
    yw["rel"] = yw.lp - sale_mean
    names = sm.PRICE_FEATURES
    out = np.full((len(yw), len(names)), np.nan)
    ds_used = [""] * len(yw)
    pos = {ix: i for i, ix in enumerate(yw.index)}
    ei = 0
    for year, block in yw.groupby("sale_year", sort=True):
        while ei < len(events) and events[ei].year < year:
            ev = events[ei]
            hist.add_graded([(ev.sire_k, ev.dam_k, ev.level)])
            ei += 1
        sold_rows = []
        for ix, r in zip(block.index, block.itertuples(index=False)):
            raw = hist.raw_stats({"year": int(year), "kind": r.kind, "sire_k": r.sire_k, "dam_k": r.dam_k,
                                  "damsire_k": r.damsire_k, "sale_key": r.sale_key, "birth_year": int(r.birth_year_n),
                                  "sex": r.sex_letter, "hip_pct": None if pd.isna(r.hip_pct) else float(r.hip_pct)})
            f = hist.features(raw)
            out[pos[ix]] = [f[n] for n in names]
            ds_used[pos[ix]] = raw["damsire_k"]
            if not np.isnan(r.lp):
                sold_rows.append({"sire_k": r.sire_k, "dam_k": r.dam_k, "damsire_k": raw["damsire_k"],
                                  "sale_key": r.sale_key, "kind": r.kind, "lp": r.lp, "rel": r.rel,
                                  "sire_rel": f["sire_rel"], "damsire_rel": f["damsire_rel"],
                                  "hip_pct": None if pd.isna(r.hip_pct) else float(r.hip_pct)})
        hist.add_sale(sold_rows, int(year))
    df = pd.DataFrame(out, index=yw.index, columns=names).drop(columns=["dam_own_gsw", "dam_own_gsp"])
    df["damsire_used"] = ds_used
    df["log_fee_conception_hist"] = df.pop("log_fee")
    _register("A_sire", ["log_fee_conception_hist", "fee_missing", "fee_momentum", "sire_rel", "sire_log_n", "sire_log_gsw"])
    _register("B_dam_production", ["dam_rel", "dam_log_n", "dam_log_gsw", "dam_log_gsp"])
    _register("D_broodmare_sire", ["damsire_rel", "damsire_missing"])
    _register("E_pedigree", ["nick"])
    _register("F_progression", ["sale_level", "sale_new", "market_trend"])
    _register("G_horse_sale", ["weanling", "filly", "hip_signal", "hip_pct"])
    return df


def _sire_racing(r: pd.DataFrame, sources: Sources) -> pd.DataFrame:
    """Sire's progeny racing to the year before the sale (one leading-sire row per season)."""
    rk = sources.table("stallion_rankings")
    prefer = {"General": 0, "First-Crop": 1, "Second-Crop": 1, "Third-Crop": 1}
    t = pd.DataFrame({"k": rk.stallion.map(sm.name_key), "year": pd.to_numeric(rk.year, errors="coerce"),
                      "lst": rk.list.map(prefer), "runners": _num(rk.runners), "winners": _num(rk.winners),
                      "sw": _num(rk.stakes_winners), "gsw": _num(rk.graded_stakes_winners), "earn": _num(rk.year_earnings)})
    t = t.dropna(subset=["lst", "year"]).sort_values(["k", "year", "lst"]).drop_duplicates(["k", "year"])
    t = t[t.runners > 0]
    for c in ["runners", "winners", "sw", "gsw", "earn"]:
        t["cum_" + c] = t.groupby("k")[c].cumsum()
    t["seasons"] = t.groupby("k").cumcount() + 1
    cols = ["cum_runners", "cum_winners", "cum_sw", "cum_gsw", "cum_earn", "seasons"]
    a = _asof(r, t, "k", "sire_k", "sale_year", cols, strict=True, prefix="s_")
    out = pd.DataFrame(index=r.index)
    out["sire_prog_seasons"] = a.s_seasons.fillna(0)
    out["sire_prog_earn_per_runner"] = np.log1p(a.s_cum_earn / a.s_cum_runners)
    out["sire_prog_win_rate"] = a.s_cum_winners / a.s_cum_runners
    out["sire_prog_sw_rate"] = a.s_cum_sw / a.s_cum_runners
    out["sire_prog_gsw_rate"] = a.s_cum_gsw / a.s_cum_runners
    _register("A_sire", list(out.columns))
    return out


def _horse_index(sources: Sources) -> pd.DataFrame:
    rh = sources.table("race_horses")
    return pd.DataFrame({"id": rh["id"].astype(str), "k": rh.name_key.map(sm.name_key),
                         "fy": pd.to_numeric(rh.foal_year, errors="coerce"), "sex": rh.sex.astype(str).str.upper().str[:1],
                         "dam_k": rh.dam.map(sm.name_key), "sire_k": rh.sire.map(sm.name_key),
                         "best_class": rh.best_class.astype(str)})


def _season_table(sources: Sources) -> pd.DataFrame:
    rs = sources.table("race_seasons")
    t = pd.DataFrame({"id": rs.horse_id.astype(str), "year": pd.to_numeric(rs.year, errors="coerce"),
                      "starts": _num(rs.starts), "firsts": _num(rs.firsts), "earn": _num(rs.earnings)})
    return t.dropna(subset=["year"])  # the "Career" rows have no year and are not used (not dated)


def _stakes_wins(sources: Sources) -> pd.DataFrame:
    """(id, year, gsw, g1, sw): dated wins of graded / Grade 1 / any stakes from race_starts."""
    st = sources.table("race_starts")
    grade = st.grade.astype(str).str.upper()
    name = st.race_name.astype(str)
    stakes = grade.str.match(r"G[123]|L") | name.str.contains(r"\b(?:S\.|Stakes|H\.|Handicap|Derby|Oaks|Cup)\b", regex=True)
    t = pd.DataFrame({"id": st.horse_id.astype(str), "year": pd.to_numeric(st.race_date.astype(str).str[:4], errors="coerce"),
                      "win": pd.to_numeric(st.place, errors="coerce") == 1, "graded": grade.str.match(r"G[123]"),
                      "g1": grade.str.startswith("G1"), "stakes": stakes, "esf": pd.to_numeric(st.esf, errors="coerce")})
    return t.dropna(subset=["year"])


def _dam_production(r: pd.DataFrame, sources: Sources) -> pd.DataFrame:
    """The dam's other foals as known before the sale year (results dated earlier only)."""
    h = _horse_index(sources)
    seasons = _season_table(sources)
    wins = _stakes_wins(sources)
    prog = h[h.dam_k != ""].dropna(subset=["fy"])
    # per progeny per year: started, won, earnings, stakes / graded / G1 win
    yr = seasons.groupby(["id", "year"]).agg(starts=("starts", "sum"), firsts=("firsts", "sum"), earn=("earn", "sum"))
    sw = wins[wins.win].groupby(["id", "year"]).agg(sw=("stakes", "max"), gsw=("graded", "max"), g1=("g1", "max"))
    py = yr.join(sw, how="outer").fillna(0).reset_index()
    py = py.merge(prog[["id", "dam_k", "fy"]], on="id", how="inner")
    # first year each progeny raced / won / won a stakes...; then per dam cumulative counts by year
    firsts = py.assign(ran=py.starts > 0, won=py.firsts > 0)
    events = []
    for col in ["ran", "won", "sw", "gsw", "g1"]:
        e = firsts[firsts[col] > 0].groupby("id").agg(year=("year", "min"), dam_k=("dam_k", "first"))
        events.append(e.assign(what=col))
    ev = pd.concat(events).reset_index(drop=True)
    earn = py.groupby(["dam_k", "year"]).earn.sum().reset_index()
    out = pd.DataFrame(index=r.index)
    rows = r[["dam_k", "sale_year", "birth_year_n"]]
    for col in ["ran", "won", "sw", "gsw", "g1"]:
        c = ev[ev.what == col].groupby(["dam_k", "year"]).size().groupby(level=0).cumsum().rename("n").reset_index()
        a = _asof(rows, c.rename(columns={"dam_k": "k"}), "k", "dam_k", "sale_year", ["n"], strict=True, prefix=col + "_")
        out["dam_prog_" + col] = a[col + "_n"].fillna(0)
    ce = earn.assign(cum=earn.groupby("dam_k").earn.cumsum()).rename(columns={"dam_k": "k"})
    out["dam_prog_earnings"] = np.log1p(_asof(rows, ce, "k", "dam_k", "sale_year", ["cum"], strict=True, prefix="e_").e_cum.fillna(0))
    # exposure: her foals known before this one, and those old enough to have raced (aged 3+)
    foals = prog.groupby(["dam_k", "fy"]).size().groupby(level=0).cumsum().rename("n").reset_index()
    foals = foals.rename(columns={"dam_k": "k", "fy": "year"})
    older = _asof(rows, foals, "k", "dam_k", "birth_year_n", ["n"], strict=True, prefix="f_")
    rows3 = rows.assign(_y3=rows.sale_year - 3)
    aged3 = _asof(rows3, foals, "k", "dam_k", "_y3", ["n"], strict=False, prefix="a_")
    out["dam_foals_before"] = older.f_n.fillna(0)
    out["dam_foals_aged3"] = aged3.a_n.fillna(0)
    denom = out.dam_foals_aged3.where(out.dam_foals_aged3 > 0)
    out["dam_prog_pct_raced"] = out.dam_prog_ran / denom
    out["dam_prog_pct_won"] = out.dam_prog_won / denom
    out["dam_prog_sw_per_foal"] = out.dam_prog_sw / denom
    # the dam's age when this foal was born (exposure); namesakes: an age of 3-27 only
    dams = h[h.sex.isin(["F", "M"])][["k", "fy"]].dropna()
    age = r[["dam_k", "birth_year_n"]].reset_index().merge(dams, left_on="dam_k", right_on="k", how="left")
    age["a"] = age.birth_year_n - age.fy
    age = age[(age.a >= 3) & (age.a <= 27)].groupby("index").a.min()
    out["dam_age_at_foaling"] = age.reindex(r.index)
    _register("B_dam_production", list(out.columns))
    return out


def _dam_race_record(r: pd.DataFrame, sources: Sources) -> pd.DataFrame:
    """The dam's own racing, from seasons and starts dated before the sale year."""
    h = _horse_index(sources)
    seasons = _season_table(sources)
    wins = _stakes_wins(sources)
    dams = h[h.sex.isin(["F", "M"])].dropna(subset=["fy"])
    m = r[["dam_k", "birth_year_n", "sale_year"]].reset_index().merge(dams[["id", "k", "fy"]], left_on="dam_k",
                                                                     right_on="k", how="left")
    m["a"] = m.birth_year_n - m.fy
    m = m[(m.a >= 3) & (m.a <= 27)].sort_values(["a", "id"], kind="stable").drop_duplicates("index")  # one dam per lot
    s = seasons.merge(m[["index", "id", "sale_year"]], on="id")
    s = s[s.year < s.sale_year]
    agg = s.groupby("index").agg(dam_starts=("starts", "sum"), dam_wins=("firsts", "sum"), dam_earn=("earn", "sum"))
    w = wins.merge(m[["index", "id", "sale_year"]], on="id")
    w = w[w.year < w.sale_year]
    wa = w.groupby("index").agg(dam_sw=("stakes", lambda x: float((x & w.loc[x.index, "win"]).sum())),
                                dam_gsw=("graded", lambda x: float((x & w.loc[x.index, "win"]).sum())),
                                dam_g1w=("g1", lambda x: float((x & w.loc[x.index, "win"]).sum())),
                                dam_best_esf=("esf", "max"))
    out = pd.DataFrame(index=r.index)
    out["dam_record_found"] = r.index.isin(m["index"]).astype(float)
    out = out.join(agg).join(wa)
    for c in ["dam_starts", "dam_wins", "dam_sw", "dam_gsw", "dam_g1w"]:
        out[c] = out[c].where(out.dam_record_found == 1).fillna(0).where(out.dam_record_found == 1)
    out["dam_raced"] = (out.dam_starts > 0).astype(float).where(out.dam_record_found == 1)
    out["dam_earn"] = np.log1p(out.dam_earn)
    _register("C_dam_race_record", list(out.columns))
    return out


def _progression(r: pd.DataFrame, yw: pd.DataFrame) -> pd.DataFrame:
    """Sire / broodmare-sire / sale sales-price trends from earlier years (log price relative to each sale)."""
    sold = yw[(yw.status == "sold") & (yw.price > 0) & (yw.price <= MAX_VALID_PRICE)].copy()
    sold["lp"] = np.log(sold.price)
    sold["rel"] = sold.lp - sold.groupby("source_file").lp.transform("mean")
    out = pd.DataFrame(index=r.index)
    for who, col in (("sire", "sire_k"), ("bms", "damsire_k")):
        y = sold[sold[col] != ""].groupby([col, "sale_year"]).agg(m=("rel", "mean"), med=("lp", "median"), n=("rel", "size"))
        y = y.reset_index().rename(columns={col: "k", "sale_year": "year"})
        last = _asof(r, y, "k", col if who == "sire" else "damsire_k_used", "sale_year", ["m", "med", "n", "year"],
                     strict=True, prefix="l_")
        r2 = r.assign(_y2=r.sale_year - 1)
        prev = _asof(r2, y, "k", col if who == "sire" else "damsire_k_used", "_y2", ["m", "year"], strict=True, prefix="p_")
        fresh = (r.sale_year - last.l_year) <= 2
        out[f"{who}_last_year_rel"] = last.l_m.where(fresh)
        out[f"{who}_last_year_median_lp"] = last.l_med.where(fresh)
        out[f"{who}_trend"] = (last.l_m - prev.p_m).where(fresh & ((last.l_year - prev.p_year) <= 2))
    m = sold.groupby("sale_year").lp.median()
    out["market_median_lp_last_year"] = r.sale_year.map(lambda y: m.get(y - 1, np.nan))
    _register("F_progression", list(out.columns))
    return out


def _pedigree_completeness(r: pd.DataFrame, sources: Sources) -> pd.DataFrame:
    """Share of the dam's 4-generation ancestor slots known in the pedigree store (0 when no tree)."""
    import json

    p = sources.table("pedigrees")
    keys = set(r.dam_k)
    known = {}
    for k, tree in zip(p.key.map(sm.name_key), p.tree):
        if k in keys:
            try:
                t = json.loads(tree) if isinstance(tree, (str, bytes)) else tree
            except ValueError:
                continue
            known[k] = _slots(t, 0, 4) / 30.0
    out = pd.DataFrame(index=r.index)
    out["dam_pedigree_completeness"] = r.dam_k.map(known).fillna(0.0)
    _register("E_pedigree", list(out.columns))
    return out


def _slots(node, depth, max_depth) -> int:
    if depth >= max_depth or not isinstance(node, dict):
        return 0
    n = 0
    for side in ("Sire", "Dam"):
        child = node.get(side)
        if isinstance(child, dict) and child.get("Horse"):
            n += 1 + _slots(child, depth + 1, max_depth)
    return n


def _derived(r: pd.DataFrame) -> None:
    for p in ("sire_", "bms_"):
        for c in ("fee_conception", "fee_initial", "fee_highest", "fee_latest"):
            r[f"log_{p}{c}"] = np.log(r[p + c])
        r[f"{p}fee_latest_vs_initial"] = r[f"log_{p}fee_latest"] - r[f"log_{p}fee_initial"]
    r["sale_month_f"] = r.sale_month.astype(float)
    for v in ("keeneland", "fasig_tipton", "obs"):
        r[f"venue_{v}"] = (r.venue == v).astype(float)
    # interactions named in Phase 12.4
    r["x_sirefee_damrel"] = r.log_sire_fee_conception * r.dam_rel
    r["x_sirefee_bmsfee"] = r.log_sire_fee_conception * r.log_bms_fee_highest
    r["x_filly_yearling"] = r.filly * (1 - r.weanling)
    _register("A_sire", ["log_sire_fee_conception", "log_sire_fee_initial", "log_sire_fee_highest", "log_sire_fee_latest",
                         "sire_fee_latest_vs_initial", "sire_fee_years_listed", "sire_fee_conception_historical"])
    _register("D_broodmare_sire", ["log_bms_fee_conception", "log_bms_fee_initial", "log_bms_fee_highest",
                                   "log_bms_fee_latest", "bms_fee_latest_vs_initial", "bms_fee_years_listed"])
    _register("G_horse_sale", ["sale_month_f", "venue_keeneland", "venue_fasig_tipton", "venue_obs", "x_filly_yearling"])
    _register("X_interactions", ["x_sirefee_damrel", "x_sirefee_bmsfee"])


def feature_columns(groups: Optional[Iterable[str]] = None) -> List[str]:
    gs = list(FEATURE_GROUPS) if groups is None else list(groups)
    cols: List[str] = []
    for g in gs:
        for c in FEATURE_GROUPS.get(g, []):
            if c not in cols:
                cols.append(c)
    return cols


def truncation_check(sources: Sources, year: int, sample: int = 300, seed: int = 1) -> dict:
    """Leakage test: features of `year` sales rebuilt from data cut off at `year` must equal the full build."""
    tx, _ = build_transactions(sources)
    full = build_features(tx, sources, rows=(tx.sale_year == year) & tx.category.isin(["weanling", "yearling"])
                          & (tx.model_exclusion.isin(["", "no price recorded"])))
    cut = sources.truncated(year)
    tx2, _ = build_transactions(cut)
    ids = full.transaction_id.sample(min(sample, len(full)), random_state=seed)
    feats2 = build_features(tx2, cut, rows=tx2.transaction_id.isin(ids) & ~tx2.transaction_id.duplicated())
    cols = feature_columns()
    a = full.set_index("transaction_id").loc[ids, cols].astype(float)
    b = feats2.set_index("transaction_id").reindex(ids)[cols].astype(float)
    diff = (a - b).abs()
    both_nan = a.isna() & b.isna()
    bad = ((diff > 1e-9) | (a.isna() != b.isna())) & ~both_nan
    return {"year": year, "rows": int(len(ids)), "features": len(cols),
            "mismatched_features": sorted(bad.columns[bad.any()].tolist()),
            "mismatched_cells": int(bad.values.sum())}

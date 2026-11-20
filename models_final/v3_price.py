"""v3 sale-price model: the step-up (v2) plus six catalog inputs, fitted with LightGBM.

    ../.venv/bin/python v3_price.py

Design decision (2026-11-18): add all six inputs and use LightGBM.
New inputs, all printed in the catalog or known before the sale:
1. hip position  - where the lot sits in its catalog (0 = first hip, 1 = last). Keeneland sorts
                   books by quality; Fasig-Tipton mostly doesn't, so the model also sees the sale.
2. consignor     - each consignor's premium from earlier years (shrunk), and the consignor itself
                   as a category for the big ones. Recorded for Fasig-Tipton sales only.
3. earlier sale  - the same horse sold (or RNA'd) at a younger age: how it sold vs that sale.
                   Same horse = same dam (+ damsire) and foaling year.
4. black type    - the dam's earlier foals with a graded-stakes win or place, and the sire's
                   graded winners, before the sale year. From the IFHA results file only
                   (graded_stakes.csv: graded races only, places 1-3), linked to sire and dam
                   through catalog pedigrees. Replaces v2's Equibase-based sire count.
5. sire's crop   - how many crops the sire has had (first-crop sires draw extra money).
6. state bred and price-tier ranges - foaling state as a category, and the 80% range calibrated
                   separately for cheap, middle and expensive sales.
Unsold horses stay censored (RNA price = upper bound), and the market-growth forecast stays.
Train 2005-2018, tune 2019-2020, test 2021-2025, as for v2.
"""
from __future__ import annotations

import itertools
import json
import warnings

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy import stats

import features as F
import stepup_price as S
from features import c

warnings.filterwarnings("ignore")
TRAIN, VALID, TEST = S.TRAIN, S.VALID, S.TEST
CATS = ["consignor_cat", "state_cat", "sale_cat"]


# ------------------------------------------------------------------ helpers
def cum_before(events: pd.DataFrame, key: str, lots: pd.DataFrame, name: str) -> pd.Series:
    """For each lot: how many events for its key happened in a year before its sale year."""
    per = events.groupby([key, "year"]).size().rename("n").reset_index().sort_values("year")
    per["cum"] = per.groupby(key)["n"].cumsum()
    per["sale_year"] = (per["year"] + 1).astype(int)  # counts through year Y-1 apply from year Y
    q = lots[[key, "sale_year"]].reset_index().dropna(subset=[key]).astype({"sale_year": int}).sort_values("sale_year")
    m = pd.merge_asof(q, per[[key, "sale_year", "cum"]].sort_values("sale_year"), on="sale_year", by=key,
                      direction="backward")
    return m.set_index("index")["cum"].reindex(lots.index).fillna(0).rename(name)


def pedigrees() -> pd.DataFrame:
    """Horse name + foaling year -> sire and dam keys, from catalog pedigrees only (sale catalogs
    and the catalog-built race_horses rows); no Equibase rows."""
    s = pd.read_csv(c.DATA / "sales_lots.csv", low_memory=False, usecols=["name", "birth_year", "sire", "dam", "damsire"])
    s = s[~s["name"].astype(str).str.contains(" x ", case=False)].rename(columns={"birth_year": "fy"})
    rh = pd.read_csv(c.DATA / "race_horses.csv", low_memory=False,
                     usecols=["display_name", "foal_year", "sire", "dam", "damsire", "equibase_refno", "profile_url"])
    rh = rh[rh["profile_url"].isna() & rh["equibase_refno"].isna()]
    rh = rh.rename(columns={"display_name": "name", "foal_year": "fy"})
    p = pd.concat([s, rh[["name", "fy", "sire", "dam", "damsire"]]])
    p["k"] = p["name"].map(c.norm)
    p["fy"] = pd.to_numeric(p["fy"], errors="coerce")
    p["sire_n"] = p["sire"].map(c.norm)
    p["dam_key"] = p["dam"].map(c.norm) + "|" + p["damsire"].map(c.norm)
    p = p[(p["k"] != "") & (p["dam"].notna())]
    return p.drop_duplicates(["k", "fy"])[["k", "fy", "sire_n", "dam_key"]]


def graded_events() -> pd.DataFrame:
    """One row per graded-stakes horse (IFHA): the year it first reached each class.
    bt_year  - first graded win or place (black type)     win_year - first graded win (GSW)
    g12_year - first G1 or G2 win                         g1_year  - first G1 win (G1W)
    mg1_year - second G1 win (multiple G1 winner, MG1W)"""
    g = pd.read_csv(c.DATA / "graded_stakes.csv", low_memory=False,
                    usecols=["Horse Name", "Foaling Year", "Year Foaled", "Place", "Grade", "Date (ISO)"])
    g["k"] = g["Horse Name"].map(c.norm)
    g["fy"] = pd.to_numeric(g["Foaling Year"].fillna(g["Year Foaled"]), errors="coerce")
    g["year"] = pd.to_datetime(g["Date (ISO)"], errors="coerce").dt.year
    g = g.dropna(subset=["fy", "year"])
    won = g[g["Place"] == 1].groupby(["k", "fy"])["year"].min().rename("win_year")
    g1 = g[(g["Place"] == 1) & (g["Grade"] == "G1")].groupby(["k", "fy"])["year"].min().rename("g1_year")
    g12 = g[(g["Place"] == 1) & g["Grade"].isin(["G1", "G2"])].groupby(["k", "fy"])["year"].min().rename("g12_year")
    g1_wins = g[(g["Place"] == 1) & (g["Grade"] == "G1")].sort_values("year")
    mg1 = g1_wins.groupby(["k", "fy"])["year"].nth(1)  # the second G1 win
    mg1 = pd.Series(mg1.to_numpy(), index=pd.MultiIndex.from_frame(g1_wins.loc[mg1.index, ["k", "fy"]]), name="mg1_year")
    placed = g.groupby(["k", "fy"])["year"].min().rename("bt_year")
    ev = pd.concat([won, g12, g1, mg1, placed], axis=1).reset_index()
    return ev.merge(pedigrees(), on=["k", "fy"], how="inner")


STATES = {"KY": "KENTUCKY", "NY": "NEW YORK", "FL": "FLORIDA", "MD": "MARYLAND", "PA": "PENNSYLVANIA",
          "CA": "CALIFORNIA", "LA": "LOUISIANA", "VA": "VIRGINIA", "ON": "ONTARIO", "IRE": "IRELAND", "TX": "TEXAS",
          "OK": "OKLAHOMA", "WV": "WEST VIRGINIA", "NJ": "NEW JERSEY", "OH": "OHIO", "IL": "ILLINOIS", "AR": "ARKANSAS"}


# ------------------------------------------------------------------ the six inputs
def add_v3_inputs(s: pd.DataFrame) -> pd.DataFrame:
    s = s.copy()
    # 1. hip position within its catalog
    # hip is stored as text: rank it as a number (as text, "1000" sorts before "2")
    s["hip_pct"] = pd.to_numeric(s["hip"], errors="coerce").groupby(s["source_file"]).rank(pct=True)
    big_sales = s[s["sale_year"] <= TRAIN[1]]["sale"].value_counts()
    s["sale_cat"] = s["sale"].where(s["sale"].isin(big_sales[big_sales >= 1000].index), "OTHER")

    # 2. consignor: shrunk premium from earlier years, plus the big ones as a category
    # "EATON SALES, AGENT II" and "EATON SALES AGENT" are the same consignor
    s["consignor_n"] = (s["consignor"].astype(str).str.upper().str.strip()
                        .str.replace(r"[,.]?\s*\(?AGENT[\s\w]*\)?$|\s*\(.*\)$", "", regex=True).str.strip(" ,.")
                        .where(s["consignor"].notna()).replace("", None))
    per = s.dropna(subset=["consignor_n", "rel"]).groupby(["consignor_n", "sale_year"])["rel"].agg(["sum", "count"]).reset_index()
    per = per.sort_values("sale_year")
    g = per.groupby("consignor_n")
    per["cons_cnt"] = g["count"].cumsum() - per["count"]
    per["cons_eff"] = (g["sum"].cumsum() - per["sum"]) / (per["cons_cnt"] + 20)
    s = s.merge(per[["consignor_n", "sale_year", "cons_cnt", "cons_eff"]], on=["consignor_n", "sale_year"], how="left")
    s["cons_eff"] = s["cons_eff"].fillna(0.0)
    s["log_cons_n"] = np.log1p(s["cons_cnt"].fillna(0))
    s["consignor_known"] = s["consignor_n"].notna().astype(int)
    big_cons = s[s["sale_year"] <= TRAIN[1]]["consignor_n"].value_counts()
    s["consignor_cat"] = s["consignor_n"].where(s["consignor_n"].isin(big_cons[big_cons >= 150].index), "OTHER")
    s.loc[s["consignor_n"].isna(), "consignor_cat"] = "UNKNOWN"

    # 3. the same horse at a younger age (same dam + damsire and foaling year)
    s["horse"] = np.where(s["damsire_n"] != "", s["dam_n"] + "|" + s["damsire_n"], s["dam_n"]) + "|" + \
        s["birth_year"].astype(int).astype(str)
    s["rel_any"] = s["log_price"] - (s["sale_level"] + s["growth_fc"].fillna(0))  # vs expected sale level, sold or RNA
    prev = s[["horse", "age", "rel_any", "log_price", "rna"]].sort_values("age")
    rows = []
    for a in (1, 2):
        earlier = prev[prev["age"] < a].groupby("horse").agg(prev_rel=("rel_any", "last"), prev_log_price=("log_price", "last"),
                                                          prev_rna=("rna", "last"))
        rows.append(s.loc[s["age"] == a, ["horse"]].join(earlier, on="horse").drop(columns="horse"))
    s = s.join(pd.concat(rows), how="left")
    s["has_prev"] = s["prev_log_price"].notna().astype(int)

    # 4. black type (IFHA graded races only), linked through catalog pedigrees
    ev = graded_events()
    s["dam_key"] = s["dam_n"] + "|" + s["damsire_n"]
    # Catalog pedigrees of named (racing) horses carry no damsire, so a graded horse's dam is known
    # by name only ("NAME|"). Link her to a sale-records mare (name + damsire) when exactly one mare
    # with that name has a known damsire and the horse was foaled within her producing years (+-4).
    full = s[s["damsire_n"] != ""]
    span = full.groupby("dam_key")["birth_year"].agg(["min", "max"])
    keys = full.groupby("dam_n")["dam_key"].unique()
    unique_key = keys[keys.map(len) == 1].map(lambda k: k[0])
    name = ev["dam_key"].str[:-1]
    cand = name.map(unique_key)
    lo, hi = cand.map(span["min"]), cand.map(span["max"])
    ok = ev["dam_key"].str.endswith("|") & cand.notna() & ev["fy"].between(lo - 4, hi + 4)
    ev.loc[ok, "dam_key"] = cand[ok]
    # her foals by class, counted before the sale year: black type < GSW < G1/G2 winner < G1W < MG1W
    for col, src, key in (("dam_bt", "bt_year", "dam_key"), ("dam_gsw", "win_year", "dam_key"),
                          ("dam_g12w", "g12_year", "dam_key"), ("dam_g1w", "g1_year", "dam_key"),
                          ("dam_mg1w", "mg1_year", "dam_key"),
                          ("sire_gsw_ifha", "win_year", "sire_n"), ("sire_g1w_ifha", "g1_year", "sire_n")):
        e = ev.dropna(subset=[src]).rename(columns={src: "year"})
        s[col] = cum_before(e, key, s, col)
    s["sire_log_gsw_ifha"] = np.log1p(s["sire_gsw_ifha"])
    s["sire_log_g1w_ifha"] = np.log1p(s["sire_g1w_ifha"])
    # her best producer class: 0 none, 1 black-type, 2 GSW, 3 G1/G2 winner, 4 G1W, 5 MG1W
    s["dam_produce_class"] = np.select(
        [s["dam_mg1w"] > 0, s["dam_g1w"] > 0, s["dam_g12w"] > 0, s["dam_gsw"] > 0, s["dam_bt"] > 0], [5, 4, 3, 2, 1], 0)

    # the dam herself as a racehorse: IFHA horse whose name is the dam and whose sire is the damsire
    own = ev.rename(columns={"k": "dam_n", "sire_n": "damsire_n"})
    own = own[own["dam_n"] != ""].drop_duplicates(["dam_n", "damsire_n"], keep=False)
    own["race_class"] = np.select([own["mg1_year"].notna(), own["g1_year"].notna(), own["g12_year"].notna(),
                                   own["win_year"].notna()], [5, 4, 3, 2], 1)
    s = s.merge(own[["dam_n", "damsire_n", "race_class", "bt_year"]].rename(columns={"bt_year": "dam_bt_year"}),
                on=["dam_n", "damsire_n"], how="left")
    s["dam_race_class"] = np.where(s["dam_bt_year"] < s["sale_year"], s["race_class"], 0)  # same scale as above
    s = s.drop(columns=["race_class", "dam_bt_year"])

    # dam's age when this foal was born: her foaling year from catalog pedigrees (name + her sire).
    # A few mares carry two foaling years a year or two apart; take the earlier, drop wider gaps.
    p = pedigrees().rename(columns={"k": "dam_n", "sire_n": "damsire_n", "fy": "dam_fy"})
    p = p[p["dam_n"] != ""].groupby(["dam_n", "damsire_n"])["dam_fy"].agg(["min", "max"])
    p = p[p["max"] - p["min"] <= 3]["min"].rename("dam_fy").reset_index()
    s = s.merge(p, on=["dam_n", "damsire_n"], how="left")
    s["dam_age"] = (s["birth_year"] - s["dam_fy"]).where(lambda a: a.between(3, 30))
    # fallback: years since her first foal in the full sale records, which start in 1999
    # (blank when her first recorded foal was born by 2000, as she may have been producing before)
    allsales = pd.read_csv(c.DATA / "sales_lots.csv", low_memory=False, usecols=["dam", "damsire", "birth_year"])
    allsales["dam_key"] = allsales["dam"].map(c.norm) + "|" + allsales["damsire"].map(c.norm)
    first_foal = pd.to_numeric(allsales["birth_year"], errors="coerce").groupby(allsales["dam_key"]).min()
    ff = s["dam_key"].map(first_foal)
    s["dam_produce_years"] = (s["birth_year"] - ff).where(ff > 2000)
    s["dam_age_known"] = s["dam_age"].notna().astype(int)

    # 5. sire's crop number (first crop = 1); unknown for sires already active when the data starts
    first = s.groupby("sire_n")["birth_year"].min()
    s["sire_crop"] = s["birth_year"] - s["sire_n"].map(first) + 1
    s.loc[s["sire_n"].map(first) <= 2003, "sire_crop"] = np.nan
    s["first_crop"] = (s["sire_crop"] == 1).astype(int)
    s["early_crops"] = s["sire_crop"].between(1, 3).astype(int)

    # 6. foaling state
    st = s["foaled_location"].astype(str).str.upper().str.strip().replace(STATES)
    common = st[s["sale_year"] <= TRAIN[1]].value_counts()
    s["state_cat"] = np.where(s["foaled_location"].isna(), "UNKNOWN",
                              np.where(st.isin(common[common >= 300].index), st, "OTHER"))
    s["kentucky_bred"] = (st == "KENTUCKY").astype(int)
    s["state_known"] = s["foaled_location"].notna().astype(int)
    for col in CATS:
        s[col] = pd.Categorical(s[col].astype(str))
    return s


V2_X = [x for x in S.GB_X if x != "sire_log_gsw"]  # v2's inputs, minus the Equibase-based sire count
NEW_X = ["hip_pct", "cons_eff", "log_cons_n", "consignor_known", "prev_rel", "prev_log_price", "prev_rna", "has_prev",
         "dam_bt", "dam_gsw", "dam_g12w", "dam_g1w", "dam_mg1w", "dam_produce_class", "dam_race_class",
         "sire_log_gsw_ifha", "sire_log_g1w_ifha", "dam_age", "dam_produce_years", "dam_age_known", "sire_crop", "first_crop", "early_crops",
         "kentucky_bred", "state_known"]
GB_X = V2_X + NEW_X + CATS
LIN_NEW = ["hip_pct0", "hip_keeneland", "cons_eff", "log_cons_n", "consignor_known", "prev_rel0", "has_prev", "prev_rna0",
           "log_dam_gsw", "log_dam_bt", "dam_g1w_any", "pc_1", "pc_2", "pc_3", "pc_4", "pc_5", "rc_2", "rc_3", "rc_4",
           "rc_5", "sire_log_gsw_ifha", "sire_log_g1w_ifha", "dam_age0", "dam_age_sq", "dam_age_known", "first_crop", "early_crops", "kentucky_bred",
           "state_known"]


def linear_frame(d: pd.DataFrame) -> np.ndarray:
    """v2's linear inputs (with the IFHA sire count in place of the Equibase one) plus the new ones."""
    keen = d["sale"].astype(str).str.startswith("KEENELAND").astype(int)
    e = d.assign(sire_log_gsw=d["sire_log_gsw_ifha"], hip_pct0=d["hip_pct"].fillna(0.5),
                 hip_keeneland=d["hip_pct"].fillna(0.5) * keen, prev_rel0=d["prev_rel"].fillna(0.0),
                 prev_rna0=d["prev_rna"].fillna(0), log_dam_gsw=np.log1p(d["dam_gsw"]), log_dam_bt=np.log1p(d["dam_bt"]),
                 dam_g1w_any=(d["dam_g1w"] > 0).astype(int),
                 dam_age0=(d["dam_age"] - 10).fillna(0.0), dam_age_sq=((d["dam_age"] - 10) ** 2).fillna(0.0) / 10,
                 **{f"pc_{k}": (d["dam_produce_class"] == k).astype(int) for k in range(1, 6)},
                 **{f"rc_{k}": (d["dam_race_class"] == k).astype(int) for k in range(2, 6)})
    base = S.linear_frame(e)
    return np.column_stack([base, e[LIN_NEW].to_numpy(float)])


# ------------------------------------------------------------------ LightGBM, censored
def lgbm(**kw):
    return lgb.LGBMRegressor(n_estimators=kw.pop("n_estimators", 5000), learning_rate=0.03, subsample=0.8,
                             subsample_freq=1, colsample_bytree=0.8, cat_smooth=20, min_data_per_group=200,
                             random_state=42, verbose=-1, **kw)


def fit_censored_lgbm(d: pd.DataFrame, params: dict, n_estimators: int, rounds: int = 3):
    """As in v2: RNA targets are replaced by E[y | y < RNA price] under the current model, then refitted."""
    y = d["log_price"].to_numpy().copy()
    up = d["rna"].to_numpy() == 1
    cap = y.copy()
    y[up] = cap[up] - 0.5
    for _ in range(rounds):
        m = lgbm(n_estimators=n_estimators, **params).fit(d[GB_X], y)
        mu = m.predict(d[GB_X])
        sig = float(np.std((cap - mu)[~up]))
        a = (cap[up] - mu[up]) / sig
        y[up] = mu[up] - sig * np.exp(stats.norm.logpdf(a) - stats.norm.logcdf(a))
    return m


def tier(d: pd.DataFrame, cuts) -> np.ndarray:
    return np.digitize(d["sale_level"].to_numpy(), cuts)


CALIB_YEARS = range(2011, 2021)


def calibrate_ranges(s: pd.DataFrame, qp: dict, n_iter: int, cuts) -> dict:
    """How far to widen the quantile range so it holds 80% of prices, learned out of time.

    For each year 2011-2020: fit the 10% and 90% quantile models on sold lots from EARLIER years
    only, then see how far that year's real prices fell below the low end or above the high end.
    Ten years include several whole-market jumps (2017 +22%, 2020 -14%), so the ranges allow
    for them. The low and high ends are widened separately (each to miss 10%), per sale tier.
    The test years 2021-2025 are never used.
    """
    lo_s, hi_s, tiers = [], [], []
    sold = s[s["rna"] == 0]
    for y in CALIB_YEARS:
        past, now = sold[sold["sale_year"].between(TRAIN[0], y - 1)], sold[sold["sale_year"] == y]
        q10 = lgbm(objective="quantile", alpha=0.10, n_estimators=n_iter, **qp).fit(past[GB_X], past["log_price"])
        q90 = lgbm(objective="quantile", alpha=0.90, n_estimators=n_iter, **qp).fit(past[GB_X], past["log_price"])
        lo_s.append(q10.predict(now[GB_X]) - now["log_price"].to_numpy())
        hi_s.append(now["log_price"].to_numpy() - q90.predict(now[GB_X]))
        tiers.append(tier(now, cuts))
    lo_s, hi_s, tiers = np.concatenate(lo_s), np.concatenate(hi_s), np.concatenate(tiers)
    out = {"low": [], "high": []}
    for t in range(3):
        m = tiers == t
        level = 0.90 * (1 + 1 / m.sum())
        out["low"].append(float(np.quantile(lo_s[m], level)))
        out["high"].append(float(np.quantile(hi_s[m], level)))
    return out


def main() -> None:
    s = add_v3_inputs(S.add_family_effects(S.auction_table()))
    tr, va, te = (s[s["sale_year"].between(*p)] for p in (TRAIN, VALID, TEST))
    tr_sold, va_sold, te_sold = (d[d["rna"] == 0] for d in (tr, va, te))
    yv = va_sold["log_price"]
    print(f"lots: train {len(tr):,}  tune {len(va):,}  test {len(te):,}")
    print("coverage of new inputs (test lots): " + ", ".join(
        f"{k} {v:.0%}" for k, v in {"consignor": te["consignor_known"].mean(), "earlier sale": te["has_prev"].mean(),
                                   "dam has black-type foal": (te["dam_bt"] > 0).mean(),
                                   "sire crop known": te["sire_crop"].notna().mean(),
                                   "state": te["state_known"].mean()}.items()))

    # Tobit with the new inputs
    b, _ = S.fit_tobit(linear_frame(tr), tr["log_price"].to_numpy(), tr["rna"].to_numpy() == 1)
    tob_va = linear_frame(va_sold) @ b
    tob_shift = float((yv - tob_va).mean())

    # LightGBM search on 2019-2020 (sold-only fits with early stopping)
    best = None
    for leaves, mchild, l2 in itertools.product([31, 63, 127], [100, 400], [0.0, 5.0]):
        p = dict(num_leaves=leaves, min_child_samples=mchild, reg_lambda=l2)
        m = lgbm(**p).fit(tr_sold[GB_X], tr_sold["log_price"], eval_set=[(va_sold[GB_X], yv)],
                          callbacks=[lgb.early_stopping(100, verbose=False)])
        r2 = S.score(yv, m.predict(va_sold[GB_X]))["R2"]
        if best is None or r2 > best[0]:
            best = (r2, p, m.best_iteration_)
    _, params, n_iter = best
    print(f"LightGBM: best on 2019-2020 R2 {best[0]:.3f}: {params}, {n_iter} trees")
    gb = fit_censored_lgbm(tr, params, n_iter)
    gb_va = gb.predict(va_sold[GB_X])
    gb_shift = float((yv - gb_va).mean())
    ws = np.linspace(0, 1, 21)
    w = max(ws, key=lambda w: S.score(yv, w * (gb_va + gb_shift) + (1 - w) * (tob_va + tob_shift))["R2"])
    print(f"blend: {w:.2f} x LightGBM + {1 - w:.2f} x Tobit (chosen on 2019-2020)")

    # 80% range: quantile LightGBM, widened per sale tier and per side (calibrated over 2011-2020)
    qp = {k: v for k, v in params.items()}
    cuts = np.quantile(tr_sold["sale_level"], [1 / 3, 2 / 3])
    widen = calibrate_ranges(s, qp, n_iter, cuts)
    print("range widening (log), cheap / middle / dear sales: low end " + " / ".join(f"{v:.2f}" for v in widen["low"])
          + ", high end " + " / ".join(f"{v:.2f}" for v in widen["high"]))
    trva_sold = pd.concat([tr_sold, va_sold])  # range models for the test: every year before 2021
    q10 = lgbm(objective="quantile", alpha=0.10, n_estimators=n_iter, **qp).fit(trva_sold[GB_X], trva_sold["log_price"])
    q90 = lgbm(objective="quantile", alpha=0.90, n_estimators=n_iter, **qp).fit(trva_sold[GB_X], trva_sold["log_price"])

    # refit on train + tune, score on 2021-2025
    trva = pd.concat([tr, va])
    n_full = int(n_iter * len(trva) / len(tr))
    b2, _ = S.fit_tobit(linear_frame(trva), trva["log_price"].to_numpy(), trva["rna"].to_numpy() == 1)
    gb2 = fit_censored_lgbm(trva, params, n_full)

    def blend(d, tob_b=b2, gbm=gb2):
        return w * (gbm.predict(d[GB_X]) + gb_shift) + (1 - w) * (linear_frame(d) @ tob_b + tob_shift)

    tt = tier(te_sold, cuts)
    w_lo, w_hi = np.array(widen["low"])[tt], np.array(widen["high"])[tt]
    yt = te_sold["log_price"]
    results = {
        "no-model guess": S.score(yt, pd.Series(tr_sold["log_price"].mean(), index=te_sold.index)),
        "Tobit (censored linear)": S.score(yt, linear_frame(te_sold) @ b2 + tob_shift),
        "censored LightGBM": S.score(yt, gb2.predict(te_sold[GB_X]) + gb_shift),
        "v3 blend (saved)": S.score(yt, blend(te_sold), q10.predict(te_sold[GB_X]) - w_lo, q90.predict(te_sold[GB_X]) + w_hi),
    }
    v2 = json.loads((F.ART / "price_stepup_results.json").read_text())["test_all_ages"]["blend (saved)"]
    results["v2 step-up (for comparison)"] = v2
    print("\nHeld-out 2021-2025, sold lots, all ages:")
    print(pd.DataFrame(results).T.round(3).to_string())

    t = te_sold.assign(pred=blend(te_sold))
    by_age = t.groupby("age").apply(lambda d: pd.Series({"lots": len(d), **S.score(d["log_price"], d["pred"])}),
                                    include_groups=False)
    print("\nv3 by age:")
    print(by_age.round(3).to_string())
    keen = t[t["sale"] == "KEENELAND SEPTEMBER YEARLING SALE"]
    print(f"\nKeeneland September only: R2 {S.score(keen['log_price'], keen['pred'])['R2']:.3f} ({len(keen):,} lots)")
    lo_t, hi_t = q10.predict(te_sold[GB_X]) - w_lo, q90.predict(te_sold[GB_X]) + w_hi
    cov = pd.Series(((yt >= lo_t) & (yt <= hi_t)).to_numpy()).groupby(tt).mean()
    print(f"prices below the range {(yt < lo_t).mean():.1%}, above it {(yt > hi_t).mean():.1%} (target 10% each)")
    print("80% range coverage by test year: " + ", ".join(
        f"{y} {v:.1%}" for y, v in pd.Series(((yt >= lo_t) & (yt <= hi_t)).to_numpy()).groupby(te_sold["sale_year"].to_numpy()).mean().items()))
    print("80% range coverage by sale tier (cheap, middle, dear): " + ", ".join(f"{v:.1%}" for v in cov))

    # what each new input adds: drop in test R2 when it's shuffled (LightGBM part)
    rng = np.random.default_rng(0)
    base_r2 = S.score(yt, gb2.predict(te_sold[GB_X]))["R2"]
    groups = {"1 hip position": ["hip_pct"], "2 consignor": ["cons_eff", "log_cons_n", "consignor_known", "consignor_cat"],
              "3 earlier sale": ["prev_rel", "prev_log_price", "prev_rna", "has_prev"],
              "4a dam's produce class (IFHA)": ["dam_bt", "dam_gsw", "dam_g12w", "dam_g1w", "dam_mg1w", "dam_produce_class"],
              "4b dam's own race class (IFHA)": ["dam_race_class"],
              "4c sire's graded winners (IFHA)": ["sire_log_gsw_ifha", "sire_log_g1w_ifha"],
              "dam age": ["dam_age", "dam_produce_years", "dam_age_known"],
              "5 sire's crop": ["sire_crop", "first_crop", "early_crops"], "6 state bred": ["state_cat", "kentucky_bred", "state_known"],
              "sale level + growth forecast": ["sale_level", "growth_fc"], "family effects": ["sire_eff", "dam_eff", "damsire_eff", "nick_eff"]}
    imp = {}
    for name, cols in groups.items():
        x = te_sold[GB_X].copy()
        idx = rng.permutation(len(x))
        for col in cols:
            x[col] = x[col].iloc[idx].set_axis(x.index)  # keeps category columns as categories
        imp[name] = base_r2 - S.score(yt, gb2.predict(x))["R2"]
    print("\nDrop in test R2 when a group of inputs is shuffled (LightGBM):")
    print(pd.Series(imp).sort_values(ascending=False).round(3).to_string())
    two = t[t["age"] == 2]
    print(f"\n2-year-olds with an earlier sale: R2 {S.score(two[two['has_prev'] == 1]['log_price'], two[two['has_prev'] == 1]['pred'])['R2']:.3f}"
          f"; without: {S.score(two[two['has_prev'] == 0]['log_price'], two[two['has_prev'] == 0]['pred'])['R2']:.3f}")

    # final refit on every year, saved
    n_all = int(n_iter * len(s) / len(tr))
    b3, _ = S.fit_tobit(linear_frame(s), s["log_price"].to_numpy(), s["rna"].to_numpy() == 1)
    gb3 = fit_censored_lgbm(s, params, n_all)
    sold_all = s[s["rna"] == 0]
    joblib.dump({"tobit_beta": b3, "gb": gb3, "weight": w, "gb_shift": gb_shift, "tobit_shift": tob_shift,
                 "q10": lgbm(objective="quantile", alpha=0.10, n_estimators=n_all, **qp).fit(sold_all[GB_X], sold_all["log_price"]),
                 "q90": lgbm(objective="quantile", alpha=0.90, n_estimators=n_all, **qp).fit(sold_all[GB_X], sold_all["log_price"]),
                 "widen_low_by_tier": widen["low"], "widen_high_by_tier": widen["high"], "tier_cuts": cuts.tolist(), "features": GB_X,
                 "categories": {k: list(s[k].cat.categories) for k in CATS}, "params": params, "n_estimators": n_all,
                 "growth_beta": S.MG.fit(S.MG.growth_table(s))},
                F.ART / "price_v3.joblib")
    (F.ART / "price_v3_results.json").write_text(json.dumps(
        {"test_all_ages": results, "by_age": by_age.to_dict(orient="index"), "importance": imp,
         "coverage_by_tier": cov.tolist(), "weight": w, "params": params}, indent=2, default=float))
    print("\nsaved artifacts/price_v3.joblib")


if __name__ == "__main__":
    main()

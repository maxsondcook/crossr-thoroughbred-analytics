"""OBS 2-year-olds in training: how much do breeze (under-tack) times add to the price model?

    ../.venv/bin/python obs_2yo.py       # run v3_price.py first (needs artifacts/v3_frame.pkl via rolling_price.py)

Design decision (2026-11-18): add OBS as a new sale source for 2-year-olds. Its public sale results
(2023-2026) were saved once from obssales.com, whose robots.txt permits it; they stay local in
data/obs_undertack/ and are not in this repository. obscatalog.com (pre-2023) was not used, since
its robots.txt reserves rights against AI training.

Data: 12 OBS 2YO sales (March, Spring, June), 2023-2026; breeze times exist from 2024.
Each lot gets, from our other sale records and IFHA, using years before its sale only:
- sire: stud fee at conception, his commercial premium (shrunk), IFHA graded winners, crop number;
- dam: her foals' premium, IFHA produce class (black-type ... MG1W);
- the horse itself sold earlier as a weanling/yearling (the pinhook): how it sold vs its sale;
- consignor premium (other 2YO sales + earlier OBS sales); hip position; sex; foaling state/month.
Breeze inputs: time scored against horses that breezed the same distance on the same day
(z-score, lower = faster), raw time per furlong, distance, galloped only / no work.

Test: predict 2025 from 2024, and 2026 from 2024-2025 (sold lots); compare with and without breeze.
"""
from __future__ import annotations

import glob
import json
import re
import warnings

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression

import features as F
import stepup_price as S
import v3_price as V
from features import c

warnings.filterwarnings("ignore")
OBS = F.HERE.parent / "data" / "obs_undertack"
SALES_2YO = {130: "MARCH", 131: "SPRING", 132: "JUNE", 135: "MARCH", 136: "SPRING", 137: "JUNE", 142: "MARCH",
             144: "SPRING", 145: "JUNE", 149: "MARCH", 150: "SPRING", 151: "JUNE"}
CONS_RE = r"[,.]?\s*\(?AGENT[\s\w]*\)?$|\s*\(.*\)$"


def obs_lots() -> pd.DataFrame:
    d = pd.concat([pd.read_csv(f, dtype=str) for f in glob.glob(str(OBS / "sale_*.csv"))])
    d["sale_id"] = d["sale_id"].astype(int)
    d = d[d["sale_id"].isin(SALES_2YO) & (d["in_out_status"] == "I")].copy()
    meta = pd.DataFrame(json.loads((OBS / "sales_1_160.json").read_text()))
    meta["sale_id"] = meta["sale_id"].astype(int)
    d = d.merge(meta[["sale_id", "sale_starts"]], on="sale_id")
    d["sale_year"] = pd.to_datetime(d["sale_starts"]).dt.year
    d["sale"] = d["sale_id"].map(SALES_2YO)
    hp, rp = pd.to_numeric(d["hammer_price"], errors="coerce"), pd.to_numeric(d["sale_price_rna"], errors="coerce")
    d["rna"] = (d["rna_summary_indicator"] == "Y").astype(int)
    d["price"] = np.where(d["rna"] == 1, rp.where(rp > 0, hp.abs()), hp)
    d = d[d["price"] >= 1000].copy()
    d["log_price"] = np.log(d["price"])
    d["sire_n"], d["dam_n"] = d["sire_name"].map(c.norm), d["dam_name"].map(c.norm)
    d["damsire_n"] = d["dam_sire"].map(c.norm)
    d["birth_year"] = pd.to_numeric(d["foaling_year"], errors="coerce")
    d["filly"] = d["sex"].eq("F").astype(int)
    d["hip_pct"] = d.groupby("sale_id")["hip_number"].transform(lambda h: pd.to_numeric(h, errors="coerce").rank(pct=True))
    st = d["foaling_area"].astype(str).str.strip()
    d["kentucky_bred"], d["florida_bred"] = (st == "KY").astype(int), (st == "FL").astype(int)
    d["foal_month"] = pd.to_datetime(d["foaling_date"], errors="coerce").dt.month
    d["consignor_n"] = (d["consignor_name"].astype(str).str.upper().str.strip()
                        .str.replace(CONS_RE, "", regex=True).str.strip(" ,."))
    # breeze
    dist = d["ut_distance"].astype(str).str.strip().str.upper()
    furl = dist.map({"1/8": 1, "1/4": 2, "3/8": 3, "1/2": 4})
    t = pd.to_numeric(d["ut_time"], errors="coerce")
    d["ut_furlongs"] = furl
    d["ut_per_furlong"] = t / furl
    d["galloped"] = dist.str.startswith("G").astype(int)
    grp = [d["sale_id"], d["ut_furlongs"], d["ut_actual_date"]]
    z = (t - t.groupby(grp).transform("mean")) / t.groupby(grp).transform("std")
    zs = (t - t.groupby([d["sale_id"], d["ut_furlongs"]]).transform("mean")) / t.groupby([d["sale_id"], d["ut_furlongs"]]).transform("std")
    bad = zs.abs() > 5  # misrecorded times (e.g. a 1/4 time filed under 1/8)
    d["ut_z_day"] = z.where(~bad & (t.groupby(grp).transform("size") >= 10), zs.where(~bad))
    d["ut_z_sale"] = zs.where(~bad)
    d["ut_per_furlong"] = d["ut_per_furlong"].where(~bad)
    d["breeze_known"] = d["ut_z_sale"].notna().astype(int)
    d["no_work"] = ((d["breeze_known"] == 0) & (d["galloped"] == 0)).astype(int)
    d["quarter"] = (furl == 2).astype(int)
    return d.reset_index(drop=True)


def latest_before(src: pd.DataFrame, key: str, val: str, d: pd.DataFrame, dkey: str, name: str) -> pd.Series:
    """Most recent value of `val` for the key from rows with sale_year < the lot's sale_year."""
    a = src[[key, "sale_year", val]].dropna().groupby([key, "sale_year"])[val].mean().reset_index()
    a["sale_year"] = a["sale_year"].astype(int) + 1
    q = d[[dkey, "sale_year"]].reset_index().dropna().astype({"sale_year": int}).sort_values("sale_year")
    m = pd.merge_asof(q, a.rename(columns={key: dkey}).sort_values("sale_year"), on="sale_year", by=dkey, direction="backward")
    return m.set_index("index")[val].reindex(d.index).rename(name)


def shrunk_before(src: pd.DataFrame, key: str, d: pd.DataFrame, dkey: str, k: float, name: str) -> pd.Series:
    """Shrunk mean of `rel` for the key from rows with sale_year < the lot's sale_year."""
    a = src.dropna(subset=[key, "rel"]).groupby([key, "sale_year"])["rel"].agg(["sum", "count"]).reset_index().sort_values("sale_year")
    g = a.groupby(key)
    a["cs"], a["cn"] = g["sum"].cumsum(), g["count"].cumsum()
    a["eff"] = a["cs"] / (a["cn"] + k)
    a["sale_year"] = a["sale_year"].astype(int) + 1
    q = d[[dkey, "sale_year"]].reset_index().dropna().astype({"sale_year": int}).sort_values("sale_year")
    m = pd.merge_asof(q, a[[key, "sale_year", "eff"]].rename(columns={key: dkey}).sort_values("sale_year"),
                      on="sale_year", by=dkey, direction="backward")
    return m.set_index("index")["eff"].reindex(d.index).fillna(0.0).rename(name)


def add_inputs(d: pd.DataFrame, s: pd.DataFrame) -> pd.DataFrame:
    d = d.copy()
    # sire's fee at conception (his median fee for that conception year in our records), else his latest earlier fee
    fee = s.dropna(subset=["log_fee"]).groupby(["sire_n", "conception_year"])["log_fee"].median().rename("log_fee")
    d["conception_year"] = d["birth_year"] - 1
    d = d.join(fee, on=["sire_n", "conception_year"])
    d["log_fee"] = d["log_fee"].fillna(latest_before(s, "sire_n", "log_fee", d, "sire_n", "x"))
    d["fee_known"] = d["log_fee"].notna().astype(int)
    # family premiums from our records + earlier OBS sales (rel = price vs its sale's average)
    d["rel"] = d["log_price"] - d.groupby("sale_id")["log_price"].transform("mean")
    d["dam_key"] = np.where(d["damsire_n"] != "", d["dam_n"] + "|" + d["damsire_n"], None)
    src = pd.concat([s[["sire_n", "dam_key", "consignor_n", "sale_year", "rel"]],
                     d[["sire_n", "dam_key", "consignor_n", "sale_year", "rel"]]])
    d["sire_eff"] = shrunk_before(src, "sire_n", d, "sire_n", S.SHRINK["sire"], "sire_eff")
    d["dam_eff"] = shrunk_before(src, "dam_key", d, "dam_key", S.SHRINK["dam"], "dam_eff")
    d["cons_eff"] = shrunk_before(src, "consignor_n", d, "consignor_n", 20, "cons_eff")
    # IFHA: sire's graded winners and dam's produce class before the sale year
    ev = V.graded_events()
    name = ev["dam_key"].str[:-1]
    full = s[s["damsire_n"] != ""]
    keys = full.groupby("dam_n")["dam_key"].unique()
    uk = keys[keys.map(len) == 1].map(lambda k: k[0])
    span = full.groupby("dam_key")["birth_year"].agg(["min", "max"])
    cand = name.map(uk)
    ok = cand.notna() & ev["fy"].between(cand.map(span["min"]) - 4, cand.map(span["max"]) + 4)
    ev.loc[ok, "dam_key"] = cand[ok]
    # OBS lots with no damsire: link by dam name the same way
    d["dam_key_ev"] = d["dam_key"].fillna(d["dam_n"].map(uk))
    for col, src_col, key, dkey in (("sire_gsw", "win_year", "sire_n", "sire_n"), ("sire_g1w", "g1_year", "sire_n", "sire_n"),
                                    ("dam_bt", "bt_year", "dam_key", "dam_key_ev"), ("dam_gsw", "win_year", "dam_key", "dam_key_ev"),
                                    ("dam_g1w", "g1_year", "dam_key", "dam_key_ev")):
        e = ev.dropna(subset=[src_col]).rename(columns={src_col: "year", key: dkey})
        d[col] = V.cum_before(e, dkey, d, col)
    d["sire_log_gsw"], d["sire_log_g1w"] = np.log1p(d["sire_gsw"]), np.log1p(d["sire_g1w"])
    d["dam_produce_class"] = np.select([d["dam_g1w"] > 0, d["dam_gsw"] > 0, d["dam_bt"] > 0], [3, 2, 1], 0)
    # sire's crop number
    first = pd.concat([s[["sire_n", "birth_year"]], d[["sire_n", "birth_year"]]]).groupby("sire_n")["birth_year"].min()
    d["sire_crop"] = d["birth_year"] - d["sire_n"].map(first) + 1
    d.loc[d["sire_n"].map(first) <= 2003, "sire_crop"] = np.nan
    # the same horse sold earlier as a weanling or yearling (pinhook): dam + damsire + foaling year
    prev = s[s["age"] < 2].copy()
    prev["rel_any"] = prev["log_price"] - (prev["sale_level"] + prev["growth_fc"].fillna(0))
    prev["hk"] = prev["dam_n"] + "|" + prev["damsire_n"] + "|" + prev["birth_year"].astype(int).astype(str)
    prev = prev.sort_values("age").groupby("hk").agg(prev_rel=("rel_any", "last"), prev_log_price=("log_price", "last"),
                                                     prev_rna=("rna", "last"))
    hk = d["dam_n"] + "|" + d["damsire_n"].fillna("") + "|" + d["birth_year"].astype("Int64").astype(str)
    d = d.assign(hk=hk).join(prev, on="hk")
    d["has_prev"] = d["prev_log_price"].notna().astype(int)
    # sale level: the same OBS sale's average last year (2023 has none -> not used for training)
    lvl = d.groupby(["sale", "sale_year"])["log_price"].mean().rename("sale_level").reset_index()
    lvl["sale_year"] += 1
    d = d.merge(lvl, on=["sale", "sale_year"], how="left")
    d["sale_cat"] = pd.Categorical(d["sale"])
    return d


BASE_X = ["log_fee", "fee_known", "sire_eff", "dam_eff", "cons_eff", "sire_log_gsw", "sire_log_g1w", "dam_bt", "dam_gsw",
          "dam_g1w", "dam_produce_class", "sire_crop", "prev_rel", "prev_log_price", "prev_rna", "has_prev", "hip_pct",
          "filly", "kentucky_bred", "florida_bred", "foal_month", "sale_level", "sale_cat"]
BREEZE_X = ["ut_z_day", "ut_z_sale", "ut_per_furlong", "quarter", "galloped", "no_work", "breeze_known"]


def lin_frame(d: pd.DataFrame, X: list[str]) -> pd.DataFrame:
    e = d[[x for x in X if x != "sale_cat"]].copy()
    for col in e:
        if e[col].isna().any():
            e[col + "_na"] = e[col].isna().astype(int)
            e[col] = e[col].fillna(0 if col.startswith(("ut_z", "prev")) else e[col].median())
    for k in ("SPRING", "JUNE"):
        e[f"sale_{k}"] = (d["sale"] == k).astype(int)
    return e


def gbm():
    return lgb.LGBMRegressor(n_estimators=400, learning_rate=0.03, num_leaves=15, min_child_samples=40,
                             subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=5.0, random_state=42, verbose=-1)


def main() -> None:
    s = pd.read_pickle(F.ART / "v3_frame.pkl")
    if "consignor_n" not in s:
        raise SystemExit("v3_frame.pkl is missing; run rolling_price.py first")
    d = add_inputs(obs_lots(), s)
    sold = d[(d["rna"] == 0) & (d["sale_year"] >= 2024)]
    print(f"OBS 2YO lots (in the ring) {len(d):,}; sold 2024-2026 {len(sold):,}; with a timed breeze "
          f"{sold['breeze_known'].mean():.0%}; sold earlier as weanling/yearling {sold['has_prev'].mean():.0%}; "
          f"fee known {sold['fee_known'].mean():.0%}; dam with a black-type foal {(sold['dam_bt'] > 0).mean():.1%}")

    preds = []
    for y in (2025, 2026):
        tr, te = sold[sold["sale_year"] < y], sold[sold["sale_year"] == y]
        out = te[["sale_id", "hip_number", "sale_year", "sale", "log_price", "ut_z_day", "breeze_known", "has_prev"]].copy()
        for tag, X in (("no breeze", BASE_X), ("with breeze", BASE_X + BREEZE_X)):
            out[f"gb {tag}"] = gbm().fit(tr[X], tr["log_price"]).predict(te[X])
            lt, le = lin_frame(tr, X), lin_frame(te, X)
            le = le.reindex(columns=lt.columns, fill_value=0)
            out[f"lin {tag}"] = LinearRegression().fit(lt, tr["log_price"]).predict(le)
        preds.append(out)
    p = pd.concat(preds)
    rows = {}
    for col in [c_ for c_ in p if c_.startswith(("gb", "lin"))]:
        rows[col] = S.score(p["log_price"], p[col])
    print("\nHeld-out OBS 2YO sales 2025-2026 (sold lots), trained on earlier OBS years only:")
    print(pd.DataFrame(rows).T.round(3).to_string())
    by = p.groupby("sale_year").apply(lambda g: pd.Series({"lots": len(g), "R2 no breeze": S.score(g["log_price"], g["gb no breeze"])["R2"],
                                                           "R2 with breeze": S.score(g["log_price"], g["gb with breeze"])["R2"]}), include_groups=False)
    print("\nby year (LightGBM):\n" + by.round(3).to_string())

    # how much is a faster breeze worth? linear fit on all sold 2024-2026, timed 1/8 breezes
    t = sold[(sold["breeze_known"] == 1) & (sold["ut_furlongs"] == 1)]
    lf = lin_frame(t, BASE_X + ["ut_per_furlong"])
    b = LinearRegression().fit(lf, t["log_price"])
    per_fifth = float(pd.Series(b.coef_, index=lf.columns)["ut_per_furlong"]) * 0.2
    print(f"\nEighth-mile breezes: each 1/5 second faster -> price x{np.exp(-per_fifth):.2f} (holding the other inputs fixed)")
    t = t.assign(band=pd.qcut(t["ut_z_day"], 5, labels=["fastest 20%", "2nd", "3rd", "4th", "slowest 20%"]))
    print("median sold price by breeze speed (vs same distance, same day):")
    print(t.groupby("band", observed=True)["price"].agg(["size", "median"]).round(0).to_string())
    p.to_pickle(F.ART / "obs_2yo_preds.pkl")
    X = BASE_X + BREEZE_X
    import joblib
    joblib.dump({"gb": gbm().fit(sold[X], sold["log_price"]), "features": X, "trained_through": int(sold["sale_year"].max()),
                 "sale_categories": list(d["sale_cat"].cat.categories)}, F.ART / "obs_2yo.joblib")
    (F.ART / "obs_2yo_results.json").write_text(json.dumps(
        {"test": rows, "by_year": by.to_dict(orient="index"), "price_x_per_fifth_faster_1_8": float(np.exp(-per_fifth)),
         "lots_sold_2024_2026": len(sold)}, indent=2, default=float))


if __name__ == "__main__":
    main()

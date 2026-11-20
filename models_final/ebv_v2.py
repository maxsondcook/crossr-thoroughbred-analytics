"""Breeding-value models, v2: v1 plus what the v3 price model knows.

    ../.venv/bin/python ebv_v2.py        # run rolling_price.py first (it saves rolling_preds.pkl)

Design decision (2026-11-18). The targets are unchanged:
- mares: her next yearling's log(price / stud fee);
- stallions: his lifetime AEI (log).

New inputs:
- "beat": how far a foal sold above or below the price model's out-of-time prediction
  (rolling_price.py: the model for year Y is trained only on years before Y). This removes the
  sale, hip position, consignor, market year and pedigree, leaving roughly what the buyers saw
  in the horse itself. Available for sales from 2008 on.
  - mares: the average beat of her earlier foals (any age 0-2; each foal counted once, and only
    foals born before this one and sold in an earlier year);
  - stallions: the average beat of his first two crops, plus where they sat in their catalogs
    (hip position) and the consignors' premium.
- IFHA black type (graded races only), as built for v3:
  - mares: her produce class so far (black-type, GSW, G1/G2W, G1W, MG1W) and her own race class;
  - stallions: his own race class.
- mares: her age, and the covering sire's and her sire's commercial premium (family effects).

Same tests as v1: mares train 2005-2019, tune 2020, test 2021-2025; stallions 5-fold CV x10.
Both versions are scored on the same rows so the comparison is fair.
"""
from __future__ import annotations

import json
import sys
import warnings

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import LinearRegression
from sklearn.model_selection import RepeatedKFold

import features as F
import train_ebv as T
import v3_price as V
from features import c

sys.path.insert(0, str(F.HERE.parent / "model_walkthrough"))
import fit_ebv_stallion  # noqa: E402

warnings.filterwarnings("ignore")
r2 = T.r2


def load() -> pd.DataFrame:
    s = pd.read_pickle(F.ART / "v3_frame.pkl")
    p = pd.read_pickle(F.ART / "rolling_preds.pkl")
    s["beat"] = (p["log_price"] - p["pred"]).reindex(s.index)  # sold lots, 2008+, out of time
    return s


# ------------------------------------------------------------------ mares
MARE2_NEW = ["prev_beat", "log_prev_beat_n", "beat_known", "dam_age0", "dam_age_known", "dam_produce_class", "log_dam_bt",
             "log_dam_gsw", "dam_g1w_any", "dam_race_class", "sire_eff", "damsire_eff", "sire_log_gsw_ifha"]
MARE2_X = T.MARE_X + MARE2_NEW


def earlier_foals(rows: pd.DataFrame, foals: pd.DataFrame, val: str, name: str) -> pd.DataFrame:
    """For each row: mean of `val` over the dam's foals born earlier AND sold in an earlier year."""
    m = rows[["dam_key", "birth_year", "sale_year"]].reset_index().merge(
        foals[["dam_key", "f_birth", "f_year", val]], on="dam_key")
    m = m[(m["f_birth"] < m["birth_year"]) & (m["f_year"] < m["sale_year"])]
    agg = m.groupby("index")[val].agg(["mean", "count"])
    return pd.DataFrame({name: agg["mean"], f"{name}_n": agg["count"]}).reindex(rows.index)


def mare_table(s: pd.DataFrame) -> pd.DataFrame:
    s = s[(s["damsire_n"] != "") & s["dam_n"].ne("")].copy()
    s["dam_key"] = s["dam_n"] + "|" + s["damsire_n"]
    y = s[(s["age"] == 1) & (s["rna"] == 0) & s["log_fee"].notna()].copy()
    y["added"] = y["log_price"] - y["log_fee"]

    # v1's record: earlier yearlings' price / fee
    f1 = y.assign(f_birth=y["birth_year"], f_year=y["sale_year"])
    y = y.join(earlier_foals(y, f1, "added", "prev_mean"))
    y = y[y["prev_mean_n"] > 0].copy()
    y["log_prev_n"] = np.log(y["prev_mean_n"])
    y["prev_n"] = y["prev_mean_n"]

    # new: earlier foals' beat vs the price model (each foal once: its mean over its sales)
    sold = s[s["beat"].notna()]
    f2 = sold.groupby(["dam_key", "horse"]).agg(f_birth=("birth_year", "first"), f_year=("sale_year", "max"),
                                               beat=("beat", "mean")).reset_index()
    y = y.join(earlier_foals(y, f2, "beat", "prev_beat"))
    y["beat_known"] = y["prev_beat"].notna().astype(int)
    y["prev_beat"] = y["prev_beat"].fillna(0.0)
    y["log_prev_beat_n"] = np.log1p(y["prev_beat_n"].fillna(0))

    # v1's own-record inputs (race record and her sire's AEI), as in fit_ebv_mare.build
    run = c.runners()
    run = run[run["sex"].isin(["F", "M"])].sort_values("earnings", ascending=False)
    run = run.drop_duplicates(["name_n", "sire_n"]).rename(columns={"name_n": "dam_n", "sire_n": "damsire_n"})
    y = y.merge(run[["dam_n", "damsire_n", "earnings", "graded_wins"]], on=["dam_n", "damsire_n"], how="left").set_axis(y.index)
    y["record_known"] = y["earnings"].notna().astype(int)
    y["log_earnings"] = np.log1p(y["earnings"]).fillna(0.0)
    y["graded_winner"] = (y["graded_wins"] > 0).astype(int)
    aei = c.lifetime_aei().groupby("stallion_n")["aei"].max()
    y["sire_aei"] = y["damsire_n"].map(aei)
    y["sire_aei_known"] = y["sire_aei"].notna().astype(int)
    y["log_sire_aei"] = np.log(y["sire_aei"]).fillna(0.0)

    # new: IFHA classes and age (built in v3_price.add_v3_inputs)
    y["dam_age0"] = (y["dam_age"] - 10).fillna(0.0)
    y["log_dam_bt"], y["log_dam_gsw"] = np.log1p(y["dam_bt"]), np.log1p(y["dam_gsw"])
    y["dam_g1w_any"] = (y["dam_g1w"] > 0).astype(int)
    for col in ("sire_eff", "damsire_eff"):
        y[col] = y[col].fillna(0.0)
    return y


def lgb_reg(**kw):
    return lgb.LGBMRegressor(n_estimators=3000, learning_rate=0.02, subsample=0.8, subsample_freq=1,
                             colsample_bytree=0.8, random_state=42, verbose=-1, **kw)


def mares(s: pd.DataFrame) -> dict:
    d = mare_table(s)
    tr, va, te = (d[d["sale_year"].between(*p)] for p in ((2005, 2019), (2020, 2020), (2021, 2025)))
    trva = pd.concat([tr, va])
    yt = te["added"]
    out = {"baseline": r2(yt, np.full(len(te), tr["added"].mean()))}
    lin = {}
    for name, X in (("v1 inputs", T.MARE_X), ("v2 inputs", MARE2_X)):
        lin[name] = LinearRegression().fit(trva[X], trva["added"])
        out[f"linear regression, {name}"] = r2(yt, lin[name].predict(te[X]))
    best = None
    for leaves in (7, 15, 31):
        for mchild in (50, 200):
            m = lgb_reg(num_leaves=leaves, min_child_samples=mchild).fit(
                tr[MARE2_X], tr["added"], eval_set=[(va[MARE2_X], va["added"])], callbacks=[lgb.early_stopping(100, verbose=False)])
            sc = r2(va["added"], m.predict(va[MARE2_X]))
            if best is None or sc > best[0]:
                best = (sc, dict(num_leaves=leaves, min_child_samples=mchild), m.best_iteration_)
    _, params, n = best
    gb = lgb_reg(**params).set_params(n_estimators=int(n * len(trva) / len(tr))).fit(trva[MARE2_X], trva["added"])
    out["LightGBM, v2 inputs"] = r2(yt, gb.predict(te[MARE2_X]))
    print(f"Mares: train {len(tr):,} foals, tune {len(va):,}, test {len(te):,} (2021-2025); "
          f"earlier foals with a beat: {te['beat_known'].mean():.0%} of test rows")
    for k, v in out.items():
        print(f"  {k:<36} test R2 {v:+.3f}")
    print(f"  LightGBM settings (chosen on 2020): {params}, {n} trees")

    winner = max([k for k in out if k != "baseline"], key=out.get)
    if winner.startswith("LightGBM"):
        model, X, pred = gb, MARE2_X, gb.predict(te[MARE2_X])
    else:
        X = T.MARE_X if "v1" in winner else MARE2_X
        model, pred = lin["v1 inputs" if "v1" in winner else "v2 inputs"], lin["v1 inputs" if "v1" in winner else "v2 inputs"].predict(te[X])
    t = te.assign(pred=pred)
    t["band"] = pd.qcut(t["pred"], 5, labels=["lowest 20%", "2nd", "3rd", "4th", "highest 20%"])
    bands = t.groupby("band", observed=True).apply(lambda g: pd.Series({
        "foals": len(g), "median_price_over_fee": float(np.median(np.exp(g["added"])))}), include_groups=False)
    print(f"  held-out foals by the {winner} prediction (median price / fee):")
    print(bands.round(2).to_string())
    lin2 = lin["v2 inputs"]
    coefs = pd.Series(lin2.coef_, index=MARE2_X)[MARE2_NEW].round(3)
    print("  linear coefficients of the new inputs:\n" + coefs.to_string())

    alld = d[d["sale_year"].between(2005, 2025)]
    final = (lgb_reg(**params).set_params(n_estimators=int(n * len(alld) / len(tr))) if winner.startswith("LightGBM")
             else LinearRegression()).fit(alld[X], alld["added"])
    joblib.dump({"model": final, "features": X, "kind": winner, "target": "log(foal price / stud fee)"}, F.ART / "mare_ebv_v2.joblib")
    print(f"  saved artifacts/mare_ebv_v2.joblib ({winner})")
    return {k: round(float(v), 4) for k, v in out.items()} | {"saved": winner, "n_test": len(te),
                                                              "bands": bands.round(3).to_dict(orient="index")}


# ------------------------------------------------------------------ stallions
STALLION2_NEW = ["first_crops_beat", "first_crops_beat_known", "first_crops_hip", "first_crops_cons", "own_race_class"]
STALLION2_X = T.STALLION_X + STALLION2_NEW


def stallion_table(s: pd.DataFrame) -> pd.DataFrame:
    st = fit_ebv_stallion.build().dropna(subset=["first_crops_log_price"]).reset_index(drop=True)
    lots = s[(s["rna"] == 0)].merge(st[["stallion_n", "foaling_year"]].rename(columns={"stallion_n": "sire_n"}), on="sire_n")
    lots = lots[lots["sale_year"] - lots["foaling_year"] <= 7]  # same window as v1: his first two crops
    agg = lots.groupby(["sire_n", "foaling_year"]).agg(first_crops_beat=("beat", "mean"), beat_n=("beat", "count"),
                                                       first_crops_hip=("hip_pct", "mean"), first_crops_cons=("cons_eff", "mean"))
    agg.loc[agg["beat_n"] < 3, "first_crops_beat"] = np.nan
    st = st.merge(agg.reset_index().rename(columns={"sire_n": "stallion_n"}), on=["stallion_n", "foaling_year"], how="left")
    st["first_crops_beat_known"] = st["first_crops_beat"].notna().astype(int)
    st["first_crops_beat"] = st["first_crops_beat"].fillna(0.0)
    st["first_crops_hip"] = st["first_crops_hip"].fillna(0.5)
    st["first_crops_cons"] = st["first_crops_cons"].fillna(0.0)
    ev = V.graded_events().rename(columns={"k": "stallion_n", "fy": "foaling_year"}).drop_duplicates(["stallion_n", "foaling_year"])
    ev["own_race_class"] = np.select([ev["mg1_year"].notna(), ev["g1_year"].notna(), ev["g12_year"].notna(),
                                      ev["win_year"].notna()], [5, 4, 3, 2], 1)
    st = st.merge(ev[["stallion_n", "foaling_year", "own_race_class"]], on=["stallion_n", "foaling_year"], how="left")
    st["own_race_class"] = st["own_race_class"].fillna(0)
    return st


def stallions(s: pd.DataFrame) -> dict:
    d = stallion_table(s)
    y = d["log_aei"]
    cv = RepeatedKFold(n_splits=5, n_repeats=10, random_state=42)
    sc = {"baseline": [], "linear regression, v1 inputs": [], "linear regression, v2 inputs": [],
          "gradient boosting, v2 inputs": []}
    for tr, te in cv.split(d):
        sc["baseline"].append(r2(y.iloc[te], np.full(len(te), y.iloc[tr].mean())))
        for name, X in (("v1", T.STALLION_X), ("v2", STALLION2_X)):
            m = LinearRegression().fit(d.iloc[tr][X], y.iloc[tr])
            sc[f"linear regression, {name} inputs"].append(r2(y.iloc[te], m.predict(d.iloc[te][X])))
        g = HistGradientBoostingRegressor(learning_rate=0.02, max_leaf_nodes=4, min_samples_leaf=25, max_iter=300,
                                          random_state=42).fit(d.iloc[tr][STALLION2_X], y.iloc[tr])
        sc["gradient boosting, v2 inputs"].append(r2(y.iloc[te], g.predict(d.iloc[te][STALLION2_X])))
    print(f"\nStallions: {len(d)} sires, 5-fold CV x10; first crops with a beat (sold 2008+): "
          f"{d['first_crops_beat_known'].sum()}")
    for k, v in sc.items():
        print(f"  {k:<36} mean R2 {np.mean(v):+.3f}  (sd {np.std(v):.3f})")
    winner = max([k for k in sc if k != "baseline"], key=lambda k: np.mean(sc[k]))
    X = T.STALLION_X if "v1" in winner else STALLION2_X
    model = (HistGradientBoostingRegressor(learning_rate=0.02, max_leaf_nodes=4, min_samples_leaf=25, max_iter=300, random_state=42)
             if winner.startswith("gradient") else LinearRegression()).fit(d[X], y)
    lin2 = LinearRegression().fit(d[STALLION2_X], y)
    print("  linear coefficients of the new inputs:\n" + pd.Series(lin2.coef_, index=STALLION2_X)[STALLION2_NEW].round(3).to_string())
    joblib.dump({"model": model, "features": X, "kind": winner, "target": "log lifetime AEI"}, F.ART / "stallion_ebv_v2.joblib")
    print(f"  saved artifacts/stallion_ebv_v2.joblib ({winner})")
    return {k: round(float(np.mean(v)), 4) for k, v in sc.items()} | {"saved": winner, "n": len(d)}


if __name__ == "__main__":
    s = load()
    res = {"mare": mares(s), "stallion": stallions(s)}
    (F.ART / "ebv_v2_results.json").write_text(json.dumps(res, indent=2, default=float))

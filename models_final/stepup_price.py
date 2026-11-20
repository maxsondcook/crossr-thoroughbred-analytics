"""Step-up sale-price model: all auction ages, unsold horses as censored, family effects, blend.

    ../.venv/bin/python stepup_price.py

Design decision (2026-11-17):
* weanlings, yearlings and 2-year-olds, sold and unsold (RNA);
* an RNA's recorded price is an UPPER bound: the market value was below it;
* family effects: sire, dam, damsire and sire x damsire premiums from earlier years only;
* stronger tuning and a blend of models; scikit-learn only; price model only.

Train 2005-2018, tune 2019-2020, test 2021-2025. Scores are on sold lots (the only real prices),
plus a check that unsold lots are predicted below their RNA price.
"""
from __future__ import annotations

import itertools
import warnings
import json
import math

import joblib
import numpy as np
import pandas as pd
from scipy import optimize, stats
from sklearn.ensemble import HistGradientBoostingRegressor

import features as F
import market_growth as MG
from features import c

warnings.filterwarnings("ignore", category=RuntimeWarning)
TRAIN, VALID, TEST = (2005, 2018), (2019, 2020), (2021, 2025)
SHRINK = {"sire": 20, "dam": 2, "damsire": 20, "nick": 5}  # pseudo-lots pulling a family toward average


# ------------------------------------------------------------------ data
def auction_table(extra: pd.DataFrame | None = None) -> pd.DataFrame:
    """All lots aged 0-2; `extra` adds rows in the sales_lots.csv layout (e.g. obs_lots.as_sales_lots())."""
    s = pd.read_csv(c.DATA / "sales_lots.csv", low_memory=False)
    if extra is not None:
        s = pd.concat([s, extra], ignore_index=True)
    s["sale_year"] = pd.to_numeric(s["catalog_year"], errors="coerce")
    s["birth_year"] = pd.to_numeric(s["birth_year"], errors="coerce")
    s["age"] = s["sale_year"] - s["birth_year"]
    s["price"] = pd.to_numeric(s["price_num"], errors="coerce")
    rna = s["is_rna"].astype(str).isin(["1", "True"])
    out = s["is_out"].astype(str).isin(["1", "True"])
    s = s[s["age"].between(0, 2) & (s["price"] > 0) & ~out].copy()
    s["rna"] = rna.loc[s.index].astype(int)
    s = s.drop_duplicates(subset=["source_file", "hip", "name_key"])
    s["sire_n"] = s["sire"].map(c.norm)
    s["dam_n"] = s["dam"].map(c.norm)
    s["damsire_n"] = s["damsire"].map(c.norm)
    s["filly"] = (s["sex_letter"].astype(str).str.upper() == "F").astype(int)
    s["sale"] = s["catalog_title"].astype(str).str.replace(r"\s*\(?\d{4}\)?\s*", " ", regex=True).str.strip().str.upper()
    s["log_price"] = np.log(s["price"])

    fees = c.stud_fees().rename(columns={"stallion_n": "sire_n"}).sort_values("year")
    s["conception_year"] = s["birth_year"] - 1
    s = s.dropna(subset=["conception_year", "sale_year"])
    for col, when in (("log_fee", "conception_year"), ("log_fee_now", "sale_year")):
        s = s.sort_values(when)
        m = pd.merge_asof(s[[when, "sire_n"]].reset_index(), fees.rename(columns={"year": "fy"}),
                          left_on=when, right_on="fy", by="sire_n", direction="backward", tolerance=3)
        s[col] = np.log(m.set_index("index")["fee"]).reindex(s.index)
    s["fee_known"] = s["log_fee"].notna().astype(int)
    s["fee_change"] = s["log_fee_now"] - s["log_fee"]  # did the sire get hotter since this mating?
    return s.reset_index(drop=True)


def add_family_effects(s: pd.DataFrame) -> pd.DataFrame:
    """Premiums learned from earlier sale years only. `rel` = price vs that sale's sold average."""
    sold = s["rna"] == 0
    s["rel"] = np.where(sold, s["log_price"] - s[sold].groupby(["sale", "sale_year", "age"])["log_price"]
                        .transform("mean").reindex(s.index), np.nan)
    level = s[sold].groupby(["sale", "sale_year", "age"], as_index=False)["log_price"].mean()
    level["sale_year"] += 1
    s = s.merge(level.rename(columns={"log_price": "sale_level"}), on=["sale", "sale_year", "age"], how="left")
    rna_rate = s.groupby(["sale", "sale_year"], as_index=False)["rna"].mean()
    rna_rate["sale_year"] += 1
    s = s.merge(rna_rate.rename(columns={"rna": "sale_rna_rate_last"}), on=["sale", "sale_year"], how="left")
    # forecast of this sale's price change vs last year (market_growth.py, earlier years only)
    growth = MG.add_forecast(MG.growth_table(s))
    s = s.merge(growth[["sale", "sale_year", "age", "growth_fc"]], on=["sale", "sale_year", "age"], how="left")

    s["dam"] = np.where(s["damsire_n"] != "", s["dam_n"] + "|" + s["damsire_n"], None)
    s["sire"] = s["sire_n"].replace("", None)
    s["damsire"] = s["damsire_n"].replace("", None)
    s["nick"] = np.where((s["sire_n"] != "") & (s["damsire_n"] != ""), s["sire_n"] + "x" + s["damsire_n"], None)
    for key, k in SHRINK.items():
        per = s.dropna(subset=[key, "rel"]).groupby([key, "sale_year"])["rel"].agg(["sum", "count"]).reset_index()
        per = per.sort_values("sale_year")
        g = per.groupby(key)
        per[f"{key}_cnt"] = g["count"].cumsum() - per["count"]
        per[f"{key}_eff"] = (g["sum"].cumsum() - per["sum"]) / (per[f"{key}_cnt"] + k)  # shrunk toward 0
        # most recent earlier year alone: is this family hot right now?
        per[f"{key}_last"] = g["sum"].shift(1) / g["count"].shift(1)
        per[f"{key}_last_gap"] = per["sale_year"] - g["sale_year"].shift(1)
        s = s.merge(per[[key, "sale_year", f"{key}_cnt", f"{key}_eff", f"{key}_last", f"{key}_last_gap"]],
                    on=[key, "sale_year"], how="left")
        s[f"{key}_cnt"] = s[f"{key}_cnt"].fillna(0)
        s[f"{key}_eff"] = s[f"{key}_eff"].fillna(0.0)
    s = s.merge(F.sire_graded_winners_by_year(), on=["sire_n", "sale_year"], how="left")
    s["sire_log_gsw"] = np.log1p(s["sire_gsw"].fillna(0))
    s["weanling"] = (s["age"] == 0).astype(int)
    s["two_yo"] = (s["age"] == 2).astype(int)
    s["log_sire_n"] = np.log1p(s["sire_cnt"])
    s["log_dam_n"] = np.log1p(s["dam_cnt"])
    s["log_damsire_n"] = np.log1p(s["damsire_cnt"])
    s["log_nick_n"] = np.log1p(s["nick_cnt"])
    return s[s["sale_year"].between(2005, 2025) & s["sale_level"].notna()].reset_index(drop=True)


GB_X = ["log_fee", "fee_known", "fee_change", "filly", "weanling", "two_yo", "sale_level", "sale_rna_rate_last",
        "sire_eff", "log_sire_n", "sire_last", "sire_last_gap", "dam_eff", "log_dam_n", "damsire_eff",
        "log_damsire_n", "nick_eff", "log_nick_n", "sire_log_gsw", "growth_fc"]
LIN_X = ["log_fee0", "fee_known", "fee_change0", "filly", "weanling", "two_yo", "sale_level", "sale_rna_rate0",
         "sire_eff", "log_sire_n", "dam_eff", "log_dam_n", "damsire_eff", "log_damsire_n", "nick_eff",
         "log_nick_n", "sire_log_gsw", "growth_fc0"]


def linear_frame(d: pd.DataFrame) -> np.ndarray:
    d = d.assign(log_fee0=d["log_fee"].fillna(0.0), fee_change0=d["fee_change"].fillna(0.0),
                 sale_rna_rate0=d["sale_rna_rate_last"].fillna(d["sale_rna_rate_last"].median()),
                 growth_fc0=d["growth_fc"].fillna(0.0))
    return np.column_stack([np.ones(len(d)), d[LIN_X].to_numpy(float)])


# ------------------------------------------------------------------ censored linear (Tobit)
def fit_tobit(X: np.ndarray, y: np.ndarray, upper: np.ndarray):
    """Normal model of log price. Sold rows: exact. RNA rows: true value < y (upper bound)."""
    beta0, *_ = np.linalg.lstsq(X[~upper], y[~upper], rcond=None)
    sig0 = np.std(y[~upper] - X[~upper] @ beta0)

    def nll(theta):
        b, ls = theta[:-1], theta[-1]
        sig = np.exp(ls)
        mu = X @ b
        z = (y - mu) / sig
        ll = np.where(upper, stats.norm.logcdf(z), stats.norm.logpdf(z) - ls)
        return -ll.sum()

    def grad(theta):
        b, ls = theta[:-1], theta[-1]
        sig = np.exp(ls)
        z = (y - X @ b) / sig
        lam = np.exp(stats.norm.logpdf(z) - stats.norm.logcdf(z))  # inverse Mills ratio
        dz = np.where(upper, lam, z)            # d ll / d z
        g_b = -(X.T @ (dz * (-1 / sig)))
        g_ls = -np.sum(np.where(upper, lam * (-z), z * z - 1))
        return np.append(g_b, g_ls)

    res = optimize.minimize(nll, np.append(beta0, math.log(sig0)), jac=grad, method="L-BFGS-B",
                            options={"maxiter": 2000})
    return res.x[:-1], float(np.exp(res.x[-1]))


# ------------------------------------------------------------------ censored boosting
def hgb(**kw):
    return HistGradientBoostingRegressor(max_iter=3000, early_stopping=True, validation_fraction=0.1,
                                         n_iter_no_change=60, random_state=42, **kw)


def fit_censored_gb(d: pd.DataFrame, params: dict, rounds: int = 3):
    """Boosting can't read 'below this price' directly, so RNA targets are replaced by their expected
    value under the current model, E[y | y < RNA price], and the model is refitted (3 rounds)."""
    y = d["log_price"].to_numpy().copy()
    up = d["rna"].to_numpy() == 1
    cap = y.copy()
    y[up] = cap[up] - 0.5
    for _ in range(rounds):
        m = hgb(**params).fit(d[GB_X], y)
        mu = m.predict(d[GB_X])
        sig = float(np.std((cap - mu)[~up]))
        a = (cap[up] - mu[up]) / sig
        y[up] = mu[up] - sig * np.exp(stats.norm.logpdf(a) - stats.norm.logcdf(a))
    return m, sig


def score(y, p, lo=None, hi=None) -> dict:
    err = y - p
    out = {"R2": float(1 - (err ** 2).sum() / ((y - y.mean()) ** 2).sum()), "MAE_log": float(err.abs().mean()),
           "typical_miss_x": float(np.exp(err.abs().median())), "bias": float(err.mean())}
    if lo is not None:
        out["coverage_80"] = float(((y >= lo) & (y <= hi)).mean())
    return out


def main() -> None:
    s = add_family_effects(auction_table())
    tr, va, te = (s[s["sale_year"].between(*p)] for p in (TRAIN, VALID, TEST))
    print(f"lots (sold + RNA): train {len(tr):,}  tune {len(va):,}  test {len(te):,};  "
          f"RNA share {s['rna'].mean():.1%};  ages: " + ", ".join(f"{a}:{n:,}" for a, n in s['age'].value_counts().sort_index().items()))
    va_sold, te_sold = va[va["rna"] == 0], te[te["rna"] == 0]
    growth_report = MG.report(MG.add_forecast(MG.growth_table(auction_table())), TEST)
    print(f"market-growth forecast, held-out sales 2021-2025: R2 {growth_report['R2_vs_average_change']:.3f} "
          f"(vs always forecasting the average change)")
    print(pd.DataFrame(growth_report["by_year"]).T.to_string())

    # Tobit on train, then the 'if it sells' shift from sold tune lots
    b, sig = fit_tobit(linear_frame(tr), tr["log_price"].to_numpy(), tr["rna"].to_numpy() == 1)
    tob_va = linear_frame(va_sold) @ b
    tob_shift = float((va_sold["log_price"] - tob_va).mean())
    print(f"Tobit: sigma {sig:.3f}; sold lots sit {tob_shift:+.3f} log above the censored fit")

    # boosting: wider search on the tune years (sold-only fits for speed), then censored refit
    best = None
    grid = list(itertools.product([0.03, 0.06], [15, 31, 63], [50, 200, 800], [0.0, 3.0]))
    tr_sold = tr[tr["rna"] == 0]
    for lr, leaves, mleaf, l2 in grid:
        p = dict(learning_rate=lr, max_leaf_nodes=leaves, min_samples_leaf=mleaf, l2_regularization=l2)
        r2 = score(va_sold["log_price"], hgb(**p).fit(tr_sold[GB_X], tr_sold["log_price"]).predict(va_sold[GB_X]))["R2"]
        if best is None or r2 > best[0]:
            best = (r2, p)
    params = best[1]
    print(f"boosting: best of {len(grid)} settings on 2019-2020 (R2 {best[0]:.3f}): {params}")
    gb, gsig = fit_censored_gb(tr, params)
    gb_va = gb.predict(va_sold[GB_X])
    gb_shift = float((va_sold["log_price"] - gb_va).mean())

    # blend weight chosen on the tune years
    yv = va_sold["log_price"]
    ws = np.linspace(0, 1, 21)
    w = max(ws, key=lambda w: score(yv, w * (gb_va + gb_shift) + (1 - w) * (tob_va + tob_shift))["R2"])
    print(f"blend: {w:.2f} x boosting + {1 - w:.2f} x Tobit (chosen on 2019-2020)")

    # refit both on train + tune, keep the shifts and weight, then the 80% range (conformal on tune years)
    trva = pd.concat([tr, va])
    b2, _ = fit_tobit(linear_frame(trva), trva["log_price"].to_numpy(), trva["rna"].to_numpy() == 1)
    gb2, _ = fit_censored_gb(trva, params)

    def blend(d, tob_b=b2, gbm=gb2):
        return w * (gbm.predict(d[GB_X]) + gb_shift) + (1 - w) * (linear_frame(d) @ tob_b + tob_shift)

    sold_trva = trva[trva["rna"] == 0]
    q10 = hgb(loss="quantile", quantile=0.10, **params).fit(tr_sold[GB_X], tr_sold["log_price"])
    q90 = hgb(loss="quantile", quantile=0.90, **params).fit(tr_sold[GB_X], tr_sold["log_price"])
    cq = np.maximum(q10.predict(va_sold[GB_X]) - yv, yv - q90.predict(va_sold[GB_X]))
    widen = float(np.quantile(cq, 0.80 * (1 + 1 / len(cq))))

    results = {
        "no-model guess": score(te_sold["log_price"], pd.Series(tr_sold["log_price"].mean(), index=te_sold.index)),
        "Tobit (censored linear)": score(te_sold["log_price"], linear_frame(te_sold) @ b2 + tob_shift),
        "censored boosting": score(te_sold["log_price"], gb2.predict(te_sold[GB_X]) + gb_shift),
        "blend (saved)": score(te_sold["log_price"], blend(te_sold), q10.predict(te_sold[GB_X]) - widen,
                               q90.predict(te_sold[GB_X]) + widen),
    }
    print("\nHeld-out 2021-2025, sold lots, all ages:")
    print(pd.DataFrame(results).T.round(3).to_string())

    t = te_sold.assign(pred=blend(te_sold))
    print("\nBlend by age:")
    print(t.groupby("age").apply(lambda d: pd.Series({"lots": len(d), **score(d["log_price"], d["pred"])}),
                                 include_groups=False).round(3).to_string())
    rn = te[te["rna"] == 1]
    below = float((blend(rn) < rn["log_price"]).mean())
    print(f"\nUnsold test lots predicted below their RNA price (should be most): {below:.1%} of {len(rn):,}")

    # like-for-like against the current final model: the same sold yearlings, 2021-2025
    old = joblib.load(F.ART / "price_model.joblib")
    pt = F.price_table()
    pt = pt[pt["sale_year"].between(*TEST)]
    key = ["sale", "sale_year", "hip", "name"]
    old_x = pt[key + F.PRICE_FEATURES].rename(columns={f: "old_" + f for f in F.PRICE_FEATURES})
    yl = t[t["age"] == 1].merge(old_x, on=key, how="inner").drop_duplicates(key)
    old_pred = old["models"]["mid"].predict(
        yl[["old_" + f for f in F.PRICE_FEATURES]].set_axis(F.PRICE_FEATURES, axis=1))
    cmp = {"current final model (yearlings only, trained to 2025)": score(yl["log_price"], pd.Series(old_pred, index=yl.index)),
           "step-up blend (trained to 2020)": score(yl["log_price"], yl["pred"])}
    print(f"\nSame {len(yl):,} sold yearlings, 2021-2025:")
    print(pd.DataFrame(cmp).T.round(3).to_string())
    print("(the current model was refit on 2005-2025, so it has already seen these lots; "
          "its score here is optimistic, which makes this a tough comparison for the step-up)")

    # final refit on every year, saved for use
    allyrs = s
    b3, _ = fit_tobit(linear_frame(allyrs), allyrs["log_price"].to_numpy(), allyrs["rna"].to_numpy() == 1)
    gb3, _ = fit_censored_gb(allyrs, params)
    sold_all = allyrs[allyrs["rna"] == 0]
    joblib.dump({"tobit_beta": b3, "gb": gb3, "weight": w, "gb_shift": gb_shift, "tobit_shift": tob_shift,
                 "q10": hgb(loss="quantile", quantile=0.10, **params).fit(sold_all[GB_X], sold_all["log_price"]),
                 "q90": hgb(loss="quantile", quantile=0.90, **params).fit(sold_all[GB_X], sold_all["log_price"]),
                 "widen": widen, "growth_beta": MG.fit(MG.growth_table(allyrs)), "growth_features": MG.GROWTH_X,
                 "gb_features": GB_X, "linear_features": LIN_X, "params": params,
                 "trained_on": "weanlings, yearlings, 2-year-olds 2005-2025, sold + RNA (upper bound)"},
                F.ART / "price_stepup.joblib")
    (F.ART / "price_stepup_results.json").write_text(json.dumps(
        {"test_all_ages": results, "growth_forecast": growth_report, "same_yearlings": cmp, "rna_below_share": below, "weight": w,
         "params": params}, indent=2, default=float))
    print("\nsaved artifacts/price_stepup.joblib")


if __name__ == "__main__":
    main()

"""Final breeding-value models: gradient boosting vs linear regression.

    ../.venv/bin/python train_ebv.py

Stallions: lifetime AEI (log) from what was known before his foals raced. Only 259 sires, so
scores are averaged over 5-fold cross-validation repeated 10 times.
Mares: her next foal's log(price / fee) from her earlier foals and her own record.
Train 2005-2019, tune 2020, test 2021-2025, as for price.
Saves artifacts/stallion_ebv.joblib and artifacts/mare_ebv.joblib.
"""
import itertools
import json
import sys

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import LinearRegression
from sklearn.model_selection import RepeatedKFold

import features as F

sys.path.insert(0, str(F.HERE.parent / "model_walkthrough"))
import fit_ebv_mare  # noqa: E402
import fit_ebv_stallion  # noqa: E402

STALLION_X = ["first_crops_log_price", "first_crops_n", "log_earnings", "record_known", "graded_winner",
              "log_sire_aei", "sire_aei_known", "year_c"]
MARE_X = ["prev_mean", "log_prev_n", "log_fee", "log_earnings", "record_known", "graded_winner",
          "log_sire_aei", "sire_aei_known"]


def r2(y, p):
    return float(1 - ((y - p) ** 2).sum() / ((y - y.mean()) ** 2).sum())


def stallions() -> dict:
    d = fit_ebv_stallion.build().dropna(subset=["first_crops_log_price"]).reset_index(drop=True)
    X, y = d[STALLION_X], d["log_aei"]
    grid = list(itertools.product([0.02, 0.05], [4, 8], [10, 25]))
    cv = RepeatedKFold(n_splits=5, n_repeats=10, random_state=42)
    scores = {"baseline": [], "linear regression": []}
    gb_scores = {g: [] for g in grid}
    for tr, te in cv.split(X):
        scores["baseline"].append(r2(y.iloc[te], np.full(len(te), y.iloc[tr].mean())))
        scores["linear regression"].append(r2(y.iloc[te], LinearRegression().fit(X.iloc[tr], y.iloc[tr]).predict(X.iloc[te])))
        for lr, leaves, mleaf in grid:
            m = HistGradientBoostingRegressor(learning_rate=lr, max_leaf_nodes=leaves, min_samples_leaf=mleaf,
                                              max_iter=300, random_state=42).fit(X.iloc[tr], y.iloc[tr])
            gb_scores[(lr, leaves, mleaf)].append(r2(y.iloc[te], m.predict(X.iloc[te])))
    best = max(gb_scores, key=lambda g: np.mean(gb_scores[g]))
    scores["gradient boosting (best settings)"] = gb_scores[best]
    print(f"Stallions: {len(d)} sires, 5-fold CV x10 (R2 on the held-out fold)")
    for k, v in scores.items():
        print(f"  {k:<34} mean R2 {np.mean(v):+.3f}  (sd {np.std(v):.3f})")
    print(f"  best boosting settings: learning_rate={best[0]}, max_leaf_nodes={best[1]}, min_samples_leaf={best[2]}")
    # the note about the gradient-boosting search: the best of 8 settings is chosen on the same folds,
    # so its score is slightly optimistic.
    winner = "gradient boosting" if np.mean(gb_scores[best]) > np.mean(scores["linear regression"]) else "linear regression"
    if winner == "gradient boosting":
        model = HistGradientBoostingRegressor(learning_rate=best[0], max_leaf_nodes=best[1], min_samples_leaf=best[2],
                                              max_iter=300, random_state=42).fit(X, y)
    else:
        model = LinearRegression().fit(X, y)
    print(f"  saved model: {winner} (higher cross-validated R2)")
    joblib.dump({"model": model, "features": STALLION_X, "kind": winner, "target": "log lifetime AEI",
                 "median_aei": float(np.exp(y.median()))}, F.ART / "stallion_ebv.joblib")
    return {k: round(float(np.mean(v)), 4) for k, v in scores.items()} | {"saved": winner, "n": len(d)}


def mares() -> dict:
    d = fit_ebv_mare.build()
    tr, va, te = (d[d["sale_year"].between(*p)] for p in ((2005, 2019), (2020, 2020), (2021, 2025)))
    trva = pd.concat([tr, va])
    out = {"baseline": r2(te["added"], np.full(len(te), tr["added"].mean())),
           "linear regression": r2(te["added"], LinearRegression().fit(trva[MARE_X], trva["added"]).predict(te[MARE_X]))}
    best = None
    for lr, leaves, mleaf in itertools.product([0.02, 0.05], [7, 15], [25, 100]):
        m = HistGradientBoostingRegressor(learning_rate=lr, max_leaf_nodes=leaves, min_samples_leaf=mleaf,
                                          max_iter=500, random_state=42).fit(tr[MARE_X], tr["added"])
        s = r2(va["added"], m.predict(va[MARE_X]))
        if best is None or s > best[0]:
            best = (s, dict(learning_rate=lr, max_leaf_nodes=leaves, min_samples_leaf=mleaf))
    gb = HistGradientBoostingRegressor(max_iter=500, random_state=42, **best[1]).fit(trva[MARE_X], trva["added"])
    out["gradient boosting"] = r2(te["added"], gb.predict(te[MARE_X]))
    print(f"\nMares: train {len(tr):,} foals, tune {len(va):,}, test {len(te):,} (sale years 2021-2025)")
    for k, v in out.items():
        print(f"  {k:<34} test R2 {v:+.3f}")
    print(f"  best boosting settings (chosen on 2020): {best[1]}")
    winner = "gradient boosting" if out["gradient boosting"] > out["linear regression"] else "linear regression"
    chosen = gb if winner == "gradient boosting" else LinearRegression().fit(trva[MARE_X], trva["added"])
    t = te.assign(pred=chosen.predict(te[MARE_X]))
    t["band"] = pd.qcut(t["pred"], 5, labels=["lowest 20%", "2nd", "3rd", "4th", "highest 20%"])
    print(f"  held-out foals by the {winner} model's prediction (median price / fee):")
    print(t.groupby("band", observed=True).apply(lambda g: pd.Series({"foals": len(g), "median_price_over_fee":
          float(np.median(g["price"] / g["fee_conception"]))}), include_groups=False).round(2).to_string())
    alld = d[d["sale_year"].between(2005, 2025)]
    model = (HistGradientBoostingRegressor(max_iter=500, random_state=42, **best[1]) if winner == "gradient boosting"
             else LinearRegression()).fit(alld[MARE_X], alld["added"])
    print(f"  saved model: {winner} (higher held-out R2)")
    joblib.dump({"model": model, "features": MARE_X, "kind": winner, "target": "log(foal price / stud fee)"},
                F.ART / "mare_ebv.joblib")
    return {k: round(float(v), 4) for k, v in out.items()} | {"saved": winner}


if __name__ == "__main__":
    res = {"stallion": stallions(), "mare": mares()}
    (F.ART / "ebv_results.json").write_text(json.dumps(res, indent=2))

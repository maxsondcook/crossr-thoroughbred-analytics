"""Final sale-price model: gradient boosting vs linear regression, tested on later sale years.

    ../.venv/bin/python train_price.py

Train 2005-2019, tune on 2020, test 2021-2025; then refit the chosen settings on 2005-2025
and save artifacts/price_*.joblib. Target: log hammer price of sold yearlings.
"""
import itertools
import json

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import LinearRegression

import features as F

TRAIN, VALID, TEST = (2005, 2019), (2020, 2020), (2021, 2025)
X_COLS = F.PRICE_FEATURES


def metrics(y, pred, lo=None, hi=None) -> dict:
    err = y - pred
    out = {"R2": 1 - (err ** 2).sum() / ((y - y.mean()) ** 2).sum(), "MAE_log": err.abs().mean(),
           "typical_miss_x": float(np.exp(err.abs().median()))}
    if lo is not None:
        out["coverage_80"] = float(((y >= lo) & (y <= hi)).mean())
    return out


def linear_X(d: pd.DataFrame) -> pd.DataFrame:
    """Linear regression can't take blanks: blanks become 0 and the *_known / *_n columns carry them."""
    return d[X_COLS].fillna(0.0)


def hgb(**kw) -> HistGradientBoostingRegressor:
    return HistGradientBoostingRegressor(max_iter=2000, early_stopping=True, validation_fraction=0.1,
                                         n_iter_no_change=50, random_state=42, **kw)


def main() -> None:
    t = F.price_table()
    tr, va, te = (t[t["sale_year"].between(*p)] for p in (TRAIN, VALID, TEST))
    print(f"lots: train {len(tr):,}  tune {len(va):,}  test {len(te):,}")

    results = {}
    base = tr["log_price"].mean()
    results["baseline (train mean)"] = metrics(te["log_price"], pd.Series(base, index=te.index))

    lin = LinearRegression().fit(linear_X(tr), tr["log_price"])
    results["linear regression"] = metrics(te["log_price"], lin.predict(linear_X(te)))

    # tune gradient boosting on the 2020 sales (never on the test years)
    grid = list(itertools.product([0.03, 0.06], [15, 31, 63], [50, 200], [0.0, 1.0]))
    best = None
    for lr, leaves, min_leaf, l2 in grid:
        m = hgb(learning_rate=lr, max_leaf_nodes=leaves, min_samples_leaf=min_leaf, l2_regularization=l2)
        m.fit(tr[X_COLS], tr["log_price"])
        r2 = metrics(va["log_price"], m.predict(va[X_COLS]))["R2"]
        if best is None or r2 > best[0]:
            best = (r2, dict(learning_rate=lr, max_leaf_nodes=leaves, min_samples_leaf=min_leaf, l2_regularization=l2))
    params = best[1]
    print(f"best settings on 2020 (R2 {best[0]:.3f}): {params}")

    # 80% range: quantile models fitted on 2005-2019, then widened by the amount that makes them hold
    # 80% of the 2020 prices (conformalized quantile regression). The test years never touch this.
    q10 = hgb(loss="quantile", quantile=0.10, **params).fit(tr[X_COLS], tr["log_price"])
    q90 = hgb(loss="quantile", quantile=0.90, **params).fit(tr[X_COLS], tr["log_price"])
    score = np.maximum(q10.predict(va[X_COLS]) - va["log_price"], va["log_price"] - q90.predict(va[X_COLS]))
    widen = float(np.quantile(score, min(1.0, 0.80 * (1 + 1 / len(va)))))
    print(f"range widened by {widen:+.3f} log on each side to hold 80% of 2020 prices")

    trva = pd.concat([tr, va])
    gb = hgb(**params).fit(trva[X_COLS], trva["log_price"])
    results["gradient boosting"] = metrics(te["log_price"], gb.predict(te[X_COLS]),
                                           q10.predict(te[X_COLS]) - widen, q90.predict(te[X_COLS]) + widen)
    # the linear model's 80% range for comparison: +/- 1.2816 residual SDs
    sd = np.std(trva["log_price"] - LinearRegression().fit(linear_X(trva), trva["log_price"]).predict(linear_X(trva)))
    lin2 = LinearRegression().fit(linear_X(trva), trva["log_price"])
    p = lin2.predict(linear_X(te))
    results["linear regression"] = metrics(te["log_price"], p, p - 1.2816 * sd, p + 1.2816 * sd)

    print("\nHeld-out sale years 2021-2025:")
    print(pd.DataFrame(results).T.round(3).to_string())

    # by year and by price band, gradient boosting
    te = te.assign(pred=gb.predict(te[X_COLS]))
    by_year = te.groupby("sale_year").apply(lambda d: pd.Series(metrics(d["log_price"], d["pred"])), include_groups=False)
    print("\nGradient boosting by test year:\n" + by_year.round(3).to_string())
    te["fee_band"] = np.where(te["fee_known"] == 0, "fee unknown",
                              pd.cut(np.exp(te["log_fee"]), [0, 10_000, 30_000, 100_000, 1e9],
                                     labels=["< $10k", "$10-30k", "$30-100k", "$100k+"]).astype(str))
    by_band = te.groupby("fee_band").apply(lambda d: pd.Series({"lots": len(d), **metrics(d["log_price"], d["pred"])}),
                                           include_groups=False)
    print("\nGradient boosting by sire-fee band:\n" + by_band.round(3).to_string())

    from sklearn.inspection import permutation_importance
    sample = te.sample(min(5000, len(te)), random_state=42)
    imp = permutation_importance(gb, sample[X_COLS], sample["log_price"], n_repeats=5, random_state=42, scoring="r2")
    order = np.argsort(-imp.importances_mean)
    print("\nWhat the model leans on (drop in test R2 when a column is shuffled):")
    for i in order:
        print(f"  {X_COLS[i]:<16} {imp.importances_mean[i]:.3f}")

    # final refit on every year through 2025 for use on new sales
    allyrs = t[t["sale_year"].between(2005, 2025)]
    final = {name: hgb(**({"loss": "quantile", "quantile": q} if q else {}), **params).fit(allyrs[X_COLS], allyrs["log_price"])
             for name, q in (("mid", None), ("p10", 0.10), ("p90", 0.90))}
    joblib.dump({"models": final, "features": X_COLS, "params": params, "widen": widen,
                 "trained_on": "sold yearlings 2005-2025"},
                F.ART / "price_model.joblib")
    (F.ART / "price_results.json").write_text(json.dumps(
        {"test": {k: {m: round(float(v), 4) for m, v in r.items()} for k, r in results.items()},
         "params": params, "n": {"train": len(tr), "tune": len(va), "test": len(te)}}, indent=2))
    print("\nsaved artifacts/price_model.joblib")


if __name__ == "__main__":
    main()

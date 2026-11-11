"""
Evaluation for sale-price models (master prompt Phases 13 and 15.3-15.5). Method: docs/price-model.md.

All metrics are on log(price) unless named *_usd. Validation is by time: models are
fit on older sales and scored on later ones (TimeSplit). Training-time only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd


@dataclass
class TimeSplit:
    """train: sale_year <= train_end; valid: (train_end, valid_end] (early stopping);
    calib: (valid_end, calib_end] (interval calibration); test: > calib_end."""

    train_end: int = 2018
    valid_end: int = 2020
    calib_end: int = 2022
    test_end: int = 2025

    def masks(self, years: pd.Series) -> Dict[str, pd.Series]:
        return {
            "train": years <= self.train_end,
            "valid": (years > self.train_end) & (years <= self.valid_end),
            "calib": (years > self.valid_end) & (years <= self.calib_end),
            "test": (years > self.calib_end) & (years <= self.test_end),
        }


# ------------------------------------------------------------------ metrics
def fit_metrics(y: np.ndarray, pred: np.ndarray, n_features: Optional[int] = None) -> dict:
    y, pred = np.asarray(y, float), np.asarray(pred, float)
    ok = ~np.isnan(pred)
    y, pred = y[ok], pred[ok]
    e = y - pred
    n = len(y)
    ss_res, ss_tot = float(np.sum(e ** 2)), float(np.sum((y - y.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    out = {
        "n": n,
        "r2": r2,
        "mae": float(np.mean(np.abs(e))),
        "rmse": float(math.sqrt(ss_res / n)) if n else float("nan"),
        "medae": float(np.median(np.abs(e))),
        "bias": float(np.mean(e)),
        "residual_sd": float(np.std(e, ddof=1)) if n > 1 else float("nan"),
        "mae_usd": float(np.mean(np.abs(np.exp(y) - np.exp(pred)))),
        "medae_usd": float(np.median(np.abs(np.exp(y) - np.exp(pred)))),
        "median_abs_pct": float(np.median(np.abs(np.exp(pred - y) - 1))),
    }
    if n_features is not None and n - n_features - 1 > 0:
        out["adj_r2"] = 1 - (1 - r2) * (n - 1) / (n - n_features - 1)
    return out


# ------------------------------------------------------------------ baselines (prior years only)
def _prior_stat(history: pd.DataFrame, test: pd.DataFrame, keys: List[str], how: str, years_back: int) -> pd.Series:
    """For each test sale in year Y: `how` of sold log prices with the same keys in years Y-years_back..Y-1."""
    out = pd.Series(np.nan, index=test.index, dtype=float)
    for y, t in test.groupby("sale_year"):
        h = history[(history.sale_year < y) & (history.sale_year >= y - years_back)]
        stat = h.groupby(keys).lp.agg(how)
        idx = pd.MultiIndex.from_frame(t[keys]) if len(keys) > 1 else t[keys[0]]
        out.loc[t.index] = stat.reindex(idx).values
    return out


def baselines(history: pd.DataFrame, test: pd.DataFrame) -> Dict[str, pd.Series]:
    """Phase 15.3 baselines for each test sale from sales before its year (category median as fallback)."""
    cat_med = _prior_stat(history, test, ["sale_key", "kind"], "median", 1)
    cat_med = cat_med.fillna(_prior_stat(history, test, ["kind"], "median", 1))
    out = {"sale_category_median": cat_med}
    out["sire_year_mean"] = _prior_stat(history, test, ["sire_k", "kind"], "mean", 1).fillna(cat_med)
    out["sire_year_median"] = _prior_stat(history, test, ["sire_k", "kind"], "median", 1).fillna(cat_med)
    comp = _prior_stat(history, test, ["sire_k", "sale_key", "kind"], "median", 2)
    out["comparable_sales"] = comp.fillna(_prior_stat(history, test, ["sire_k", "kind"], "median", 2)).fillna(cat_med)
    return out


# ------------------------------------------------------------------ models
def ridge_fit(X: pd.DataFrame, y: np.ndarray, alpha: float = 1.0) -> dict:
    """Standardized ridge with median imputation + missing indicators (closed form)."""
    med = X.median()
    Z, cols = _ridge_design(X, med)
    mu, sd = Z.mean(0), Z.std(0)
    sd[sd == 0] = 1.0
    Zs = (Z - mu) / sd
    A = Zs.T @ Zs + alpha * np.eye(Zs.shape[1])
    beta = np.linalg.solve(A, Zs.T @ (y - y.mean()))
    return {"med": med, "mu": mu, "sd": sd, "beta": beta, "b0": float(y.mean()), "cols": cols}


def _ridge_design(X: pd.DataFrame, med: pd.Series):
    miss = X.isna()
    cols = list(X.columns) + [f"{c}__missing" for c in X.columns if miss[c].any()]
    Z = np.column_stack([X.fillna(med).fillna(0).values] + [miss[c].values.astype(float)[:, None]
                                                            for c in X.columns if miss[c].any()])
    return Z, cols


def ridge_predict(m: dict, X: pd.DataFrame) -> np.ndarray:
    miss = X.isna()
    base = X.fillna(m["med"]).fillna(0).values
    extra = [miss[c[:-9]].values.astype(float)[:, None] for c in m["cols"] if c.endswith("__missing")]
    Z = np.column_stack([base] + extra) if extra else base
    return m["b0"] + ((Z - m["mu"]) / m["sd"]) @ m["beta"]


def lgbm_fit(X_tr, y_tr, X_va, y_va, params: Optional[dict] = None, objective: str = "regression", alpha: float = 0.5):
    import lightgbm as lgb

    p = {"objective": objective, "learning_rate": 0.05, "num_leaves": 63, "min_data_in_leaf": 100,
         "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 1.0, "verbose": -1,
         "seed": 7, "deterministic": True}
    if objective == "quantile":
        p["alpha"] = alpha
    p.update(params or {})
    dtr = lgb.Dataset(X_tr, y_tr)
    dva = lgb.Dataset(X_va, y_va, reference=dtr)
    return lgb.train(p, dtr, num_boost_round=3000, valid_sets=[dva],
                     callbacks=[lgb.early_stopping(100, verbose=False)])


# ------------------------------------------------------------------ intervals
def conformal_scale_groups(df: pd.DataFrame) -> pd.Series:
    """Segments whose errors differ (Phase 13.5): category x sire record x dam record x fee known."""
    sire = pd.cut(df.sire_log_n, [-1, 0.5, 3, 99], labels=["sire_new", "sire_few", "sire_many"]).astype(str)
    dam = np.where(df.dam_log_n > 0, "dam_sold_before", "dam_unproven")
    fee = np.where(df.log_sire_fee_conception.notna(), "fee", "nofee")
    return df.kind.astype(str) + "|" + sire + "|" + dam + "|" + fee


def conformal_intervals(resid_cal: np.ndarray, groups_cal: pd.Series, groups_test: pd.Series,
                        pred_test: np.ndarray, level: float = 0.8, min_n: int = 200) -> tuple:
    """Group-normalized split conformal: |resid| / group scale calibrated on held-out sales.

    Groups with fewer than min_n calibration sales use the overall scale. Coverage holds
    when calibration and test sales are exchangeable; checked on the test years.
    """
    a = np.abs(resid_cal)
    overall = float(np.median(a))
    g = pd.Series(a, index=groups_cal.index).groupby(groups_cal.values)
    scale = g.median().where(g.size() >= min_n)
    s_cal = groups_cal.map(scale).fillna(overall).values
    q = float(np.quantile(a / s_cal, min(1.0, level * (1 + 1 / len(a)))))
    s_test = groups_test.map(scale).fillna(overall).values
    return pred_test - q * s_test, pred_test + q * s_test, {"q": q, "scales": scale.dropna().to_dict(), "overall": overall}


def interval_metrics(y, lo, hi) -> dict:
    y, lo, hi = map(lambda v: np.asarray(v, float), (y, lo, hi))
    return {"coverage": float(np.mean((y >= lo) & (y <= hi))),
            "mean_width_log": float(np.mean(hi - lo)),
            "median_width_ratio": float(np.median(np.exp(hi - lo)))}


def cqr_intervals(q_lo_cal, q_hi_cal, y_cal, q_lo_test, q_hi_test, level: float = 0.8) -> tuple:
    """Conformalized quantile regression: widen (or shrink) the quantile band by the
    calibration-set quantile of max(lo - y, y - hi), so coverage holds on held-out sales."""
    score = np.maximum(np.asarray(q_lo_cal) - y_cal, np.asarray(y_cal) - q_hi_cal)
    q = float(np.quantile(score, min(1.0, level * (1 + 1 / len(score)))))
    return np.asarray(q_lo_test) - q, np.asarray(q_hi_test) + q, {"adjustment": q}


def interaction_search(X_tr: pd.DataFrame, y_tr, X_te: pd.DataFrame, y_te, base_cols: List[str],
                       pairs: Iterable[tuple], alpha: float = 10.0) -> pd.DataFrame:
    """Add one product term at a time to the ridge model; held-out R2 gain and the term's t-stat (train)."""
    base = ridge_fit(X_tr[base_cols], y_tr, alpha)
    r2_0 = fit_metrics(y_te, ridge_predict(base, X_te[base_cols]))["r2"]
    rows = []
    for a, b in pairs:
        name = f"{a} x {b}"
        tr = X_tr[base_cols].assign(**{name: X_tr[a] * X_tr[b]})
        te = X_te[base_cols].assign(**{name: X_te[a] * X_te[b]})
        m = ridge_fit(tr, y_tr, alpha)
        r2 = fit_metrics(y_te, ridge_predict(m, te))["r2"]
        # OLS t-stat of the product on complete rows (residualized on the base fit)
        ok = tr[name].notna().values
        resid = y_tr[ok] - ridge_predict(base, X_tr[base_cols][ok])
        x = tr[name].values[ok]
        x = x - x.mean()
        beta = float(x @ resid / (x @ x)) if (x @ x) > 0 else float("nan")
        se = float(np.sqrt(np.sum((resid - beta * x) ** 2) / (len(x) - 2) / (x @ x))) if (x @ x) > 0 else float("nan")
        rows.append({"interaction": name, "rows with both": int(ok.sum()), "held-out r2 gain": r2 - r2_0,
                     "t (train)": beta / se if se else float("nan")})
    return pd.DataFrame(rows).sort_values("held-out r2 gain", ascending=False)


# ------------------------------------------------------------------ segments
def price_band(lp: pd.Series) -> pd.Series:
    edges = [0, math.log(10_000), math.log(50_000), math.log(150_000), math.log(500_000), 99]
    return pd.cut(lp, edges, labels=["<$10k", "$10-50k", "$50-150k", "$150-500k", "$500k+"]).astype(str)


def segment_report(df: pd.DataFrame, pred: np.ndarray, lo=None, hi=None, by: str = "sale_year",
                   min_n: int = 100) -> pd.DataFrame:
    d = df.assign(_pred=pred)
    if lo is not None:
        d = d.assign(_lo=lo, _hi=hi)
    rows = []
    for key, g in d.groupby(by):
        if len(g) < min_n:
            continue
        m = fit_metrics(g.lp.values, g._pred.values)
        row = {by: key, "n": m["n"], "r2": m["r2"], "mae": m["mae"], "bias": m["bias"],
               "median_abs_pct": m["median_abs_pct"]}
        if lo is not None:
            row.update(interval_metrics(g.lp, g._lo, g._hi))
        rows.append(row)
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ explainability
def shap_by_feature(model, X: pd.DataFrame, groups: Dict[str, List[str]], sample: int = 5000, seed: int = 1):
    """Mean |SHAP| per feature and per feature group (LightGBM's exact tree SHAP)."""
    Xs = X.sample(min(sample, len(X)), random_state=seed)
    contrib = model.predict(Xs, pred_contrib=True)[:, :-1]
    per = pd.Series(np.abs(contrib).mean(0), index=X.columns).sort_values(ascending=False)
    per_group = {g: float(per.reindex([c for c in cols if c in per.index]).sum()) for g, cols in groups.items()}
    return per, pd.Series(per_group).sort_values(ascending=False)


def comparable_sales(history: pd.DataFrame, row: pd.Series, k: int = 5) -> pd.DataFrame:
    """Earlier sales by the same sire at the same kind of sale, closest in dam record and year."""
    h = history[(history.sire_k == row.sire_k) & (history.kind == row.kind) & (history.sale_year < row.sale_year)]
    if h.empty:
        return h
    d = (h.dam_rel - row.dam_rel).abs().fillna(1) + (row.sale_year - h.sale_year) * 0.1
    return h.assign(_d=d).nsmallest(k, "_d")[["sale_year", "catalog_title", "hip", "dam", "price", "_d"]]

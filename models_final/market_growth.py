"""How much will a sale's prices rise or fall this year? A small forecasting model whose answer
feeds the step-up price model (stepup_price.py).

Unit: one sale, one year, one age (weanling / yearling / 2-year-old).
Target: the change in that sale's average log price of sold lots versus the same sale last year.
Inputs, all known before the sale opens:
* so_far        - how much the sales held EARLIER THIS YEAR changed vs their own last year
                  (month from the sale's name, see SALE_MONTH; same-month sales don't count);
* has_so_far    - 1 when at least one earlier sale this year exists;
* last_market   - the whole market's change last year (prices tend to bounce back);
* catalog_fee   - change in the catalog's average sire fee vs last year (a stronger or
                  weaker catalog, known when the catalog is published);
* catalog_size  - change in the number of lots catalogued (log).
Fitted as a weighted linear regression on EARLIER years only (expanding window), so the
forecast for year Y never sees year Y.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# (keyword in the sale name, month). First match wins, so the specific ones come first.
# Months are the usual sale months; where a name has no month it's the sale's normal slot.
SALE_MONTH = [
    ("SARATOGA FALL", 10), ("JANUARY", 1), ("HORSES OF ALL AGES SALE", 1), ("WINTER", 2),
    ("CALDER", 3), ("GULFSTREAM", 3), ("FLORIDA SALE", 3), ("SANTA ANITA", 4), ("APRIL", 4),
    ("TEXAS TWO YEAR OLDS", 4), ("MAY", 5), ("MIDLANTIC TWO YEAR OLDS", 5), ("JUNE", 6),
    ("JULY", 7), ("KENTUCKY SELECTED YEARLINGS", 7), ("SARATOGA", 8), ("NEW YORK", 8),
    ("SUMMER", 8), ("SEPTEMBER", 9), ("SHOWCASE", 9), ("KEENELAND YEARLING SALE", 9),
    ("OCTOBER", 10), ("FALL", 10), ("NOVEMBER", 11), ("BREEDING STOCK", 11), ("DECEMBER", 12),
]
GROWTH_X = ["so_far", "has_so_far", "last_market", "catalog_fee", "catalog_size"]


def sale_month(name: str) -> float:
    if "DISPERSAL" in name:
        return np.nan
    for key, month in SALE_MONTH:
        if key in name:
            return month
    return np.nan  # one-off sales: no month, so they get no 'so far' signal


def growth_table(s: pd.DataFrame) -> pd.DataFrame:
    """One row per (sale, sale_year, age) with the target change and the inputs.
    s: the auction table (sold and RNA lots) with sale, sale_year, age, rna, log_price, log_fee."""
    sold = s[s["rna"] == 0]
    g = (sold.groupby(["sale", "sale_year", "age"])["log_price"].agg(level="mean", sold_n="count")
         .join(s.groupby(["sale", "sale_year", "age"]).agg(cat_n=("rna", "size"), cat_fee=("log_fee", "mean")))
         .reset_index())
    g["month"] = g["sale"].map(sale_month)
    prev = g[["sale", "sale_year", "age", "level", "sold_n", "cat_n", "cat_fee"]].assign(sale_year=g["sale_year"] + 1)
    g = g.merge(prev, on=["sale", "sale_year", "age"], how="left", suffixes=("", "_prev"))
    g["change"] = g["level"] - g["level_prev"]
    g["w"] = np.minimum(g["sold_n"], g["sold_n_prev"])
    g["catalog_fee"] = (g["cat_fee"] - g["cat_fee_prev"]).fillna(0.0)
    g["catalog_size"] = np.log(g["cat_n"] / g["cat_n_prev"])

    # whole market's change by year (all ages, weighted), then last year's value
    has = g.dropna(subset=["change"])
    mkt = (has.assign(wc=has["change"] * has["w"]).groupby("sale_year")[["wc", "w"]].sum()
           .pipe(lambda d: d["wc"] / d["w"]))
    g["last_market"] = g["sale_year"].map(lambda y: mkt.get(y - 1, np.nan)).fillna(0.0)

    # sales held earlier this calendar year (strictly earlier month), all ages
    so_far = []
    for (y, m) in zip(g["sale_year"], g["month"]):
        e = has[(has["sale_year"] == y) & (has["month"] < m)] if m == m else has.iloc[0:0]
        so_far.append(np.average(e["change"], weights=e["w"]) if len(e) else np.nan)
    g["so_far"] = so_far
    g["has_so_far"] = g["so_far"].notna().astype(int)
    g["so_far"] = g["so_far"].fillna(0.0)
    return g


def fit(rows: pd.DataFrame) -> np.ndarray:
    """Weighted least squares on rows with a known change. Returns [intercept, *GROWTH_X]."""
    r = rows.dropna(subset=["change"]).dropna(subset=["catalog_size"])
    X = np.column_stack([np.ones(len(r)), r[GROWTH_X].to_numpy(float)])
    sw = np.sqrt(r["w"].to_numpy(float))
    beta, *_ = np.linalg.lstsq(X * sw[:, None], r["change"].to_numpy() * sw, rcond=None)
    return beta


def predict(beta: np.ndarray, rows: pd.DataFrame) -> np.ndarray:
    X = np.column_stack([np.ones(len(rows)), rows[GROWTH_X].fillna(0.0).to_numpy(float)])
    return X @ beta


def add_forecast(g: pd.DataFrame, first_year: int = 2006, min_years: int = 4) -> pd.DataFrame:
    """growth_fc for each row: forecast from a model fitted on years before that row's year only."""
    g = g.copy()
    g["growth_fc"] = np.nan
    years = sorted(g["sale_year"].unique())
    for y in years:
        past = g[g["sale_year"] < y]
        if past["sale_year"].nunique() < min_years or y < first_year:
            continue
        idx = g["sale_year"] == y
        g.loc[idx, "growth_fc"] = predict(fit(past), g[idx])
    return g


def report(g: pd.DataFrame, years=(2021, 2025)) -> dict:
    """How well the forecast tracks the real change in held-out years (weighted by lots)."""
    t = g[g["sale_year"].between(*years)].dropna(subset=["change", "growth_fc"])
    w = t["w"]
    err = t["change"] - t["growth_fc"]
    base = t["change"] - np.average(g[g["sale_year"] < years[0]].dropna(subset=["change"])["change"],
                                    weights=g[g["sale_year"] < years[0]].dropna(subset=["change"])["w"])
    by_year = t.groupby("sale_year").apply(lambda d: pd.Series({
        "actual": np.average(d["change"], weights=d["w"]), "forecast": np.average(d["growth_fc"], weights=d["w"])}),
        include_groups=False)
    return {"R2_vs_average_change": float(1 - np.average(err ** 2, weights=w) / np.average(base ** 2, weights=w)),
            "by_year": by_year.round(3).to_dict(orient="index")}

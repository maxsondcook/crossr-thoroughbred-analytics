"""Feature tables for the final models. Every feature is known before the moment it predicts.

Builds on model_walkthrough/common.py (loaders, name matching) and reads the local data/ snapshot.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "model_walkthrough"))
import common as c  # noqa: E402

ART = HERE / "artifacts"
ART.mkdir(exist_ok=True)


def _earlier_mean(y: pd.DataFrame, key: str, name: str) -> pd.DataFrame:
    """Mean of `rel` for the same key in strictly earlier sale years, and how many lots that was."""
    per = y.groupby([key, "sale_year"])["rel"].agg(["sum", "count"]).reset_index().sort_values("sale_year")
    g = per.groupby(key)
    per[f"{name}_n"] = g["count"].cumsum() - per["count"]
    per[name] = (g["sum"].cumsum() - per["sum"]) / per[f"{name}_n"].where(per[f"{name}_n"] > 0)
    return y.merge(per[[key, "sale_year", name, f"{name}_n"]], on=[key, "sale_year"], how="left")


def sire_graded_winners_by_year() -> pd.DataFrame:
    """For each sire and year Y: his distinct progeny with a graded win dated before 1 January of Y."""
    rh = pd.read_csv(c.DATA / "race_horses.csv", low_memory=False, usecols=["id", "sire", "status"])
    rh = rh[rh["status"] == "ok"]
    rh["id"] = rh["id"].astype(str)
    rh["sire_n"] = rh["sire"].map(c.norm)
    st = pd.read_csv(c.DATA / "race_starts.csv", low_memory=False, usecols=["horse_id", "race_date", "place", "grade"])
    st = st[(st["place"].astype(str) == "1") & pd.to_numeric(st["grade"], errors="coerce").between(1, 3)]
    st["horse_id"] = st["horse_id"].astype(str)
    st["year"] = pd.to_datetime(st["race_date"], errors="coerce").dt.year
    first = st.groupby("horse_id")["year"].min().rename("first_gw_year").reset_index()
    first = first.merge(rh[["id", "sire_n"]], left_on="horse_id", right_on="id")
    per = first.groupby(["sire_n", "first_gw_year"]).size().rename("new").reset_index()
    rows = []
    for sire, g in per.groupby("sire_n"):
        g = g.sort_values("first_gw_year")
        years = range(int(g["first_gw_year"].min()) + 1, 2027)
        cum = 0
        counts = dict(zip(g["first_gw_year"], g["new"]))
        for yr in years:
            cum += counts.get(yr - 1, 0)
            rows.append((sire, yr, cum))
    return pd.DataFrame(rows, columns=["sire_n", "sale_year", "sire_gsw"])


PRICE_FEATURES = ["log_fee", "fee_known", "filly", "sale_level", "dam_prev", "dam_prev_n",
                  "damsire_prev", "damsire_prev_n", "sire_prev", "sire_prev_n", "sire_log_gsw"]


def price_table() -> pd.DataFrame:
    """One row per sold yearling, 2005-2025, with features from earlier years only."""
    y = c.yearlings()
    y = y[y["sale_year"].between(2003, 2025)].copy()
    y["fee_known"] = y["log_fee"].notna().astype(int)  # missing fee kept; the model learns what it means
    y["rel"] = y["log_price"] - y.groupby(["sale", "sale_year"])["log_price"].transform("mean")
    level = y.groupby(["sale", "sale_year"], as_index=False)["log_price"].mean()
    level["sale_year"] += 1
    y = y.merge(level.rename(columns={"log_price": "sale_level"}), on=["sale", "sale_year"], how="left")
    y["dam_key"] = np.where(y["damsire_n"] != "", y["dam_n"] + "|" + y["damsire_n"], np.nan)
    y = _earlier_mean(y, "dam_key", "dam_prev")
    y["damsire_key"] = y["damsire_n"].replace("", np.nan)
    y = _earlier_mean(y, "damsire_key", "damsire_prev")
    y = _earlier_mean(y, "sire_n", "sire_prev")
    for col in ("dam_prev_n", "damsire_prev_n", "sire_prev_n"):
        y[col] = y[col].fillna(0)
    y = y.merge(sire_graded_winners_by_year(), on=["sire_n", "sale_year"], how="left")
    y["sire_log_gsw"] = np.log1p(y["sire_gsw"].fillna(0))
    y = y[y["sale_year"].between(2005, 2025) & y["sale_level"].notna()]
    return y.reset_index(drop=True)


if __name__ == "__main__":
    t = price_table()
    print(t.shape)
    print(t[PRICE_FEATURES].describe().T[["count", "mean", "min", "max"]].to_string())

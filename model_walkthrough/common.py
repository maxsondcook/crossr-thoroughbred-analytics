"""Shared loaders for the model walkthrough (see WALKTHROUGH.md).

Reads the local data snapshot in ../data/ (not on GitHub). Every horse is matched on
name plus foaling year, or name plus sire, never on name alone.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parents[1] / "data"
OUT = Path(__file__).resolve().parent / "out"
OUT.mkdir(exist_ok=True)
pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 50)
pd.set_option("display.float_format", lambda v: f"{v:,.4f}")

_PAREN = re.compile(r"\(.*?\)")
_NONWORD = re.compile(r"[^A-Z0-9 ]")


def norm(name) -> str:
    """'Storm Cat (USA)' -> 'STORM CAT'; "Hartwell's Gift" -> 'HARTWELLS GIFT'."""
    if not isinstance(name, str):
        return ""
    s = _PAREN.sub("", name).upper().replace("'", "").replace("’", "").replace(".", "")
    return " ".join(_NONWORD.sub(" ", s).split())


def money(v) -> float:
    """'$25,000' -> 25000.0; 'Private', 'N/A', blanks -> NaN."""
    s = re.sub(r"[^0-9.]", "", str(v))
    try:
        x = float(s)
    except ValueError:
        return np.nan
    return x if x > 0 else np.nan


def stud_fees() -> pd.DataFrame:
    """One fee per (stallion, year), in USD."""
    f = pd.read_csv(DATA / "stud_fees.csv")
    f["stallion_n"] = f["Stallion"].map(norm)
    f["year"] = pd.to_numeric(f["Year"], errors="coerce")
    f["fee"] = f["Stud Fee"].map(money)
    f = f.dropna(subset=["year", "fee"])
    return f.groupby(["stallion_n", "year"], as_index=False)["fee"].max()


def yearlings() -> pd.DataFrame:
    """Every yearling sold at auction (no RNAs, no outs), one row per sale."""
    s = pd.read_csv(DATA / "sales_lots.csv", low_memory=False)
    s["sale_year"] = pd.to_numeric(s["catalog_year"], errors="coerce")
    s["birth_year"] = pd.to_numeric(s["birth_year"], errors="coerce")
    s["price"] = pd.to_numeric(s["price_num"], errors="coerce")
    sold = (s["sale_year"] - s["birth_year"] == 1) & (s["price"] > 0)
    sold &= s["is_rna"].astype(str).isin(["0", "False"]) & s["is_out"].astype(str).isin(["0", "False"])
    y = s[sold].copy()
    y = y.drop_duplicates(subset=["source_file", "hip", "name_key"])
    y["sire_n"] = y["sire"].map(norm)
    y["dam_n"] = y["dam"].map(norm)
    y["damsire_n"] = y["damsire"].map(norm)
    y["filly"] = (y["sex_letter"].astype(str).str.upper() == "F").astype(int)
    y["sale"] = y["catalog_title"].astype(str).str.replace(r"\s*\(?\d{4}\)?\s*", " ", regex=True).str.strip().str.upper()
    y["log_price"] = np.log(y["price"])
    # The sire's fee in the conception year; if none, his latest fee from up to 3 years earlier
    # (flagged fee_carried). Only earlier fees are used, so nothing from after the mating leaks in.
    fees = stud_fees().rename(columns={"stallion_n": "sire_n", "year": "fee_year", "fee": "fee_conception"})
    y["conception_year"] = y["birth_year"] - 1
    y = y.dropna(subset=["conception_year"]).sort_values("conception_year")
    fees = fees.sort_values("fee_year")
    y = pd.merge_asof(y, fees, left_on="conception_year", right_on="fee_year", by="sire_n",
                      direction="backward", tolerance=3)
    y["fee_carried"] = (y["fee_year"] < y["conception_year"]).astype(int)
    y["log_fee"] = np.log(y["fee_conception"])
    keep = ["sale_year", "sale", "hip", "name", "sire_n", "dam_n", "damsire_n", "filly", "birth_year",
            "price", "log_price", "fee_conception", "fee_carried", "log_fee"]
    return y[keep].reset_index(drop=True)


def lifetime_aei() -> pd.DataFrame:
    """Each stallion's lifetime AEI from the leading-sires-by-AEI list, by list year."""
    r = pd.read_csv(DATA / "stallion_rankings.csv", low_memory=False)
    r = r[r["list"] == "Lifetime AEI"].copy()
    r["aei"] = pd.to_numeric(r["aei"], errors="coerce")
    r["foals"] = pd.to_numeric(r["foals"], errors="coerce")
    r["foaling_year"] = pd.to_numeric(r["foaling_year"], errors="coerce")
    r["stallion_n"] = r["stallion"].map(norm)
    r["sire_n"] = r["sire"].map(norm)
    return r.dropna(subset=["aei", "foaling_year"])[["year", "stallion_n", "foaling_year", "sire_n", "aei", "foals"]]


def aei_as_of(aei: pd.DataFrame) -> pd.DataFrame:
    """Best-known AEI of each stallion by name up to each list year (for 'as of the sale' joins)."""
    a = aei.sort_values("year")
    return a.groupby(["stallion_n", "year"], as_index=False)["aei"].last()


def runners() -> pd.DataFrame:
    """Horses with an Equibase race record: career earnings, starts, graded wins."""
    rh = pd.read_csv(DATA / "race_horses.csv", low_memory=False)
    rh = rh[rh["status"] == "ok"].copy()
    rh["name_n"] = rh["display_name"].fillna(rh["name_key"]).map(norm)
    rh["sire_n"] = rh["sire"].map(norm)
    rh["foal_year"] = pd.to_numeric(rh["foal_year"], errors="coerce")
    rs = pd.read_csv(DATA / "race_seasons.csv", low_memory=False)
    career = rs[rs["year"].astype(str) == "Career"][["horse_id", "starts", "firsts", "earnings"]]
    career = career.rename(columns={"horse_id": "id"})
    career["id"] = career["id"].astype(str)
    rh["id"] = rh["id"].astype(str)
    st = pd.read_csv(DATA / "race_starts.csv", low_memory=False, usecols=["horse_id", "place", "grade"])
    st["g"] = pd.to_numeric(st["grade"], errors="coerce")
    gw = st[(st["place"].astype(str) == "1") & st["g"].between(1, 3)].groupby("horse_id").size()
    out = rh.merge(career, on="id", how="left")
    out["graded_wins"] = out["id"].map(gw.rename(index=str)).fillna(0)
    for c in ("starts", "firsts", "earnings"):
        out[c] = pd.to_numeric(out[c], errors="coerce")
    return out[["name_n", "foal_year", "sire_n", "sex", "starts", "firsts", "earnings", "graded_wins"]]


def show_fit(model, title: str) -> None:
    """The lines the PIP labs ask for: each coefficient, p-value, R-squared, adjusted R-squared."""
    print(f"\n--- {title} ---")
    print(model.summary())
    print("\nCoefficients (p-value):")
    for k in model.params.index:
        print(f"  {k:<22} {model.params[k]:>+10.4f}   p = {model.pvalues[k]:.4f}")
    print(f"  R-squared {model.rsquared:.3f}   adjusted R-squared {model.rsquared_adj:.3f}   n = {int(model.nobs):,}")
    print(f"  typical error (residual SD) {np.sqrt(model.mse_resid):.3f}")

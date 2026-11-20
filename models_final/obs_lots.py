"""OBS sale results (data/obs_undertack/, downloaded once from obssales.com on 2026-11-18)
converted to the sales_lots.csv layout, so they run through the same pipeline as the
Keeneland and Fasig-Tipton lots. Every OBS sale is included (2YO, yearling, winter mixed);
the pipeline keeps ages 0-2. Breeze fields ride along (ut_*) for the 2YO breeze model.
"""
from __future__ import annotations

import glob
import json

import numpy as np
import pandas as pd

import features as F

OBS = F.HERE.parent / "data" / "obs_undertack"
SKIP = {121}  # "Test 2019": a test catalog, not a sale


def as_sales_lots() -> pd.DataFrame:
    meta = pd.DataFrame(json.loads((OBS / "sales_1_160.json").read_text()))
    meta["sale_id"] = meta["sale_id"].astype(int)
    d = pd.concat([pd.read_csv(f, dtype=str) for f in glob.glob(str(OBS / "sale_*.csv"))])
    d["sale_id"] = d["sale_id"].astype(int)
    d = d[~d["sale_id"].isin(SKIP)].merge(meta[["sale_id", "sale_name", "sale_starts"]], on="sale_id")
    year = pd.to_datetime(d["sale_starts"]).dt.year
    # "2024 March 2YO in Training Sale" -> "OBS MARCH 2YO IN TRAINING SALE (2024)"; the pipeline strips the year
    title = ("OBS " + d["sale_name"].str.replace(r"\b(19|20)\d{2}\b", "", regex=True)
             .str.replace(r"^OBS\s+", "", regex=True).str.replace(r"\s+", " ", regex=True).str.strip()
             .str.replace("2YOs", "2YO", regex=False).str.replace(" Live", "", regex=False)
             .str.replace(" Sale", "", regex=False).replace({"March": "March 2YO in Training"})
             + " (" + year.astype(str) + ")")
    hp, rp = pd.to_numeric(d["hammer_price"], errors="coerce"), pd.to_numeric(d["sale_price_rna"], errors="coerce")
    rna = d["rna_summary_indicator"].eq("Y")
    price = np.where(rna, rp.where(rp > 0, hp.abs()), hp)
    out = pd.DataFrame({
        "catalog_year": year, "catalog_title": title, "source_file": "obs_sale_" + d["sale_id"].astype(str),
        "hip": pd.to_numeric(d["hip_number"], errors="coerce").astype("Int64").astype(str), "name": d["horse_name"], "sex": d["sex"],
        "sex_letter": d["sex"].replace({"C": "C", "F": "F", "G": "G", "M": "F", "H": "C", "R": "C"}),
        "sire": d["sire_name"], "dam": d["dam_name"], "damsire": d["dam_sire"], "color": d["color"],
        "consignor": d["consignor_name"], "purchaser": d["buyer_name"], "barn": np.nan,
        "foaled_location": d["foaling_area"].str.strip(), "birth_year": pd.to_numeric(d["foaling_year"], errors="coerce"),
        "price_raw": d["hammer_price"], "price_num": price, "is_rna": rna.astype(int),
        "is_out": d["in_out_status"].ne("I").astype(int), "is_fasig": 0,
        "obs_session": pd.to_numeric(d["session_number"], errors="coerce"),
        "ut_time": d["ut_time"], "ut_distance": d["ut_distance"], "ut_actual_date": d["ut_actual_date"],
    })
    out["name_key"] = out["name"].fillna("") + "|" + out["hip"].astype(str)
    return out.reset_index(drop=True)


if __name__ == "__main__":
    o = as_sales_lots()
    print(o.groupby(["catalog_title"]).size().to_string())

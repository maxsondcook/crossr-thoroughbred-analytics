"""How much of a horse's sale price does pedigree explain, compared with where the sale company
and seller put it (sale, hip, consignor), and what's left for physical, vet and luck?

    ../.venv/bin/python decompose_price.py        # needs artifacts/v4_frame_obs.pkl (rolling_price.py --obs)

Same setup as the final price model (rolling_price.py --obs --hl 8): censored LightGBM retrained
before every year on all earlier years (half-life 8), plus the bias shift from the previous three
years. Only the inputs change. Scored on sold Keeneland + Fasig-Tipton lots, 2021-2025 (OBS lots
are in training, as in the final model). The within-sale and sire updates are left out, so the
"all inputs" row sits a little below the final model's 0.608.

Input groups (every input is known before the sale):
- basics: age, sex, foaling date, state bred, and the market forecast for the year;
- pedigree: sire (stud fee, his foals' prices, graded winners, crops), dam (her foals' prices,
  produce and race record, age), damsire and sire x damsire cross;
- placement: which sale and its price level, hip position in the catalog, consignor. Sales
  companies place horses after inspecting them, so this mixes pedigree with a first look at
  the horse's physique;
- earlier sale: the horse's own price at an earlier sale (resold horses only).
"""
from __future__ import annotations

import json
import time

import joblib
import numpy as np
import pandas as pd

import features as F
import rolling_price as R
import stepup_price as S

BASICS = ["filly", "weanling", "two_yo", "foal_doy", "dob_known", "kentucky_bred", "state_known", "state_cat",
          "growth_fc"]
PEDIGREE = ["log_fee", "fee_known", "fee_change", "sire_eff", "log_sire_n", "sire_last", "sire_last_gap",
            "sire_log_gsw_ifha", "sire_log_g1w_ifha", "sire_crop", "first_crop", "early_crops",
            "dam_eff", "log_dam_n", "dam_bt", "dam_gsw", "dam_g12w", "dam_g1w", "dam_mg1w", "dam_produce_class",
            "dam_race_class", "dam_age", "dam_produce_years", "dam_age_known",
            "damsire_eff", "log_damsire_n", "nick_eff", "log_nick_n"]
PLACEMENT = ["sale_level", "sale_rna_rate_last", "sale_cat", "hip_pct", "cons_eff", "log_cons_n", "consignor_known",
             "consignor_cat"]
EARLIER = ["prev_rel", "prev_log_price", "prev_rna", "has_prev"]

SETS = {
    "basics": BASICS,
    "basics + pedigree": BASICS + PEDIGREE,
    "basics + placement": BASICS + PLACEMENT,
    "basics + pedigree + placement": BASICS + PEDIGREE + PLACEMENT,
    "all inputs": BASICS + PEDIGREE + PLACEMENT + EARLIER,
}
HALF_LIFE = 8
YEARS = range(2018, 2026)  # 2018-2020 only feed the bias shift


def run(s: pd.DataFrame, cols: list[str], params: dict, n_per_row: float) -> pd.DataFrame:
    R.GB_X = cols  # one_year and fit_censored read the module's input list
    p = pd.concat([R.one_year(s, y, HALF_LIFE, params, n_per_row, ranges=False) for y in YEARS])
    p["shift"] = 0.0
    for y in YEARS:
        prev = p[p["sale_year"].between(y - 3, y - 1)]
        if len(prev):
            p.loc[p["sale_year"] == y, "shift"] = float((prev["log_price"] - prev["raw"]).mean())
    p["pred"] = p["raw"] + p["shift"]
    return p[(p["sale_year"] >= 2021) & (p["is_obs"] == 0)]


def within_session_r2(te: pd.DataFrame) -> float:
    """R2 after removing each session's average price and average prediction: how well the inputs
    separate horses that sold in the same session of the same sale."""
    g = te.groupby(["source_file", "session"], dropna=False)
    y = te["log_price"] - g["log_price"].transform("mean")
    p = te["pred"] - g["pred"].transform("mean")
    return float(1 - ((y - p) ** 2).sum() / (y ** 2).sum())


def shares(res: dict) -> dict:
    """Two-group split of the explained variation. Basics are in both groups' models, so their
    small share sits in the overlap. (A basics-only model isn't used as a baseline: with so few
    inputs the censored fit misses by +0.33 on average and scores below zero.)"""
    r = {k: v["R2"] for k, v in res.items()}
    ped, place, both = r["basics + pedigree"], r["basics + placement"], r["basics + pedigree + placement"]
    return {
        "pedigree only (not carried by placement)": both - place,
        "shared by pedigree and placement (incl. age, sex, foaling date, market)": ped + place - both,
        "placement only (sale, hip, consignor beyond pedigree)": both - ped,
        "earlier sale of the same horse": r["all inputs"] - both,
        "unexplained (physical, vet, luck, and anything else)": 1 - r["all inputs"],
    }


def main() -> None:
    every = set(sum(SETS.values(), []))
    missing = set(R.GB_X) - every
    assert not missing, f"inputs not in any group: {missing}"
    s = R.frame(obs=True)
    params = joblib.load(F.ART / "price_v3.joblib")["params"]
    n_per_row = 262 / len(s[s["sale_year"].between(*S.TRAIN)])
    res = {}
    for name, cols in SETS.items():
        t = time.time()
        te = run(s, cols, params, n_per_row)
        sc = S.score(te["log_price"], te["pred"])
        sc["within_session_R2"] = within_session_r2(te)
        sc["by_age"] = {int(a): S.score(g["log_price"], g["pred"])["R2"] for a, g in te.groupby("age")}
        sc["lots"] = len(te)
        res[name] = sc
        print(f"{name:<32} lots {len(te):,}  R2 {sc['R2']:.3f}  within-session R2 {sc['within_session_R2']:.3f}  "
              f"miss x{sc['typical_miss_x']:.2f}  by age {', '.join(f'{v:.3f}' for v in sc['by_age'].values())}  "
              f"({time.time() - t:.0f}s)", flush=True)

    split = shares(res)
    print("\nShare of price variation (log price, sold lots 2021-2025):")
    for k, v in split.items():
        print(f"  {k:<72} {v:6.1%}")
    out = {"scores": res, "split": split, "half_life": HALF_LIFE, "groups": {k: v for k, v in SETS.items()}}
    (F.ART / "price_decomposition.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()

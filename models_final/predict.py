"""Use the saved models on a new horse. Run train_price.py and train_ebv.py first.

    ../.venv/bin/python predict.py        # prints three worked examples

All inputs are things known before the sale or mating; blanks (None) are allowed where the
model was trained with missing values.
"""
from __future__ import annotations

import math

import joblib
import numpy as np
import pandas as pd

import features as F

_cache: dict = {}


def _load(name: str):
    if name not in _cache:
        _cache[name] = joblib.load(F.ART / f"{name}.joblib")
    return _cache[name]


def sale_price(*, sire_fee=None, filly=False, sale_avg_last_year, dam_prev=None, dam_prev_n=0,
               damsire_prev=None, damsire_prev_n=0, sire_prev=None, sire_prev_n=0,
               sire_graded_winners=0) -> dict:
    """Expected hammer price of a yearling, with an 80% range.

    sale_avg_last_year: the sale's geometric-mean yearling price last year ($).
    *_prev: how that relative's earlier yearlings sold vs their sale, on the log scale
            (0 = average, +0.69 = twice the sale average); None when there were none.
    """
    art = _load("price_model")
    row = pd.DataFrame([{
        "log_fee": math.log(sire_fee) if sire_fee else np.nan, "fee_known": int(bool(sire_fee)),
        "filly": int(filly), "sale_level": math.log(sale_avg_last_year),
        "dam_prev": dam_prev, "dam_prev_n": dam_prev_n, "damsire_prev": damsire_prev,
        "damsire_prev_n": damsire_prev_n, "sire_prev": sire_prev, "sire_prev_n": sire_prev_n,
        "sire_log_gsw": math.log1p(sire_graded_winners)}])[art["features"]].astype(float)
    m = art["models"]
    mid = float(m["mid"].predict(row)[0])
    lo = float(m["p10"].predict(row)[0]) - art["widen"]
    hi = float(m["p90"].predict(row)[0]) + art["widen"]
    return {"price": math.exp(mid), "low_80": math.exp(min(lo, mid)), "high_80": math.exp(max(hi, mid))}


def stallion_aei(*, first_crops_avg_price, first_crops_n, earnings=None, graded_winner=False,
                 sire_aei=None, foaling_year) -> float:
    """Expected lifetime AEI (1.00 = average runner) of a sire whose first two crops have sold."""
    art = _load("stallion_ebv")
    row = pd.DataFrame([{
        "first_crops_log_price": math.log(first_crops_avg_price), "first_crops_n": first_crops_n,
        "log_earnings": math.log1p(earnings) if earnings is not None else 0.0, "record_known": int(earnings is not None),
        "graded_winner": int(graded_winner), "log_sire_aei": math.log(sire_aei) if sire_aei else 0.0,
        "sire_aei_known": int(bool(sire_aei)), "year_c": foaling_year - 2000}])[art["features"]]
    return float(math.exp(art["model"].predict(row)[0]))


def mare_value(*, stud_fee, earlier_foals_price_over_fee, earlier_foals_n, earnings=None,
               graded_winner=False, sire_aei=None) -> dict:
    """Her breeding value for one mating: the foal's typical price as a multiple of the fee, and in $.

    earlier_foals_price_over_fee: geometric mean of (price / fee) for her yearlings sold so far.
    """
    art = _load("mare_ebv")
    row = pd.DataFrame([{
        "prev_mean": math.log(earlier_foals_price_over_fee), "log_prev_n": math.log(earlier_foals_n),
        "log_fee": math.log(stud_fee), "log_earnings": math.log1p(earnings) if earnings is not None else 0.0,
        "record_known": int(earnings is not None), "graded_winner": int(graded_winner),
        "log_sire_aei": math.log(sire_aei) if sire_aei else 0.0, "sire_aei_known": int(bool(sire_aei))}])[art["features"]]
    mult = math.exp(float(art["model"].predict(row)[0]))
    return {"price_over_fee": mult, "typical_foal_price": stud_fee * mult}


if __name__ == "__main__":
    p = sale_price(sire_fee=50_000, sale_avg_last_year=100_000, dam_prev=0.3, dam_prev_n=2,
                   sire_prev=0.2, sire_prev_n=120, sire_graded_winners=8)
    print("Colt by a $50k sire (8 graded winners, earlier yearlings 22% above their sales), dam's foals 35% above, "
          f"at a sale that averaged $100k: ${p['price']:,.0f} (80% range ${p['low_80']:,.0f} to ${p['high_80']:,.0f})")
    q = sale_price(sire_fee=None, sale_avg_last_year=30_000, filly=True)
    print(f"Filly by a sire with no fee on file, first foal of her dam, at a $30k sale: ${q['price']:,.0f} "
          f"(80% range ${q['low_80']:,.0f} to ${q['high_80']:,.0f})")
    a = stallion_aei(first_crops_avg_price=120_000, first_crops_n=90, earnings=1_500_000, graded_winner=True,
                     sire_aei=2.0, foaling_year=2018)
    print(f"Young sire (first crops averaged $120k, earned $1.5M, graded winner, sire's AEI 2.0): AEI {a:.2f}")
    v = mare_value(stud_fee=40_000, earlier_foals_price_over_fee=3.0, earlier_foals_n=2)
    print(f"Mare whose 2 yearlings sold for 3x the fee, bred to a $40k sire: foal typically {v['price_over_fee']:.2f}x "
          f"the fee = ${v['typical_foal_price']:,.0f}")


def sale_price_stepup(lot: dict) -> dict:
    """Step-up model (stepup_price.py): any age 0-2, with family effects. `lot` holds the
    columns in stepup_price.GB_X and LIN_X (build them with stepup_price.add_family_effects)."""
    import stepup_price as S

    art = _load("price_stepup")
    d = pd.DataFrame([lot])
    gb =float(art["gb"].predict(d[art["gb_features"]])[0]) + art["gb_shift"]
    d["sale_rna_rate_last"] = d["sale_rna_rate_last"].fillna(0.11)  # typical share of unsold lots
    lin = float((S.linear_frame(d) @ art["tobit_beta"])[0]) + art["tobit_shift"]
    mid = art["weight"] * gb + (1 - art["weight"]) * lin
    lo = float(art["q10"].predict(d[art["gb_features"]])[0]) - art["widen"]
    hi = float(art["q90"].predict(d[art["gb_features"]])[0]) + art["widen"]
    return {"price": math.exp(mid), "low_80": math.exp(min(lo, mid)), "high_80": math.exp(max(hi, mid))}


def market_growth(*, earlier_sales_change=None, last_year_market_change=0.0, catalog_fee_change=0.0,
                  catalog_size_change=0.0) -> float:
    """Forecast change in a sale's average log price vs last year (market_growth.py).

    earlier_sales_change: how much this year's earlier sales moved vs their last year (log);
                          None when none have been held yet.
    last_year_market_change: the whole market's move last year (log).
    catalog_fee_change: change in the catalog's average log sire fee vs last year.
    catalog_size_change: log(lots catalogued this year / last year).
    Pass the result as lot["growth_fc"] to sale_price_stepup.
    """
    import market_growth as MG

    art = _load("price_stepup")
    row = pd.DataFrame([{"so_far": earlier_sales_change or 0.0, "has_so_far": int(earlier_sales_change is not None),
                         "last_market": last_year_market_change, "catalog_fee": catalog_fee_change,
                         "catalog_size": catalog_size_change}])
    return float(MG.predict(art["growth_beta"], row)[0])


def sale_price_v3(lot: dict) -> dict:
    """v3 model (v3_price.py, LightGBM): any age 0-2, with hip position, consignor, earlier sale,
    black-type class (IFHA), dam age, sire's crop and state bred. Build `lot` with
    v3_price.add_v3_inputs(stepup_price.add_family_effects(stepup_price.auction_table()))."""
    import v3_price as V

    art = _load("price_v3")
    d = pd.DataFrame([lot])
    for col, cats in art["categories"].items():  # unseen consignors/states/sales fall back to OTHER
        v = d[col].astype(str).where(d[col].astype(str).isin(cats), "OTHER")
        d[col] = pd.Categorical(v, categories=cats)
    d["sale_rna_rate_last"] = d["sale_rna_rate_last"].fillna(0.11)
    x = d[art["features"]]
    gb = float(art["gb"].predict(x)[0]) + art["gb_shift"]
    lin = float((V.linear_frame(d) @ art["tobit_beta"])[0]) + art["tobit_shift"]
    mid = art["weight"] * gb + (1 - art["weight"]) * lin
    t = int(np.digitize(d["sale_level"].iloc[0], art["tier_cuts"]))  # cheap / middle / dear sale
    lo = float(art["q10"].predict(x)[0]) - art["widen_low_by_tier"][t]
    hi = float(art["q90"].predict(x)[0]) + art["widen_high_by_tier"][t]
    return {"price": math.exp(mid), "low_80": math.exp(min(lo, mid)), "high_80": math.exp(max(hi, mid))}


def sale_price_rolling(lot: dict, *, earlier_sessions_miss: float | None = None, earlier_sessions_n: int = 0,
                       sire_earlier_miss: float | None = None, sire_earlier_n: int = 0, with_obs: bool = True) -> dict:
    """Yearly-retrained model (rolling_price.py): v3's inputs plus foaling date, LightGBM weighted
    toward recent years, a bias shift from the last three years' out-of-time misses, and two
    optional updates from lots already sold this year. Build `lot` with
    rolling_price.frame(obs=True) (v3 inputs + raw-file consignor and foaling date).
    Retrain after each year's last sale.

    earlier_sessions_miss / _n: average (log price - prediction) over the lots already sold in
        this sale's earlier sessions, and how many.
    sire_earlier_miss / _n: the same for this sire's lots sold earlier this year (earlier-month
        sales, or earlier sessions of this sale), measured after the within-sale update.
    """
    art = _load("price_rolling_obs" if with_obs and (F.ART / "price_rolling_obs.joblib").exists() else "price_rolling")
    adj = 0.0
    if earlier_sessions_miss is not None and art.get("within_sale_k") is not None:
        adj += earlier_sessions_miss * earlier_sessions_n / (earlier_sessions_n + art["within_sale_k"])
    if sire_earlier_miss is not None and art.get("sire_k") is not None:
        adj += sire_earlier_miss * sire_earlier_n / (sire_earlier_n + art["sire_k"])
    d = pd.DataFrame([lot])
    for col, cats in art["categories"].items():
        v = d[col].astype(str).where(d[col].astype(str).isin(cats), "OTHER")
        d[col] = pd.Categorical(v, categories=cats)
    x = d[art["features"]]
    mid = float(art["gb"].predict(x)[0]) + art["shift"] + adj
    g = int(np.digitize(d["sale_level"].iloc[0], art["tier_cuts"])) * 3 + min(max(int(d["age"].iloc[0]), 0), 2)
    lo = float(art["q10"].predict(x)[0]) + adj - art["widen_low_by_group"][g]
    hi = float(art["q90"].predict(x)[0]) + adj + art["widen_high_by_group"][g]
    return {"price": math.exp(mid), "low_80": math.exp(min(lo, mid)), "high_80": math.exp(max(hi, mid))}


def sale_price_2yo_breeze(lot: dict) -> dict:
    """OBS 2-year-old with a breeze time (obs_2yo.py). Build `lot` with
    obs_2yo.add_inputs(obs_2yo.obs_lots(), frame); use sale_price_rolling for untimed horses."""
    art = _load("obs_2yo")
    d = pd.DataFrame([lot])
    d["sale_cat"] = pd.Categorical(d["sale"].astype(str), categories=art["sale_categories"])
    return {"price": math.exp(float(art["gb"].predict(d[art["features"]])[0]))}

"""No-peeking tests: every prediction may use only information available before that lot sold.

    .venv/bin/python -m pytest models_final/tests -q

The central test (test_prediction_ignores_everything_not_earlier) builds a synthetic set of
out-of-time predictions, picks lots, scrambles the prices of every lot that is NOT earlier
than the chosen one (later years, the same or a later session, the same or a later month at
another sale, and the lot itself), and checks that the chosen lot's prediction and 80% range
don't move. The other tests pin down each "before" helper on a small hand-made case.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import ebv_v2 as E
import features as F
import market_growth as MG
import obs_2yo as O
import rolling_price as R
import v3_price as V


# ------------------------------------------------------------------ synthetic predictions
def synthetic(seed: int = 0, years=range(2012, 2022)) -> pd.DataFrame:
    """Out-of-time predictions shaped like rolling_price.one_year's output: several sales a year
    (different months, several sessions each), shared sires, three ages, quantile columns."""
    rng = np.random.default_rng(seed)
    rows = []
    sales = [("JAN", 1, 2), ("JULY", 7, 1), ("SEPT", 9, 5), ("SEPT B", 9, 2), ("ONE-OFF", np.nan, 2)]
    for y in years:
        for name, month, n_ses in sales:
            for ses in range(1, n_ses + 1):
                for _ in range(12):
                    rows.append({"sale_year": y, "sale": name, "source_file": f"{name}_{y}", "session": float(ses),
                                 "month": month, "sire_n": f"S{rng.integers(0, 8)}", "age": int(rng.integers(0, 3)),
                                 "is_obs": 0, "sale_level": float(rng.normal(10.3, 0.6))})
    p = pd.DataFrame(rows)
    p["raw"] = rng.normal(10.5, 0.8, len(p))
    p["log_price"] = p["raw"] + rng.normal(0.05, 0.7, len(p))
    p["q10"] = p["raw"] - 0.9
    p["q90"] = p["raw"] + 0.9
    p.index = rng.permutation(len(p)) + 1000  # non-trivial index, as in the real frame
    return p


CUTS = np.array([10.0, 10.6])
SETTINGS = [  # (within-sale k, sire k, sire scope, position correction)
    (None, None, "all", False), (100, None, "all", False), (100, 10, "all", False),
    (30, 5, "age", False), (10, 2, "age_tier", False), (100, 10, "all", True)]


def run(p: pd.DataFrame, k, ks, scope, pos) -> pd.DataFrame:
    return R.finish(p, CUTS, k, ks, None, scope, pos=pos)


def not_earlier(p: pd.DataFrame, lot) -> pd.Series:
    """Lots whose price the model may NOT know when `lot` sells: anything that isn't
    (an earlier year) or (an earlier month this year) or (an earlier session of the same sale)."""
    earlier = ((p["sale_year"] < lot["sale_year"])
               | ((p["sale_year"] == lot["sale_year"]) & (p["month"] < lot["month"]))
               | ((p["source_file"] == lot["source_file"]) & (p["session"] < lot["session"])))
    return ~earlier


@pytest.mark.parametrize("k,ks,scope,pos", SETTINGS)
def test_prediction_ignores_everything_not_earlier(k, ks, scope, pos):
    p = synthetic()
    base = run(p, k, ks, scope, pos)
    rng = np.random.default_rng(1)
    test_year = p[p["sale_year"] == 2020]
    picks = list(test_year.sample(25, random_state=2).index)
    # make sure the hard cases are covered: a late session of the big sale, and a one-off sale
    picks.append(test_year[(test_year["sale"] == "SEPT") & (test_year["session"] == 5)].index[0])
    picks.append(test_year[test_year["sale"] == "ONE-OFF"].index[0])
    for i in picks:
        lot = p.loc[i]
        hidden = not_earlier(p, lot)
        assert hidden[i], "the lot's own price must be among the hidden ones"
        q = p.copy()
        q.loc[hidden, "log_price"] = q.loc[hidden, "log_price"] + rng.normal(0, 3, hidden.sum())
        out = run(q, k, ks, scope, pos)
        for col in ("pred", "lo", "hi"):
            assert out.loc[i, col] == pytest.approx(base.loc[i, col], abs=1e-9), (
                f"{col} of lot {i} ({lot['sale']} {lot['sale_year']} session {lot['session']}) "
                f"moved when later prices changed (settings k={k}, ks={ks}, {scope}, pos={pos})")


def test_updates_do_react_to_earlier_prices():
    """The other direction: the updates aren't simply switched off. Raising every earlier-session
    price at the big sale must raise a late-session prediction."""
    p = synthetic()
    lot = p[(p["sale_year"] == 2020) & (p["sale"] == "SEPT") & (p["session"] == 5)].index[0]
    q = p.copy()
    earlier = (q["source_file"] == "SEPT_2020") & (q["session"] < 5)
    q.loc[earlier, "log_price"] += 1.0
    before, after = run(p, 100, 10, "all", False), run(q, 100, 10, "all", False)
    assert after.loc[lot, "pred"] > before.loc[lot, "pred"] + 0.05


def test_first_session_gets_no_within_sale_update():
    out = run(synthetic(), 100, None, "all", False)
    first = out["session"] == out.groupby("source_file")["session"].transform("min")
    assert (out.loc[first, "adj"] == 0).all()


def test_sire_update_counts_only_earlier_lots_and_respects_scope():
    p = pd.DataFrame({"sale_year": [2020] * 6, "sire_n": ["A", "A", "A", "A", "A", "B"],
                      "month": [1, 9, 9, 9, 9, 9], "source_file": ["jan", "sep", "sep", "sep", "sepB", "sep"],
                      "session": [1, 1, 2, 2, 1, 2], "age": [0, 1, 1, 1, 1, 1], "tier": [0, 2, 2, 2, 2, 2]},
                     index=[10, 11, 12, 13, 14, 15])
    res = pd.Series([0.5, 0.2, 9.0, 7.0, 3.0, 1.0], index=p.index)
    a = R.sire_earlier(p, res, "all")
    assert a.loc[11].tolist() == [0.5, 1]           # September session 1 sees January only
    assert a.loc[12].tolist() == pytest.approx([0.7, 2])  # session 2: January + session 1, not its own session
    assert a.loc[14].tolist() == [0.5, 1]           # another September sale: same-month lots don't count
    assert a.loc[15].tolist() == [0, 0]             # another sire
    assert a.loc[10].tolist() == [0, 0]             # nothing earlier
    same_age = R.sire_earlier(p, res, "age")
    assert same_age.loc[12].tolist() == pytest.approx([0.2, 1])  # the January weanling no longer counts


def test_bias_shift_uses_previous_three_years_only():
    p = synthetic()
    out = R.finish(p, CUTS)
    for y in (2016, 2020):
        prev = p[p["sale_year"].between(y - 3, y - 1)]
        expected = (prev["log_price"] - prev["raw"]).mean()
        assert out.loc[out["sale_year"] == y, "shift"].iloc[0] == pytest.approx(expected)


# ------------------------------------------------------------------ "before" helpers
def test_cum_before_counts_events_before_the_sale_year():
    events = pd.DataFrame({"sire_n": ["A", "A", "A"], "year": [2010, 2012, 2015]})
    lots = pd.DataFrame({"sire_n": ["A", "A", "A", "B"], "sale_year": [2012, 2013, 2015, 2013]}, index=[5, 6, 7, 8])
    out = V.cum_before(events, "sire_n", lots, "n")
    assert out.tolist() == [1, 2, 2, 0]  # an event in the sale year itself doesn't count yet


def test_earlier_mean_uses_strictly_earlier_years():
    y = pd.DataFrame({"k": ["A", "A", "A", "A"], "sale_year": [2010, 2011, 2011, 2012], "rel": [1.0, 3.0, 5.0, 100.0]})
    out = F._earlier_mean(y, "k", "prev")
    assert out["prev"].isna().iloc[0]
    assert out.loc[out["sale_year"] == 2011, "prev"].tolist() == [1.0, 1.0]  # not its own year
    assert out.loc[out["sale_year"] == 2012, "prev"].iloc[0] == pytest.approx(3.0)


def test_obs_helpers_use_earlier_years_only():
    src = pd.DataFrame({"sire_n": ["A", "A", "A"], "sale_year": [2023, 2024, 2025], "rel": [1.0, 2.0, 50.0],
                        "log_fee": [9.0, 9.5, 99.0]})
    d = pd.DataFrame({"sire_n": ["A", "A"], "sale_year": [2024, 2025]}, index=[3, 4])
    eff = O.shrunk_before(src, "sire_n", d, "sire_n", 0, "eff")
    assert eff.tolist() == pytest.approx([1.0, 1.5])
    fee = O.latest_before(src, "sire_n", "log_fee", d, "sire_n", "fee")
    assert fee.tolist() == [9.0, 9.5]


def test_mare_record_excludes_the_foal_itself_and_later_sales():
    rows = pd.DataFrame({"dam_key": ["M", "M"], "birth_year": [2020, 2021], "sale_year": [2021, 2022]}, index=[1, 2])
    foals = pd.DataFrame({"dam_key": ["M", "M", "M", "M"], "f_birth": [2018, 2020, 2020, 2021],
                          "f_year": [2019, 2021, 2022, 2022], "beat": [1.0, 2.0, 2.0, 9.0]})
    out = E.earlier_foals(rows, foals, "beat", "b")
    assert out.loc[1].tolist() == [1.0, 1]        # the 2020 foal is this lot itself: excluded
    assert out.loc[2].tolist() == [1.5, 2]        # 2018 and 2020 foals; the 2021 foal is itself


def test_growth_forecast_fits_on_earlier_years_only():
    rng = np.random.default_rng(0)
    g = pd.DataFrame([{"sale": f"S{i}", "sale_year": y, "age": 1, "change": rng.normal(0.05, 0.1), "w": 100.0,
                       **{x: rng.normal() for x in MG.GROWTH_X}} for y in range(2005, 2016) for i in range(6)])
    base = MG.add_forecast(g)
    h = g.copy()
    h.loc[h["sale_year"] >= 2012, "change"] += 5.0  # change 2012 onward
    out = MG.add_forecast(h)
    m = g["sale_year"] == 2012
    assert np.allclose(out.loc[m, "growth_fc"], base.loc[m, "growth_fc"])


# ------------------------------------------------------------------ session parser
def test_keeneland_glued_stream_is_split_by_session():
    head = "Session,Hip,PropertyLine1,PropertyLine2,Description,DOB,Color,Sex,Horse Name,Sire,Dam,CoveringSire,LastService,Pregnancy,SoldAs,Purchaser,Price"

    def rec(hip, cons, price_and_next):
        return f'{hip:04d},"{cons}","","","05/01/2021","B",Colt,"X","Sire","Dam","","","","","Buyer",{price_and_next}'
    # session 1 for hips 1-2, session 2 for hip 3; hip 2's price (100001) itself ends in 1
    text = head + "1," + rec(1, "Lane's End, Agent", "500001") + "," + rec(2, "Gainesway", "1000012") + "," + rec(3, "Taylor Made", "25000")
    out = R.SS._keeneland(text)
    assert [(h, s) for h, s, *_ in out] == [(1, 1), (2, 1), (3, 2)]
    assert out[0][2] == "Lane's End, Agent" and out[0][3] == "05/01/2021"


def test_fasig_sessions_ranked_by_date():
    df = pd.DataFrame({"SESSION": ["2010-10-06", "2010-10-05", "2010-10-06"], "HIP": ["3", "1", "2"],
                       "PROPERTY LINE": ["A", "B", "C"], "YEAR OF BIRTH": ["2009-04-15", "", "2009-02-01"]})
    out = R.SS._fasig(df)
    assert sorted((h, s) for h, s, *_ in out) == [(1, 1), (2, 2), (3, 2)]

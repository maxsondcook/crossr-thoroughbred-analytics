"""Ranking scores for the breeding-value models (targets unchanged).

    ../.venv/bin/python ebv_ranking.py

Design decision (2026-11-19): keep the agreed targets (stallions: lifetime AEI; mares: foal
price relative to the stud fee) and score the models on how well they RANK, alongside R2.
A single foal's price is noisy whoever the dam is, so R2 on single foals stays low even when
the model sorts mares well; a breeder needs the sorting.

Same models and splits as train_ebv.py (the saved v1 linear regressions):
- mares: fit on sale years 2005-2020, scored on 2021-2025 foals;
- stallions: 5-fold cross-validation repeated 10 times (259 sires), out-of-fold predictions.

Scores:
- Spearman rank correlation between prediction and outcome (0 = no better than random order,
  1 = perfect order);
- top fifth vs bottom fifth by prediction: the outcome's median in each, and the ratio;
- share of pairs ordered correctly (concordance; 50% = coin flip);
- mares only: the breed-or-not rule from the walkthrough (breed when the predicted foal price
  beats the fee plus $30k upkeep): average profit per foal when the rule says breed vs don't.
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.linear_model import LinearRegression
from sklearn.model_selection import RepeatedKFold

import features as F
import train_ebv as T

sys.path.insert(0, str(F.HERE.parent / "model_walkthrough"))
import fit_ebv_mare  # noqa: E402
import fit_ebv_stallion  # noqa: E402

UPKEEP = fit_ebv_mare.UPKEEP


def concordance(y: np.ndarray, p: np.ndarray, n: int = 200_000, seed: int = 0) -> float:
    """Share of random pairs (with different outcomes) that the prediction puts in the right order."""
    rng = np.random.default_rng(seed)
    i, j = rng.integers(0, len(y), n), rng.integers(0, len(y), n)
    keep = y[i] != y[j]
    i, j = i[keep], j[keep]
    return float(np.mean(np.sign(p[i] - p[j]) == np.sign(y[i] - y[j])))


def fifths(y: pd.Series, p: pd.Series, outcome) -> pd.DataFrame:
    band = pd.qcut(p.rank(method="first"), 5, labels=["bottom 20%", "2nd", "3rd", "4th", "top 20%"])
    return pd.DataFrame({"band": band, "o": outcome}).groupby("band", observed=True)["o"].agg(["size", "median"])


def mares() -> dict:
    d = fit_ebv_mare.build()
    tr, te = d[d["sale_year"].between(2005, 2020)], d[d["sale_year"].between(2021, 2025)]
    m = LinearRegression().fit(tr[T.MARE_X], tr["added"])
    p = pd.Series(m.predict(te[T.MARE_X]), index=te.index)
    y = te["added"]
    ratio = np.exp(y)
    f = fifths(y, p, ratio)
    out = {"foals": len(te), "R2": T.r2(y, p), "spearman": float(stats.spearmanr(p, y).statistic),
           "concordance": concordance(y.to_numpy(), p.to_numpy()),
           "median_price_over_fee_by_fifth": f["median"].round(3).to_dict(),
           "top_vs_bottom_fifth": float(f["median"].iloc[-1] / f["median"].iloc[0])}
    profit = te["price"] - te["fee_conception"] - UPKEEP
    breed = te["fee_conception"] * np.exp(p) > te["fee_conception"] + UPKEEP
    out["rule"] = {"breed": {"foals": int(breed.sum()), "mean_profit": float(profit[breed].mean()),
                             "median_profit": float(profit[breed].median()), "share_profitable": float((profit[breed] > 0).mean())},
                   "dont": {"foals": int((~breed).sum()), "mean_profit": float(profit[~breed].mean()),
                            "median_profit": float(profit[~breed].median()), "share_profitable": float((profit[~breed] > 0).mean())},
                   "all": {"mean_profit": float(profit.mean()), "share_profitable": float((profit > 0).mean())}}
    print(f"Mares: {len(te):,} held-out foals (2021-2025), v1 linear model")
    print(f"  R2 {out['R2']:.3f}   Spearman {out['spearman']:.3f}   pairs ordered correctly {out['concordance']:.1%}")
    print("  median foal price / fee by the model's fifth:\n" + f.round(2).to_string())
    print(f"  top fifth vs bottom fifth: x{out['top_vs_bottom_fifth']:.2f}")
    r = out["rule"]
    print(f"  breed-or-not rule (fee + ${UPKEEP:,} upkeep): says breed for {r['breed']['foals']:,} foals, mean profit "
          f"${r['breed']['mean_profit']:,.0f} ({r['breed']['share_profitable']:.0%} profitable); says don't for "
          f"{r['dont']['foals']:,}, mean profit ${r['dont']['mean_profit']:,.0f} ({r['dont']['share_profitable']:.0%} profitable)")
    return out


def stallions() -> dict:
    d = fit_ebv_stallion.build().dropna(subset=["first_crops_log_price"]).reset_index(drop=True)
    X, y = d[T.STALLION_X], d["log_aei"]
    cv = RepeatedKFold(n_splits=5, n_repeats=10, random_state=42)
    sp, conc, preds = [], [], []
    for k, (tr, te) in enumerate(cv.split(X)):
        p = LinearRegression().fit(X.iloc[tr], y.iloc[tr]).predict(X.iloc[te])
        sp.append(stats.spearmanr(p, y.iloc[te]).statistic)
        conc.append(concordance(y.iloc[te].to_numpy(), p, n=20_000, seed=k))
        if k < 5:  # the first repeat covers every sire exactly once
            preds.append(pd.Series(p, index=y.index[te]))
    p1 = pd.concat(preds).sort_index()
    f = fifths(y, p1, np.exp(y))
    out = {"sires": len(d), "spearman_mean": float(np.mean(sp)), "spearman_sd": float(np.std(sp)),
           "concordance": float(np.mean(conc)), "median_aei_by_fifth": f["median"].round(3).to_dict(),
           "top_vs_bottom_fifth": float(f["median"].iloc[-1] / f["median"].iloc[0])}
    print(f"\nStallions: {len(d)} sires, v1 linear model, 5-fold CV x10")
    print(f"  Spearman {out['spearman_mean']:.3f} (sd {out['spearman_sd']:.3f})   pairs ordered correctly {out['concordance']:.1%}")
    print("  median lifetime AEI by the model's fifth (out-of-fold, one repeat):\n" + f.round(2).to_string())
    print(f"  top fifth vs bottom fifth: x{out['top_vs_bottom_fifth']:.2f}")
    return out


if __name__ == "__main__":
    res = {"mare": mares(), "stallion": stallions()}
    (F.ART / "ebv_ranking_results.json").write_text(json.dumps(res, indent=2, default=float))

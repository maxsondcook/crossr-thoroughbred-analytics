"""Model 3 (females): a mare's expected breeding value, measured by what her foals sell for.

    ../.venv/bin/python fit_ebv_mare.py

Her breeding value is the "added value" of her yearlings: how many times the covering stud
fee they sold for, on the log scale (0 = sold for exactly the fee, +0.69 = twice the fee).
The predictors are what an owner knows before her first yearling sells: her own race
record, her sire, the fees she was bred to and the year.
"""
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
from sklearn.model_selection import train_test_split

import common as c

UPKEEP = 30_000  # rough cost to carry a mare and raise her foal to a yearling sale (an assumption)


TRAIN = (2005, 2020)
TEST = (2021, 2025)


def build() -> pd.DataFrame:
    """One row per yearling whose dam already had a yearling sold: her record so far -> this foal."""
    y = c.yearlings().dropna(subset=["log_fee"])
    y = y[(y["damsire_n"] != "") & y["sale_year"].between(2002, 2025)].copy()
    y["added"] = y["log_price"] - y["log_fee"]  # this foal's price as a multiple of the fee (log)
    y["dam_key"] = y["dam_n"] + "|" + y["damsire_n"]  # name plus her sire, never her name alone

    # her produce record from strictly earlier sale years
    per = y.groupby(["dam_key", "sale_year"])["added"].agg(["sum", "count"]).reset_index().sort_values("sale_year")
    g = per.groupby("dam_key")
    per["prev_n"] = g["count"].cumsum() - per["count"]
    per["prev_mean"] = (g["sum"].cumsum() - per["sum"]) / per["prev_n"].where(per["prev_n"] > 0)
    y = y.merge(per[["dam_key", "sale_year", "prev_n", "prev_mean"]], on=["dam_key", "sale_year"])
    y = y[y["prev_n"] > 0].copy()
    y["log_prev_n"] = np.log(y["prev_n"])

    # her own race record: a runner with her name whose sire is her sire
    run = c.runners()
    run = run[run["sex"].isin(["F", "M"])].sort_values("earnings", ascending=False)
    run = run.drop_duplicates(["name_n", "sire_n"]).rename(columns={"name_n": "dam_n", "sire_n": "damsire_n"})
    y = y.merge(run[["dam_n", "damsire_n", "earnings", "graded_wins"]], on=["dam_n", "damsire_n"], how="left")
    y["record_known"] = y["earnings"].notna().astype(int)
    y["log_earnings"] = np.log1p(y["earnings"]).fillna(0.0)
    y["graded_winner"] = (y["graded_wins"] > 0).astype(int)

    # her sire as a sire: his best lifetime AEI, when he made the list
    aei = c.lifetime_aei().groupby("stallion_n")["aei"].max()
    y["sire_aei"] = y["damsire_n"].map(aei)
    y["sire_aei_known"] = y["sire_aei"].notna().astype(int)
    y["log_sire_aei"] = np.log(y["sire_aei"]).fillna(0.0)
    return y


def main() -> None:
    y = build()
    train = y[y["sale_year"].between(*TRAIN)]
    test = y[y["sale_year"].between(*TEST)]
    print("=== Stage 0: look at the data ===")
    print("yearlings whose dam had an earlier yearling sold (fee on file):", y.shape,
          f"  mares: {y['dam_key'].nunique():,}")
    print("  train", train.shape, " test", test.shape, "  dam has a race record:", int(y["record_known"].sum()))
    print(y[["added", "prev_mean", "prev_n", "price", "fee_conception", "earnings", "sire_aei"]].describe().to_string())
    s = train.sample(min(4000, len(train)), random_state=42)
    plt.figure(figsize=(7, 5))
    plt.scatter(s["prev_mean"], s["added"], s=3, alpha=0.3)
    plt.axhline(0, color="grey", lw=0.8)
    plt.axvline(0, color="grey", lw=0.8)
    plt.xlabel("her earlier foals: mean log(price / fee)")
    plt.ylabel("this foal: log(price / fee)")
    plt.title("Mares: does her record predict her next foal?")
    plt.savefig(c.OUT / "mare_scatter.png", dpi=120, bbox_inches="tight")
    print("saved out/mare_scatter.png")

    print("\n=== Stage 1: best guess with no model ===")
    base = train["added"].mean()
    print(f"mean added value {base:+.3f} -> a foal sells for {np.exp(base):.2f}x the fee (typical); "
          f"typical error (SD) {train['added'].std():.3f}")

    formula = ("added ~ prev_mean + log_prev_n + log_fee + log_earnings + record_known + graded_winner"
               " + log_sire_aei + sire_aei_known")
    m1 = smf.ols(formula, data=train).fit()
    c.show_fit(m1, "Stage 2: multiple regression (training years)")
    print("\nIn plain terms (multiples of the fee):")
    print(f"  her earlier foals 1 log-unit better (2.7x the fee) -> next foal x{np.exp(m1.params['prev_mean']):.3f}")
    print(f"  bred to a stallion with twice the fee -> price/fee x{2 ** m1.params['log_fee']:.3f}")
    print(f"  she was a graded winner -> x{np.exp(m1.params['graded_winner']):.3f}")
    print(f"  her sire's AEI twice as high -> x{2 ** m1.params['log_sire_aei']:.3f}")

    print("\n=== Stage 3: predict and decide ===")

    def predict_ebv(fee, prev_mean, prev_n, earnings=None, graded_winner=False, sire_aei=None, model=m1):
        """Her breeding value for this mating: expected log(foal price / fee)."""
        row = pd.DataFrame([dict(prev_mean=prev_mean, log_prev_n=np.log(prev_n), log_fee=np.log(fee),
                                 log_earnings=np.log1p(earnings) if earnings is not None else 0.0,
                                 record_known=int(earnings is not None), graded_winner=int(graded_winner),
                                 log_sire_aei=np.log(sire_aei) if sire_aei else 0.0,
                                 sire_aei_known=int(bool(sire_aei)))])
        return float(model.predict(row).iloc[0])

    def should_breed(fee, **mare):
        """Breed her to this stallion when her foal's typical price beats the fee plus upkeep."""
        foal = fee * np.exp(predict_ebv(fee, **mare))
        return foal > fee + UPKEEP, foal

    for label, mare in (("good producer (2 foals sold at 3x the fee)", dict(prev_mean=np.log(3), prev_n=2)),
                        ("poor producer (2 foals sold at half the fee)", dict(prev_mean=np.log(0.5), prev_n=2))):
        for fee in (20_000, 75_000):
            ok, foal = should_breed(fee, **mare)
            print(f"{label}, stallion fee ${fee:,}: typical foal ${foal:,.0f} vs cost ${fee + UPKEEP:,} -> breed? {ok}")

    weakest = m1.pvalues.drop("Intercept").idxmax()
    terms = [t for t in m1.params.index if t not in ("Intercept", weakest)]
    m2 = smf.ols("added ~ " + " + ".join(terms), data=train).fit()
    c.show_fit(m2, f"Refit without the weakest predictor ({weakest}, p = {m1.pvalues[weakest]:.4f})")

    print("\n=== Stage 4: held-out sale years", TEST, "===")

    def score(model, d):
        err = d["added"] - model.predict(d)
        return 1 - (err ** 2).sum() / ((d["added"] - d["added"].mean()) ** 2).sum(), err.abs().mean()

    b_err = test["added"] - base
    print(f"  {'model':<30}{'test R2':>8}{'test MAE':>10}{'train R2':>10}")
    print(f"  {'baseline (train mean)':<30}{1 - (b_err ** 2).sum() / ((test['added'] - test['added'].mean()) ** 2).sum():>8.3f}{b_err.abs().mean():>10.3f}")
    for name, mod in (("m1 all predictors", m1), ("m2 without " + weakest, m2)):
        r2, mae = score(mod, test)
        print(f"  {name:<30}{r2:>8.3f}{mae:>10.3f}{score(mod, train)[0]:>10.3f}")

    print("\n=== Stage 5: does a higher breeding value mean better foals? ===")
    t = test.copy()
    t["pred"] = m1.predict(t)
    t["band"] = pd.qcut(t["pred"], 5, labels=["lowest 20%", "2nd", "3rd", "4th", "highest 20%"])
    t["ratio"] = t["price"] / t["fee_conception"]
    t["profit"] = t["price"] - t["fee_conception"] - UPKEEP
    print(t.groupby("band", observed=True).agg(foals=("ratio", "size"), median_price_over_fee=("ratio", "median"),
                                               share_beat_fee_plus_upkeep=("profit", lambda p: (p > 0).mean()))
          .to_string())
    t["breed"] = t["pred"] > np.log1p(UPKEEP / t["fee_conception"])
    same = t.groupby("breed")["profit"].agg(["count", "mean", "median"])
    print(same.rename(index={True: "rule says breed", False: "rule says don't"}).to_string())


if __name__ == "__main__":
    main()

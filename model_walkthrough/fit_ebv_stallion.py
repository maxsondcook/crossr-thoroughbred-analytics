"""Model 2 (males): a stallion's expected breeding value, measured as his lifetime AEI.

    ../.venv/bin/python fit_ebv_stallion.py

AEI (Average Earnings Index) compares his foals' race earnings with all runners: 1.00 is
average, 2.00 is twice the average. The predictors are what a breeder knows when the
stallion goes to stud: his initial fee, his own race record, his sire's AEI, his year.

Caveat: the AEI list only ranks each year's top 150 sires (AEI about 1.0 and up), so this
model describes proven, above-average sires, not every stallion.
"""
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
from sklearn.model_selection import train_test_split

import common as c


def build() -> pd.DataFrame:
    aei = c.lifetime_aei()
    # target: his most recent (most mature) lifetime AEI
    st = aei.sort_values("year").groupby(["stallion_n", "foaling_year"], as_index=False).last()
    st = st.rename(columns={"aei": "aei_lifetime", "year": "list_year"})

    # his sire's AEI: the best lifetime AEI the sire ever posted (by name; a top-150 sire name is unique enough)
    sire_aei = aei.groupby("stallion_n")["aei"].max().rename("sire_aei")
    st["sire_aei"] = st["sire_n"].map(sire_aei)

    # first-crop yearling price: what buyers paid for his first two crops of yearlings. Known a year
    # or more before any of his foals race, so it can't contain the answer (his foals' earnings).
    # (His initial stud fee would be the textbook input, but the fee table covers only 10 of these
    # sires in their first seasons, and later fees already reflect his foals' racing.)
    y = c.yearlings()[["sire_n", "sale_year", "log_price"]]
    y = y.merge(st[["stallion_n", "foaling_year"]].rename(columns={"stallion_n": "sire_n"}), on="sire_n")
    y = y[y["sale_year"] - y["foaling_year"] <= 7]  # first crop sells at about his age 5; keep two crops
    early = y.groupby(["sire_n", "foaling_year"]).agg(first_crops_log_price=("log_price", "mean"),
                                                      first_crops_n=("log_price", "size")).reset_index()
    early = early[early["first_crops_n"] >= 3]
    st = st.merge(early.rename(columns={"sire_n": "stallion_n"}), on=["stallion_n", "foaling_year"], how="left")

    # his own race record, matched on name AND foaling year
    run = c.runners().rename(columns={"name_n": "stallion_n", "foal_year": "foaling_year", "sire_n": "rec_sire"})
    run = run.sort_values("earnings", ascending=False).drop_duplicates(["stallion_n", "foaling_year"])
    st = st.merge(run[["stallion_n", "foaling_year", "earnings", "starts", "graded_wins"]],
                  on=["stallion_n", "foaling_year"], how="left")

    st["log_aei"] = np.log(st["aei_lifetime"])
    st["year_c"] = st["foaling_year"] - 2000  # years since 2000 (centring keeps the numbers readable)
    st["record_known"] = st["earnings"].notna().astype(int)
    st["log_earnings"] = np.log1p(st["earnings"]).fillna(0.0)  # 0 when no race record on file; record_known carries it
    st["graded_winner"] = (st["graded_wins"] > 0).astype(int)
    st["sire_aei_known"] = st["sire_aei"].notna().astype(int)
    st["log_sire_aei"] = np.log(st["sire_aei"]).fillna(0.0)  # 0 = log(1.00), an average sire
    return st


def main() -> None:
    st = build()
    print("=== Stage 0: look at the data ===")
    print("stallions with a lifetime AEI:", len(st))
    print("  with first-crop yearling prices:", st["first_crops_log_price"].notna().sum(),
          "  with a race record:", st["earnings"].notna().sum(),
          "  with a sire AEI:", st["sire_aei_known"].sum())
    df = st.dropna(subset=["first_crops_log_price"]).copy()
    print("usable (first-crop yearling prices on file):", df.shape)
    df["first_crops_price"] = np.exp(df["first_crops_log_price"])
    print(df[["aei_lifetime", "first_crops_price", "first_crops_n", "earnings", "graded_winner", "sire_aei", "foaling_year"]]
          .describe().to_string())
    plt.figure(figsize=(7, 5))
    plt.scatter(df["first_crops_log_price"], df["log_aei"], s=12, alpha=0.6)
    plt.xlabel("log(average price of his first-crop yearlings)")
    plt.ylabel("log(lifetime AEI)")
    plt.title("Stallion AEI vs what his first yearlings sold for")
    plt.savefig(c.OUT / "stallion_scatter.png", dpi=120, bbox_inches="tight")
    print("saved out/stallion_scatter.png")

    train, test = train_test_split(df, test_size=0.3, random_state=42)
    print(f"train {len(train)} stallions, test {len(test)}")

    print("\n=== Stage 1: best guess with no model ===")
    base = train["log_aei"].mean()
    print(f"mean log AEI {base:.3f} -> AEI {np.exp(base):.2f}; typical error (SD) {train['log_aei'].std():.3f}")

    formula = ("log_aei ~ first_crops_log_price + log_earnings + record_known + graded_winner"
               " + log_sire_aei + sire_aei_known + year_c")
    m1 = smf.ols(formula, data=train).fit()
    c.show_fit(m1, "Stage 2: multiple regression (training stallions)")
    print("\nIn plain terms:")
    print(f"  first-crop yearlings selling for twice as much -> AEI x{2 ** m1.params['first_crops_log_price']:.3f}")
    print(f"  doubling his own earnings -> AEI x{2 ** m1.params['log_earnings']:.3f}")
    print(f"  graded winner himself -> AEI x{np.exp(m1.params['graded_winner']):.3f}")
    print(f"  sire's AEI twice as high -> AEI x{2 ** m1.params['log_sire_aei']:.3f}")

    print("\n=== Stage 3: predict and decide ===")

    def predict_aei(first_crops_price, earnings, graded_winner, sire_aei, foaling_year, model=m1):
        row = pd.DataFrame([dict(first_crops_log_price=np.log(first_crops_price),
                                 log_earnings=np.log1p(earnings) if earnings is not None else 0.0,
                                 record_known=int(earnings is not None),
                                 graded_winner=int(graded_winner),
                                 log_sire_aei=np.log(sire_aei) if sire_aei else 0.0,
                                 sire_aei_known=int(bool(sire_aei)), year_c=foaling_year - 2000)])
        return float(np.exp(model.predict(row).iloc[0]))

    median_aei = float(np.exp(train["log_aei"].median()))

    def should_breed_to(**stallion):
        """Yes when his predicted AEI beats the median proven sire in the training data."""
        return predict_aei(**stallion) > median_aei

    ex = dict(first_crops_price=120_000, earnings=1_500_000, graded_winner=True, sire_aei=2.0, foaling_year=2018)
    print("Example young sire: first-crop yearlings averaged $120,000, earned $1.5M, graded winner, "
          "sire's AEI 2.00, foaled 2018")
    print(f"  predicted lifetime AEI {predict_aei(**ex):.2f} (median proven sire {median_aei:.2f}) "
          f"-> breed to him? {should_breed_to(**ex)}")

    weakest = m1.pvalues.drop("Intercept").idxmax()
    terms = [t for t in m1.params.index if t not in ("Intercept", weakest)]
    m2 = smf.ols("log_aei ~ " + " + ".join(terms), data=train).fit()
    c.show_fit(m2, f"Refit without the weakest predictor ({weakest}, p = {m1.pvalues[weakest]:.4f})")

    print("\n=== Stage 4: held-out stallions ===")

    def score(model, d):
        err = d["log_aei"] - model.predict(d)
        return 1 - (err ** 2).sum() / ((d["log_aei"] - d["log_aei"].mean()) ** 2).sum(), err.abs().mean()

    b_err = test["log_aei"] - base
    print(f"  {'model':<28}{'test R2':>8}{'test MAE':>10}{'train R2':>10}")
    print(f"  {'baseline (train mean)':<28}{1 - (b_err ** 2).sum() / ((test['log_aei'] - test['log_aei'].mean()) ** 2).sum():>8.3f}{b_err.abs().mean():>10.3f}")
    for name, m in (("m1 all predictors", m1), ("m2 without " + weakest, m2)):
        r2, mae = score(m, test)
        print(f"  {name:<28}{r2:>8.3f}{mae:>10.3f}{score(m, train)[0]:>10.3f}")

    print("\n=== Stage 5: does the rule pick better sires? ===")
    t = test.copy()
    t["pick"] = np.exp(m1.predict(t)) > median_aei
    print(t.groupby("pick")["aei_lifetime"].agg(["count", "mean", "median"]).rename(index={True: "rule says breed", False: "rule says pass"}).to_string())


if __name__ == "__main__":
    main()

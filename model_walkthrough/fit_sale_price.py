"""Model 1: expected sale price of a yearling at auction (PIP regression-lab stages).

    ../.venv/bin/python fit_sale_price.py

Stage 0 explore, Stage 1 no-model baseline, Stage 2 multiple regression (statsmodels OLS),
Stage 3 predict + decide, refit with fewer covariates, Stage 4 held-out years, Stage 5 the
decision rule with an interval. Every feature is known before the hammer falls.
"""
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

import common as c

TRAIN = (2005, 2020)   # sale years used to fit
TEST = (2021, 2025)    # sale years held out (2026 is still in progress)


def add_history(y: pd.DataFrame) -> pd.DataFrame:
    """Features from earlier sale years only: the sale's price level, the dam's and damsire's record."""
    y = y.copy()
    year_mean = y.groupby(["sale", "sale_year"])["log_price"].transform("mean")
    y["rel"] = y["log_price"] - year_mean  # price vs the rest of that sale that year

    level = y.groupby(["sale", "sale_year"], as_index=False)["log_price"].mean()
    level["sale_year"] += 1  # last year's level is known at this year's sale
    y = y.merge(level.rename(columns={"log_price": "sale_level"}), on=["sale", "sale_year"], how="left")

    def earlier_mean(key: str, name: str) -> None:
        per_year = y.groupby([key, "sale_year"])["rel"].agg(["sum", "count"]).reset_index().sort_values("sale_year")
        g = per_year.groupby(key)
        per_year["cum_sum"] = g["sum"].cumsum() - per_year["sum"]   # strictly earlier years
        per_year["cum_n"] = g["count"].cumsum() - per_year["count"]
        per_year[name] = np.where(per_year["cum_n"] > 0, per_year["cum_sum"] / per_year["cum_n"].clip(lower=1), np.nan)
        y[name] = y.merge(per_year[[key, "sale_year", name]], on=[key, "sale_year"], how="left")[name].values

    y["dam_key"] = y["dam_n"] + "|" + y["damsire_n"]  # a dam is her name plus her sire, never her name alone
    earlier_mean("dam_key", "dam_prev")
    y.loc[y["damsire_n"] == "", "dam_prev"] = np.nan
    earlier_mean("damsire_n", "damsire_prev")
    y.loc[y["damsire_n"] == "", "damsire_prev"] = np.nan
    for col in ("dam_prev", "damsire_prev"):
        y[f"{col}_known"] = y[col].notna().astype(int)
        y[col] = y[col].fillna(0.0)  # 0 = priced like an average lot; the _known flag carries the difference
    return y


def main() -> None:
    y = add_history(c.yearlings())
    y = y[y["sale_year"].between(TRAIN[0], TEST[1])]
    y = y.dropna(subset=["log_fee", "sale_level"])
    train = y[y["sale_year"].between(*TRAIN)]
    test = y[y["sale_year"].between(*TEST)]

    # ---------------------------------------------------------------- Stage 0
    print("=== Stage 0: look at the data ===")
    print("df.shape (all usable lots):", y.shape, "  train", train.shape, "  test", test.shape)
    print(y[["price", "fee_conception", "log_price", "log_fee", "sale_level", "dam_prev", "damsire_prev", "filly"]]
          .describe().to_string())
    sample = train.sample(min(5000, len(train)), random_state=42)
    plt.figure(figsize=(7, 5))
    plt.scatter(sample["log_fee"], sample["log_price"], s=4, alpha=0.3)
    plt.xlabel("log(sire stud fee at conception)")
    plt.ylabel("log(sale price)")
    plt.title("Yearling price vs sire fee (5,000 training lots)")
    plt.savefig(c.OUT / "sale_price_scatter.png", dpi=120, bbox_inches="tight")
    print("saved out/sale_price_scatter.png")

    # ---------------------------------------------------------------- Stage 1
    print("\n=== Stage 1: best guess with no model ===")
    mean_log, sd_log = train["log_price"].mean(), train["log_price"].std()
    print(f"mean log price {mean_log:.3f}  -> guess ${np.exp(mean_log):,.0f} (geometric mean)")
    print(f"median price ${train['price'].median():,.0f}   typical error (SD of log price) {sd_log:.3f}")

    # ---------------------------------------------------------------- Stage 2
    formula = ("log_price ~ log_fee + filly + sale_level + dam_prev + dam_prev_known"
               " + damsire_prev + damsire_prev_known")
    m1 = smf.ols(formula, data=train).fit()
    c.show_fit(m1, "Stage 2: multiple regression on the training years")
    print("\nIn percent (exp(coef) - 1):")
    for k in m1.params.index[1:]:
        print(f"  {k:<22} {np.expm1(m1.params[k]) * 100:+.1f}% per unit")

    # ---------------------------------------------------------------- Stage 3
    print("\n=== Stage 3: predict and decide ===")

    def predict_price(log_fee, filly, sale_level, dam_prev=0.0, dam_prev_known=0, damsire_prev=0.0,
                      damsire_prev_known=0, model=m1):
        row = pd.DataFrame([dict(log_fee=log_fee, filly=filly, sale_level=sale_level, dam_prev=dam_prev,
                                 dam_prev_known=dam_prev_known, damsire_prev=damsire_prev,
                                 damsire_prev_known=damsire_prev_known)])
        return float(np.exp(model.predict(row).iloc[0]))

    def should_buy(asking, **lot):
        return asking < predict_price(**lot)

    lot = dict(log_fee=np.log(50_000), filly=0, sale_level=np.log(100_000), dam_prev=0.3, dam_prev_known=1,
               damsire_prev=0.1, damsire_prev_known=1)
    pred = predict_price(**lot)
    print("Example colt: sire fee $50,000, sale averaged $100,000 last year (geometric), dam's earlier foals "
          "sold 35% above their sales (dam_prev 0.3), damsire's grand-foals 10% above (0.1).")
    print(f"  predicted price ${pred:,.0f}; asking $150,000 -> buy? {should_buy(150_000, **lot)}")

    # refit without the weakest predictor (largest p-value)
    weakest = m1.pvalues.drop("Intercept").idxmax()
    terms = [t for t in m1.params.index if t not in ("Intercept", weakest)]
    m2 = smf.ols("log_price ~ " + " + ".join(terms), data=train).fit()
    c.show_fit(m2, f"Refit without the weakest predictor ({weakest}, p = {m1.pvalues[weakest]:.4f})")

    # ---------------------------------------------------------------- Stage 4
    print("\n=== Stage 4: held-out sale years", TEST, "===")

    def score(model, df):
        pred = model.predict(df)
        err = df["log_price"] - pred
        r2 = 1 - (err ** 2).sum() / ((df["log_price"] - df["log_price"].mean()) ** 2).sum()
        return r2, err.abs().mean(), np.expm1(err.abs().median())

    base_err = (test["log_price"] - mean_log).abs()
    print(f"  {'model':<26}{'R2':>7}{'MAE log':>9}{'typical miss':>14}   (typical miss = median factor between predicted and real price)")
    print(f"  {'baseline (train mean)':<26}{1 - ((test['log_price'] - mean_log) ** 2).sum() / ((test['log_price'] - test['log_price'].mean()) ** 2).sum():>7.3f}"
          f"{base_err.mean():>9.3f}{'x' + format(np.exp(base_err.median()), '.2f'):>14}")
    for name, m in (("m1 all predictors", m1), ("m2 without " + weakest, m2)):
        r2, mae, med = score(m, test)
        r2_tr, mae_tr, _ = score(m, train)
        print(f"  {name:<26}{r2:>7.3f}{mae:>9.3f}{'x' + format(1 + med, '.2f'):>14}    (train R2 {r2_tr:.3f}, MAE {mae_tr:.3f})")

    # ---------------------------------------------------------------- Stage 5
    print("\n=== Stage 5: the decision rule, with a range ===")
    pi = m1.get_prediction(test).summary_frame(alpha=0.20)
    inside = ((test["log_price"].values >= pi["obs_ci_lower"].values) & (test["log_price"].values <= pi["obs_ci_upper"].values)).mean()
    print(f"80% prediction interval holds the real price for {inside * 100:.1f}% of held-out lots (target 80%).")
    one = m1.get_prediction(pd.DataFrame([lot])).summary_frame(alpha=0.20).iloc[0]
    print(f"Example colt: 80% range ${np.exp(one['obs_ci_lower']):,.0f} to ${np.exp(one['obs_ci_upper']):,.0f} "
          f"around ${np.exp(one['mean']):,.0f}.")
    below = (test["log_price"] < m1.predict(test)).mean()
    print(f"Share of held-out lots that sold below the model's price (a 'buy' under should_buy): {below * 100:.1f}%")


if __name__ == "__main__":
    main()

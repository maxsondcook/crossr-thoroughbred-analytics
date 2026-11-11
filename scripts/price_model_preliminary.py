"""
PRELIMINARY end-to-end run of the sale-price modeling pipeline (Phases 12-14).

Not final training: Phase 15 waits for the data-readiness gate.
This proves the dataset, leakage checks, baselines, models, intervals, segment reports
and explanations run end to end, and records what the current data supports.

    scripts/pipeline/railway_env.sh crossr_dev <ml-python> scripts/price_model_preliminary.py \\
        [--csv-dir ~/Desktop/Crossr/data --csv-tables sales_lots race_horses ...] [--skip-leakage]

Needs pandas, numpy, scipy, lightgbm (requirements-model.txt). Writes
docs/price-model-preliminary.md and, with --save-dataset, a pickle of the modeling dataset.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from crossr_core import price_eval as pe  # noqa: E402
from crossr_core import price_features as pf  # noqa: E402

LEVEL = 0.80


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def md_table(df: pd.DataFrame, fmt: dict | None = None) -> str:
    fmt = fmt or {}
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        cells = []
        for c in cols:
            v = r[c]
            f = fmt.get(c)
            if f and isinstance(v, (int, float, np.floating, np.integer)) and not pd.isna(v):
                cells.append(format(v, f))
            elif isinstance(v, (float, np.floating)):
                cells.append("" if pd.isna(v) else f"{v:.3f}")
            elif isinstance(v, (int, np.integer)):
                cells.append(f"{v:,}")
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv-dir")
    ap.add_argument("--csv-tables", nargs="*", default=[])
    ap.add_argument("--skip-leakage", action="store_true")
    ap.add_argument("--save-dataset")
    ap.add_argument("--report", default=str(ROOT / "docs" / "price-model-preliminary.md"))
    args = ap.parse_args()

    src = pf.Sources(csv_dir=args.csv_dir, csv_tables=args.csv_tables)
    provenance = {t: src.provenance(t) for t in pf.TABLE_COLUMNS}
    log(f"sources: {provenance}")
    tx, exclusions = pf.build_transactions(src)
    log(f"transactions {len(tx):,}; excluded from the Y/W model {len(exclusions):,}")
    feats = pf.build_features(tx, src)
    log(f"features for {len(feats):,} eligible sales")
    if args.save_dataset:
        feats.to_pickle(args.save_dataset)

    leakage = []
    if not args.skip_leakage:
        for y in (2012, 2018, 2024):
            res = pf.truncation_check(src, y, sample=400)
            leakage.append(res)
            log(f"leakage check {y}: {res['mismatched_cells']} mismatched cells over {res['features']} features")

    # ---------------------------------------------------------------- modeling frame (sold, valid price)
    d = feats[(feats.status == "sold") & (feats.price > 0)].copy()
    d["lp"] = np.log(d.price)
    d["kind"] = np.where(d.age == 1, "Y", "W")
    cols = pf.feature_columns()
    split = pe.TimeSplit()
    m = split.masks(d.sale_year)
    log("split sizes: " + ", ".join(f"{k} {int(v.sum()):,}" for k, v in m.items()))
    X, y = d[cols].astype(float), d.lp.values

    results = {}
    # ---------------------------------------------------------------- baselines
    test = d[m["test"]]
    base = pe.baselines(d, test)
    for name, pred in base.items():
        results[f"baseline: {name}"] = pe.fit_metrics(test.lp.values, pred.values)
    core = ["log_sire_fee_conception", "weanling", "filly", "sale_level"]
    rb = pe.ridge_fit(X.loc[m["train"], core], y[m["train"]], alpha=1e-6)
    results["baseline: regression (sire fee + sex + age + sale level)"] = pe.fit_metrics(
        test.lp.values, pe.ridge_predict(rb, X.loc[m["test"], core]), n_features=len(core))

    # ---------------------------------------------------------------- ridge, all features
    rr = pe.ridge_fit(X[m["train"]], y[m["train"]], alpha=10.0)
    p_ridge = {k: pe.ridge_predict(rr, X[v]) for k, v in m.items()}
    n_ridge = len(rr["cols"])
    for k in ("train", "valid", "test"):
        results[f"ridge (all features): {k}"] = pe.fit_metrics(y[m[k]], p_ridge[k], n_features=n_ridge)

    # ---------------------------------------------------------------- gradient boosting, all features
    t0 = time.time()
    gbm = pe.lgbm_fit(X[m["train"]], y[m["train"]], X[m["valid"]], y[m["valid"]])
    log(f"lightgbm: {gbm.best_iteration} trees in {time.time() - t0:.0f}s")
    p_gbm = {k: gbm.predict(X[v], num_iteration=gbm.best_iteration) for k, v in m.items()}
    for k in ("train", "valid", "calib", "test"):
        results[f"lightgbm (all features): {k}"] = pe.fit_metrics(y[m[k]], p_gbm[k])

    # ---------------------------------------------------------------- intervals (80%)
    cal, tst = d[m["calib"]], d[m["test"]]
    resid_cal = y[m["calib"]] - p_gbm["calib"]
    g_cal, g_tst = pe.conformal_scale_groups(cal), pe.conformal_scale_groups(tst)
    lo, hi, conf = pe.conformal_intervals(resid_cal, g_cal, g_tst, p_gbm["test"], level=LEVEL)
    lo1, hi1, _ = pe.conformal_intervals(resid_cal, pd.Series("all", index=cal.index),
                                         pd.Series("all", index=tst.index), p_gbm["test"], level=LEVEL)
    intervals = {"group-normalized conformal": pe.interval_metrics(tst.lp, lo, hi),
                 "single-width conformal": pe.interval_metrics(tst.lp, lo1, hi1)}
    log(f"intervals: {intervals}")

    # ---------------------------------------------------------------- segments (test years)
    tst = tst.assign(price_band=pe.price_band(tst.lp), sex=np.where(tst.filly == 1, "filly", "colt"),
                     fee_known=np.where(tst.log_sire_fee_conception.notna(), "sire fee known", "sire fee missing"),
                     dam_record=np.where(tst.dam_record_found == 1, "dam race record found", "no dam race record"),
                     bms_fee=np.where(tst.log_bms_fee_highest.notna(), "bms fee known", "bms fee missing"))
    top_sires = tst.sire.value_counts().index[:12]
    top_bms = tst.damsire.value_counts().index[:12]
    seg = {}
    for by in ("sale_year", "venue", "kind", "sex", "price_band", "fee_known", "dam_record", "bms_fee"):
        seg[by] = pe.segment_report(tst, p_gbm["test"], lo, hi, by=by)
    seg["sire (12 most-sold)"] = pe.segment_report(tst[tst.sire.isin(top_sires)], p_gbm["test"][tst.sire.isin(top_sires).values],
                                                   lo[tst.sire.isin(top_sires).values], hi[tst.sire.isin(top_sires).values], by="sire", min_n=50)
    seg["broodmare sire (12 most-sold)"] = pe.segment_report(tst[tst.damsire.isin(top_bms)], p_gbm["test"][tst.damsire.isin(top_bms).values],
                                                             lo[tst.damsire.isin(top_bms).values], hi[tst.damsire.isin(top_bms).values], by="damsire", min_n=50)
    # group-aware: test dams never sold through in the training years
    train_dams = set(d[m["train"]].dam_k)
    unseen = ~tst.dam_k.isin(train_dams)
    results["lightgbm: test, dams unseen in training"] = pe.fit_metrics(tst.lp.values[unseen.values], p_gbm["test"][unseen.values])
    results["lightgbm: test, dams seen in training"] = pe.fit_metrics(tst.lp.values[~unseen.values], p_gbm["test"][~unseen.values])

    # ---------------------------------------------------------------- feature-group value (drop one group)
    # Early stopping makes single refits noisy (+-0.02 R2), so each drop is scored two stable ways:
    # ridge (deterministic) and LightGBM at the full model's tree count, averaged over 3 seeds.
    def gbm_fixed(cs):
        import lightgbm as lgb

        preds = []
        for seed in (7, 11, 13):
            mdl = lgb.train({"objective": "regression", "learning_rate": 0.05, "num_leaves": 63, "min_data_in_leaf": 100,
                             "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 1.0,
                             "verbose": -1, "seed": seed, "deterministic": True},
                            lgb.Dataset(X.loc[m["train"], cs], y[m["train"]]), num_boost_round=gbm.best_iteration)
            preds.append(mdl.predict(X.loc[m["test"], cs]))
        return pe.fit_metrics(y[m["test"]], np.mean(preds, axis=0))

    ref_g = gbm_fixed(cols)
    ref_r = pe.fit_metrics(y[m["test"]], pe.ridge_predict(pe.ridge_fit(X.loc[m["train"], cols], y[m["train"]], 10.0),
                                                           X.loc[m["test"], cols]))
    ablation = []
    for g, gcols in pf.FEATURE_GROUPS.items():
        keep = [c for c in cols if c not in gcols]
        rg = gbm_fixed(keep)
        rr2 = pe.fit_metrics(y[m["test"]], pe.ridge_predict(pe.ridge_fit(X.loc[m["train"], keep], y[m["train"]], 10.0),
                                                            X.loc[m["test"], keep]))
        ablation.append({"group dropped": g, "features": len(gcols),
                         "r2 lost (lightgbm)": ref_g["r2"] - rg["r2"], "r2 lost (ridge)": ref_r["r2"] - rr2["r2"],
                         "mae added (lightgbm)": rg["mae"] - ref_g["mae"]})
        log(f"drop {g}: R2 lost lightgbm {ref_g['r2'] - rg['r2']:+.4f}, ridge {ref_r['r2'] - rr2['r2']:+.4f}")
    ablation = pd.DataFrame(ablation).sort_values("r2 lost (lightgbm)", ascending=False)

    # ---------------------------------------------------------------- alternatives named in Phase 12.2 D
    bms_alt = []
    base_cols = [c for c in cols if not c.startswith(("log_bms_fee", "bms_fee", "x_sirefee_bmsfee"))]
    for label, extra in (("none", []), ("highest", ["log_bms_fee_highest"]), ("latest", ["log_bms_fee_latest"]),
                         ("at conception", ["log_bms_fee_conception"])):
        cs = base_cols + extra
        mdl = pe.lgbm_fit(X.loc[m["train"], cs], y[m["train"]], X.loc[m["valid"], cs], y[m["valid"]])
        r = pe.fit_metrics(y[m["test"]], mdl.predict(X.loc[m["test"], cs], num_iteration=mdl.best_iteration))
        bms_alt.append({"broodmare-sire fee used": label, "test r2": r["r2"], "test mae": r["mae"]})
    bms_alt = pd.DataFrame(bms_alt)

    # ---------------------------------------------------------------- quantile intervals (CQR) vs conformal
    q_models = {a_: pe.lgbm_fit(X[m["train"]], y[m["train"]], X[m["valid"]], y[m["valid"]], objective="quantile", alpha=a_)
                for a_ in (0.1, 0.9)}
    qp = {k: {a_: mdl.predict(X[m[k]], num_iteration=mdl.best_iteration) for a_, mdl in q_models.items()}
          for k in ("calib", "test")}
    clo, chi, cqr = pe.cqr_intervals(qp["calib"][0.1], qp["calib"][0.9], y[m["calib"]], qp["test"][0.1],
                                     qp["test"][0.9], level=LEVEL)
    intervals["conformalized quantile regression"] = pe.interval_metrics(tst.lp, clo, chi)
    log(f"CQR: {intervals['conformalized quantile regression']}")
    # judge ranges by the price the model expected (conditioning on the actual price conditions on the outcome)
    tst = tst.assign(predicted_band=pe.price_band(pd.Series(p_gbm["test"], index=tst.index)))
    by_pred = []
    for band, g in tst.groupby("predicted_band"):
        ix = tst.index.get_indexer(g.index)
        by_pred.append({"predicted price band": band, "n": len(g),
                        **{f"conformal {k}": v for k, v in pe.interval_metrics(g.lp, lo[ix], hi[ix]).items() if k != "mean_width_log"},
                        **{f"CQR {k}": v for k, v in pe.interval_metrics(g.lp, clo[ix], chi[ix]).items() if k != "mean_width_log"}})
    by_pred = pd.DataFrame(by_pred)

    # ---------------------------------------------------------------- interaction search (Phase 12.4)
    pairs = [("log_sire_fee_conception", "dam_rel"), ("log_sire_fee_conception", "dam_starts"),
             ("log_sire_fee_conception", "log_bms_fee_highest"), ("dam_rel", "damsire_rel"),
             ("dam_starts", "dam_pedigree_completeness"), ("log_bms_fee_highest", "bms_trend"),
             ("sire_trend", "market_trend"), ("dam_pedigree_completeness", "weanling"),
             ("sire_rel", "damsire_rel"), ("filly", "weanling"), ("log_sire_fee_conception", "weanling"),
             ("log_sire_fee_conception", "filly"), ("sale_level", "log_sire_fee_conception")]
    base_cols = [c for c in cols if not c.startswith("x_")]
    inter = pe.interaction_search(X[m["train"]], y[m["train"]], X[m["test"]], y[m["test"]], base_cols, pairs)
    log("interactions:\n" + inter.to_string())

    # ---------------------------------------------------------------- explanations
    shap_feat, shap_group = pe.shap_by_feature(gbm, X[m["test"]], pf.FEATURE_GROUPS)

    write_report(Path(args.report), provenance, tx, exclusions, feats, cols, leakage, results, intervals, conf,
                 seg, ablation, bms_alt, shap_feat, shap_group, split, gbm, by_pred, inter)
    log(f"wrote {args.report}")


def write_report(path, provenance, tx, exclusions, feats, cols, leakage, results, intervals, conf, seg, ablation,
                 bms_alt, shap_feat, shap_group, split, gbm, by_pred, inter):
    L = ["# Sale-price model — PRELIMINARY run", "",
         "> **Preliminary.** The data-readiness gate has not passed, so this is not the "
         "final model. It proves the pipeline end to end on the data available today. Numbers will change.", "",
         f"Generated {time.strftime('%Y-%m-%d')} by `scripts/price_model_preliminary.py`. Method: `docs/price-model.md`.", "",
         "## Sources", "", md_table(pd.DataFrame([{"table": k, "source": v} for k, v in provenance.items()])), "",
         "## Dataset (one row per sale transaction)", "",
         f"Transactions: {len(tx):,}. Eligible for the yearling/weanling model: {int((tx.model_exclusion == '').sum()):,} "
         f"({int((tx.status[tx.model_exclusion == ''] == 'sold').sum()):,} sold, "
         f"{int((tx.status[tx.model_exclusion == ''] == 'rna').sum()):,} RNA with a last bid).", "",
         "Exclusions (each row keeps its reason):", "",
         md_table(exclusions.model_exclusion.value_counts().rename_axis("reason").reset_index(name="rows")), "",
         f"Quality flags kept in the data: catalog title vs age mismatch {int(tx.flag_title_age_mismatch.sum()):,}; "
         f"same horse twice in one sale {int(tx.flag_repeat_horse_same_sale.sum()):,}.", "",
         "Feature coverage (share of eligible sales with a value):", ""]
    cov = feats[cols].notna().mean()
    rows = [{"group": g, "feature": c, "coverage": cov[c]} for g, cs in pf.FEATURE_GROUPS.items() for c in cs]
    L += [md_table(pd.DataFrame(rows), {"coverage": ".1%"}), ""]
    if leakage:
        L += ["## Leakage check", "",
              "Features for a sale year rebuilt from source tables cut off at that year (later fees, races, "
              "rankings, sales and this year's prices removed) and compared with the full build:", "",
              md_table(pd.DataFrame([{**r, "mismatched_features": ", ".join(r["mismatched_features"]) or "none"}
                                     for r in leakage])), ""]
    L += ["## Validation design", "",
          f"Time-based: train ≤ {split.train_end}, early stopping {split.train_end + 1}–{split.valid_end}, "
          f"interval calibration {split.valid_end + 1}–{split.calib_end}, test {split.calib_end + 1}–{split.test_end}. "
          "Sold lots only (RNAs are censored and kept for the Tobit model in docs/sales-model.md). "
          "Group-aware check: test sales whose dam had no sale in the training years.", "",
          "## Fit and baselines (log price)", ""]
    res = pd.DataFrame([{"model / set": k, **{c: v.get(c) for c in ("n", "r2", "adj_r2", "mae", "rmse", "medae",
                                                                      "residual_sd", "median_abs_pct", "medae_usd")}}
                        for k, v in results.items()])
    L += [md_table(res, {"median_abs_pct": ".0%", "medae_usd": ",.0f"}), "",
          "Adjusted R² is shown for the linear models only; for gradient boosting the parameter count is not "
          "meaningful, so out-of-sample R² on later years is the measure.", "",
          f"## Prediction intervals ({int(LEVEL * 100)}%, test years)", "",
          md_table(pd.DataFrame([{"method": k, **v} for k, v in intervals.items()]),
                   {"coverage": ".1%", "median_width_ratio": ".2f"}), "",
          "Width ratio = upper / lower bound in dollars. Group-normalized intervals are wider where the "
          "model has erred more on held-out sales (new sire, unproven dam, missing fee):", "",
          md_table(pd.DataFrame([{"segment": k, "median |error| (log)": v} for k, v in sorted(conf["scales"].items(),
                                                                                               key=lambda kv: -kv[1])])), "",
          "By the price the model predicted (the honest view of interval width: grouping by the actual price "
          "conditions on the outcome, so extreme actual prices always look under-covered):", "",
          md_table(by_pred, {c: ".1%" for c in by_pred.columns if "coverage" in c} |
                   {c: ".2f" for c in by_pred.columns if "ratio" in c}), "",
          "## Interaction search (Phase 12.4, ridge, one product term at a time)", "",
          md_table(inter, {"held-out r2 gain": "+.4f", "t (train)": ".1f"}), "",
          "Gradient boosting already models interactions; a term is worth keeping in an interpretable model only "
          "if it raises held-out R2, not just when its training t-stat is large.", "",
          "## Performance by segment (test years, LightGBM)", ""]
    for name, df in seg.items():
        if len(df):
            L += [f"### {name}", "", md_table(df, {"coverage": ".1%", "median_abs_pct": ".0%", "median_width_ratio": ".2f"}), ""]
    L += ["## What each feature group adds (drop one group, refit, score test years)", "",
          md_table(ablation, {"r2 lost (lightgbm)": "+.4f", "r2 lost (ridge)": "+.4f", "mae added (lightgbm)": "+.4f"}), "",
          "Positive = the group helps on later years. Small negatives are noise: those features are redundant "
          "with others, not harmful.", "",
          "## Broodmare-sire fee: highest vs latest vs at conception (Phase 12.2 D)", "",
          md_table(bms_alt), "",
          "## Main contributing features (mean |SHAP| on test sales, log-price units)", "",
          md_table(shap_group.rename_axis("group").reset_index(name="mean |SHAP|")), "",
          md_table(shap_feat.head(25).rename_axis("feature").reset_index(name="mean |SHAP|")), "",
          "SHAP and drop-one results measure predictive contribution, not causal effect: sire fee and sale level, "
          "for example, partly reflect market judgments of the same horse.", ""]
    path.write_text("\n".join(L))


if __name__ == "__main__":
    main()

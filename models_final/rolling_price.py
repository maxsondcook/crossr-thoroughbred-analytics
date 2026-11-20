"""Sale-price model retrained every year, with recent years weighted more (v3 inputs, LightGBM).

    ../.venv/bin/python rolling_price.py              # tunes the half-life, sessions on, no OBS
    ../.venv/bin/python rolling_price.py --obs --hl 8 # adds the OBS sales; fixed half-life

Design decision (2026-11-18): test the model the way it would be used. Before each year's sales,
retrain on every earlier year, giving year t a weight of 0.5 ** ((Y - 1 - t) / half_life).
The half-life is picked on 2019-2020; 2021-2025 is the test.

Everything for year Y is learned from years before Y only:
- the model itself (censored LightGBM, v3 inputs and settings);
- a bias shift = average out-of-time miss over the three previous years;
- the 80% range: 10%/90% quantile models, widened per sale tier and per side so the previous
  five years' out-of-time ranges would have held 80%.
The Tobit half of v3's blend is dropped: it added nothing on the test years (R2 0.555 either way).

Added 2026-11-19:
- session inputs (sessions.py): tried, and they hurt (hip position already carries them), so
  sessions are used only for the within-sale update below;
- within-sale update: once a sale's earlier sessions have sold, a lot's prediction moves by
  the average miss on them, shrunk toward 0 (n / (n + k)); k is picked on 2019-2020. The
  80% range moves with it. Nothing from the lot's own session or later is used;
- --obs: OBS's sales (obs_lots.py) added as ordinary lots.
"""
from __future__ import annotations

import argparse
import json
import warnings

import joblib
import numpy as np
import pandas as pd
from scipy import stats

import features as F
import obs_lots as OL
import sessions as SS
import stepup_price as S
import v3_price as V

warnings.filterwarnings("ignore")
SESSION_X = ["session", "session_pct", "session_known"]
RAW_X = ["foal_doy", "dob_known"]  # foaling date from the raw files (sessions.add_raw_fields)
GB_X = V.GB_X + RAW_X  # session as an input was tried and hurt (R2 0.583 vs 0.592); --session-x adds it
FIRST_YEAR = 2008      # first out-of-time year (needs a few training years before it)
HALF_LIVES = [None, 12, 8, 5, 3]
SHRINK_K = [None, 1000, 300, 100, 30, 10]  # None = no within-sale update
SIRE_K = [None, 100, 30, 10, 5, 2]          # None = no sire update
MIN_GROUP = 300  # fewest earlier-year lots for an age x tier range group; else the tier's widening


def frame(obs: bool) -> pd.DataFrame:
    """v3's inputs, with the consignor filled from the raw files (Keeneland) and the foaling date."""
    cache = F.ART / ("v4_frame_obs.pkl" if obs else "v4_frame.pkl")
    if cache.exists():
        s = pd.read_pickle(cache)
    else:
        a = SS.add_raw_fields(S.auction_table(OL.as_sales_lots() if obs else None))
        s = V.add_v3_inputs(S.add_family_effects(a))
        s.to_pickle(cache)
    s = SS.add_sessions(s)
    if "obs_session" in s:
        o = s["obs_session"].notna()
        n = s[o].groupby("source_file")["obs_session"].transform("max")
        s.loc[o, "session"] = s.loc[o, "obs_session"]
        s.loc[o, "session_pct"] = np.where(n > 1, (s.loc[o, "obs_session"] - 1) / (n - 1), 0.0)
        s.loc[o, "session_known"] = 1
    s["is_obs"] = s["source_file"].astype(str).str.startswith("obs_sale_").astype(int)
    return s


def weights(years: pd.Series, y: int, half_life) -> np.ndarray:
    if half_life is None:
        return np.ones(len(years))
    return 0.5 ** ((y - 1 - years.to_numpy()) / half_life)


def fit_censored(d: pd.DataFrame, w: np.ndarray, params: dict, n: int, rounds: int = 3):
    """V.fit_censored_lgbm with sample weights."""
    y = d["log_price"].to_numpy().copy()
    up = d["rna"].to_numpy() == 1
    cap = y.copy()
    y[up] = cap[up] - 0.5
    for _ in range(rounds):
        m = V.lgbm(n_estimators=n, **params).fit(d[GB_X], y, sample_weight=w)
        mu = m.predict(d[GB_X])
        sig = float(np.sqrt(np.average((cap - mu)[~up] ** 2, weights=w[~up])))
        a = (cap[up] - mu[up]) / sig
        y[up] = mu[up] - sig * np.exp(stats.norm.logpdf(a) - stats.norm.logcdf(a))
    return m


def one_year(s: pd.DataFrame, y: int, half_life, params: dict, n_per_row: float, ranges: bool) -> pd.DataFrame:
    """Train on years < y, predict year y's sold lots. Returns raw prediction (+ raw quantiles)."""
    past = s[s["sale_year"] < y]
    now = s[(s["sale_year"] == y) & (s["rna"] == 0)]
    w = weights(past["sale_year"], y, half_life)
    n = int(n_per_row * len(past))
    m = fit_censored(past, w, params, n)
    out = now[["sale_year", "sale_level", "age", "sale", "source_file", "session", "is_obs", "sire_n", "log_price"]].assign(
        raw=m.predict(now[GB_X]), month=now["sale"].astype(str).map(S.MG.sale_month))
    if ranges:
        sold = past["rna"].to_numpy() == 0
        ps, ws = past[sold], w[sold]
        for a, col in ((0.10, "q10"), (0.90, "q90")):
            q = V.lgbm(objective="quantile", alpha=a, n_estimators=n, **params).fit(ps[GB_X], ps["log_price"], sample_weight=ws)
            out[col] = q.predict(now[GB_X])
    return out


def within_sale(p: pd.DataFrame, k) -> pd.Series:
    """For each lot: the shrunk average miss (after the yearly shift) on its sale's earlier sessions."""
    if k is None:
        return pd.Series(0.0, index=p.index)
    r = p.assign(res=p["log_price"] - p["base"]).dropna(subset=["session"])
    per = r.groupby(["source_file", "session"])["res"].agg(["sum", "count"]).reset_index().sort_values(["source_file", "session"])
    g = per.groupby("source_file")
    per["cs"] = g["sum"].cumsum() - per["sum"]
    per["cn"] = g["count"].cumsum() - per["count"]
    per["adj"] = per["cs"] / (per["cn"] + k)
    adj = p[["source_file", "session"]].reset_index().merge(per[["source_file", "session", "adj"]], how="left",
                                                             on=["source_file", "session"]).set_index("index")["adj"]
    return adj.reindex(p.index).fillna(0.0)


SIRE_SCOPES = ["all", "age", "age_tier"]


def sire_earlier(p: pd.DataFrame, res: pd.Series, scope: str = "all") -> pd.DataFrame:
    """For each lot: sum and count of `res` over the same sire's lots sold EARLIER the same year:
    at a sale in an earlier month, or in an earlier session of the same sale. Sales in the same
    month (other than the lot's own) don't count, since their order isn't known.
    scope: "all" lots of the sire; "age" only lots of the same age (weanlings don't move
    yearlings); "age_tier" same age and same price tier of sale."""
    q = p[["sale_year", "sire_n", "month", "source_file", "session", "age", "tier"]].assign(res=res)
    q = q[q["sire_n"].astype(str) != ""].reset_index()
    out = []
    for _, g in q.groupby("sale_year"):
        m = g.merge(g, on="sire_n", suffixes=("", "_o"))
        early = (m["month_o"] < m["month"]) | ((m["source_file_o"] == m["source_file"]) & (m["session_o"] < m["session"]))
        if scope in ("age", "age_tier"):
            early &= m["age_o"] == m["age"]
        if scope == "age_tier":
            early &= m["tier_o"] == m["tier"]
        out.append(m[early].groupby("index")["res_o"].agg(["sum", "count"]))
    return pd.concat(out).reindex(p.index).fillna(0.0)


def group(p: pd.DataFrame) -> pd.Series:
    """Range group: price tier x age (0-2)."""
    return p["tier"] * 3 + p["age"].clip(0, 2).astype(int)


def widen(prev: pd.DataFrame, g: int) -> tuple[float, float]:
    """How far to widen the low and high ends so `prev` (out-of-time) would have held 80%:
    by tier x age when that group has MIN_GROUP lots, else by tier."""
    m = group(prev) == g
    if m.sum() < MIN_GROUP:
        m = prev["tier"] == g // 3
    lev = min(0.90 * (1 + 1 / max(m.sum(), 1)), 1.0)
    return (float(np.quantile((prev["q10a"] - prev["log_price"])[m], lev)),
            float(np.quantile((prev["log_price"] - prev["q90a"])[m], lev)))


POS_BINS = [-0.01, 0.2, 0.4, 0.6, 0.8, 1.0]  # session position in its sale: first fifth ... last fifth


def position(p: pd.DataFrame) -> pd.Series:
    """Where the lot's session sits in its sale: 0 = first session, 1 = last (NaN if unknown)."""
    first = p.groupby("source_file")["session"].transform("min")
    last = p.groupby("source_file")["session"].transform("max")
    return ((p["session"] - first) / (last - first)).where(last > first, 0.5)


def finish(p: pd.DataFrame, cuts, k=None, ks=None, sire_sums: pd.DataFrame | None = None, scope: str = "all",
           pos: bool = False) -> pd.DataFrame:
    """Bias shift, position correction, within-sale update, sire update and range widening, each from
    earlier information only.

    pos: the model underprices a sale's early sessions and overprices its late ones every year.
    Without a correction, the within-sale and sire updates read that as "the market is hot"
    and push later sessions too high. With pos=True, the average miss by session position over
    the previous five years is added to every prediction, and the updates work on what's left."""
    p = p.copy()
    p["tier"] = np.digitize(p["sale_level"], cuts)
    p["shift"] = 0.0
    for y in sorted(p["sale_year"].unique()):
        prev3 = p[p["sale_year"].between(y - 3, y - 1)]
        p.loc[p["sale_year"] == y, "shift"] = float((prev3["log_price"] - prev3["raw"]).mean()) if len(prev3) else 0.0
    p["pos_corr"] = 0.0
    if pos:
        p["pos_bin"] = pd.cut(position(p), POS_BINS, labels=False)
        r0 = p["log_price"] - p["raw"] - p["shift"]
        for y in sorted(p["sale_year"].unique()):
            prev = p["sale_year"].between(y - 5, y - 1)
            if not prev.any():
                continue
            means = r0[prev].groupby(p.loc[prev, "pos_bin"]).mean()
            cur = p["sale_year"] == y
            p.loc[cur, "pos_corr"] = p.loc[cur, "pos_bin"].map(means).fillna(0.0)
    p["base"] = p["raw"] + p["shift"] + p["pos_corr"]
    p["adj"] = within_sale(p, k)
    p["sire_adj"] = 0.0
    if ks is not None:
        if sire_sums is None:
            sire_sums = sire_earlier(p, p["log_price"] - p["base"] - p["adj"], scope)
        p["sire_adj"] = sire_sums["sum"] / (sire_sums["count"] + ks)
    p["pred"] = p["base"] + p["adj"] + p["sire_adj"]
    if "q10" in p:
        move = p["pos_corr"] + p["adj"] + p["sire_adj"]
        p["q10a"], p["q90a"] = p["q10"] + move, p["q90"] + move
        p["lo"], p["hi"] = np.nan, np.nan
        p["grp"] = group(p)
        for y in sorted(p["sale_year"].unique()):
            prev5 = p[p["sale_year"].between(y - 5, y - 1)]
            if not len(prev5):
                continue
            for g in sorted(p.loc[p["sale_year"] == y, "grp"].unique()):
                cm = (p["sale_year"] == y) & (p["grp"] == g)
                wl, wh = widen(prev5, g)
                p.loc[cm, "lo"] = np.minimum(p.loc[cm, "q10a"] - wl, p.loc[cm, "pred"])
                p.loc[cm, "hi"] = np.maximum(p.loc[cm, "q90a"] + wh, p.loc[cm, "pred"])
    return p


DOLLAR_BANDS = [0, 10_000, 30_000, 100_000, 300_000, np.inf]
DOLLAR_LABELS = ["under $10k", "$10k-30k", "$30k-100k", "$100k-300k", "$300k+"]


def dollar_scores(te: pd.DataFrame) -> pd.DataFrame:
    """Accuracy in dollars, by the model's predicted price (what a user knows before the sale).
    point estimate in $ = exp(pred) (the median price for a lot like this)."""
    price, pred = np.exp(te["log_price"]), np.exp(te["pred"])
    err = (pred - price).abs()
    band = pd.cut(pred, DOLLAR_BANDS, labels=DOLLAR_LABELS, right=False)
    d = pd.DataFrame({"band": band, "price": price, "pred": pred, "err": err, "rel": err / price})

    def agg(g):
        return pd.Series({"lots": len(g), "median_price": g["price"].median(), "median_$_error": g["err"].median(),
                          "within_25%": (g["rel"] <= 0.25).mean(), "within_50%": (g["rel"] <= 0.50).mean(),
                          "$_error_/_$_sold": g["err"].sum() / g["price"].sum()})
    out = d.groupby("band", observed=True).apply(agg, include_groups=False)
    out.loc["all lots"] = agg(d)
    return out


def postprocess(raw: pd.DataFrame, cuts) -> dict:
    """For the position correction off and on: pick the within-sale k, then the sire update's
    scope and k, on 2019-2020. Keep the best combination; return the finished predictions without
    updates, with the within-sale update, and with everything."""
    tried = {}
    for pos in (False, True):
        ktune = {}
        for k in SHRINK_K:
            f = finish(raw, cuts, k, pos=pos)
            f = f[f["sale_year"].between(2019, 2020)]
            ktune[str(k)] = S.score(f["log_price"], f["pred"])["R2"]
        best_k = max(SHRINK_K, key=lambda k: ktune[str(k)])
        f_k = finish(raw, cuts, best_k, pos=pos)
        res = f_k["log_price"] - f_k["base"] - f_k["adj"]
        stune, sums = {}, {}
        for scope in SIRE_SCOPES:
            sums[scope] = sire_earlier(f_k, res, scope)
            for ks in SIRE_K[1:]:
                f = finish(raw, cuts, best_k, ks, sums[scope], scope, pos=pos)
                f = f[f["sale_year"].between(2019, 2020)]
                stune[f"{scope} k={ks}"] = S.score(f["log_price"], f["pred"])["R2"]
        stune["none"] = ktune[str(best_k)]
        best = max(stune, key=stune.get)
        scope, ks = (None, None) if best == "none" else (best.split()[0], float(best.split("k=")[1]))
        tag = "position correction" if pos else "no position correction"
        print(f"{tag}: within-sale k={best_k} (2019-2020 R2 {ktune[str(best_k)]:.4f}); sire update {best} "
              f"(R2 {stune[best]:.4f})", flush=True)
        tried[pos] = {"pos": pos, "k": best_k, "ktune": ktune, "scope": scope, "ks": ks, "stune": stune,
                      "r2": stune[best], "sums": sums.get(scope)}
    c = max(tried.values(), key=lambda d: d["r2"])
    print(f"chosen on 2019-2020: {'with' if c['pos'] else 'without'} the position correction", flush=True)
    return {**{k: v for k, v in c.items() if k != "sums"}, "tried": {str(k): {kk: vv for kk, vv in v.items() if kk != "sums"}
                                                                  for k, v in tried.items()},
            "p_no": finish(raw, cuts, None), "p_ws": finish(raw, cuts, c["k"], pos=c["pos"]),
            "p": finish(raw, cuts, c["k"], c["ks"], c["sums"], c["scope"] or "all", pos=c["pos"])}


def report(te: pd.DataFrame, label: str) -> dict:
    sc = S.score(te["log_price"], te["pred"], te["lo"], te["hi"])
    print(f"  {label:<44} lots {len(te):>6,}  R2 {sc['R2']:.3f}  miss x{sc['typical_miss_x']:.2f}  "
          f"bias {sc['bias']:+.3f}  80% range {sc['coverage_80']:.1%}")
    return sc


RAW_COLS = ["sale_year", "sale_level", "age", "sale", "source_file", "session", "is_obs", "sire_n", "log_price", "raw",
            "month", "q10", "q90"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--obs", action="store_true")
    ap.add_argument("--hl", type=float, default=None, help="fixed half-life (skips the search)")
    ap.add_argument("--no-final", action="store_true")
    ap.add_argument("--session-x", action="store_true", help="also use session as a model input (tried: worse)")
    ap.add_argument("--post-only", action="store_true",
                    help="reuse the saved out-of-time predictions; redo only the updates, ranges and scores, and "
                         "refresh those settings in the saved model (no retraining)")
    a = ap.parse_args()
    global GB_X
    if a.session_x:
        GB_X = V.GB_X + SESSION_X
    tag = ("_obs" if a.obs else "") + ("_sx" if a.session_x else "")
    v3 = joblib.load(F.ART / "price_v3.joblib")
    params = v3["params"]
    cuts = np.array(v3["tier_cuts"])
    preds_path = F.ART / f"rolling_preds{tag}.pkl"
    tune, best_hl = {}, a.hl

    if a.post_only:
        before = pd.read_pickle(preds_path)
        before.to_pickle(F.ART / f"rolling_preds{tag}_before_post.pkl")
        raw = before[RAW_COLS]
        best_hl = joblib.load(F.ART / f"price_rolling{tag}.joblib")["half_life"]
        print(f"reusing {len(raw):,} saved out-of-time predictions (half-life {best_hl})", flush=True)
    else:
        before = None
        s = frame(a.obs)
        n_tr = len(s[s["sale_year"].between(*S.TRAIN)])
        n_per_row = 262 / n_tr  # v3's tree count (early-stopped on 2019-2020) scaled to the training size
        print(f"lots {len(s):,} (OBS {s['is_obs'].sum():,}); session known {s['session_known'].mean():.0%}; settings {params}",
              flush=True)
        # 1. half-life on 2019-2020 (raw predictions + shift from 2016-2018)
        if a.hl is None:
            for hl in HALF_LIVES:
                p = pd.concat([one_year(s, y, hl, params, n_per_row, ranges=False) for y in range(2016, 2021)])
                f = finish(p, cuts)
                f = f[f["sale_year"] >= 2019]
                tune[str(hl)] = S.score(f["log_price"], f["pred"])
                print(f"half-life {hl}: 2019-2020 R2 {tune[str(hl)]['R2']:.4f}", flush=True)
            best_hl = max(HALF_LIVES, key=lambda h: tune[str(h)]["R2"])
        print(f"half-life: {best_hl}", flush=True)
        # 2. out-of-time predictions for every year from FIRST_YEAR
        parts = []
        for y in range(FIRST_YEAR, 2026):
            parts.append(one_year(s, y, best_hl, params, n_per_row, ranges=True))
            print(f"  {y} done", flush=True)
        raw = pd.concat(parts)

    # 3. within-sale update and sire update (scope and shrinkage picked on 2019-2020)
    pp = postprocess(raw, cuts)
    p, p_no, p_ws = pp["p"], pp["p_no"], pp["p_ws"]
    p.to_pickle(preds_path)
    te, te_no, te_ws = (d[d["sale_year"] >= 2021] for d in (p, p_no, p_ws))
    print("\nHeld-out 2021-2025, sold lots (Keeneland + Fasig-Tipton unless marked):")
    res = {}
    earlier = [("yearly retrain (2026-11-18)", F.ART / "rolling_preds_base.pkl"),
               ("+ within-sale update (cf70efc)", F.ART / f"rolling_preds{'_obs' if a.obs else ''}_cf70efc.pkl")]
    if before is not None:
        earlier.append(("before this run (saved predictions)", None))
    for name, f in earlier:
        b = before if f is None else (pd.read_pickle(f) if f.exists() else None)
        if b is not None:
            b = b[(b["sale_year"] >= 2021) & (b.get("is_obs", 0) == 0)]
            res[name] = report(b, name)
    res["this run, no updates"] = report(te_no[te_no["is_obs"] == 0], "this run, no updates")
    res["this run + within-sale update"] = report(te_ws[te_ws["is_obs"] == 0], "  + within-sale update")
    lab = f"+ {'position correction, ' if pp['pos'] else ''}sire update: {pp['scope']}, k={pp['ks']} (saved)"
    res["this run " + lab] = report(te[te["is_obs"] == 0], "  " + lab)
    if a.obs:
        res["all lots incl. OBS"] = report(te, "all lots incl. OBS")
        res["OBS lots only"] = report(te[te["is_obs"] == 1], "OBS lots only")
    cov_g = te.groupby(["tier", "age"]).apply(lambda d: pd.Series({
        "lots": len(d), "coverage_80": ((d["log_price"] >= d["lo"]) & (d["log_price"] <= d["hi"])).mean()}), include_groups=False)
    print("\n80% range by price tier x age (0 cheap, 1 middle, 2 top-priced):\n" + cov_g.round(3).to_string())
    by_year = te.groupby("sale_year").apply(lambda d: pd.Series({
        "lots": len(d), **S.score(d["log_price"], d["pred"], d["lo"], d["hi"])}), include_groups=False)
    print("\nby year:\n" + by_year.round(3).to_string())
    by_age = te.groupby("age").apply(lambda d: pd.Series({"lots": len(d), **S.score(d["log_price"], d["pred"], d["lo"], d["hi"])}),
                                     include_groups=False)
    print("\nby age:\n" + by_age.round(3).to_string())
    keen = te[te["sale"] == "KEENELAND SEPTEMBER YEARLING SALE"].assign(
        part=lambda d: pd.cut(d["session"], [0, 1, 3, 6, 20], labels=["session 1", "2-3", "4-6", "7+"]))
    cols = [("no updates", te_no)] + ([("before", before)] if before is not None else []) + [("now", te)]
    print("\nKeeneland September by session, R2 / bias: " + " | ".join(c for c, _ in cols))
    by_session = {}
    for part, g in keen.groupby("part", observed=True):
        cells = []
        for c, src in cols:
            gg = src.loc[g.index]
            sc = S.score(gg["log_price"], gg["pred"])
            cells.append(f"{sc['R2']:.3f} / {sc['bias']:+.3f}")
            by_session[f"{part} {c}"] = sc
        print(f"  {part:<10} " + " | ".join(cells))
    cov_t = te.groupby("tier").apply(lambda d: ((d["log_price"] >= d["lo"]) & (d["log_price"] <= d["hi"])).mean(), include_groups=False)
    print("coverage by tier (cheap, middle, top-priced): " + ", ".join(f"{v:.1%}" for v in cov_t))
    print(f"below range {(te['log_price'] < te['lo']).mean():.1%}, above {(te['log_price'] > te['hi']).mean():.1%}")

    # dollar scores
    dol = dollar_scores(te)
    print("\nAccuracy in dollars, by predicted price (all test lots" + (" incl. OBS" if a.obs else "") + "):")
    print(dol.to_string(formatters={"lots": "{:,.0f}".format, "median_price": "${:,.0f}".format,
                                    "median_$_error": "${:,.0f}".format, "within_25%": "{:.0%}".format,
                                    "within_50%": "{:.0%}".format, "$_error_/_$_sold": "{:.0%}".format}))
    if before is not None:
        db = dollar_scores(before[before["sale_year"] >= 2021]).loc["all lots"]
        print(f"before this run, all lots: median $ error ${db['median_$_error']:,.0f}, within 25% {db['within_25%']:.0%}, "
              f"$ error / $ sold {db['$_error_/_$_sold']:.0%}")

    (F.ART / f"price_rolling{tag}_results.json").write_text(json.dumps(
        {"half_life": best_hl, "tune": tune, "position_correction": pp["pos"], "post_tried": pp["tried"],
         "within_sale_k": pp["k"], "k_tune": pp["ktune"], "sire_scope": pp["scope"],
         "sire_k": pp["ks"], "sire_tune": pp["stune"], "test": res, "by_year": by_year.to_dict(orient="index"),
         "by_age": by_age.to_dict(orient="index"), "keeneland_by_session": by_session, "dollars": dol.to_dict(orient="index"),
         "coverage_by_tier": cov_t.tolist(), "coverage_by_tier_age": {f"{t}_{a_}": v for (t, a_), v in cov_g["coverage_80"].items()}},
        indent=2, default=float))

    # 4. the model to use for 2026+: trained on every year, weighted toward the latest
    if a.no_final:
        return
    y = 2026
    last5 = p[p["sale_year"].between(y - 5, y - 1)]
    wid = [widen(last5, g) for g in range(9)]  # group = tier * 3 + age
    pos_table = (p[p["sale_year"].between(y - 5, y - 1)].assign(
        pb=lambda d: pd.cut(position(d), POS_BINS, labels=False), r0=lambda d: d["log_price"] - d["raw"] - d["shift"])
        .groupby("pb")["r0"].mean().to_dict() if pp["pos"] else {})
    post = {"within_sale_k": pp["k"], "sire_k": pp["ks"], "sire_scope": pp["scope"], "position_correction": pos_table,
            "position_bins": POS_BINS,
            "widen_low_by_group": [w[0] for w in wid], "widen_high_by_group": [w[1] for w in wid]}
    out = F.ART / f"price_rolling{tag}.joblib"
    if a.post_only:
        art = joblib.load(out)
        art.update(post)
        joblib.dump(art, out)
        print(f"\nupdated the update and range settings in {out.name} (model unchanged)")
        return
    w = weights(s["sale_year"], y, best_hl)
    n = int(n_per_row * len(s))
    sold = s["rna"].to_numpy() == 0
    prev3 = p[p["sale_year"].between(y - 3, y - 1)]
    joblib.dump({"gb": fit_censored(s, w, params, n),
                 "q10": V.lgbm(objective="quantile", alpha=0.10, n_estimators=n, **params).fit(s[sold][GB_X], s[sold]["log_price"], sample_weight=w[sold]),
                 "q90": V.lgbm(objective="quantile", alpha=0.90, n_estimators=n, **params).fit(s[sold][GB_X], s[sold]["log_price"], sample_weight=w[sold]),
                 "shift": float((prev3["log_price"] - prev3["raw"]).mean()), **post, "tier_cuts": cuts.tolist(),
                 "features": GB_X, "half_life": best_hl, "params": params, "trained_through": y - 1,
                 "categories": {k: list(s[k].cat.categories) for k in V.CATS}}, out)
    print(f"\nsaved {out.name} (for {y} sales)")


if __name__ == "__main__":
    main()

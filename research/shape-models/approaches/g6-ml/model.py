"""G6: ML on history features (LightGBM / XGBoost / RF / ExtraTrees / ridge / elastic net), rolling-origin.

    /tmp/shape-venv/bin/python research/shape-models/approaches/g6-ml/model.py [--configs lgbm_resid,ridge_resid,...]

Each config = algorithm x target (direct VDOT | residual over production_curve) x layout (pooled | split short/long).
For a harness target on day d the model is (re)fitted on feature rows (features.py: targets of all persons + hard GAP
segments) with row day < cutoff, cutoff = d rounded down to a 28-day block (cutoff <= d). The target's own features
come from harness.history(person, d) (strictly before d). So no label or feature from day >= d is ever used.
`predict(person, hist, target)` = the recommended config (for G9).
"""
from __future__ import annotations

import argparse
import functools
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[1]))
import features as F  # noqa: E402
import harness as H  # noqa: E402

warnings.filterwarnings("ignore")
BLOCK = 28
MIN_ROWS = 60
META = {"row_id", "person", "day", "date", "kind", "vdot", "w", "src", "tier", "activity_id", "resid"}


@functools.lru_cache(maxsize=1)
def rows():
    p = F.OUTDIR / "rows.parquet"
    D = pd.read_parquet(p) if p.exists() else F.build()
    return D.sort_values("day").reset_index(drop=True)


def feat_cols(D, drop=()):
    return [c for c in D.columns if c not in META and c not in drop]


# ------------------------------------------------------------------ learners
def make(algo):
    from sklearn.pipeline import make_pipeline
    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import StandardScaler
    imp = lambda: SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True)  # noqa: E731
    if algo == "ridge":
        from sklearn.linear_model import RidgeCV
        return make_pipeline(imp(), StandardScaler(), RidgeCV(alphas=np.logspace(-1, 4, 30)))
    if algo == "enet":
        from sklearn.linear_model import ElasticNetCV
        from sklearn.model_selection import TimeSeriesSplit
        return make_pipeline(imp(), StandardScaler(), ElasticNetCV(l1_ratio=0.5, cv=TimeSeriesSplit(4), alphas=30,
                                                                    max_iter=5000))
    if algo == "rf":
        from sklearn.ensemble import RandomForestRegressor
        return make_pipeline(imp(), RandomForestRegressor(300, min_samples_leaf=5, max_features=0.33, n_jobs=4,
                                                          random_state=0))
    if algo == "et":
        from sklearn.ensemble import ExtraTreesRegressor
        return make_pipeline(imp(), ExtraTreesRegressor(300, min_samples_leaf=5, max_features=0.33, n_jobs=4,
                                                        random_state=0))
    if algo == "lgbm":
        import lightgbm as lgb
        return lgb.LGBMRegressor(n_estimators=300, learning_rate=0.03, num_leaves=8, min_child_samples=15,
                                 subsample=0.8, subsample_freq=1, colsample_bytree=0.7, reg_lambda=5.0, verbose=-1,
                                 random_state=0, n_jobs=1)
    if algo == "xgb":
        import xgboost as xgb
        return xgb.XGBRegressor(n_estimators=300, learning_rate=0.03, max_depth=3, min_child_weight=5, subsample=0.8,
                                colsample_bytree=0.7, reg_lambda=5.0, random_state=0, n_jobs=1)
    raise ValueError(algo)


def fit(model, X, y, w):
    last = model.steps[-1][0] if hasattr(model, "steps") else None
    if last:
        try:
            model.fit(X, y, **{f"{last}__sample_weight": w})
        except TypeError:
            model.fit(X, y)
    else:
        model.fit(X, y, sample_weight=w)
    return model


# ------------------------------------------------------------------ configs
# algo x target (direct | resid) x segs (all hard segments | racelike = only segments of races / time trials | none)
# x race_as (race: own kind incl. race-day segments | interval: race targets read as an interval-scale row at the
# race distance) x split (pooled with distance features | separate short <= 6 km / long models) x clip (|resid| cap)
CONFIGS = {}
ALGOS = ("lgbm", "xgb", "rf", "et", "ridge", "enet")
for algo in ALGOS:
    for tgt in ("direct", "resid"):
        CONFIGS[f"{algo}_{tgt}_allsegs"] = dict(algo=algo, target=tgt, segs="all", race_as="race", split=False, clip=None)
        CONFIGS[f"{algo}_{tgt}_rl"] = dict(algo=algo, target=tgt, segs="racelike", race_as="race", split=False,
                                           clip=4.0 if tgt == "resid" else None)
    CONFIGS[f"{algo}_resid_ivscale"] = dict(algo=algo, target="resid", segs="none", race_as="interval", split=False,
                                            clip=4.0)
SMALL = ["prod_pred", "prod_theta", "prod_slope", "prod_sd", "logkm", "is_race", "kind_interval", "kind_hard_effort",
         "kind_racelike", "iv_max_42_m_prod", "bg_hard_max_90_m_prod", "bg_5k_max_90_m_prod", "race_last_m_prod",
         "race_last_age", "iv_n_90", "km_28", "ctl", "tsb", "acwr", "days_since_hard", "ef_ratio_28_365", "mix_track",
         "mix_indoor_track", "mix_climb_per_km", "t_surf_track", "climb_per_km", "person_adrian", "person_rise"]
for algo in ("lgbm", "ridge", "rf"):
    for sh in (1.0, 0.5):
        CONFIGS[f"{algo}_resid_rl_small" + ("_shrink" if sh < 1 else "")] = dict(
            algo=algo, target="resid", segs="racelike", race_as="race", split=False, clip=4.0, feats="small", shrink=sh)
for algo in ("lgbm", "ridge"):
    for tgt in ("direct", "resid"):
        CONFIGS[f"{algo}_{tgt}_rl_split"] = dict(algo=algo, target=tgt, segs="racelike", race_as="race", split=True,
                                                 clip=4.0 if tgt == "resid" else None)


def prep(X):
    """Derived columns shared by training rows and prediction rows."""
    X = X.copy()
    X["kind_racelike"] = X[["kind_race", "kind_seg_race", "kind_seg_time_trial"]].max(axis=1)
    return X


@functools.lru_cache(maxsize=8192)
def fitted(cfg_name, cutoff, part):
    """Model of config `cfg_name` fitted on rows with day < cutoff (part: all | short | long)."""
    cfg = CONFIGS[cfg_name]
    D = rows()
    D = D[D.day < cutoff]
    if cfg["segs"] == "none":
        D = D[D.src == "target"]
    elif cfg["segs"] == "racelike":
        D = D[(D.src == "target") | D.kind.isin(["seg_race", "seg_time_trial"])]
    if cfg["race_as"] == "interval":
        D = D[D.kind != "race"]
    if part == "short":
        D = D[D.km <= 6.0]
    elif part == "long":
        D = D[D.km > 6.0]
    if cfg["target"] == "resid":
        D = D[D.prod_pred.notna()]
    if len(D) < MIN_ROWS:
        return None
    D = prep(D)
    cols = SMALL if cfg.get("feats") == "small" else feat_cols(D)
    y = (D.resid if cfg["target"] == "resid" else D.vdot).to_numpy(float)
    m = fit(make(cfg["algo"]), D[cols].astype(float), y, D.w.to_numpy(float))
    return m, cols


def predict_cfg(cfg_name, person, hist, target):
    cfg = CONFIGS[cfg_name]
    kind = F.TARGET_KIND[target["tier"]]
    if kind == "race" and cfg["race_as"] == "interval":
        kind = "interval"
    f = F.row_feats(person, hist.day, target, kind)
    cutoff = hist.day - (hist.day % BLOCK)
    part = "all" if not cfg["split"] else ("short" if float(target["km"]) <= 6.0 else "long")
    fm = fitted(cfg_name, cutoff, part)
    if fm is None and part == "long":                     # too few long rows yet: pooled model of the same config
        fm = fitted(cfg_name, cutoff, "all")
    prod, psd = f.get("prod_pred"), f.get("prod_sd")
    if fm is None:
        return None if prod is None or np.isnan(prod) else {"vdot": prod, "sd": psd}   # fallback = production
    m, cols = fm
    x = prep(pd.DataFrame([f]))
    x = x.reindex(columns=cols).astype(float)
    yhat = float(m.predict(x)[0])
    if cfg["target"] == "resid":
        if prod is None or np.isnan(prod):
            return None
        yhat *= cfg.get("shrink", 1.0)
        if cfg["clip"]:
            yhat = float(np.clip(yhat, -cfg["clip"], cfg["clip"]))
        yhat += prod
    return {"vdot": yhat, "sd": float(psd) if psd and not np.isnan(psd) else 2.0}


def make_predict(cfg_name):
    def fn(person, hist, target):
        return predict_cfg(cfg_name, person, hist, target)
    fn.__name__ = cfg_name
    return fn


RECOMMENDED = "ridge_resid_rl_small_shrink"   # closest to production; see REPORT.md (not a recommendation to ship)


def predict(person, hist, target):
    return predict_cfg(RECOMMENDED, person, hist, target)


def run(cfg_names, persons=("adrian", "rise", "patrik")):
    res = [H.evaluate(H.production_curve, persons=persons, name="production_curve"),
           H.evaluate(H.naive_best_recent, persons=persons, name="naive_best_recent_60d")]
    for c in cfg_names:
        t0 = time.time()
        r = H.evaluate(make_predict(c), persons=persons, name=c, verbose=True)
        res.append(r)
        pd.concat([r["preds"].assign(model=r["name"]) for r in res]).to_parquet(F.OUTDIR / "preds.parquet", index=False)
        table(res).to_csv(F.OUTDIR / "scores.csv", index=False)
        print(c, r["headline"], round(time.time() - t0, 1), "s", flush=True)
    return res


def table(res):
    out = []
    for r in res:
        S = r["summary"]; hl = r["headline"]
        g = lambda tier, p: S[(S.tier == tier) & (S.person == p) & (S.band == "all")]  # noqa: E731
        row = {"model": r["name"], "race_adrian": hl["race_mae_adrian"], "race_rise": hl["race_mae_rise"],
               "race_a+r": hl["race_mae_adrian_rise"]}
        pr = g("race", "patrik")
        row["race_patrik"] = float(pr.mae.iloc[0]) if len(pr) else np.nan
        for tier in ("interval", "hard_effort"):
            x = S[(S.tier == tier) & (S.person == "all") & (S.band == "all")]
            row[tier] = float(x.mae.iloc[0]); row[f"{tier}_n"] = int(x.n_pred.iloc[0])
            row[f"{tier}_bias"] = float(x.bias.iloc[0])
        x = S[(S.tier == "race") & (S.person == "all") & (S.band == "all")]
        row["race_bias"] = float(x.bias.iloc[0]); row["race_cover1sd"] = float(x.get("cover_1sd", pd.Series([np.nan])).iloc[0])
        out.append(row)
    return pd.DataFrame(out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--configs", default=",".join(CONFIGS))
    a = ap.parse_args()
    res = run(a.configs.split(","))
    T = table(res)
    pd.concat([r["preds"].assign(model=r["name"]) for r in res]).to_parquet(F.OUTDIR / "preds.parquet", index=False)
    T.to_csv(F.OUTDIR / "scores.csv", index=False)
    with pd.option_context("display.width", 250, "display.float_format", "{:.2f}".format):
        print(T.to_string(index=False))

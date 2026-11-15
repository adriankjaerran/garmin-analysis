"""G6 feature importance: TreeSHAP (LightGBM pred_contrib) on models fitted on all rows (insight only, not a score),
grouped by feature family; ridge standardized coefficients of the covariates (surface / incline / device / kind).

    /tmp/shape-venv/bin/python research/shape-models/approaches/g6-ml/importance.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[1]))
import model as M  # noqa: E402
import features as F  # noqa: E402


def family(c):
    rules = [("prod_", "production state/pred"), ("iv_", "interval scores"), ("race_", "past races"),
             ("he_", "past hard efforts"), ("bg_", "GAP best efforts"), ("br_", "raw best efforts"),
             ("ef_", "HR efficiency"), ("hrr_", "HR efficiency"), ("decoup", "HR efficiency"),
             ("hr_trust", "HR device (history)"), ("strap_", "HR device (history)"), ("t_dev_", "HR device (target)"),
             ("t_trust", "HR device (target)"), ("mix_", "surface/terrain mix (history)"),
             ("t_surf_", "surface (target)"), ("climb_per_km", "incline (target)"), ("t_grade", "incline (target)"),
             ("flat_course", "incline (target)"), ("kind_", "effort kind"), ("is_race", "effort kind"),
             ("km_", "load"), ("km", "distance"), ("logkm", "distance"), ("person_", "person"),
             ("ctl", "load"), ("atl", "load"), ("tsb", "load"), ("acwr", "load"), ("n_", "load"), ("h_28", "load"),
             ("all_h", "load"), ("ski_h", "load"), ("days_since", "recency"), ("history_days", "recency")]
    for k, v in rules:
        if k == "km":
            if c == "km":
                return v
        elif c.startswith(k):
            return v
    return "load" if c.startswith("km_") else "other"


def shap_table(cfg_name):
    import lightgbm as lgb  # noqa: F401
    cfg = M.CONFIGS[cfg_name]
    D = M.rows()
    if cfg["segs"] == "racelike":
        D = D[(D.src == "target") | D.kind.isin(["seg_race", "seg_time_trial"])]
    elif cfg["segs"] == "none":
        D = D[D.src == "target"]
    if cfg["target"] == "resid":
        D = D[D.prod_pred.notna()]
    D = M.prep(D)
    cols = M.SMALL if cfg.get("feats") == "small" else M.feat_cols(D)
    y = (D.resid if cfg["target"] == "resid" else D.vdot).to_numpy(float)
    m = M.fit(M.make("lgbm"), D[cols].astype(float), y, D.w.to_numpy(float))
    contrib = m.predict(D[cols].astype(float), pred_contrib=True)[:, :-1]
    imp = pd.Series(np.abs(contrib).mean(0), index=cols).sort_values(ascending=False)
    fam = imp.groupby(imp.index.map(family)).sum().sort_values(ascending=False)
    # race-like rows only
    rl = (D.kind_racelike == 1).to_numpy() | (D.kind == "hard_effort").to_numpy()
    imp_rl = pd.Series(np.abs(contrib[rl]).mean(0), index=cols)
    fam_rl = imp_rl.groupby(imp_rl.index.map(family)).sum()
    return imp, fam, fam_rl, len(D)


def ridge_coefs(cfg_name):
    cfg = M.CONFIGS[cfg_name]
    D = M.rows()
    if cfg["segs"] == "racelike":
        D = D[(D.src == "target") | D.kind.isin(["seg_race", "seg_time_trial"])]
    if cfg["target"] == "resid":
        D = D[D.prod_pred.notna()]
    D = M.prep(D)
    cols = M.feat_cols(D)
    y = (D.resid if cfg["target"] == "resid" else D.vdot).to_numpy(float)
    m = M.fit(M.make("ridge"), D[cols].astype(float), y, D.w.to_numpy(float))
    names = m[:-1].get_feature_names_out(cols) if hasattr(m[:-1], "get_feature_names_out") else None
    coef = pd.Series(m[-1].coef_, index=names if names is not None else range(len(m[-1].coef_)))
    return coef, float(m[-1].alpha_)


if __name__ == "__main__":
    out = []
    for cfg in ("lgbm_direct_allsegs", "lgbm_resid_allsegs", "lgbm_resid_rl"):
        imp, fam, fam_rl, n = shap_table(cfg)
        print(f"\n=== {cfg} (n={n}) mean |SHAP| by family (all rows | race-like + hard_effort rows)")
        print(pd.DataFrame({"all": fam, "racelike": fam_rl}).sort_values("all", ascending=False).round(2).to_string())
        print("top 15 features:\n", imp.head(15).round(2).to_string())
        out.append(imp.rename(cfg))
    pd.concat(out, axis=1).to_csv(F.OUTDIR / "shap_importance.csv")
    for cfg in ("ridge_direct_allsegs", "ridge_resid_rl"):
        coef, alpha = ridge_coefs(cfg)
        sel = coef[coef.index.str.contains("t_surf|t_dev|climb_per_km|t_grade|flat_course|kind_|mix_|ef_|strap|is_race")]
        print(f"\n=== {cfg} ridge alpha={alpha:.1f}: standardized coefs (VDOT per sd) of covariates")
        print(sel.sort_values().round(2).to_string())

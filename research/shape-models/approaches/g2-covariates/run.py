"""G2 runner: harness evaluation of model variants + effect table.

    /tmp/shape-venv/bin/python research/shape-models/approaches/g2-covariates/run.py eval [variant ...]
    /tmp/shape-venv/bin/python research/shape-models/approaches/g2-covariates/run.py effects
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1])); sys.path.insert(0, str(HERE))
import common as C  # noqa: E402
from harness import evaluate, compare, production_curve, naive_best_recent  # noqa: E402
from model import Config, Predictor, Fit, prepare  # noqa: E402

OUTD = C.OUT / "g2"
OUTD.mkdir(parents=True, exist_ok=True)

BASE = Config()
VARIANTS = {
    "g2": BASE,
    "g2_smooth": replace(BASE, mode="smooth"),
    "g2_sym": replace(BASE, robust="sym"),
    "g2_nokappa": replace(BASE, kappa=False),
    "g2_strava": replace(BASE, gap="strava"),
    "g2_raw": replace(BASE, gap="raw"),
    "g2_sub": replace(BASE, include_sub=True),
    "g2_tau0": replace(BASE, tau=0.0),
    "g2_tau05": replace(BASE, tau=0.5),
    "g2_tau2": replace(BASE, tau=2.0),
    "g2_tau5": replace(BASE, tau=5.0),
    "g2_noeffects": replace(BASE, effects=("race",), pooled=(), kappa=False),
    "g2_norace": replace(BASE, effects=tuple(e for e in BASE.effects if e not in ("race", "race_track")),
                         pooled=tuple(e for e in BASE.pooled if e != "race")),
    "g2_noheat": replace(BASE, effects=tuple(e for e in BASE.effects if e != "heat"),
                         pooled=tuple(e for e in BASE.pooled if e != "heat")),
    "g2_q02": replace(BASE, q_theta=0.02),
    "g2_q10": replace(BASE, q_theta=0.10),
    "g2_sig15": replace(BASE, sig_mult=1.5),
}
CORE = replace(BASE, use_eff=False, effects=("race", "int_road", "int_indoor", "kappa", "heat"),
               pooled=("race", "int_road", "int_indoor", "heat"))
VARIANTS.update({
    "g2_core": CORE,                                                    # hard efforts out of the fitness fit
    "g2_core_noeffects": replace(CORE, effects=("race",), pooled=("race",), kappa=False),
    "g2_race_heat": replace(BASE, effects=("race", "heat"), pooled=("race", "heat"), kappa=False),
})


def row(res):
    S = res["summary"]; out = {"model": res["name"]}
    def g(t, p, b="all", col="mae"):
        x = S[(S.tier == t) & (S.person == p) & (S.band == b)]
        return float(x.iloc[0][col]) if len(x) else np.nan
    out["race_adrian"] = g("race", "adrian"); out["race_rise"] = g("race", "rise")
    out["race_a+r"] = res["headline"]["race_mae_adrian_rise"]
    out["race_bias"] = float((lambda P: (P.pred - P.actual).mean())(res["preds"][(res["preds"].tier == "race") &
                                                                                  res["preds"].person.isin(["adrian", "rise"])]))
    out["race_patrik"] = g("race", "patrik")
    out["interval"] = res["headline"]["interval_mae"]; out["hard_effort"] = res["headline"]["hard_effort_mae"]
    out["race_nll"] = g("race", "all", col="nll") if "nll" in S else np.nan
    out["race_cov1"] = g("race", "all", col="cover_1sd") if "cover_1sd" in S else np.nan
    return out


def run_eval(names):
    rows, results = [], []
    for nm in names:
        if nm == "production_curve":
            r = evaluate(production_curve, name=nm)
        elif nm == "naive":
            r = evaluate(naive_best_recent, name="naive_best_recent_60d")
        elif nm.startswith("prodcorr"):
            from prodcorr import ProdCorr, VARIANTS as PV
            r = evaluate(ProdCorr(name=nm, **PV[nm]), name=nm)
        else:
            r = evaluate(Predictor(VARIANTS[nm]), name=nm)
        results.append(r); rows.append(row(r))
        print(pd.DataFrame([rows[-1]]).to_string(index=False, float_format="{:.2f}".format), flush=True)
        r["preds"].assign(model=r["name"]).to_parquet(OUTD / f"preds_{r['name']}.parquet", index=False)
    return pd.DataFrame(rows), results


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "eval":
        names = sys.argv[2:] or ["production_curve", "g2"]
        df, _ = run_eval(names)
        print(df.to_string(index=False, float_format="{:.2f}".format))


def full_fit(cfg):
    O = prepare(cfg)
    return Fit(cfg, O).run()


if __name__ == "__main__" and sys.argv[1] == "effects":
    names = sys.argv[2:] or ["g2"]
    pd.set_option("display.width", 200)
    for nm in names:
        f = full_fit(VARIANTS[nm])
        print("==", nm); print(f.effects().to_string(index=False, float_format="{:.2f}".format))

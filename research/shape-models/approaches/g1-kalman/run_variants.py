"""Evaluate G1 variants through harness.evaluate; writes results/*.csv (summary metrics only, no raw data).

    /tmp/shape-venv/bin/python research/shape-models/approaches/g1-kalman/run_variants.py [group ...]
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[1]))
import g1  # noqa: E402
import harness as H  # noqa: E402
from g1 import Cfg, X_HM, T_DEFAULT  # noqa: E402

OUT = HERE / "results"; OUT.mkdir(exist_ok=True)
B = Cfg()
X10 = 1.0

T_SYM = (("race", (4.0, 4.0, 1.0, 1.0)), ("hard", (4.0, 4.0, 1.0, 1.0)))
T_SKEW = (("race", (4.0, 4.0, 1.0, 1.0)), ("hard", (1.0, 4.0, 1.0, 1.0)))
T_SKEW2 = (("race", (4.0, 4.0, 1.0, 1.0)), ("hard", (1.0, 4.0, 1.5, 1.0)))   # wider lower scale: sub-max sessions
T_SKEW3 = (("race", (6.0, 6.0, 1.0, 1.0)), ("hard", (2.0, 6.0, 1.0, 1.0)))
T_CAUCHY = (("race", (1.0, 1.0, 1.0, 1.0)), ("hard", (1.0, 1.0, 1.0, 1.0)))

GROUPS = {
    "base": {
        "curve2 (=production)": B,
        "curve2 flat slope (slope_sd 0.01)": replace(B, slope_sd=0.01, q_slope=1e-6),
    },
    "anchors": {
        # separate short / long fitness, correlated random walk, linear interpolation in x
        **{f"SL lin rho{r} k0": replace(B, structure="anchors", rho=r, q_anchor=0.05) for r in (0.0, 0.5, 0.8, 0.95, 1.0)},
        # with mean reversion of the gap (time constant ~ 1/kappa days)
        **{f"SL lin rho{r} k1/900": replace(B, structure="anchors", rho=r, kappa=1 / 900) for r in (0.0, 0.5, 0.8)},
        **{f"SL lin rho{r} k1/200": replace(B, structure="anchors", rho=r, kappa=1 / 200) for r in (0.0, 0.5, 0.8)},
        # separate disciplines: each item updates only its nearest anchor (production v1 style)
        **{f"SL nearest rho{r}": replace(B, structure="anchors", rho=r, hmap="nearest") for r in (0.0, 0.5, 0.8, 0.95)},
        # 3 anchors 5k / 10k / half
        **{f"3A lin rho{r}": replace(B, structure="anchors", anchors=(0.0, X10, X_HM), rho=r) for r in (0.5, 0.8, 0.95)},
        "3A lin rho0.8 k1/900": replace(B, structure="anchors", anchors=(0.0, X10, X_HM), rho=0.8, kappa=1 / 900),
    },
    "curve3": {
        **{f"curve3 csd{c} qc{q}": replace(B, structure="curve3", curv_sd=c, q_curv=q)
           for c in (0.25, 0.5, 1.0) for q in (0.0001, 0.0005)},
    },
    "curvevol": {f"curvevol volsd{v}": replace(B, structure="curvevol", vol_sd=v) for v in (0.5, 1.0, 2.0)},
    "llt": {
        **{f"llt vsd{v} tau{t} qth{q}": replace(B, structure="llt", vel_sd=v, vel_tau=t, q_theta=q)
           for v in (0.005, 0.01, 0.02) for t in (30, 90) for q in (0.02, 0.05)},
    },
    "load": {
        **{f"load a{a}": replace(B, load_a=a) for a in (0.5, 1, 2, 4)},
        **{f"gap q{g}": replace(B, gap_q=g) for g in (0.05, 0.1, 0.2)},
        **{f"q_theta {q}": replace(B, q_theta=q) for q in (0.02, 0.03, 0.08, 0.12)},
        **{f"q_theta 0.03 load a{a}": replace(B, q_theta=0.03, load_a=a) for a in (1, 2, 4)},
    },
    "robust": {
        "t sym nu4": replace(B, robust="t", t=T_SYM),
        "t skew nu(1,4)": replace(B, robust="t", t=T_SKEW),
        "t skew nu(1,4) lo-scale1.5": replace(B, robust="t", t=T_SKEW2),
        "t skew nu(2,6) race6": replace(B, robust="t", t=T_SKEW3),
        "t cauchy": replace(B, robust="t", t=T_CAUCHY),
        "gauss (no robust)": replace(B, robust="t", t=(("race", (1e6, 1e6, 1, 1)), ("hard", (1e6, 1e6, 1, 1)))),
    },
}


def metrics(res, cfg=None):
    S = res["summary"]; P = res["preds"]

    def g(tier, person="all", band="all", col="mae"):
        x = S[(S.tier == tier) & (S.person == person) & (S.band == band)]
        return float(x.iloc[0][col]) if len(x) and col in x else np.nan
    ar = P[P.person.isin(["adrian", "rise"])]

    def mae(mask):
        x = ar[mask & ar.pred.notna()]
        return float((x.pred - x.actual).abs().mean()) if len(x) else np.nan
    row = {"race_adrian": g("race", "adrian"), "race_rise": g("race", "rise"),
           "race_ar": res["headline"]["race_mae_adrian_rise"], "race_patrik": g("race", "patrik"),
           "race_bias_ar": float((ar[ar.tier == "race"].pred - ar[ar.tier == "race"].actual).mean()),
           "race_short": mae((ar.tier == "race") & (ar.band == "short")),
           "race_10k": mae((ar.tier == "race") & (ar.band == "10k")),
           "race_HM": mae((ar.tier == "race") & (ar.band == "HM+")),
           "int_ar": mae(ar.tier == "interval"), "int_adrian": g("interval", "adrian"), "int_rise": g("interval", "rise"),
           "int_all": g("interval"), "hard_ar": mae(ar.tier == "hard_effort"), "hard_all": g("hard_effort"),
           "race_nll": g("race", col="nll"), "race_cov": g("race", col="cover_1sd"),
           "int_nll": g("interval", col="nll"), "int_cov": g("interval", col="cover_1sd")}
    if cfg is not None:
        for p in ("adrian", "rise"):
            e, ll, n = g1.one_step_ll(p, cfg)
            row[f"fc_mae_{p}"] = e; row[f"fc_ll_{p}"] = ll
    return row


def run(groups, persons=("adrian", "rise", "patrik")):
    rows, preds = [], []
    for gname in groups:
        for name, cfg in GROUPS[gname].items():
            res = H.evaluate(g1.make_predict(cfg, name), persons=persons, name=name)
            rows.append({"group": gname, "model": name, **metrics(res, cfg)})
            preds.append(res["preds"].assign(model=name))
            r = rows[-1]
            print(f"{gname:9s} {name:34s} race a {r['race_adrian']:.2f} r {r['race_rise']:.2f} ar {r['race_ar']:.2f} "
                  f"| int {r['int_ar']:.2f} hard {r['hard_ar']:.2f} | fc ll a {r['fc_ll_adrian']:.3f} "
                  f"r {r['fc_ll_rise']:.3f}", flush=True)
    return pd.DataFrame(rows), pd.concat(preds)


def baselines(persons=("adrian", "rise", "patrik")):
    rows = []
    for name, fn in (("production_curve", H.production_curve), ("naive_best_recent_60d", H.naive_best_recent),
                     ("production_flat", H.production_flat)):
        res = H.evaluate(fn, persons=persons, name=name)
        rows.append({"group": "baseline", "model": name, **metrics(res)})
    return pd.DataFrame(rows)


if __name__ == "__main__":
    groups = sys.argv[1:] or list(GROUPS)
    df, P = run(groups)
    tag = "_".join(groups) if sys.argv[1:] else "all"
    df.to_csv(OUT / f"variants_{tag}.csv", index=False)
    bl = baselines(); bl.to_csv(OUT / "baselines.csv", index=False)
    with pd.option_context("display.width", 250, "display.max_columns", 40, "display.float_format", "{:.2f}".format):
        print(pd.concat([bl, df]).to_string(index=False))

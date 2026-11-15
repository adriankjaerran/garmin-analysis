"""Final comparison tables for REPORT.md (markdown to results/final_tables.md).

    /tmp/shape-venv/bin/python research/shape-models/approaches/g1-kalman/final_tables.py
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
from g1 import Cfg, X_HM  # noqa: E402
from run_variants import metrics  # noqa: E402
from run_round3 import GRID  # noqa: E402

OUT = HERE / "results"
B = Cfg()
M = replace(B, q_slope=0.04, slope_sd=2.0)
TSK = (("race", (4.0, 4.0, 1, 1)), ("hard", (1.0, 30.0, 1, 1)))
MODELS = {
    "production_curve (harness)": H.production_curve,
    "naive_best_recent_60d": H.naive_best_recent,
    "production_flat": H.production_flat,
    "G1 curve2 = production re-impl.": B,
    "SL: separate 5k/HM states, rho 0.8, no reversion": replace(B, structure="anchors", rho=0.8),
    "SL: rho 0, reversion 1/200 d": replace(B, structure="anchors", rho=0.0, kappa=1 / 200),
    "SL: rho 0.5, reversion 1/900 d": replace(B, structure="anchors", rho=0.5, kappa=1 / 900),
    "SL nearest (separate disciplines), rho 0.5": replace(B, structure="anchors", rho=0.5, hmap="nearest"),
    "3 anchors 5k/10k/HM, rho 0.8, rev 1/900": replace(B, structure="anchors", anchors=(0.0, 1.0, X_HM), rho=0.8,
                                                       kappa=1 / 900),
    "curve3 (curv sd 0.5, q 5e-4)": replace(B, structure="curve3", curv_sd=0.5, q_curv=0.0005),
    "curvevol (slope ~ load, b sd 0.5)": replace(B, structure="curvevol", vol_sd=0.5),
    "LLT (vel sd 0.01, tau 30, q_theta 0.02)": replace(B, structure="llt", vel_sd=0.01, vel_tau=30, q_theta=0.02),
    "load: q up when load low (a 1)": replace(B, load_a=1.0),
    "load: q up when load high (a 1)": replace(B, load_hi=1.0),
    "gap: q +0.1/day after 7 d without run": replace(B, gap_q=0.1),
    "Student-t symmetric nu 4": replace(B, robust="t", t=(("race", (4.0, 4.0, 1, 1)), ("hard", (4.0, 4.0, 1, 1)))),
    "skew-t hard nu(1 below, 30 above)": replace(B, robust="t", t=TSK),
    "no robust weights (Gaussian)": replace(B, robust="t", t=(("race", (1e6, 1e6, 1, 1)), ("hard", (1e6, 1e6, 1, 1)))),
    "**mobile slope (q_s 0.04, sd 2.0)**": M,
    "auto: curve2 slope grid by history fc log-lik": "AUTO",
    "mobile slope + skew-t": replace(M, robust="t", t=TSK),
    "mobile slope + load-high q": replace(M, load_hi=1.0),
}


def fmt(x, d=2):
    return "–" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{d}f}"


if __name__ == "__main__":
    rows, errs = [], {}
    for name, m in MODELS.items():
        if m == "AUTO":
            fn = g1.make_auto_predict(GRID, name)
        elif isinstance(m, Cfg):
            fn = g1.make_predict(m, name)
        else:
            fn = m
        res = H.evaluate(fn, name=name)
        row = metrics(res)
        row["model"] = name; rows.append(row)
        P = res["preds"]; r = P[(P.tier == "race") & P.person.isin(["adrian", "rise"])]
        errs[name] = (r.pred - r.actual).round(2).to_numpy(); errs["race"] = (r.person + " " + r.note).to_numpy()
        print(name, round(row["race_ar"], 3), flush=True)
    D = pd.DataFrame(rows)
    D.to_csv(OUT / "final_metrics.csv", index=False)
    L = ["| model | race adrian (5) | race rise (5) | **race a+r (10)** | bias a+r | race 5k (1) | race 10k (5) | race HM (4) "
         "| race NLL / 1-sd cov | patrik race (1) | interval a+r (160) | interval all (164) | hard_effort a+r (15) | hard_effort all (19) |",
         "|" + "---|" * 14]
    for _, r in D.iterrows():
        L.append(f"| {r.model} | {fmt(r.race_adrian)} | {fmt(r.race_rise)} | **{fmt(r.race_ar)}** | {fmt(r.race_bias_ar, 2)} | "
                 f"{fmt(r.race_short)} | {fmt(r.race_10k)} | {fmt(r.race_HM)} | {fmt(r.race_nll)} / {fmt(r.race_cov)} | "
                 f"{fmt(r.race_patrik)} | {fmt(r.int_ar)} | {fmt(r.int_all)} | {fmt(r.hard_ar)} | {fmt(r.hard_all)} |")
    E = pd.DataFrame(errs).set_index("race").T
    keep = ["production_curve (harness)", "SL: rho 0, reversion 1/200 d", "skew-t hard nu(1 below, 30 above)",
            "**mobile slope (q_s 0.04, sd 2.0)**", "auto: curve2 slope grid by history fc log-lik"]
    E2 = E.loc[keep]
    L += ["", "Per-race error (pred − actual, VDOT):", "", "| model | " + " | ".join(E2.columns) + " |",
          "|" + "---|" * (len(E2.columns) + 1)]
    for nm, rr in E2.iterrows():
        L.append(f"| {nm} | " + " | ".join(f"{v:+.2f}" for v in rr.to_numpy()) + " |")
    (OUT / "final_tables.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))

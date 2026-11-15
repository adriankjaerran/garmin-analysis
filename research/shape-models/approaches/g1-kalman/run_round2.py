"""Round 2: extra robust / load variants and time-forward auto-selection of the curve hyperparameters.

    /tmp/shape-venv/bin/python research/shape-models/approaches/g1-kalman/run_round2.py
"""
from __future__ import annotations

import collections
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[1]))
import g1  # noqa: E402
import harness as H  # noqa: E402
from g1 import Cfg, X_HM  # noqa: E402
from run_variants import metrics, GROUPS  # noqa: E402

OUT = HERE / "results"
B = Cfg()
V = {
    "t skew nu(1,30)": replace(B, robust="t", t=(("race", (4.0, 4.0, 1, 1)), ("hard", (1.0, 30.0, 1, 1)))),
    "t skew nu(0.5,30)": replace(B, robust="t", t=(("race", (4.0, 4.0, 1, 1)), ("hard", (0.5, 30.0, 1, 1)))),
    "t skew nu(1,30) race30": replace(B, robust="t", t=(("race", (30.0, 30.0, 1, 1)), ("hard", (1.0, 30.0, 1, 1)))),
    "t skew nu(2,10)": replace(B, robust="t", t=(("race", (4.0, 4.0, 1, 1)), ("hard", (2.0, 10.0, 1, 1)))),
    "load_hi a1": replace(B, load_hi=1.0),
    "load_hi a2": replace(B, load_hi=2.0),
    "q_theta 0.03 load_hi a2": replace(B, q_theta=0.03, load_hi=2.0),
}
CURVE2 = [replace(B, q_slope=qs, slope_sd=sd) for qs in (0.001, 0.005, 0.01, 0.02, 0.04) for sd in (1.0, 1.5, 2.0, 3.0)
          if qs < sd * sd]
CURVE2Q = [replace(c, q_theta=q) for c in CURVE2 for q in (0.03, 0.05, 0.08)]
SLC = [replace(B, structure="anchors", rho=r, kappa=k, q_anchor=q) for r in (0.0, 0.5, 0.8)
       for k in (1 / 100, 1 / 200, 1 / 400, 1 / 900) for q in (0.03, 0.05)]

if __name__ == "__main__":
    rows = []
    which = sys.argv[1:] or ["fixed", "auto"]
    if "fixed" in which:
        for name, cfg in V.items():
            res = H.evaluate(g1.make_predict(cfg, name), name=name)
            rows.append({"model": name, **metrics(res, cfg)}); r = rows[-1]
            print(f"{name:30s} race a {r['race_adrian']:.2f} r {r['race_rise']:.2f} ar {r['race_ar']:.2f} | int "
                  f"{r['int_ar']:.2f} hard {r['hard_ar']:.2f} | fc ll a {r['fc_ll_adrian']:.3f} r {r['fc_ll_rise']:.3f}",
                  flush=True)
    if "auto" in which:
        for name, cands in (("auto curve2 (q_slope x slope_sd)", CURVE2), ("auto curve2 (+q_theta)", CURVE2Q),
                            ("auto SL (rho x kappa x q)", SLC), ("auto curve2 + SL", CURVE2 + SLC)):
            fn = g1.make_auto_predict(cands, name)
            res = H.evaluate(fn, name=name)
            rows.append({"model": name, **metrics(res)}); r = rows[-1]
            ch = collections.Counter(fn.chosen.values())
            top = [(cands[i].structure, cands[i].q_slope, cands[i].slope_sd, cands[i].q_theta, cands[i].rho,
                    round(cands[i].kappa, 4), n) for i, n in ch.most_common(4)]
            last = cands[fn.chosen[max(fn.chosen)]]
            print(f"{name:30s} race a {r['race_adrian']:.2f} r {r['race_rise']:.2f} ar {r['race_ar']:.2f} | int "
                  f"{r['int_ar']:.2f} hard {r['hard_ar']:.2f} | chosen {top} | last {last}", flush=True)
    tag = "_".join(which)
    pd.DataFrame(rows).to_csv(OUT / f"round2_{tag}.csv", index=False)

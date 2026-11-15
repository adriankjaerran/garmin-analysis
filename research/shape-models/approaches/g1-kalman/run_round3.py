"""Round 3: extended curve2 slope grid (q_slope beyond 0.04), history-only fc log-lik per person, auto-selection on
the extended grid.

    /tmp/shape-venv/bin/python research/shape-models/approaches/g1-kalman/run_round3.py
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
from g1 import Cfg  # noqa: E402
from run_variants import metrics  # noqa: E402

OUT = HERE / "results"
B = Cfg()
GRID = [replace(B, q_slope=qs, slope_sd=sd) for qs in (0.001, 0.005, 0.01, 0.02, 0.04, 0.08, 0.16)
        for sd in (1.0, 1.5, 2.0, 3.0, 4.0) if qs < 0.5 * sd * sd]

if __name__ == "__main__":
    rows = []
    for cfg in GRID:
        name = f"curve2 qs{cfg.q_slope} ssd{cfg.slope_sd}"
        if cfg.q_slope >= 0.08 or cfg.slope_sd == 4.0:
            res = H.evaluate(g1.make_predict(cfg, name), name=name)
            m = metrics(res, cfg)
        else:
            m = {}
            for p in ("adrian", "rise"):
                e, ll, n = g1.one_step_ll(p, cfg); m[f"fc_ll_{p}"] = ll; m[f"fc_mae_{p}"] = e
        rows.append({"model": name, "q_slope": cfg.q_slope, "slope_sd": cfg.slope_sd, **m})
        r = rows[-1]
        print(name, {k: round(v, 3) for k, v in r.items() if k in ("race_adrian", "race_rise", "race_ar", "int_ar",
              "hard_ar", "fc_ll_adrian", "fc_ll_rise")}, flush=True)
    fn = g1.make_auto_predict(GRID, "auto curve2 ext")
    res = H.evaluate(fn, name="auto curve2 ext")
    rows.append({"model": "auto curve2 ext", **metrics(res)})
    ch = collections.Counter(fn.chosen.values())
    print("auto", {k: round(v, 3) for k, v in rows[-1].items() if isinstance(v, float)},
          [(GRID[i].q_slope, GRID[i].slope_sd, n) for i, n in ch.most_common(5)])
    pd.DataFrame(rows).to_csv(OUT / "round3_curve2_ext.csv", index=False)

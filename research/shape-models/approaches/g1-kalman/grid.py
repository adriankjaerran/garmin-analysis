"""Hyperparameter grids for G1 variants: race MAE surface, one-step forecast log-lik (history only), and an honest
leave-one-race-out (LORO) estimate of 'tune on the races' (pick the grid point best on the other 9 races, score it on
the held-out one).

    /tmp/shape-venv/bin/python research/shape-models/approaches/g1-kalman/grid.py <grid-name>
"""
from __future__ import annotations

import itertools
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

OUT = HERE / "results"; OUT.mkdir(exist_ok=True)
B = Cfg()
GRIDS = {
    "curve2": {f"curve2 qs{qs} ssd{sd}": replace(B, q_slope=qs, slope_sd=sd)
               for qs in (0.001, 0.005, 0.01, 0.02, 0.04) for sd in (1.0, 1.5, 2.0, 3.0) if qs < sd * sd},
    "sl": {f"SL rho{r} k1/{int(1/k)} qa{q}": replace(B, structure="anchors", rho=r, kappa=k, q_anchor=q)
           for r in (0.0, 0.5, 0.8) for k in (1 / 100, 1 / 200, 1 / 400, 1 / 900) for q in (0.03, 0.05)},
    "robust": {f"prod-robust hard_lo{lo} hard_hi{hi} race{rc}": ("ROB", lo, hi, rc)
               for lo in (0.5, 1.0, 1.5, 2.5) for hi in (2.0, 2.5, 4.0) for rc in (2.0, 2.5, 4.0)},
}


def run_grid(name, persons=("adrian", "rise", "patrik")):
    rows, races = [], []
    for label, cfg in GRIDS[name].items():
        if isinstance(cfg, tuple) and cfg[0] == "ROB":
            _, lo, hi, rc = cfg
            g1.PROD_ROBUST.update({"hard": (lo, hi), "race": (rc, rc)})
            g1._FITS.clear()
            cfg = B
        res = H.evaluate(g1.make_predict(cfg, label), persons=persons, name=label)
        m = metrics(res, cfg)
        rows.append({"model": label, **m})
        P = res["preds"]; r = P[(P.tier == "race") & P.person.isin(["adrian", "rise"])]
        races.append(pd.Series((r.pred - r.actual).abs().to_numpy(), index=r.target_id.to_numpy(), name=label))
        print(f"{label:40s} race a {m['race_adrian']:.2f} r {m['race_rise']:.2f} ar {m['race_ar']:.2f} | int "
              f"{m['int_ar']:.2f} hard {m['hard_ar']:.2f} | fc ll a {m['fc_ll_adrian']:.3f} r {m['fc_ll_rise']:.3f}",
              flush=True)
    g1.PROD_ROBUST.update({"race": (2.5, 2.5), "hard": (1.0, 2.5)})
    df = pd.DataFrame(rows)
    E = pd.concat(races, axis=1)          # races x configs (abs errors)
    loro = []
    for rid in E.index:
        others = E.drop(index=rid).mean()
        best = others.idxmin()
        loro.append(E.loc[rid, best])
    fc = (df.fc_ll_adrian + df.fc_ll_rise).to_numpy()
    by_fc = df.model.iloc[int(np.argmax(fc))]
    summ = {"grid": name, "n_cfg": len(df), "race_ar_min": E.mean().min(), "race_ar_max": E.mean().max(),
            "race_ar_median": E.mean().median(), "loro_tuned_race_mae": float(np.mean(loro)),
            "fc_selected": by_fc, "fc_selected_race_mae": float(E[by_fc].mean())}
    df.to_csv(OUT / f"grid_{name}.csv", index=False)
    return df, summ


if __name__ == "__main__":
    for nm in sys.argv[1:]:
        df, s = run_grid(nm)
        print(s)
        pd.Series(s).to_csv(OUT / f"grid_{nm}_summary.csv")

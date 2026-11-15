"""Score the pre-registered combined model, its ablations and the tempo variants (harness.evaluate, all persons).

    /tmp/shape-venv/bin/python research/shape-models/approaches/g9-combined/run_eval.py
Writes data/_research/shape/g9/configs.parquet (per-target preds) and results/configs.csv (metrics).
"""
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import combined as CB  # noqa: E402
import common as C  # noqa: E402
import harness as H  # noqa: E402

S = CB.SPEC
P0 = CB.PROD
B = dict(bests=True); Sx = dict(q_slope=0.04, slope_sd=2.0); M = dict(slope_mean=0.75); E = dict(shortfall=True)
CONFIGS = {
    "production": P0,
    "P+B (bests)": replace(P0, **B),
    "P+S (fast slope)": replace(P0, **Sx),
    "P+M (slope mean +0.75)": replace(P0, **M),
    "P+E (hard_effort shortfall)": replace(P0, **E),
    "C (pre-registered)": S,
    "C-B": replace(S, bests=False),
    "C-S": replace(S, q_slope=0.005, slope_sd=1.5),
    "C-M": replace(S, slope_mean=0.0),
    "C-E": replace(S, shortfall=False),
    "C, M=+0.5": replace(S, slope_mean=0.5),
    "C, M=+1.0": replace(S, slope_mean=1.0),
    "C+T1 (tempo own dist + learned shortfall)": replace(S, tempo="own"),
    "C+T2 (tempo as censored best)": replace(S, tempo="censored"),
}


def metrics(P, name):
    P = P[P.pred.notna()]
    e = P.pred - P.actual
    z = e / np.maximum(P.sd.astype(float), 0.3)
    row = {"model": name}
    r = P.tier == "race"; ar = P.person.isin(["adrian", "rise"])
    for lab, m in (("race adrian", r & (P.person == "adrian")), ("race rise", r & (P.person == "rise")),
                   ("race a+r", r & ar), ("race patrik", r & (P.person == "patrik"))):
        row[lab] = e[m].abs().mean()
    row["race bias a+r"] = e[r & ar].mean()
    row["race ±1sd"] = (z[r & ar].abs() <= 1).mean(); row["race ±2sd"] = (z[r & ar].abs() <= 2).mean()
    for t in ("interval", "hard_effort"):
        m = P.tier == t
        row[t] = e[m].abs().mean(); row[f"{t} a+r"] = e[m & ar].abs().mean()
        row[f"{t} ±1sd"] = (z[m].abs() <= 1).mean(); row[f"{t} ±2sd"] = (z[m].abs() <= 2).mean()
    row["hard_effort patrik"] = e[(P.tier == "hard_effort") & (P.person == "patrik")].abs().mean()
    return row


if __name__ == "__main__":
    out, rows = [], []
    for name, sp in CONFIGS.items():
        t0 = time.time()
        r = H.evaluate(CB.make(sp, name), persons=("adrian", "rise", "patrik"), name=name, verbose=True)
        out.append(r["preds"].assign(model=name)); rows.append(metrics(r["preds"], name))
        print(f"{name}: {time.time() - t0:.0f} s", flush=True)
    (C.OUT / "g9").mkdir(parents=True, exist_ok=True)
    pd.concat(out).to_parquet(C.OUT / "g9" / "configs.parquet", index=False)
    T = pd.DataFrame(rows)
    (HERE / "results").mkdir(exist_ok=True)
    T.round(3).to_csv(HERE / "results" / "configs.csv", index=False)
    with pd.option_context("display.width", 250, "display.max_columns", 40, "display.float_format", "{:.2f}".format):
        print(T.to_string(index=False))

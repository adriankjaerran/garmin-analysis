"""G8 harness runs: curve families x shape dynamics x GAP / interval placement / extrapolation cap.

    /tmp/shape-venv/bin/python research/shape-models/approaches/g8-distance-curve/run_eval.py [--set main|gap|all]

Writes data/_research/shape/g8/eval_<set>.parquet (per-target predictions of every model) and prints the compare tables.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1])); sys.path.insert(0, str(HERE))
import common as C  # noqa: E402
import curves as K  # noqa: E402
import harness as H  # noqa: E402

OUT = C.OUT / "g8"


def models(which):
    m = []
    if which in ("main", "all"):
        m += [("production_curve", H.production_curve), ("naive_best_recent_60d", H.naive_best_recent),
              ("prodK (production filter, own evidence)", K.make_prodk())]
        for fam in ("flat", "prod", "riegel", "cs2", "pt"):
            for hl in ((None,) if fam == "flat" else (None, 730, 365, 180)):
                m.append((f"{fam} hl={hl}", K.make_predict(fam, half_life=hl)))
        for fam in ("riegel", "pt"):
            m.append((f"{fam} hl=None obs@own-time (biased)", K.make_predict(fam, obs_space="time")))
            m.append((f"{fam} hl=365 obs@own-time (biased)", K.make_predict(fam, half_life=365, obs_space="time")))
        for fam in ("prod", "riegel", "cs2", "pt"):
            m.append((f"{fam} hl=365 nocap", K.make_predict(fam, half_life=365, cap=False)))
        m.append(("prodK nocap", K.make_prodk(cap=False)))
    if which in ("place", "all"):
        m += [("prodK interval@work", K.make_prodk(place="work"))]
        for fam in ("prod", "riegel", "pt"):
            m.append((f"{fam} hl=365 interval@work", K.make_predict(fam, half_life=365, place="work")))
    if which in ("pop", "all"):    # shared (population) slope prior mean; 1 knob, chosen on the short-only test
        for mu in (0.5, 1.0):
            for hl in (None, 365, 180):
                m.append((f"prod{mu:+.1f} hl={hl}", K.make_predict(f"prod{mu:+.1f}", half_life=hl)))
    if which in ("gap", "all"):
        for g in ("mh", "mf", "st", "none"):
            m.append((f"prodK gap={g}", K.make_prodk(gap=g)))
            m.append((f"prod hl=365 gap={g}", K.make_predict("prod", half_life=365, gap=g)))
    if which in ("shape_q", "all"):   # production filter, slope dynamics (q_slope, stationary sd)
        for qs, sd in ((0.0, 1.5), (0.001, 1.5), (0.005, 1.5), (0.02, 1.5), (0.005, 0.75), (0.005, 3.0), (1e-9, 1e-3)):
            m.append((f"prodK q_s={qs} sd={sd}", K.make_prodk(q_slope=qs, slope_sd=sd)))
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="main")
    ap.add_argument("--persons", default="adrian,rise")
    args = ap.parse_args()
    persons = tuple(args.persons.split(","))
    res = []
    for name, f in models(args.set):
        r = H.evaluate(f, persons=persons, name=name)
        res.append(r)
        print(f"{name:45s} {r['headline']}", flush=True)
    rows = []
    for r in res:
        S = r["summary"]
        row = {"model": r["name"]}
        for tier in H.TIERS:
            for p in persons + ("all",):
                x = S[(S.tier == tier) & (S.person == p) & (S.band == "all")]
                if len(x):
                    row[f"{tier[:4]} {p}"] = round(float(x.iloc[0]["mae"]), 2)
            x = S[(S.tier == tier) & (S.person == "all") & (S.band == "all")]
            if len(x) and "nll" in x:
                row[f"{tier[:4]} nll"] = round(float(x.iloc[0]["nll"]), 2)
                row[f"{tier[:4]} cov1"] = round(float(x.iloc[0]["cover_1sd"]), 2)
        for band in ("10k", "HM+"):
            x = S[(S.tier == "race") & (S.person == "all") & (S.band == band)]
            if len(x):
                row[f"race {band}"] = round(float(x.iloc[0]["mae"]), 2)
        rows.append(row)
    T = pd.DataFrame(rows)
    with pd.option_context("display.width", 250, "display.max_columns", 40):
        print(T.to_string(index=False))
    pd.concat([r["preds"].assign(model=r["name"]) for r in res]).to_parquet(OUT / f"eval_{args.set}.parquet", index=False)
    T.to_csv(OUT / f"table_{args.set}.csv", index=False)


if __name__ == "__main__":
    main()

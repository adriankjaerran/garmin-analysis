"""G8 features from the 1 Hz series (production session_series, read-only):

    /tmp/shape-venv/bin/python research/shape-models/approaches/g8-distance-curve/features.py [--workers 8]

Per production evidence session (activities.prod_kind in hard / race / bound, running):
  * interval sessions: W'-balance score (production wbal_vdot, tau 0.35, no offset) under
      - GAP variants  mh (Minetti uphill + half downhill = production), mf (full Minetti), st (Strava-style), none
      - D' multipliers m in DPM (D' = m * Daniels-implied D'(VDOT), CS unchanged; GAP mh)
  * every session: best continuous window speed for 1200..5400 s under the 4 GAP variants
Writes data/_research/shape/g8/session_feats.parquet (one row per session).
"""
from __future__ import annotations

import argparse
import json
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
import common as C  # noqa: E402

OUT = C.OUT / "g8"
GAPS = ("mh", "mf", "st", "none")
DPM = (0.5, 0.7, 1.0, 1.4, 2.0)
WIN = (1200, 1800, 2700, 3600, 5400)


def gap_vg(v, grade, g):
    if g == "mh":
        return v * C.gap_factor_minetti(grade, 0.5)
    if g == "mf":
        return v * C.gap_factor_minetti(grade, 1.0)
    if g == "st":
        return v * C.gap_factor_strava(grade, clip=0.20)
    return v


def wbal_vdot_m(fm, vg, m=1.0, lo=25.0, hi=90.0):
    """production wbal_vdot with D' scaled by m."""
    for _ in range(3):
        grid = np.linspace(lo, hi, 9)
        cs, dp = fm.cs_dprime(grid)
        mn = fm.wbal_min(vg, cs, dp * m)
        ok = np.nonzero(mn >= 0)[0]
        if not len(ok):
            return None
        j = ok[0]
        if j == 0:
            return float(grid[0])
        lo, hi = grid[j - 1], grid[j]
        last = (mn[j - 1], mn[j])
    return float(lo + (hi - lo) * (-last[0]) / (last[1] - last[0]))


def job(arg):
    aid, fit, act, is_int = arg
    try:
        fm = C.import_production()
        S = fm.session_series(fit, act)
        v, grade = S["v"], np.clip(S["grade"], -0.2, 0.2)
        r = {"activity_id": aid}
        for g in GAPS:
            vg = gap_vg(v, grade, g)
            if is_int:
                r[f"wb_{g}"] = wbal_vdot_m(fm, vg, 1.0)
            for D in WIN:
                m, at = fm.best_window(vg, S["legs"], D)
                r[f"win{D}_{g}"] = m
        if is_int:
            vg = gap_vg(v, grade, "mh")
            for m in DPM:
                if m != 1.0:
                    r[f"wb_dp{m}"] = wbal_vdot_m(fm, vg, m)
            r["wb_dp1.0"] = r["wb_mh"]
            # work structure: seconds above the Daniels CS of the session score
            if r["wb_mh"]:
                cs, _ = fm.cs_dprime(r["wb_mh"])
                r["sec_above_cs"] = int((vg > cs).sum())
        return r
    except Exception as e:  # noqa: BLE001
        return {"activity_id": aid, "err": repr(e)[:200]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    A = C.load("activities")
    A = A[A.prod_kind.isin(["hard", "race", "bound"]) & A.prod_discipline.isin(["interval", "long"])]
    jobs = []
    for p in C.PERSONS:
        groups = json.loads((C.DATA / p / "groups.json").read_text())["activities"]
        fits = {Path(f).stem: f for f in (C.DATA / p / "activities").glob("*/*.fit")}
        for r in A[A.person == p].itertuples():
            k = str(r.activity_id)
            if k in fits and k in groups:
                jobs.append((k, str(fits[k]), groups[k], r.prod_discipline == "interval" and r.prod_kind == "hard"))
    print(len(jobs), "sessions", flush=True)
    with Pool(args.workers) as pool:
        rows = list(pool.imap_unordered(job, jobs, chunksize=2))
    D = pd.DataFrame(rows)
    print("errors:", D["err"].notna().sum() if "err" in D else 0)
    D.to_parquet(OUT / "session_feats.parquet", index=False)
    print(D.describe().T.to_string())


if __name__ == "__main__":
    main()

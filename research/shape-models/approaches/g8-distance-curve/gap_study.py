"""Which grade-adjusted-pace (GAP) formula reconciles hilly and flat running? Two tests.

    /tmp/shape-venv/bin/python research/shape-models/approaches/g8-distance-curve/gap_study.py [--workers 8]

(1) HR invariance (submaximal, many runs): easy / steady / long / tempo road runs are cut into 3-min chunks of
    continuous running (after the first 10 min); HR is averaged over the chunk shifted by 40 s (HR lag). Per person:
    HR_chunk = a_run + beta * GAPspeed_chunk + gamma * minutes_elapsed + e. A GAP formula that prices hills right
    leaves no grade signal: we report the residual SD, and the coefficient on the chunk's mean uphill / downhill
    grade added to the regression (bpm per % grade, ideal 0). Formulas: none, Minetti full, Minetti uphill + half
    downhill (production), Strava-style quadratic, and a per-person learned cost 1 + a g + b g^2 (linear in the
    chunk sums of v, v g, v g^2; fitted by OLS with run fixed effects, 5-fold by run for an out-of-sample SD).
(2) Performance reconciliation (maximal, few items): road interval sessions and tempo / race windows on hilly
    courses vs the person's flat-course (track) interval level within +-45 days, per GAP variant (session_feats).
Writes data/_research/shape/g8/gap_chunks.parquet and gap_study.csv.
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
CH, LAG, SKIP = 180, 40, 600


def job(arg):
    aid, fit, act = arg
    try:
        fm = C.import_production()
        S = fm.session_series(fit, act)
        v, g, hr = S["v"], np.clip(S["grade"], -0.2, 0.2), S["hr"]
        if hr is None:
            return []
        rows = []
        for a, b in S["legs"]:
            t = max(a, SKIP)
            while t + CH + LAG <= b:
                sl = slice(t, t + CH)
                h = hr[t + LAG: t + CH + LAG]
                vv, gg = v[sl], g[sl]
                if np.isnan(h).mean() > 0.2 or vv.min() < 1.2:
                    t += CH; continue
                rows.append({"activity_id": aid, "t_min": t / 60, "hr": float(np.nanmean(h)), "v": float(vv.mean()),
                             "vg": float((vv * gg).mean()), "vg2": float((vv * gg * gg).mean()),
                             "v_mh": float((vv * C.gap_factor_minetti(gg, 0.5)).mean()),
                             "v_mf": float((vv * C.gap_factor_minetti(gg, 1.0)).mean()),
                             "v_st": float((vv * C.gap_factor_strava(gg, 0.2)).mean()),
                             "up": float(np.clip(gg, 0, None).mean() * 100), "down": float(np.clip(gg, None, 0).mean() * 100),
                             "gabs": float(np.abs(gg).mean() * 100)})
                t += CH
        return rows
    except Exception:  # noqa: BLE001
        return []


def build(workers):
    A = C.load("activities")
    A = A[A.usable & A.pace_ok & A.hr_ok & A.workout.isin(["easy", "steady", "long", "tempo"]) &
          (A.surface == "road")]
    jobs = []
    for p in C.PERSONS:
        groups = json.loads((C.DATA / p / "groups.json").read_text())["activities"]
        fits = {Path(f).stem: f for f in (C.DATA / p / "activities").glob("*/*.fit")}
        for r in A[A.person == p].itertuples():
            k = str(r.activity_id)
            if k in fits and k in groups:
                jobs.append((k, str(fits[k]), groups[k]))
    print(len(jobs), "runs", flush=True)
    with Pool(workers) as pool:
        rows = [x for res in pool.imap_unordered(job, jobs, chunksize=2) for x in res]
    D = pd.DataFrame(rows)
    D = D.merge(A[["activity_id", "person", "day", "hr_device", "hr_source", "trust_steady", "workout"]].astype(
        {"activity_id": str}), on="activity_id")
    D.to_parquet(OUT / "gap_chunks.parquet", index=False)
    return D


def _demean(X, grp):
    X = pd.DataFrame(X)
    return (X - X.groupby(grp.values).transform("mean")).to_numpy()


def hr_test(D):
    rows = []
    sets = {"adrian 4315": (D.person == "adrian") & (D.hr_device == "4315/wrist"),
            "adrian fr935": (D.person == "adrian") & (D.hr_device == "fr935/wrist"),
            "rise strap": (D.person == "rise") & (D.hr_source == "strap"),
            "rise wrist": (D.person == "rise") & (D.hr_source == "wrist"),
            "patrik": D.person == "patrik"}
    for name, mask in sets.items():
        x = D[mask].copy()
        # keep runs with some hills and at least 4 chunks
        cnt = x.groupby("activity_id").activity_id.transform("size")
        x = x[cnt >= 4].reset_index(drop=True)
        if len(x) < 50:
            continue
        grp = x.activity_id
        y = _demean(x[["hr"]], grp)[:, 0]
        tt = _demean(x[["t_min"]], grp)[:, 0]
        runs = grp.unique(); rng = np.random.default_rng(1); fold = dict(zip(runs, rng.integers(0, 5, len(runs))))
        fo = grp.map(fold).to_numpy()
        res = {"set": name, "runs": grp.nunique(), "chunks": len(x), "mean |grade| %": round(x.gabs.mean(), 2)}
        for var in ("v", "v_mf", "v_mh", "v_st", "learned"):
            if var == "learned":
                Xv = _demean(x[["v", "vg", "vg2"]], grp)
            else:
                Xv = _demean(x[[var]], grp)
            X = np.c_[Xv, tt]
            # out-of-sample residual SD (5-fold by run)
            e = np.zeros(len(x))
            for f in range(5):
                tr, te = fo != f, fo == f
                b = np.linalg.lstsq(X[tr], y[tr], rcond=None)[0]
                e[te] = y[te] - X[te] @ b
            b = np.linalg.lstsq(X, y, rcond=None)[0]
            # leftover grade signal: add up / down grade (demeaned)
            G = _demean(x[["up", "down"]], grp)
            b2 = np.linalg.lstsq(np.c_[X, G], y, rcond=None)[0]
            res[f"sd {var}"] = round(float(np.sqrt((e ** 2).mean())), 3)
            res[f"up/down {var}"] = f"{b2[-2]:+.2f}/{b2[-1]:+.2f}"
            if var == "learned":
                a_, b_ = b[1] / b[0], b[2] / b[0]
                res["learned cost"] = f"1 {a_:+.2f} g {b_:+.1f} g²  (Strava 1 +2.98 g +17 g²)"
        rows.append(res)
    return pd.DataFrame(rows)


def perf_test():
    """Hilly road sessions vs flat-course level (+-45 d), per GAP variant: mean residual and slope vs climb/km."""
    sys.path.insert(0, str(HERE))
    A = C.load("activities"); A["activity_id"] = A.activity_id.astype(str)
    F = pd.read_parquet(OUT / "session_feats.parquet")
    X = A.merge(F, on="activity_id")
    I = X[(X.prod_discipline == "interval") & (X.prod_kind == "hard")].copy()
    I["cpk"] = I.climb_m / I.dist_km
    rows = []
    for p, g in I.groupby("person"):
        fc = g.flat_course.fillna(False).astype(bool)
        flat = g[fc]
        road = g[~fc]
        for var in ("none", "mf", "mh", "st"):
            r = []
            for s in road.itertuples():
                nb = flat[(flat.day - s.day).abs() <= 45]
                if len(nb) >= 2:
                    r.append((getattr(s, f"wb_{var}") - nb[f"wb_{var}"].median(), s.cpk))
            if len(r) < 3:
                continue
            r = np.array(r)
            sl = np.polyfit(r[:, 1], r[:, 0], 1)[0] if len(r) >= 4 else np.nan
            rows.append({"person": p, "gap": var, "n road": len(r), "mean climb m/km": round(r[:, 1].mean(), 1),
                         "road - flat (VDOT)": round(r[:, 0].mean(), 2), "sd": round(r[:, 0].std(), 2),
                         "slope per 10 m/km": round(10 * sl, 2)})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--rebuild", action="store_true")
    args = ap.parse_args()
    f = OUT / "gap_chunks.parquet"
    D = build(args.workers) if args.rebuild or not f.exists() else pd.read_parquet(f)
    with pd.option_context("display.width", 250, "display.max_columns", 30):
        T = hr_test(D); print(T.to_string(index=False))
        P = perf_test(); print(P.to_string(index=False))
    T.to_csv(OUT / "gap_study_hr.csv", index=False); P.to_csv(OUT / "gap_study_perf.csv", index=False)


if __name__ == "__main__":
    main()

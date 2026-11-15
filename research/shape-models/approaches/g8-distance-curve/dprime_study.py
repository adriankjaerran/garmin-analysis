"""Short-end profile from interval sessions alone: does a person score systematically higher on short-rep or on
long-rep sessions (W'-balance score vs rep length), and which personal D' multiplier m removes that trend?

    /tmp/shape-venv/bin/python research/shape-models/approaches/g8-distance-curve/dprime_study.py

Residual of a session = its score minus the leave-one-out median of the person's other sessions within +-60 days
(same m), so slow fitness changes cancel. Slope of residual vs log2(median rep seconds) per person and m, with a
session bootstrap (1000 resamples). Interval_offset does not matter here (it cancels in the residual).
Positive slope at m = 1: long-rep sessions score higher than short-rep sessions -> relatively weaker at short /
anaerobic efforts than Daniels' D' assumes (personal D' smaller: m* < 1). Negative slope: stronger short (m* > 1).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
import common as C  # noqa: E402

DPM = (0.5, 0.7, 1.0, 1.4, 2.0)


def load():
    A = C.load("activities")
    A["activity_id"] = A.activity_id.astype(str)
    F = pd.read_parquet(C.OUT / "g8" / "session_feats.parquet")
    X = A.merge(F, on="activity_id")
    X = X[(X.prod_discipline == "interval") & (X.prod_kind == "hard") & X.prod_rep_s.notna()].copy()
    X["lrep"] = np.log2(X.prod_rep_s)
    X["year"] = X.date.dt.year
    return X


def resid(X, col, win=60):
    out = np.full(len(X), np.nan)
    d = X.day.to_numpy(); s = X[col].to_numpy()
    for i in range(len(X)):
        nb = (np.abs(d - d[i]) <= win) & (np.arange(len(X)) != i)
        if nb.sum() >= 2:
            out[i] = s[i] - np.median(s[nb])
    return out


def slope(x, y):
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    if len(x) < 6:
        return np.nan
    return float(np.polyfit(x, y, 1)[0])


def study(X, label, rng):
    rows = []
    for p, g in X.groupby("person"):
        g = g.sort_values("day").reset_index(drop=True)
        R = {m: resid(g, f"wb_dp{m}") for m in DPM}
        sl = {m: slope(g.lrep.to_numpy(), R[m]) for m in DPM}
        boots = {m: [] for m in DPM}
        n = len(g)
        for _ in range(1000):
            ix = rng.integers(0, n, n)
            for m in DPM:
                boots[m].append(slope(g.lrep.to_numpy()[ix], R[m][ix]))
        # m* where the slope crosses 0 (interpolate in log m)
        lm = np.log(DPM)

        def mstar(svals):
            s = np.array([svals[m] for m in DPM])
            if not np.all(np.isfinite(s)):
                return np.nan
            if s[0] * s[-1] > 0:
                return DPM[0] if abs(s[0]) < abs(s[-1]) else DPM[-1]
            j = np.nonzero(np.sign(s[:-1]) != np.sign(s[1:]))[0][0]
            return float(np.exp(lm[j] + (lm[j + 1] - lm[j]) * s[j] / (s[j] - s[j + 1])))
        ms = [mstar({m: boots[m][b] for m in DPM}) for b in range(1000)]
        rows.append({"set": label, "person": p, "n": n,
                     "rep_s range": f"{g.prod_rep_s.quantile(.1):.0f}-{g.prod_rep_s.quantile(.9):.0f}",
                     **{f"slope m={m}": f"{sl[m]:+.2f} ± {np.nanstd(boots[m]):.2f}" for m in (0.7, 1.0, 1.4)},
                     "m*": round(mstar(sl), 2), "m* 68% CI": f"{np.nanpercentile(ms, 16):.2f}-{np.nanpercentile(ms, 84):.2f}"})
    return rows


def main():
    rng = np.random.default_rng(0)
    X = load()
    rows = study(X, "all", rng)
    rows += study(X[X.flat_course], "flat courses", rng)
    rows += study(X[X.date >= "2024-01-01"], "2024 ->", rng)
    rows += study(X[X.date < "2024-01-01"], "< 2024", rng)
    T = pd.DataFrame(rows)
    with pd.option_context("display.width", 250, "display.max_columns", 30):
        print(T.to_string(index=False))
    T.to_csv(C.OUT / "g8" / "dprime_study.csv", index=False)
    # pooled difference adrian - rise in the m = 1 slope (bootstrap)
    a = X[X.person == "adrian"].sort_values("day").reset_index(drop=True)
    r = X[X.person == "rise"].sort_values("day").reset_index(drop=True)
    ra, rr = resid(a, "wb_dp1.0"), resid(r, "wb_dp1.0")
    d = []
    for _ in range(2000):
        ia = rng.integers(0, len(a), len(a)); ir = rng.integers(0, len(r), len(r))
        d.append(slope(a.lrep.to_numpy()[ia], ra[ia]) - slope(r.lrep.to_numpy()[ir], rr[ir]))
    print(f"slope(adrian) - slope(rise) at m=1: {slope(a.lrep.to_numpy(), ra) - slope(r.lrep.to_numpy(), rr):+.2f} "
          f"(boot sd {np.nanstd(d):.2f}, P(>0) {np.nanmean(np.array(d) > 0):.2f}) VDOT per doubling of rep length")


if __name__ == "__main__":
    main()

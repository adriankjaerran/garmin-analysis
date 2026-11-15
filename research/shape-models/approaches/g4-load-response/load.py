"""G4: daily training-load series per person (run TRIMP / km / hours, ski and other cross-training TRIMP / hours).

Runs: activities.parquet (all runs, incl. unusable: load is load) -> trimp, dist_km, moving_s.
Non-run activities: data/<person>/activities_index.json (Garmin summary): TRIMP from averageHR and movingDuration
with the person's max HR and median resting HR (same Banister formula as prep_data). Activities without HR get the
person's median TRIMP/min of the same class. 'stop_watch' / 'other' without HR and anything > 8 h are dropped.
Cached to data/_research/shape/g4/daily_load.parquet.
"""
from __future__ import annotations

import functools
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
import common as C  # noqa: E402

G4 = C.OUT / "g4"
RUN_KEYS = {"running", "track_running", "treadmill_running", "indoor_running", "trail_running", "multi_sport"}


def _cls(key):
    if key in RUN_KEYS:
        return None
    if "ski" in key:
        return "ski"
    if key in ("stop_watch", "other"):
        return "drop"
    return "other"


def build():
    A = pd.read_parquet(C.OUT / "activities.parquet")
    rows = []
    for p in C.PERSONS:
        a = A[A.person == p]
        hmax = float(a.hr_max_person.dropna().max())
        hrest = float(a.hr_rest_30d.dropna().median())
        for r in a.itertuples():
            rows.append(dict(person=p, day=int(r.day), cls="run", trimp=float(r.trimp or 0),
                             km=float(r.dist_km or 0), h=float((r.moving_s or 0) / 3600)))
        idx = json.load(open(C.DATA / p / "activities_index.json"))
        oth = []
        for x in idx:
            c = _cls(x["activityType"]["typeKey"])
            if c is None or c == "drop":
                continue
            dur = (x.get("movingDuration") or x.get("duration") or 0) / 3600
            if dur <= 0 or dur > 8:
                continue
            day = pd.Timestamp(x["startTimeLocal"][:10]).toordinal()
            hr = x.get("averageHR")
            tr = np.nan
            if hr:
                hrr = float(np.clip((hr - hrest) / (hmax - hrest), 0, 1))
                tr = dur * 60 * hrr * 0.64 * np.exp(1.92 * hrr)
            oth.append(dict(person=p, day=day, cls=c, trimp=tr, km=(x.get("distance") or 0) / 1000, h=dur))
        o = pd.DataFrame(oth)
        if len(o):
            pm = (o.trimp / (o.h * 60)).groupby(o.cls).transform("median")
            o["trimp"] = o.trimp.fillna(o.h * 60 * pm)
            rows += o.to_dict("records")
    D = pd.DataFrame(rows)
    D = D.groupby(["person", "day", "cls"], as_index=False)[["trimp", "km", "h"]].sum()
    G4.mkdir(parents=True, exist_ok=True)
    D.to_parquet(G4 / "daily_load.parquet", index=False)
    return D


@functools.lru_cache(maxsize=1)
def daily():
    f = G4 / "daily_load.parquet"
    return pd.read_parquet(f) if f.exists() else build()


@functools.lru_cache(maxsize=64)
def series(person, metric="trimp", ski=0.5, other=0.25):
    """(day0, daily array) of load for `person`: run + ski*ski_load + other*other_load in `metric`
    (trimp | h | km; for km the cross-training is converted at 10 km per hour)."""
    D = daily()
    D = D[D.person == person]
    d0 = int(D.day.min()) - 1; d1 = int(D.day.max()) + 400
    out = np.zeros(d1 - d0 + 1)
    for cls, f in (("run", 1.0), ("ski", ski), ("other", other)):
        x = D[D.cls == cls]
        if metric == "km":
            v = x.km.to_numpy() if cls == "run" else x.h.to_numpy() * 10.0
        else:
            v = x[metric].to_numpy()
        np.add.at(out, x.day.to_numpy() - d0, f * v)
    return d0, out


@functools.lru_cache(maxsize=256)
def ewma(person, tau, metric="trimp", ski=0.5, other=0.25):
    """(day0, arr): arr[i] = (1-a) * sum_{s < day0+i} load_s a^(day0+i-1-s), a = e^(-1/tau): load of days strictly before
    day i (CTL/ATL-style, units load/day)."""
    d0, x = series(person, metric, ski, other)
    a = np.exp(-1.0 / tau)
    out = np.zeros_like(x); s = 0.0
    for i in range(len(x)):
        out[i] = s
        s = s * a + x[i] * (1 - a)
    return d0, out


def at(person, day, tau, **kw):
    d0, arr = ewma(person, tau, **kw)
    i = int(day) - d0
    return float(arr[min(max(i, 0), len(arr) - 1)])


if __name__ == "__main__":
    D = build()
    print(D.groupby(["person", "cls"])[["trimp", "km", "h"]].sum().round(0))


@functools.lru_cache(maxsize=64)
def busso(person, tau2, tau3, metric="trimp", ski=0.5, other=0.25):
    """Busso (2003) variable-gain fatigue term: sum_s w_s k(s) e^(-(t-s)/tau2) with k(s) = sum_{i<=s} w_i
    e^(-(s-i)/tau3) (the fatigue gain grows with recent load). Normalised like ewma ((1-a2)(1-a3) factors), so
    units are load^2/day^2; days strictly before t."""
    d0, x = series(person, metric, ski, other)
    a2, a3 = np.exp(-1.0 / tau2), np.exp(-1.0 / tau3)
    out = np.zeros_like(x); s = 0.0; k = 0.0
    for i in range(len(x)):
        out[i] = s
        k = k * a3 + x[i] * (1 - a3)
        s = s * a2 + x[i] * k * (1 - a2)
    return d0, out

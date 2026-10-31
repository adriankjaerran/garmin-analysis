"""Workout comparison: one session vs one or many ("this run vs similar runs / intervals").

    uv run scripts/compare.py adrian distance --route R3                  # all Holmenkollen climbs, ghost gaps
    uv run scripts/compare.py adrian intervals --sig 6×1000 --place Bislet --last 6
    uv run scripts/compare.py adrian breaks 24874084265 24288925182
    uv run scripts/compare.py adrian moving 24874084265 24460851059 --reference median --json out.json
    uv run scripts/compare.py adrian validate                             # sanity checks on real data

    import compare as cmp
    r = cmp.compare([24874084265, 24460851059], "intervals")             # JSON-serialisable dict

Modes: elapsed | moving | distance (= route_position) | intervals | breaks. The first id is the
reference session; reference="median" uses a virtual median-of-the-group session instead.
Builds on data/<p>/groups.json (session_groups.py: routes, reference polylines, waypoints, signatures)
and re-reads FIT files with the fast reader. Rep boundaries (run_analysis.analyse, ~0.6 s per file; ski:
ski_intervals.analyse) and route projections are cached in data/<p>/cache/compare.pkl (keyed by FIT mtime+size).
Docs: docs/compare.md.
"""

from __future__ import annotations

import argparse
import json
import math
import pickle
import sys
import time
import warnings
from multiprocessing import Pool
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import session_groups as sg  # noqa: E402  (read-only: FIT reader, 1 Hz grid, pauses, route projection)
import ski_intervals as si  # noqa: E402  (ski rep detector)

ROOT = Path(__file__).resolve().parent.parent
CACHE_VERSION = 1
MODES = ("elapsed", "moving", "distance", "route_position", "intervals", "breaks")
NO_DIST_MODES = ("elapsed", "intervals", "breaks")   # sessions without distance (no-GPS lap workouts): HR only

# ---- parameters
MAX_POINTS = 1500          # series points per session
GAP_S = 60                 # intervals view: visual width of a collapsed break (s on the x axis)
REP_GAP_S = 30             # breaks view: visual width of a collapsed rep
PRE_S = 60                 # intervals view: seconds shown before the first rep
MATCH_TOL = 0.2            # rep matching: max |log ratio| of distance or time (about +-22 %)
MATCH_GAP = 0.25           # rep matching: cost of leaving a rep unmatched
MATCH_SAME = 0.05          # rep matching: distance or time within 5 % = the same prescribed rep
SKI_MATCH = (0.45, 0.10)   # ski reps (terrain-defined, durations vary): closer of distance / time, tol and free band
STRIDE_S = 35              # reps < 35 s in groups of <= 2 are strides (left out, as in the signature)
HR_AT = (30, 60, 90)       # breaks: HR read at these seconds after the break start
LEG_TOL_M = 100.0          # distance legs: a session joining / stopping this close to a leg end covers it
SPEED_H_M = 50.0           # distance axis: speed = 2h / time over +-h metres
MINETTI = (155.4, -30.4, -43.3, 46.3, 19.5, 3.6)   # cost of running vs grade (J/kg/m), Minetti 2002


# ---------------------------------------------------------------- small helpers
def _r(v, n=1):
    if v is None:
        return None
    v = float(v)
    if math.isnan(v) or math.isinf(v):
        return None
    return int(round(v)) if n == 0 else round(v, n)


def _lst(a, n=1):
    return [_r(v, n) for v in np.asarray(a, float)]


def _nanmean(a):
    a = np.asarray(a, float)
    a = a[~np.isnan(a)]
    return float(a.mean()) if len(a) else None


def _pace(speed_ms):
    """s per km"""
    return 1000 / speed_ms if speed_ms and speed_ms > 0.3 else None


def _smooth(a, k):
    k = int(k)
    if k <= 1 or len(a) < k:
        return np.asarray(a, float)
    a = np.asarray(a, float)
    m = ~np.isnan(a)
    num = np.convolve(np.where(m, a, 0), np.ones(k), mode="same")
    den = np.convolve(m.astype(float), np.ones(k), mode="same")
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(den > 0, num / den, np.nan)


def _thin(cols: dict, max_points: int) -> dict:
    """block means (nan-aware) so every column has <= max_points values; 'x'-like columns use the block mean too"""
    n = len(next(iter(cols.values()))) if cols else 0
    if n <= max_points:
        return cols
    k = math.ceil(n / max_points)
    m = n // k * k
    out = {}
    for key, a in cols.items():
        a = np.asarray(a, float)
        head = a[:m].reshape(-1, k)
        with np.errstate(invalid="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            v = np.nanmean(head, 1)
            if m < n:
                v = np.concatenate([v, [np.nanmean(a[m:])]])
        out[key] = v
    return out


def _minetti(g):
    g = np.clip(g, -0.3, 0.3)
    c = MINETTI
    return ((((c[0] * g + c[1]) * g + c[2]) * g + c[3]) * g + c[4]) * g + c[5]


def _gap_factor(grade):
    """grade-adjusted speed = speed * factor (flat-equivalent effort)"""
    return _minetti(grade) / MINETTI[5]


# ---------------------------------------------------------------- data access
class _Store:
    def __init__(self, person):
        self.person = person
        self.D = ROOT / "data" / person
        self._groups = None
        self._gkey = None
        self._fits = None
        self._sess = {}
        self._route = {}
        self.cfile = self.D / "cache" / "compare.pkl"
        self._cache = None
        self._dirty = False

    # groups.json (reloaded when it changes)
    def groups(self):
        p = self.D / "groups.json"
        k = p.stat().st_mtime_ns
        if k != self._gkey:
            self._groups = json.loads(p.read_text())
            self._gkey = k
            self._route.clear()
        return self._groups

    def act(self, i):
        return self.groups()["activities"].get(str(i))

    def fit(self, i):
        if self._fits is None or int(i) not in self._fits:
            self._fits = {int(p.stem): p for p in (self.D / "activities").glob("*/*.fit")}
        return self._fits.get(int(i))

    @staticmethod
    def fkey(p):
        st = p.stat()
        return (st.st_mtime_ns, st.st_size)

    def cache(self):
        if self._cache is None:
            c = {}
            if self.cfile.exists():
                try:
                    c = pickle.loads(self.cfile.read_bytes())
                except Exception:
                    c = {}
            self._cache = c if c.get("_v") == CACHE_VERSION else {"_v": CACHE_VERSION}
        return self._cache

    def save(self):
        if self._dirty:
            self.cfile.parent.mkdir(exist_ok=True)
            self.cfile.write_bytes(pickle.dumps(self._cache))
            self._dirty = False

    # one session on the 1 Hz grid
    def session(self, i):
        i = int(i)
        if i in self._sess:
            return self._sess[i]
        f = self.fit(i)
        if f is None:
            raise KeyError(f"no FIT file for {i}")
        s = sg.read_series(f)
        G = sg.to_grid(s)
        no_dist = bool(G["d"][-1] < 100)
        if no_dist and ("hr" not in G or not np.isfinite(G["hr"]).any()):
            raise ValueError(f"{i}: no distance")
        # without distance (indoor / no-GPS lap workouts) only timer stops are pauses; HR-only comparisons
        P = [(a, b) for a, b in sg.runs_of(G["timer"])] if no_dist else sg.find_pauses(G)
        T = G["T"]
        paused = np.zeros(T + 1, bool)
        for a, b in P:
            paused[a:b] = True
        a = self.act(i) or {}
        S = {"id": i, "G": G, "P": P, "paused": paused, "mt": np.concatenate([[0], np.cumsum(~paused[:-1])]).astype(float),
             "sport": a.get("sport") or ("ski" if s["sport"] == 12 else "run"), "meta": a, "fit": f, "no_dist": no_dist}
        if len(self._sess) > 64:
            self._sess.clear()
        self._sess[i] = S
        return S

    # reps (run_analysis v2 for runs, ski_intervals for ski; cached on disk)
    def reps(self, ids, workers=8):
        c = self.cache()
        todo, keys = [], {}
        for i in ids:
            f = self.fit(i)
            if f is None:
                continue
            ski = (self.act(i) or {}).get("sport") == "ski"
            keys[str(f)] = (*self.fkey(f), si.VERSION) if ski else self.fkey(f)
            e = c.get(("reps", int(i)))
            if e is None or e["key"] != keys[str(f)]:
                todo.append((str(f), ski))
        if todo:
            if len(todo) > 2 and workers > 1:
                with Pool(min(workers, len(todo))) as pool:
                    res = pool.map(_rep_job, todo)
            else:
                res = [_rep_job(f) for f in todo]
            for f, r in res:
                p = Path(f)
                c[("reps", int(p.stem))] = {"key": keys[f], "r": r}
            self._dirty = True
            self.save()
        return {int(i): (c.get(("reps", int(i))) or {}).get("r") for i in ids}

    # route context: reference polyline from the medoid (as in session_groups.compare_route)
    def route(self, rid):
        if rid in self._route:
            return self._route[rid]
        R = self.groups()["routes"].get(rid)
        if not R or "legs_table" not in R:
            self._route[rid] = None
            return None
        Gm = self.session(R["medoid"])["G"]
        ref = sg.ref_polyline(Gm)
        ctx = {"id": rid, "R": R, "ref": ref, "L": ref["L"], "laps": R["shape"] == "laps",
               "bnd": [w["km"] * 1000 for w in R["waypoints"]], "names": [w["name"] for w in R["waypoints"]]}
        ctx["bnd"][-1] = min(ctx["bnd"][-1], ref["L"])
        self._route[rid] = ctx
        return ctx

    def progress(self, i, ctx):
        """travel-direction progress along the route reference per grid second (m), on-route mask, direction"""
        S = self.session(i)
        c = self.cache()
        k = ("proj", int(i), ctx["id"], int(ctx["R"]["medoid"]))
        fk = (self.fkey(S["fit"]), self.fkey(self.fit(ctx["R"]["medoid"])))
        e = c.get(k)
        if e is None or e["key"] != fk:
            p, on, direc = sg.project(S["G"], ctx["ref"], ctx["laps"])
            tp = p if direc == "fwd" else ctx["L"] - p
            e = {"key": fk, "tp": tp.astype(np.float32), "on": on, "dir": direc}
            c[k] = e
            self._dirty = True
        return e["tp"].astype(float), e["on"], e["dir"]


def _rep_job(job):
    """compact rep / break rows from run_analysis.analyse, or ski_intervals.analyse for ski (subprocess-safe)"""
    fit, ski = job
    import run_analysis as ra
    try:
        x = si.analyse(Path(fit)) if ski else ra.analyse(Path(fit))
    except Exception as e:
        return fit, {"err": repr(e)[:100]}
    keep = ("start", "end", "time", "dist", "speed", "hr", "maxhr", "hr_start", "hr_end", "dist_source")
    return fit, {"mode": x["mode"], "reps": [{k: r.get(k) for k in keep} for r in x["reps"]],
                 "laps": list(x["laps"]) if ski else [(l.get("intensity"), l.get("trigger")) for l in x["laps"]]}


_STORES: dict = {}


def store(person="adrian") -> _Store:
    if person not in _STORES:
        _STORES[person] = _Store(person)
    return _STORES[person]


# ---------------------------------------------------------------- per-session summary
def _grade_per_s(G, win=30):
    """grade per grid second from smoothed altitude over own distance (+-win s, >= 50 m)"""
    if "alt" not in G:
        return None
    a, d = G["alt"], G["d"]
    n = len(a)
    i0 = np.clip(np.arange(n) - win, 0, n - 1); i1 = np.clip(np.arange(n) + win, 0, n - 1)
    dd = d[i1] - d[i0]
    with np.errstate(invalid="ignore", divide="ignore"):
        g = np.where(dd > 50, (a[i1] - a[i0]) / dd, 0.0)
    return np.clip(g, -0.3, 0.3)


def _summary(S):
    G, mov = S["G"], ~S["paused"]
    T = G["T"]
    dv = np.diff(G["d"])
    m1 = mov[:-1]
    dist = float(G["d"][-1])
    mov_s = int(m1.sum())
    hr = G.get("hr")
    hrm = _nanmean(hr[mov]) if hr is not None else None
    hmax = float(np.nanmax(hr)) if hr is not None and np.isfinite(hr).any() else None
    v = dist / mov_s if mov_s and not S.get("no_dist") else None
    gap_v = None
    if S["sport"] == "run":
        g = _grade_per_s(G)
        if g is not None and mov_s:
            f = _gap_factor(g[:-1])
            # flat-equivalent distance (distance x cost factor) over moving time, like the moving pace
            gap_v = float(np.sum(dv * f) / mov_s)
    eff_v = gap_v or v
    a = S["meta"]
    out = {"id": S["id"], "date": a.get("date"), "sport": S["sport"], "type": a.get("type"), "sig": a.get("sig"),
           "route": a.get("route"), "place": a.get("place"),
           "elapsed_s": T, "moving_s": mov_s, "pause_s": T - mov_s, "n_pauses": len(S["P"]),
           "dist_m": _r(dist, 0), "moving_kmh": _r(v * 3.6 if v else None, 2), "moving_pace_s_km": _r(_pace(v), 1),
           "gap_pace_s_km": _r(_pace(gap_v), 1), "hr_avg": _r(hrm, 0), "hr_max": _r(hmax, 0),
           "climb_m": _r(G["up"][-1], 0) if "up" in G else None,
           # m per heart beat (GAP speed for runs): higher = more economical at the same effort
           "eff_m_per_beat": _r(eff_v * 60 / hrm, 3) if eff_v and hrm else None}
    return out


SUMMARY_DELTA = {"moving_s": 0, "elapsed_s": 0, "pause_s": 0, "dist_m": 0, "moving_pace_s_km": 1, "gap_pace_s_km": 1,
                 "moving_kmh": 2, "hr_avg": 0, "climb_m": 0}


def _deltas(row, ref, keys):
    d = {}
    for k, n in keys.items():
        a, b = row.get(k), ref.get(k)
        d[k] = _r(a - b, n) if a is not None and b is not None else None
    if row.get("eff_m_per_beat") and ref.get("eff_m_per_beat"):
        d["eff_pct"] = _r(100 * (row["eff_m_per_beat"] / ref["eff_m_per_beat"] - 1), 1)
    return d


def _median_row(rows, keys):
    out = {}
    for k in list(keys) + ["eff_m_per_beat", "hr_max"]:
        v = [r[k] for r in rows if r.get(k) is not None]
        out[k] = float(np.median(v)) if v else None
    return out


# ---------------------------------------------------------------- public API
def compare(ids, mode="distance", *, person="adrian", reference="first", max_points=MAX_POINTS,
            smooth_s=None, axis="auto", rep_axis="absolute", gap_s=GAP_S, rep_gap_s=REP_GAP_S,
            step_m=None, match="auto", workers=8) -> dict:
    """Compare sessions. ids[0] is the reference (reference="first") unless reference="median".

    mode: elapsed | moving | distance | route_position | intervals | breaks
    axis (distance mode): auto (route reference when all sessions share a compared route, else own
        distance) | route | own
    rep_axis (intervals): absolute (seconds from rep start) | normalized (0..100 % of the rep)
    match (intervals/breaks): auto (nearest distance/duration, order kept; index pairing when less than half
        of the reps match) | index
    smooth_s: speed smoothing for time modes (default 5 s runs, 15 s ski)
    Returns a JSON-serialisable dict, see docs/compare.md."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if reference not in ("first", "median"):
        raise ValueError("reference must be 'first' or 'median'")
    ids = list(dict.fromkeys(int(i) for i in ids))
    if not ids:
        raise ValueError("no ids")
    T0 = time.time()
    st = store(person)
    sess, skipped = [], []
    for i in ids:
        try:
            S = st.session(i)
            if S["no_dist"] and mode not in NO_DIST_MODES:
                raise ValueError(f"{i}: no distance (only {', '.join(NO_DIST_MODES)} work without it)")
            sess.append(S)
        except Exception as e:
            skipped.append({"id": i, "why": str(e)[:80]})
    if not sess:
        raise ValueError(f"no usable sessions: {skipped}")
    if sess[0]["id"] != ids[0]:
        skipped.append({"id": ids[0], "why": "reference unusable, next session is the reference"})
    summ = [_summary(S) for S in sess]
    ref_row = summ[0] if reference == "first" else _median_row(summ, SUMMARY_DELTA)
    for r in summ:
        r["delta"] = _deltas(r, ref_row, SUMMARY_DELTA)
    out = {"mode": "distance" if mode == "route_position" else mode, "person": person,
           "reference": {"kind": "session", "id": sess[0]["id"]} if reference == "first" else {"kind": "median", "n": len(sess)},
           "ids": [S["id"] for S in sess], "skipped": skipped, "sessions": summ}
    if reference == "median":
        out["reference"]["summary"] = {k: _r(v, 2) for k, v in ref_row.items()}
    if mode in ("elapsed", "moving"):
        out.update(_time_mode(sess, mode, reference, max_points, smooth_s))
    elif mode in ("distance", "route_position"):
        out.update(_distance_mode(st, sess, reference, max_points, axis, step_m))
    else:
        out.update(_interval_modes(st, sess, mode, reference, max_points, rep_axis, gap_s, rep_gap_s, workers, match))
    st.save()
    out["compute_s"] = round(time.time() - T0, 2)
    return out


# ---------------------------------------------------------------- elapsed / moving
def _speed(S, smooth_s):
    k = smooth_s or (15 if S["sport"] == "ski" else 5)
    return _smooth(S["G"]["v"], max(1, k - 4))   # G["v"] is already a 4 s difference


def _time_mode(sess, mode, reference, max_points, smooth_s):
    """x = elapsed seconds, or moving seconds (paused seconds dropped). Gap = metres ahead of the reference."""
    cols, pauses = {}, {}
    for S in sess:
        G = S["G"]
        T = G["T"]
        v = _speed(S, smooth_s) if not S["no_dist"] else np.full(T + 1, np.nan)
        keep = np.ones(T + 1, bool) if mode == "elapsed" else ~S["paused"]
        x = np.arange(T + 1, dtype=float) if mode == "elapsed" else S["mt"]
        c = {"x": x[keep], "t": np.arange(T + 1, dtype=float)[keep], "speed_kmh": v[keep] * 3.6,
             "hr": G["hr"][keep] if "hr" in G else np.full(keep.sum(), np.nan),
             "alt": G["alt"][keep] if "alt" in G else np.full(keep.sum(), np.nan),
             "dist_m": G["d"][keep] if not S["no_dist"] else np.full(keep.sum(), np.nan)}
        cols[S["id"]] = c
        pauses[S["id"]] = [{"x": _r(x[a], 0), "t": a, "dur_s": b - a} for a, b in S["P"]]
    # common 1 s grid for the gap (distance ahead) and the median reference
    X = np.arange(0, max(c["x"][-1] for c in cols.values()) + 1)

    def on_grid(c, key):
        y = np.interp(X, c["x"], c[key], left=np.nan, right=np.nan)
        return y

    ref_d = None
    if reference == "first":
        ref_d = on_grid(cols[sess[0]["id"]], "dist_m")
    else:
        D = np.array([on_grid(c, "dist_m") for c in cols.values()])
        ok = np.sum(~np.isnan(D), 0) >= max(2, len(cols) / 2)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            ref_d = np.where(ok, np.nanmedian(D, 0), np.nan)
            med = {k: np.where(ok, np.nanmedian(np.array([on_grid(c, k) for c in cols.values()]), 0), np.nan)
                   for k in ("speed_kmh", "hr")}
    series = {}
    for i, c in cols.items():
        ahead = on_grid(c, "dist_m") - ref_d
        c = dict(c)
        c["ahead_m"] = np.interp(c["x"], X, ahead)
        th = _thin(c, max_points)
        series[str(i)] = {"x": _lst(th["x"], 0), "t": _lst(th["t"], 0), "speed_kmh": _lst(th["speed_kmh"], 2),
                          "hr": _lst(th["hr"], 0), "alt": _lst(th["alt"], 1), "dist_m": _lst(th["dist_m"], 0),
                          "ahead_m": _lst(th["ahead_m"], 0), "pauses": pauses[i]}
    out = {"axis": {"x": "elapsed_s" if mode == "elapsed" else "moving_s", "unit": "s",
                    "note": "ahead_m = own distance - reference distance at the same x (positive = ahead)"},
           "series": series}
    if reference == "median":
        th = _thin({"x": X, "speed_kmh": med["speed_kmh"], "hr": med["hr"], "dist_m": ref_d}, max_points)
        out["median"] = {"x": _lst(th["x"], 0), "speed_kmh": _lst(th["speed_kmh"], 2), "hr": _lst(th["hr"], 0),
                         "dist_m": _lst(th["dist_m"], 0)}
    return out


# ---------------------------------------------------------------- distance / route position
def _common_route(st, sess, axis):
    if axis == "own":
        return None
    rids = {(S["meta"] or {}).get("route") for S in sess}
    if len(rids) == 1 and None not in rids:
        ctx = st.route(next(iter(rids)))
        if ctx and not ctx["laps"]:
            return ctx
    if axis == "route":
        raise ValueError(f"sessions don't share one compared (non-laps) route: {sorted(map(str, rids))}")
    return None


def _distance_mode(st, sess, reference, max_points, axis, step_m):
    """x = position along the route reference (projection) or own distance; per x: first time there."""
    ctx = _common_route(st, sess, axis)
    prog = {}
    for S in sess:
        G = S["G"]
        if ctx:
            tp, on, direc = st.progress(S["id"], ctx)
            onix = np.nonzero(on)[0]
            j0 = float(tp[onix[0]]) if len(onix) else 0.0
            tp = np.maximum.accumulate(np.maximum(tp, j0))
            # where the session joins the route the projection waits until it has caught up: start 20 m later
            x0 = j0 + 20.0 if j0 > 0 else 0.0
            prog[S["id"]] = (tp, x0, float(tp[-1]), direc)
        else:
            d = G["d"] - G["d"][0]
            prog[S["id"]] = (d, 0.0, float(d[-1]), "own")
    Lmax = ctx["L"] if ctx else max(p[2] for p in prog.values())
    step = step_m or max(10.0, math.ceil(Lmax / max_points / 10) * 10)
    X = np.arange(0, Lmax + step / 2, step)
    h = max(SPEED_H_M, step)
    per = {}
    for S in sess:
        G = S["G"]
        p, x0, x1, direc = prog[S["id"]]
        T = G["T"]
        # strictly increasing samples: first second at each new progress value
        nz = np.concatenate([[True], np.diff(p) > 0])
        pi, ti = p[nz], np.arange(T + 1, dtype=float)[nz]
        ok = (X >= x0) & (X <= x1)
        te = np.where(ok, np.interp(X, pi, ti), np.nan)                 # elapsed time at x
        tm = np.where(ok, np.interp(X, pi, S["mt"][nz]), np.nan)        # moving time at x
        lo, hi = np.maximum(X - h, x0), np.minimum(X + h, x1)
        tmh = np.interp(hi, pi, S["mt"][nz]) - np.interp(lo, pi, S["mt"][nz])
        with np.errstate(invalid="ignore", divide="ignore"):
            v = np.where(ok & (tmh > 0), (hi - lo) / tmh, np.nan)
        hr = np.interp(np.nan_to_num(te), np.arange(T + 1), G["hr"]) if "hr" in G else np.full(len(X), np.nan)
        hr[~ok] = np.nan
        if ctx:
            alt = np.interp(X, ctx["ref"]["m"], ctx["ref"]["alt"])
        elif "alt" in G:
            alt = np.where(ok, np.interp(np.nan_to_num(te), np.arange(T + 1), G["alt"]), np.nan)
        else:
            alt = np.full(len(X), np.nan)
        gapv = None
        if S["sport"] == "run" and np.isfinite(alt).any():
            ah = np.interp(np.clip(X + h, 0, Lmax), X[np.isfinite(alt)], alt[np.isfinite(alt)])
            al = np.interp(np.clip(X - h, 0, Lmax), X[np.isfinite(alt)], alt[np.isfinite(alt)])
            gapv = v * _gap_factor((ah - al) / (2 * h))
        per[S["id"]] = {"te": te, "tm": tm, "v": v, "hr": hr, "alt": alt, "gapv": gapv, "x0": x0, "x1": x1, "dir": direc}

    # reference curve: first session, or median of per-bin time increments
    def incr(a):
        return np.diff(a, prepend=np.nan)
    if reference == "first":
        R = per[sess[0]["id"]]
        r_dm, r_de = incr(R["tm"]), incr(R["te"])
    else:
        DM = np.array([incr(q["tm"]) for q in per.values()]); DE = np.array([incr(q["te"]) for q in per.values()])
        okn = np.sum(~np.isnan(DM), 0) >= max(2, len(per) / 2)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            r_dm = np.where(okn, np.nanmedian(DM, 0), np.nan); r_de = np.where(okn, np.nanmedian(DE, 0), np.nan)
            med_hr = np.where(okn, np.nanmedian(np.array([q["hr"] for q in per.values()]), 0), np.nan)
    series = {}
    for S in sess:
        q = per[S["id"]]
        gm = np.cumsum(np.nan_to_num(incr(q["tm"]) - r_dm)); ge = np.cumsum(np.nan_to_num(incr(q["te"]) - r_de))
        both = ~np.isnan(incr(q["tm"]) - r_dm)
        # gap defined from the first common bin on (both sessions present)
        first = int(np.argmax(both)) if both.any() else len(X)
        last = len(both) - int(np.argmax(both[::-1])) if both.any() else 0
        valid = np.zeros(len(X), bool); valid[max(0, first - 1):last] = True
        if first > 0:
            gm = gm - gm[first - 1]; ge = ge - ge[first - 1]
        gm[~valid] = np.nan; ge[~valid] = np.nan
        q["gap_m"], q["gap_e"] = gm, ge
        ok = ~np.isnan(q["tm"])
        sel = slice(int(np.argmax(ok)), len(ok) - int(np.argmax(ok[::-1]))) if ok.any() else slice(0, 0)
        series[str(S["id"])] = {
            "x": _lst(X[sel], 0), "moving_s": _lst(q["tm"][sel], 0), "elapsed_s": _lst(q["te"][sel], 0),
            "speed_kmh": _lst(q["v"][sel] * 3.6, 2), "pace_s_km": [_r(_pace(v)) for v in q["v"][sel]],
            "gap_kmh": _lst(q["gapv"][sel] * 3.6, 2) if q["gapv"] is not None else None,
            "hr": _lst(q["hr"][sel], 0), "alt": _lst(q["alt"][sel], 1),
            "delta_moving_s": _lst(gm[sel], 1), "delta_elapsed_s": _lst(ge[sel], 1), "dir": q["dir"]}
    # legs: route waypoints, Bislet-style 400 m laps for laps routes, else 1 km
    if ctx:
        bnd, names = list(ctx["bnd"]), list(ctx["names"])
        legs_kind = "route_waypoints"
    else:
        lap = 400.0 if all(((S["meta"] or {}).get("place") == "Bislet") for S in sess) else 1000.0
        Lc = min(p[2] for p in prog.values())
        bnd = list(np.arange(0, Lc + 1, lap))
        if Lc - bnd[-1] > 0.3 * lap:
            bnd.append(Lc)
        names = [f"{b / 1000:.1f}" for b in bnd]
        legs_kind = "laps_400m" if lap == 400 else "km"
    legs = []
    for k in range(len(bnd) - 1):
        a, b = bnd[k], bnd[k + 1]
        rows = {}
        for S in sess:
            q = per[S["id"]]
            # leg ends within LEG_TOL_M of where a session joins / stops count as reached
            xa, xb = max(a, q["x0"]), min(b, q["x1"])
            if xa > a + LEG_TOL_M or xb < b - LEG_TOL_M or xb <= xa:
                rows[str(S["id"])] = None
                continue
            okx = np.isfinite(q["tm"])
            tm0, tm1 = np.interp([xa, xb], X[okx], q["tm"][okx]); te0, te1 = np.interp([xa, xb], X[okx], q["te"][okx])
            if tm1 <= tm0:
                rows[str(S["id"])] = None
                continue
            G = S["G"]
            hv = G["hr"][int(te0):int(te1) + 1][~S["paused"][int(te0):int(te1) + 1]] if "hr" in G else []
            rows[str(S["id"])] = {"moving_s": _r(tm1 - tm0, 0), "pause_s": _r((te1 - te0) - (tm1 - tm0), 0),
                                  "speed_kmh": _r((xb - xa) / (tm1 - tm0) * 3.6, 2), "hr": _r(_nanmean(hv), 0),
                                  "delta_moving_s": _r(np.interp(xb, X[np.isfinite(q["gap_m"])], q["gap_m"][np.isfinite(q["gap_m"])]), 1)
                                  if np.isfinite(q["gap_m"]).any() else None}
            if xb - xa < 0.98 * (b - a):
                rows[str(S["id"])]["partial"] = _r((xb - xa) / (b - a), 2)
        vals = [r["moving_s"] for r in rows.values() if r]
        refm = (rows[str(sess[0]["id"])] or {}).get("moving_s") if reference == "first" else (
            float(np.median(vals)) if vals else None)
        for r in rows.values():
            if r:
                r["d_moving_s"] = _r(r["moving_s"] - refm, 0) if refm is not None else None
        rk = sorted((r["moving_s"], i) for i, r in rows.items() if r)
        for n, (_, i) in enumerate(rk, 1):
            rows[i]["rank"] = n
        legs.append({"from_m": _r(a, 0), "to_m": _r(b, 0), "name": f"{names[k]} → {names[k + 1]}",
                     "climb_m": _r(_ref_climb(ctx, a, b), 0) if ctx else None, "sessions": rows})
    out = {"axis": {"x": "route_m" if ctx else "dist_m", "unit": "m", "step_m": step,
                    "route": ctx["id"] if ctx else None, "route_name": ctx["R"]["name"] if ctx else None,
                    "note": "delta_*_s = own time to reach x - reference time (positive = behind), counted from the "
                            "first position both sessions cover"},
           "legs_kind": legs_kind, "legs": legs, "series": series}
    if ctx:
        r = ctx["ref"]; stp = max(1, math.ceil(len(r["m"]) / 300))
        out["route_ref"] = {"id": ctx["R"]["medoid"], "m": _lst(r["m"][::stp], 0), "alt": _lst(r["alt"][::stp], 1),
                            "lat": _lst(r["lat"][::stp], 5), "lon": _lst(r["lon"][::stp], 5),
                            "waypoints": ctx["R"]["waypoints"]}
    if reference == "median":
        tmed = np.cumsum(np.nan_to_num(r_dm)); tmed[np.isnan(r_dm) & (np.arange(len(X)) > 0)] = np.nan
        with np.errstate(invalid="ignore", divide="ignore"):
            vmed = np.where(np.isfinite(r_dm) & (r_dm > 0), step / r_dm, np.nan)
        out["median"] = {"x": _lst(X, 0), "moving_s": _lst(tmed, 0), "speed_kmh": _lst(_smooth(vmed, max(1, round(2 * h / step))) * 3.6, 2),
                         "hr": _lst(med_hr, 0)}
    return out


def _ref_climb(ctx, a, b):
    up, _ = sg.cum_climb(ctx["ref"]["alt"])
    m = ctx["ref"]["m"]
    return float(np.interp(b, m, up) - np.interp(a, m, up))


# ---------------------------------------------------------------- intervals / breaks
def _main_reps(reps, ski=False):
    """drop strides like session_groups.signature: consecutive groups of similar reps (+-15 % time or
    distance); groups of <= 2 reps all shorter than STRIDE_S go, unless nothing else is left.
    Ski reps (ski_intervals) are used as detected."""
    if not reps or ski:
        return list(reps or [])
    sim = lambda x, y: abs(math.log(max(x, 1) / max(y, 1))) < 0.15
    groups = []
    for r in reps:
        g = groups[-1] if groups else None
        if g and (sim(r["time"], np.median([x["time"] for x in g])) or sim(r["dist"], np.median([x["dist"] for x in g]))):
            g.append(r)
        else:
            groups.append([r])
    main = [g for g in groups if not (len(g) <= 2 and max(x["time"] for x in g) < STRIDE_S)]
    return [r for g in (main or groups) for r in g]


def _match(a, b, ski=False):
    """order-preserving alignment (DP) of rep lists a (anchor) and b by nearest distance / duration:
    c = min(|log dist ratio|, |log time ratio|) when that is <= MATCH_SAME (same prescribed rep), else the max
    of both; pairs with c > MATCH_TOL can't match; pair cost max(0, c - MATCH_SAME) (GPS noise is free),
    unmatched rep MATCH_GAP, tiny index-shift penalty so ties leave the trailing reps unmatched.
    ski=True (terrain-defined ski reps): c = the closer of both, tol / free band SKI_MATCH.
    Returns {j_b: i_a}."""
    n, m = len(a), len(b)
    INF = 1e9
    D = np.full((n + 1, m + 1), INF); D[0, :] = np.arange(m + 1) * MATCH_GAP; D[:, 0] = np.arange(n + 1) * MATCH_GAP
    B = np.zeros((n + 1, m + 1), int)  # 0 diag, 1 up (a unmatched), 2 left (b unmatched)
    B[1:, 0] = 1; B[0, 1:] = 2
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            ra, rb = a[i - 1], b[j - 1]
            ct = abs(math.log(max(ra["time"], 1) / max(rb["time"], 1)))
            # no distance (indoor / no-GPS reps): duration only
            cd = abs(math.log(max(ra["dist"], 1) / max(rb["dist"], 1))) if (ra["dist"] or 0) > 0 and (rb["dist"] or 0) > 0 else ct
            # same prescribed distance (or duration) if either is within MATCH_SAME; else both must be close
            tol, same = SKI_MATCH if ski else (MATCH_TOL, MATCH_SAME)
            c = min(cd, ct) if (ski or min(cd, ct) <= same) else max(cd, ct)
            dg = D[i - 1, j - 1] + (max(0.0, c - same) + 1e-3 * abs(i - j) if c <= tol else INF)
            up, lf = D[i - 1, j] + MATCH_GAP, D[i, j - 1] + MATCH_GAP
            k = int(np.argmin([dg, up, lf]))
            D[i, j] = (dg, up, lf)[k]; B[i, j] = k
    out, i, j = {}, n, m
    while i > 0 or j > 0:
        k = B[i, j]
        if k == 0:
            out[j - 1] = i - 1; i -= 1; j -= 1
        elif k == 1:
            i -= 1
        else:
            j -= 1
    return out


def _slots(reps_by, match="auto", ski=False):
    """slot per (session, rep): anchor (first) session's reps are slots 0..n-1; unmatched reps of other sessions
    get extra slots right after the anchor slot they follow. Returns slot keys (sorted), {sid: [slot idx per rep]}"""
    ids = list(reps_by)
    anchor = reps_by[ids[0]]
    keys = {(i, 0) for i in range(len(anchor))}
    raw = {ids[0]: [(i, 0) for i in range(len(anchor))]}
    method = {ids[0]: "anchor"}
    for sid in ids[1:]:
        b = reps_by[sid]
        mp, method[sid] = (_match(anchor, b, ski), "distance") if match != "index" else ({}, "index")
        if len(mp) < max(1, 0.5 * min(len(anchor), len(b))):   # different workouts: rep 1 vs rep 1, ...
            mp, method[sid] = {j: j for j in range(min(len(anchor), len(b)))}, "index"
        prev, k, lst = -1, 0, []
        for j in range(len(reps_by[sid])):
            if j in mp:
                prev, k = mp[j], 0
                lst.append((prev, 0))
            else:
                k += 1
                lst.append((prev, k))
        raw[sid] = lst
        keys.update(lst)
    order = sorted(keys)
    idx = {kk: n for n, kk in enumerate(order)}
    labels = [f"{i + 1}" if k == 0 else f"{i + 1}+{k}" for i, k in order]
    return labels, [k == 0 for _, k in order], {sid: [idx[kk] for kk in lst] for sid, lst in raw.items()}, method


def _hr_at(G, t):
    if "hr" not in G or t is None:
        return None
    i = int(round(t))
    if i < 0 or i > G["T"]:
        return None
    return _r(G["hr"][i], 0)


def _d_at(G, t):
    return np.interp(t, np.arange(G["T"] + 1), G["d"])


def _rep_speed(G, t):
    return (_d_at(G, np.asarray(t) + 1.5) - _d_at(G, np.asarray(t) - 1.5)) / 3


def _break_stats(S, a, b, nxt_start):
    """break from rep end a to next rep start b (elapsed s)"""
    G = S["G"]
    ia, ib = int(math.ceil(a)), int(b)
    v = G["v"][ia:ib + 1]
    dur = b - a
    dist = float(_d_at(G, b) - _d_at(G, a))
    stand = int(np.sum(v < 0.5))
    mean_v = dist / dur if dur > 0 else 0.0
    kind = "stand" if stand >= 0.7 * len(v) else ("jog" if mean_v >= 1.8 else ("walk" if mean_v >= 0.8 else "stand+walk"))
    hr = G.get("hr")
    peak = None
    if hr is not None:
        w = hr[max(0, int(a) - 5): min(G["T"], int(a) + 16)]
        peak = _r(np.nanmax(w), 0) if np.isfinite(w).any() else None
    row = {"start_s": _r(a, 1), "dur_s": _r(dur, 1), "dist_m": _r(dist, 0), "speed_kmh": _r(mean_v * 3.6, 2),
           "standing_s": stand, "kind": kind, "hr_peak": peak}
    if S.get("no_dist"):
        row.update(dist_m=None, speed_kmh=None, standing_s=None, kind=None)
    for s_ in HR_AT:
        h = _hr_at(G, a + s_) if a + s_ <= b else None
        row[f"hr_{s_}"] = h
        row[f"drop_{s_}"] = (peak - h) if peak is not None and h is not None else None
    hv = hr[ia:ib + 1] if hr is not None else np.array([])
    row["hr_min"] = _r(np.nanmin(hv), 0) if len(hv) and np.isfinite(hv).any() else None
    row["hr_next_start"] = _hr_at(G, nxt_start)
    return row


def _interval_modes(st, sess, mode, reference, max_points, rep_axis, gap_s, rep_gap_s, workers, match):
    R = st.reps([S["id"] for S in sess], workers)
    reps_by, info = {}, {}
    for S in sess:
        r = R.get(S["id"])
        rr = _main_reps((r or {}).get("reps") or [], str((r or {}).get("mode") or "").startswith("ski"))
        if len(rr) >= 1:
            reps_by[S["id"]] = rr
            info[S["id"]] = {"det_mode": r["mode"], "n_detected": len(r["reps"]), "n_used": len(rr)}
    if not reps_by:
        raise ValueError("no reps found in any session")
    SS = {S["id"]: S for S in sess if S["id"] in reps_by}
    ids_ = list(reps_by)
    ski = all(str(info[sid]["det_mode"]).startswith("ski") for sid in reps_by)
    labels, anchored, slot_of, method = _slots(reps_by, match, ski)
    for sid in ids_:
        info[sid]["match"] = method[sid]
    ns = len(labels)
    ids = list(reps_by)

    # ---- per-rep table
    rows = {sid: [None] * ns for sid in ids}
    for sid in ids:
        G = SS[sid]["G"]
        for j, (r, k) in enumerate(zip(reps_by[sid], slot_of[sid])):
            v = r["dist"] / r["time"] if r["time"] and r["dist"] else None
            rows[sid][k] = {"n": j + 1, "start_s": _r(r["start"], 1), "time_s": _r(r["time"], 1), "dist_m": _r(r["dist"], 0),
                            "speed_kmh": _r(v * 3.6 if v else None, 2), "pace_s_km": _r(_pace(v), 1),
                            "hr": r["hr"], "hr_max": r["maxhr"], "hr_start": r["hr_start"], "hr_end": _hr_at(G, r["end"])}

    def ref_of(table, keys):
        """reference value per slot: anchor session row, or median over sessions in the slot"""
        out = []
        for k in range(ns):
            if reference == "first":
                out.append(table[ids[0]][k])
                continue
            vals = [table[s][k] for s in ids if table[s][k]]
            out.append({key: (float(np.median([x[key] for x in vals if x.get(key) is not None]))
                              if any(x.get(key) is not None for x in vals) else None) for key in keys} if vals else None)
        return out

    rkeys = ("time_s", "dist_m", "pace_s_km", "speed_kmh", "hr", "hr_max", "hr_end")
    rref = ref_of(rows, rkeys)
    for sid in ids:
        for k, row in enumerate(rows[sid]):
            rf = rref[k]
            if row and rf:
                # time at the reference distance (pace-based) so 1016 m vs 1000 m reps compare fairly
                row["d_time_s"] = _r(row["time_s"] * rf["dist_m"] / row["dist_m"] - rf["time_s"], 1) if row["dist_m"] and rf.get("dist_m") else None
                row["d_pace_s_km"] = _r(row["pace_s_km"] - rf["pace_s_km"], 1) if row["pace_s_km"] and rf.get("pace_s_km") else None
                for key in ("hr", "hr_max", "hr_end"):
                    row[f"d_{key}"] = _r(row[key] - rf[key], 0) if row.get(key) is not None and rf.get(key) is not None else None

    # ---- per-break table (break after the rep in slot k)
    brows = {sid: [None] * ns for sid in ids}
    for sid in ids:
        rr, sl = reps_by[sid], slot_of[sid]
        for j in range(len(rr) - 1):
            b = _break_stats(SS[sid], rr[j]["end"], rr[j + 1]["start"], rr[j + 1]["start"])
            b["after_rep"] = j + 1; b["next_slot"] = labels[sl[j + 1]]
            brows[sid][sl[j]] = b
    bkeys = ("dur_s", "dist_m", "speed_kmh", "drop_30", "drop_60", "hr_next_start", "hr_peak")
    bref = ref_of(brows, bkeys)
    for sid in ids:
        for k, b in enumerate(brows[sid]):
            rf = bref[k]
            if b and rf:
                for key in ("dur_s", "drop_30", "drop_60", "hr_next_start"):
                    b[f"d_{key}"] = _r(b[key] - rf[key], 1) if b.get(key) is not None and rf.get(key) is not None else None

    # ---- per-session rep summary
    rsum = {}
    for sid in ids:
        rr = reps_by[sid]
        tt = sum(r["time"] for r in rr); dd = sum(r["dist"] for r in rr)
        hrs = [(r["hr"], r["time"]) for r in rr if r["hr"]]
        bl = [b for b in brows[sid] if b]
        rsum[sid] = {"n_reps": len(rr), "rep_time_s": _r(tt, 1), "rep_dist_m": _r(dd, 0), "rep_pace_s_km": _r(1000 * tt / dd, 1) if dd else None,
                     "rep_hr": _r(sum(h * t for h, t in hrs) / sum(t for _, t in hrs), 0) if hrs else None,
                     "rep_hr_max": max((r["maxhr"] for r in rr if r["maxhr"]), default=None),
                     "break_total_s": _r(sum(b["dur_s"] for b in bl), 0),
                     "break_median_s": _r(np.median([b["dur_s"] for b in bl]), 0) if bl else None,
                     "drop_60_median": _r(np.median([b["drop_60"] for b in bl if b["drop_60"] is not None]), 0)
                     if any(b["drop_60"] is not None for b in bl) else None,
                     **info[sid]}
    # matched-slot pace vs reference (same slots only, so 5×1000 vs 6×1000 compares like for like)
    for sid in ids:
        both = [k for k in range(ns) if rows[sid][k] and rref[k]]
        if both:
            t1 = sum(rows[sid][k]["time_s"] for k in both); d1 = sum(rows[sid][k]["dist_m"] for k in both)
            t0 = sum(rref[k]["time_s"] for k in both); d0 = sum(rref[k]["dist_m"] for k in both)
            rsum[sid]["matched_slots"] = len(both)
            rsum[sid]["d_rep_pace_matched_s_km"] = _r(1000 * (t1 / d1 - t0 / d0), 1) if d1 and d0 else None
            hh = [(rows[sid][k]["hr"], rref[k]["hr"]) for k in both if rows[sid][k]["hr"] and rref[k]["hr"]]
            rsum[sid]["d_rep_hr_matched"] = _r(np.mean([a - b for a, b in hh]), 0) if hh else None

    slots = [{"slot": k, "label": labels[k], "anchor": anchored[k],
              "n_sessions": sum(1 for sid in ids if rows[sid][k])} for k in range(ns)]
    out = {"slots": slots, "anchor_id": ids[0], "rep_summary": {str(s): v for s, v in rsum.items()},
           "no_reps": [S["id"] for S in sess if S["id"] not in reps_by]}
    if mode == "intervals":
        out.update(_rep_timeline(SS, reps_by, slot_of, rows, ns, max_points, rep_axis, gap_s))
        out["reps"] = {str(s): rows[s] for s in ids}
        out["reps_ref"] = rref if reference == "median" else None
        out["breaks"] = {str(s): brows[s] for s in ids}
    else:
        out.update(_break_timeline(SS, reps_by, slot_of, brows, ns, max_points, rep_gap_s))
        out["breaks"] = {str(s): brows[s] for s in ids}
        out["breaks_ref"] = bref if reference == "median" else None
        out["reps"] = {str(s): rows[s] for s in ids}
    return out


def _stride(cols, max_points):
    n = len(cols["x"])
    if n <= max_points:
        return cols
    k = math.ceil(n / max_points)
    return {key: (v[::k] if isinstance(v, np.ndarray) else v) for key, v in cols.items()}


def _pack(c, max_points):
    c = {k: np.asarray(v, float) for k, v in c.items()}
    c = _stride(c, max_points)
    return {"x": _lst(c["x"], 1), "t": _lst(c["t"], 0), "speed_kmh": _lst(c["speed_kmh"], 2), "hr": _lst(c["hr"], 0),
            "phase": [int(v) for v in c["phase"]], "slot": [None if v < 0 else int(v) for v in c["slot"]]}


PHASES = {0: "pre", 1: "rep", 2: "break", 3: "post"}


def _rep_timeline(SS, reps_by, slot_of, rows, ns, max_points, rep_axis, gap_s):
    """rep-aligned x: each slot starts at the same x for every session; breaks squeezed into a fixed gap"""
    norm = rep_axis == "normalized"
    W = [100.0 if norm else max(math.ceil(r["time_s"]) for r in (rows[s][k] for s in rows) if r) for k in range(ns)]
    X0 = np.concatenate([[0.0], np.cumsum([w + gap_s for w in W])])[:ns]
    series = {}
    for sid, rr in reps_by.items():
        S = SS[sid]; G = S["G"]; sl = slot_of[sid]
        xs, ts, ph, sl_ = [], [], [], []

        def add(t, x, p, s_):
            xs.append(x); ts.append(t); ph.append(np.full(len(t), p)); sl_.append(np.full(len(t), s_))
        a0 = rr[0]["start"]
        t = np.arange(max(0.0, a0 - PRE_S), a0, 1.0)
        add(t, X0[sl[0]] - (a0 - t) * (gap_s / PRE_S if norm else 1.0), 0, -1)
        for j, r in enumerate(rr):
            k = sl[j]
            t = np.append(np.arange(r["start"], r["end"], 1.0), r["end"])
            add(t, X0[k] + (t - r["start"]) * (100 / r["time"] if norm else 1.0), 1, k)
            if j + 1 < len(rr):
                b0, b1 = r["end"], rr[j + 1]["start"]
                t = np.arange(math.floor(b0) + 1, b1, 1.0)
                g0, g1 = X0[k] + W[k], X0[sl[j + 1]]
                add(t, g0 + (g1 - g0) * (t - b0) / max(b1 - b0, 1e-6), 2, -1)
        t = np.concatenate(ts)
        sp = _rep_speed(G, t) * 3.6 if not S["no_dist"] else np.full(len(t), np.nan)
        hr = np.interp(t, np.arange(G["T"] + 1), G["hr"]) if "hr" in G else np.full(len(t), np.nan)
        series[str(sid)] = _pack({"x": np.concatenate(xs), "t": t, "speed_kmh": sp, "hr": hr,
                                  "phase": np.concatenate(ph), "slot": np.concatenate(sl_)}, max_points)
    return {"axis": {"x": "rep_aligned_pct" if norm else "rep_aligned_s", "rep_axis": rep_axis, "gap_s": gap_s,
                     "slot_x0": _lst(X0, 1), "slot_width": _lst(W, 1), "phases": PHASES,
                     "note": "x of a rep point = slot_x0 + seconds (or % of the rep) since the rep start; break "
                             "points are squeezed linearly into [slot end, next slot start]"},
            "series": series}


def _break_timeline(SS, reps_by, slot_of, brows, ns, max_points, rep_gap_s, post_s=120):
    """break-aligned x: each break (after the rep in slot k) starts at the same x; reps squeezed to rep_gap_s"""
    BW = []
    for k in range(ns):
        d = [b["dur_s"] for b in (brows[s][k] for s in brows) if b]
        BW.append(math.ceil(max(d)) if d else 0.0)
    # last rep of each session: show post_s of recovery in its slot if that slot has no break
    for sid, rr in reps_by.items():
        k = slot_of[sid][-1]
        if BW[k] == 0:
            BW[k] = post_s
    XR0 = np.concatenate([[0.0], np.cumsum([rep_gap_s + w for w in BW])])[:ns]    # collapsed rep region start
    XB0 = XR0 + rep_gap_s                                                          # break region start
    series = {}
    for sid, rr in reps_by.items():
        S = SS[sid]; G = S["G"]; sl = slot_of[sid]
        xs, ts, ph, sl_ = [], [], [], []

        def add(t, x, p, s_):
            xs.append(x); ts.append(t); ph.append(np.full(len(t), p)); sl_.append(np.full(len(t), s_))
        for j, r in enumerate(rr):
            k = sl[j]
            t = np.append(np.arange(r["start"], r["end"], 1.0), r["end"])
            add(t, XR0[k] + rep_gap_s * (t - r["start"]) / max(r["time"], 1e-6), 1, k)
            if j + 1 < len(rr):
                b0, b1, p = r["end"], rr[j + 1]["start"], 2
            else:
                b0, b1, p = r["end"], min(r["end"] + post_s, G["T"]), 3
            t = np.arange(math.floor(b0) + 1, b1, 1.0)
            add(t, XB0[k] + (t - b0), p, k)
        t = np.concatenate(ts)
        sp = (_smooth(G["v"], 3)[np.clip(np.round(t).astype(int), 0, G["T"])] * 3.6 if not S["no_dist"]
              else np.full(len(t), np.nan))
        hr = np.interp(t, np.arange(G["T"] + 1), G["hr"]) if "hr" in G else np.full(len(t), np.nan)
        series[str(sid)] = _pack({"x": np.concatenate(xs), "t": t, "speed_kmh": sp, "hr": hr,
                                  "phase": np.concatenate(ph), "slot": np.concatenate(sl_)}, max_points)
    return {"axis": {"x": "break_aligned_s", "rep_gap_s": rep_gap_s, "break_x0": _lst(XB0, 1), "break_width": _lst(BW, 1),
                     "rep_x0": _lst(XR0, 1), "phases": PHASES,
                     "note": "x of a break point = break_x0[slot of the rep before] + seconds since the break start; "
                             "reps squeezed to rep_gap_s; after the last rep up to 120 s of recovery (phase post)"},
            "series": series}


# ---------------------------------------------------------------- CLI
def select_ids(person, ids=(), route=None, sig=None, place=None, last=None):
    """explicit ids first, then route members / signature matches, most recent first"""
    g = store(person).groups()
    A = g["activities"]
    out = [int(i) for i in ids]
    cand = []
    if route:
        cand = list(g["routes"][route]["members"])
    elif sig:
        cand = [int(k) for k, a in A.items() if a.get("sig") == sig and (not place or a.get("place") == place)]
    cand.sort(key=lambda i: A[str(i)]["date"], reverse=True)
    for i in cand:
        if i not in out:
            out.append(i)
    return out[:last] if last else out


def _mm(s):
    if s is None:
        return "-"
    s = int(round(s)); sg_ = "-" if s < 0 else ""
    s = abs(s)
    return f"{sg_}{s // 60}:{s % 60:02d}"


def print_result(r):
    ref = r["reference"]
    print(f"mode {r['mode']}, reference {ref.get('id') or 'median of ' + str(ref.get('n'))}, {len(r['ids'])} sessions, "
          f"{r['compute_s']} s" + (f", skipped {r['skipped']}" if r["skipped"] else ""))
    print(f"{'id':>12} {'date':10} {'type':16} {'moving':>7} {'pace':>6} {'GAP':>6} {'HR':>4} {'eff':>6} | "
          f"{'Δmov':>6} {'Δpace':>6} {'ΔHR':>4} {'Δeff%':>6}")
    for s in r["sessions"]:
        d = s["delta"]
        print(f"{s['id']:>12} {s['date']:10} {(s['type'] or '')[:16]:16} {_mm(s['moving_s']):>7} {_mm(s['moving_pace_s_km']):>6} "
              f"{_mm(s['gap_pace_s_km']):>6} {s['hr_avg'] or '-':>4} {s['eff_m_per_beat'] or '-':>6} | {_mm(d['moving_s']):>6} "
              f"{d['moving_pace_s_km'] if d['moving_pace_s_km'] is not None else '-':>6} {d['hr_avg'] if d['hr_avg'] is not None else '-':>4} "
              f"{d.get('eff_pct', '-') if d.get('eff_pct') is not None else '-':>6}")
    if r["mode"] == "distance":
        print(f"\nlegs ({r['legs_kind']}, x = {r['axis']['x']}): moving time m:ss (gap to reference at leg end, s)")
        legs = r["legs"]
        if len(legs) > 14:
            print(f"  ({len(legs)} legs, showing the first 14)")
            legs = legs[:14]
        print(f"{'id':>12} " + " ".join(f"{l['to_m'] / 1000:>10.2f}" for l in legs))
        for i in r["ids"]:
            cells = []
            for l in legs:
                x = l["sessions"].get(str(i))
                cells.append(f"{_mm(x['moving_s'])}({x['delta_moving_s']:+.0f})" if x and x.get("delta_moving_s") is not None else "-")
            print(f"{i:>12} " + " ".join(c.rjust(10) for c in cells))
    elif r["mode"] in ("intervals", "breaks"):
        labs = [s["label"] for s in r["slots"]]
        print(f"\nreps (slots {', '.join(labs)}): time m:ss / pace / HR avg (Δtime at ref distance)")
        for i in r["ids"]:
            rows = r["reps"].get(str(i))
            if rows is None:
                print(f"{i:>12} no reps"); continue
            rs = r["rep_summary"][str(i)]
            cells = [f"{_mm(x['time_s'])}/{x['dist_m']}/{x['hr']}({x.get('d_time_s') or 0:+.0f})" if x else "-" for x in rows]
            print(f"{i:>12} {rs['n_reps']:>2}r " + " ".join(c.rjust(20) for c in cells))
        print("\nbreaks after each slot: dur s / kind / drop60 / HR at next start")
        for i in r["ids"]:
            rows = r["breaks"].get(str(i)) or []
            cells = [f"{x['dur_s']:.0f}/{x['kind']}/{x['drop_60']}/{x['hr_next_start']}" if x else "-" for x in rows]
            print(f"{i:>12}     " + " ".join(c.rjust(20) for c in cells))


def validate(person="adrian"):
    """sanity checks on real data (printed): see docs/compare.md#validation"""
    g = store(person).groups()
    A = g["activities"]
    ok = lambda c: "OK " if c else "BAD"
    # 1. Holmenkollen route R3, all sessions, route position + ghost gap
    rid = "R3"
    ids = select_ids(person, route=rid)
    r = compare(ids, "distance")
    print(f"[R3] {g['routes'][rid]['name']}: {len(r['ids'])} sessions, axis {r['axis']['x']}, step {r['axis']['step_m']} m, {r['compute_s']} s")
    lt = {s["id"]: s for s in g["routes"][rid]["legs_table"]["sessions"]}
    diffs, worst_jump, nonmono, jumps_all = [], 0.0, 0, []
    wpm = [w["km"] * 1000 for w in g["routes"][rid]["waypoints"]]
    for i in r["ids"]:
        s = r["series"][str(i)]
        tm = np.array([np.nan if v is None else v for v in s["moving_s"]], float)
        nonmono += int(np.sum(np.diff(tm[np.isfinite(tm)]) < -0.5))
        gm = np.array([np.nan if v is None else v for v in s["delta_moving_s"]], float)
        st = r["axis"]["step_m"]
        # a gap can't change faster than step / slowest plausible speed: here > 60 s per 20 m = stalled projection
        xx = np.array(s["x"], float)[1:]
        far = np.min(np.abs(xx[:, None] - np.array(wpm)[None, :]), 1) > 150
        dj = np.abs(np.diff(gm))[far]
        j = np.nanmax(dj) if np.isfinite(dj).any() else 0
        worst_jump = max(worst_jump, float(j))
        jumps_all += list(dj[np.isfinite(dj)])
        for k, l in enumerate(r["legs"]):
            mine = l["sessions"].get(str(i)); theirs = (lt.get(i) or {}).get("legs", [None] * 99)[k]
            # first / last leg differ by design (session_groups starts after 20 m of progress, ends 30 m early)
            if 0 < k < len(r["legs"]) - 1 and mine and theirs and not theirs.get("partial") and not mine.get("partial"):
                diffs.append(mine["moving_s"] - theirs["moving_s"])
    d = np.abs(diffs)
    print(f"  {ok(nonmono == 0)} moving time vs position monotone ({nonmono} decreases)")
    print(f"  {ok(worst_jump < 20)} gap change per {st:.0f} m bin away (> 150 m) from common stops: p99 "
          f"{np.percentile(jumps_all, 99):.1f} s, max {worst_jump:.1f} s (moving time; slow walking at stops excluded)")
    print(f"  {ok(np.median(d) <= 15)} leg moving time vs session_groups legs_table (middle legs, n={len(d)}): "
          f"median |Δ| {np.median(d):.1f} s, p90 {np.percentile(d, 90):.1f} s")
    fin = sorted(((r["series"][str(i)]["delta_moving_s"][-1] or 0), i) for i in r["ids"][1:])
    print(f"  ghost gap at the finish vs {r['ids'][0]} (moving time, s): best {fin[0][0]:+.0f} ({fin[0][1]}), "
          f"worst {fin[-1][0]:+.0f} ({fin[-1][1]})")
    rm = compare(ids, "distance", reference="median")
    print("  median reference: finish gaps " + ", ".join(f"{(rm['series'][str(i)]['delta_moving_s'][-1] or 0):+.0f}" for i in rm["ids"]))

    # 2. Bislet 6×1000: rep alignment
    ids = select_ids(person, sig="6×1000", place="Bislet", last=8)
    r = compare(ids, "intervals")
    print(f"\n[6×1000 Bislet] {len(r['ids'])} sessions, {len(r['slots'])} slots, {r['compute_s']} s")
    x0 = r["axis"]["slot_x0"]
    bad, tdiff = 0, []
    for i in r["ids"]:
        s = r["series"][str(i)]
        x = np.array(s["x"]); v = np.array([np.nan if q is None else q for q in s["speed_kmh"]]); ph = np.array(s["phase"])
        for k, xs in enumerate(x0):
            pre = v[(x >= xs - 12) & (x < xs - 3) & (ph != 1)]
            post = v[(x >= xs + 5) & (x < xs + 15) & (ph == 1)]
            if len(post) and len(pre) and not np.nanmean(post) > np.nanmean(pre) + 3:
                bad += 1
        gr = A[str(i)].get("reps") or []
        mine = [x for x in r["reps"][str(i)] if x]
        for a_, b_ in zip(sorted(mine, key=lambda q: q["n"]), [q for q in gr if q[0] >= STRIDE_S]):
            tdiff.append(abs(a_["time_s"] - b_[0]))
    print(f"  {ok(bad == 0)} speed jumps at every slot start (pre-start < post-start − 3 km/h): {bad} misses")
    print(f"  {ok(max(tdiff) < 0.2)} rep times = groups.json reps (max |Δ| {max(tdiff):.2f} s)")
    b = compare(ids, "breaks")
    xb = b["axis"]["break_x0"]
    jumps = 0
    for i in b["ids"]:
        s = b["series"][str(i)]
        x = np.array(s["x"]); ph = np.array(s["phase"]); sl = np.array([-1 if q is None else q for q in s["slot"]])
        for k, xs in enumerate(xb):
            m = (ph >= 2) & (sl == k)
            if m.any() and abs(x[m].min() - xs - 1) > 1.01:
                jumps += 1
    print(f"  {ok(jumps == 0)} every break starts at its common x (break-aligned view): {jumps} misses")
    for i in b["ids"][:4]:
        rows = [q for q in b["breaks"][str(i)] if q]
        print(f"    {i} {A[str(i)]['date']} breaks " + " ".join(f"{q['dur_s']:.0f}s/{q['kind']}/−{q['drop_60']}" for q in rows))

    # 3. different rep counts
    for pair in ([24874084265, 24288925182], [8884710494, 8835446895], [24874084265, 24713319874]):
        r = compare(pair, "intervals")
        labs = [s["label"] for s in r["slots"]]
        m = {str(i): [(q["n"] if q else None) for q in r["reps"][str(i)]] for i in r["ids"]}
        dd = {str(i): [(q["dist_m"] if q else None) for q in r["reps"][str(i)]] for i in r["ids"]}
        print(f"\n[mixed] {' vs '.join(A[str(i)].get('sig') or '?' for i in pair)}: slots {labs}")
        for i in r["ids"]:
            print(f"   {i} rep# {m[str(i)]} dist {dd[str(i)]}  Δpace matched {r['rep_summary'][str(i)].get('d_rep_pace_matched_s_km')}")

    # 4. ski intervals (ski_intervals.py): same reps as groups.json, none in non-interval ski sessions
    ski = [int(k) for k, a in A.items() if a.get("sport") == "ski" and k.isdigit() and store(person).fit(k) is not None]
    R = store(person).reps(ski)
    mism, extra, n_iv = 0, [], 0
    for i in ski:
        a = A[str(i)]; rr = (R.get(i) or {}).get("reps") or []
        gr = a.get("reps") or []
        if a.get("type") == "ski_intervals":
            n_iv += 1
            mism += int(len(rr) != len(gr) or any(abs(x["time"] - y[0]) > 0.2 for x, y in zip(rr, gr)))
        elif rr:
            extra.append(i)
    r3 = g["routes"]["R3"]["members"]
    r3iv = [i for i in r3 if A[str(i)].get("type") == "ski_intervals"]
    print(f"\n[ski] {len(ski)} ski sessions, {n_iv} ski_intervals ({len(r3iv)} on R3)")
    print(f"  {ok(mism == 0)} ski rep times = groups.json reps ({mism} sessions differ)")
    print(f"  {ok(not extra)} no reps in non-interval ski sessions ({len(extra)}: {extra[:5]})")
    easy = [i for i in r3 if A[str(i)].get("type") == "ski_easy"]
    try:
        compare(easy[:3], "intervals")
        print("  BAD easy R3 climbs got reps")
    except ValueError as e:
        print(f"  OK  easy R3 climbs ({len(easy)}) have no reps: intervals mode raises '{e}'")
    if len(r3iv) >= 2:
        r = compare(r3iv, "intervals")
        b = compare(r3iv, "breaks")
        print(f"  R3 interval climbs: slots {[s['label'] for s in r['slots']]}; " + "; ".join(
            f"{A[str(i)]['date']} {A[str(i)]['sig']} rest {r['rep_summary'][str(i)]['break_median_s']} s "
            f"drop60 {r['rep_summary'][str(i)]['drop_60_median']}" for i in r["ids"]))
        print(f"  breaks view: {len(b['axis']['break_x0'])} break slots")
    lw = [i for i in ski if A[str(i)].get("type") == "ski_intervals" and "lap workout" in (A[str(i)].get("type_detail") or "")]
    if len(lw) >= 2:
        r = compare(lw[:4], "intervals")
        print(f"  lap workouts (no GPS, HR only): {[A[str(i)]['sig'] for i in r['ids']]} slots {len(r['slots'])}, "
              f"matched {[r['rep_summary'][str(i)]['match'] for i in r['ids']]}")


def main():
    ap = argparse.ArgumentParser(description="compare sessions (see docs/compare.md)")
    ap.add_argument("person")
    ap.add_argument("mode", choices=MODES + ("validate",))
    ap.add_argument("ids", nargs="*", type=int, help="session ids; the first is the reference")
    ap.add_argument("--route", help="add all members of this route (e.g. R3)")
    ap.add_argument("--sig", help="add all interval sessions with this signature (e.g. 6×1000)")
    ap.add_argument("--place", help="with --sig: only this place")
    ap.add_argument("--last", type=int, help="keep only the first N ids (explicit ids, then most recent)")
    ap.add_argument("--reference", default="first", choices=("first", "median"))
    ap.add_argument("--axis", default="auto", choices=("auto", "route", "own"))
    ap.add_argument("--rep-axis", default="absolute", choices=("absolute", "normalized"))
    ap.add_argument("--match", default="auto", choices=("auto", "index"))
    ap.add_argument("--json", help="write the result dict here")
    a = ap.parse_args()
    if a.mode == "validate":
        validate(a.person)
        return
    ids = select_ids(a.person, a.ids, a.route, a.sig, a.place, a.last)
    r = compare(ids, a.mode, person=a.person, reference=a.reference, axis=a.axis, rep_axis=a.rep_axis, match=a.match)
    print_result(r)
    if a.json:
        js = json.dumps(r, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        Path(a.json).write_text(js)
        print(f"wrote {a.json} ({len(js) / 1e3:.0f} kB)")


if __name__ == "__main__":
    main()

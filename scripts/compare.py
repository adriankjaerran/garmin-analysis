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
import itertools
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
CACHE_VERSION = 2   # 2: clock jumps fixed (session_groups.READER_VERSION 2), capped projection jump window
MODES = ("elapsed", "moving", "distance", "route_position", "intervals", "breaks")
NO_DIST_MODES = ("elapsed", "intervals", "breaks")   # sessions without distance (no-GPS lap workouts): HR only

# ---- parameters
MAX_POINTS = 1500          # series points per session
GAP_S = 60                 # intervals view: visual width of a collapsed break (s on the x axis)
REP_GAP_S = 30             # breaks view: visual width of a collapsed rep
PRE_S = 60                 # intervals view: seconds shown before the first rep
REP_LEAD_S = 15            # per-rep series (Rep N / Per rep views): real seconds before each rep start ...
REP_POST_S = 30            # ... and after its end (the start of the next break, never into the next rep)
MATCH_TOL = 0.2            # rep matching: max |log ratio| of distance or time (about +-22 %)
MATCH_GAP = 0.25           # rep matching: cost of leaving a rep unmatched
MATCH_SAME = 0.05          # rep matching: distance or time within 5 % = the same prescribed rep
SKI_MATCH = (0.45, 0.10)   # ski reps (terrain-defined, durations vary): closer of distance / time, tol and free band
STRIDE_S = 35              # reps < 35 s in groups of <= 2 are strides (left out, as in the signature)
HR_AT = (30, 60, 90)       # breaks: HR read at these seconds after the break start
LEG_TOL_M = 100.0          # distance legs: a session joining / stopping this close to a leg end covers it
SPEED_H_M = 50.0           # distance axis: speed = 2h / time over +-h metres
JUMP_MIN_M = 100.0         # route axis: progress jumps between two on-route fixes larger than this ...
JUMP_D_K = 1.5             # ... and > JUMP_MIN_M + 1.5 x own distance moved meanwhile, or faster than
JUMP_V = (2.5, 25.0, 60.0)  # max(2.5 x own moving speed, 25 km/h run / 60 km/h ski): stretch not covered
START_STALL_S, START_STALL_K = 20.0, 4.0   # distance axis: start bins slower than max(20 s, 4 x median bin) ...
START_TRIM_M = 300.0       # ... within the first 300 m are cut off (the session starts when it leaves)
OFF_ROUTE_S = 120          # route axis: progress made while off the route this long: not covered ...
OFF_SAME_K, OFF_SAME_M = 0.10, 50.0   # ... unless the own distance matched it (+-10 % + 50 m): parallel path
LEG_UNCOVERED = 0.10       # legs with more than this share not covered: no leg row for that session
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
        # Bislett indoor: watch distance is made up; whole-lap reps only (n x 546.5 m - 7.5 m), else distance-free
        no_dist = not sg.indoor_grid(G, self.act(i) or {}) or bool(G["d"][-1] < 100)
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
        out = {int(i): (c.get(("reps", int(i))) or {}).get("r") for i in ids}
        for i in ids:   # Bislett indoor: reps = the manual run laps (groups.json), whole laps n x 546.5 m - 7.5 m
            ind = (self.act(i) or {}).get("indoor_bislett")
            if ind and ind.get("rep_laps"):
                out[int(i)] = self._indoor_reps(i, ind)
        return out

    def _indoor_reps(self, i, ind):
        hr = self.session(i)["G"].get("hr")
        reps = []
        for (t0, dur, _), m in zip(ind["rep_laps"], ind["rep_m"]):
            a, b = int(round(t0)), int(round(t0 + dur))
            h = hr[a:b] if hr is not None else np.array([])
            h = h[np.isfinite(h)] if len(h) else h
            reps.append({"start": t0, "end": t0 + dur, "time": dur, "dist": m, "speed": m / dur if m else None,
                         "hr": round(float(h.mean())) if len(h) > 5 else None, "maxhr": round(float(h.max())) if len(h) > 5 else None,
                         "hr_start": round(float(h[0])) if len(h) else None, "hr_end": round(float(h[-1])) if len(h) else None,
                         "dist_source": "bislett_indoor_laps" if m else None})
        return {"mode": "indoor", "reps": reps, "laps": []}

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


# ---------------------------------------------------------------- common start line ("align starts")
START_RADIUS_M = 40.0      # start line half-width: max cross-track distance at the crossing
LINE_STEP_M = 10.0         # anchor path / template spacing (m of GPS path)
LINE_TPL_M = 200.0         # template: the anchor's first 200 m after the line ...
LINE_SHAPE_TOL = 25.0      # ... a session must follow within a mean 25 m (order-free nearest points) ...
LINE_ORDER_MIN = 0.7       # ... in the same order (>= 70 % of the steps forward: rejects the reverse direction)
LINE_DIR_COS = 0.5         # direction of motion within 60 degrees of the line's heading / the anchor's path
AT_LINE_M = 20.0           # a session starting <= 20 m past the line counts as starting at the line
JOIN_SCAN_M = 100.0        # late joiners: the first 100 m of own GPS path are searched for a point on the anchor's path
JOIN_DAWDLE_S = 60.0       # ... a late joiner that takes longer to leave its join point starts when it leaves
SIDE_JOIN_M = 150.0        # ... or later (from a side street), onto the anchor's first 150 m after the line
POINT_NEAR_M = 30.0        # start=lat,lon: the anchor's first second within 30 m of the point (else closest approach)


def _parse_start(start, ids):
    """start=None | 'ref' | <id> | (lat, lon) | 'lat,lon' -> (kind, anchor_id, point)"""
    if start is None or start is False or start == "":
        return None
    if isinstance(start, (tuple, list)):
        return "point", ids[0], (float(start[0]), float(start[1]))
    s = str(start).strip()
    if s in ("ref", "first", "reference"):
        return "ref", ids[0], None
    if "," in s:
        a, b = s.split(",", 1)
        return "point", ids[0], (float(a), float(b))
    if s.isdigit():
        return "session", int(s), None
    raise ValueError("start must be 'ref', a session id or 'lat,lon'")


def _geo(S):
    """cumulative GPS path length per grid second (m). Not the watch distance field: at the start of a file it often
    grows while the fixes are still stale. Cached on the (store's) session dict."""
    if "_geo" not in S:
        G = S["G"]
        x, y = sg.local_xy(G["lat"], G["lon"], (G["lat"][0], G["lon"][0]))
        stp = np.hypot(np.diff(x), np.diff(y))
        stp[~(G["gps"][1:] & G["gps"][:-1])] = 0
        S["_geo"] = np.concatenate([[0.0], np.cumsum(stp)])
    return S["_geo"]


def _t_at_geo(S, t0, offs):
    """grid times at GPS path length geo(t0) + offs (nan past the end)"""
    g = _geo(S)
    T = np.arange(len(g), dtype=float)
    nz = np.concatenate([[True], np.diff(g) > 0])
    return np.interp(np.interp(t0, T, g) + np.asarray(offs, float), g[nz], T[nz], right=np.nan)


def _xy_at(x, y, t):
    t = np.atleast_1d(np.asarray(t, float))
    T = np.arange(len(x), dtype=float)
    ok = np.isfinite(t)
    X = np.full(len(t), np.nan); Y = np.full(len(t), np.nan)
    X[ok] = np.interp(t[ok], T, x); Y[ok] = np.interp(t[ok], T, y)
    return X, Y


def _departure(S, tc, r, R):
    """start at a stop: if a pause overlaps the stretch from tc until the session is more than 2R from the line point
    (r = distance to it per second), the clock starts when that pause ends (the session *leaves* the line)"""
    T = S["G"]["T"]
    i = int(min(T, max(0, math.floor(tc))))
    after = np.nonzero(r[i:] > 2 * R)[0]
    leave = i + int(after[0]) if len(after) else T
    t0 = float(tc)
    for a, b in S["P"]:
        if a < leave and b > tc:
            t0 = max(t0, float(b))
    return min(t0, float(T))


def _start_line(SA, point=None, R=START_RADIUS_M):
    """the anchor's start line: start moment tA (first moving second with GPS; with a point: the anchor's first second
    within POINT_NEAR_M of it, else its closest approach, then when it leaves), line point P, heading h (P -> the
    anchor's positions 10..50 m further), the anchor's path after tA every LINE_STEP_M (local metres around P, with
    the anchor's watch distance since tA per path point)"""
    G = SA["G"]
    gps = G.get("gps")
    if gps is None or not gps.any():
        raise ValueError(f"start anchor {SA['id']} has no GPS")
    if point is None:
        mov = ~SA["paused"] & gps
        tA = float(np.argmax(mov)) if mov.any() else float(np.argmax(gps))
        P = (float(G["lat"][int(tA)]), float(G["lon"][int(tA)]))
        x, y = sg.local_xy(G["lat"], G["lon"], P)
    else:
        P = (float(point[0]), float(point[1]))
        x, y = sg.local_xy(G["lat"], G["lon"], P)
        dd = np.hypot(x, y); dd[~gps] = np.inf
        near = dd < POINT_NEAR_M
        tA = float(np.argmax(near)) if near.any() else float(np.argmin(dd))
        tA = _departure(SA, tA, np.hypot(x, y), R)
    g = _geo(SA)
    offs = np.arange(0, g[-1] - np.interp(tA, np.arange(len(g)), g) + 1e-6, LINE_STEP_M)
    tt = _t_at_geo(SA, tA, offs)
    tt = tt[np.isfinite(tt)]
    if len(tt) < 6:
        raise ValueError(f"start anchor {SA['id']}: less than 50 m of GPS track after the start")
    PX, PY = _xy_at(x, y, tt)
    x0, y0 = _xy_at(x, y, [tA])
    h = np.array([np.mean(PX[1:6]) - x0[0], np.mean(PY[1:6]) - y0[0]])
    if not np.linalg.norm(h) > 0:
        raise ValueError(f"start anchor {SA['id']}: no heading at the start")
    h /= np.linalg.norm(h)
    # heading of the anchor's path at each path point (+-30 m), for the late joiners' direction check
    k = np.arange(len(PX)); a, b = np.maximum(k - 3, 0), np.minimum(k + 3, len(PX) - 1)
    PH = np.stack([PX[b] - PX[a], PY[b] - PY[a]], 1)
    PH /= np.maximum(np.linalg.norm(PH, axis=1, keepdims=True), 1e-9)
    dA = np.interp(tt, np.arange(G["T"] + 1), G["d"]) - np.interp(tA, np.arange(G["T"] + 1), G["d"])
    return {"id": SA["id"], "tA": tA, "tt": tt, "P": P, "h": h, "x0": (float(x0[0]), float(y0[0])), "PX": PX, "PY": PY, "PH": PH,
            "dA": dA, "n_tpl": int(LINE_TPL_M / LINE_STEP_M) + 1, "R": R, "point": point}


def _shape_err(S, x, y, tc, TX, TY):
    """mean distance (m) from each template point to the session's path over the next 2 x LINE_TPL_M of its GPS path
    (nearest points, order-free: milling about at a stop and distance noise don't matter); inf if the template is
    passed in the wrong order (reverse direction) or too short"""
    T = S["G"]["T"]
    t1 = _t_at_geo(S, tc, [2 * LINE_TPL_M])[0]
    t1 = T if not np.isfinite(t1) else t1
    a, b = math.floor(tc), min(T, math.ceil(t1)) + 1
    gx, gy = x[a:b], y[a:b]
    ok = np.isfinite(TX)
    if ok.sum() < 5 or len(gx) < 2:
        return np.inf
    D = np.hypot(TX[ok][:, None] - gx[None, :], TY[ok][:, None] - gy[None, :])
    j = D.argmin(1)
    if np.mean(np.diff(j) >= 0) < LINE_ORDER_MIN:
        return np.inf
    return float(D.min(1).mean())


def _find_start(S, A):
    """where session S starts on the anchor's line / path. Returns {status: aligned | late | excluded, kind, t0, ...}:
    - crossing: the first forward crossing of the line (|offset| <= R, moving within 60 degrees of its heading) after
      which the session follows the anchor's first 200 m; within the same pass (it stays within 2R of the line point)
      the last such crossing (jitter while waiting at the line); a stop there: the clock starts when it leaves.
    - join (late): no crossing, but where its own recording begins (its first JOIN_SCAN_M of GPS path) it is within R
      of the anchor's path, moving the same way, and follows the anchor's path from there (same template check):
      kept as *late*, joining at join_m along the anchor's path (<= AT_LINE_M: at_line, counts as aligned).
    - else excluded with a reason."""
    G = S["G"]
    gps = G.get("gps")
    R = A["R"]
    if gps is None or not gps.any():
        return {"status": "excluded", "reason": "no GPS"}
    x, y = sg.local_xy(G["lat"], G["lon"], A["P"])
    h = A["h"]
    u = x * h[0] + y * h[1]
    w = -x * h[1] + y * h[0]
    r = np.hypot(x, y)
    T = G["T"]
    n = A["n_tpl"]
    TX, TY = A["PX"][:n], A["PY"][:n]
    cand, rev = [], 0
    for i in np.nonzero((u[:-1] < 0) & (u[1:] >= 0) & gps[:-1] & gps[1:])[0]:
        f = -u[i] / (u[i + 1] - u[i])
        wc = w[i] + f * (w[i + 1] - w[i])
        if abs(wc) > R:
            continue
        a, b = max(0, i - 5), min(T, i + 6)
        dx, dy = x[b] - x[a], y[b] - y[a]
        if dx * h[0] + dy * h[1] < LINE_DIR_COS * math.hypot(dx, dy):
            continue
        cand.append((i + f, wc))
    for i in np.nonzero((u[:-1] >= 0) & (u[1:] < 0) & gps[:-1] & gps[1:])[0]:
        rev += int(abs(w[i]) <= R)
    best_e, crossed = None, None
    for k, (tc, wc) in enumerate(cand):
        # template shifted to where the session crossed the line (a crossing 30 m to the side isn't penalised)
        ox, oy = wc * -h[1], wc * h[0]
        e = _shape_err(S, x, y, tc, TX + ox, TY + oy)
        if e > LINE_SHAPE_TOL:
            best_e = e if best_e is None or e < best_e else best_e
            continue
        g = _geo(S)
        j = k
        for k2 in range(k + 1, len(cand)):   # same pass: later forward crossings while still near the line
            t2 = cand[k2][0]
            if (r[int(tc):int(t2) + 1].max() < 2 * R and g[int(t2)] - g[int(tc)] < 4 * R
                    and _shape_err(S, x, y, t2, TX + ox, TY + oy) <= LINE_SHAPE_TOL):
                j = k2
            else:
                break
        tc, wc = cand[j]
        t0 = _departure(S, tc, r, R)
        crossed = {"status": "aligned", "kind": "crossing", "t0": t0, "t_cross": tc, "zone_pause_s": t0 - tc,
                   "dist_to_line_m": abs(wc), "shape_err_m": e, "pass": j + 1}
        break
    joined = _join(S, A, x, y, n, before=crossed["t_cross"] if crossed else None)
    if crossed and joined and joined.get("status") == "late" and not joined["side_street"]:
        # a recording that begins on the anchor's path *and* crosses the line later (a loop started elsewhere on
        # it, or just before the line): whichever leaves the longer stretch to compare
        Td = np.arange(T + 1)
        rest = lambda t: G["d"][-1] - np.interp(t, Td, G["d"])
        lj = min(rest(joined["t0"]), A["dA"][-1] - joined["join_m"])
        return joined if lj > min(rest(crossed["t0"]), A["dA"][-1]) else crossed
    if crossed or (joined and joined.get("status")):
        return crossed or joined
    reversed_at_start = bool(joined is not None and joined.get("reversed_at_start"))
    dmin = float(np.min(r[gps]))
    if best_e is not None:
        why = (f"crosses the start line but then takes a different way (path differs by {best_e:.0f} m)"
               if np.isfinite(best_e) else "crosses the start line but then runs the anchor's path the other way")
    elif rev:
        why = f"crosses the start line only in the opposite direction ({rev}×)"
    elif reversed_at_start:
        why = "starts on the anchor's path, but running the other way"
    else:
        why = (f"never within {R:.0f} m of the start line (closest {dmin:.0f} m)" if dmin > R else
               f"doesn't cross the start line within {R:.0f} m of its point (closest {dmin:.0f} m)") + \
            " and doesn't start on the anchor's path"
    return {"status": "excluded", "reason": why, "closest_m": dmin}


def _join(S, A, x, y, n, before=None):
    """late joiner: its recording begins on the anchor's path (anywhere after the line), or it comes in from a side
    street onto the anchor's first SIDE_JOIN_M (only before `before`, a line crossing); moving the same way and
    following the anchor's path from there. Returns the start dict, {reversed_at_start: True} or None"""
    G = S["G"]
    T, R = G["T"], A["R"]
    gps = G["gps"]
    PX, PY, PH = A["PX"], A["PY"], A["PH"]
    mov = ~S["paused"] & gps
    reversed_at_start = False
    if mov.any():
        g = _geo(S)
        i0 = int(np.argmax(mov))
        ns = min(len(PX), int(SIDE_JOIN_M / LINE_STEP_M) + 1)
        early = g - g[i0] <= JOIN_SCAN_M
        Dn = np.hypot(PX[None, :ns] - x[:, None], PY[None, :ns] - y[:, None]).min(1)
        tried = 0
        for i in np.nonzero(mov & (np.arange(T + 1) >= i0) & (early | (Dn < R)))[0]:
            if before is not None and i >= before:
                break
            kmax = len(PX) if early[i] else ns
            dd = np.hypot(PX[:kmax] - x[i], PY[:kmax] - y[i])
            ks = np.nonzero(dd < R)[0]
            if not len(ks):
                continue
            tb = _t_at_geo(S, i, [30.0])[0]
            if not np.isfinite(tb):
                break
            vx, vy = _xy_at(x, y, [tb]); vx, vy = vx[0] - x[i], vy[0] - y[i]
            nv = math.hypot(vx, vy)
            if nv < 10:
                continue
            tried += 1
            if tried > 400:
                break
            # one candidate per pass along the anchor's path (laps / out & back): its point nearest to the session
            runs = np.split(ks, np.nonzero(np.diff(ks) > 1)[0] + 1)
            for k in (int(q[np.argmin(dd[q])]) for q in runs):
                if (vx * PH[k, 0] + vy * PH[k, 1]) < LINE_DIR_COS * nv:
                    reversed_at_start = reversed_at_start or bool(early[i])
                    continue
                e = _shape_err(S, x, y, float(i), PX[k:k + n], PY[k:k + n])
                if e <= LINE_SHAPE_TOL:
                    rj = np.hypot(x - PX[k], y - PY[k])
                    t0 = _departure(S, float(i), rj, R)
                    # dawdling at the join (walking about, GPS creeping) for over a minute before it leaves:
                    # the clock starts at its last second near the join point, re-placed on the anchor's path
                    a0 = int(t0)
                    away = np.nonzero(rj[a0:] > 2 * R)[0]
                    leave = a0 + int(away[0]) if len(away) else T
                    if leave - t0 > JOIN_DAWDLE_S:
                        near = np.nonzero(rj[a0:leave + 1] <= R / 2)[0]
                        if len(near):
                            t0 = float(a0 + near[-1])
                            kk = np.arange(k, min(len(PX), k + int(2 * R / LINE_STEP_M) + 1))
                            k = int(kk[np.argmin(np.hypot(PX[kk] - x[int(t0)], PY[kk] - y[int(t0)]))])
                    out = {"kind": "join", "t0": t0, "t_join": float(i), "zone_pause_s": t0 - i, "join_geo_m": k * LINE_STEP_M,
                           "dist_to_line_m": float(dd[k]), "shape_err_m": e, "join_m": float(A["dA"][k]),
                           "t_anchor": float(A["tt"][k]),
                           "side_street": not bool(early[i])}
                    if k * LINE_STEP_M <= AT_LINE_M:
                        return out | {"status": "aligned", "kind": "at_line"}
                    return out | {"status": "late"}
    return {"reversed_at_start": True} if reversed_at_start else None


def _window(S, t0, d_add=0.0, x_off=(0.0, 0.0)):
    """copy of session S cut at grid second floor(t0): every per-second array sliced, distance / climb / moving time
    from 0 there. d_add = distance already covered at the cut (late joiners: join_m along the anchor's path),
    x_off = (elapsed, moving) x offset in the time modes (late joiners: the pacer's time at join_m). The store's
    session stays untouched (shared across requests)."""
    G = S["G"]
    T = G["T"]
    a = int(min(T, max(0, math.floor(t0 + 1e-6))))
    n = T + 1
    G2 = {}
    for k, v in G.items():
        G2[k] = v[a:] if isinstance(v, np.ndarray) and len(v) == n else v
    G2["T"] = T - a
    G2["d"] = G["d"][a:] - G["d"][a]
    for k in ("up", "dn"):
        if k in G:
            G2[k] = G[k][a:] - G[k][a]
    P = [(max(0, p - a), q - a) for p, q in S["P"] if q > a]
    paused = S["paused"][a:].copy()
    S2 = {k: v for k, v in S.items() if k != "_geo"}
    S2.update(G=G2, P=P, paused=paused, mt=np.concatenate([[0], np.cumsum(~paused[:-1])]).astype(float),
              t_off=a, d_add=float(d_add), x_off_e=float(x_off[0]), x_off_m=float(x_off[1]))
    return S2


def _align_starts(st, sess, start, reference):
    """detect every session's start on the anchor's line, window the sessions. Returns (sessions, align, skipped, line)"""
    kind, anchor_id, point, R = start
    SA = next((S for S in sess if S["id"] == anchor_id), None)
    if SA is None:
        raise ValueError(f"start anchor {anchor_id} isn't one of the usable sessions")
    A = _start_line(SA, point, R)
    res = {}
    for S in sess:
        if S["id"] == anchor_id:
            res[S["id"]] = {"status": "anchor", "kind": "anchor", "t0": A["tA"], "dist_to_line_m": 0.0, "zone_pause_s": 0.0}
        else:
            res[S["id"]] = _find_start(S, A)
    # a late joiner that runs the anchor's route the other way overall (route projection) only shares a stretch with
    # it (out & back on the same streets, loops run backwards): excluded, its position on the anchor's path is no
    # race position
    rid = (SA["meta"] or {}).get("route")
    ctx = st.route(rid) if rid else None
    if ctx and not ctx["laps"]:
        dA = st.progress(anchor_id, ctx)[2]
        for S in sess:
            q = res[S["id"]]
            if q["status"] == "late" and (S["meta"] or {}).get("route") == rid and st.progress(S["id"], ctx)[2] != dA:
                res[S["id"]] = {"status": "excluded", "closest_m": q["dist_to_line_m"],
                                "reason": f"starts on the anchor's path {q['join_m']:.0f} m after the line, but runs route "
                                          f"{rid} the other way"}
    win = {S["id"]: _window(S, res[S["id"]]["t0"]) for S in sess if res[S["id"]]["status"] in ("anchor", "aligned")}
    # late joiners enter the time modes at the pacer's time at join_m: the reference when it starts at the line, else
    # the anchor; so their distance gap starts at 0 where they join
    pac = sess[0]["id"] if reference == "first" and sess[0]["id"] in win else anchor_id
    late = [S for S in sess if res[S["id"]]["status"] == "late"]
    for S in late:
        jm = res[S["id"]]["join_m"]
        xo = (0.0, 0.0)
        for pid in (pac, anchor_id):
            Q = win[pid]
            dq = Q["G"]["d"]
            if dq[-1] >= jm:
                te = float(np.argmax(dq >= jm))
                xo = (te, float(np.interp(te, np.arange(len(dq)), Q["mt"])))
                break
        res[S["id"]]["x_offset_s"] = xo
        win[S["id"]] = _window(S, res[S["id"]]["t0"], jm, xo)
        win[S["id"]]["join_t_anchor"] = res[S["id"]]["t_anchor"]
    out, skipped, sess_rows = [], [], {}
    for S in sess:
        q = res[S["id"]]
        row = {"status": q["status"], "kind": q.get("kind"), "reason": q.get("reason")}
        if q["status"] != "excluded":
            G = S["G"]
            t0 = q["t0"]
            row.update(offset_s=_r(t0, 1), moving_offset_s=_r(np.interp(t0, np.arange(G["T"] + 1), S["mt"]), 1),
                       offset_m=_r(np.interp(t0, np.arange(G["T"] + 1), G["d"]) - G["d"][0], 0),
                       dist_to_line_m=_r(q.get("dist_to_line_m"), 1), zone_pause_s=_r(q.get("zone_pause_s"), 0),
                       join_m=_r(q.get("join_m"), 0) if q["status"] == "late" else None,
                       x_offset_s=_r(q["x_offset_s"][0], 0) if q["status"] == "late" else None,
                       x_offset_moving_s=_r(q["x_offset_s"][1], 0) if q["status"] == "late" else None,
                       shape_err_m=_r(q.get("shape_err_m"), 1))
            W = win[S["id"]]
            if W["G"]["T"] < 10:
                q["status"] = "excluded"; row.update(status="excluded", reason="starts at the very end of the session")
            else:
                out.append(W)
        else:
            row["closest_m"] = _r(q.get("closest_m"), 0)
        if row["status"] == "excluded":
            skipped.append({"id": S["id"], "why": f"start: {row['reason']}"})
        sess_rows[str(S["id"])] = row
    hd = math.degrees(math.atan2(A["h"][0], A["h"][1])) % 360
    align = {"applied": True, "start": {"kind": kind, "anchor_id": anchor_id, "lat": _r(A["P"][0], 6), "lon": _r(A["P"][1], 6),
                                        "heading_deg": _r(hd, 0), "radius_m": R, "t_anchor_s": _r(A["tA"], 1), "route_m": None},
             "finish": None, "pacer_id": pac, "sessions": sess_rows,
             "excluded": [{"id": int(i), "reason": v["reason"], "closest_m": v.get("closest_m")} for i, v in sess_rows.items()
                          if v["status"] == "excluded"],
             "late": [int(i) for i, v in sess_rows.items() if v["status"] == "late"]}
    return out, align, skipped, A


def _align_rep1(st, sess, match, workers):
    """t = 0 at rep 1 (start="rep1"): every session is cut at the detected start of its rep matched to the anchor's
    (first session with reps) rep 1, with the intervals-mode matching (strides left out, extra reps before it cut off
    with the warm-up). A session whose first matched rep is the anchor's rep k > 1 (it skipped rep 1) is kept as
    late: it enters at the anchor's time / moving time / distance of rep k since its rep 1. Sessions without reps
    (or not interval sessions in groups.json), or none matched to an anchor rep, are excluded. Sub-second starts: x = 0 exactly at the rep start (x_off and
    d_add take out the fraction of the cut second). Returns (sessions, align, skipped)"""
    # interval sessions only: the run detector also finds "reps" in easy runs (the whole run as one effort), which the
    # grouping doesn't count as reps (groups.json reps empty); sessions not in groups.json keep the detector's reps
    nonint = {S["id"] for S in sess if S["meta"] and not S["meta"].get("reps")}
    cand = [S for S in sess if S["id"] not in nonint]
    if not cand:
        raise ValueError("no interval session (no reps): t = 0 at rep 1 needs reps")
    reps_by, _info, labels, anchored, slot_of = _reps_slots(st, cand, match, workers)
    aid = next(iter(reps_by))
    SA = next(S for S in sess if S["id"] == aid)
    ra, sa = reps_by[aid], slot_of[aid]
    a_start = {sa[j]: ra[j]["start"] for j in range(len(ra))}
    k1 = sa[0]

    def at(S, t):   # (elapsed, moving, distance) at file second t
        ar = np.arange(S["G"]["T"] + 1)
        return float(t), float(np.interp(t, ar, S["mt"])), float(np.interp(t, ar, S["G"]["d"]))
    tA = at(SA, ra[0]["start"])
    out, rows, skipped = [], {}, []
    for S in sess:
        i = S["id"]
        rr = reps_by.get(i)
        js = [j for j in range(len(rr)) if anchored[slot_of[i][j]]] if rr else []
        if not js:
            why = ("not an interval session (no reps)" if i in nonint else "no reps detected" if not rr else
                   "none of its reps matches a rep of the anchor") + " (t = 0 at rep 1 needs a rep 1)"
            rows[str(i)] = {"status": "excluded", "kind": "rep1", "reason": why}
            skipped.append({"id": i, "why": f"start: {why}"})
            continue
        j = js[0]; k = slot_of[i][j]
        t0 = rr[j]["start"]
        a = math.floor(t0 + 1e-6)
        _e0, m0, d0 = at(S, t0)
        fe, fm, fd = t0 - a, m0 - float(S["mt"][a]), d0 - float(S["G"]["d"][a])
        late = k != k1
        xo = (0.0, 0.0, 0.0)
        if late:   # the anchor's time, moving time and distance from its rep 1 to its rep in this slot
            ek, mk, dk = at(SA, a_start[k])
            xo = (ek - tA[0], mk - tA[1], dk - tA[2])
        W = _window(S, t0, xo[2] - fd, (xo[0] - fe, xo[1] - fm))
        if W["G"]["T"] < 10:
            rows[str(i)] = {"status": "excluded", "kind": "rep1", "reason": "rep 1 starts at the very end of the session"}
            skipped.append({"id": i, "why": "start: rep 1 starts at the very end of the session"})
            continue
        out.append(W)
        rows[str(i)] = {"status": "anchor" if i == aid else "late" if late else "aligned", "kind": "rep1",
                        "reason": (f"no rep matching the anchor's rep 1: its first rep matches rep {labels[k]}, entered at "
                                   f"the anchor's time of that rep") if late else None,
                        "offset_s": _r(t0, 1), "moving_offset_s": _r(m0, 1), "offset_m": _r(d0 - S["G"]["d"][0], 0),
                        "rep_n": j + 1, "slot": labels[k], "join_m": _r(xo[2], 0) if late else None,
                        "x_offset_s": _r(xo[0], 0) if late else None, "x_offset_moving_s": _r(xo[1], 0) if late else None,
                        "dist_to_line_m": None, "zone_pause_s": None}
    align = {"applied": True, "start": {"kind": "rep1", "anchor_id": aid, "lat": None, "lon": None, "heading_deg": None,
                                        "radius_m": None, "t_anchor_s": _r(tA[0], 1), "route_m": None},
             "finish": None, "pacer_id": aid, "sessions": rows,
             "excluded": [{"id": int(i), "reason": v["reason"], "closest_m": None} for i, v in rows.items()
                          if v["status"] == "excluded"],
             "late": [int(i) for i, v in rows.items() if v["status"] == "late"]}
    return out, align, skipped


# ---------------------------------------------------------------- public API
def compare(ids, mode="distance", *, person="adrian", reference="first", max_points=MAX_POINTS,
            smooth_s=None, axis="auto", rep_axis="absolute", gap_s=GAP_S, rep_gap_s=REP_GAP_S,
            step_m=None, match="auto", workers=8, start=None, start_radius_m=START_RADIUS_M,
            rep_series=False, rep_lead_s=REP_LEAD_S, rep_post_s=REP_POST_S) -> dict:
    """Compare sessions. ids[0] is the reference (reference="first") unless reference="median".

    mode: elapsed | moving | distance | route_position | intervals | breaks
    axis (distance mode): auto (route reference when all sessions share a compared route, else own
        distance) | route | own
    rep_axis (intervals): absolute (seconds from rep start) | normalized (0..100 % of the rep)
    match (intervals/breaks): auto (nearest distance/duration, order kept; index pairing when less than half
        of the reps match) | index
    smooth_s: speed smoothing for time modes (default 5 s runs, 15 s ski)
    start (elapsed / moving / distance): None (each session from its own start) | "ref" (common start line at the
        first id's start) | <id> (at that session's start) | (lat, lon) or "lat,lon" (where the first id passes that
        point); start_radius_m = the line's half-width. Sessions are cut at the line (late joiners kept, sessions
        that never get onto the anchor's path excluded); see docs/compare.md#common-start.
        "rep1": t = 0 at rep 1 instead (the detected start of each session's rep matched to the anchor's rep 1, real
        breaks kept, the warm-up cut off; distance mode on the own distance from there); see docs/compare.md#rep-1
    rep_series (intervals): also return `rep_series`, every rep on its own real-time axis from rep_lead_s before
        its start to rep_post_s after its end (Rep N / Per rep views)
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
    rep1 = str(start).strip().lower() == "rep1" if start is not None else False
    sp = None if rep1 else _parse_start(start, ids)
    align, A, whole = None, None, {}
    if rep1 and mode in ("elapsed", "moving", "distance", "route_position"):
        whole = {S["id"]: _summary(S) for S in sess}
        sess, align, sk = _align_rep1(st, sess, match, workers)
        skipped += sk
        if not sess:
            raise ValueError(f"no session has a rep 1: {skipped}")
        axis = "own"   # reps start anywhere along a route: x = own distance since rep 1
    elif rep1:
        align = {"applied": False, "note": "intervals / breaks are aligned by rep already: t = 0 at rep 1 doesn't apply"}
    elif sp and mode in ("elapsed", "moving", "distance", "route_position"):
        if not 5 <= float(start_radius_m) <= 500:
            raise ValueError("start_radius_m must be 5..500")
        if sp[1] == ids[0] and sess[0]["id"] != ids[0]:
            sp = (sp[0], sess[0]["id"], sp[2])
        whole = {S["id"]: _summary(S) for S in sess}
        sess, align, sk, A = _align_starts(st, sess, (*sp, float(start_radius_m)), reference)
        skipped += sk
        if not sess:
            raise ValueError(f"no session starts at the start line: {skipped}")
    elif sp:
        align = {"applied": False, "note": "intervals / breaks are aligned by rep: the start line doesn't apply"}
    if sess[0]["id"] != ids[0]:
        skipped.append({"id": ids[0], "why": "reference unusable, next session is the reference"})
    summ = [_summary(S) for S in sess]
    for row in summ:
        w = whole.get(row["id"])
        if w:   # whole-session totals (the table shows the aligned window)
            row["whole"] = {k: w[k] for k in ("elapsed_s", "moving_s", "pause_s", "dist_m", "moving_pace_s_km",
                                               "gap_pace_s_km", "hr_avg", "climb_m")}
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
        dm = _distance_mode(st, sess, reference, max_points, axis, step_m, anchor=A)
        if align and dm["axis"].get("origin_m") is not None:
            align["start"]["route_m"] = dm["axis"]["origin_m"]
        cover = dm.pop("_cover")
        for row in summ:
            row.update(cover.get(row["id"]) or {})
        out.update(dm)
    else:
        out.update(_interval_modes(st, sess, mode, reference, max_points, rep_axis, gap_s, rep_gap_s, workers, match,
                                   rep_series, rep_lead_s, rep_post_s))
    if rep1 and align["applied"]:
        out["axis"]["t0"] = "rep1"
        out["axis"]["note"] = ("x = 0 at the start of rep 1 (the rep matched to the anchor's rep 1; warm-up cut off, "
                               "real breaks kept); " + out["axis"]["note"])
    if align is not None:
        out["align"] = align
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
        # common start: windowed sessions count from the line (late joiners from the pacer's time at their join);
        # t stays the file's elapsed time (the map places sessions by it)
        x = (np.arange(T + 1, dtype=float) + S.get("x_off_e", 0.0)) if mode == "elapsed" else S["mt"] + S.get("x_off_m", 0.0)
        to = S.get("t_off", 0)
        c = {"x": x[keep], "t": np.arange(T + 1, dtype=float)[keep] + to, "speed_kmh": v[keep] * 3.6,
             "hr": G["hr"][keep] if "hr" in G else np.full(keep.sum(), np.nan),
             "alt": G["alt"][keep] if "alt" in G else np.full(keep.sum(), np.nan),
             "dist_m": G["d"][keep] + S.get("d_add", 0.0) if not S["no_dist"] else np.full(keep.sum(), np.nan)}
        cols[S["id"]] = c
        pauses[S["id"]] = [{"x": _r(x[a], 0), "t": a + to, "dur_s": b - a} for a, b in S["P"]]
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


def _distance_mode(st, sess, reference, max_points, axis, step_m, anchor=None):
    """x = position along the route reference (projection) or own distance; per x: first time there.
    Common start (sessions windowed by _align_starts, `anchor` = the start line): on the route axis x = route metres
    minus the line's route position (origin, from the anchor's projection), so the line is x = 0; own axis: the
    windowed distance (+ join_m for late joiners)."""
    ctx = _common_route(st, sess, axis)
    prog, unc = {}, {}
    origin, tpA = 0.0, None
    if ctx and anchor is not None:
        # the line's position on the route: the anchor's progress at its first on-route fix from its start on
        tpA, onA, _ = st.progress(anchor["id"], ctx)
        a = int(anchor["tA"])
        k = np.nonzero(onA[a:])[0]
        tpA = np.maximum.accumulate(np.maximum(tpA, tpA[a + k[0]] if len(k) else 0.0))
        origin = float(min(tpA[a + k[0]], ctx["L"] - 10 * sg.REF_STEP)) if len(k) else 0.0
        origin = max(0.0, origin)
    for S in sess:
        G = S["G"]
        if ctx:
            tp, on, direc = st.progress(S["id"], ctx)
            a = S.get("t_off", 0)
            if a or len(tp) != G["T"] + 1:   # windowed copy (common start): the cached projection of the whole file
                tp, on = tp[a:a + G["T"] + 1], on[a:a + G["T"] + 1]
            onix = np.nonzero(on)[0]
            j0 = float(tp[onix[0]]) if len(onix) else origin
            tp = np.maximum.accumulate(np.maximum(tp, j0))
            # where the session joins the route the projection waits until it has caught up: start 20 m later.
            # Sessions cut at the start line are at the line (+- the reference's 20 m step), so they start at x = 0
            thr = origin + 2 * sg.REF_STEP if anchor is not None and S.get("d_add", 0) == 0 else origin
            x0 = j0 + 20.0 if j0 > thr else origin
            if S.get("join_t_anchor") is not None and tpA is not None:
                # late joiner: not before the anchor's route position where it joined (the projection of a late
                # start can lag behind it)
                x0 = max(x0, float(np.interp(S["join_t_anchor"], np.arange(len(tpA)), tpA)))
            # time counts from the first on-route fix: a lead-in before the session gets on the route (even at
            # its start, j0 = 0) is not dumped into the first bin
            i0 = int(onix[0]) if len(onix) else 0
            prog[S["id"]] = (tp, x0, float(tp[-1]), direc, i0)
            unc[S["id"]] = _jumps(S, tp, onix)
        else:
            d = G["d"] - G["d"][0] + S.get("d_add", 0.0)
            prog[S["id"]] = (d, S.get("d_add", 0.0), float(d[-1]), "own", 0)
            unc[S["id"]] = []
    Lmax = ctx["L"] if ctx else max(p[2] for p in prog.values())
    step = step_m or max(10.0, math.ceil((Lmax - origin) / max_points / 10) * 10)
    X = np.arange(origin, Lmax + step / 2, step)
    h = max(SPEED_H_M, step)
    per = {}
    for S in sess:
        G = S["G"]
        p, x0, x1, direc, i0 = prog[S["id"]]
        T = G["T"]
        # strictly increasing samples from the start index on: first second at each new progress value
        p_, mt_ = p[i0:], S["mt"][i0:]
        nz = np.concatenate([[True], np.diff(p_) > 0])
        pi, ti, mi = p_[nz], np.arange(i0, T + 1, dtype=float)[nz], mt_[nz]
        ok = (X >= x0) & (X <= x1)
        te = np.where(ok, np.interp(X, pi, ti), np.nan)                 # elapsed time at x
        okk = np.nonzero(ok)[0]
        if len(okk) > 3:
            # the clock starts when the session leaves its start: first bins that take far longer than its usual
            # bin (milling about / standing / creeping along at the start, within START_TRIM_M) are cut off
            ie = np.diff(te[okk])
            lim = max(START_STALL_S, START_STALL_K * float(np.median(ie)))
            n = 0
            while n < len(ie) - 2 and ie[n] > lim and X[okk[n + 1]] - x0 <= START_TRIM_M:
                n += 1
            if n:
                x0 = float(X[okk[n]]); ok &= X >= x0
                te[~ok] = np.nan
        tm = np.where(ok, np.interp(X, pi, mi), np.nan)                 # moving time at x
        lo, hi = np.maximum(X - h, x0), np.minimum(X + h, x1)
        tmh = np.interp(hi, pi, mi) - np.interp(lo, pi, mi)
        if ctx:   # stalls: a bin passed while the session moved much further (detour, wrong direction, milling)
            dg = np.diff(np.interp(np.nan_to_num(te), np.arange(T + 1), G["d"]), prepend=np.nan)
            stall = ok & np.concatenate([[False], ok[:-1]]) & (dg > JUMP_MIN_M + JUMP_D_K * step)
            add = [(float(X[k] - step), float(X[k])) for k in np.nonzero(stall)[0]]
            # too fast: JUMP_MIN_M passed quicker than the plausible top speed in elapsed or moving time (small
            # projection jumps; GPS drift along the route during a pause)
            w = max(1, round(JUMP_MIN_M / step))
            if len(X) > w:
                with np.errstate(invalid="ignore"):
                    tmin = w * step / _vcap(S)
                    fast = np.nonzero(((te[w:] - te[:-w]) < tmin) | ((tm[w:] - tm[:-w]) < tmin))[0]
                add += [(float(X[k]), float(X[k + w])) for k in fast]
            unc[S["id"]] = _merge(unc[S["id"]] + add)
        # bins inside a projection jump (shortcut / re-acquired further on / off the route) or a stall: not covered
        ub = np.zeros(len(X), bool); uw = np.zeros(len(X), bool)
        for a, b in unc[S["id"]]:
            ub |= (X - step < b) & (X > a) & (X > X[0])   # every bin overlapping the stretch (>= 1 bin)
            uw |= (hi > a) & (lo < b)       # speed window touches the jump
        with np.errstate(invalid="ignore", divide="ignore"):
            v = np.where(ok & (tmh > 0) & ~uw, (hi - lo) / tmh, np.nan)
        hr = np.interp(np.nan_to_num(te), np.arange(T + 1), G["hr"]) if "hr" in G else np.full(len(X), np.nan)
        hr[~ok | ub] = np.nan
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
        per[S["id"]] = {"te": te, "tm": tm, "v": v, "hr": hr, "alt": alt, "gapv": gapv, "x0": x0, "x1": x1, "dir": direc,
                        "unc": ub, "stretches": unc[S["id"]]}

    # reference curve: first session, or median of per-bin time increments. Uncovered bins add nothing
    def incr(q, k):
        a = np.diff(q[k], prepend=np.nan)
        a[q["unc"]] = np.nan
        return a
    if reference == "first":
        R = per[sess[0]["id"]]
        r_dm, r_de = incr(R, "tm"), incr(R, "te")
    else:
        DM = np.array([incr(q, "tm") for q in per.values()]); DE = np.array([incr(q, "te") for q in per.values()])
        okn = np.sum(~np.isnan(DM), 0) >= max(2, len(per) / 2)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            r_dm = np.where(okn, np.nanmedian(DM, 0), np.nan); r_de = np.where(okn, np.nanmedian(DE, 0), np.nan)
            med_hr = np.where(okn, np.nanmedian(np.array([q["hr"] for q in per.values()]), 0), np.nan)
    series, cover = {}, {}
    for S in sess:
        q = per[S["id"]]
        dm_, de_ = incr(q, "tm") - r_dm, incr(q, "te") - r_de
        # the gap only changes on bins both sessions covered; it's held across jumps / bins one of them lacks
        gm = np.cumsum(np.nan_to_num(dm_)); ge = np.cumsum(np.nan_to_num(de_))
        both = ~np.isnan(dm_)
        # gap defined from the first common bin on (both sessions present)
        first = int(np.argmax(both)) if both.any() else len(X)
        last = len(both) - int(np.argmax(both[::-1])) if both.any() else 0
        valid = np.zeros(len(X), bool); valid[max(0, first - 1):last] = True
        if first > 0:
            gm = gm - gm[first - 1]; ge = ge - ge[first - 1]
        gm[~valid] = np.nan; ge[~valid] = np.nan
        q["gap_m"], q["gap_e"] = gm.copy(), ge.copy()      # legs: held values across uncovered bins
        gm[q["unc"]] = np.nan; ge[q["unc"]] = np.nan          # charts: a break where the session wasn't on the route
        span = max(0.0, min(q["x1"], Lmax) - q["x0"])
        unc_m = float(sum(min(b, q["x1"]) - max(a, q["x0"]) for a, b in q["stretches"] if min(b, q["x1"]) > max(a, q["x0"])))
        cover[S["id"]] = {
            "coverage_pct": _r(100 * max(0.0, span - unc_m) / (Lmax - origin), 1) if ctx and Lmax > origin else None,
            "uncovered_m": _r(unc_m, 0),
            "uncovered": [[_r(max(a, origin) - origin, 0), _r(b - origin, 0)] for a, b in q["stretches"] if b > origin],
            "reversed": q["dir"] == "rev",
            # gap at the last position both this session and the reference covered
            "gap_end": {"x_m": _r(X[last - 1] - origin, 0), "delta_moving_s": _r(q["gap_m"][last - 1], 1),
                        "delta_elapsed_s": _r(q["gap_e"][last - 1], 1)} if both.any() else None}
        ok = ~np.isnan(q["tm"])
        sel = slice(int(np.argmax(ok)), len(ok) - int(np.argmax(ok[::-1]))) if ok.any() else slice(0, 0)
        series[str(S["id"])] = {
            "x": _lst(X[sel] - origin, 0), "moving_s": _lst(q["tm"][sel], 0), "elapsed_s": _lst(q["te"][sel], 0),
            # file elapsed time at x (the map places sessions by it; differs from elapsed_s with a common start)
            "t": _lst(q["te"][sel] + S.get("t_off", 0), 0),
            "speed_kmh": _lst(q["v"][sel] * 3.6, 2), "pace_s_km": [_r(_pace(v)) for v in q["v"][sel]],
            "gap_kmh": _lst(q["gapv"][sel] * 3.6, 2) if q["gapv"] is not None else None,
            "hr": _lst(q["hr"][sel], 0), "alt": _lst(q["alt"][sel], 1),
            "delta_moving_s": _lst(gm[sel], 1), "delta_elapsed_s": _lst(ge[sel], 1), "dir": q["dir"],
            "uncovered": cover[S["id"]]["uncovered"]}
    # legs: route waypoints, Bislet-style 400 m laps for laps routes, else 1 km
    if ctx:
        bnd, names = list(ctx["bnd"]), list(ctx["names"])
        if origin > 0:   # common start: legs from the start line (the first one partial)
            keep = [k for k, b in enumerate(bnd) if b > origin + LEG_TOL_M / 2]
            bnd, names = [origin] + [bnd[k] for k in keep], ["start line"] + [names[k] for k in keep]
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
            ul = sum(max(0.0, min(hb, xb) - max(ha, xa)) for ha, hb in q["stretches"])
            if ul > LEG_UNCOVERED * (b - a):      # jumped over a large part of the leg: its time means nothing
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
            if xb - xa - ul < 0.98 * (b - a):
                rows[str(S["id"])]["partial"] = _r((xb - xa - ul) / (b - a), 2)
        vals = [r["moving_s"] for r in rows.values() if r]
        refm = (rows[str(sess[0]["id"])] or {}).get("moving_s") if reference == "first" else (
            float(np.median(vals)) if vals else None)
        for r in rows.values():
            if r:
                r["d_moving_s"] = _r(r["moving_s"] - refm, 0) if refm is not None else None
        rk = sorted((r["moving_s"], i) for i, r in rows.items() if r)
        for n, (_, i) in enumerate(rk, 1):
            rows[i]["rank"] = n
        legs.append({"from_m": _r(a - origin, 0), "to_m": _r(b - origin, 0), "name": f"{names[k]} → {names[k + 1]}",
                     "climb_m": _r(_ref_climb(ctx, a, b), 0) if ctx else None, "sessions": rows})
    out = {"axis": {"x": "route_m" if ctx else "dist_m", "unit": "m", "step_m": step,
                    "route": ctx["id"] if ctx else None, "route_name": ctx["R"]["name"] if ctx else None,
                    "origin_m": _r(origin, 0) if ctx and anchor is not None else None,
                    "note": "delta_*_s = own time to reach x - reference time (positive = behind), counted from the "
                            "first position both sessions cover"},
           "legs_kind": legs_kind, "legs": legs, "series": series, "_cover": cover}
    if ctx:
        r = ctx["ref"]; stp = max(1, math.ceil(len(r["m"]) / 300))
        out["route_ref"] = {"id": ctx["R"]["medoid"], "m": _lst(r["m"][::stp] - origin, 0), "alt": _lst(r["alt"][::stp], 1),
                            "lat": _lst(r["lat"][::stp], 5), "lon": _lst(r["lon"][::stp], 5),
                            "waypoints": ctx["R"]["waypoints"]}
    if reference == "median":
        tmed = np.cumsum(np.nan_to_num(r_dm)); tmed[np.isnan(r_dm) & (np.arange(len(X)) > 0)] = np.nan
        with np.errstate(invalid="ignore", divide="ignore"):
            vmed = np.where(np.isfinite(r_dm) & (r_dm > 0), step / r_dm, np.nan)
        out["median"] = {"x": _lst(X - origin, 0), "moving_s": _lst(tmed, 0), "speed_kmh": _lst(_smooth(vmed, max(1, round(2 * h / step))) * 3.6, 2),
                         "hr": _lst(med_hr, 0)}
    return out


def _jumps(S, tp, onix):
    """route stretches [(from_m, to_m)] not covered, between two consecutive on-route fixes:
    - jump: progress gained > JUMP_MIN_M and more than JUMP_MIN_M + JUMP_D_K x own distance moved meanwhile
      (shortcut, re-acquired further on along a loop / out & back), or > 40 m and faster than JUMP_V
      (max(2.5 x own moving speed, 25 / 60 km/h), + one 20 m reference step);
    - off route: > OFF_ROUTE_S without an on-route fix while progress went on by > JUMP_MIN_M and the own distance
      differs from it by more than 10 % + 50 m (another way, or no GPS: the time there isn't comparable; a parallel
      path of the same length, or GPS offset from the route, stays covered).
    Stalls (one bin passed while the own distance grew by more than JUMP_MIN_M + JUMP_D_K x step: a detour that
    returns, running the other way, milling about) are added per bin in _distance_mode."""
    if len(onix) < 2:
        return []
    G = S["G"]
    a, b = onix[:-1], onix[1:]
    dtp = tp[b] - tp[a]
    dd = G["d"][b] - G["d"][a]
    vcap = _vcap(S)
    q = sg.REF_STEP   # progress is quantised to the reference's 20 m steps
    jump = ((dtp > JUMP_MIN_M) & (dtp > JUMP_MIN_M + JUMP_D_K * dd)) | ((dtp > 2 * q) & (dtp > vcap * (b - a) + q))
    off = (b - a > OFF_ROUTE_S) & (dtp > JUMP_MIN_M) & (np.abs(dd - dtp) > OFF_SAME_K * dtp + OFF_SAME_M)
    return _merge([(float(tp[a[k]]), float(tp[b[k]])) for k in np.nonzero(jump | off)[0]])


def _vcap(S):
    """fastest plausible speed (m/s): max(2.5 x own moving speed, 25 km/h run / 60 km/h ski)"""
    mov = max(1, int((~S["paused"]).sum()))
    return max(JUMP_V[0] * S["G"]["d"][-1] / mov, (JUMP_V[2] if S["sport"] == "ski" else JUMP_V[1]) / 3.6)


def _merge(st):
    """sorted, merged [(from_m, to_m)] stretches"""
    out = []
    for lo, hi in sorted(st):
        if out and lo <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return [tuple(x) for x in out]


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


def _reps_slots(st, sess, match, workers):
    """main reps per session (strides left out) and their slots (matched against the first session with reps):
    (reps_by {id: [rep]}, info {id: {...}}, labels, anchored, slot_of {id: [slot index per rep]})"""
    R = st.reps([S["id"] for S in sess], workers)
    reps_by, info = {}, {}
    for S in sess:
        r = R.get(S["id"])
        md = str((r or {}).get("mode") or "")
        rr = _main_reps((r or {}).get("reps") or [], md.startswith("ski") or md == "indoor")   # indoor: laps as recorded
        if len(rr) >= 1:
            reps_by[S["id"]] = rr
            info[S["id"]] = {"det_mode": r["mode"], "n_detected": len(r["reps"]), "n_used": len(rr)}
    if not reps_by:
        raise ValueError("no reps found in any session")
    ski = all(str(info[sid]["det_mode"]).startswith("ski") for sid in reps_by)
    labels, anchored, slot_of, method = _slots(reps_by, match, ski)
    for sid in reps_by:
        info[sid]["match"] = method[sid]
    return reps_by, info, labels, anchored, slot_of


def _interval_modes(st, sess, mode, reference, max_points, rep_axis, gap_s, rep_gap_s, workers, match,
                    rep_series=False, rep_lead_s=REP_LEAD_S, rep_post_s=REP_POST_S):
    reps_by, info, labels, anchored, slot_of = _reps_slots(st, sess, match, workers)
    SS = {S["id"]: S for S in sess if S["id"] in reps_by}
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
        if rep_series:
            out["rep_series"] = _rep_windows(SS, reps_by, slot_of, rows, ns, rep_lead_s, rep_post_s)
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


def _pack(c, max_points, keep_reps=False):
    """round + thin to max_points. keep_reps (intervals): rep points (phase 1) are never thinned; the other points
    (built at ~1 per x unit) only when there are more than max_points of them"""
    c = {k: np.asarray(v, float) for k, v in c.items()}
    if keep_reps:
        rep = c["phase"] == 1
        other = np.nonzero(~rep)[0]
        k = max(1, math.ceil(len(other) / max_points))
        keep = rep.copy()
        keep[other[::k]] = True
        keep[other[np.isnan(c["speed_kmh"][other]) & np.isnan(c["hr"][other])]] = True   # line breaks stay
        c = {key: v[keep] for key, v in c.items()}
    else:
        c = _stride(c, max_points)
    return {"x": _lst(c["x"], 1), "t": _lst(c["t"], 1), "speed_kmh": _lst(c["speed_kmh"], 2), "hr": _lst(c["hr"], 0),
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
        gaps = []    # [(index in t, x)] line breaks: across the rep area of a slot this session has no rep in
        a0 = rr[0]["start"]
        t = np.arange(max(0.0, a0 - PRE_S), a0, 1.0)
        add(t, X0[sl[0]] - (a0 - t) * (gap_s / PRE_S if norm else 1.0), 0, -1)
        for j, r in enumerate(rr):
            k = sl[j]
            t = np.append(np.arange(r["start"], r["end"], 1.0), r["end"])      # reps at 1 s, never thinned
            add(t, X0[k] + (t - r["start"]) * (100 / r["time"] if norm else 1.0), 1, k)
            if j + 1 < len(rr):
                b0, b1 = r["end"], rr[j + 1]["start"]
                k1 = sl[j + 1]
                # the break goes into the collapsed gaps only (one per slot passed: k .. k1-1), never through the
                # rep area of a slot in between; about 1 point per x unit (not per real second: a long break,
                # or an unfixed clock jump, can't flood the series)
                for m in range(k, k1):
                    f0 = (m - k) / (k1 - k); f1 = (m - k + 1) / (k1 - k)
                    g0, g1 = X0[m] + W[m], X0[m + 1]
                    n = int(min(max(2, math.ceil(g1 - g0)), max(2, b1 - b0)))
                    tb = b0 + (b1 - b0) * np.linspace(f0, f1, n + 1)[1:-1] if n > 1 else np.array([])
                    if m > k:
                        gaps.append(sum(len(q) for q in ts))
                        add(np.array([tb[0] if len(tb) else b0]), np.array([g0 - 1e-3]), 2, -1)
                    add(tb, g0 + (g1 - g0) * ((tb - b0) / max(b1 - b0, 1e-6) - f0) / (f1 - f0), 2, -1)
        t = np.concatenate(ts)
        sp = _rep_speed(G, t) * 3.6 if not S["no_dist"] else np.full(len(t), np.nan)
        hr = np.interp(t, np.arange(G["T"] + 1), G["hr"]) if "hr" in G else np.full(len(t), np.nan)
        sp[gaps] = np.nan; hr[gaps] = np.nan
        series[str(sid)] = _pack({"x": np.concatenate(xs), "t": t, "speed_kmh": sp, "hr": hr,
                                  "phase": np.concatenate(ph), "slot": np.concatenate(sl_)}, max_points, keep_reps=True)
    return {"axis": {"x": "rep_aligned_pct" if norm else "rep_aligned_s", "rep_axis": rep_axis, "gap_s": gap_s,
                     "slot_x0": _lst(X0, 1), "slot_width": _lst(W, 1), "phases": PHASES,
                     "note": "x of a rep point = slot_x0 + seconds (or % of the rep) since the rep start; break "
                             "points are squeezed linearly into [slot end, next slot start] (only the gaps, a slot "
                             "without a rep of this session is left empty: a null point breaks the line); reps "
                             "are at 1 s and never thinned"},
            "series": series}


def _rep_windows(SS, reps_by, slot_of, rows, ns, lead_s, post_s):
    """per-rep series (Rep N / Per rep views): for every slot and session the rep on its own real-time axis,
    x = seconds since the rep's (detected, sub-second) start on a shared 1 s grid x = -lead_s .. width + post_s
    (width = the slot's longest rep, rounded up). Values: speed (3 s difference of the distance, as in the timeline)
    and HR; null before the session's start, after post_s past its own rep end and from its next rep start on (the
    lead-in may reach back into the previous rep when the break is shorter than lead_s)."""
    W = [int(max((math.ceil(r["time_s"]) for r in (rows[s][k] for s in rows) if r), default=0)) for k in range(ns)]
    slots = []
    for k in range(ns):
        x = np.arange(-lead_s, W[k] + post_s + 1, 1.0)
        ser = {}
        for sid, rr in reps_by.items():
            js = [j for j, q in enumerate(slot_of[sid]) if q == k]
            if not js:
                continue
            j = js[0]
            r, S = rr[j], SS[sid]
            G = S["G"]
            t = r["start"] + x
            nxt = rr[j + 1]["start"] if j + 1 < len(rr) else math.inf
            ok = (t >= 0) & (t <= G["T"]) & (t <= r["end"] + post_s) & (t < nxt)
            tc = np.clip(t, 0, G["T"])
            sp = _rep_speed(G, tc) * 3.6 if not S["no_dist"] else np.full(len(t), np.nan)
            hr = np.interp(tc, np.arange(G["T"] + 1), G["hr"]) if "hr" in G else np.full(len(t), np.nan)
            sp[~ok] = np.nan; hr[~ok] = np.nan
            ser[str(sid)] = {"speed_kmh": _lst(sp, 2), "hr": _lst(hr, 0), "start_s": _r(r["start"], 2),
                             "time_s": _r(r["time"], 1)}
        slots.append({"slot": k, "width": W[k], "x0": -lead_s, "n": len(x), "series": ser})
    return {"lead_s": lead_s, "post_s": post_s, "slots": slots,
            "note": "x = x0 + i (s since the rep start, i = array index); the rep runs from 0 to time_s; values "
                    "beyond post_s after the rep end or into the session's next rep are null"}


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
            t = np.arange(math.floor(b0) + 1, b1, max(1.0, (b1 - b0) / 3000))   # guard: hours-long breaks stay small
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
    al = r.get("align")
    if al and al.get("applied"):
        s0 = al["start"]
        print(f"\ncommon start at {s0['anchor_id']}'s {'start' if s0['kind'] != 'point' else 'pass of the point'} "
              f"({s0['lat']}, {s0['lon']}, heading {s0['heading_deg']}°, ±{s0['radius_m']:.0f} m"
              + (f", route {s0['route_m']} m" if s0.get("route_m") is not None else "") + ")")
        for i, q in al["sessions"].items():
            if q["status"] == "excluded":
                print(f"{i:>12} excluded: {q['reason']}")
            else:
                print(f"{i:>12} {q['status']:8} {q['kind']:8} cut {_mm(q['offset_s'])} / {q['offset_m']} m"
                      + (f", late: joins {q['join_m']} m after the line" if q["status"] == "late" else "")
                      + (f", left the line after a {q['zone_pause_s']} s stop" if q.get("zone_pause_s") else ""))
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
    """sanity checks on real data (printed): see docs/compare.md#validation. The R3 / Bislet / ski sections use
    adrian's sessions; the correctness checks (clock jumps, ghost-race jumps, route lead-in, intervals timeline) run
    for any person."""
    validate_fixes(person)
    validate_start(person)
    validate_reps(person)
    if person != "adrian":
        return
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


def validate_start(person):
    """common start line checks (docs/compare.md#validation): every route with >= 3 sessions for any person, then
    adrian's named cases (R11 lead-in, R3 anchor at a stop, R4 late reference, R4 km 3 turn-off, R1 laps, R13
    reversed, R11 parallel street)"""
    st_ = store(person)
    g = st_.groups()
    ok = lambda c: "OK " if c else "BAD"
    nn = lambda a: np.array([np.nan if v is None else v for v in a], float)
    cnt = {"aligned": 0, "late": 0, "excluded": 0, "anchor": 0}
    proj_d, proj_first, bad_first, bad_ahead, bad_start, n_routes, t_max = [], [], [], [], [], 0, 0.0
    for rid, R in g["routes"].items():
        if R["n"] < 3:
            continue
        ids = [i for i in R["members"][:40] if st_.fit(i) is not None]
        try:
            r = compare(ids, "moving", person=person, start="ref", workers=4)
        except ValueError:
            continue
        n_routes += 1
        t_max = max(t_max, r["compute_s"])
        al = r["align"]
        for sid, q in al["sessions"].items():
            cnt[q["status"]] += 1
            if q["status"] == "excluded":
                continue
            ser = r["series"].get(sid)
            if not ser:
                continue
            d0 = ser["dist_m"][0] or 0
            want = q["join_m"] if q["status"] == "late" else 0
            if abs(d0 - want) > 30:   # first moving second, thinned points
                bad_first.append((rid, sid, d0, want))
            # every session starts level with the pacer (late joiners: where they join)
            if int(sid) != r["ids"][0] and al["pacer_id"] == r["ids"][0] and ser["ahead_m"][0] is not None \
                    and abs(ser["ahead_m"][0]) > 40:
                bad_ahead.append((rid, sid, q["status"], ser["ahead_m"][0]))
        if R["shape"] == "laps":
            continue
        d = compare(ids, "distance", person=person, start="ref", workers=4)
        if d["axis"]["x"] != "route_m":
            continue
        ctx = st_.route(rid)
        o = d["axis"]["origin_m"]
        for s_ in d["sessions"]:
            sid = str(s_["id"])
            q = d["align"]["sessions"][sid]
            ser = d["series"][sid]
            x = nn(ser["x"]); dm = nn(ser["delta_moving_s"]); k = np.isfinite(dm)
            if k.sum() >= 4:
                w = x[k] <= x[k][0] + 20
                ch = float(np.max(np.abs(dm[k][w] - dm[k][0])))
                if ch > 60:
                    bad_start.append((rid, sid, round(ch)))
            if q["kind"] == "crossing" and R["shape"] != "out_and_back":
                # (out & back: the projection matches the shared streets of the other leg, so it's no reference)
                # line crossing vs the route projection: the on-route seconds projected within 30 m of the line's
                # route position (raw projection, before the running max). Precision = the nearest such second;
                # "not the first pass" = the projection was at the line earlier (another lap, or ran past it first)
                tp, on, _ = st_.progress(s_["id"], ctx)
                at = np.nonzero(on & (np.abs(tp - o) <= 30))[0]
                tc = q["offset_s"] - q["zone_pause_s"]
                if len(at):
                    near = at[np.argmin(np.abs(at - tc))]
                    proj_d.append((abs(tc - near), rid, sid, round(tc), int(near)))
                    if at[0] < tc - 120 and q["zone_pause_s"] < tc - at[0]:
                        proj_first.append((rid, sid, round(tc), int(at[0])))
    print(f"\n[common start] {person}: {n_routes} routes, start=ref (each route's newest session is the anchor): "
          f"{cnt['aligned']} aligned, {cnt['late']} late, {cnt['excluded']} excluded; slowest call {t_max} s")
    print(f"  {ok(not bad_first)} every kept session starts at distance 0 (late: at its join_m): {len(bad_first)} misses {bad_first[:3]}")
    print(f"  {ok(not bad_ahead)} distance ahead of the reference at the first point <= 40 m: {len(bad_ahead)} misses {bad_ahead[:3]}")
    print(f"  {ok(not bad_start)} route axis: gap change within the first 20 m <= 60 s: {len(bad_start)} misses {bad_start[:3]}")
    if proj_d:
        pd_ = np.array([v[0] for v in proj_d])
        print(f"  {ok(np.median(pd_) <= 20)} line crossing vs route projection (loops / point-to-point, n={len(pd_)}): median |Δ| {np.median(pd_):.0f} s, "
              f"p90 {np.percentile(pd_, 90):.0f} s (projection: 20 m steps, 80 m on-route tolerance); > 60 s: "
              f"{[v[1:] for v in proj_d if v[0] > 60]}")
        print(f"  line crossing later than the projection's first pass at the line (> 2 min, not a stop): {len(proj_first)} "
              f"{proj_first[:6]}")
    if person != "adrian":
        return

    def run(ids, mode="moving", **kw):
        return compare(ids, mode, person=person, workers=4, **kw)
    route = lambda rid: select_ids(person, route=rid)
    # R11 lead-in: 12910143619 jogged 890 m / 324 s to the usual start
    r = run([11266836943, 12910143619, 12069730602, 9885837163, 12935044109], start="ref")
    q = r["align"]["sessions"]
    s_ = r["series"]["12910143619"]
    am = [round(float(np.interp(t, nn(s_["x"]), nn(s_["ahead_m"])))) for t in (300, 600, 1200, 1800)]
    row = next(x for x in r["sessions"] if x["id"] == 12910143619)
    print(f"\n[R11 lead-in] 12910143619: {q['12910143619']['kind']} cut {q['12910143619']['offset_s']} s / "
          f"{q['12910143619']['offset_m']} m; ahead at 5/10/20/30 min {am} m (own start: -210/-373/-504/-368)")
    print(f"  {ok(abs(q['12910143619']['offset_s'] - 324) < 10 and all(-300 < v < -50 for v in am))} lead-in cut, "
          f"table {row['dist_m']} m / {_mm(row['moving_s'])} moving (whole {row['whole']['dist_m']} m / {_mm(row['whole']['moving_s'])})")
    print(f"  {ok(q['12069730602']['status'] == 'excluded')} parallel street 12069730602: {q['12069730602']['reason']}")
    print(f"  {ok(q['9885837163']['status'] == q['12935044109']['status'] == 'excluded')} loop run the other way: "
          f"{q['9885837163']['reason']}")
    # R3: anchor started its watch at the km 1.86 stop
    ids = [19475311436] + [i for i in route("R3") if i != 19475311436]
    d = run(ids, "distance", start="ref")
    q = d["align"]["sessions"]
    offs = [v["offset_s"] for k, v in q.items() if v["status"] == "aligned"]
    zp = [v["zone_pause_s"] for v in q.values() if v["status"] == "aligned" and v["zone_pause_s"]]
    print(f"\n[R3 anchor at a stop] 19475311436, line at route {d['axis']['origin_m']} m: "
          f"{ok(len(offs) == len(ids) - 1)} {len(offs)} of {len(ids) - 1} aligned, cut at {min(offs):.0f}–{max(offs):.0f} s; "
          f"{len(zp)} left the line after a stop of {min(zp)}–{max(zp)} s")
    # R4: the medoid started its watch ~150 m into the usual route
    m = g["routes"]["R4"]["medoid"]
    ids = [m] + [i for i in route("R4") if i != m]
    r = run(ids, start="ref")
    q = r["align"]["sessions"]
    late = {k: v["join_m"] for k, v in q.items() if v["status"] == "late"}
    al_ = [v["offset_s"] for v in q.values() if v["status"] == "aligned"]
    print(f"\n[R4 late reference] {m}: {len(al_)} aligned (cut {min(al_):.0f}–{max(al_):.0f} s), {len(late)} late {late}, "
          f"{sum(v['status'] == 'excluded' for v in q.values())} excluded")
    lr = [r["series"][k]["ahead_m"][0] for k in late]
    print(f"  {ok(len(late) >= 3 and all(v is not None and abs(v) <= 40 for v in lr))} late joiners start level with the "
          f"reference (ahead at join {lr} m); {ok(q.get('4749963603', {}).get('status') == 'aligned')} 4749963603 (rev in the "
          f"route grouping) crosses the line forwards")
    ref = st_.route("R4")["ref"]
    pt = (float(np.interp(3000, ref["m"], ref["lat"])), float(np.interp(3000, ref["m"], ref["lon"])))
    r = run(ids, "distance", start=f"{pt[0]:.6f},{pt[1]:.6f}")
    q = r["align"]["sessions"]
    print(f"  point at route km 3 ({pt[0]:.5f},{pt[1]:.5f}): {sum(v['status'] == 'aligned' for v in q.values())} aligned; "
          f"{ok(q['5073438359']['status'] == 'excluded' and 'different way' in q['5073438359']['reason'])} 5073438359: "
          f"{q['5073438359']['reason']}")
    # R1 Bislett laps: the first pass, not a later lap
    m = g["routes"]["R1"]["medoid"]
    ids = [m] + [i for i in route("R1") if i != m]
    r = run(ids[:40], start="ref")
    q = r["align"]["sessions"]
    lap = 400 * 1.4
    far = {k: (v["offset_s"], v["offset_m"]) for k, v in q.items() if v["status"] != "excluded" and (v["offset_m"] or 0) > lap}
    print(f"\n[R1 laps] {sum(v['status'] != 'excluded' for v in q.values())} of {len(q)} kept; cut after more than one lap "
          f"(warm-up outside the track): {far}")
    # R13 out & back: sessions running it the other way are not late joiners
    m = g["routes"]["R13"]["medoid"]
    ids = [m] + [i for i in route("R13") if i != m]
    ctx = st_.route("R13")
    rev = [i for i in ids if st_.progress(i, ctx)[2] != st_.progress(m, ctx)[2]]
    ref = ctx["ref"]
    pt = (float(np.interp(2000, ref["m"], ref["lat"])), float(np.interp(2000, ref["m"], ref["lon"])))
    for start in ("ref", f"{pt[0]:.6f},{pt[1]:.6f}"):
        q = run(ids, start=start)["align"]["sessions"]
        print(f"[R13 reversed] start={start}: {ok(all(q[str(i)]['status'] == 'excluded' for i in rev))} reversed {rev}: "
              + "; ".join(f"{q[str(i)]['reason']}" for i in rev))
    # patrik R2 (5-6 s smart recording) is checked by `compare.py patrik validate`


def route_scan(person, max_ids=40):
    """every non-laps route with >= 3 sessions in distance mode: sessions with an impossible speed over a covered
    >= 250 m stretch (> max(2 x own moving speed, 25 / 50 km/h)), plus coverage and gap at the last common point.
    Returns {bad: [...], n, rows: [(rid, id, date, dir, coverage_pct, gap_end_s)]}"""
    g = store(person).groups()
    bad, rows, n = [], [], 0
    for rid, R in g["routes"].items():
        if R["shape"] == "laps" or R["n"] < 3:
            continue
        try:
            r = compare(R["members"][:max_ids], "distance", person=person, workers=4)
        except ValueError:
            continue
        if r["axis"]["x"] != "route_m":
            continue
        st = r["axis"]["step_m"]
        for s in r["sessions"]:
            n += 1
            ser = r["series"][str(s["id"])]
            x = np.array(ser["x"], float)
            ms = np.array([np.nan if v is None else v for v in ser["moving_s"]], float)
            cov = np.array([v is not None for v in ser["speed_kmh"]])   # uncovered bins have no speed
            k = np.isfinite(ms) & cov
            x, ms = x[k], ms[k]
            vmax = 0.0
            if len(x) >= 3:
                j = np.minimum(np.searchsorted(x, x + 300), len(x) - 1)
                dx, dt = x[j] - x, ms[j] - ms
                sel = (dx > 250) & ((j - np.arange(len(x))) * st >= dx - st)   # windows not spanning a gap in x
                vmax = float(np.max(np.where(sel, dx / np.maximum(dt, 0.5) * 3.6, 0))) if sel.any() else 0.0
            ge = (s.get("gap_end") or {}).get("delta_moving_s")
            rows.append((rid, s["id"], s["date"], ser["dir"], s.get("coverage_pct"), ge))
            if vmax > max(2.0 * (s["moving_kmh"] or 0), 50 if s["sport"] == "ski" else 25):
                bad.append((rid, s["id"], s["date"], ser["dir"], round(vmax)))
    return {"bad": bad, "n": n, "rows": rows}


def validate_fixes(person):
    """Phase 0 correctness checks (docs/compare.md#validation): clock jumps, ghost-race jumps / coverage, route
    lead-in, intervals timeline (reps at full resolution, empty slots left empty)"""
    st_ = store(person)
    g = st_.groups()
    A = g["activities"]
    ok = lambda c: "OK " if c else "BAD"
    # 0.1 clock jumps: the reader's time base matches the session's elapsed time, no day-long record gaps left
    import gps_quality as gq
    fixed, left = [], []
    for f in sorted((st_.D / "activities").glob("*/*.fit")):
        raw = gq._read_fast(f)
        recs = sorted((r for r in raw["record"] if "timestamp" in r), key=lambda r: r["timestamp"])
        if len(recs) < 2 or recs[-1]["timestamp"] - recs[0]["timestamp"] < gq.CLOCK_GAP_S:
            continue
        if not any(b["timestamp"] - a["timestamp"] >= gq.CLOCK_GAP_S for a, b in itertools.pairwise(recs)):
            continue
        s = sg.read_series(f)
        el = (raw["session"][0].get("total_elapsed_time") or 0) / 1000 if len(raw["session"]) == 1 else None
        if s["clock_jumps"]:
            fixed.append((int(f.stem), round(sum(c for _, c in s["clock_jumps"]))))
        if el and s["t"][-1] - el > gq.CLOCK_SLACK_S:
            left.append(int(f.stem))
    print(f"[clock jumps] {len(fixed)} FIT files with a watch clock jump taken out "
          f"({', '.join(f'{i} −{c / 86400:.1f} d' for i, c in fixed[:12])})")
    print(f"  {ok(not left)} record span = session elapsed time (+-{gq.CLOCK_SLACK_S} s) in every file with long gaps "
          f"({len(left)} left: {left[:5]})")
    iv = [i for i, _ in fixed if A.get(str(i), {}).get("reps")]
    if iv:
        r = compare(iv, "intervals", person=person, match="index")
        n = {sid: sum(1 for p in s_["phase"] if p == 1) for sid, s_ in r["series"].items()}
        el = {s_["id"]: s_["elapsed_s"] for s_ in r["sessions"]}
        print(f"  {ok(all(v > 100 for v in n.values()) and max(el.values()) < 86400)} interval sessions with a "
              f"clock jump draw their reps: rep points {n}, elapsed {el}")
    # 0.2 ghost race: no impossible speed over covered stretches; coverage
    sc = route_scan(person)
    cov = [c for *_, c, _ in sc["rows"] if c is not None]
    print(f"\n[ghost race] {sc['n']} route sessions (non-laps routes with >= 3 sessions)")
    print(f"  {ok(not sc['bad'])} no impossible speed over a covered >= 250 m stretch: {len(sc['bad'])} sessions {sc['bad'][:6]}")
    ge = sorted((v, rid, i, d) for rid, i, d, _, _, v in sc["rows"] if v is not None)
    print(f"  coverage: median {np.median(cov):.0f} %, {sum(c < 90 for c in cov)} sessions < 90 %; gap at the last common "
          f"point most ahead {ge[0] if ge else '-'}, most behind {ge[-1] if ge else '-'}")
    rev = [(rid, i, d, c, v) for rid, i, d, dr, c, v in sc["rows"] if dr == "rev"]
    print(f"  reversed sessions (own travel direction): {len(rev)}; |gap at end| max "
          f"{max((abs(v) for *_, v in rev if v is not None), default=0):.0f} s")
    # 0.3 route lead-in: the gap changes by little within the first 60 m after a session starts on the axis
    worst = []
    for rid, R in g["routes"].items():
        if R["shape"] == "laps" or R["n"] < 3:
            continue
        r = compare(R["members"][:40], "distance", person=person, workers=4)
        if r["axis"]["x"] != "route_m":
            continue
        for s_ in r["sessions"]:
            ser = r["series"][str(s_["id"])]
            x = np.array(ser["x"], float); dm = np.array([np.nan if v is None else v for v in ser["delta_moving_s"]], float)
            k = np.isfinite(dm)
            if k.sum() < 4:
                continue
            xs, ds = x[k], dm[k]
            w = xs <= xs[0] + 20
            worst.append((float(np.max(np.abs(ds[w] - ds[0]))), rid, s_["id"]))
    worst.sort(reverse=True)
    n_big = sum(1 for v, *_ in worst if v > 60)
    print(f"\n[route start] gap change within the first 20 m on the axis: {ok(n_big == 0)} {n_big} sessions > 60 s "
          f"(lead-in dumped at the start); worst {[(round(v), rid, i) for v, rid, i in worst[:3]]}")
    # 0.4 intervals timeline: reps at full resolution, no break through an empty slot's rep area
    sets = [[i for i in g["routes"].get("R3", {}).get("members", []) if A[str(i)].get("type") == "ski_intervals"],
            select_ids(person, sig="6×1000", place="Bislet", last=8), [8884710494, 8835446895]]
    sets += [[i for i, _ in fixed if A.get(str(i), {}).get("reps")]]
    for ids in sets:
        ids = [i for i in ids if str(i) in A and st_.fit(i) is not None]
        if len(ids) < 2:
            continue
        r = compare(ids, "intervals", person=person)
        x0, w = r["axis"]["slot_x0"], r["axis"]["slot_width"]
        through, coarse = 0, 0
        for sid, s_ in r["series"].items():
            x = np.array(s_["x"], float); ph = np.array(s_["phase"])
            sp = np.array([np.nan if v is None else v for v in s_["speed_kmh"]], float)
            mine = {k for k, q in enumerate(r["reps"][sid]) if q}
            for k in range(len(x0)):
                if k in mine:
                    rx = np.diff(x[(ph == 1) & (x >= x0[k]) & (x <= x0[k] + w[k])])
                    coarse += int(len(rx) > 0 and np.max(rx) > 1.01)
                else:   # no rep here: nothing drawn inside the rep area (a null point breaks the line)
                    inside = (x > x0[k] + 0.5) & (x < x0[k] + w[k] - 0.5) & np.isfinite(sp)
                    through += int(inside.any())
        print(f"[intervals] {len(ids)} sessions, slots {[q['label'] for q in r['slots']]}: {ok(coarse == 0)} reps at 1 s "
              f"({coarse} coarse); {ok(through == 0)} empty slots left empty ({through} drawn through)")


def validate_reps(person):
    """t = 0 at rep 1 (start=rep1) and the per-rep series (rep_series), on the person's two most common rep signatures
    (8 newest each, else the 6 newest interval sessions; + one easy session that must be left out), adrian's 6×1000 vs
    1×800 + 5×1000 (late rep 1) and patrik's clock-jump pair:
    - rep 1 at x = 0 (|x| < 1 s; distance mode 0 m) for every kept session, late ones at their x offset
    - real breaks kept: in the elapsed view every rep k starts at x = its start - rep 1's start, with a speed jump there
    - sessions without reps excluded
    - rep_series: every rep from x = -lead_s on a 1 s grid, speed jump at x = 0, nothing after own end + post_s"""
    st_ = store(person)
    A = st_.groups()["activities"]
    ok = lambda c: "OK " if c else "BAD"
    nn = lambda a: np.array([np.nan if v is None else v for v in a], float)
    from collections import Counter
    sigs = Counter(a.get("sig") for k, a in A.items() if a.get("reps") and a.get("sig") and st_.fit(k) is not None)
    sets = []
    easy = sorted((a["date"], int(k)) for k, a in A.items() if k.isdigit() and not a.get("reps") and a.get("sport") == "run"
                  and (a.get("dist_km") or 0) > 1 and st_.fit(k) is not None)
    for sig, n in sigs.most_common(2):
        if n >= 2:
            sets.append((sig, select_ids(person, sig=sig, last=8) + ([easy[-1][1]] if easy else [])))
    if not sets:   # no repeated signature (patrik: mixed sessions): the newest interval sessions
        ivs = sorted(((a["date"], int(k)) for k, a in A.items() if k.isdigit() and a.get("reps") and st_.fit(k) is not None), reverse=True)
        sets.append(("newest interval sessions", [i for _, i in ivs[:6]] + ([easy[-1][1]] if easy else [])))
    if person == "adrian":
        sets.append(("6×1000 vs 1×800 + 5×1000", [24874084265, 24288925182, 8884710494]))
    if person == "patrik":
        sets.append(("clock-jump pair", [23044028473, 21445487763]))
    print(f"\n[rep 1 start / per-rep series] {person}")
    for name, ids in sets:
        ids = [i for i in ids if st_.fit(i) is not None]
        miss0, missk, nk, excl_bad, late, roll = 0, 0, 0, 0, 0, 0
        iv = compare(ids, "intervals", person=person, rep_series=True, max_points=20000)
        for mode in ("elapsed", "moving", "distance"):
            r = compare(ids, mode, person=person, start="rep1", max_points=20000)
            al = r["align"]["sessions"]
            for i in ids:
                a = al[str(i)]
                no_reps = not (A.get(str(i)) or {}).get("reps")
                if no_reps != (a["status"] == "excluded"):
                    excl_bad += 1
                if a["status"] == "excluded":
                    continue
                x = nn(r["series"][str(i)]["x"])
                want = 0.0 if a["status"] != "late" else (a["join_m"] if mode == "distance" else
                                                         a["x_offset_s"] if mode == "elapsed" else a["x_offset_moving_s"])
                late += a["status"] == "late"
                if abs(x[0] - want) > (10 if mode == "distance" else 1.01):
                    miss0 += 1
                if mode != "elapsed" or a["status"] == "late":
                    continue
                # every rep k at x = start_k - start_1 (real breaks kept), with a speed jump there
                v = nn(r["series"][str(i)]["speed_kmh"])
                reps = [q for q in iv["reps"][str(i)] if q]
                r1 = next(q for q in reps if q["n"] == a["rep_n"])
                for q in reps:
                    if q["n"] <= a["rep_n"] or not q["dist_m"]:
                        continue
                    xs = q["start_s"] - r1["start_s"]
                    pre, post = v[(x >= xs - 12) & (x < xs - 3)], v[(x >= xs + 5) & (x < xs + 15)]
                    if not (len(pre) and len(post)):
                        continue
                    if np.nanmean(pre) > 0.6 * (q["speed_kmh"] or 0):   # rolling start out of a fast jog: no step
                        roll += 1
                        continue
                    nk += 1
                    if not np.nanmean(post) > np.nanmean(pre) + 3:
                        missk += 1
        rs = iv["rep_series"]
        nrep, jump, grid, tail, roll2 = 0, 0, 0, 0, 0
        for S in rs["slots"]:
            x = S["x0"] + np.arange(S["n"])
            grid += S["n"] != rs["lead_s"] + S["width"] + rs["post_s"] + 1
            for sid, q in S["series"].items():
                v = nn(q["speed_kmh"])
                nrep += 1
                pre, post = v[(x >= -12) & (x < -3)], v[(x >= 5) & (x < 15)]
                if np.isfinite(pre).any() and np.isfinite(post).any():
                    if np.nanmean(pre) > 0.6 * ((iv["reps"][sid][S["slot"]] or {}).get("speed_kmh") or 0):
                        roll2 += 1
                    elif not np.nanmean(post) > np.nanmean(pre) + 3:
                        jump += 1
                tail += int(np.isfinite(v[x > q["time_s"] + rs["post_s"] + 1]).any())
        print(f"  {name}: {len(ids)} sessions ({late // 3} late)")
        print(f"    {ok(miss0 == 0)} rep 1 at x = 0 (late: at its offset) in elapsed / moving / distance: {miss0} misses; "
              f"{ok(excl_bad == 0)} sessions without reps excluded ({excl_bad} wrong)")
        print(f"    {ok(missk == 0)} real breaks kept: speed jump at x = rep k start - rep 1 start in elapsed, {missk} of "
              f"{nk} reps miss ({roll} rolling starts out of a jog > 60 % of rep speed not checked)")
        print(f"    {ok(grid == 0 and tail == 0)} rep_series: {len(rs['slots'])} slots, {nrep} reps from x = -{rs['lead_s']:.0f} s on "
              f"a 1 s grid ({grid} bad grids, {tail} with values after end + post); {ok(jump == 0)} speed jump at "
              f"x = 0: {jump} misses ({roll2} rolling starts not checked)")


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
    ap.add_argument("--start", help="common start line: ref | <id> | lat,lon (time and distance modes)")
    ap.add_argument("--start-radius", type=float, default=START_RADIUS_M, help="start line half-width (m)")
    ap.add_argument("--json", help="write the result dict here")
    a = ap.parse_args()
    if a.mode == "validate":
        validate(a.person)
        return
    ids = select_ids(a.person, a.ids, a.route, a.sig, a.place, a.last)
    r = compare(ids, a.mode, person=a.person, reference=a.reference, axis=a.axis, rep_axis=a.rep_axis, match=a.match,
                start=a.start, start_radius_m=a.start_radius)
    print_result(r)
    if a.json:
        js = json.dumps(r, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        Path(a.json).write_text(js)
        print(f"wrote {a.json} ({len(js) / 1e3:.0f} kB)")


if __name__ == "__main__":
    main()

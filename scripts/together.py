"""Together: workouts done by two or more people (detection, groups, session engine). See docs/together.md.

    uv run scripts/together.py build [--full]       # detect -> data/together.json (incremental: changed pairs only)
    uv run scripts/together.py list [--all]         # groups (--all: also the hidden near-misses)
    uv run scripts/together.py show <gid>           # one group + its session results (standings, totals)
    uv run scripts/together.py validate             # generic checks on together.json and every session

Detection (build): every pair of people, index-level candidates (time windows overlap, start times from the FIT
because of watch clock jumps) -> per pair metrics on the time overlap at 1 Hz on absolute UTC time -> pair label
(same_workout / together / partly_together / same_race / unverified / same_place_diff_workout / not_together) ->
groups = connected components over the shown labels (any number of people) -> stable group ids, persisted in
data/together.json and kept on rebuild. Caches: data/_together/cache.pkl (FIT start times, pair results).

Session engine (session / replay): a thin layer on the per-person compare engine (scripts/compare.py: store
sessions, rep detection, `_reps_slots` main reps, `_break_stats`), which isn't forked. Reps are paired across people
by clock time (with the pair's clock offset), results are times for the set distance per rep."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import itertools
import json
import math
import os
import pickle
import re
import sys
import time
import unicodedata
import warnings
from bisect import bisect_left
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
import compare as cmp
import gps_quality as gq
import session_groups as sg

DATA = ROOT / "data"
OUT = DATA / "together.json"
PEOPLE_JSON = DATA / "people.json"
CDIR = DATA / "_together"        # not a person folder (no activities_index.json)
CACHE = CDIR / "cache.pkl"
SESS_CACHE = CDIR / "sessions.pkl"
FORMAT_VERSION = 1
PARAM_VERSION = 1                # detection rules / thresholds: bump -> every pair recomputed
CACHE_VERSION = 1
SESSION_VERSION = 1              # session engine output: bump -> every cached session rebuilt

# ------------------------------------------------------------------ detection thresholds (docs/together.md)
PRE_MARGIN_S = 600     # index candidates: time windows overlap (with this margin)
MIN_OVERLAP_S = 300    # less shared time than this: nothing to judge
NEAR_M = 50            # "with each other": 60 s rolling median distance <= NEAR_M ...
ROLL_S = 61
DV_MS = 0.8            # ... and 60 s rolling median |speed difference| <= DV_MS
MOVE_MS = 1.0          # ... counted over seconds where at least one is moving
MOVE_HIKE_MS = 0.25    # moving, on a hike (steep stairs are slow: ~0.35 m/s)
SMOOTH_S = 15
TOG_FULL = 0.60        # with-share of the joint moving time -> together
TOG_OVERRIDE = 0.80    # with-share so high that the rep structure doesn't matter
TOG_PART_S = 600       # >= 10 min with each other -> partly together / warm-up together
REP_MIN_S = 30         # reps shorter than this are ignored by detection
ONSET_DT_S = 12        # other's speed step within this of the rep start (after the pair's clock offset)
ONSET_RATIO = 1.3      # other's speed after / before the rep start
ONSET_ON = 0.7         # other is "on" this share of the first min(dur, 120 s) of the rep
PACE_RATIO = 0.7       # other's speed in the rep >= this x own rep speed
REP_POS_M = 60         # positions at the rep start within this (if both have GPS)
SHARED_FULL = 0.75     # shared rep time / own rep time (in the overlap), best side -> same workout
SHARED_MIN = 2
RACE_START_S, RACE_START_M = 120, 150
IDX_START_M, IDX_START_S = 500, 900   # index-only (no FIT) plausibility
CLOCK_FIX_S = 60       # FIT record span exceeds the elapsed time by more than this: index start is wrong
OFFSET_MIN_N, OFFSET_MAX_DMED = 600, 10.0   # a pair's own position offset is trusted with >= 600 s side by side, <= 10 m
IV = ("intervals_track", "intervals_road", "intervals_treadmill", "ski_intervals")
UNTYPED = ("multi_sport", "run_other", "other")
BISLET = (59.92527, 10.73306)

SHOW = ("same_workout", "together", "partly_together", "same_race", "unverified")
NEAR = ("same_place_diff_workout",)
RANK = {"same_workout": 6, "together": 6, "same_race": 5, "partly_together": 4, "unverified": 3,
        "same_place_diff_workout": 2, "not_together": 0, "error": -1}
LABEL_TEXT = {"same_workout": "Same workout", "together": "Side by side", "partly_together": "Partly together",
              "same_race": "Same race", "unverified": "Unverified (no track)",
              "same_place_diff_workout": "Same place, different workout"}

# ------------------------------------------------------------------ session engine
MATCH_TOL_S = 8.0      # cross-person rep pairing: |clock start difference| <= max(8 s, 2 x sample interval) ...
MATCH_OVERLAP = 0.5    # ... and the reps overlap > 50 % of the shorter one
TAIL_TOL = 0.04        # a detected rep > 4 % longer than the set distance (x GPS scale): time at the set distance
FULL_TOL = 0.08        # a rep within +-8 % of the set distance is the full rep (GPS error: time = detector time)
PARTIAL = 0.92         # shorter than 92 % of the set distance: partial (shown, not ranked)
TIME_TOL = 0.15        # time-target reps (0:45): own duration within +-15 % of the set time
SUSPECT_FAST = 0.10    # a rep > 10 % faster than the person's median for the same set distance: implausible (flagged)
TIE_MIN_S = 1.0        # dead heat: |gap| <= max(1 s, half the coarser recording interval)
NICE_M = (100, 150, 200, 250, 300, 400, 500, 600, 800, 1000, 1200, 1500, 1600, 2000, 2500, 3000, 4000, 5000)
REPLAY_STEP_S = 2
# replay passes: a pair's order flips and the new lead holds >= PASS_M for >= PASS_HOLD_S consecutive seconds
# (hysteresis: GPS jitter / running side by side isn't a pass). rep = along the track (own distance x scale),
# km / route = along the reference's path (projection: noisier)
PASS_M = {"rep": 2.0, "km": 8.0, "route": 10.0}
PASS_HOLD_S = {"rep": 3, "km": 8, "route": 10}
KM_MIN_M = 300         # continuous: a last partial km shorter than this isn't a segment
CONT_STEP_M = 50       # continuous: gap-over-distance grid
GAP_VMIN, GAP_VMIN_HIKE = 1.5, 0.25   # m/s: slowest plausible moving speed (bounds a time gap by the distance apart)
GAP_SPIKE_S = 60       # a gap > this off its 450 m running median (a spike <= 200 m wide): projection artefact -> null
APART_STEP_S = 5       # continuous: distance-apart grid

FAMILY = {   # Garmin typeKey -> sport family (same as app/server/store.py FAMILY)
    "running": "run", "track_running": "run", "treadmill_running": "run", "indoor_running": "run",
    "trail_running": "run", "street_running": "run",
    "cross_country_skiing_ws": "ski", "skate_skiing_ws": "ski", "backcountry_skiing": "ski",
    "hiking": "hike", "walking": "hike",
    "cycling": "bike", "road_biking": "bike", "mountain_biking": "bike", "indoor_cycling": "bike",
    "lap_swimming": "swim", "open_water_swimming": "swim",
}
STYPE_LABEL = {"easy": "Easy run", "long": "Long run", "tempo": "Tempo run", "race?": "Race", "hike": "Hike",
               "run_other": "Run", "run_nogps": "Run (no GPS)", "cycling": "Ride", "walking": "Walk"}
# automatic person colours (light theme, dark theme): blue, orange, green, purple, teal, ochre, red, slate.
# Each has >= 3:1 contrast on white and on the dark background, and neighbours differ for colour-blind readers.
AUTO_COLOURS = [("#2f5fd0", "#6f96ee"), ("#c2560c", "#f0884a"), ("#2f8a4c", "#5cbf7a"), ("#8e44ad", "#b07fd6"),
                ("#1f8fa6", "#45b5cc"), ("#b88a00", "#d9ae2a"), ("#c43c3c", "#ec6d6d"), ("#5d6d7e", "#9aa8b6")]


def _r(v, n=1):
    return cmp._r(v, n)


def _iso(ts):
    return dt.datetime.fromtimestamp(ts, dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _jsonable(o):
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, (float, np.floating)):
        f = float(o)
        return None if math.isnan(f) or math.isinf(f) else f
    return o


# ------------------------------------------------------------------ people
def people() -> list[str]:
    return sorted(p.parent.name for p in DATA.glob("*/activities_index.json"))


def people_info(ps=None) -> dict:
    """{person: {name, initials, colour, colour_dark, auto}}: data/people.json (optional, hand-edited) +
    automatic names / colours / initials for everyone not in it."""
    try:
        cfg = json.loads(PEOPLE_JSON.read_text()) if PEOPLE_JSON.exists() else {}
    except Exception:  # noqa: BLE001
        cfg = {}
    ps = list(ps or people())
    used = {(c or {}).get("colour") for c in cfg.values()}
    free = [c for c in AUTO_COLOURS if c[0] not in used]
    out = {}
    for k, p in enumerate(sorted(set(ps) | set(cfg))):
        c = cfg.get(p) or {}
        name = c.get("name") or p.replace("_", " ").title()
        if c.get("colour"):
            col, dark = c["colour"], c.get("colour_dark") or c["colour"]
        else:
            col, dark = free.pop(0) if free else AUTO_COLOURS[k % len(AUTO_COLOURS)]
        out[p] = {"name": name, "initials": c.get("initials"), "colour": col, "colour_dark": dark,
                  "auto": not c.get("colour")}
    # automatic initials: the shortest name prefix (1-3 letters) that no one else has
    taken = {v["initials"] for v in out.values() if v["initials"]}
    auto = [v for v in out.values() if not v["initials"]]
    for v in auto:
        for k in (1, 2, 3):
            ini = v["name"][:1].upper() + v["name"][1:k].lower()
            others = [w["name"][:1].upper() + w["name"][1:k].lower() for w in auto if w is not v]
            if ini not in taken and ini not in others:
                break
        v["initials"] = ini
        taken.add(ini)
    return out


def hr_norm(person) -> dict:
    """{max, rest, mode: hrr|hrmax, source, warn}: data/<p>/profile/hr_settings.json {max_hr, rest_hr} (hand-edited),
    else the watch's heart_rate_zones.json (max from the zones; rest HR rarely set: then % of max HR, with a warning)."""
    P = DATA / person / "profile"
    try:
        h = json.loads((P / "hr_settings.json").read_text())
        mx, rs = h.get("max_hr"), h.get("rest_hr")
        if mx:
            return {"max": int(mx), "rest": int(rs) if rs else None, "mode": "hrr" if rs else "hrmax",
                    "source": "hr_settings", "warn": None if rs else "no resting HR: effort = % of max HR"}
    except Exception:  # noqa: BLE001, S110
        pass
    try:
        z = json.loads((P / "heart_rate_zones.json").read_text())
        z = next((x for x in z if x.get("sport") == "DEFAULT"), z[0])
        mx, rs = z.get("maxHeartRateUsed"), z.get("restingHeartRateUsed")
        if mx:
            return {"max": int(mx), "rest": int(rs) if rs else None, "mode": "hrr" if rs else "hrmax",
                    "source": "watch_zones",
                    "warn": "HR max / rest from the watch's zone settings (may be defaults)" + ("" if rs else "; effort = % of max HR")}
    except Exception:  # noqa: BLE001, S110
        pass
    return {"max": None, "rest": None, "mode": None, "source": None, "warn": "no HR settings: effort not computed"}


def _effort(hr, H):
    if hr is None or not H.get("max"):
        return None
    if H["mode"] == "hrr":
        return _r(100 * (hr - H["rest"]) / max(1, H["max"] - H["rest"]), 0)
    return _r(100 * hr / H["max"], 0)


# ------------------------------------------------------------------ caches
class _Cache:
    def __init__(self, path=CACHE):
        self.path = path
        self.d = {"_v": CACHE_VERSION, "fit": {}, "pair": {}}
        if path.exists():
            try:
                d = pickle.loads(path.read_bytes())
                if d.get("_v") == CACHE_VERSION:
                    self.d = d
            except Exception:  # noqa: BLE001, S110
                pass
        self.dirty = False

    def save(self):
        if self.dirty:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_bytes(pickle.dumps(self.d))
            tmp.replace(self.path)
            self.dirty = False


def fkey(p: Path):
    st = p.stat()
    return (st.st_mtime_ns, st.st_size)


def _raw_times(f):
    raw = gq._read_fast(f)
    ts = sorted({r["timestamp"] for r in raw["record"] if "timestamp" in r})
    prod = next((x.get("product") for x in raw.get("file_id", []) if x.get("product")), None)
    return raw, ts, prod


def fit_index(cache: _Cache, f: Path, elapsed: float) -> dict:
    """per FIT (cached by mtime + size): true UTC start when the watch clock jumped (record span > elapsed + 60 s),
    product, median record interval"""
    k = str(f)
    key = fkey(f)
    e = cache.d["fit"].get(k)
    if e and e["key"] == key and e.get("elapsed") == elapsed:
        return e
    try:
        _, ts, prod = _raw_times(f)
    except Exception:  # noqa: BLE001
        ts, prod = [], None
    start = None
    if len(ts) >= 2 and max(ts) - min(ts) - elapsed > CLOCK_FIX_S:
        start = max(ts) + gq.FIT_EPOCH - elapsed
    e = {"key": key, "elapsed": elapsed, "start": start, "prod": prod,
         "sample_s": float(np.median(np.diff(ts))) if len(ts) > 2 else None}
    cache.d["fit"][k] = e
    cache.dirty = True
    return e


def _utc(s):
    return dt.datetime.fromisoformat(s).replace(tzinfo=dt.UTC).timestamp()


def index(person, cache: _Cache) -> list[dict]:
    """activities of a person with true UTC start / end (s, e), local-time offset, groups.json meta, FIT path"""
    D = DATA / person
    A = json.loads((D / "activities_index.json").read_text())
    gp = D / "groups.json"
    g = json.loads(gp.read_text()) if gp.exists() else {"activities": {}, "places": {}}
    fits = {int(p.stem): p for p in (D / "activities").glob("*/*.fit")}
    out = []
    for x in A:
        try:
            s = _utc(x["startTimeGMT"])
            tz = (dt.datetime.fromisoformat(x["startTimeLocal"]) - dt.datetime.fromisoformat(x["startTimeGMT"])).total_seconds()
        except Exception:  # noqa: BLE001, S112
            continue
        el = float(x.get("elapsedDuration") or x.get("duration") or 0)
        i = int(x["activityId"])
        f = fits.get(i)
        fi = fit_index(cache, f, el) if f else None
        fixed = bool(fi and fi["start"] is not None and abs(fi["start"] - s) > CLOCK_FIX_S)
        if fixed:
            s = fi["start"]
        out.append({"person": person, "id": i, "s": s, "e": s + el, "tz": tz, "x": x,
                    "meta": g["activities"].get(str(i)) or {}, "fit": f, "places": g.get("places", {}),
                    "clock_fixed": fixed, "prod": fi["prod"] if fi else None,
                    "sample_s": fi["sample_s"] if fi else None})
    return out


# ------------------------------------------------------------------ 1 Hz tracks on absolute time
_TR: dict = {}


def _smooth(v, k):
    return np.convolve(np.pad(v, (k // 2, k - 1 - k // 2), mode="edge"), np.ones(k) / k, mode="valid")


def _rep_cond(m):
    """which activities get detector reps in detection (interval types, untyped runs / multisport, indoor laps)"""
    typ = m.get("type")
    ind = m.get("indoor_bislett")
    return bool(ind and ind.get("rep_laps")) or typ == "ski_intervals" or \
        (m.get("sport") == "run" and typ in IV) or typ in UNTYPED or m.get("atype") == "multi_sport"


def track(a) -> dict:
    """1 Hz grid of an activity from the compare engine's store (same reader, clock-jump fix and Bislett indoor
    speed), base = UTC of grid second 0 (anchored on the last record: the post-jump clock), detection reps."""
    k = (a["person"], a["id"])
    if k in _TR:
        return _TR[k]
    f, m = a["fit"], a["meta"]
    st = cmp.store(a["person"])
    try:
        S = st.session(a["id"])
        G = S["G"]
    except Exception:  # noqa: BLE001 — no distance and no HR: the engine refuses it; detection reads it anyway
        S = None
        G = sg.to_grid(sg.read_series(f))
        sg.indoor_grid(G, m)
    _, ts, prod = _raw_times(f)
    base = ts[-1] + gq.FIT_EPOCH - G["T"]
    gps = G["gps"].copy() if G["gps"] is not None else np.zeros(G["T"] + 1, bool)
    ind = m.get("indoor_bislett")
    if ind and "lat" in G:   # indoor Bislett: stale / garbage positions near the track
        near = np.hypot((G["lat"] - BISLET[0]) * 111320, (G["lon"] - BISLET[1]) * 55700) < 400
        gps &= ~near
    reps, src = [], None
    if _rep_cond(m) and S is not None:
        try:
            r = st.reps([a["id"]], workers=1).get(a["id"]) or {}
            reps = [(x["start"], x["end"]) for x in r.get("reps") or []]
            src = r.get("mode")
        except Exception as e:  # noqa: BLE001
            print("rep err", k, e, file=sys.stderr)
    reps = [(x, y) for x, y in reps if y - x >= REP_MIN_S]
    T = {"person": a["person"], "id": a["id"], "prod": prod, "G": G, "S": S, "gps": gps, "base": float(base),
         "vs": _smooth(G["v"], SMOOTH_S), "v5": _smooth(G["v"], 5), "reps": reps, "rep_src": src, "T": G["T"],
         "sample_s": float(np.median(np.diff(ts))) if len(ts) > 2 else 1.0,
         "sample_p90_s": float(np.percentile(np.diff(ts), 90)) if len(ts) > 2 else 1.0}
    if len(_TR) > 48:
        _TR.clear()
    _TR[k] = T
    return T


def xy(lat, lon, o):
    return (lat - o[0]) * 111320.0, (lon - o[1]) * 111320.0 * math.cos(math.radians(o[0]))


def at(S, t, key):
    """grid series `key` at absolute times t (linear; NaN outside)"""
    y = S["G"].get(key) if key in S["G"] else S.get(key)
    if y is None:
        return np.full(len(t), np.nan)
    return np.interp(t - S["base"], np.arange(S["T"] + 1), y, left=np.nan, right=np.nan)


def gps_at(S, t):
    g = np.round(np.asarray(t, float) - S["base"]).astype(int)
    ok = (g >= 0) & (g <= S["T"])
    out = np.zeros(len(g), bool)
    out[ok] = S["gps"][g[ok]]
    return out


def pos_lag(A, B, t, sel, o):
    """tau: B's position at t+tau best matches A's at t (median distance over `sel`)"""
    ax, ay = xy(at(A, t, "lat"), at(A, t, "lon"), o)
    best = (None, np.inf)
    for tau in np.arange(-15, 15.01, 0.25):
        bx, by = xy(at(B, t + tau, "lat"), at(B, t + tau, "lon"), o)
        d = np.hypot(ax - bx, ay - by)[sel]
        d = d[np.isfinite(d)]
        if len(d) > 120 and np.median(d) < best[1]:
            best = (float(tau), float(np.median(d)))
    return best


def onset(S, t0, lo=-20, hi=25):
    """speed step near absolute time t0: (onset time, v before, v after) or None"""
    tt = np.arange(t0 + lo - 40, t0 + 130, 1.0)
    v = at(S, tt, "v5")
    if np.isnan(v).mean() > 0.3:
        return None
    rel = tt - t0
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        pre = np.nanmedian(v[(rel >= -40) & (rel <= -5)])
        post = np.nanmedian(v[(rel >= 10) & (rel <= 60)])
    if not (np.isfinite(pre) and np.isfinite(post)):
        return None
    thr = (pre + post) / 2
    w = np.flatnonzero((rel >= lo) & (rel <= hi) & (v >= thr))
    if not len(w) or post < 2.0:
        return None
    return float(t0 + rel[w[0]]), float(pre), float(post)


def follows(S_own, S_oth, reps, tau, both_gps_fn, o):
    """which of S_own's reps (absolute [(s, e)]) S_oth starts too: [(k, dt_onset, dpos, t_onset_oth)]"""
    out = []
    for k, (s, e) in enumerate(reps):
        on_own = onset(S_own, s, -8, 8)
        on_oth = onset(S_oth, s + tau)
        if not on_oth:
            continue
        t_oth, pre, post = on_oth
        if post < ONSET_RATIO * max(pre, 0.3):
            continue
        if on_own and post < PACE_RATIO * on_own[2]:   # other's "rep" much slower than own: jogging, not the rep
            continue
        dur = min(e - s, 120)
        tt = np.arange(s + 15, s + max(16, dur), 1.0)
        vo = at(S_oth, tt + tau, "v5")
        if np.nanmean(vo >= (pre + post) / 2) < ONSET_ON:
            continue
        ref = on_own[0] if on_own else s
        dto = t_oth - ref   # includes tau (raw clock difference)
        if abs(dto - tau) > ONSET_DT_S:
            continue
        dpos = None
        if both_gps_fn(np.array([s]))[0]:
            pa = xy(at(S_own, np.array([s]), "lat"), at(S_own, np.array([s]), "lon"), o)
            pb = xy(at(S_oth, np.array([s + tau]), "lat"), at(S_oth, np.array([s + tau]), "lon"), o)
            dpos = float(np.hypot(pa[0] - pb[0], pa[1] - pb[1])[0])
            if dpos > REP_POS_M:
                continue
        out.append((k, round(float(dto), 1), None if dpos is None else round(dpos), t_oth))
    return out


def segments(mask, min_len=60, gap=90):
    runs, cur = [], None
    for i in np.flatnonzero(mask):
        if cur and i - cur[1] <= gap:
            cur[1] = i
        else:
            if cur:
                runs.append(cur)
            cur = [i, i]
    if cur:
        runs.append(cur)
    return [(a, b) for a, b in runs if b - a >= min_len]


def rolling_median(x, k):
    """centred rolling median over k samples, NaN-aware (needs >= k/3 finite values)"""
    h = k // 2
    xp = np.pad(np.asarray(x, float), (h, k - 1 - h), constant_values=np.nan)
    W = np.lib.stride_tricks.sliding_window_view(xp, k)
    cnt = np.isfinite(W).sum(1)
    out = np.full(len(x), np.nan)
    ok = cnt >= k // 3
    if ok.any():
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            out[ok] = np.nanmedian(W[ok], axis=1)
    return out


def first_fix(a, S):
    if S is not None and S["gps"].any():
        j = int(np.argmax(S["gps"]))
        return (float(S["G"]["lat"][j]), float(S["G"]["lon"][j]))
    if a["x"].get("startLatitude"):
        return (a["x"]["startLatitude"], a["x"]["startLongitude"])
    return None


def with_mask(A, B, t, tau=0.0, o=None):
    """per absolute second t: both GPS, distance (m), "with each other" (rolling medians), joint moving"""
    both = gps_at(A, t) & gps_at(B, t + tau)
    va, vb = at(A, t, "vs"), at(B, t + tau, "vs")
    mov = (va > MOVE_MS) | (vb > MOVE_MS)
    o = o or (float(np.nanmedian(at(A, t, "lat"))), float(np.nanmedian(at(A, t, "lon"))))
    ax, ay = xy(at(A, t, "lat"), at(A, t, "lon"), o)
    bx, by = xy(at(B, t + tau, "lat"), at(B, t + tau, "lon"), o)
    d = np.hypot(ax - bx, ay - by)
    d[~both] = np.nan
    dr = rolling_median(d, ROLL_S)
    dv = rolling_median(np.abs(va - vb), ROLL_S)
    w = both & (dr <= NEAR_M) & (dv <= DV_MS)
    return both, d, w, mov, va, vb


def pair(a, b):
    """metrics + label for two activities of different people (a's person < b's person)"""
    r = {"a": [a["person"], a["id"]], "b": [b["person"], b["id"]]}
    if a["fit"] is None or b["fit"] is None:
        sa, sb = first_fix(a, None), first_fix(b, None)
        r["start_dist_m"] = round(sg.hav(sa, sb)) if sa and sb else None
        r["dstart_s"] = round(b["s"] - a["s"])
        ov = min(a["e"], b["e"]) - max(a["s"], b["s"])
        r["overlap_s"] = round(ov)
        r["overlap_start_utc"] = max(a["s"], b["s"])
        r["overlap_pct"] = round(100 * ov / max(1, min(a["e"] - a["s"], b["e"] - b["s"])))
        ok = r["start_dist_m"] is not None and r["start_dist_m"] <= IDX_START_M and abs(r["dstart_s"]) <= IDX_START_S \
            and r["overlap_pct"] >= 50
        r["label"], r["why"] = ("unverified", (f"no FIT on one side: index start points {r['start_dist_m']} m / "
                                f"{r['dstart_s']} s apart, overlap {r['overlap_pct']} %")) if ok else ("not_together", "index only, no match")
        r["score"] = 0.5 if ok else 0
        r["joint_start_utc"] = max(a["s"], b["s"])
        if ok and sa:
            r["joint_pos"] = (round(sa[0], 5), round(sa[1], 5))
        return r
    A, B = track(a), track(b)
    t0, t1 = max(A["base"], B["base"]), min(A["base"] + A["T"], B["base"] + B["T"])
    r["dstart_s"] = round(B["base"] - A["base"])
    r["overlap_s"] = max(0, round(t1 - t0))
    r["overlap_start_utc"] = t0
    r["share_of"] = (round(100 * r["overlap_s"] / max(1, A["T"])), round(100 * r["overlap_s"] / max(1, B["T"])))
    r["overlap_pct"] = max(r["share_of"])
    sa, sb = first_fix(a, A), first_fix(b, B)
    r["start_dist_m"] = round(sg.hav(sa, sb)) if sa and sb else None
    r["products"] = (A["prod"], B["prod"])
    r["sample_s"] = (A["sample_s"], B["sample_s"])
    if r["overlap_s"] < MIN_OVERLAP_S:
        r["label"], r["why"], r["score"] = "not_together", "no time overlap", 0
        return r
    t = np.arange(math.ceil(t0), math.floor(t1) + 1, 1.0)
    o = sa or sb or (0, 0)
    both, d, w, mov, va, vb = with_mask(A, B, t, 0.0, o)
    r["both_gps_pct"] = round(100 * both.mean())
    if both.sum() >= 120:
        db = d[both]
        for R in (25, 50, 100):
            r[f"colo{R}"] = round(100 * float((db <= R).mean()))
        r["with_s"] = int((w & mov).sum())
        r["with_pct_gps"] = round(100 * float((w & mov).sum()) / max(1, (both & mov).sum()))
        r["with_pct"] = round(100 * float((w & mov).sum()) / max(1, mov.sum()))
        r["gps_cover_pct"] = round(100 * float((both & mov).sum()) / max(1, mov.sum()))
        segs = segments(w)
        r["with_segs_min"] = [(round(float(t[x] - t0) / 60, 1), round(float(t[y] - t0) / 60, 1)) for x, y in segs]
        sel = w & (va > 2) & (vb > 2) & (d <= 30)
        if sel.sum() > 120:
            lag, dmed = pos_lag(A, B, t, sel, o)
            r["tau_pos_s"], r["tau_pos_dmed_m"], r["tau_pos_n"] = lag, round(dmed, 1), int(sel.sum())
    else:
        r["with_pct"] = None
        r["gps_cover_pct"] = 0
    best = (None, -2)
    va5 = at(A, t, "v5")
    for lag in range(-20, 21):
        vb_ = at(B, t + lag, "v5")
        ok = np.isfinite(va5) & np.isfinite(vb_)
        if ok.sum() > 120 and np.std(vb_[ok]) > 0.05 and np.std(va5[ok]) > 0.05:
            c = float(np.corrcoef(va5[ok], vb_[ok])[0, 1])
            if c > best[1]:
                best = (lag, c)
    r["speed_lag_s"], r["speed_corr"] = best[0], round(best[1], 2)
    ra = [(x + A["base"], y + A["base"]) for x, y in A["reps"] if t0 <= x + A["base"] <= t1]
    rb = [(x + B["base"], y + B["base"]) for x, y in B["reps"] if t0 <= x + B["base"] <= t1]
    r["n_reps"] = (len(ra), len(rb))
    r["rep_src"] = (A["rep_src"], B["rep_src"])
    tau = r.get("tau_pos_s") or 0.0
    fa = follows(A, B, ra, tau, lambda tt: gps_at(A, tt) & gps_at(B, tt + tau), o) if ra else []
    fb = follows(B, A, rb, -tau, lambda tt: gps_at(B, tt) & gps_at(A, tt - tau), o) if rb else []
    if len(fa) >= SHARED_MIN or len(fb) >= SHARED_MIN:
        offs = [x[1] for x in fa] + [-x[1] for x in fb]   # B onset - A onset
        r["tau_onset_s"] = round(float(np.median(offs)), 1)
    r["follow"] = {"b_in_a": len(fa), "a_in_b": len(fb)}
    r["follow_detail"] = {"b_in_a": [x[:3] for x in fa], "a_in_b": [x[:3] for x in fb]}
    r["label"], r["why"], r["score"], r["shared_reps"] = classify(r, a, b, ra, rb, fa, fb)
    cand = []
    if fa:
        cand.append(ra[fa[0][0]][0])
    if fb:
        cand.append(rb[fb[0][0]][0])
    if r.get("with_segs_min"):
        cand.append(t0 + 60 * r["with_segs_min"][0][0])
    tj = min(cand) if cand else t0
    r["joint_start_utc"] = tj
    for S in (A, B):
        if gps_at(S, np.array([tj]))[0]:
            r["joint_pos"] = (round(float(at(S, np.array([tj]), "lat")[0]), 5), round(float(at(S, np.array([tj]), "lon")[0]), 5))
            break
    return r


def near_start(r):
    return r.get("start_dist_m") is not None and r["start_dist_m"] <= 300


def classify(r, a, b, ra, rb, fa, fb):
    """pair label (docs/together.md#rules): (label, why, score 0..1, shared reps)"""
    ta, tb = a["meta"].get("type"), b["meta"].get("type")
    wp = r.get("with_pct")
    wf = (wp or 0) / 100
    ws = r.get("with_s", 0)
    na, nb = len(ra), len(rb)
    shared = max(len(fa), len(fb))
    struct = na >= 2 or nb >= 2
    tog_note = ""
    if wp is not None and ws >= TOG_PART_S:
        tog_note = f"; with each other {wp} % ({ws // 60} min)"
    # 1. side by side nearly all the time: together whatever the detectors say
    if wp is not None and wf >= TOG_OVERRIDE:
        if struct and shared >= SHARED_MIN:
            return "same_workout", f"with each other {wp} % of the joint moving time, {shared} shared reps (reps {na}/{nb})", round(wf, 2), shared
        return "together", f"with each other {wp} % of the joint moving time", round(wf, 2), shared
    # 2. structured: shared reps decide
    if struct:
        if shared >= SHARED_MIN:
            # coverage: share of a side's rep time that was shared (the side that did less decides); rep time, not
            # count: detectors count warm-up blocks / short strides as reps
            covs = []
            for reps, fol in ((ra, fa), (rb, fb)):
                tot = sum(e - s_ for s_, e in reps)
                if len(reps) >= 2 and tot > 0:
                    covs.append(sum(reps[k][1] - reps[k][0] for k, *_ in fol) / tot)
            cov = max(covs) if covs else 0.0
            extra = f"{shared} shared reps (reps {na}/{nb}), onset offset {r.get('tau_onset_s')} s"
            if cov >= SHARED_FULL:
                return "same_workout", extra + tog_note, round(min(1, cov), 2), shared
            return "partly_together", extra + f", at most {round(100 * cov)} % of one side's rep time" + tog_note, round(cov * 0.8, 2), shared
        lab = "same_place_diff_workout" if (wp is None or (r.get("colo100") or 0) >= 20 or ws >= TOG_PART_S) else "not_together"
        if wp is None and not near_start(r):
            lab = "not_together"
        if wp is not None and 180 <= ws < TOG_PART_S:
            tog_note = f"; with each other {ws // 60} min (warm-up / cool-down?)"
        return lab, f"reps {na}/{nb}, {shared} started together" + tog_note, round(0.3 * min(1, ws / 1800), 2) if lab != "not_together" else 0, 0
    # 3. continuous
    if wp is None:
        sc = r.get("speed_corr") or 0
        if sc >= 0.6:
            return "unverified", f"no GPS on one side; speed profiles correlate {sc}", 0.4, 0
        return "not_together", f"no GPS on one side, speed corr {sc}", 0, 0
    race = ("race" in (ta or "")) and ("race" in (tb or ""))
    if race and abs(r["dstart_s"]) <= RACE_START_S and (r["start_dist_m"] or 1e9) <= RACE_START_M:
        return "same_race", f"both race?, starts {r['dstart_s']} s / {r['start_dist_m']} m apart; with each other {wp} %", 0.6, 0
    if wf >= TOG_FULL:
        return "together", f"with each other {wp} % of the joint moving time", round(wf, 2), 0
    if ws >= TOG_PART_S:
        return "partly_together", f"with each other {wp} % ({ws // 60} min)", round(wf, 2), 0
    if (r.get("colo100") or 0) >= 30 or near_start(r):
        return "same_place_diff_workout", f"same place & time, apart (with {wp} %, colo100 {r.get('colo100')} %)", 0.1, 0
    return "not_together", f"with {wp} %", 0, 0


# ------------------------------------------------------------------ build
def _meta_key(a):
    m = a["meta"]
    ind = m.get("indoor_bislett") or {}
    return json.dumps({"type": m.get("type"), "sport": m.get("sport"), "atype": m.get("atype"), "place": m.get("place"),
                       "ind": [ind.get("rep_laps"), ind.get("rep_m")] if ind else None}, sort_keys=True, default=str)


def _pair_key(a, b):
    def one(x):
        if x["fit"] is None:
            return ("idx", round(x["s"]), round(x["e"]), x["x"].get("startLatitude"), x["x"].get("startLongitude"))
        return ("fit", fkey(x["fit"]), _meta_key(x), round(x["s"]))
    return (PARAM_VERSION, one(a), one(b))


def candidates(P: dict):
    """(a, b) over every pair of people whose time windows overlap (with PRE_MARGIN_S)"""
    for p, q in itertools.combinations(sorted(P), 2):
        B = sorted(P[q], key=lambda x: x["s"])
        bs = [x["s"] for x in B]
        maxdur = max((x["e"] - x["s"] for x in B), default=0)
        for a in P[p]:
            j = bisect_left(bs, a["s"] - maxdur - PRE_MARGIN_S)
            while j < len(B) and bs[j] <= a["e"] + PRE_MARGIN_S:
                b = B[j]
                if min(a["e"], b["e"]) - max(a["s"], b["s"]) >= -PRE_MARGIN_S:
                    yield a, b
                j += 1


def watch_offsets(pairs) -> dict:
    """median trusted position offset per watch pair: {"p:prod|q:prod": {offset_s, n}}"""
    by = {}
    for r in pairs:
        if r.get("tau_pos_s") is None or not r.get("products"):
            continue
        if (r.get("tau_pos_n") or 0) >= OFFSET_MIN_N and (r.get("tau_pos_dmed_m") or 99) <= OFFSET_MAX_DMED:
            by.setdefault(_wkey(r), []).append(r["tau_pos_s"])
    return {k: {"offset_s": round(float(np.median(v)), 2), "n": len(v)} for k, v in sorted(by.items())}


def _wkey(r):
    pa, pb = r["products"]
    return f"{r['a'][0]}:{pa}|{r['b'][0]}:{pb}"


def pair_offset(r, W) -> tuple:
    """seconds b's file shows the same event later than a's: own trusted position lag, else the watch pair's median,
    else the median rep-onset difference (>= 3 shared reps), else 0"""
    if r.get("tau_pos_s") is not None and (r.get("tau_pos_n") or 0) >= OFFSET_MIN_N and (r.get("tau_pos_dmed_m") or 99) <= OFFSET_MAX_DMED:
        return r["tau_pos_s"], "position"
    if r.get("products") and _wkey(r) in W:
        return W[_wkey(r)]["offset_s"], "watch_pair"
    if r.get("tau_onset_s") is not None and (r.get("shared_reps") or 0) >= 3:
        return r["tau_onset_s"], "rep_onsets"
    return 0.0, "none"


def build(full=False, quiet=False) -> dict:
    T0 = time.time()
    cache = _Cache()
    if full:
        cache.d["pair"] = {}
    ps = people()
    P = {p: index(p, cache) for p in ps}
    cache.save()
    acts = {(x["person"], x["id"]): x for p in P for x in P[p]}
    pairs, n_new = [], 0
    for a, b in candidates(P):
        k = (a["person"], a["id"], b["person"], b["id"])
        key = _pair_key(a, b)
        hit = cache.d["pair"].get(k)
        if hit and hit["key"] == key:
            r = hit["r"]
        else:
            try:
                r = _jsonable(pair(a, b))
            except Exception as e:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                r = {"a": [a["person"], a["id"]], "b": [b["person"], b["id"]], "label": "error", "why": repr(e)[:200]}
            cache.d["pair"][k] = {"key": key, "r": r}
            cache.dirty = True
            n_new += 1
            if not quiet:
                print(f"  {dt.datetime.fromtimestamp(a['s'] + a['tz'], dt.UTC):%Y-%m-%d %H:%M} "
                      f"{a['person']}:{a['id']} {b['person']}:{b['id']} {r['label']:24} {r.get('why')}")
        pairs.append(r)
    cache.save()
    W = watch_offsets(pairs)
    for r in pairs:
        if r.get("label") != "error":
            r["offset_s"], r["offset_src"] = pair_offset(r, W)
    old = load(missing_ok=True)
    groups, aliases = build_groups(pairs, acts, old)
    doc = {"version": FORMAT_VERSION, "param_version": PARAM_VERSION, "built": dt.datetime.now().isoformat(timespec="seconds"),
           "people": ps, "offsets": W, "aliases": aliases, "groups": groups,
           "rejected": [{k: r.get(k) for k in ("a", "b", "label", "why")} for r in pairs
                        if r.get("label") not in SHOW + NEAR]}
    OUT.parent.mkdir(exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(_jsonable(doc), indent=1, ensure_ascii=False))
    tmp.replace(OUT)
    if not quiet:
        nv = sum(not g["hidden"] for g in groups)
        print(f"{len(pairs)} candidate pairs ({n_new} computed), {nv} groups + {len(groups) - nv} hidden near-misses "
              f"-> {OUT.relative_to(ROOT)} ({time.time() - T0:.1f} s)")
    return doc


def _slug(s):
    s = re.sub(r"[^A-Za-z0-9-]+", "-", str(s or "unknown")).strip("-")
    return s or "place"


def _place(nodes, es, acts):
    """group place: the best named place of any member within 600 m of the joint start
    (known > landmark > geocode > generic), else the first member's start place"""
    jp = next((e["joint_pos"] for e in sorted(es, key=lambda e: e.get("joint_start_utc") or 0) if e.get("joint_pos")), None)
    best = None
    if jp:
        for n in nodes:
            for pid, pl in acts[n]["places"].items():
                if pl.get("lat") is None:
                    continue
                dd = sg.hav(jp, (pl["lat"], pl["lon"]))
                if dd <= 600:
                    c = ({"known": 0, "landmark": 1, "geocode": 2}.get(pl.get("src"), 3), dd, n[0], pid)
                    best = min(best, c) if best else c
    if best is None:
        n = next((n for n in nodes if acts[n]["meta"].get("place")), None)
        if n is None:
            a = acts[nodes[0]]
            return {"name": a["x"].get("locationName"), "lat": a["x"].get("startLatitude"), "lon": a["x"].get("startLongitude"),
                    "person": None, "id": None, "src": "index"}
        best = (9, 0, n[0], acts[n]["meta"]["place"])
    _, _, person, pid = best
    pl = next(acts[n]["places"][pid] for n in nodes if n[0] == person and pid in acts[n]["places"])
    out = {"name": pl.get("name") or pid, "lat": pl.get("lat"), "lon": pl.get("lon"), "person": person, "id": pid,
           "src": pl.get("src")}
    return _latin_place(out)


def _is_latin(s):
    return all(not c.isalpha() or unicodedata.name(c, "").startswith("LATIN") for c in s)


def _latin_part(s):
    """the Latin-script words of a name ("Centre-Ville وسط المدينة" -> "Centre-Ville"), "" if none"""
    return " ".join(w for w in str(s or "").split() if _is_latin(w) and any(c.isalpha() for c in w)).strip(" ,")


_GEO: dict = {}


def _geocode_points(person):
    """data/<person>/cache/geocode.json points (memoised); [] when missing"""
    if person not in _GEO:
        f = DATA / person / "cache" / "geocode.json"
        try:
            x = json.loads(f.read_text()) if f.exists() else {}
        except ValueError:
            x = {}
        _GEO[person] = (x.get("points") if isinstance(x, dict) else x) or []
    return _GEO[person]


GEO_FALLBACK = ("suburb", "town", "city", "village", "municipality", "county", "state_district", "state", "region", "country")


def _latin_place(pl):
    """a non-Latin place name (geocoded abroad: "青柯坪") gets a Latin `name` from the same geocode answer (the first
    Latin address part: town / city / county / state ..., "Huayin City"); the original goes to `name_local`"""
    nm = pl.get("name") or ""
    if _is_latin(nm):
        return pl
    lat = _latin_part(nm)
    if not lat and pl.get("person"):
        pts = [q for q in _geocode_points(pl["person"]) if q.get("for") == pl.get("id")]
        if not pts and pl.get("lat") is not None:
            pts = sorted((q for q in _geocode_points(pl["person"]) if q.get("lat") is not None),
                         key=lambda q: sg.hav((pl["lat"], pl["lon"]), (q["lat"], q["lon"])))[:1]
        addr = ((pts[0].get("raw") or {}).get("address") or {}) if pts else {}
        lat = next((_latin_part(addr[k]) for k in GEO_FALLBACK if _latin_part(addr.get(k))), "")
    if not lat:
        return pl
    return {**pl, "name": lat, "name_local": nm}


def _family(a):
    return FAMILY.get(((a["x"].get("activityType") or {}).get("typeKey")) or "", "other")


GENERIC_TYPES = ("run_other", "other", "multi_sport", "run_nogps", None)   # untyped: says little about the sport


def _group_sport(nodes, acts):
    """group sport family: the majority over the members with a specific type (a typed hike beats an untyped
    "run" at 3.5 km/h), else over everyone; a tie goes to the earliest such member. -> (family, node)"""
    spec = [n for n in nodes if acts[n]["meta"].get("type") not in GENERIC_TYPES and _family(acts[n]) != "other"] or list(nodes)
    cnt = {}
    for n in spec:
        cnt[_family(acts[n])] = cnt.get(_family(acts[n]), 0) + 1
    top = max(cnt.values())
    n = next(n for n in spec if cnt[_family(acts[n])] == top)
    return _family(acts[n]), n


def _cont_node(nodes, acts, fam, default):
    """whose label names a continuous group: a member of the group sport that didn't log it as intervals (the
    joint part was steady; one of them may have done reps alone afterwards)"""
    ok = [n for n in nodes if _family(acts[n]) == fam and acts[n]["meta"].get("type") not in IV + GENERIC_TYPES]
    return min(ok, key=lambda n: (acts[n]["meta"].get("type") == "walking", ok.index(n)), default=default)   # "Hike" over "Walk"


def _kind(label, es, fam):
    if label == "same_race":
        return "race"
    if any((e.get("shared_reps") or 0) >= SHARED_MIN and e["label"] in ("same_workout", "partly_together") for e in es):
        return "intervals"
    return {"hike": "hike", "bike": "ride", "ski": "ski", "swim": "swim", "run": "continuous"}.get(fam, "other")


def _group_offsets(nodes, es):
    """seconds to subtract from each member's UTC to put it on the group clock (the first member = 0), along the
    pair edges (BFS); members without a FIT pair: 0"""
    off = {nodes[0]: 0.0}
    adj = {}
    for e in es:
        a, b = tuple(e["a"]), tuple(e["b"])
        tau = e.get("offset_s") or 0.0
        adj.setdefault(a, []).append((b, tau))
        adj.setdefault(b, []).append((a, -tau))
    todo = [nodes[0]]
    while todo:
        x = todo.pop()
        for y, tau in adj.get(x, []):
            if y not in off:
                off[y] = off[x] + tau
                todo.append(y)
    return {n: round(off.get(n, 0.0), 2) for n in nodes}


def _workout(a):
    m = a["meta"]
    return m.get("sig") or STYPE_LABEL.get(m.get("type")) or ((a["x"].get("activityType") or {}).get("typeKey") or "").replace("_", " ").capitalize() or None


def _group(nodes, es, others, acts, hidden):
    nodes = sorted(nodes, key=lambda n: acts[n]["s"])
    first = acts[nodes[0]]
    best = max(es, key=lambda e: (RANK.get(e["label"], 0), e.get("score") or 0))
    label = best["label"]
    fam, fam_node = _group_sport(nodes, acts)
    kind = _kind(label, es, fam)
    place = _place(nodes, es, acts)
    start = min(acts[n]["s"] for n in nodes)
    end = max(acts[n]["e"] for n in nodes)
    loc = dt.datetime.fromtimestamp(start + first["tz"], dt.UTC).replace(tzinfo=None)
    offs = _group_offsets(nodes, es)
    # anchor (intervals): the member whose reps were followed most
    fol = {}
    for e in es:
        fd = e.get("follow") or {}
        fol[tuple(e["a"])] = max(fol.get(tuple(e["a"]), 0), fd.get("b_in_a", 0))
        fol[tuple(e["b"])] = max(fol.get(tuple(e["b"]), 0), fd.get("a_in_b", 0))
    anchor = max(nodes, key=lambda n: (fol.get(n, 0), -acts[n]["s"])) if kind == "intervals" else nodes[0]
    parts = []
    for n in nodes + sorted(others, key=lambda n: acts[n]["s"]):
        a = acts[n]
        role = "other" if n in others else "full"
        mine = [e for e in es if n in (tuple(e["a"]), tuple(e["b"]))]
        span = None
        if n not in others:
            if any(e["label"] == "partly_together" for e in mine) and not all(e["label"] in ("same_workout", "together") for e in mine):
                role = "partial"
            if kind == "intervals" and n != anchor:
                e = next((e for e in mine if anchor in (tuple(e["a"]), tuple(e["b"]))), None)
                if e:
                    na = (e.get("n_reps") or (0, 0))[0 if tuple(e["a"]) == anchor else 1]
                    got = (e.get("follow") or {}).get("b_in_a" if tuple(e["a"]) == anchor else "a_in_b", 0)
                    if na and got < 0.9 * na:
                        role = "partial"
            pe = next((e for e in mine if e["label"] == "partly_together" and e.get("with_segs_min")), None)
            if pe:
                o = (pe["overlap_start_utc"] - start) / 60
                sgm = [(round(x + o, 1), round(y + o, 1)) for x, y in pe["with_segs_min"]]
                span = {"from_min": sgm[0][0], "to_min": sgm[-1][1], "segments": sgm,
                        "text": f"together {sgm[0][0]:.0f}–{sgm[-1][1]:.0f} min"}
        e_nr = next((e for e in mine if e.get("n_reps")), None)
        nreps = (e_nr["n_reps"][0 if tuple(e_nr["a"]) == n else 1]) if e_nr else None
        parts.append({
            "person": n[0], "activity_id": str(n[1]), "role": role, "anchor": n == anchor and kind == "intervals",
            "name": a["x"].get("activityName"), "type": a["meta"].get("type"), "sport": _family(a),
            "signature": a["meta"].get("sig"), "workout": _workout(a), "n_reps": nreps,
            "dist_km": _r((a["x"].get("distance") or 0) / 1000, 2), "dur_s": _r(a["e"] - a["s"], 0),
            "start_utc": _iso(a["s"]), "end_utc": _iso(a["e"]),
            "start_local": dt.datetime.fromtimestamp(a["s"] + a["tz"], dt.UTC).strftime("%Y-%m-%dT%H:%M:%S"),
            "offset_s": offs.get(n, 0.0) if n in offs else None, "clock_fixed": a["clock_fixed"],
            "has_fit": a["fit"] is not None, "product": a["prod"], "sample_s": _r(a["sample_s"], 1),
            "together": span,
        })
    ps = [p["person"] for p in parts if p["role"] != "other"]
    return {
        "gid": None, "date": f"{loc:%Y-%m-%d}", "start_local": f"{loc:%H:%M}", "start_local_iso": f"{loc:%Y-%m-%dT%H:%M:%S}",
        "start_utc": _iso(start), "end_utc": _iso(end),
        "joint_start_utc": _iso(min(e.get("joint_start_utc") or start for e in es)),
        "sport": fam, "sports": sorted({p["sport"] for p in parts}), "kind": kind,
        "label": label, "label_text": LABEL_TEXT.get(label, label), "score": best.get("score"), "hidden": hidden,
        "place": place, "workout": _workout(acts[anchor if kind in ("intervals", "race") else _cont_node(nodes, acts, fam, fam_node)]),
        "people": sorted(set(ps)), "n_people": len(set(ps)), "participants": parts,
        "pairs": [{k: e.get(k) for k in ("a", "b", "label", "score", "why", "with_pct", "with_s", "shared_reps", "n_reps",
                                         "follow", "dstart_s", "start_dist_m", "overlap_s", "tau_pos_s", "tau_pos_n",
                                         "tau_pos_dmed_m", "tau_onset_s", "offset_s", "offset_src")} for e in es],
        "no_track": any(not p["has_fit"] for p in parts) or label == "unverified",
        "_nodes": [list(n) for n in nodes + list(others)], "_t": start,
    }


def _components(edges):
    par = {}

    def f(x):
        while par.setdefault(x, x) != x:
            par[x] = par[par[x]]
            x = par[x]
        return x
    for e in edges:
        par[f(tuple(e["a"]))] = f(tuple(e["b"]))
    comp = {}
    for e in edges:
        comp.setdefault(f(tuple(e["a"])), []).append(e)
    return list(comp.values())


def build_groups(pairs, acts, old=None):
    """visible groups = connected components over SHOW edges (any number of people); a near-miss partner of a group
    member joins that group as role "other"; near-miss components without a visible member = hidden groups.
    Group ids are inherited from the old group sharing the most members (stable on rebuild)."""
    vis_edges = [p for p in pairs if p.get("label") in SHOW]
    out = []
    in_vis = {}
    for es in _components(vis_edges):
        nodes = sorted({tuple(e["a"]) for e in es} | {tuple(e["b"]) for e in es})
        k = len(out)
        for n in nodes:
            in_vis[n] = k
        out.append([nodes, es, set(), False])
    near_rest = []
    for p in pairs:
        if p.get("label") not in NEAR:
            continue
        a, b = tuple(p["a"]), tuple(p["b"])
        if (a in in_vis) != (b in in_vis):
            gi, other = (in_vis[a], b) if a in in_vis else (in_vis[b], a)
            if other not in in_vis:
                out[gi][2].add(other)
                out[gi][1].append(p)
        elif a not in in_vis:
            near_rest.append(p)
    for es in _components(near_rest):
        nodes = sorted({tuple(e["a"]) for e in es} | {tuple(e["b"]) for e in es})
        out.append([nodes, es, set(), True])
    groups = [_group([n for n in nodes if n not in oth], es, oth, acts, hid) for nodes, es, oth, hid in out]
    groups.sort(key=lambda g: g["_t"])
    # stable ids
    old_groups = (old or {}).get("groups", [])
    old_mem = {g["gid"]: {(p["person"], int(p["activity_id"])) for p in g["participants"]} for g in old_groups}
    used, taken = set(), set()
    for g in groups:
        mem = {tuple(n) for n in g["_nodes"]}
        cand = sorted(((len(mem & m), gid) for gid, m in old_mem.items() if gid not in used and mem & m), reverse=True)
        if cand:
            g["gid"] = cand[0][1]
        else:
            base = f"{g['start_local_iso'][:10]}_{g['start_local_iso'][11:13]}{g['start_local_iso'][14:16]}_{_slug(g['place'].get('id') or g['place'].get('name'))}"
            gid, k = base, 2
            while gid in taken or gid in old_mem:
                gid, k = f"{base}-{k}", k + 1
            g["gid"] = gid
        used.add(g["gid"])
        taken.add(g["gid"])
    aliases = dict((old or {}).get("aliases") or {})
    for gid, m in old_mem.items():
        if gid in used:
            aliases.pop(gid, None)
            continue
        best = max(groups, key=lambda g: len(m & {tuple(n) for n in g["_nodes"]}), default=None)
        if best and m & {tuple(n) for n in best["_nodes"]}:
            aliases[gid] = best["gid"]
    aliases = {k: (aliases.get(v, v)) for k, v in aliases.items() if k not in used}
    for g in groups:
        g.pop("_nodes")
        g.pop("_t")
    groups.sort(key=lambda g: g["start_utc"], reverse=True)
    return groups, aliases


# ------------------------------------------------------------------ reading together.json
_DOC = {"key": None, "doc": None}


def load(missing_ok=False) -> dict | None:
    """data/together.json (memoised by mtime)"""
    if not OUT.exists():
        if missing_ok:
            return None
        raise FileNotFoundError("no data/together.json: run `uv run scripts/together.py build`")
    k = OUT.stat().st_mtime_ns
    if _DOC["key"] != k:
        _DOC["doc"] = json.loads(OUT.read_text())
        _DOC["key"] = k
    return _DOC["doc"]


def find(gid, doc=None) -> dict | None:
    doc = doc or load()
    gid = (doc.get("aliases") or {}).get(gid, gid)
    return next((g for g in doc["groups"] if g["gid"] == gid), None)


# ------------------------------------------------------------------ session engine
def _fitness_scores(person) -> dict:
    p = DATA / person / "fitness.json"
    if not p.exists():
        return {}
    k = ("fit", person, p.stat().st_mtime_ns)
    if _FS.get(person, (None,))[0] != k:
        try:
            d = json.loads(p.read_text())
            _FS[person] = (k, {str(s["id"]): s for s in d.get("sessions", [])})
        except Exception:  # noqa: BLE001
            _FS[person] = (k, {})
    return _FS[person][1]


_FS: dict = {}


def _participants(g):
    """participants with FIT tracks, on the group clock: base_clock = UTC of grid second 0 - offset"""
    out = []
    seen = {}
    for p in g["participants"]:
        n = seen.get(p["person"], 0) + 1
        seen[p["person"]] = n
        key = p["person"] if n == 1 else f"{p['person']}#{n}"
        q = dict(p, key=key)
        if p["has_fit"]:
            st = cmp.store(p["person"])
            f = st.fit(int(p["activity_id"]))
            a = {"person": p["person"], "id": int(p["activity_id"]), "fit": f, "meta": st.act(int(p["activity_id"])) or {}}
            try:
                T = dict(track(a))
                T["base"] = T["base"] - (p.get("offset_s") or 0.0)
                q["T"] = T
            except Exception as e:  # noqa: BLE001
                q["T"] = None
                q["track_error"] = str(e)[:100]
        else:
            q["T"] = None
        out.append(q)
    return out


def _ptab(g, P, info):
    """participant rows for the payload"""
    rows = []
    for p in P:
        T = p.get("T")
        fs = _fitness_scores(p["person"]).get(p["activity_id"]) or {}
        rows.append({k: p.get(k) for k in ("key", "person", "activity_id", "role", "anchor", "name", "type", "sport",
                                           "signature", "workout", "dist_km", "dur_s", "start_utc", "start_local",
                                           "offset_s", "clock_fixed", "has_fit", "product", "together")}
                    | {"display": info.get(p["person"]), "hr_norm": hr_norm(p["person"]),
                       "sample_s": _r(T["sample_s"], 1) if T else None, "sample_p90_s": _r(T["sample_p90_s"], 1) if T else None,
                       "has_gps": bool(T and T["gps"].any()),
                       "shape": {"score": fs.get("score"), "discipline": fs.get("discipline"), "kind": fs.get("kind")} if fs else None})
    return rows


def _caveats(g, P):
    out = []
    for p in P:
        T, nm = p.get("T"), p["person"]
        if p["role"] == "other":
            continue
        if not p["has_fit"]:
            out.append({"kind": "no_track", "person": p["key"], "text": f"{nm}: no FIT file (no track, summary only)"})
            continue
        if T and T["sample_s"] >= 2:
            out.append({"kind": "sampling", "person": p["key"],
                        "text": f"{nm} records every {T['sample_s']:.0f} s (p90 {T['sample_p90_s']:.0f} s): rep times ±{max(1, T['sample_s'] / 2):.0f} s"})
        if p.get("offset_s"):
            out.append({"kind": "clock", "person": p["key"], "text": f"{nm}'s recording shifted by {-p['offset_s']:+.1f} s to the group clock"})
        if T and not T["gps"].any():
            out.append({"kind": "no_gps", "person": p["key"], "text": f"{nm}: no GPS positions (map / gaps unavailable)"})
        H = hr_norm(nm)
        if H.get("warn"):
            out.append({"kind": "hr", "person": p["key"], "text": f"{nm}: {H['warn']}"})
        if p.get("clock_fixed"):
            out.append({"kind": "clock_jump", "person": p["key"], "text": f"{nm}: the watch clock jumped in this file (start time taken from the FIT)"})
    return out


def _parse_sig(sig, n):
    """per-rep targets from a signature '3×1000 + 2×0:45' when its counts add up to n: [("dist", m) | ("time", s)]"""
    if not sig or "mixed" in sig:
        return None
    out = []
    for part in sig.split("+"):
        m = re.match(r"\s*(\d+)×([\d:]+)\s*$", part)
        if not m:
            return None
        c, v = int(m.group(1)), m.group(2)
        if ":" in v:
            mm, ss = v.split(":")
            out += [("time", int(mm) * 60 + int(ss))] * c
        else:
            out += [("dist", int(v))] * c
    return out if len(out) == n else None


def _nice(d):
    for x in NICE_M:
        if abs(d - x) <= 0.04 * x:
            return x
    return None


def _infer_target(cells):
    """set distance / time of a slot from the reps in it: the shortest full distance, rounded to a round number"""
    ds = [c["own_dist"] for c in cells if c.get("own_dist")]
    ts = [c["own_time"] for c in cells if c.get("own_time")]
    if not ds:
        return ("time", int(max(15, round(np.median(ts) / 15) * 15))) if ts else None
    dmax = max(ds)
    dref = min(d for d in ds if d >= 0.6 * dmax)
    nice = _nice(dref)
    if nice:
        return ("dist", nice)
    r100 = round(dref / 100) * 100
    if r100 >= 100 and abs(dref - r100) <= 0.05 * dref:
        return ("dist", r100)
    tref = float(np.median(ts))
    if len(ts) >= 2 and max(ts) - min(ts) <= 0.1 * tref and max(ds) - min(ds) > 0.1 * dref:
        return ("time", int(round(tref / 15) * 15))
    return ("dist", int(round(dref / 50) * 50) or 50)


def _t_at_dist(D, st, m):
    """first time (grid s, fractional) when distance since grid time st reaches m; None if never"""
    d0 = float(np.interp(st, np.arange(len(D)), D))
    goal = d0 + m
    j = int(np.searchsorted(D, goal, side="left"))
    if j >= len(D):
        return None
    if j == 0:
        return 0.0
    a, b = D[j - 1], D[j]
    t = (j - 1) + ((goal - a) / (b - a) if b > a else 1.0)
    return max(t, st)


def _rep_hr(G, a, b):
    h = G.get("hr")
    if h is None:
        return None, None, None
    w = h[max(0, math.floor(a)): min(G["T"], math.ceil(b)) + 1]
    w = w[np.isfinite(w)]
    if len(w) < 3:
        return None, None, None
    i = round(a)
    hs = h[i] if 0 <= i <= G["T"] and np.isfinite(h[i]) else None
    return _r(float(w.mean()), 0), _r(float(w.max()), 0), _r(hs, 0)


def _intervals(g, P):
    """cross-person intervals: slots by clock time, results for the set distance, standings, totals, breaks, close-ups"""
    act = [p for p in P if p["role"] != "other" and p.get("T") is not None]
    # ---- own reps per participant (compare engine main reps: strides left out), clock times
    for p in act:
        T = p["T"]
        p["reps"] = []
        S = T["S"]
        if S is None or not _rep_cond(S["meta"] or {}):
            continue
        try:
            reps_by = cmp._reps_slots(cmp.store(p["person"]), [S], "auto", 1)[0]
        except (ValueError, KeyError):
            continue
        for r in reps_by.get(S["id"], []):
            cs = T["base"] + r["start"]
            # only reps while someone else is recording
            if any(q is not p and q["T"]["base"] <= cs <= q["T"]["base"] + q["T"]["T"] for q in act):
                p["reps"].append({"start": float(r["start"]), "end": float(r["end"]), "time": float(r["time"]),
                                  "dist": float(r["dist"]) if r.get("dist") else None, "source": "detector",
                                  "clock": cs, "clock_end": T["base"] + r["end"]})
    if not any(p["reps"] for p in act):
        raise ValueError("no reps detected in any participant's file")
    anchor = max(act, key=lambda p: (bool(p.get("anchor")), len(p["reps"]), -p["T"]["sample_s"]))
    # ---- slots: the anchor's reps, then other participants' reps by clock time (unmatched -> extra slots)
    slots = [{"clock": r["clock"], "clock_end": r["clock_end"], "owner": anchor["key"], "anchor": True, "cells": {anchor["key"]: r}}
             for r in anchor["reps"]]
    for p in sorted((q for q in act if q is not anchor), key=lambda q: -len(q["reps"])):
        for r in p["reps"]:
            tol = max(MATCH_TOL_S, 2 * max(p["T"]["sample_s"], anchor["T"]["sample_s"]))
            best = None
            for s in slots:
                if p["key"] in s["cells"]:
                    continue
                d = abs(r["clock"] - s["clock"])
                ov = min(r["clock_end"], s["clock_end"]) - max(r["clock"], s["clock"])
                if d <= tol and ov > MATCH_OVERLAP * min(r["time"], s["clock_end"] - s["clock"]) and (best is None or d < best[0]):
                    best = (d, s)
            if best:
                best[1]["cells"][p["key"]] = r
            else:
                slots.append({"clock": r["clock"], "clock_end": r["clock_end"], "owner": p["key"], "anchor": False, "cells": {p["key"]: r}})
    slots.sort(key=lambda s: s["clock"])
    byk = {p["key"]: p for p in act}
    # ---- targets (set distance / time per slot): owner's signature, else inferred from the reps
    sigt = {}
    for p in act:
        S = p["T"]["S"]
        sigt[p["key"]] = None
        if S is not None and p["reps"]:
            try:
                allm = cmp._reps_slots(cmp.store(p["person"]), [S], "auto", 1)[0].get(S["id"], [])
            except (ValueError, KeyError):
                allm = []
            tg = _parse_sig((S["meta"] or {}).get("sig"), len(allm))
            if tg:
                sigt[p["key"]] = {round(float(r["start"]), 3): tg[i] for i, r in enumerate(allm)}
    for s in slots:
        tg = None
        for key in [s["owner"]] + [k_ for k_ in s["cells"] if k_ != s["owner"]]:
            m = sigt.get(key)
            if m:
                tg = m.get(round(s["cells"][key]["start"], 3))
                if tg:
                    if tg[0] == "time":   # signature times are rounded to 15 s: keep the owner's own duration unless close
                        ot = s["cells"][key]["time"]
                        if abs(ot / tg[1] - 1) > TIME_TOL:
                            tg = ("time", round(ot))
                    break
        s["target"] = tg or _infer_target([{"own_dist": r["dist"], "own_time": r["time"]} for r in s["cells"].values()])
    # ---- synthesized reps: a participant without a detected rep in a slot who followed its start (onset test);
    # not when own detected reps overlap the slot (they did something else there, shown as their own extra reps)
    for s in slots:
        own = byk[s["owner"]]
        for p in act:
            if p["key"] in s["cells"]:
                continue
            T = p["T"]
            if not (T["base"] + 30 <= s["clock"] <= T["base"] + T["T"] - 30):
                continue
            if any(r["clock"] < s["clock_end"] and r["clock_end"] > s["clock"] for r in p["reps"]):
                continue
            A_, B_ = own["T"], T
            fol = follows(A_, B_, [(s["clock"], s["clock_end"])], 0.0,
                          lambda tt, A_=A_, B_=B_: gps_at(A_, tt) & gps_at(B_, tt), _origin(A_, B_))
            if not fol:
                continue
            t_on = fol[0][3]
            s["cells"][p["key"]] = {"start": t_on - T["base"], "end": None, "time": None, "dist": None, "source": "onset",
                                    "clock": t_on, "clock_end": None}
    # ---- per participant GPS scale k = median own rep distance / set distance over full detector reps
    for p in act:
        ks = []
        for s in slots:
            r = s["cells"].get(p["key"])
            if r and r["source"] == "detector" and r["dist"] and s["target"] and s["target"][0] == "dist":
                q = r["dist"] / s["target"][1]
                if abs(q - 1) <= FULL_TOL:
                    ks.append(q)
        p["k"] = float(np.clip(np.median(ks), 0.95, 1.05)) if ks else 1.0
    # ---- cells: result for the set distance (time) / set time (metres)
    absorbed = set()   # (participant, slot index) of own extra reps merged into a split rep
    for si, s in enumerate(slots):
        kind, tv = s["target"] or ("time", None)
        s["res"] = {}
        ref_t = [r["time"] for key, r in s["cells"].items() if r["source"] == "detector" and r["dist"] and kind == "dist"
                 and tv and r["dist"] >= PARTIAL * tv]
        for key, r in list(s["cells"].items()):
            p = byk[key]
            T = p["T"]
            D = T["G"]["d"]
            gi = np.arange(len(D))
            no_dist = bool(T["S"] and T["S"].get("no_dist"))
            c = {"source": r["source"], "start_s": _r(r["start"], 1), "own_time_s": _r(r["time"], 1),
                 "own_dist_m": _r(r["dist"], 0), "clock_s": r["clock"], "partial": False, "result_s": None,
                 "time_s": None, "dist_m": None, "method": None, "scale": None, "rank": None, "gap_s": None, "tie": False}
            if not tv:
                c.update(partial=True, method="no_target")
            elif kind == "dist":
                te = None if no_dist else _t_at_dist(D, r["start"], tv * p["k"])
                ti = te - r["start"] if te is not None else None
                if r["source"] == "onset":
                    ok = ti is not None and (not ref_t or ti <= 1.3 * min(ref_t))
                    if ti is None:
                        del s["cells"][key]
                        continue
                    c.update(time_s=_r(ti, 1), own_time_s=_r(ti, 1), dist_m=tv, method="interpolated" if ok else "partial",
                             scale=1 / p["k"], partial=not ok)
                    r["end"], r["time"], r["clock_end"] = te, ti, T["base"] + te
                elif no_dist or not r["dist"]:
                    c.update(time_s=_r(r["time"], 1), dist_m=tv, method="no_distance")
                elif r["dist"] >= PARTIAL * tv:
                    if ti is not None and r["dist"] > (1 + TAIL_TOL) * tv * p["k"] and r["time"] - ti > max(1.0, T["sample_s"]):
                        # the detected rep runs on well past the set distance (tail): time at the set distance
                        c.update(time_s=_r(ti, 1), dist_m=tv, method="interpolated", scale=1 / p["k"])
                    else:
                        c.update(time_s=_r(r["time"], 1), dist_m=tv, method="detector", scale=tv / r["dist"])
                else:
                    # split rep (detector cut it at a slowdown): the stream from own start to the set distance, if plausible
                    ref = min([x for x in ref_t if x != r["time"]] or [r["time"] * tv / r["dist"]])
                    if ti is not None and ti <= 1.3 * ref:
                        c.update(time_s=_r(ti, 1), dist_m=tv, method="merged", scale=1 / p["k"])
                        for sj, s2 in enumerate(slots):
                            r2 = s2["cells"].get(key)
                            if sj != si and not s2["anchor"] and r2 and r2["source"] == "detector" and r["start"] < r2["start"] < te:
                                absorbed.add((key, sj))
                        r["end"], r["clock_end"] = te, T["base"] + te
                    else:
                        c.update(partial=True, method="partial", time_s=_r(r["time"], 1), dist_m=_r(r["dist"], 0))
                c["result_s"] = None if c["partial"] else c["time_s"]
            else:   # set time: metres from own start in the set time (GPS scale applied)
                if no_dist:
                    c.update(partial=True, method="no_distance", time_s=_r(r["time"], 1))
                elif r["source"] == "onset" or r["time"] >= (1 - TIME_TOL) * tv:
                    m = (np.interp(r["start"] + tv, gi, D) - np.interp(r["start"], gi, D)) / p["k"]
                    c.update(time_s=tv, dist_m=_r(m, 0), method="interpolated" if r["source"] == "onset" else "detector")
                    if r["source"] == "onset":
                        r["end"], r["time"], r["clock_end"] = r["start"] + tv, float(tv), T["base"] + r["start"] + tv
                else:
                    c.update(partial=True, method="partial", time_s=_r(r["time"], 1), dist_m=_r(r["dist"], 0))
            H = hr_norm(p["person"])
            h_mean, h_peak, h_start = _rep_hr(T["G"], r["start"], r["start"] + (c["time_s"] or r["time"] or 0))
            c.update(hr=h_mean, hr_peak=h_peak, hr_start=h_start, effort_pct=_effort(h_peak, H), effort_mode=H.get("mode"))
            s["res"][key] = c
    for key, sj in absorbed:
        slots[sj]["cells"].pop(key, None)
        slots[sj]["res"].pop(key, None)
    slots = [s for s in slots if s["res"]]
    # set time: rank by metres -> time-equivalent result (the time to cover the winner's metres at own pace)
    for s in slots:
        kind, tv = s["target"] or ("time", None)
        if kind == "time" and tv:
            ok = [c for c in s["res"].values() if not c["partial"] and c["dist_m"]]
            if ok:
                best = max(c["dist_m"] for c in ok)
                for c in ok:
                    c["result_s"] = _r(tv * best / c["dist_m"], 1)
    # implausible reps: > SUSPECT_FAST faster than the person's median for the same set distance (a lap pressed early,
    # a short detected rep): flagged, kept in totals, left out of best rep and fade
    by_t = {}
    for s in slots:
        if s["target"] and s["target"][0] == "dist":
            for key, c in s["res"].items():
                if c["result_s"] and not c["partial"]:
                    by_t.setdefault((key, s["target"]), []).append(c)
    for (key, tg_), cs in by_t.items():
        med = float(np.median([c["result_s"] for c in cs])) if len(cs) >= 3 else None
        for c in cs:
            if med and c["result_s"] < (1 - SUSPECT_FAST) * med:
                c["suspect"] = (f"{c['result_s']:.1f} s is {100 * (1 - c['result_s'] / med):.0f} % faster than "
                                f"{key.split('#')[0]}'s median {med:.1f} s for {tg_[1]} m: probably a short rep or a lap pressed early")
    # labels: anchor reps 1..n, extras "k+1" after anchor rep k
    k = j = 0
    for s in slots:
        if s["anchor"]:
            k, j = k + 1, 0
            s["label"] = str(k)
        else:
            j += 1
            s["label"] = f"{k}+{j}"
    # ---- standings
    samp = {p["key"]: p["T"]["sample_s"] for p in act}
    standings = []
    for i, s in enumerate(slots):
        res = s["res"]
        ranked = sorted(((c["result_s"], key) for key, c in res.items() if c["result_s"] is not None and byk[key]["role"] != "other"))
        order = []
        if len(ranked) >= 2:   # a rep only one person ran isn't a race
            win = ranked[0][0]
            rank, gbest, gkey = 1, ranked[0][0], ranked[0][1]
            for pos, (v, key) in enumerate(ranked):
                tie_s = max(TIE_MIN_S, 0.5 * max(samp[key], samp[gkey]))
                if v - gbest > tie_s:
                    rank, gbest, gkey = pos + 1, v, key
                    tie_s = max(TIE_MIN_S, 0.5 * max(samp[key], samp[gkey]))
                tgt = s["target"][1] if s["target"][0] == "dist" else (res[key]["dist_m"] or 0)
                order.append({"person": key, "result_s": v, "rank": rank, "gap_s": _r(v - win, 1),
                              "gap_m": _r((v - win) / v * tgt, 0) if v else None, "tie_s": _r(tie_s, 1)})
            for o in order:
                o["tie"] = sum(1 for x in order if x["rank"] == o["rank"]) > 1
            for o in order:
                res[o["person"]].update(rank=o["rank"], gap_s=o["gap_s"], tie=o["tie"], tie_s=o["tie_s"])
        s["order"] = order
        standings.append({"slot": i, "label": s["label"], "order": order,
                          "partial": [key for key, c in res.items() if c["partial"]],
                          "missing": [p["key"] for p in act if p["key"] not in res]})
    keys = [p["key"] for p in act]
    shared = [i for i, s in enumerate(slots) if all(s["res"].get(k_, {}).get("result_s") is not None for k_ in keys)]
    # ---- leaderboard
    lb = []
    n_anchor = sum(s["anchor"] for s in slots)
    for p in act:
        key = p["key"]
        mine = [(i, s["res"][key]) for i, s in enumerate(slots) if key in s["res"]]
        done = sum(1 for i, c in mine if slots[i]["anchor"])
        rk = [(i, c) for i, c in mine if c["result_s"] is not None and slots[i]["order"]]   # raced reps
        tot = sum(slots[i]["res"][key]["result_s"] for i in shared) if shared else None
        wins = sum(1 for i in shared if slots[i]["res"][key].get("rank") == 1 and not slots[i]["res"][key].get("tie"))
        ties = sum(1 for i in shared if slots[i]["res"][key].get("rank") == 1 and slots[i]["res"][key].get("tie"))
        rk_ok = [(i, c) for i, c in rk if not c.get("suspect")]
        best = min(rk_ok, key=lambda x: x[1]["result_s"] / (slots[x[0]]["target"][1] if slots[x[0]]["target"][0] == "dist" else 1), default=None) if rk_ok else None
        # fade: last 3 vs first 3 ranked reps (suspect reps left out), as pace (s/km) for distance reps
        pace = [(c["result_s"] or 0) * 1000 / slots[i]["target"][1] for i, c in rk_ok if slots[i]["target"][0] == "dist"]
        fade = _r(np.mean(pace[-3:]) - np.mean(pace[:3]), 1) if len(pace) >= 6 else None
        same_t = len({slots[i]["target"] for i, _ in rk_ok}) == 1
        fade_s = _r(np.mean([c["result_s"] for _, c in rk_ok[-3:]]) - np.mean([c["result_s"] for _, c in rk_ok[:3]]), 1) if len(rk_ok) >= 6 and same_t else None
        # every full rep the person ran (also the ones only they ran): count, total, average, distance-weighted pace
        allr = [(i, c) for i, c in mine if c["result_s"] is not None and not c["partial"]]
        all_t = {slots[i]["target"] for i, _ in allr}

        def _pace(cells):
            d = [(c["result_s"], slots[i]["target"][1]) for i, c in cells if slots[i]["target"] and slots[i]["target"][0] == "dist"]
            return _r(1000 * sum(x for x, _ in d) / sum(m for _, m in d), 1) if d and len(d) == len(cells) else None
        eff = [c["effort_pct"] for _, c in rk if c.get("effort_pct") is not None]
        lb.append({"person": key, "reps_done": done, "extra_reps": len(mine) - done, "reps_ranked": len(rk), "of": n_anchor,
                   "n_slots": len(slots),
                   "total_s": _r(tot, 1), "avg_s": _r(tot / len(shared), 1) if shared else None,
                   "all_n": len(allr), "all_total_s": _r(sum(c["result_s"] for _, c in allr), 1) if allr else None,
                   "all_avg_s": _r(np.mean([c["result_s"] for _, c in allr]), 1) if allr and len(all_t) == 1 else None,
                   "all_pace_s_km": _pace(allr), "pace_s_km": _pace([(i, slots[i]["res"][key]) for i in shared]) if shared else None,
                   "suspect": [slots[i]["label"] for i, c in mine if c.get("suspect")],
                   "rep_wins": wins, "rep_ties": ties,
                   "best": {"slot": best[0], "label": slots[best[0]]["label"], "result_s": best[1]["result_s"]} if best else None,
                   "fade_s_km": fade, "fade_s": fade_s, "avg_effort_pct": _r(np.mean(eff), 0) if eff else None,
                   "effort_mode": hr_norm(p["person"]).get("mode")})
    lead = min((x["total_s"] for x in lb if x["total_s"] is not None), default=None)
    for x in lb:
        x["gap_s"] = _r(x["total_s"] - lead, 1) if x["total_s"] is not None and lead is not None else None
    lb.sort(key=lambda x: (x["total_s"] is None, x["total_s"] or 0, -x["reps_ranked"]))
    for n, x in enumerate(lb):
        x["rank"] = n + 1 if x["total_s"] is not None else None
    # ---- cumulative total / gap to the leader across the raced reps (>= 2 results); a person missing one drops out
    cslots = [i for i, s in enumerate(slots) if s["order"]]
    cum = {k_: [] for k_ in keys}
    tot = {k_: 0.0 for k_ in keys}
    alive = {k_: True for k_ in keys}
    for i in cslots:
        for k_ in keys:
            c = slots[i]["res"].get(k_)
            if alive[k_] and c and c["result_s"] is not None:
                tot[k_] += c["result_s"]
            else:
                alive[k_] = False
        lead_ = min((tot[k_] for k_ in keys if alive[k_]), default=None)
        for k_ in keys:
            cum[k_].append({"total_s": _r(tot[k_], 1), "gap_s": _r(tot[k_] - lead_, 1)} if alive[k_] else None)
    # ---- breaks (between consecutive reps of each participant, own or synthesized), compare's _break_stats
    breaks = {}
    for p in act:
        key = p["key"]
        S = p["T"]["S"]
        H = hr_norm(p["person"])
        rows = []
        seq = [(i, s["cells"][key]) for i, s in enumerate(slots) if key in s["cells"] and s["cells"][key].get("end") is not None]
        for (i, r), (j2, r2) in itertools.pairwise(seq):
            if S is None or r2["start"] <= r["end"]:
                continue
            b = cmp._break_stats(S, r["end"], r2["start"], r2["start"])
            rng = (H["max"] - (H["rest"] or 0)) if H.get("max") else None
            b.update(after_slot=i, after_label=slots[i]["label"], next_slot=j2, next_label=slots[j2]["label"],
                     drop_30_pct=_r(100 * b["drop_30"] / rng, 0) if b.get("drop_30") is not None and rng else None,
                     hr_next_start_pct=_effort(b.get("hr_next_start"), H))
            rows.append(b)
        breaks[key] = rows
    faster = []
    for i in range(len(slots)):
        cand = [(b["drop_30_pct"], k_) for k_ in keys for b in breaks[k_] if b["after_slot"] == i and b.get("drop_30_pct") is not None]
        if len(cand) >= 2:
            faster.append({"after_slot": i, "after_label": slots[i]["label"], "person": max(cand)[1]})
    # ---- rep close-ups
    closeups = [_closeup(s, byk, keys) for s in slots]
    # ---- output
    sl = []
    for i, s in enumerate(slots):
        kind, tv = s["target"] or (None, None)
        sl.append({"slot": i, "label": s["label"], "anchor": s["anchor"], "owner": s["owner"],
                   "target": {"kind": kind, "value": tv, "text": (f"{tv} m" if kind == "dist" else cmp_mmss(tv)) if tv else None},
                   "start_s": _r(s["clock"] - _t0(P), 1), "n_people": len(s["res"]), "shared": i in shared})
    t0 = _t0(P)
    reps = {k_: [_cell_out(slots[i]["res"].get(k_), t0) for i in range(len(slots))] for k_ in keys}
    return {"anchor": anchor["key"], "slots": sl, "reps": reps, "standings": standings, "shared_slots": shared,
            "leaderboard": lb, "cumulative": {"slots": cslots, "labels": [slots[i]["label"] for i in cslots], "people": cum},
            "breaks": breaks, "recovered_faster": faster, "rep_closeups": closeups,
            "gps_scale": {p["key"]: _r(p["k"], 3) for p in act}}


def cmp_mmss(s):
    return sg.mmss(s) if s is not None else None


def _cell_out(c, t0):
    if c is None:
        return None
    out = {k: v for k, v in c.items() if k != "clock_s"}
    out["clock_start_s"] = _r(c["clock_s"] - t0, 1)   # rep start on the group clock (s since t0_utc)
    out["scale"] = _r(c["scale"], 4)
    return out


def _origin(A, B):
    for S in (A, B):
        if S["gps"].any():
            j = int(np.argmax(S["gps"]))
            return (float(S["G"]["lat"][j]), float(S["G"]["lon"][j]))
    return (0.0, 0.0)


def _t0(P):
    return min(p["T"]["base"] for p in P if p.get("T") is not None)


def _closeup(s, byk, keys):
    """metres along the track behind the rep's leader vs seconds into the rep: own-start and clock alignment.
    d_m = own distance since own rep start x scale (to the set distance), capped at the set distance; null after own
    finish; behind_m = leader's d_m - own d_m, null after the leader's finish (no more data for the leader)."""
    if not s["order"] or not s["target"] or s["target"][0] != "dist":
        return {"slot": s["label"], "available": False}
    tv = s["target"][1]
    lead = s["order"][0]["person"]
    W = math.ceil(max((c["time_s"] or 0) for c in s["res"].values())) + 2
    t_clock0 = min(c["clock_s"] for c in s["res"].values())
    dstart = max(c["clock_s"] for c in s["res"].values()) - t_clock0
    out = {"slot": s["label"], "available": True, "leader": lead, "target_m": tv}
    for align, ext in (("own", 0), ("clock", math.ceil(dstart))):
        x = np.arange(0, W + ext + 1, 1.0)
        dm = {}
        fin = {}
        for key, c in s["res"].items():
            r = s["cells"][key]
            p = byk[key]
            G = p["T"]["G"]
            if c["time_s"] is None or (p["T"]["S"] or {}).get("no_dist"):
                continue
            sc = c["scale"] or 1.0
            off = 0.0 if align == "own" else c["clock_s"] - t_clock0
            tt = r["start"] + x - off
            d = (np.interp(tt, np.arange(G["T"] + 1), G["d"]) - np.interp(r["start"], np.arange(G["T"] + 1), G["d"])) * sc
            d = np.clip(d, 0, tv)
            d[tt < r["start"]] = 0.0
            fin[key] = off + c["time_s"]
            d[x > fin[key] + 1] = np.nan
            dm[key] = d
        if lead not in dm:
            out[align] = None
            continue
        ppl = {}
        for key, d in dm.items():
            beh = dm[lead] - d
            beh[x > fin[lead]] = np.nan
            ppl[key] = {"d_m": cmp._lst(d, 1), "behind_m": cmp._lst(beh, 1),
                        "start_delta_s": _r(s["res"][key]["clock_s"] - t_clock0, 1)}
        out[align] = {"x0": 0, "n": len(x), "people": ppl}
    return out


def _route_progress(g, P):
    """continuous: the joint window [a, b] (absolute s), the reference polyline and each person's progress along it
    (clock s, metres along the reference (running max), on-path mask), or {available: False, why} when there's none"""
    act = [p for p in P if p["role"] != "other" and p.get("T") is not None and p["T"]["gps"].any()]
    if len(act) < 2:
        return None, {"available": False, "why": "fewer than 2 participants with GPS"}
    a = max(p["T"]["base"] for p in act)
    b = min(p["T"]["base"] + p["T"]["T"] for p in act)
    spans = [p["together"] for p in act if p.get("together")]
    if g["label"] == "partly_together" and spans:   # only the stretch run together
        gs = _utc(g["start_utc"].replace("Z", ""))
        a = max(a, gs + 60 * min(x["from_min"] for x in spans))
        b = min(b, gs + 60 * max(x["to_min"] for x in spans))
    if b - a < 120:
        return None, {"available": False, "why": "no time overlap"}
    race = g["label"] == "same_race"
    a_ov, b_ov = a, b
    if race:   # a race: each person's whole file (the faster one's file ends before the others finish)
        a = min(p["T"]["base"] for p in act)
        b = max(p["T"]["base"] + p["T"]["T"] for p in act)
    ref = act[0]
    Gr = ref["T"]["G"]
    i0, i1 = int(max(0, a - ref["T"]["base"])), int(min(Gr["T"], b - ref["T"]["base"]))
    Gw = {"gps": ref["T"]["gps"][i0:i1 + 1], "lat": Gr["lat"][i0:i1 + 1], "lon": Gr["lon"][i0:i1 + 1]}
    if "alt" in Gr:
        Gw["alt"] = Gr["alt"][i0:i1 + 1]
    if Gw["gps"].sum() < 30:
        return None, {"available": False, "why": "reference has too little GPS in the overlap"}
    rp = sg.ref_polyline(Gw)
    prog = {}
    for p in act:
        G = dict(p["T"]["G"])
        G["gps"] = p["T"]["gps"]
        pr, on, direc = sg.project(G, rp, False)
        tp = pr if direc == "fwd" else rp["L"] - pr
        tc = p["T"]["base"] + np.arange(G["T"] + 1)
        w = (tc >= a) & (tc <= b)
        prog[p["key"]] = (tc[w], np.maximum.accumulate(tp[w]), on[w])
    return (act, a, b, a_ov, b_ov, race, ref, Gw, rp, prog), None


def _continuous(g, P):
    """continuous runs / hikes / races: gap over distance (projected onto the reference's path), distance apart over
    time, who led where, km splits; race finish order"""
    R, why = _route_progress(g, P)
    if R is None:
        return why
    act, a, b, a_ov, b_ov, race, ref, Gw, rp, prog = R
    t0 = _t0(P)
    lo = max(v[1][0] for v in prog.values())
    hi = min(v[1][-1] for v in prog.values())
    keys = [p["key"] for p in act]
    # moving clock: seconds in [a, b] in which anyone in the group moves. A stop everyone makes together (a summit
    # break, waiting at a crossing) doesn't count, so passing a point before / after a joint break isn't a 40 min
    # "gap"; one person stopping alone still falls behind. Races: plain clock time.
    thr = MOVE_HIKE_MS if g.get("sport") == "hike" or g["kind"] == "hike" else MOVE_MS
    tt_all = np.arange(math.floor(a), math.ceil(b) + 1, 1.0)
    moving = np.zeros(len(tt_all), bool)
    for p in act:
        v = at(p["T"], tt_all, "vs")
        moving |= np.nan_to_num(v, nan=0.0) > thr
    mclock = np.cumsum(moving).astype(float)
    if race:
        mclock = tt_all - tt_all[0]

    def mc(t):
        return np.interp(t, tt_all, mclock)
    out = {"available": True, "reference": ref["key"], "window_s": [_r(a_ov - t0, 0), _r(b_ov - t0, 0)],
           "ref_length_m": _r(rp["L"], 0), "clock": "elapsed" if race else "moving",
           "stopped_s": None if race else _r(float(len(moving) - moving.sum()), 0)}
    if hi - lo >= 500:
        xs = np.arange(math.ceil(lo / CONT_STEP_M) * CONT_STEP_M, hi, CONT_STEP_M)
        tx = {}
        for k_ in keys:
            tc, pr, _ = prog[k_]
            j = np.searchsorted(pr, xs, side="left")
            j = np.clip(j, 0, len(tc) - 1)
            tx[k_] = tc[j].astype(float)
        mx_ = {k_: mc(tx[k_]) for k_ in keys}
        lead = np.min(np.vstack([mx_[k_] for k_ in keys]), axis=0)
        gap = {k_: mx_[k_] - lead for k_ in keys}
        # projection artefacts: a gap that jumps by > 60 s within 100 m and comes back (one track snapped to a
        # later part of the path) -> null there; the count is reported
        flagged = 0
        for k_ in keys:
            v = gap[k_]
            med = np.array([np.median(v[max(0, n - 4):n + 5]) for n in range(len(v))])
            bad_ = np.abs(v - med) > GAP_SPIKE_S
            flagged += int(bad_.sum())
            gap[k_] = np.where(bad_, np.nan, v)
        if not race:
            # a time gap the positions can't support: when the front passed the point, this person was only d m
            # away, so being > (d + 50 m) / slowest plausible speed behind is the projection (switchbacks, a track
            # snapped to a parallel path), not a gap
            byk = {p["key"]: p for p in act}
            fr = np.argmin(np.vstack([mx_[k_] for k_ in keys]), axis=0)
            tf = np.array([tx[keys[f]][n] for n, f in enumerate(fr)])
            o = (float(np.nanmedian(Gw["lat"])), float(np.nanmedian(Gw["lon"])))
            vmin = GAP_VMIN_HIKE if thr == MOVE_HIKE_MS else GAP_VMIN
            fx = np.full(len(tf), np.nan)
            fy = np.full(len(tf), np.nan)
            for f in set(fr.tolist()):
                sel_ = fr == f
                x_, y_ = xy(at(byk[keys[f]]["T"], tf[sel_], "lat"), at(byk[keys[f]]["T"], tf[sel_], "lon"), o)
                fx[sel_], fy[sel_] = x_, y_
            for k_ in keys:
                kx, ky = xy(at(byk[k_]["T"], tf, "lat"), at(byk[k_]["T"], tf, "lon"), o)
                d = np.hypot(kx - fx, ky - fy)
                bad_ = np.isfinite(d) & np.isfinite(gap[k_]) & (gap[k_] > (d + 50) / vmin)
                flagged += int(bad_.sum())
                gap[k_] = np.where(bad_, np.nan, gap[k_])
        out["gap_over_distance"] = {"x_m": cmp._lst(xs - xs[0], 0), "x0_route_m": _r(xs[0], 0),
                                    "people": {k_: cmp._lst(gap[k_], 1) for k_ in keys}, "flagged": flagged}
        raw_tx, tx = tx, mx_     # who led / finish on the moving clock
        # who led where (per 100 m; within 2 s = side by side, null)
        led, cur = [], None
        for n, x in enumerate(xs):
            if n % 2:
                continue
            vals = sorted((tx[k_][n], k_) for k_ in keys)
            who = vals[0][1] if vals[1][0] - vals[0][0] > 2 else None
            if cur and cur["person"] == who:
                cur["to_m"] = _r(x - xs[0] + 100, 0)
            else:
                cur = {"from_m": _r(x - xs[0], 0), "to_m": _r(x - xs[0] + 100, 0), "person": who}
                led.append(cur)
        out["led"] = led
        # km splits: races on each person's own distance from their start (the whole race, also past the shared
        # stretch), otherwise along the common axis on the moving clock
        splits = []
        if race:
            own = {}
            for p in act:
                G = p["T"]["G"]
                own[p["key"]] = (p["T"]["base"] + np.arange(G["T"] + 1), np.maximum.accumulate(np.nan_to_num(G["d"], nan=0.0)))
            nkm = int(min(v[1][-1] for v in own.values()) // 1000)
            ks = None
        else:
            ks = np.arange(xs[0], hi + 1, 1000.0)
            nkm = len(ks) - 1
        for n in range(1, nkm + 1):
            row = {"km": n, "people": {}}
            for k_ in keys:
                if race:
                    tc, dd_ = own[k_]
                    ja, jb = np.searchsorted(dd_, [1000.0 * (n - 1), 1000.0 * n], side="left")
                    if jb < len(tc):
                        row["people"][k_] = {"time_s": _r(tc[jb] - tc[ja], 0)}
                    continue
                tc, pr, _ = prog[k_]
                ja, jb = np.searchsorted(pr, [ks[n - 1], ks[n]], side="left")
                if jb < len(tc):
                    row["people"][k_] = {"time_s": _r(float(mc(tc[jb]) - mc(tc[ja])), 0)}
            ranked = sorted((v["time_s"], k_) for k_, v in row["people"].items())
            for rk, (v, k_) in enumerate(ranked):
                row["people"][k_]["rank"] = rk + 1
                row["people"][k_]["gap_s"] = _r(v - ranked[0][0], 0)
            splits.append(row)
        out["km_splits"] = splits
        fin = sorted((tx[k_][-1], k_) for k_ in keys)
        out["finish"] = [{"person": k_, "rank": n + 1, "time_s": _r(t - tx[k_][0], 0), "gap_s": _r(t - fin[0][0], 0),
                          "clock_s": _r(raw_tx[k_][-1] - t0, 0)} for n, (t, k_) in enumerate(fin)]
        # where the group split up: the first point after which someone stays > 10 s behind the front for good
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            gmax = np.nanmax(np.vstack([gap[k_] for k_ in keys]), axis=0)
        apart_from = np.flatnonzero(gmax <= 10)
        sp = int(apart_from[-1]) + 1 if len(apart_from) else 0
        out["split_at_m"] = _r(float(xs[sp] - xs[0]), 0) if sp < len(xs) - 2 else None
        out["common_m"] = _r(xs[-1] - xs[0], 0)
    if g["label"] == "same_race":   # race result: each person's own recorded race (elapsed time of the file)
        rr = sorted((p["dur_s"], p["key"], p["dist_km"]) for p in act if p.get("dur_s"))
        out["race"] = [{"person": k_, "rank": n + 1, "time_s": t, "dist_km": d, "gap_s": t - rr[0][0]}
                       for n, (t, k_, d) in enumerate(rr)]
    # distance apart over clock time, per pair (with the together mask)
    tt = np.arange(math.ceil(a), math.floor(b) + 1, 1.0)
    apart = []
    for p, q in itertools.combinations(act, 2):
        both, d, w, mov, _, _ = with_mask(p["T"], q["T"], tt, 0.0, None)
        sel = slice(None, None, APART_STEP_S)
        dd = d.copy()
        apart.append({"a": p["key"], "b": q["key"], "t_s": cmp._lst(tt[sel] - t0, 0), "apart_m": cmp._lst(dd[sel], 0),
                      "with": [bool(x) for x in w[sel]],
                      "with_pct": _r(100 * float((w & mov).sum()) / max(1, mov.sum()), 0),
                      "within_20m_pct": _r(100 * float((both & (d <= 20)).sum()) / max(1, both.sum()), 0),
                      "median_apart_m": _r(float(np.nanmedian(d[w])) if w.any() else None, 0)})
    out["apart"] = apart
    return out


def _fingerprint(g, P):
    h = hashlib.sha1()
    h.update(json.dumps(g, sort_keys=True, default=str).encode())
    for p in P:
        h.update(p["person"].encode())
        if p["has_fit"]:
            st = cmp.store(p["person"])
            f = st.fit(int(p["activity_id"]))
            if f:
                h.update(repr(fkey(f)).encode())
            h.update(json.dumps(st.act(int(p["activity_id"])) or {}, sort_keys=True, default=str).encode())
        h.update(json.dumps(hr_norm(p["person"]), sort_keys=True).encode())
        fp = DATA / p["person"] / "fitness.json"
        h.update(repr(fp.stat().st_mtime_ns if fp.exists() else 0).encode())
    pj = PEOPLE_JSON.stat().st_mtime_ns if PEOPLE_JSON.exists() else 0
    h.update(repr((SESSION_VERSION, cmp.CACHE_VERSION, pj, _CODE_SIG)).encode())
    return h.hexdigest()


# engine code as loaded by this process (not the file's current mtime: a long-running server with older code in
# memory must not store its payloads under the new code's key in the shared sessions.pkl)
_CODE_SIG = hashlib.sha1(Path(__file__).read_bytes() + Path(cmp.__file__).read_bytes()).hexdigest()[:16]
_SC: dict = {"d": None}


def _sess_cache():
    if _SC["d"] is None:
        d = {}
        if SESS_CACHE.exists():
            try:
                d = pickle.loads(SESS_CACHE.read_bytes())
            except Exception:  # noqa: BLE001
                d = {}
        _SC["d"] = d if d.get("_v") == SESSION_VERSION else {"_v": SESSION_VERSION}
    return _SC["d"]


def _sess_save():
    SESS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SESS_CACHE.with_suffix(".tmp")
    tmp.write_bytes(pickle.dumps(_SC["d"]))
    tmp.replace(SESS_CACHE)


def session(gid, doc=None, use_cache=True) -> dict:
    """the session payload of a group (docs/together.md#session); cached in data/_together/sessions.pkl by a
    fingerprint of the group record, the participants' FIT files / groups.json entries and HR settings"""
    doc = doc or load()
    g = find(gid, doc)
    if g is None:
        raise KeyError(f"no group {gid}")
    T0 = time.time()
    P = _participants(g)
    fp = _fingerprint(g, P)
    C = _sess_cache()
    hit = C.get(g["gid"])
    if use_cache and hit and hit["key"] == fp:
        return hit["s"]
    info = people_info()
    out = {"gid": g["gid"], "group": g, "kind": g["kind"], "participants": _ptab(g, P, info),
           "people": {p: info[p] for p in g["people"] if p in info} | {p["person"]: info.get(p["person"]) for p in g["participants"]},
           "caveats": _caveats(g, P), "t0_utc": _iso(_t0(P)) if any(p.get("T") for p in P) else None,
           "intervals": None, "continuous": None}
    if any(p.get("T") for p in P):
        if g["kind"] == "intervals":
            try:
                out["intervals"] = _intervals(g, P)
            except ValueError as e:
                out["intervals"] = None
                out["caveats"].append({"kind": "engine", "person": None, "text": str(e)})
        if g["kind"] != "intervals" or g["label"] in ("together",) or out["intervals"] is None:
            out["continuous"] = _continuous(g, P)
        elif g["kind"] == "intervals":
            c = _continuous(g, P)
            out["continuous"] = {k: c.get(k) for k in ("available", "why", "reference", "window_s", "apart")}
    out["headline"] = headline(out)
    out["compute_s"] = round(time.time() - T0, 2)
    out = _jsonable(out)
    C[g["gid"]] = {"key": fp, "s": out}
    _sess_save()
    return out


def headline(s) -> dict:
    """one-line result for the list: intervals: winner, rep wins, mean gap per shared rep, closest rep;
    race: finish order; together: share of time together"""
    names = {k: (v or {}).get("name", k) for k, v in (s.get("people") or {}).items()}

    def nm(key):
        return names.get(key.split("#")[0], key)
    iv = s.get("intervals")
    if iv and iv.get("leaderboard") and iv["shared_slots"]:
        lb = [x for x in iv["leaderboard"] if x["total_s"] is not None]
        w = lb[0]
        wins = {x["person"]: x["rep_wins"] for x in iv["leaderboard"]}
        ns = len(iv["shared_slots"])
        gaps = [st["order"][-1]["gap_s"] for i, st in enumerate(iv["standings"]) if i in iv["shared_slots"] and st["order"]]
        closest = min(((st["order"][1]["gap_s"], st["label"]) for i, st in enumerate(iv["standings"])
                       if i in iv["shared_slots"] and len(st["order"]) >= 2), default=None)
        per = (lb[-1]["gap_s"] or 0) / ns if len(lb) >= 2 else None
        wtxt = "–".join(str(wins[x["person"]]) for x in lb)
        nt = sum(1 for i in iv["shared_slots"] if iv["standings"][i]["order"] and iv["standings"][i]["order"][0]["tie"])
        text = f"{nm(w['person'])} {wtxt}" + (f" ({nt} tied)" if nt else "") + (f" · {per:.1f} s faster per rep" if per is not None and per >= 0.05 else "")
        if closest and ns > 1:
            text += f" · closest: Rep {closest[1]}, {closest[0]:.1f} s"
        return {"kind": "intervals", "winner": w["person"], "rep_wins": wins, "rep_ties": nt, "shared_reps": ns,
                "avg_gap_s_per_rep": _r(per, 1), "max_gap_s": _r(max(gaps), 1) if gaps else None,
                "closest": {"label": closest[1], "gap_s": closest[0]} if closest else None, "text": text}
    c = s.get("continuous") or {}
    g = s["group"]
    if g["label"] == "same_race" and (c.get("race") or c.get("finish")):
        f = c.get("race") or c["finish"]
        text = " · ".join(f"{nm(x['person'])} " + (sg.mmss(x["time_s"]) if x["rank"] == 1 else f"{sg.mmss(x['gap_s'])} behind") for x in f)
        return {"kind": "race", "winner": f[0]["person"], "order": [x["person"] for x in f], "text": text}
    ap = (c.get("apart") or [])
    if ap:
        wp = min(x["with_pct"] or 0 for x in ap)
        text = f"side by side {wp:.0f} % of the time"
        span = next((p["together"]["text"] for p in g["participants"] if p.get("together")), None)
        return {"kind": "together", "with_pct": wp, "text": span or text}
    return {"kind": g["label"], "text": g["label_text"]}


def _passes(x, D, kind):
    """passes on a common axis x (1 s steps): D = {key: metres along (NaN = no data)}. A pass = a pair's order flips
    and the new lead holds >= PASS_M[kind] for >= PASS_HOLD_S[kind] s; the first established order isn't a pass.
    -> [{x_s (where the lead changed sign), at_m (the passer's metres there), who, whom}] sorted by x"""
    hy, hold = PASS_M[kind], PASS_HOLD_S[kind]
    out = []
    for a, b in itertools.combinations(list(D), 2):
        diff = D[a] - D[b]
        state, cand, run = 0, 0, 0
        for n, v in enumerate(diff):
            sg_ = 0 if not np.isfinite(v) else (1 if v >= hy else -1 if v <= -hy else 0)
            if sg_ == 0:
                cand, run = 0, 0
                continue
            if sg_ != cand:
                cand, run = sg_, 0
            run += 1
            if run < hold or cand == state:
                continue
            if state != 0:
                # the crossing: the last sample before n where the lead was still the old one's (or level)
                m = n
                while m > 0 and np.isfinite(diff[m - 1]) and diff[m - 1] * cand > 0:
                    m -= 1
                who, whom = (a, b) if cand > 0 else (b, a)
                dw = D[who][m]
                out.append({"x_s": _r(float(x[m]), 0), "at_m": _r(float(dw) if np.isfinite(dw) else None, 0),
                            "who": who, "whom": whom})
            state = cand
    out.sort(key=lambda e: e["x_s"])
    return out


def _moves(prev, cur):
    """rank changes between two standings {key: rank}: [{person, from_rank, to_rank}] (up first)"""
    mv = [{"person": k_, "from_rank": prev[k_], "to_rank": r} for k_, r in cur.items() if k_ in prev and prev[k_] != r]
    return sorted(mv, key=lambda m: (m["to_rank"] - m["from_rank"], m["to_rank"]))


def _ranked(tot):
    """{key: total_s} -> {key: {total_s, gap_s, rank}}"""
    order = sorted(tot.items(), key=lambda kv: kv[1])
    return {k_: {"total_s": _r(v, 1), "gap_s": _r(v - order[0][1], 1), "rank": n + 1} for n, (k_, v) in enumerate(order)}


def _seg_dist(fn, st, x, off, run_s, cap, full=True):
    """metres into a segment at axis x for one person: fn(abs t) = metres along, st = own start (abs), off = own
    start on the axis (0 = own-start alignment), run_s = own segment time, cap = segment length: after the finish a
    full segment is exactly at the line (GPS distance x scale is within a few m of it), a partial one holds"""
    tt = st + np.clip(x - off, 0, run_s)
    d = fn(tt) - fn(np.array([st]))[0]
    d = np.clip(d, 0, cap if cap else None)
    d[x < off] = 0.0
    if cap and full:
        d[x - off >= run_s] = cap
    return d


def _segment_passes(people, fns, kind):
    """passes in one segment for both alignments; people = {key: {st (abs), run_s, scale, cap}}"""
    out = {}
    if len(people) < 2:
        return {"own": [], "clock": []}
    s0 = min(v["st"] for v in people.values())
    W = math.ceil(max(v["run_s"] for v in people.values())) + 2
    dstart = math.ceil(max(v["st"] for v in people.values()) - s0)
    for align, ext in (("own", 0), ("clock", dstart)):
        x = np.arange(0, W + ext + 1, 1.0)
        D = {k_: _seg_dist(fns[k_], v["st"], x, 0.0 if align == "own" else v["st"] - s0, v["run_s"], v["cap"],
                           not v.get("partial")) for k_, v in people.items()}
        out[align] = _passes(x, D, kind)
    return out


def _rep_segments(g, P, s, t0):
    """interval sessions: one segment per rep slot (start / time per person, passes own / clock, total so far, rank
    changes overall); the whole-session passes (clock) with group-clock times"""
    iv = s["intervals"]
    byk = {p["key"]: p for p in P}
    cum = iv["cumulative"]
    ci = {sl: n for n, sl in enumerate(cum["slots"])}
    segs, allp = [], []
    prev_rank = None
    for sl in iv["slots"]:
        tgt = sl.get("target") or {}
        cap = tgt.get("value") if tgt.get("kind") == "dist" else None
        ppl, fns = {}, {}
        for k_, cells in iv["reps"].items():
            c = cells[sl["slot"]]
            p = byk.get(k_)
            if not c or p is None or p.get("T") is None:
                continue
            run_s = c.get("time_s") if not c.get("partial") else c.get("own_time_s")
            run_s = run_s or c.get("own_time_s")
            if not run_s:
                continue
            T = p["T"]
            st_abs = t0 + c["clock_start_s"]
            sc = c.get("scale") or 1.0
            ppl[k_] = {"st": st_abs, "run_s": float(run_s), "scale": sc, "cap": cap, "partial": bool(c.get("partial"))}
            if not (T["S"] or {}).get("no_dist"):
                G = T["G"]
                fns[k_] = (lambda tt, G=G, b=T["base"], sc=sc: np.interp(tt - b, np.arange(G["T"] + 1), G["d"]) * sc)
        if not ppl:
            continue
        s0 = min(v["st"] for v in ppl.values())
        pas = _segment_passes({k_: v for k_, v in ppl.items() if k_ in fns}, fns, "rep")
        for e in pas["clock"]:
            allp.append({**e, "t_s": _r(s0 - t0 + e["x_s"], 1), "seg": len(segs), "label": sl["label"]})
        total = moves = None
        if sl["slot"] in ci:
            n = ci[sl["slot"]]
            tot = {k_: v[n]["total_s"] for k_, v in cum["people"].items() if v[n]}
            if len(tot) >= 2:
                total = _ranked(tot)
                cur = {k_: v["rank"] for k_, v in total.items()}
                moves = _moves(prev_rank, cur) if prev_rank else []
                prev_rank = cur
        segs.append({"i": len(segs), "kind": "rep", "slot": sl["slot"], "label": sl["label"],
                     "title": f"Rep {sl['label']}" + (f" · {tgt['text']}" if tgt.get("text") else ""),
                     "target_m": cap, "start_s": _r(s0 - t0, 1),
                     "end_s": _r(max(v["st"] + v["run_s"] for v in ppl.values()) - t0, 1),
                     "people": {k_: {"start_s": _r(v["st"] - t0, 1), "time_s": _r(v["run_s"], 1), "scale": _r(v["scale"], 4),
                                     "partial": bool(iv["reps"][k_][sl["slot"]].get("partial")),
                                     "result_s": iv["reps"][k_][sl["slot"]].get("result_s"),
                                     "rank": iv["reps"][k_][sl["slot"]].get("rank")} for k_, v in ppl.items()},
                     "passes": pas, "total": total, "moves": moves})
    return segs, sorted(allp, key=lambda e: e["t_s"])


def _km_segments(g, P, t0):
    """continuous sessions: one segment per km along the reference's path (from where everyone is on it), passes per
    km (own / clock) and over the whole window (clock), total time so far per km; + each person's metres along the
    path (route_m) for the client"""
    R, _why = _route_progress(g, P)
    if R is None:
        return None
    act, a, b, a_ov, b_ov, race, ref, Gw, rp, prog = R
    lo = max(v[1][0] for v in prog.values())
    hi = min(v[1][-1] for v in prog.values())
    keys = list(prog)
    fns = {k_: (lambda tt, tc=v[0], pr=v[1]: np.interp(tt, tc, pr, left=np.nan, right=np.nan)) for k_, v in prog.items()}
    if hi - lo < KM_MIN_M:
        return None
    bnd = [lo + 1000.0 * n for n in range(int((hi - lo) // 1000) + 1)]
    if hi - bnd[-1] >= KM_MIN_M or len(bnd) == 1:
        bnd.append(hi)
    else:   # a short remainder joins the last km
        bnd[-1] = hi
    segs = []
    first = {}
    prev_rank = None
    for n in range(1, len(bnd)):
        ppl = {}
        for k_ in keys:
            tc, pr, _ = prog[k_]
            ja, jb = np.searchsorted(pr, [bnd[n - 1], bnd[n] - 0.5], side="left")
            if jb >= len(tc):
                continue
            ppl[k_] = {"st": float(tc[ja]), "run_s": float(tc[jb] - tc[ja]), "scale": 1.0, "cap": float(bnd[n] - bnd[n - 1])}
            first.setdefault(k_, float(tc[ja]))
        if not ppl:
            break
        fk = {k_: (lambda tt, f=fns[k_], b0=bnd[n - 1]: np.nan_to_num(f(tt), nan=b0)) for k_ in ppl}
        pas = _segment_passes(ppl, fk, "km")
        tot = {k_: v["st"] + v["run_s"] - first[k_] for k_, v in ppl.items()}
        total = _ranked(tot) if len(tot) >= 2 else None
        moves = None
        if total:
            cur = {k_: v["rank"] for k_, v in total.items()}
            moves = _moves(prev_rank, cur) if prev_rank else []
            prev_rank = cur
        L = bnd[n] - bnd[n - 1]
        res = sorted((v["run_s"], k_) for k_, v in ppl.items())
        segs.append({"i": n - 1, "kind": "km", "slot": n - 1, "label": str(n),
                     "title": f"km {n}" + ("" if abs(L - 1000) < 1 else f" · {round(L)} m"),
                     "target_m": _r(L, 0), "from_m": _r(bnd[n - 1] - lo, 0),
                     "start_s": _r(min(v["st"] for v in ppl.values()) - t0, 1),
                     "end_s": _r(max(v["st"] + v["run_s"] for v in ppl.values()) - t0, 1),
                     "people": {k_: {"start_s": _r(v["st"] - t0, 1), "time_s": _r(v["run_s"], 1), "scale": 1.0, "partial": False,
                                     "result_s": _r(v["run_s"], 1), "rank": [kk for _, kk in res].index(k_) + 1} for k_, v in ppl.items()},
                     "passes": pas, "total": total, "moves": moves})
    # the whole window on the clock, metres along the shared route
    tt = np.arange(math.ceil(a), math.floor(b) + 1, 1.0)
    D = {k_: fns[k_](tt) - lo for k_ in keys}
    allp = []
    for e in _passes(tt, D, "route"):
        t_abs = float(e["x_s"])
        seg = next((sg_["i"] for sg_ in segs if sg_["start_s"] <= t_abs - t0 <= sg_["end_s"] + 1), None)
        e.pop("x_s")
        allp.append({**e, "t_s": _r(t_abs - t0, 1), "seg": seg,
                     "label": segs[seg]["label"] if seg is not None else None})
    return segs, allp, {k_: (v[0], v[1] - lo) for k_, v in prog.items()}, lo


def replay(gid, step_s=REPLAY_STEP_S, max_points=4000, doc=None) -> dict:
    """per participant position over the group clock (s since t0_utc), thinned to step_s (and at most max_points),
    + HR; rep windows per slot for "reps only" playback; segments (reps, or km along the shared route for continuous
    sessions) with per-person start / time, passes (own-start and clock alignment), total time so far and rank
    changes; passes over the whole session on the clock"""
    doc = doc or load()
    g = find(gid, doc)
    if g is None:
        raise KeyError(f"no group {gid}")
    P = _participants(g)
    if not any(p.get("T") for p in P):
        return {"gid": g["gid"], "available": False, "why": "no FIT on any side", "people": {}}
    t0 = _t0(P)
    s = None
    try:
        s = session(g["gid"], doc)
    except Exception:  # noqa: BLE001, S110
        pass
    segs, allp, route, mode = [], [], {}, None
    try:
        if s and s.get("intervals"):
            segs, allp = _rep_segments(g, P, s, t0)
            mode = "rep"
        elif s and (s.get("continuous") or {}).get("available"):
            km = _km_segments(g, P, t0)
            if km:
                segs, allp, route, _lo = km
                mode = "km"
    except Exception as e:  # noqa: BLE001 — positions still replay without segments
        print("replay segments", g["gid"], e, file=sys.stderr)
    ppl = {}
    for p in P:
        T = p.get("T")
        if T is None or not T["gps"].any():
            continue
        G = T["G"]
        idx = np.flatnonzero(T["gps"])
        st = max(int(step_s), math.ceil(len(idx) / max_points))
        idx = idx[::st]
        tt = T["base"] + idx
        ppl[p["key"]] = {"person": p["person"], "role": p["role"], "t": cmp._lst(tt - t0, 1),
                         "lat": cmp._lst(G["lat"][idx], 6), "lon": cmp._lst(G["lon"][idx], 6),
                         "hr": cmp._lst(G["hr"][idx], 0) if "hr" in G else None,
                         "dist_m": cmp._lst(G["d"][idx], 0)}
        if p["key"] in route:
            tc, pr = route[p["key"]]
            ppl[p["key"]]["route_m"] = cmp._lst(np.interp(tt, tc, pr, left=np.nan, right=np.nan), 0)
    reps = []
    if s and s.get("intervals"):
        iv = s["intervals"]
        for sl in iv["slots"]:
            cells = [(k_, iv["reps"][k_][sl["slot"]]) for k_ in iv["reps"] if iv["reps"][k_][sl["slot"]]]
            reps.append({"slot": sl["slot"], "label": sl["label"], "start_s": sl["start_s"],
                         "end_s": _r(sl["start_s"] + max((c.get("time_s") or c.get("own_time_s") or 0) for _, c in cells) + 2, 1) if cells else None})
    return {"gid": g["gid"], "available": bool(ppl), "t0_utc": _iso(t0), "step_s": step_s, "people": ppl, "reps": reps,
            "segment_kind": mode, "segments": segs, "passes": allp,
            "pass_rule": {"min_lead_m": PASS_M, "hold_s": PASS_HOLD_S},
            "note": "t = seconds since t0_utc on the group clock (each participant's clock offset applied); "
                    "segment passes x_s = s into the segment on that alignment's axis (own: since each person's own "
                    "start; clock: since the earliest start), at_m = the passer's metres into the segment; "
                    "session passes t_s on the group clock"}


# ------------------------------------------------------------------ list / filters (shared with the API)
def rows(doc=None, person=None, sport=None, label=None, near=False, place=None, date_from=None, date_to=None, q=None,
         kind=None) -> list[dict]:
    doc = doc or load()
    info = people_info()
    ps = set(person.split(",")) if person else None
    sports = set(sport.split(",")) if sport else None
    labels = set(label.split(",")) if label else None
    kinds = set(kind.split(",")) if kind else None
    ql = q.lower().split() if q else None
    out = []
    for g in doc["groups"]:
        if g["hidden"] and not near and not (labels and g["label"] in labels):
            continue
        if ps and not ps & set(g["people"]):
            continue
        if sports and g["sport"] not in sports:   # the group's sport (a hike one of them logged as "run" is a hike)
            continue
        if labels and g["label"] not in labels:
            continue
        if kinds and g["kind"] not in kinds:
            continue
        if place and place.lower() not in " ".join(str(g["place"].get(k) or "") for k in ("name", "id", "name_local")).lower():
            continue
        if date_from and g["date"] < date_from:
            continue
        if date_to and g["date"] > date_to:
            continue
        if ql:
            hay = " ".join([g["gid"], g["date"], g["label"], g["label_text"], g["kind"], g["workout"] or "",
                            str(g["place"].get("name")), str(g["place"].get("name_local") or ""), *(info.get(p, {}).get("name", p) for p in g["people"]),
                            *(p.get("workout") or "" for p in g["participants"])]).lower()
            if not all(w in hay for w in ql):
                continue
        out.append(g)
    return out


def head_to_head(groups: list[dict], sessions: dict) -> list[dict]:
    """per pair of people over the given groups: sessions together, shared reps won / tied, sessions won
    (total over the pair's shared reps), mean gap per shared rep (b - a). sessions: {gid: session payload}"""
    acc = {}
    for g in groups:
        s = sessions.get(g["gid"]) or {}
        ps = sorted(set(g["people"]))
        iv = s.get("intervals")
        for a, b in itertools.combinations(ps, 2):
            x = acc.setdefault((a, b), {"a": a, "b": b, "sessions": 0, "interval_sessions": 0, "shared_reps": 0,
                                        "rep_wins": {a: 0, b: 0}, "rep_ties": 0, "session_wins": {a: 0, b: 0},
                                        "_gaps": []})
            x["sessions"] += 1
            if not iv:
                continue
            ra, rb = iv["reps"].get(a), iv["reps"].get(b)
            if not ra or not rb:
                continue
            ta = tb = 0.0
            n = 0
            for ca, cb in zip(ra, rb):
                if not ca or not cb or ca.get("result_s") is None or cb.get("result_s") is None:
                    continue
                n += 1
                d = cb["result_s"] - ca["result_s"]
                tie = abs(d) <= max(ca.get("tie_s") or TIE_MIN_S, TIE_MIN_S, (cb.get("tie_s") or 0))
                if tie:
                    x["rep_ties"] += 1
                else:
                    x["rep_wins"][a if d > 0 else b] += 1
                ta += ca["result_s"]
                tb += cb["result_s"]
                x["_gaps"].append(d)
            if n:
                x["interval_sessions"] += 1
                x["shared_reps"] += n
                if abs(tb - ta) > TIE_MIN_S:
                    x["session_wins"][a if tb > ta else b] += 1
    out = []
    for x in acc.values():
        gp = x.pop("_gaps")
        x["mean_gap_s"] = _r(np.mean(gp), 1) if gp else None
        x["note"] = "mean_gap_s = b's rep result - a's, over shared reps (positive: a faster)"
        out.append(x)
    return out


def list_payload(doc=None, viewer=None, **flt) -> dict:
    """the list endpoint: filtered group rows + headline + completion, head-to-head per pair"""
    doc = doc or load()
    gs = rows(doc, **flt)
    info = people_info()
    out, sess = [], {}
    for g in gs:
        s = None
        if not g["hidden"] and not g["no_track"]:
            try:
                s = session(g["gid"], doc)
                sess[g["gid"]] = s
            except Exception as e:  # noqa: BLE001
                s = {"error": str(e)[:200]}
        row = {k: g[k] for k in ("gid", "date", "start_local", "start_utc", "sport", "sports", "kind", "label",
                                 "label_text", "score", "hidden", "place", "workout", "people", "n_people", "no_track")}
        row["participants"] = [{k: p.get(k) for k in ("person", "activity_id", "role", "workout", "signature", "n_reps",
                                                       "dist_km", "dur_s", "together")} for p in g["participants"]]
        row["headline"] = (s or {}).get("headline") or _summary_headline(g, info)
        if s and s.get("intervals"):
            lbm = {x["person"]: x for x in s["intervals"]["leaderboard"]}
            for p in row["participants"]:
                x = lbm.get(p["person"])
                if x:
                    p["reps_done"], p["of"] = x["reps_done"], x["of"]
        if s and s.get("error"):
            row["error"] = s["error"]
        out.append(row)
    return {"n": len(out), "viewer": viewer, "built": doc.get("built"),
            "people": {p: info[p] for p in info}, "h2h": head_to_head([g for g in gs if not g["hidden"]], sess), "groups": out}


def _summary_headline(g, info):
    """list headline of a group without a comparison: unverified -> who has no watch file / no GPS (plain words)"""
    if g["no_track"]:
        miss = [(info.get(p["person"]) or {}).get("name", p["person"].capitalize()) for p in g["participants"]
                if not p["has_fit"] and p["role"] != "other"]
        if miss:
            return {"kind": "unverified", "text": f"{' and '.join(miss)} {'has' if len(miss) == 1 else 'have'} no watch file"}
        return {"kind": "unverified", "text": "no track to compare"}
    return {"kind": g["label"], "text": g["label_text"]}


def inputs_sig() -> tuple:
    """cheap signature (mtimes) of everything the list / sessions read, for response memos"""
    ps = [OUT, PEOPLE_JSON, Path(__file__), Path(cmp.__file__)]
    for p in people():
        D = DATA / p
        ps += [D / "groups.json", D / "fitness.json", D / "profile" / "hr_settings.json", D / "profile" / "heart_rate_zones.json"]
    return tuple(x.stat().st_mtime_ns if x.exists() else 0 for x in ps)


# ------------------------------------------------------------------ links for activity rows
def links(doc=None) -> dict:
    """{(person, activity_id str): {gid, label, with: [person, ...]}} for visible groups (role "other" left out)"""
    doc = doc or load(missing_ok=True)
    out = {}
    if not doc:
        return out
    for g in doc["groups"]:
        if g["hidden"]:
            continue
        ps = [p for p in g["participants"] if p["role"] != "other"]
        for p in ps:
            out[(p["person"], str(p["activity_id"]))] = {
                "gid": g["gid"], "label": g["label"], "kind": g["kind"], "role": p["role"],
                "with": sorted({q["person"] for q in ps if q["person"] != p["person"]})}
    return out


# ------------------------------------------------------------------ CLI
def _fmt_s(v):
    return "—" if v is None else f"{v:.1f}"


def cmd_list(show_all=False):
    doc = load()
    for g in doc["groups"]:
        if g["hidden"] and not show_all:
            continue
        ps = " ".join(f"{p['person']}:{p['activity_id']}({p['role'][0]}{'' if not p.get('signature') else ' ' + p['signature']})"
                      for p in g["participants"])
        print(f"{g['gid']:42} {g['label']:24} {g['kind']:11} {g['score'] if g['score'] is not None else '':>5} "
              f"{g['place']['name'] or '':18.18} {ps}{'  [hidden]' if g['hidden'] else ''}")
    print(f"{sum(not g['hidden'] for g in doc['groups'])} groups, {sum(g['hidden'] for g in doc['groups'])} hidden "
          f"(built {doc['built']}); watch-pair offsets: {doc.get('offsets')}")


def cmd_show(gid):
    doc = load()
    g = find(gid, doc)
    if not g:
        sys.exit(f"no group {gid}")
    print(json.dumps({k: v for k, v in g.items() if k != "pairs"}, indent=1, ensure_ascii=False)[:3000])
    for e in g["pairs"]:
        print(f"  pair {e['a']} {e['b']} {e['label']} score {e['score']} offset {e.get('offset_s')} ({e.get('offset_src')}): {e['why']}")
    s = session(g["gid"], doc)
    print("headline:", s["headline"].get("text"))
    for c in s["caveats"]:
        print("  ⚠", c["text"])
    iv = s.get("intervals")
    if iv:
        keys = list(iv["reps"])
        print(f"\nanchor {iv['anchor']}  gps scale {iv['gps_scale']}  shared slots {len(iv['shared_slots'])}")
        print("slot  target   " + "".join(f"{k:>28}" for k in keys))
        for sl in iv["slots"]:
            cells = []
            for k in keys:
                c = iv["reps"][k][sl["slot"]]
                if not c:
                    cells.append(f"{'—':>28}")
                    continue
                tag = ("◐" if c["partial"] else "") + ("~" if c["method"] == "interpolated" else "") + ("*" if c["source"] == "onset" else "")
                rk = c.get("rank")
                cells.append(f"{tag:>3}{_fmt_s(c['result_s'] if c['result_s'] is not None else c['time_s']):>7} "
                             f"{'#' + str(rk) if rk else '':>3}{('=' if c.get('tie') else ''):1} "
                             f"{('+' + _fmt_s(c.get('gap_s'))) if c.get('gap_s') else '':>6} {c.get('effort_pct') or '':>4}%")
            print(f"{sl['label']:5} {sl['target']['text'] or '':8} " + "".join(cells) + ("  shared" if sl["shared"] else ""))
        print("\nleaderboard:")
        for x in iv["leaderboard"]:
            print(f"  {x['rank'] or '-'} {x['person']:10} reps {x['reps_done']}/{x['of']} total {_fmt_s(x['total_s'])} "
                  f"avg {_fmt_s(x['avg_s'])} gap {_fmt_s(x['gap_s'])} wins {x['rep_wins']} ties {x['rep_ties']} "
                  f"fade {x['fade_s']} s ({x['fade_s_km']} s/km) effort {x['avg_effort_pct']} % {x['effort_mode']}")
        print("cumulative gap:", {k: [c["gap_s"] if c else None for c in v] for k, v in iv["cumulative"]["people"].items()})
        for k, rows_ in iv["breaks"].items():
            print(f"breaks {k}: " + ", ".join(f"{b['after_label']}:{b['dur_s']:.0f}s drop30 {b.get('drop_30')} ({b.get('drop_30_pct')} %)" for b in rows_[:8]))
    c = s.get("continuous")
    if c and c.get("available"):
        for x in c.get("apart") or []:
            print(f"apart {x['a']}–{x['b']}: with {x['with_pct']} %, within 20 m {x['within_20m_pct']} %, median {x['median_apart_m']} m")
        if c.get("race"):
            print("race:", [(f["person"], f["time_s"], f["gap_s"], f["dist_km"]) for f in c["race"]])
        if c.get("finish"):
            print("finish:", [(f["person"], f["time_s"], f["gap_s"]) for f in c["finish"]], "over", c.get("common_m"), "m")
        if c.get("km_splits"):
            print("km splits:", [{k: v["time_s"] for k, v in r["people"].items()} for r in c["km_splits"][:12]])
        if c.get("led"):
            print("led:", [(x["from_m"], x["to_m"], x["person"]) for x in c["led"]][:12])
    print(f"compute {s['compute_s']} s")


def validate() -> int:
    """generic checks: together.json structure, links into the indexes, every session's numbers"""
    doc = load()
    bad = 0

    def check(ok, msg):
        nonlocal bad
        if not ok:
            bad += 1
            print("  FAIL", msg)
        return ok
    gids = [g["gid"] for g in doc["groups"]]
    check(len(gids) == len(set(gids)), "group ids unique")
    for k, v in (doc.get("aliases") or {}).items():
        check(v in gids, f"alias {k} -> {v} resolves")
    idx = {p: {str(x["activityId"]) for x in json.loads((DATA / p / "activities_index.json").read_text())} for p in people()}
    for g in doc["groups"]:
        check(re.match(r"^\d{4}-\d{2}-\d{2}_\d{4}_[A-Za-z0-9-]+$", g["gid"]) is not None, f"{g['gid']}: id format")
        check(len(set(g["people"])) >= 2 or g["hidden"], f"{g['gid']}: >= 2 people")
        for p in g["participants"]:
            check(p["activity_id"] in idx.get(p["person"], set()), f"{g['gid']}: {p['person']}:{p['activity_id']} in index")
            check(p.get("offset_s") is None or abs(p["offset_s"]) <= 15, f"{g['gid']}: clock offset {p.get('offset_s')} within 15 s")
        if g["hidden"] or g["no_track"]:
            continue
        bad0 = bad
        try:
            s = session(g["gid"], doc, use_cache=False)
        except Exception as e:  # noqa: BLE001
            check(False, f"{g['gid']}: session failed: {e!r}")
            continue
        iv = s.get("intervals")
        if g["kind"] == "intervals":
            if not check(iv is not None, f"{g['gid']}: intervals result"):
                continue
            ns = len(iv["shared_slots"])
            check(ns >= 1, f"{g['gid']}: shared reps ({ns})")
            for st in iv["standings"]:
                o = st["order"]
                if not o:
                    continue
                check(o[0]["rank"] == 1 and o[0]["gap_s"] == 0, f"{g['gid']} rep {st['label']}: winner rank 1, gap 0")
                check(all(x["gap_s"] >= 0 for x in o), f"{g['gid']} rep {st['label']}: gaps >= 0")
                check(all(a["result_s"] <= b["result_s"] for a, b in itertools.pairwise(o)), f"{g['gid']} rep {st['label']}: sorted")
                for x in o:
                    if x["tie"]:
                        check(x["gap_s"] - min(y["gap_s"] for y in o if y["rank"] == x["rank"]) <= x["tie_s"] + 0.05,
                              f"{g['gid']} rep {st['label']}: tie within tie_s")
            sl = iv["slots"]
            for k, cells in iv["reps"].items():
                for c, slt in zip(cells, sl):
                    if c and c["result_s"] and slt["target"]["kind"] == "dist":
                        v = slt["target"]["value"] / c["time_s"]
                        check(1.5 <= v <= 12, f"{g['gid']} {k} rep {slt['label']}: speed {v:.2f} m/s plausible")
                    if c and c["source"] == "detector" and c["method"] == "detector" and slt["target"]["kind"] == "dist":
                        check(c["time_s"] == c["own_time_s"], f"{g['gid']} {k} rep {slt['label']}: result = detected rep time")
            # detected reps = the person's own compare output (intervals mode, same session)
            for pt in s["participants"]:
                if pt["role"] == "other" or not pt["has_fit"] or pt["key"] not in iv["reps"]:
                    continue
                try:
                    own = cmp.compare([int(pt["activity_id"])], "intervals", person=pt["person"], workers=1)
                    starts = {r["start_s"]: r["time_s"] for r in own["reps"][pt["activity_id"]] if r}
                except ValueError:
                    starts = {}
                for c in iv["reps"][pt["key"]]:
                    if c and c["source"] == "detector":
                        check(starts.get(c["start_s"]) == c["own_time_s"],
                              f"{g['gid']} {pt['key']} rep at {c['start_s']} s: {c['own_time_s']} = compare {starts.get(c['start_s'])}")
            for x in iv["leaderboard"]:
                if x["total_s"] is not None:
                    tot = sum(iv["reps"][x["person"]][i]["result_s"] for i in iv["shared_slots"])
                    check(abs(tot - x["total_s"]) < 0.2 * ns + 0.1, f"{g['gid']} {x['person']}: total = sum of shared reps")
            cum = iv["cumulative"]
            for k, v in cum["people"].items():
                live = [c for c in v if c]
                if live:
                    tot = sum(iv["reps"][k][i]["result_s"] for i, c in zip(cum["slots"], v) if c)
                    check(abs(live[-1]["total_s"] - tot) < 0.2 * len(live) + 0.1, f"{g['gid']} {k}: cumulative total = sum")
            for j in range(len(cum["slots"])):
                gaps = [v[j]["gap_s"] for v in cum["people"].values() if v[j]]
                check(not gaps or min(gaps) == 0, f"{g['gid']} cumulative after {cum['labels'][j]}: leader at 0")
        c = s.get("continuous")
        if c and c.get("available") and c.get("gap_over_distance"):
            gd = c["gap_over_distance"]
            for i in range(len(gd["x_m"])):
                vals = [v[i] for v in gd["people"].values() if v[i] is not None]
                check(not vals or min(vals) == 0, f"{g['gid']} gap over distance: leader at 0 at {gd['x_m'][i]} m")
                if bad > 50:
                    break
        r = replay(g["gid"], doc=doc)
        for k, v in r["people"].items():
            check(len(v["t"]) == len(v["lat"]) == len(v["lon"]), f"{g['gid']} replay {k}: equal lengths")
            check(all(a < b for a, b in zip(v["t"], v["t"][1:])), f"{g['gid']} replay {k}: t increasing")
        print(f"  {'ok' if bad == bad0 else 'FAILED'} {g['gid']:42} {s['headline'].get('text')}")
    print(f"{len(doc['groups'])} groups checked, {bad} failures")
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    b = sp.add_parser("build")
    b.add_argument("--full", action="store_true", help="recompute every pair (ignore the pair cache)")
    b.add_argument("--quiet", action="store_true")
    li = sp.add_parser("list")
    li.add_argument("--all", action="store_true", help="also hidden near-misses")
    sh = sp.add_parser("show")
    sh.add_argument("gid")
    sp.add_parser("validate")
    a = ap.parse_args()
    os.chdir(ROOT)
    if a.cmd == "build":
        build(full=a.full, quiet=a.quiet)
    elif a.cmd == "list":
        cmd_list(a.all)
    elif a.cmd == "show":
        cmd_show(a.gid)
    elif a.cmd == "validate":
        sys.exit(1 if validate() else 0)


if __name__ == "__main__":
    main()

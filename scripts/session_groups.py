"""Group sessions by place / route / session type, find similar sessions, split every session into
legs and pauses, and compare the sessions of each recurring route leg by leg.

Usage: uv run scripts/session_groups.py adrian
Writes data/<person>/groups.json (see docs/session-groups.md). Caches the (slow, fitparse-based)
rep detector output per FIT file in data/<person>/cache/session_groups.pkl, keyed by mtime+size.
Place name overrides: data/<person>/places.json {"<place id>" or "lat,lon": "name"}.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import math
import pickle
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gps_quality as gq  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CACHE_VERSION = 1

# the fast reader decodes only listed fields: add altitude, heart rate and timer events
gq._WANT[20].update({2: "altitude", 3: "heart_rate", 78: "enhanced_altitude"})
gq._WANT[21] = {0: "event", 1: "event_type"}
gq._NAMES[21] = "event"

# ---- parameters ----
PLACE_R, AREA_R = 400, 2000          # m: place = greedy density cluster of first fix; area = single link
CELL = 75.0                          # m: route grid cell
ROUTE_SIM, ROUTE_DIST_RATIO = 0.6, 1.3
STOP_V, MIN_PAUSE_S = 0.5, 15        # pause = speed < 0.5 m/s for >= 15 s (or timer stopped)
BLIP_S, MIN_LEG_S, MIN_LEG_M = 5, 20, 30   # moving blips < 5 s inside a stop are stop; legs < 20 s and < 30 m merge pauses
CLIMB_HYST = 2.0                     # m: climb/descent hysteresis on 9 s smoothed altitude
REF_STEP = 20.0                      # m: reference polyline spacing
ON_ROUTE_M = 80.0                    # m: max distance to the reference to count as on route
WP_CLUSTER_M, WP_SHARE, WP_MATCH_M = 150.0, 0.3, 200.0   # stop cluster gap, min share of sessions, match radius
SPLIT_LEG_M = 3000.0                 # m: stretches between common stops longer than this are split into ~2 km legs
OWN_RATIO = (0.8, 1.25)              # leg dropped if own distance / reference length is outside this
LEG_COVER = 0.8                      # a session's leg counts if it covers >= 80 % of it (speed uses covered length)
MIN_ROUTE_N = 3
FAM = {"running": "run", "track_running": "run", "treadmill_running": "run", "indoor_running": "run",
       "trail_running": "run", "cross_country_skiing_ws": "ski", "skate_skiing_ws": "ski", "hiking": "hike"}
KNOWN = {"Bislet": (59.9253, 10.7331), "Sognsvann": (59.9690, 10.7310), "Frognerseteren": (59.9785, 10.6800),
         "Holmenkollen": (59.9620, 10.6600), "Granåsen": (63.3800, 10.3130), "NTNU Gløshaugen": (63.4190, 10.4040)}
BISLET = KNOWN["Bislet"]
SIM_W = {  # similarity presets; components missing for a pair score 0.4
    "route": {"route": 0.45, "type": 0.15, "dist": 0.12, "dur": 0.05, "pace": 0.04, "intensity": 0.07,
              "structure": 0.03, "elev": 0.03, "season": 0.03, "recency": 0.03},
    "workout": {"route": 0.10, "type": 0.30, "dist": 0.12, "dur": 0.08, "pace": 0.10, "intensity": 0.12,
                "structure": 0.10, "elev": 0.04, "season": 0.02, "recency": 0.02},
}
RELATED = {frozenset(p) for p in [("intervals_track", "intervals_road"), ("intervals_road", "tempo"), ("tempo", "race?"),
                                  ("easy", "long"), ("ski_easy", "ski_long"), ("ski_hard", "ski_long_race?"),
                                  ("ski_long", "ski_long_race?"), ("intervals_track", "intervals_treadmill"),
                                  ("intervals_road", "intervals_treadmill")]}
STD = [(5, "5k"), (10, "10k"), (21.0975, "half"), (42.195, "marathon")]
FIELDS = {
    "legs": ["t", "dur_s", "dist_m", "kmh", "hr", "hr_max", "climb_m", "desc_m", "grade_pct"],
    "pauses": ["t", "dur_s", "lat", "lon", "km", "timer"],
    "reps": ["time_s", "dist_m", "speed_ms", "hr"],
}


# ---------------------------------------------------------------- geometry helpers
def hav(a, b):
    return gq.hav(a, b)


def dist_matrix(pts):
    p = np.radians(np.array(pts, float))
    lat, lon = p[:, 0][:, None], p[:, 1][:, None]
    h = np.sin((lat - lat.T) / 2) ** 2 + np.cos(lat) * np.cos(lat.T) * np.sin((lon - lon.T) / 2) ** 2
    return 2 * 6371008.8 * np.arcsin(np.sqrt(np.minimum(1, h)))


def single_link(D, eps):
    n = len(D); lab = [-1] * n; k = 0
    for i in range(n):
        if lab[i] >= 0:
            continue
        st = [i]; lab[i] = k
        while st:
            j = st.pop()
            for m in np.nonzero(D[j] <= eps)[0]:
                if lab[m] < 0:
                    lab[m] = k; st.append(m)
        k += 1
    return lab


def leader(D, R):
    """greedy density clustering: densest unassigned point + its unassigned neighbours within R"""
    n = len(D); lab = np.full(n, -1); k = 0
    A = D <= R
    while (lab < 0).any():
        free = lab < 0
        c = int(np.argmax(A[:, free].sum(1) * free))
        lab[free & A[c]] = k; k += 1
    return lab.tolist()


def densify(pts, step=20.0, max_gap=500.0):
    out = [pts[0]]
    for a, b in zip(pts, pts[1:]):
        d = math.hypot((b[0] - a[0]) * 111320, (b[1] - a[1]) * 111320 * math.cos(math.radians(a[0])))
        if d > max_gap:
            out.append(None)
        elif d > step:
            k = int(d // step)
            out += [(a[0] + (b[0] - a[0]) * i / (k + 1), a[1] + (b[1] - a[1]) * i / (k + 1)) for i in range(1, k + 1)]
        out.append(b)
    return out


def cells_of(pts, lat_ref=60.0):
    kx = 111320 * math.cos(math.radians(lat_ref)) / CELL
    ky = 111320 / CELL
    return [(int(math.floor(p[1] * kx)), int(math.floor(p[0] * ky))) if p else None for p in densify(pts)]


def shape_of(seq, pts, track_len):
    """retrace share = share of cell visits re-entering a cell first seen >= 8 cells earlier"""
    first, re, n, prev = {}, 0, 0, None
    for c in (s for s in seq if s):
        if c == prev:
            continue
        prev = c; n += 1
        if c in first and n - first[c] >= 8:
            re += 1
        first.setdefault(c, n)
    r = re / max(n, 1)
    se = hav(pts[0], pts[-1])
    kx = math.cos(math.radians(pts[0][0]))
    xs = [p[1] * kx for p in pts]; ys = [p[0] for p in pts]
    area = sum(xs[i] * ys[i + 1] - xs[i + 1] * ys[i] for i in range(len(pts) - 1))
    if se > max(1000, 0.25 * track_len):
        return "point_to_point"
    if r > 0.75:
        return "laps"
    if r > 0.35:
        return "out_and_back"
    return "loop_" + ("ccw" if area > 0 else "cw")


def dilate(s):
    return {(x + dx, y + dy) for x, y in s for dx in (-1, 0, 1) for dy in (-1, 0, 1)}


def known_near(p, r):
    d, n = min((hav(p, q), n) for n, q in KNOWN.items())
    return n if d < r else None


def cum_climb(alt, thr=CLIMB_HYST):
    """cumulative climb / descent per sample with hysteresis"""
    up = np.zeros(len(alt)); dn = np.zeros(len(alt))
    if not len(alt):
        return up, dn
    ref, u, d = alt[0], 0.0, 0.0
    for i, a in enumerate(alt):
        if a - ref >= thr:
            u += a - ref; ref = a
        elif ref - a >= thr:
            d += ref - a; ref = a
        up[i] = u; dn[i] = d
    return up, dn


def r1(v, n=1):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return int(round(float(v))) if n == 0 else round(float(v), n)


# ---------------------------------------------------------------- FIT -> 1 Hz series, legs, pauses
def read_series(fit: Path) -> dict:
    raw = gq._read_fast(fit)
    recs = sorted((r for r in raw["record"] if "timestamp" in r), key=lambda r: r["timestamp"])
    if len(recs) < 2:
        raise ValueError("no records")
    t0 = recs[0]["timestamp"]
    t, lat, lon, dist, alt, hr = [], [], [], [], [], []
    last = None
    for r in recs:
        if r["timestamp"] == last:
            continue
        last = r["timestamp"]
        t.append(r["timestamp"] - t0)
        la, lo = r.get("lat"), r.get("lon")
        ok = la is not None and lo is not None and (la or lo)
        lat.append(la * gq.SEMI if ok else np.nan); lon.append(lo * gq.SEMI if ok else np.nan)
        d = r.get("distance"); dist.append(d / 100 if d is not None else np.nan)
        a = r.get("enhanced_altitude", r.get("altitude")); alt.append(a / 5 - 500 if a is not None else np.nan)
        h = r.get("heart_rate"); hr.append(h if h and 30 < h < 230 else np.nan)
    s = {k: np.array(v, float) for k, v in dict(t=t, lat=lat, lon=lon, dist=dist, alt=alt, hr=hr).items()}
    # timer stop intervals (event 0 = timer; type 1/4 = stop/stop_all, 0 = start)
    stops, cur = [], None
    for e in sorted((e for e in raw["event"] if "timestamp" in e and e.get("event") == 0), key=lambda e: e["timestamp"]):
        te = e["timestamp"] - t0
        if e.get("event_type") in (1, 4) and cur is None:
            cur = te
        elif e.get("event_type") == 0 and cur is not None:
            if te > cur:
                stops.append((cur, te))
            cur = None
    s["timer_stops"] = stops
    s["sport"] = (raw["session"][0].get("sport") if raw["session"] else None)
    return s


def to_grid(s: dict) -> dict:
    """1 Hz grid: distance, speed, lat/lon (+ validity), smoothed altitude, HR."""
    t = s["t"]
    T = int(min(t[-1], 86400))
    g = np.arange(T + 1, dtype=float)
    gps = ~np.isnan(s["lat"])
    dm = ~np.isnan(s["dist"])
    if dm.sum() >= 2:
        d = np.interp(g, t[dm], np.maximum.accumulate(s["dist"][dm]))
    elif gps.sum() >= 2:
        la, lo = s["lat"][gps], s["lon"][gps]
        step = np.hypot(np.diff(la) * 111320, np.diff(lo) * 111320 * np.cos(np.radians(la[:-1])))
        d = np.interp(g, t[gps], np.concatenate([[0], np.cumsum(step)]))
    else:
        d = np.zeros(T + 1)
    v = np.zeros(T + 1)
    if T >= 4:
        v[2:-2] = (d[4:] - d[:-4]) / 4
        v[:2], v[-2:] = v[2], v[-3]
    G = {"T": T, "d": d, "v": v, "gps": None}
    if gps.sum() >= 2:
        tg = t[gps]
        G["lat"] = np.interp(g, tg, s["lat"][gps]); G["lon"] = np.interp(g, tg, s["lon"][gps])
        i = np.clip(np.searchsorted(tg, g), 1, len(tg) - 1)
        near = np.minimum(np.abs(g - tg[i - 1]), np.abs(tg[i] - g))
        G["gps"] = (near <= 15) | ((tg[i] - tg[i - 1]) <= 30)
    am = ~np.isnan(s["alt"])
    if am.sum() >= 2:
        a = np.interp(g, t[am], s["alt"][am])
        k = min(9, len(a))
        a = np.convolve(np.pad(a, (k // 2, k - 1 - k // 2), mode="edge"), np.ones(k) / k, mode="valid")
        G["alt"] = a
        G["up"], G["dn"] = cum_climb(a)
    hm = ~np.isnan(s["hr"])
    if hm.sum() >= 2:
        th = t[hm]
        h = np.interp(g, th, s["hr"][hm])
        i = np.clip(np.searchsorted(th, g), 1, len(th) - 1)
        h[np.minimum(np.abs(g - th[i - 1]), np.abs(th[i] - g)) > 10] = np.nan
        G["hr"] = h
    stopped = v < STOP_V
    G["timer"] = np.zeros(T + 1, bool)
    for a_, b_ in s["timer_stops"]:
        G["timer"][int(max(0, a_)): int(min(T, b_)) + 1] = True
    G["stopped"] = stopped | G["timer"]
    return G


def runs_of(mask):
    """[(a, b)] half-open index runs where mask is True"""
    m = np.concatenate([[False], mask, [False]]).astype(np.int8)
    dd = np.diff(m)
    return list(zip(np.nonzero(dd == 1)[0].tolist(), np.nonzero(dd == -1)[0].tolist()))


def find_pauses(G):
    st = G["stopped"].copy()
    for a, b in runs_of(~st):  # short moving blips between stops are part of the stop
        if b - a < BLIP_S and a > 0 and b < len(st):
            st[a:b] = True
    P = [[a, b] for a, b in runs_of(st) if b - a >= MIN_PAUSE_S or G["timer"][a:b].mean() > 0.5]
    out = []
    for p in P:  # merge pauses separated by a tiny leg
        if out and (p[0] - out[-1][1] < MIN_LEG_S and G["d"][p[0]] - G["d"][out[-1][1] - 1] < MIN_LEG_M):
            out[-1][1] = p[1]
        else:
            out.append(p)
    return [tuple(p) for p in out]


def seg_stats(G, a, b):
    """stats for grid seconds [a, b)"""
    b1 = min(b, G["T"])
    dur = b - a
    dist = float(G["d"][b1] - G["d"][a])
    hr = G.get("hr")
    hs = hr[a:b] if hr is not None else np.array([])
    hs = hs[~np.isnan(hs)] if len(hs) else hs
    up = dn = grade = None
    if "up" in G:
        up = float(G["up"][b1] - G["up"][a]); dn = float(G["dn"][b1] - G["dn"][a])
        grade = 100 * (G["alt"][b1] - G["alt"][a]) / dist if dist > 50 else None
    return [a, dur, round(dist), r1(dist / dur * 3.6 if dur else None, 2), r1(hs.mean() if len(hs) > 5 else None, 0),
            r1(hs.max() if len(hs) > 5 else None, 0), r1(up, 0), r1(dn, 0), r1(grade, 1)]


def legs_and_pauses(G):
    P = find_pauses(G)
    T = G["T"]
    pauses, legs, cur = [], [], 0
    for a, b in P:
        if a > cur:
            legs.append(seg_stats(G, cur, a))
        m = (a + b) // 2
        ll = [r1(G["lat"][m], 5), r1(G["lon"][m], 5)] if G["gps"] is not None and G["gps"][m] else [None, None]
        pauses.append([a, b - a, *ll, r1(G["d"][a] / 1000, 2), int(G["timer"][a:b].mean() > 0.5)])
        cur = b
    if cur < T:
        legs.append(seg_stats(G, cur, T))
    ptot = sum(p[1] for p in pauses)
    summ = {"elapsed": T, "moving": T - ptot, "pause": ptot, "n_pauses": len(pauses), "n_legs": len(legs),
            "longest": max((p[1] for p in pauses), default=0)}
    return legs, pauses, summ, P


# ---------------------------------------------------------------- per-FIT worker
def ana_compact(fit):
    import run_analysis as ra
    x = ra.analyse(fit)
    reps = x["reps"]
    return {"mode": x["mode"], "threshold": x["threshold"],
            "rep_times": [rp["time"] for rp in reps], "rep_dists": [rp["dist"] for rp in reps],
            "rep_speeds": [rp["speed"] for rp in reps], "rep_hr": [rp["hr"] for rp in reps],
            "breaks": [(b["time"], b["dist"], b["standing"]) for b in x["breaks"]],
            "laps": [(l.get("time"), l.get("dist"), l.get("intensity"), l.get("trigger")) for l in x["laps"]]}


def worker(job):
    fit, typ, need_ana = job
    fit = Path(fit)
    out = {"id": int(fit.stem)}
    try:
        s = read_series(fit)
    except Exception as e:
        out["err"] = str(e)[:100]
        return out
    gps = ~np.isnan(s["lat"])
    pts, last = [], None
    for la, lo in zip(s["lat"][gps], s["lon"][gps]):
        p = (float(la), float(lo))
        if last is None or hav(last, p) >= 20:
            pts.append(p); last = p
    out["pts"] = pts
    hh = collections.Counter()
    t, h = s["t"], s["hr"]
    for i in range(len(t) - 1):
        if not np.isnan(h[i]):
            hh[int(h[i])] += min(t[i + 1] - t[i], 10)
    out["hr_hist"] = dict(hh)
    G = to_grid(s)
    if G["d"][-1] >= 200:
        out["legs"], out["pauses"], out["summ"], _ = legs_and_pauses(G)
    out["elapsed"] = G["T"]
    if need_ana:
        try:
            out["ana"] = ana_compact(fit)
        except Exception as e:
            out["ana_err"] = repr(e)[:100]
    return out


def ana_job(fit):
    try:
        return fit, ana_compact(Path(fit))
    except Exception as e:
        return fit, {"err": repr(e)[:100]}


# ---------------------------------------------------------------- session features / types
def zones_floor():
    z = json.loads((ROOT / "data/adrian/profile/heart_rate_zones.json").read_text())
    z = [x for x in z if x.get("sport") in ("DEFAULT", None)][0] if isinstance(z, list) else z
    return [z[f"zone{i}Floor"] for i in range(1, 6)]


def features(a, ZF):
    x, f = a["x"], a["f"] or {}
    dist = (x.get("distance") or 0) / 1000
    mov = x.get("movingDuration") or x.get("duration") or 0
    r = {"dist_km": dist, "moving_s": mov, "speed": dist * 1000 / mov if mov else None,
         "elev_per_km": (x.get("elevationGain") or 0) / dist if dist > 0.3 else 0,
         "avg_hr": x.get("averageHR")}
    hh = f.get("hr_hist") or {}
    tot = sum(hh.values())
    r["zones"] = None
    if tot > 120:
        z = [0.0] * 6
        for hv, sec in hh.items():
            z[sum(hv >= fl for fl in ZF)] += sec
        r["zones"] = [v / tot for v in z]
    ana = f.get("ana")
    if ana:
        sp = [v for v in ana["rep_speeds"] if v]
        rt, br = ana["rep_times"], ana["breaks"]
        r.update(n_reps=len(rt), rep_time=sum(rt), rep_med_s=float(np.median(rt)) if rt else 0,
                 rep_cv=float(np.std(rt) / np.mean(rt)) if len(rt) > 1 else 0,
                 rep_speed=float(np.median(sp)) if sp else None,
                 brk_speed=float(np.median([b[1] / b[0] for b in br if b[0] > 0])) if br else None,
                 brk_med_s=float(np.median([b[0] for b in br])) if br else 0,
                 n_manual=sum(1 for l in ana["laps"] if l[3] == "manual"),
                 n_active=sum(1 for l in ana["laps"] if l[2] == "active"), mode=ana["mode"])
    r["at_bislet"] = bool(a["start"]) and hav(a["start"], BISLET) < 400
    return r


def classify(a, ZF, easy_ref):
    F = a["F"]
    if a["fam"] == "ski":
        z = F["zones"]; mov = F["moving_s"] / 60; hard = (z[4] + z[5]) if z else None
        if not F["dist_ok"]:
            return ("ski_hard" if (F["avg_hr"] or 0) >= ZF[3] else "ski_easy"), "no GPS", None
        surface = "roller?" if a["date"][5:7] in ("05", "06", "07", "08", "09", "10") else "snow"
        if mov >= 150 or F["dist_km"] >= 35:
            t = "ski_long" + ("_race?" if hard is not None and hard > 0.4 else "")
        elif (hard is not None and hard >= 0.25) or (hard is None and (F["avg_hr"] or 0) >= ZF[3]):
            t = "ski_hard"
        else:
            t = "ski_easy"
        return t, "", surface
    if a["fam"] != "run":
        return a["fam"] if a["fam"] != "other" else a["type"], "", None
    if F["dist_km"] < 1.5 or not F["speed"] or F["speed"] < 1.8:
        return "run_other", "walk/short/paused", None
    if not F["dist_ok"]:
        return ("long" if F["moving_s"] >= 85 * 60 else "run_nogps"), "no GPS: distance untrusted", None
    ref = easy_ref(a); rel = F["speed"] / ref; F["rel_speed"] = rel
    z = F["zones"]; hard = (z[4] + z[5]) if z else None
    tags = ["hilly"] if F["elev_per_km"] >= 25 else []
    rs, bs = F.get("rep_speed") or 0, F.get("brk_speed") or 0
    cv_max = 1.0 if F.get("n_manual", 0) >= 6 else 0.6
    structured = (F.get("n_reps", 0) >= 3 and rs >= 1.2 * ref and F.get("rep_time", 0) >= 240
                  and F.get("rep_cv", 9) <= cv_max and bs <= 0.8 * rs) or F.get("n_active", 0) >= 3
    if structured and F.get("rep_med_s", 0) < 35 and F.get("rep_time", 0) < 300:
        structured = False; tags.append("strides")
    if structured:
        where = "track" if (F["at_bislet"] or F.get("mode") == "track" or a["type"] == "track_running") else (
            "treadmill" if a["type"] in ("treadmill_running", "indoor_running") else "road")
        L = F["rep_med_s"]; length = "short" if L < 100 else ("medium" if L < 300 else "long")
        return f"intervals_{where}", " ".join([f"{length} reps"] + tags), None
    near_std = [nm for d, nm in STD if abs(F["dist_km"] / d - 1) <= 0.035]
    continuous = F.get("brk_med_s", 0) < 30 or F.get("n_reps", 0) <= 2
    if rel >= 1.25 and (hard is None or hard >= 0.6) and near_std and continuous:
        return "race?", " ".join([near_std[0]] + tags), None
    if rel >= 1.15 and F["moving_s"] >= 900 and (hard is None or hard >= 0.35):
        return "tempo", " ".join(tags), None
    if F["dist_km"] >= 16 or F["moving_s"] >= 85 * 60:
        return "long", " ".join(tags), None
    return "easy", " ".join(tags), None


def mmss(s):
    s = int(round(s)); return f"{s // 60}:{s % 60:02d}"


def signature(ana):
    """'6×1000', '5×3:00', '3×1800 + 2×900': consecutive reps grouped by similar time or distance (±15 %);
    stray short reps (< 35 s, groups of <= 2: strides) are left out"""
    reps = list(zip(ana["rep_times"], ana["rep_dists"]))
    if not reps:
        return None
    sim = lambda x, y: abs(math.log(max(x, 1) / max(y, 1))) < 0.15
    groups = []
    for t_, d_ in reps:
        g = groups[-1] if groups else None
        if g and (sim(t_, np.median([x[0] for x in g])) or sim(d_, np.median([x[1] for x in g]))):
            g.append((t_, d_))
        else:
            groups.append([(t_, d_)])
    main = [g for g in groups if not (len(g) <= 2 and max(x[0] for x in g) < 35)]
    groups = main or groups
    if len(groups) > 3:
        n = sum(map(len, groups))
        tt = [x[0] for g in groups for x in g]
        return f"{n} reps (mixed {mmss(min(tt))}–{mmss(max(tt))})"
    parts = []
    for g in groups:
        ds = [x[1] for x in g]; d_ = float(np.median(ds)); t_ = float(np.median([x[0] for x in g]))
        r100 = round(d_ / 100) * 100
        steady = max(ds) - min(ds) <= 0.06 * d_
        if r100 >= 100 and steady and (ana["mode"] == "track" or abs(d_ - r100) <= 0.04 * d_):
            lab = f"{r100:.0f}"
        else:
            lab = mmss(round(t_ / 15) * 15)
        parts.append(f"{len(g)}×{lab}")
    return " + ".join(parts)


# ---------------------------------------------------------------- route alignment
def local_xy(lat, lon, o):
    kx = 111320 * math.cos(math.radians(o[0]))
    return (np.asarray(lon) - o[1]) * kx, (np.asarray(lat) - o[0]) * 111320


def ref_polyline(G):
    ok = G["gps"]
    idx = np.nonzero(ok)[0]
    la, lo = G["lat"][idx], G["lon"][idx]
    step = np.hypot(np.diff(la) * 111320, np.diff(lo) * 111320 * np.cos(np.radians(la[:-1])))
    cum = np.concatenate([[0], np.cumsum(step)])
    km = np.arange(0, cum[-1], REF_STEP)
    alt = np.interp(km, cum, G["alt"][idx]) if "alt" in G else np.zeros(len(km))
    return {"lat": np.interp(km, cum, la), "lon": np.interp(km, cum, lo), "m": km, "alt": alt, "L": float(cum[-1])}


def project(G, ref, laps=False):
    """progress along the reference (m, physical direction) per grid second, on-route mask, direction"""
    T = G["T"]; L = ref["L"]
    if laps or G["gps"] is None:
        tot = G["d"][-1] or 1
        return np.minimum(G["d"] / tot * L, L), np.ones(T + 1, bool), "fwd"
    o = (ref["lat"][0], ref["lon"][0])
    rx, ry = local_xy(ref["lat"], ref["lon"], o)
    mx, my = local_xy(G["lat"], G["lon"], o)
    first = int(np.argmax(G["gps"]))
    d0 = math.hypot(mx[first] - rx[0], my[first] - ry[0]); d1 = math.hypot(mx[first] - rx[-1], my[first] - ry[-1])

    def run(rev):
        if rev:
            x_, y_, rm = rx[::-1], ry[::-1], L - ref["m"][::-1]
        else:
            x_, y_, rm = rx, ry, ref["m"]
        n = len(x_)
        half = max(2, n // 2)
        dd = np.hypot(x_[:half] - mx[first], y_[:half] - my[first])
        cur = int(np.argmin(dd))
        if dd[cur] > 200:
            dd = np.hypot(x_ - mx[first], y_ - my[first]); cur = int(np.argmin(dd))
        prog = np.zeros(T + 1); on = np.zeros(T + 1, bool); best = rm[cur]; last_on = -1
        for i in range(T + 1):
            if G["gps"][i]:
                # search 200 m back .. as far ahead as plausible since the last on-route fix (40 m + 15 m/s):
                # keeps hairpins / switchbacks from jumping ahead along the reference
                ahead = 40 + 15 * (i - last_on if last_on >= 0 else 60)
                lo_ = max(0, cur - 10); hi_ = max(lo_ + 1, min(n, int(np.searchsorted(rm, rm[cur] + ahead, side="right"))))
                dd = np.hypot(x_[lo_:hi_] - mx[i], y_[lo_:hi_] - my[i])
                j = int(np.argmin(dd))
                if dd[j] <= ON_ROUTE_M:
                    cur = lo_ + j; on[i] = True; last_on = i
                    best = max(best, rm[cur])
            prog[i] = best
        onix = np.nonzero(on)[0]
        cover = prog[-1] - prog[onix[0]] if len(onix) else 0.0
        return (L - prog if rev else prog), on, cover

    # direction: the one covering more of the reference (loops, out & back); near-ties by start position
    f, r = run(False), run(True)
    prefer_rev = d1 + 100 < d0
    if r[2] > f[2] * 1.05 or (prefer_rev and r[2] >= f[2] * 0.95):
        return r[0], r[1], "rev"
    return f[0], f[1], "fwd"


def leg_name(a, b):
    return f"{a} → {b}"


def compare_route(rid, R, members, A, series_of, laps):
    """route-aligned comparison: reference = medoid, common waypoints, sessions × legs table"""
    ref_id = R["medoid"]
    Gref = series_of(ref_id)
    if Gref is None or Gref["gps"] is None:
        return None
    ref = ref_polyline(Gref)
    L = ref["L"]
    if L < 500:
        return None
    prog, info = {}, {}
    for mid in members:
        G = series_of(mid)
        if G is None:
            continue
        p, on, direc = project(G, ref, laps)
        P = find_pauses(G)
        prog[mid] = (G, p, on, P)
        info[mid] = direc
    # common stops: cluster pause positions (route m) over sessions
    pts = []
    for mid, (G, p, on, P) in prog.items():
        for a, b in P:
            m = (a + b) // 2
            if on[min(m, len(on) - 1)] or laps:
                pts.append((float(p[m]), mid))
    pts.sort()
    clusters = []
    for km, mid in pts:
        if clusters and km - clusters[-1][-1][0] <= WP_CLUSTER_M and km - clusters[-1][0][0] <= 2 * WP_CLUSTER_M:
            clusters[-1].append((km, mid))
        else:
            clusters.append([(km, mid)])
    nses = len(prog)
    wps = []
    if A[ref_id]["fam"] == "ski" and not laps:
        for c in clusters:
            share = len({m for _, m in c}) / nses
            km = float(np.median([k for k, _ in c]))
            if share >= WP_SHARE and len({m for _, m in c}) >= 2 and 250 < km < L - 250:
                wps.append({"m": km, "share": share})
        # drop waypoints closer than 300 m to a more common one
        wps.sort(key=lambda w: -w["share"])
        keep = []
        for w in wps:
            if all(abs(w["m"] - k["m"]) > 300 for k in keep):
                keep.append(w)
        wps = sorted(keep, key=lambda w: w["m"])
    equal = not wps
    if equal:
        nl = int(min(12, max(1, round(L / 1000))))
        wps = [{"m": L * i / nl, "share": None} for i in range(1, nl)]
    else:  # split long stretches between stops into ~2 km pieces
        cuts, prev = [], 0.0
        for m in [w["m"] for w in wps] + [L]:
            if m - prev > SPLIT_LEG_M:
                k = math.ceil((m - prev) / (SPLIT_LEG_M / 1.5))
                cuts += [{"m": prev + (m - prev) * i / k, "share": None} for i in range(1, k)]
            prev = m
        wps = sorted(wps + cuts, key=lambda w: w["m"])
    start_share = len({m for k, m in pts if k < WP_MATCH_M}) / nses
    end_share = len({m for k, m in pts if k > L - WP_MATCH_M}) / nses
    wps = [{"m": 0.0, "share": start_share}] + wps + [{"m": L, "share": end_share}]
    rlat, rlon, rm = ref["lat"], ref["lon"], ref["m"]
    for k, w in enumerate(wps):
        i = int(np.clip(np.searchsorted(rm, w["m"]), 0, len(rm) - 1))
        nm = known_near((rlat[i], rlon[i]), 400 if k in (0, len(wps) - 1) else 250)
        if k == 0:
            nm = nm or R["place_name"]
        elif w["share"] is None or nm == wps[0]["name"]:
            nm = None   # equal-distance split points are named by km
        w["name"] = nm or f"km {w['m'] / 1000:.1f}"
        w["lat"], w["lon"] = float(rlat[i]), float(rlon[i])
    nleg = len(wps) - 1
    up, dn = cum_climb(ref["alt"])
    bnd = np.array([w["m"] for w in wps])
    legs = []
    for k in range(nleg):
        i0 = int(np.searchsorted(rm, bnd[k])); i1 = min(len(rm) - 1, int(np.searchsorted(rm, bnd[k + 1])))
        legs.append({"from_km": round(bnd[k] / 1000, 2), "to_km": round(bnd[k + 1] / 1000, 2),
                     "climb_m": round(float(up[i1] - up[min(i0, i1)])), "desc_m": round(float(dn[i1] - dn[min(i0, i1)])),
                     "name": leg_name(wps[k]["name"], wps[k + 1]["name"])})
    sessions = []
    for mid, (G, p, on, P) in sorted(prog.items(), key=lambda kv: A[kv[0]]["date"]):
        T = G["T"]
        direc = info[mid]
        tp = p if direc == "fwd" else L - p          # travel-direction progress
        tb = bnd if direc == "fwd" else (L - bnd)[::-1]
        onix = np.nonzero(on)[0]
        if not len(onix):
            continue
        s_min, s_max = float(tp[onix[0]]), float(tp[-1])   # travel progress where the session joins / ends
        # travel window: from leaving the join point to reaching the furthest point
        t_from = int(np.argmax(tp > s_min + 20)) if (tp > s_min + 20).any() else T
        t_to = int(np.argmax(tp >= s_max - 30))
        w_first, w_last = (0, len(wps) - 1) if direc == "fwd" else (len(wps) - 1, 0)
        stops = [0] * len(wps)
        lab = np.full(T + 1, -1)   # -1 outside, k = leg k, 100+w = stop at waypoint w
        pause_mask = np.zeros(T + 1, bool)
        for a, b in P:
            pause_mask[a:b] = True
            m = (a + b) // 2
            dk = np.abs(bnd - p[m])
            w = int(np.argmin(dk))
            if b <= t_from + 1 and s_min <= 300:          # standing before the start
                w = w_first
            elif a >= t_to - 1 and s_max >= L - 300:       # standing after the finish
                w = w_last
            elif not (dk[w] <= WP_MATCH_M and (on[m] or laps)):
                continue
            stops[w] += b - a
            lab[a:b] = 100 + w
        legk = np.clip(np.searchsorted(bnd, p, side="right") - 1, 0, nleg - 1)
        inwin = np.zeros(T + 1, bool); inwin[t_from:t_to + 1] = True
        free = lab < 0
        lab[free & inwin] = legk[free & inwin]
        rows, n_bad = [], 0
        for k in range(nleg):
            u, w = (bnd[k], bnd[k + 1]) if direc == "fwd" else (L - bnd[k + 1], L - bnd[k])
            lm = min(w, s_max) - max(u, s_min)          # covered part of the leg
            sel = lab == k
            mov = int((sel & ~pause_mask).sum()); pau = int((sel & pause_mask).sum())
            if lm < LEG_COVER * (w - u) or mov < 10:
                rows.append(None); continue
            own = float(np.sum(np.diff(G["d"])[np.nonzero(sel[:-1])[0]]))
            if not OWN_RATIO[0] <= own / lm <= OWN_RATIO[1]:   # off the reference: own distance disagrees
                rows.append(None); n_bad += 1; continue
            hr = G.get("hr")
            hv = hr[sel & ~pause_mask] if hr is not None else np.array([])
            hv = hv[~np.isnan(hv)]
            idx = np.nonzero(sel)[0]
            climb = float(np.sum(np.clip(np.diff(G["up"])[idx[idx < T]], 0, None))) if "up" in G else None
            row = {"moving_s": mov, "pause_s": pau, "speed_kmh": round(lm / mov * 3.6, 2),
                   "hr": r1(hv.mean(), 0) if len(hv) > 5 else None, "climb_m": r1(climb, 0), "rank": None}
            if lm < 0.98 * (w - u):
                row["partial"] = round(lm / (w - u), 2)
            rows.append(row)
        summ = A[mid].get("summ") or {}
        sessions.append({"id": mid, "date": A[mid]["date"], "dir": direc, "type": A[mid]["stype"],
                         "elapsed": summ.get("elapsed"), "moving": summ.get("moving"), "pause": summ.get("pause"),
                         "legs": rows, "stops": stops, **({"legs_off_route": n_bad} if n_bad else {})})
        A[mid]["route_dir"] = direc if not R["shape"].startswith("loop") else R["shape"][5:]
    for k in range(nleg):
        sp = sorted((s["legs"][k]["speed_kmh"], i) for i, s in enumerate(sessions) if s["legs"][k])
        for r, (_, i) in enumerate(sorted(sp, reverse=True), 1):
            sessions[i]["legs"][k]["rank"] = r
    n = len(rlat); step = max(1, math.ceil(n / 300))
    sl = slice(0, n, step)
    refo = {"id": ref_id, "lat": [round(float(v), 5) for v in rlat[sl]], "lon": [round(float(v), 5) for v in rlon[sl]],
            "km": [round(float(v) / 1000, 2) for v in rm[sl]], "alt": [round(float(v)) for v in ref["alt"][sl]]}
    return {"ref": refo, "legs_mode": "equal" if equal else "stops",
            "waypoints": [{"km": round(w["m"] / 1000, 2), "name": w["name"], "lat": round(w["lat"], 5), "lon": round(w["lon"], 5),
                           "share_stopped": r1(w["share"], 2)} for w in wps],
            "legs_table": {"legs": legs, "sessions": sessions}}


def print_route(R):
    """text table: sessions × legs (speed km/h / moving m:ss, '+p' = pause inside the leg), stops per waypoint"""
    mm = lambda s: f"{int(s) // 60}:{int(s) % 60:02d}"
    Lt = R["legs_table"]
    print(f"{R['name']} ({R['n']} sessions, ref {R['ref']['id']}, legs by {R['legs_mode']})")
    print("waypoints: " + " | ".join(f"{w['name']} km {w['km']} ({w['share_stopped']})" for w in R["waypoints"]))
    print(f"{'leg':>26} " + " ".join(f"{k + 1:>11}" for k in range(len(Lt["legs"]))))
    print(f"{'km / climb m':>26} " + " ".join(f"{l['from_km']:.1f}-{l['to_km']:.1f}+{l['climb_m']:<3}".rjust(11) for l in Lt["legs"]))
    for s in Lt["sessions"]:
        cells = [(f"{x['speed_kmh']:.1f}/{mm(x['moving_s'])}" + ("+p" if x["pause_s"] else "  ")) if x else "-" for x in s["legs"]]
        print(f"{s['date']} {mm(s['elapsed']):>5} {mm(s['moving']):>5} {mm(s['pause']):>5} "
              + " ".join(c.rjust(11) for c in cells) + "  stops " + " ".join(f"{v:>3}" for v in s["stops"]))


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("person")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--show", nargs="*", metavar="ROUTE", help="print the comparison table of these route ids "
                    "(no id: the largest ski route)")
    args = ap.parse_args()
    T0 = time.time()
    D = ROOT / "data" / args.person
    ZF = zones_floor()
    idx = json.loads((D / "activities_index.json").read_text())
    fits = {int(p.stem): p for p in (D / "activities").glob("*/*.fit")}

    # rep detector cache (runs only; slow fitparse reader)
    cdir = D / "cache"; cdir.mkdir(exist_ok=True)
    cfile = cdir / "session_groups.pkl"
    cache = pickle.loads(cfile.read_bytes()) if cfile.exists() else {}
    if cache.get("_v") != CACHE_VERSION:
        cache = {"_v": CACHE_VERSION}
    types = {x["activityId"]: x["activityType"]["typeKey"] for x in idx}
    key = lambda p: (p.stat().st_mtime_ns, p.stat().st_size)
    todo = [str(p) for i, p in fits.items() if FAM.get(types.get(i)) == "run" and cache.get(str(p), {}).get("key") != key(p)]
    with Pool(args.workers) as pool:
        for fp, ana in pool.imap_unordered(ana_job, todo, chunksize=4):
            cache[fp] = {"key": key(Path(fp)), "ana": ana}
        if todo:
            cfile.write_bytes(pickle.dumps(cache))
        res = pool.map(worker, [(str(p), types.get(i), False) for i, p in fits.items()], chunksize=8)
    feats = {}
    for r in res:
        if "err" in r:
            continue
        c = cache.get(str(fits[r["id"]]), {}).get("ana")
        if c and "err" not in c:
            r["ana"] = c
        feats[r["id"]] = r
    print(f"read {len(feats)} FIT files ({len(todo)} rep analyses refreshed) {time.time() - T0:.1f}s")

    acts = []
    for x in idx:
        f = feats.get(x["activityId"])
        a = {"id": x["activityId"], "type": x["activityType"]["typeKey"], "date": x["startTimeLocal"][:10],
             "loc": x.get("locationName") or None, "x": x, "f": f}
        a["start"] = tuple(f["pts"][0]) if f and f["pts"] else (
            (x["startLatitude"], x["startLongitude"]) if x.get("startLatitude") else None)
        a["end"] = tuple(f["pts"][-1]) if f and f["pts"] else None
        a["fam"] = FAM.get(a["type"], "other")
        a["summ"] = f.get("summ") if f else None
        acts.append(a)
    A = {a["id"]: a for a in acts}
    for a in acts:
        a["F"] = features(a, ZF)
        a["gps"] = bool(a["f"] and a["f"]["pts"])
        a["F"]["dist_ok"] = a["F"]["dist_km"] >= 0.3 and (a["gps"] or a["type"] in ("treadmill_running", "indoor_running"))

    # ---- places / areas
    geo = [a for a in acts if a["start"]]
    Dm = dist_matrix([a["start"] for a in geo])
    plab, alab = leader(Dm, PLACE_R), single_link(Dm, AREA_R)
    over = {}
    pj = D / "places.json"
    if pj.exists():
        over = json.loads(pj.read_text() or "{}")
    places, rank = {}, collections.defaultdict(int)
    groups = collections.defaultdict(list)
    for a, l in zip(geo, plab):
        groups[l].append(a)
    cens = {l: tuple(float(v) for v in np.median([a["start"] for a in m], 0)) for l, m in groups.items()}
    # a known name goes to the one cluster nearest to it (within 600 m); before, every cluster within
    # 600 m got the same id and their entries in `places` overwrote each other
    known_of = {}
    for kn, q in KNOWN.items():
        d, l = min((hav(c, q), l) for l, c in cens.items())
        if d < 600:
            known_of[l] = kn
    for l, m in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        cen = cens[l]
        nm = known_of.get(l)
        if not nm:
            loc = collections.Counter(a["loc"] for a in m if a["loc"]).most_common(1)
            loc = loc[0][0] if loc else "?"
            rank[loc] += 1; nm = f"{loc}-{rank[loc]}"
        pid = nm
        for k, v in over.items():
            if k == pid:
                nm = v
            elif "," in k:
                try:
                    q = tuple(float(s) for s in k.split(","))
                    if hav(q, cen) <= PLACE_R:
                        nm = v
                except ValueError:
                    pass
        places[pid] = {"name": nm, "lat": round(cen[0], 5), "lon": round(cen[1], 5), "n": len(m),
                       "types": dict(collections.Counter(a["type"] for a in m).most_common())}
        for a in m:
            a["place"] = pid
    areas = {}
    agroups = collections.defaultdict(list)
    for a, l in zip(geo, alab):
        agroups[l].append(a)
    for k, (l, m) in enumerate(sorted(agroups.items(), key=lambda kv: -len(kv[1]))):
        aid = f"A{k + 1}"
        pc = collections.Counter(a["place"] for a in m)
        cen = np.median([a["start"] for a in m], 0)
        areas[aid] = {"name": places[pc.most_common(1)[0][0]]["name"], "lat": round(float(cen[0]), 4),
                      "lon": round(float(cen[1]), 4), "n": len(m), "places": [p for p, _ in pc.most_common()]}
        for a in m:
            a["area"] = aid

    # ---- routes
    tr = [a for a in acts if a["f"] and len(a["f"]["pts"]) > 10 and a.get("place")]
    for a in tr:
        seq = cells_of(a["f"]["pts"])
        a["cells"] = {c for c in seq if c}; a["dil"] = dilate(a["cells"])
        a["shape"] = shape_of(seq, a["f"]["pts"], a["F"]["dist_km"] * 1000)
        xs = [c[0] for c in a["cells"]]; ys = [c[1] for c in a["cells"]]
        a["bbox"] = (min(xs), max(xs), min(ys), max(ys))
    n = len(tr); C = np.zeros((n, n)); TI = {a["id"]: i for i, a in enumerate(tr)}
    for i in range(n):
        bi = tr[i]["bbox"]
        for j in range(i + 1, n):
            bj = tr[j]["bbox"]
            if bi[1] < bj[0] or bj[1] < bi[0] or bi[3] < bj[2] or bj[3] < bi[2]:
                continue
            ci = len(tr[i]["cells"] & tr[j]["dil"]) / len(tr[i]["cells"])
            if ci == 0:
                continue
            C[i, j] = ci; C[j, i] = len(tr[j]["cells"] & tr[i]["dil"]) / len(tr[j]["cells"])
    Smin = np.minimum(C, C.T); np.fill_diagonal(Smin, 1)
    dk = np.array([max(a["F"]["dist_km"], 0.1) for a in tr])
    ok = np.abs(np.log(dk[:, None] / dk[None, :])) <= math.log(ROUTE_DIST_RATIO)
    fam = np.array([a["fam"] for a in tr])
    S = Smin * ok * (fam[:, None] == fam[None, :])
    cl = [[i] for i in range(n)]; M = S.copy(); np.fill_diagonal(M, -1); size = np.ones(n); alive = np.ones(n, bool)
    while n:
        Mm = np.where(alive[:, None] & alive[None, :], M, -1)
        i, j = np.unravel_index(np.argmax(Mm), Mm.shape)
        if Mm[i, j] < ROUTE_SIM:
            break
        M[i, :] = (M[i, :] * size[i] + M[j, :] * size[j]) / (size[i] + size[j]); M[:, i] = M[i, :]; M[i, i] = -1
        size[i] += size[j]; alive[j] = False; cl[i] += cl[j]; cl[j] = []
    cl = sorted((c for c in cl if len(c) >= 2), key=lambda c: (-len(c), tr[c[0]]["date"]))
    routes = {}
    SHAPE_LAB = {"laps": "laps", "out_and_back": "out & back", "point_to_point": "point to point",
                 "loop_cw": "loop", "loop_ccw": "loop"}
    for k, c in enumerate(cl):
        rid = f"R{k + 1}"
        m = [tr[i] for i in c]
        shape = collections.Counter(a["shape"] for a in m).most_common(1)[0][0]
        pl = collections.Counter(a["place"] for a in m).most_common(1)[0][0]
        dkm = float(np.median([a["F"]["dist_km"] for a in m]))
        med = c[int(np.argmax(Smin[np.ix_(c, c)].sum(1)))]
        pname = places[pl]["name"]
        if shape == "point_to_point":
            ends = [a["end"] for a in m if a["end"]]
            e = tuple(np.median(ends, 0))
            en = known_near(e, 600)
            if not en:
                de = [(hav(e, (p["lat"], p["lon"])), p["name"]) for p in places.values()]
                d_, en = min(de)
                en = en if d_ < PLACE_R else None
            name = f"{pname} → {en or '?'} {dkm:.1f} km"
        else:
            name = f"{pname} {SHAPE_LAB[shape]} {dkm:.1f} km"
        routes[rid] = {"place": pl, "place_name": pname, "name": name, "sport": m[0]["fam"], "shape": shape,
                       "dist_km": round(dkm, 2), "n": len(m), "medoid": tr[med]["id"],
                       "members": [a["id"] for a in sorted(m, key=lambda a: a["date"])]}
        for a in m:
            a["route"] = rid
            if shape.startswith("loop"):
                a["route_dir"] = a["shape"][5:] if a["shape"].startswith("loop") else None

    # ---- session types
    dord = {a["id"]: dt.date.fromisoformat(a["date"]).toordinal() for a in acts}
    run_sp = [(dord[b["id"]], b["F"]["speed"]) for b in acts
              if b["fam"] == "run" and b["F"]["speed"] and b["F"]["dist_km"] >= 3]
    rs_d = np.array([d for d, _ in run_sp]); rs_v = np.array([v for _, v in run_sp])

    def easy_ref(a):  # median speed of own runs within +-180 days (pace scale drifts over the years)
        m = np.abs(rs_d - dord[a["id"]]) <= 180
        return float(np.median(rs_v[m])) if m.sum() >= 5 else 3.2

    for a in acts:
        a["stype"], a["detail"], a["surface"] = classify(a, ZF, easy_ref)
        ana = (a["f"] or {}).get("ana")
        a["sig"] = signature(ana) if ana and a["stype"].startswith("intervals") else None

    # ---- similarity
    def comps(q, c):
        s = {}
        i, j = TI.get(q["id"]), TI.get(c["id"])
        if i is not None and j is not None:
            s["route"] = max(0.5 * (C[i, j] + C[j, i]), 0.35 if q["place"] == c["place"] else 0,
                             0.15 if q["area"] == c["area"] else 0)
        elif q.get("place") and c.get("place"):
            s["route"] = 0.5 if q["place"] == c["place"] else (0.2 if q["area"] == c["area"] else 0)
        s["type"] = 1.0 if q["stype"] == c["stype"] else (0.5 if frozenset((q["stype"], c["stype"])) in RELATED else 0.0)
        qF, cF = q["F"], c["F"]
        if qF["dist_ok"] and cF["dist_ok"]:
            s["dist"] = math.exp(-abs(math.log(qF["dist_km"] / cF["dist_km"])) / 0.2)
            s["elev"] = math.exp(-abs(qF["elev_per_km"] - cF["elev_per_km"]) / 10)
        s["dur"] = math.exp(-abs(math.log(max(qF["moving_s"], 60) / max(cF["moving_s"], 60))) / 0.25)
        if qF.get("rel_speed") and cF.get("rel_speed"):
            s["pace"] = math.exp(-abs(math.log(qF["rel_speed"] / cF["rel_speed"])) / 0.1)
        elif q["fam"] != "run" and qF["dist_ok"] and cF["dist_ok"] and qF["speed"] and cF["speed"]:
            s["pace"] = math.exp(-abs(math.log(qF["speed"] / cF["speed"])) / 0.1)
        if qF["zones"] and cF["zones"]:
            s["intensity"] = 1 - 0.5 * sum(abs(u - v) for u, v in zip(qF["zones"], cF["zones"]))
        if q["stype"].startswith("intervals") and c["stype"].startswith("intervals") and qF.get("rep_med_s") and cF.get("rep_med_s"):
            s["structure"] = math.exp(-abs(math.log(qF["rep_med_s"] / max(cF["rep_med_s"], 1))) / 0.4) * \
                math.exp(-abs(math.log(qF["rep_time"] / max(cF["rep_time"], 1))) / 0.5)
        doy = abs(q["doy"] - c["doy"]); doy = min(doy, 365 - doy)
        s["season"] = 0.5 + 0.5 * math.cos(math.pi * doy / 182.5)
        s["recency"] = math.exp(-abs(dord[q["id"]] - dord[c["id"]]) / 730)
        return s

    for a in acts:
        a["doy"] = dt.date.fromisoformat(a["date"]).timetuple().tm_yday
    byfam = collections.defaultdict(list)
    for a in acts:
        byfam[a["fam"] if a["fam"] != "other" else "other:" + a["type"]].append(a)
    for q in acts:
        q["similar"] = {}
        cand = [c for c in byfam[q["fam"] if q["fam"] != "other" else "other:" + q["type"]] if c["id"] != q["id"]]
        cs = [(comps(q, c), c) for c in cand]
        for preset, W0 in SIM_W.items():
            W = dict(W0)
            if q["stype"] == "race?":
                W["dist"] *= 3; W["route"] *= 0.5
            out = []
            for s, c in cs:
                keys = [k for k in W if k != "structure" or "structure" in s]
                sc = sum(W[k] * s.get(k, 0.4) for k in keys) / sum(W[k] for k in keys)
                out.append((sc, s, c))
            out.sort(key=lambda t: -t[0])
            q["similar"][preset] = [{"id": c["id"], "score": round(sc, 2),
                                     "reasons": ", ".join(f"{k} {v:.2f}".rstrip("0").rstrip(".") for k, v in sorted(s.items(), key=lambda kv: -W[kv[0]] * kv[1])[:3])}
                                    for sc, s, c in out[:5]]
    print(f"groups+types+similar {time.time() - T0:.1f}s")

    # ---- route comparisons
    gcache = {}

    def series_of(i):
        if i not in gcache:
            try:
                gcache[i] = to_grid(read_series(fits[i])) if i in fits else None
            except Exception:
                gcache[i] = None
        return gcache[i]

    for rid, R in routes.items():
        if R["n"] < MIN_ROUTE_N:
            continue
        cmp_ = compare_route(rid, R, R["members"], A, series_of, R["shape"] == "laps")
        if cmp_:
            R.update(cmp_)
        gcache.clear()
    print(f"route comparisons {time.time() - T0:.1f}s")

    # ---- output
    out_acts = {}
    for a in acts:
        f = a["f"] or {}
        sm = a["summ"] or {}
        o = {"date": a["date"], "sport": a["fam"], "atype": a["type"], "type": a["stype"], "type_detail": a["detail"] or None,
             "place": a.get("place"), "area": a.get("area"), "route": a.get("route"), "route_dir": a.get("route_dir"),
             "shape": a.get("shape"), "dist_km": round(a["F"]["dist_km"], 2),
             "dur_s": sm.get("elapsed") or a["x"].get("elapsedDuration") and round(a["x"]["elapsedDuration"]),
             "moving_s": sm.get("moving") if sm else (round(a["F"]["moving_s"]) if a["F"]["moving_s"] else None),
             "pause_s": sm.get("pause"), "n_pauses": sm.get("n_pauses"), "longest_pause_s": sm.get("longest"),
             "sig": a["sig"], "surface": a["surface"], "dist_trusted": a["F"]["dist_ok"]}
        if f.get("legs") is not None:
            o["legs"] = f["legs"]; o["pauses"] = f["pauses"]
        ana = f.get("ana")
        if a["sig"] and ana:
            o["reps"] = [[r1(t_, 1), r1(d_, 0), r1(v_, 2), h_] for t_, d_, v_, h_ in
                         zip(ana["rep_times"], ana["rep_dists"], ana["rep_speeds"], ana["rep_hr"])]
            o["breaks_s"] = [r1(b[0], 0) for b in ana["breaks"]]
        o["similar"] = a["similar"]
        out_acts[str(a["id"])] = {k: v for k, v in o.items() if v is not None}
    for R in routes.values():
        R.pop("place_name", None)
    doc = {"built": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "params": {"place_r_m": PLACE_R, "area_r_m": AREA_R, "cell_m": CELL, "route_sim": ROUTE_SIM,
                      "route_dist_ratio": ROUTE_DIST_RATIO, "stop_speed_ms": STOP_V, "min_pause_s": MIN_PAUSE_S,
                      "waypoint_cluster_m": WP_CLUSTER_M, "waypoint_share": WP_SHARE, "similarity_weights": SIM_W},
           "fields": FIELDS, "places": places, "areas": areas, "routes": routes, "activities": out_acts}
    js = json.dumps(doc, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    (D / "groups.json").write_text(js)
    print(f"wrote {D / 'groups.json'} ({len(js) / 1e6:.2f} MB): {len(places)} places, {len(areas)} areas, "
          f"{len(routes)} routes (>=2), {sum(1 for r in routes.values() if 'legs_table' in r)} compared; "
          f"types {dict(collections.Counter(a['stype'] for a in acts).most_common())}; {time.time() - T0:.1f}s")
    if args.show is not None:
        ids = args.show or [max((r for r in routes if routes[r]["sport"] == "ski" and "legs_table" in routes[r]),
                                key=lambda r: routes[r]["n"])]
        for rid in ids:
            print(); print_route(routes[rid])


if __name__ == "__main__":
    main()

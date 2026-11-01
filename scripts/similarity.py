"""Activity similarity engine: places / regions / routes / workout hierarchy, filtered search and
"top-k closest activities" with per-component reasons. Library for the API + a small CLI.

    uv run scripts/similarity.py adrian build          # -> data/<p>/similarity_index.json + tracks.json
    uv run scripts/similarity.py adrian search --sig 6×1000 --rest 120-200
    uv run scripts/similarity.py adrian search --family run/intervals --rep-count 5-7 --rep-dist 900-1100
    uv run scripts/similarity.py adrian nearest 24874084265 -k 10 --preset workout
    uv run scripts/similarity.py adrian groups place|start_place|region|route|family|signature|exact|workout
    uv run scripts/similarity.py adrian validate

    import similarity as sim
    ix = sim.load_index("adrian")                    # Index (records in ix.acts, keyed by int id)
    ix.search({"signature": "6×1000", "rest_s": [120, 200]})
    ix.nearest(24874084265, k=10, weights={"workout": 0.5, "pace": 0.2})
    ix.groups("exact")

Builds on data/<p>/groups.json (session_groups.py: start places, areas, routes, session types,
interval signatures, reps, breaks) and re-reads the FIT tracks (fast reader) for where the activity
actually happens: time share in named places / regions, encoded polylines and 75 m route cells.
Docs: docs/similarity.md.
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
import session_groups as sg  # noqa: E402  (read-only use: FIT reader, helpers, signature logic)

ROOT = Path(__file__).resolve().parent.parent
INDEX_VERSION = 2   # 2: place / start_place / end_place / route places are ids, + *_name display names
TRACK_CACHE_VERSION = 1

# ---------------------------------------------------------------- geography
# Named places: small spots, circle (lat, lon, r_m) or polygon [(lat, lon), ...]. "kind" is informative,
# "name" = display name when it differs from the id (the id is what search filters, URLs and other scripts use).
# Matched by time share (where the activity happens), not just the start point.
# Extend / override per person in data/<p>/regions.json {"places": {...}, "regions": {...}} (same format).
NAMED_PLACES = {
    "Bislet": {"kind": "track", "circle": (59.9253, 10.7331, 130), "name": "Bislett"},   # id stays "Bislet"
    "Frognerparken": {"kind": "park", "poly": [(59.9310, 10.6960), (59.9310, 10.7075), (59.9232, 10.7075), (59.9232, 10.6960)]},
    "St. Hanshaugen": {"kind": "park", "circle": (59.9266, 10.7408, 230)},
    "Sognsvann": {"kind": "lake", "circle": (59.9718, 10.7290, 700)},
    "Holmenkollen": {"kind": "hill", "circle": (59.9635, 10.6650, 450)},
    "Frognerseteren": {"kind": "hill", "circle": (59.9785, 10.6800, 300)},
    "NTNU Gløshaugen": {"kind": "campus", "circle": (63.4190, 10.4040, 400)},
    "Granåsen": {"kind": "ski arena", "circle": (63.3800, 10.3130, 600)},
}
# Regions: large areas (polygons, rough outlines, ±300 m). Region of an activity = the one it spends
# the most time in; fallback = Garmin locationName (municipality).
REGIONS = {
    "Marka": {"kind": "forest", "poly": [  # Nordmarka + Lillomarka + northern Bærumsmarka, south edge ~ city limit
        (59.990, 10.440), (59.958, 10.560), (59.955, 10.610), (59.959, 10.640), (59.962, 10.662), (59.968, 10.690),
        (59.968, 10.715), (59.966, 10.735), (59.963, 10.765), (59.958, 10.790), (59.950, 10.810), (59.952, 10.860),
        (59.975, 10.900), (60.250, 10.950), (60.250, 10.400)]},
    "Østmarka": {"kind": "forest", "poly": [(59.935, 10.840), (59.935, 10.990), (59.800, 10.990), (59.800, 10.880), (59.860, 10.840)]},
    "Bymarka": {"kind": "forest", "poly": [(63.445, 10.200), (63.445, 10.330), (63.410, 10.345), (63.370, 10.330), (63.370, 10.200)]},
}
PLACE_MIN_SHARE = 0.05       # a named place / region counts as touched with >= 5 % of the time (or >= 3 min)
PLACE_MIN_S = 180
MAIN_SHARE = 0.4             # main place = named place with >= 40 % of the time, else the start place

# ---------------------------------------------------------------- similarity
COMPONENTS = ["route", "place", "workout", "pace", "hr", "distance", "duration", "climb", "season", "recency"]
PRESETS = {
    "balanced": {"route": 0.2, "place": 0.1, "workout": 0.25, "pace": 0.1, "hr": 0.05, "distance": 0.1,
                 "duration": 0.05, "climb": 0.05, "season": 0.05, "recency": 0.05},
    "workout": {"route": 0.05, "place": 0.05, "workout": 0.5, "pace": 0.15, "hr": 0.05, "distance": 0.05,
                "duration": 0.05, "climb": 0.0, "season": 0.05, "recency": 0.05},
    "route": {"route": 0.5, "place": 0.1, "workout": 0.1, "pace": 0.05, "hr": 0.0, "distance": 0.15,
              "duration": 0.0, "climb": 0.05, "season": 0.05, "recency": 0.0},
    "place": {"route": 0.15, "place": 0.5, "workout": 0.1, "pace": 0.05, "hr": 0.0, "distance": 0.1,
              "duration": 0.0, "climb": 0.0, "season": 0.05, "recency": 0.05},
}
MISSING = 0.4                # component score used when a component can't be computed for a pair

OVERVIEW_PTS = 200           # overview polyline (index) <= 200 points
DETAIL_TOL_M = 3.0           # tracks.json: Douglas-Peucker tolerance

KIND_LABEL = {"intervals": "Interval running", "tempo": "Tempo run", "race": "Race", "long": "Long run",
              "easy": "Easy run", "other": "Other run", "nogps": "Run without GPS"}
SPORT_LABEL = {"hike": "Hike", "cycling": "Cycling", "lap_swimming": "Pool swim", "open_water_swimming": "Open water swim",
               "rowing_v2": "Rowing", "kayaking_v2": "Kayaking", "stop_watch": "Stopwatch (no GPS)",
               "multi_sport": "Multisport", "other": "Other", "run": "Run", "ski": "Ski"}
# readable labels per Garmin activity type (the app's sport chips are run / ski / hike / bike / swim / other)
ATYPE_LABEL = {"running": "Run", "track_running": "Track run", "treadmill_running": "Treadmill run",
               "indoor_running": "Indoor run", "trail_running": "Trail run", "street_running": "Road run",
               "cross_country_skiing_ws": "Cross-country ski", "skate_skiing_ws": "Skate ski",
               "backcountry_skiing": "Backcountry ski", "hiking": "Hike", "walking": "Walk", "cycling": "Ride",
               "road_biking": "Road ride", "mountain_biking": "MTB ride", "indoor_cycling": "Indoor ride",
               "lap_swimming": "Pool swim", "open_water_swimming": "Open water swim", "rowing_v2": "Rowing",
               "kayaking_v2": "Kayaking", "stop_watch": "Stopwatch (no GPS)", "multi_sport": "Multisport", "other": "Other"}


def atype_label(atype):
    """readable label for a Garmin typeKey ('lap_swimming' -> 'Pool swim'); unknown keys are humanised"""
    if not atype:
        return None
    return ATYPE_LABEL.get(atype) or atype.replace("_v2", "").replace("_ws", "").replace("_", " ").capitalize()


# ---------------------------------------------------------------- small helpers
def _xy(lat, lon, o):
    kx = 111320 * math.cos(math.radians(o[0]))
    return (np.asarray(lon, float) - o[1]) * kx, (np.asarray(lat, float) - o[0]) * 111320


def dp_simplify(lat, lon, tol):
    """Douglas-Peucker (iterative) on local metres; returns kept indices"""
    n = len(lat)
    if n <= 2:
        return list(range(n))
    x, y = _xy(lat, lon, (lat[0], lon[0]))
    keep = np.zeros(n, bool); keep[0] = keep[-1] = True
    st = [(0, n - 1)]
    while st:
        a, b = st.pop()
        if b - a < 2:
            continue
        dx, dy = x[b] - x[a], y[b] - y[a]
        L = math.hypot(dx, dy)
        xs, ys = x[a + 1:b] - x[a], y[a + 1:b] - y[a]
        if L < 1e-6:
            d = np.hypot(xs, ys)
        else:
            d = np.abs(xs * dy - ys * dx) / L
        i = int(np.argmax(d))
        if d[i] > tol:
            m = a + 1 + i; keep[m] = True
            st += [(a, m), (m, b)]
    return np.nonzero(keep)[0].tolist()


def _enc_num(v):
    v = ~(v << 1) if v < 0 else (v << 1)
    out = []
    while v >= 0x20:
        out.append(chr((0x20 | (v & 0x1F)) + 63)); v >>= 5
    out.append(chr(v + 63))
    return "".join(out)


def encode_ints(seq):
    """Google polyline varint encoding of a delta-coded integer sequence"""
    out, prev = [], 0
    for v in seq:
        out.append(_enc_num(int(v) - prev)); prev = int(v)
    return "".join(out)


def encode_pairs(pairs):
    """[(x, y)] int pairs, delta-coded per coordinate (same varint as polylines); decode_ints_pairs inverts"""
    out, pa, pb = [], 0, 0
    for a, b in pairs:
        out.append(_enc_num(int(a) - pa) + _enc_num(int(b) - pb)); pa, pb = int(a), int(b)
    return "".join(out)


def decode_ints(s):
    out, i, prev = [], 0, 0
    while i < len(s):
        res, sh = 0, 0
        while True:
            b = ord(s[i]) - 63; i += 1
            res |= (b & 0x1F) << sh; sh += 5
            if b < 0x20:
                break
        prev += ~(res >> 1) if res & 1 else (res >> 1)
        out.append(prev)
    return out


def encode_polyline(lat, lon, prec=5):
    """standard Google encoded polyline (precision 5 = ~1 m), as used by Leaflet / Mapbox plugins"""
    f = 10 ** prec
    out, pa, po = [], 0, 0
    for la, lo in zip(lat, lon):
        a, b = int(round(la * f)), int(round(lo * f))
        out.append(_enc_num(a - pa) + _enc_num(b - po)); pa, po = a, b
    return "".join(out)


def decode_polyline(s, prec=5):
    v = decode_ints_pairs(s)
    f = 10 ** prec
    return [(a / f, b / f) for a, b in v]


def decode_ints_pairs(s):
    vals, i = [], 0
    while i < len(s):
        res, sh = 0, 0
        while True:
            b = ord(s[i]) - 63; i += 1
            res |= (b & 0x1F) << sh; sh += 5
            if b < 0x20:
                break
        vals.append(~(res >> 1) if res & 1 else (res >> 1))
    out, a, b = [], 0, 0
    for k in range(0, len(vals) - 1, 2):
        a += vals[k]; b += vals[k + 1]; out.append((a, b))
    return out


def pip(lat, lon, poly):
    """vectorised point in polygon (ray casting) for arrays lat/lon"""
    lat = np.asarray(lat, float); lon = np.asarray(lon, float)
    ins = np.zeros(len(lat), bool)
    n = len(poly)
    for i in range(n):
        y1, x1 = poly[i]; y2, x2 = poly[(i + 1) % n]
        c = ((y1 > lat) != (y2 > lat))
        with np.errstate(divide="ignore", invalid="ignore"):
            xint = (x2 - x1) * (lat - y1) / (y2 - y1) + x1
        ins ^= c & (lon < xint)
    return ins


def in_shape(lat, lon, shp):
    if "circle" in shp:
        la, lo, r = shp["circle"]
        x, y = _xy(lat, lon, (la, lo))
        return np.hypot(x, y) <= r
    return pip(lat, lon, shp["poly"])


def shape_centre(shp):
    if "circle" in shp:
        return shp["circle"][0], shp["circle"][1]
    p = np.array(shp["poly"]); return float(p[:, 0].mean()), float(p[:, 1].mean())


def mmss(s):
    s = int(round(s)); return f"{s // 60}:{s % 60:02d}"


def fmt_pace(v):  # m/s -> min/km
    return mmss(1000 / v) + "/km" if v else "?"


def fmt_speed(v, sport):
    """runs as min/km, pool swims as /100 m, everything else (ski, hike, bike, ...) as km/h like the app"""
    if not v:
        return "?"
    if sport == "run":
        return fmt_pace(v)
    if sport in ("lap_swimming", "open_water_swimming"):
        return mmss(100 / v) + "/100 m"
    return f"{v * 3.6:.1f} km/h"


# ---------------------------------------------------------------- per-FIT track (worker)
def track_job(fit):
    """GPS track thinned to >= 10 m steps (keeps t, alt): lat, lon, t, alt arrays"""
    try:
        s = sg.read_series(Path(fit))
    except Exception as e:
        return fit, {"err": str(e)[:100]}
    ok = ~np.isnan(s["lat"])
    if ok.sum() < 2:
        return fit, {"pts": 0}
    la, lo, t, al = s["lat"][ok], s["lon"][ok], s["t"][ok], s["alt"][ok]
    # drop isolated wild fixes (> 50 m/s from both neighbours)
    keep, last = [0], 0
    for i in range(1, len(la)):
        d = sg.hav((la[last], lo[last]), (la[i], lo[i]))
        if d / max(t[i] - t[last], 1) > 50:
            continue
        if d >= 10 or t[i] - t[last] >= 30:
            keep.append(i); last = i
    if keep[-1] != len(la) - 1 and sg.hav((la[keep[-1]], lo[keep[-1]]), (la[-1], lo[-1])) / max(t[-1] - t[keep[-1]], 1) <= 50:
        keep.append(len(la) - 1)
    k = np.array(keep)
    return fit, {"lat": la[k].astype(np.float64), "lon": lo[k].astype(np.float64), "t": t[k].astype(np.float32),
                 "alt": al[k].astype(np.float32), "pts": len(k)}


def load_tracks(D, fits, workers=8):
    cfile = D / "cache" / "similarity_tracks.pkl"
    cache = pickle.loads(cfile.read_bytes()) if cfile.exists() else {}
    if cache.get("_v") != (TRACK_CACHE_VERSION, sg.READER_VERSION):
        cache = {"_v": (TRACK_CACHE_VERSION, sg.READER_VERSION)}
    key = lambda p: (p.stat().st_mtime_ns, p.stat().st_size)
    todo = [str(p) for p in fits.values() if cache.get(str(p), {}).get("key") != key(p)]
    if todo:
        with Pool(workers) as pool:
            for fp, tr in pool.imap_unordered(track_job, todo, chunksize=8):
                cache[fp] = {"key": key(Path(fp)), "tr": tr}
        cfile.parent.mkdir(exist_ok=True)
        cfile.write_bytes(pickle.dumps(cache))
    return {i: cache[str(p)]["tr"] for i, p in fits.items()}, len(todo)


# ---------------------------------------------------------------- workout structure
def rep_parts(reps, ski=False):
    """reps [[time_s, dist_m, ...]] -> parts [index lists] grouped like session_groups.signature
    (session_groups.rep_groups: runs by similar time or distance, +-15 %, strides dropped; ski by duration)"""
    return sg.rep_groups([r[0] for r in reps], [r[1] for r in reps], ski)


def rest_label(s):
    """rest bucket: 15 s steps below 1:00, 30 s to 3:00, 1:00 to 6:00, then 2:00"""
    if s is None:
        return None
    step = 15 if s < 60 else 30 if s < 180 else 60 if s < 360 else 120
    return mmss(max(step, round(s / step) * step))


def dist_band(sport, km):
    bands = {"run": [5, 8, 12, 16, 21, 30], "ski": [10, 20, 30, 45], "hike": [5, 10, 20],
             "lap_swimming": [1, 1.5, 2, 3], "open_water_swimming": [1, 2, 4]}.get(sport, [10, 30, 60])
    lo = 0
    for b in bands:
        if km < b:
            return f"{lo}–{b} km"
        lo = b
    return f"{lo}+ km"


FAMILY = {"intervals_track": "intervals", "intervals_road": "intervals", "intervals_treadmill": "intervals",
          "tempo": "tempo", "race?": "race", "long": "long", "easy": "easy", "run_nogps": "nogps",
          "run_other": "other", "ski_easy": "easy", "ski_hard": "hard", "ski_long": "long", "ski_long_race?": "long",
          "ski_intervals": "intervals"}
RELATED_FAM = {frozenset(p) for p in [("run/intervals", "run/tempo"), ("run/tempo", "run/race"), ("run/easy", "run/long"),
                                      ("ski/easy", "ski/long"), ("ski/hard", "ski/long"), ("run/easy", "run/nogps"),
                                      ("ski/intervals", "ski/hard")]}


def workout_of(o, sport, ana, dist_km):
    """hierarchy family > signature > exact_key, plus rep / rest stats for intervals"""
    st = o.get("type") or "?"
    kind = FAMILY.get(st)
    fam = f"{sport}/{kind}" if kind and sport in ("run", "ski") else sport
    label = (KIND_LABEL.get(kind, kind) if sport == "run" else f"{sport.capitalize()} {kind}" if kind
             else SPORT_LABEL.get(sport, sport.replace("_v2", "").replace("_", " ").capitalize()))
    w = {"family": fam, "family_label": label, "category": st, "detail": o.get("type_detail")}
    reps = o.get("reps")
    if o.get("sig") and reps:
        ski_mode = str((ana or {}).get("mode") or "").startswith("ski")
        parts = rep_parts(reps, ski_mode)
        brk = o.get("breaks_s") or []
        # Bislett indoor: reps come from laps (groups.json), the cached speed-detector breaks don't match them
        sbrk = [b for b in (ana or {}).get("breaks", [])] if ana and not o.get("indoor_bislett") else []
        P = []
        for g in parts:
            ts = [reps[j][0] for j in g]; ds = [reps[j][1] for j in g if reps[j][1]]   # None: no rep distance (indoor)
            sp = [reps[j][2] for j in g if reps[j][2]]; hr = [reps[j][3] for j in g if reps[j][3]]
            rests = [brk[j] for j in g[:-1] if j < len(brk) and j + 1 in g]
            stand = [min(1.0, sbrk[j][2] / sbrk[j][0]) for j in g[:-1] if j < len(sbrk) and j + 1 in g and sbrk[j][0] > 0]
            jog = [sbrk[j][1] for j in g[:-1] if j < len(sbrk) and j + 1 in g]
            P.append({"n": len(g), "dist_m": round(float(np.median(ds))) if ds else None, "time_s": round(float(np.median(ts)), 1),
                      "speed_ms": round(float(np.median(sp)), 2) if sp else None,
                      "hr": round(float(np.mean(hr))) if hr else None,
                      "rest_s": round(float(np.median(rests))) if rests else None,
                      "rest_min_s": min(rests) if rests else None, "rest_max_s": max(rests) if rests else None,
                      "rest_stand_share": round(float(np.mean(stand)), 2) if stand else None,
                      "rest_dist_m": round(float(np.median(jog))) if jog else None, "total_s": sum(ts)})
        main = max(range(len(P)), key=lambda k: P[k]["total_s"])
        M = P[main]
        mode = None
        if M["rest_stand_share"] is not None:
            mode = "stand" if M["rest_stand_share"] >= 0.6 else "jog" if M["rest_stand_share"] <= 0.2 else "walk/mixed"
        sig = o["sig"]
        rl = rest_label(M["rest_s"])
        g = parts[main]   # main part alone, labelled like session_groups.signature ('1×800 + 5×1000' -> '5×1000')
        main_sig = sg.signature({"rep_times": [reps[j][0] for j in g], "rep_dists": [reps[j][1] for j in g],
                                 "mode": (ana or {}).get("mode", "gps")}) if len(P) > 1 else sig
        w.update(signature=sig, main_sig=main_sig, exact_key=f"{sig} · rest {rl}" if rl else sig, parts=P, main_part=main,
                 rep_count=M["n"], reps_total=len(reps), rep_dist_m=M["dist_m"], rep_time_s=M["time_s"],
                 rep_speed_ms=M["speed_ms"], rep_hr=M["hr"], rest_s=M["rest_s"], rest_min_s=M["rest_min_s"],
                 rest_max_s=M["rest_max_s"], rest_label=rl, rest_mode=mode, rest_stand_share=M["rest_stand_share"], rest_dist_m=M["rest_dist_m"],
                 work_s=round(sum(r[0] for r in reps)))
        if ski_mode and (ana or {}).get("mode") == "ski_effort" and o.get("route"):
            # terrain-defined ski reps (climbs between the stops of a route): the route is the workout, the rep
            # durations follow the terrain and vary between sessions -> exact_key by route like other ski sessions
            w["exact_key"] = f"{fam} · {o['route']}"; w["exact_by"] = "route"
        for p in P:
            p.pop("total_s")
    else:
        w["signature"] = f"{label} {dist_band(sport, dist_km)}" if dist_km and o.get("dist_trusted") else label
        w["exact_key"] = f"{fam} · {o['route']}" if o.get("route") else None
    return w


REST_GAP_S, REST_GAP_REL, REST_SPREAD = 20, 0.2, 1.5


def rest_clusters(vals):
    """1-D clusters of rest medians: split where consecutive sorted values differ by > max(20 s, 20 %),
    then split any cluster spanning more than 1.5x at its largest gap. -> list of sorted value lists"""
    v = sorted(vals)
    if not v:
        return []
    cl = [[v[0]]]
    for x in v[1:]:
        if x - cl[-1][-1] > max(REST_GAP_S, REST_GAP_REL * cl[-1][-1]):
            cl.append([x])
        else:
            cl[-1].append(x)
    out = []
    while cl:
        c = cl.pop(0)
        if len(c) > 1 and c[-1] / max(c[0], 1) > REST_SPREAD:
            i = int(np.argmax(np.diff(c))) + 1
            cl[:0] = [c[:i], c[i:]]
        else:
            out.append(c)
    return out


def assign_rest_clusters(acts):
    """exact_key for intervals = signature + rest cluster (within family + signature) labelled by its median"""
    by = collections.defaultdict(list)
    for a in acts.values():
        w = a["workout"]
        if w.get("rest_s") is not None and w.get("parts") and w.get("exact_by") != "route":
            by[(w["family"], w["signature"])].append(a)   # per family: a run 4×4:00 and a ski 4×4:00 are different
    for (_, sig), m in by.items():
        used = set()
        for c in rest_clusters([a["workout"]["rest_s"] for a in m]):
            med = float(np.median(c))
            lab = rest_label(med)
            while lab in used:
                lab += "'"
            used.add(lab)
            for a in m:
                w = a["workout"]
                if c[0] <= w["rest_s"] <= c[-1]:
                    w["exact_key"] = f"{sig} · rest ~{lab}"
                    w["rest_cluster"] = [c[0], c[-1]]
                    w["rest_group_s"] = round(med)   # median rest of the group (label "~lab"); rest_s = this session


# ---------------------------------------------------------------- build
def touched_places(a):
    """every place id an activity counts for in a place search / touched group: main, start, end, touched"""
    return ({a.get("place"), a.get("start_place"), a.get("end_place")} | {p["name"] for p in a.get("places", [])}) - {None}


def touched_regions(a):
    """every region an activity counts for in a region search / touched group: main, Garmin municipality, touched"""
    return ({a.get("region"), a.get("loc")} | {p["name"] for p in a.get("regions", [])}) - {None}


def _geo_defs(D):
    places, regions = dict(NAMED_PLACES), dict(REGIONS)
    f = D / "regions.json"
    if f.exists():
        x = json.loads(f.read_text() or "{}")
        for k, v in (x.get("places") or {}).items():
            places[k] = v
        for k, v in (x.get("regions") or {}).items():
            regions[k] = v
    for d in (places, regions):
        for k, v in list(d.items()):
            if v is None:
                d.pop(k)
    return places, regions


def _shares(tr, defs, min_share=PLACE_MIN_SHARE, min_s=PLACE_MIN_S):
    """time share per named shape: [(name, share, seconds)] for touched shapes, largest first"""
    t = tr["t"].astype(float)
    w = np.minimum(np.diff(t, append=t[-1]), 30.0)
    tot = max(float(w.sum()), 1.0)
    out = []
    for nm, shp in defs.items():
        m = in_shape(tr["lat"], tr["lon"], shp)
        if not m.any():
            continue
        s = float(w[m].sum())
        if s / tot >= min_share or s >= min_s or m.mean() >= min_share:
            out.append((nm, round(max(s / tot, float(m.mean())), 3), round(s)))
    return sorted(out, key=lambda r: -r[1])


def _cells(lat, lon):
    pts = list(zip(lat, lon))
    return sorted({c for c in sg.cells_of(pts) if c})


def _overview(lat, lon):
    for tol in (3, 5, 8, 12, 20, 30, 50, 80):
        k = dp_simplify(lat, lon, tol)
        if len(k) <= OVERVIEW_PTS:
            return k
    return np.linspace(0, len(lat) - 1, OVERVIEW_PTS).round().astype(int).tolist()


def build(person="adrian", workers=8, quiet=False):
    T0 = time.time()
    D = ROOT / "data" / person
    G = json.loads((D / "groups.json").read_text())
    idx = {x["activityId"]: x for x in json.loads((D / "activities_index.json").read_text())}
    fits = {int(p.stem): p for p in (D / "activities").glob("*/*.fit")}
    tracks, n_new = load_tracks(D, fits, workers)
    sgc = D / "cache" / "session_groups.pkl"
    anac = pickle.loads(sgc.read_bytes()) if sgc.exists() else {}
    P_DEF, R_DEF = _geo_defs(D)
    gplaces = G["places"]

    def pname(pid):
        """display name of a place id: start cluster name (override / landmark / ...), named place "name", else id"""
        if not pid:
            return None
        if pid in gplaces:
            return gplaces[pid].get("name") or pid
        return (P_DEF.get(pid) or {}).get("name") or sg.display(pid)

    def place_at(p, defs=P_DEF, margin=150):
        best = None
        for nm, shp in defs.items():
            if "circle" in shp:
                la, lo, r = shp["circle"]; d = sg.hav(p, (la, lo))
                if d <= r + margin and (best is None or d < best[0]):
                    best = (d, nm)
            elif in_shape([p[0]], [p[1]], shp)[0]:
                best = (0, nm)
        return best[1] if best else None

    acts, detail = {}, {}
    for sid, o in G["activities"].items():
        aid = int(sid); x = idx.get(aid, {}); tr = tracks.get(aid) or {}
        sport = o["sport"] if o["sport"] != "other" else o["atype"]
        gps = bool(tr.get("pts", 0) >= 2)
        ana = (anac.get(str(fits[aid])) or {}).get("ana") if aid in fits else None
        if ana and "err" in ana:
            ana = None
        dist_km = o.get("dist_km") or 0
        mov = o.get("moving_s") or x.get("movingDuration")
        r = {"id": aid, "date": o["date"], "start": (x.get("startTimeLocal") or o["date"])[:16], "name": x.get("activityName"),
             "sport": sport, "atype": o["atype"], "gps": gps, "dist_km": dist_km, "dist_trusted": o.get("dist_trusted", False),
             "dur_s": o.get("dur_s"), "moving_s": round(mov) if mov else None, "climb_m": r_(x.get("elevationGain")),
             "desc_m": r_(x.get("elevationLoss")), "avg_hr": r_(x.get("averageHR")), "max_hr": r_(x.get("maxHR")),
             "speed_ms": round(dist_km * 1000 / mov, 3) if mov and dist_km and o.get("dist_trusted") else None,
             "loc": x.get("locationName") or None, "area": o.get("area"),
             "start_place": o.get("place"), "start_place_name": pname(o.get("place")),
             "route": o.get("route"), "route_dir": o.get("route_dir"), "shape": o.get("shape")}
        r["workout"] = workout_of(o, sport, ana, dist_km)
        # ---- where: named places / regions by time share
        places, regions = [], []
        if gps:
            places = _shares(tr, P_DEF)
            regions = _shares(tr, R_DEF)
            la, lo = tr["lat"], tr["lon"]
            r["bbox"] = [round(float(la.min()), 5), round(float(lo.min()), 5), round(float(la.max()), 5), round(float(lo.max()), 5)]
            r["start_ll"] = [round(float(la[0]), 5), round(float(lo[0]), 5)]
            r["end_ll"] = [round(float(la[-1]), 5), round(float(lo[-1]), 5)]
            k = _overview(la, lo)
            r["poly"] = encode_polyline(la[k], lo[k])
            r["cells"] = encode_pairs(_cells(la, lo))   # 75 m route grid cells, see Index.cells
            kd = dp_simplify(la, lo, DETAIL_TOL_M)
            alt = tr["alt"][kd]
            detail[aid] = {"p": encode_polyline(la[kd], lo[kd]),
                           "alt": encode_ints(np.nan_to_num(alt, nan=-999).round().astype(int)) if np.isfinite(alt).any() else None}
            r["end_place"] = place_at((float(la[-1]), float(lo[-1])))
        elif x.get("startLatitude"):
            r["start_ll"] = [round(x["startLatitude"], 5), round(x["startLongitude"], 5)]
        main = places[0][0] if places and places[0][1] >= MAIN_SHARE else None
        # main place (id): the named place with >= 40 % of the time, else the start cluster
        r["place"] = main or r["start_place"]
        r["place_name"] = pname(r["place"])
        r["place_src"] = "named" if main else ("start" if r["place"] else None)
        r["end_place_name"] = pname(r.get("end_place"))
        r["places"] = [{"name": n_, "label": pname(n_), "share": s_, "s": t_} for n_, s_, t_ in places]
        r["region"] = regions[0][0] if regions and regions[0][1] >= MAIN_SHARE else (r["loc"] or (G["areas"].get(o.get("area"), {}).get("name") if o.get("area") else None))
        r["regions"] = [{"name": n_, "share": s_, "s": t_} for n_, s_, t_ in regions]
        acts[aid] = {k: v for k, v in r.items() if v is not None}
    assign_rest_clusters(acts)
    # ---- routes: names from where the members go
    routes = {}
    for rid, R in G["routes"].items():
        m = [acts[i] for i in R["members"] if i in acts]
        med = acts.get(R["medoid"], {})
        # ids throughout; names via pname(). Start place = session_groups' route place (one source, ties resolved there)
        sp = R.get("place") or collections.Counter(a.get("start_place") for a in m).most_common(1)[0][0]
        ep = None
        if R["shape"] == "point_to_point":
            ec = collections.Counter(a.get("end_place") or a.get("start_place") for a in m)
            ep = min(ec, key=lambda p: (-ec[p], str(p)))
            if ep == sp:
                ep = None
        if R["shape"] == "point_to_point" and not ep:   # no named place at the end: session_groups' end place
            ep = R.get("end_place")
        ep_name = pname(ep) or ("?" if R["shape"] == "point_to_point" else None)
        cnt = collections.Counter(p["name"] for a in m for p in a.get("places", []))
        via = [p for p, c in cnt.most_common() if c >= 0.5 * len(m) and p not in (sp, ep)]
        mc = collections.Counter(a.get("place") for a in m)
        main_pl = min(mc, key=lambda p: (-mc[p], p != sp, str(p)))
        gain = float(np.median([a.get("climb_m") or 0 for a in m])); loss = float(np.median([a.get("desc_m") or 0 for a in m]))
        climb = R["shape"] == "point_to_point" and gain >= 25 * R["dist_km"] and loss < 0.4 * gain
        shape_lab = {"laps": "laps", "out_and_back": "out & back", "point_to_point": "", "loop_cw": "loop", "loop_ccw": "loop"}[R["shape"]]
        where = main_pl if R["shape"] in ("laps",) else sp
        vian = [pname(v) for v in via]
        if R["shape"] == "point_to_point":
            nm = f"{pname(sp)} → {ep_name}" + (f" via {', '.join(vian[:2])}" if via else "")
            if climb:
                nm = f"{(vian or [ep_name])[0]} climb: {nm}"
        else:
            nm = f"{pname(where)} {shape_lab}" + (f" via {', '.join(vian[:2])}" if via and R["shape"] != "laps" else "")
        nm += f" {R['dist_km']:.1f} km"
        reg = collections.Counter(a.get("region") for a in m).most_common(1)[0][0]
        routes[rid] = {"name": nm, "sport": R["sport"], "shape": R["shape"], "dist_km": R["dist_km"], "n": R["n"],
                       "start_place": sp, "end_place": ep, "via": via, "place": main_pl, "region": reg,
                       "start_place_name": pname(sp), "end_place_name": ep_name, "via_names": vian, "place_name": pname(main_pl),
                       "climb_m": round(gain), "is_climb": climb, "medoid": R["medoid"], "members": R["members"],
                       "poly": med.get("poly")}
        for a in m:
            a["route_name"] = nm
    # places by id: n = touched (= what a place search matches: main, start, end or any touched place),
    # n_main = main place, n_start = start-cluster members (session_groups)
    places = collections.defaultdict(lambda: {"n": 0, "n_main": 0, "n_start": 0})
    for a in acts.values():
        for p in touched_places(a):
            places[p]["n"] += 1
        if a.get("place"):
            places[a["place"]]["n_main"] += 1
        if a.get("start_place"):
            places[a["start_place"]]["n_start"] += 1
    for pid, v in places.items():
        v["name"] = pname(pid)
        gp = gplaces.get(pid)
        if pid in P_DEF:
            v.update(kind=P_DEF[pid].get("kind"), src="named")
            v["lat"], v["lon"] = (round(c, 5) for c in shape_centre(P_DEF[pid]))
        else:
            v.update(kind="start cluster", src="start", **({"lat": gp["lat"], "lon": gp["lon"]} if gp else {}))
        if gp:   # also a start cluster: how its id / name were made (known / landmark / geocode / generic / override)
            v.update(start_src=gp.get("src"), name_src=gp.get("name_src", gp.get("src")),
                     **({"generic_id": gp["generic_id"]} if gp.get("generic_id") else {}))
    for a in acts.values():   # readable labels next to the ids
        a["sport_label"] = atype_label(a.get("atype"))
        w = a["workout"]
        if w.get("exact_key"):
            rid = a.get("route")
            w["exact_label"] = (f"{w['family_label']} · {rid} {routes[rid]['name']}" if w["exact_key"].endswith(f" · {rid}") and rid in routes
                                else w["exact_key"])
    regions = collections.defaultdict(lambda: {"n": 0, "n_main": 0})
    for a in acts.values():
        for p in touched_regions(a):
            regions[p]["n"] += 1
        if a.get("region"):
            regions[a["region"]]["n_main"] += 1
    for nm, v in regions.items():
        v.update(kind=R_DEF[nm].get("kind"), src="polygon") if nm in R_DEF else v.update(kind="municipality", src="garmin")
    doc = {"version": INDEX_VERSION, "built": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "person": person, "groups_built": G["built"],
           "params": {"named_places": P_DEF, "regions": R_DEF, "place_min_share": PLACE_MIN_SHARE, "main_share": MAIN_SHARE,
                      "presets": PRESETS, "missing_score": MISSING, "cell_m": sg.CELL, "overview_pts": OVERVIEW_PTS,
                      "detail_tol_m": DETAIL_TOL_M},
           "places": dict(sorted(places.items(), key=lambda kv: -kv[1]["n"])),
           "regions": dict(sorted(regions.items(), key=lambda kv: -kv[1]["n"])),
           "routes": routes, "activities": {str(k): v for k, v in sorted(acts.items(), key=lambda kv: kv[1]["date"])}}
    js = json.dumps(doc, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    (D / "similarity_index.json").write_text(js)
    tj = json.dumps({"format": "Google encoded polyline, precision 5; alt = delta-coded int metres (same encoding, -999 = none)",
                     "tol_m": DETAIL_TOL_M, "tracks": {str(k): v for k, v in detail.items()}}, separators=(",", ":"))
    (D / "tracks.json").write_text(tj)
    if not quiet:
        print(f"wrote {D / 'similarity_index.json'} ({len(js) / 1e6:.2f} MB, {len(acts)} activities, {len(routes)} routes) "
              f"and tracks.json ({len(tj) / 1e6:.2f} MB, {len(detail)} tracks); {n_new} tracks re-read; {time.time() - T0:.1f}s")
    return doc


def r_(v):
    return None if v is None else round(float(v))



# ---------------------------------------------------------------- query side
def _rng(v, tol=None):
    """filter value -> (lo, hi): [lo, hi] / (lo, hi) with None for open, "lo-hi", "lo..hi", or a number (+- tol)"""
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        lo, hi = v
        return (None if lo is None else float(lo), None if hi is None else float(hi))
    if isinstance(v, str):
        for sep in ("..", "-"):
            if sep in v.strip("-"):
                a, b = v.split(sep, 1) if not v.startswith("-") else ("", v[1:])
                return (_num(a) if a.strip() else None, _num(b) if b.strip() else None)
        v = _num(v)
    t = 0.0 if tol is None else tol
    return (v * (1 - t), v * (1 + t)) if t < 1 else (v - t, v + t)


def _num(s):
    """'4:00' -> 240, '1000m' -> 1000, '90s' -> 90, '12.5' -> 12.5"""
    s = str(s).strip().lower().rstrip("ms").strip() if not str(s).strip().lower().endswith("km") else str(float(str(s)[:-2]) * 1000)
    if ":" in s:
        m, sec = s.split(":"); return int(m) * 60 + float(sec)
    return float(s)


def _in(v, rg):
    if rg is None:
        return True
    if v is None:
        return False
    lo, hi = rg
    return (lo is None or v >= lo) and (hi is None or v <= hi)


def parse_rep_len(v, tol=0.1):
    """rep length filter: '1000m' / 1000 (distance) or '4:00' / '240s' (duration), +-10 % by default;
    ranges '900-1100' (m), '3:30-4:30' (s). -> ("dist"|"time", (lo, hi))"""
    if v is None:
        return None
    s = str(v).strip().lower()
    unit = "time" if (":" in s or s.endswith("s") and not s.endswith("ms")) else "dist"
    return unit, _rng(v if not isinstance(v, (int, float)) else float(v), tol)


class Index:
    """In-memory similarity index; build with build(), load with load_index()."""

    def __init__(self, doc):
        self.doc = doc
        self.acts = {int(k): v for k, v in doc["activities"].items()}
        self.routes = doc["routes"]; self.places = doc["places"]; self.regions = doc["regions"]
        # place filters take an id or a display name ("Bislet" / "Bislett"), case-insensitive
        self._pid = {}
        for pid, v in self.places.items():
            for k in (pid, v.get("name"), v.get("generic_id")):
                if k:
                    self._pid.setdefault(k.lower(), pid)
        self._cells = {}
        self._ord = {k: dt.date.fromisoformat(a["date"]).toordinal() for k, a in self.acts.items()}
        # pace reference: median speed of own runs within +-180 days (like session_groups.easy_ref)
        rs = [(self._ord[k], a["speed_ms"]) for k, a in self.acts.items() if a["sport"] == "run" and a.get("speed_ms") and a["dist_km"] >= 3]
        self._rs = (np.array([d for d, _ in rs]), np.array([v for _, v in rs]))

    # ---- helpers
    def place_id(self, x):
        """place id for an id or display name (unknown values are returned unchanged)"""
        return self._pid.get(str(x).lower(), x) if x is not None else None

    def place_name(self, pid):
        return (self.places.get(pid) or {}).get("name") or pid

    def cells(self, aid):
        if aid not in self._cells:
            c = self.acts[aid].get("cells")
            s = set(decode_ints_pairs(c)) if c else set()
            self._cells[aid] = (s, sg.dilate(s) if s else set())
        return self._cells[aid]

    def overlap(self, a, b):
        """route overlap: mean of both coverages (share of A's 75 m cells within one cell of B's, and v.v.)"""
        A, Ad = self.cells(a); B, Bd = self.cells(b)
        if not A or not B:
            return None
        return 0.5 * (len(A & Bd) / len(A) + len(B & Ad) / len(B))

    def rel_speed(self, aid, v):
        d, s = self._rs
        m = np.abs(d - self._ord[aid]) <= 180
        ref = float(np.median(s[m])) if m.sum() >= 5 else (float(np.median(s)) if len(s) else 3.2)
        return v / ref if v else None

    def summary(self, a):
        w = a["workout"]
        return {k: a.get(k) for k in ("id", "date", "sport", "name", "dist_km", "moving_s", "place", "place_name", "region",
                                      "route", "route_name")} | {"family": w["family"], "signature": w.get("signature"),
                                                         "exact_key": w.get("exact_key")}

    # ---- search
    def search(self, filters=None, sort="-date", limit=None):
        """filters (all optional, AND-ed):
          sport, atype, family, category, signature, main_sig, exact_key, route, place, region, rest_mode: str or list;
            place/region match the main one or any touched (place_mode/region_mode="main" for main only);
            signature also matches as a case-insensitive substring if it starts with '~' ('~×1000')
          date_from, date_to: 'YYYY-MM-DD'; year: int/list; months: [m, ...]
          distance_km, duration_s (moving), climb_m, avg_hr, pace (min/km as '4:00-4:30'), dist_trusted, gps
          intervals: rep_count (main part, or any part with part="any"), reps_total, rep_dist_m, rep_time_s,
            rep_len ('1000m', '4:00', with tol, default 0.1), rest_s, rep_pace
          ranges: [lo, hi] (None = open), 'lo-hi', or a number with tol; text: substring of the name"""
        f = dict(filters or {})
        tol = f.get("tol", 0.1)
        L = lambda k: None if f.get(k) is None else ([f[k]] if isinstance(f[k], (str, int)) else list(f[k]))
        eq = {k: L(k) for k in ("sport", "atype", "family", "category", "exact_key", "main_sig", "route", "rest_mode")}
        sig = L("signature"); pl = L("place"); rg = L("region"); yr = L("year")
        pl = [self.place_id(x) for x in pl] if pl else pl
        R = {k: _rng(f.get(k), tol if isinstance(f.get(k), (int, float)) else None) for k in
             ("distance_km", "duration_s", "climb_m", "avg_hr", "rep_count", "reps_total", "rep_dist_m", "rep_time_s", "rest_s")}
        pace = _rng(f.get("pace")); rpace = _rng(f.get("rep_pace"))
        rl = parse_rep_len(f.get("rep_len"), tol)
        part_any = f.get("part", "any") == "any"
        out = []
        for a in self.acts.values():
            w = a["workout"]
            if any(v is not None and str((w if k in ("family", "category", "exact_key", "main_sig", "rest_mode") else a).get(k)) not in map(str, v)
                   for k, v in eq.items()):
                continue
            if sig and not any((s[1:].lower() in (w.get("signature") or "").lower()) if s.startswith("~") else s == w.get("signature") for s in sig):
                continue
            if f.get("date_from") and a["date"] < f["date_from"] or f.get("date_to") and a["date"] > f["date_to"]:
                continue
            if yr and int(a["date"][:4]) not in map(int, yr):
                continue
            if f.get("months") and int(a["date"][5:7]) not in f["months"]:
                continue
            if pl:
                cand = {a.get("place")} if f.get("place_mode") == "main" else touched_places(a)
                if not cand & set(pl):
                    continue
            if rg:
                cand = {a.get("region")} if f.get("region_mode") == "main" else touched_regions(a)
                if not cand & set(rg):
                    continue
            for k in ("dist_trusted", "gps"):
                if k in f and bool(a.get(k)) != bool(f[k]):
                    break
            else:
                if not (_in(a.get("dist_km"), R["distance_km"]) and _in(a.get("moving_s"), R["duration_s"])
                        and _in(a.get("climb_m"), R["climb_m"]) and _in(a.get("avg_hr"), R["avg_hr"])
                        and _in(1000 / a["speed_ms"] / 60 * 60 if a.get("speed_ms") else None, pace)
                        and _in(w.get("reps_total"), R["reps_total"])):
                    continue
                need_part = any(R[k] for k in ("rep_count", "rep_dist_m", "rep_time_s", "rest_s")) or rl or rpace
                if need_part:
                    parts = w.get("parts") or []
                    if not part_any:
                        parts = parts[w["main_part"]:w["main_part"] + 1] if parts else []
                    ok = False
                    for p in parts:
                        if not (_in(p["n"], R["rep_count"]) and _in(p["dist_m"], R["rep_dist_m"]) and _in(p["time_s"], R["rep_time_s"])
                                and _in(p["rest_s"], R["rest_s"])
                                and _in(1000 / p["speed_ms"] if p.get("speed_ms") else None, rpace)):
                            continue
                        if rl and not _in(p["dist_m"] if rl[0] == "dist" else p["time_s"], rl[1]):
                            continue
                        ok = True; break
                    if not ok:
                        continue
                if f.get("text") and f["text"].lower() not in (a.get("name") or "").lower():
                    continue
                out.append(a)
        key, rev = sort.lstrip("-"), sort.startswith("-")
        out.sort(key=lambda a: (a.get(key) is None, a.get(key) if a.get(key) is not None else 0), reverse=rev)
        if rev:  # keep missing values last
            out.sort(key=lambda a: a.get(key) is None)
        return out[:limit] if limit else out

    # ---- similarity
    def components(self, q, c):
        """per-component scores 0..1 (missing = not computable) and a text per component"""
        s, t = {}, {}
        qi, ci = q["id"], c["id"]
        qw, cw = q["workout"], c["workout"]
        ov = self.overlap(qi, ci)
        if ov is not None:
            s["route"] = ov
            t["route"] = (f"same route {q['route']} ({ov:.0%} overlap)" if q.get("route") and q.get("route") == c.get("route")
                          else f"{ov:.0%} track overlap")
        qp = {q.get("place"), q.get("start_place")} | {p["name"] for p in q.get("places", [])}
        cp = {c.get("place"), c.get("start_place")} | {p["name"] for p in c.get("places", [])}
        N = self.place_name
        qp.discard(None); cp.discard(None)
        if qp and cp:
            jac = len(qp & cp) / len(qp | cp)
            if q.get("place") and q.get("place") == c.get("place"):
                s["place"] = 0.8 + 0.2 * jac; t["place"] = f"both at {N(q['place'])}"
            elif qp & cp:
                s["place"] = 0.4 + 0.4 * jac; t["place"] = "both touch " + ", ".join(sorted(N(p) for p in qp & cp))
            elif q.get("region") and q.get("region") == c.get("region"):
                s["place"] = 0.3; t["place"] = f"same region {q['region']}"
            elif q.get("area") and q.get("area") == c.get("area"):
                s["place"] = 0.15; t["place"] = "same area"
            else:
                s["place"] = 0.0; t["place"] = f"{N(q.get('place'))} vs {N(c.get('place'))}"
        # workout
        if qw.get("exact_key") and qw["exact_key"] == cw.get("exact_key"):
            s["workout"] = 1.0; t["workout"] = f"same workout {qw['exact_key']}"
            if qw.get("rest_s") and cw.get("rest_s") and qw.get("rest_group_s"):   # group label vs measured rests
                t["workout"] += f" (rests {mmss(qw['rest_s'])} vs {mmss(cw['rest_s'])})"
        elif qw.get("rep_count") and cw.get("rep_count"):
            k = 2.0 if q["sport"] == "ski" and c["sport"] == "ski" else 1.0   # ski reps: terrain-defined, looser
            st = math.exp(-abs(math.log(qw["rep_count"] / cw["rep_count"])) / (0.35 * k)) * \
                math.exp(-abs(math.log(qw["rep_time_s"] / max(cw["rep_time_s"], 1))) / (0.3 * k))
            if qw.get("rest_s") and cw.get("rest_s"):
                st *= math.exp(-abs(math.log(qw["rest_s"] / max(cw["rest_s"], 1))) / 0.6)
            if qw["signature"] == cw["signature"]:
                s["workout"] = 0.8 + 0.15 * st; t["workout"] = f"same reps {qw['signature']} (rest {mmss(qw['rest_s']) if qw.get('rest_s') else '-'} vs {mmss(cw['rest_s']) if cw.get('rest_s') else '-'})"
            else:
                s["workout"] = 0.75 * st; t["workout"] = f"{qw['signature']} vs {cw['signature']}"
        elif qw["family"] == cw["family"]:
            s["workout"] = 0.75 if qw.get("signature") == cw.get("signature") else 0.6
            t["workout"] = f"both {qw['signature'] if s['workout'] == 0.75 else qw['family_label']}"
        elif frozenset((qw["family"], cw["family"])) in RELATED_FAM:
            s["workout"] = 0.35; t["workout"] = f"{qw['family_label']} vs {cw['family_label']}"
        else:
            s["workout"] = 0.0 if q["sport"] != c["sport"] else 0.1
            t["workout"] = f"{qw['family_label']} vs {cw['family_label']}"
        # pace: rep speed for interval pairs, else moving speed (trusted distance only; runs relative to own form)
        if qw.get("rep_speed_ms") and cw.get("rep_speed_ms"):
            a_, b_ = qw["rep_speed_ms"], cw["rep_speed_ms"]
            s["pace"] = math.exp(-abs(math.log(a_ / b_)) / 0.1); t["pace"] = f"reps {fmt_speed(a_, q['sport'])} vs {fmt_speed(b_, c['sport'])}"
        elif q.get("speed_ms") and c.get("speed_ms"):
            a_, b_ = q["speed_ms"], c["speed_ms"]
            s["pace"] = math.exp(-abs(math.log(a_ / b_)) / 0.1); t["pace"] = f"{fmt_speed(a_, q['sport'])} vs {fmt_speed(b_, c['sport'])}"
        if q.get("avg_hr") and c.get("avg_hr"):
            s["hr"] = math.exp(-abs(q["avg_hr"] - c["avg_hr"]) / 8); t["hr"] = f"HR {q['avg_hr']} vs {c['avg_hr']}"
        if q.get("dist_trusted") and c.get("dist_trusted") and q["dist_km"] > 0 and c["dist_km"] > 0:
            s["distance"] = math.exp(-abs(math.log(q["dist_km"] / c["dist_km"])) / 0.2); t["distance"] = f"{q['dist_km']:.1f} vs {c['dist_km']:.1f} km"
            qc, cc = (q.get("climb_m") or 0) / q["dist_km"], (c.get("climb_m") or 0) / c["dist_km"]
            if q.get("climb_m") is not None and c.get("climb_m") is not None:
                s["climb"] = math.exp(-abs(qc - cc) / 10); t["climb"] = f"climb {qc:.0f} vs {cc:.0f} m/km"
        if q.get("moving_s") and c.get("moving_s"):
            s["duration"] = math.exp(-abs(math.log(max(q["moving_s"], 60) / max(c["moving_s"], 60))) / 0.25)
            t["duration"] = f"{mmss(q['moving_s'] / 60)} vs {mmss(c['moving_s'] / 60)} h"
        dq = dt.date.fromisoformat(q["date"]).timetuple().tm_yday; dc = dt.date.fromisoformat(c["date"]).timetuple().tm_yday
        dd = abs(dq - dc); dd = min(dd, 365 - dd)
        s["season"] = 0.5 + 0.5 * math.cos(math.pi * dd / 182.5); t["season"] = f"{dd} days apart in the year"
        days = abs(self._ord[qi] - self._ord[ci])
        s["recency"] = math.exp(-days / 730); t["recency"] = f"{days} days apart"
        return s, t

    def nearest(self, activity_id, k=10, weights=None, filters=None, same_sport=True, explain=4):
        """top-k closest activities. weights: preset name ('balanced', 'workout', 'route', 'place') or a dict
        {component: weight} (only these; add "preset": name to override a preset). Components a pair lacks score
        MISSING (0.4). filters: search() filters restricting the candidates. -> [{id, score, components,
        reasons, ...summary}]"""
        q = self.acts[int(activity_id)]
        if weights is None or isinstance(weights, str):
            W = dict(PRESETS[weights or "balanced"])
        else:
            weights = dict(weights)
            base = weights.pop("preset", None)
            unknown = set(weights) - set(COMPONENTS)
            if unknown:
                raise ValueError(f"unknown components {unknown}; use {COMPONENTS}")
            W = (dict(PRESETS[base]) if base else {}) | {k_: float(v) for k_, v in weights.items()}
        tot = sum(W.values()) or 1
        cand = self.search(filters) if filters else list(self.acts.values())
        res = []
        for c in cand:
            if c["id"] == q["id"] or (same_sport and c["sport"] != q["sport"]):
                continue
            s, t = self.components(q, c)
            sc = sum(w * s.get(k_, MISSING) for k_, w in W.items()) / tot
            contrib = sorted(((W[k_] * v / tot, k_) for k_, v in s.items() if W.get(k_)), reverse=True)
            res.append({**self.summary(c), "score": round(sc, 3),
                        "components": {k_: round(v, 2) for k_, v in s.items()},
                        "reasons": [f"{t[k_]} ({k_} {s[k_]:.2f})" for _, k_ in contrib[:explain]]})
        res.sort(key=lambda r: -r["score"])
        return res[:k]

    # ---- groups
    def groups(self, kind, min_n=1, touched=False, filters=None):
        """kind: place | start_place | region | route | family | signature | exact | workout (tree).
        place/region: by the main one, or every touched one with touched=True. -> [{key, label, n, ids, ...}]"""
        acts = self.search(filters) if filters else list(self.acts.values())
        if kind == "workout":
            tree = {}
            for a in acts:
                w = a["workout"]
                f = tree.setdefault(w["family"], {"key": w["family"], "label": w["family_label"], "n": 0, "children": {}})
                f["n"] += 1
                s = f["children"].setdefault(w.get("signature"), {"key": w.get("signature"), "n": 0, "children": {}})
                s["n"] += 1
                e = s["children"].setdefault(w.get("exact_key"), {"key": w.get("exact_key"), "label": w.get("exact_label", w.get("exact_key")),
                                                                  "n": 0, "ids": []})
                e["n"] += 1; e["ids"].append(a["id"])
            out = []
            for f in sorted(tree.values(), key=lambda x: -x["n"]):
                f["children"] = [s for s in sorted(f["children"].values(), key=lambda x: -x["n"]) if s["n"] >= min_n]
                for s in f["children"]:
                    s["children"] = [e for e in sorted(s["children"].values(), key=lambda x: -x["n"]) if e["n"] >= min_n]
                out.append(f)
            return out
        G = collections.defaultdict(list)
        for a in acts:
            w = a["workout"]
            if kind == "place" and touched:
                keys = touched_places(a)
            elif kind == "region" and touched:
                keys = touched_regions(a)
            else:
                keys = {{"place": a.get("place"), "start_place": a.get("start_place"), "region": a.get("region"),
                         "route": a.get("route"), "family": w["family"], "signature": w.get("signature"),
                         "exact": w.get("exact_key")}[kind]}
            for k_ in keys - {None}:
                G[k_].append(a)
        out = []
        for k_, m in G.items():
            if len(m) < min_n:
                continue
            m.sort(key=lambda a: a["date"])
            label = (self.routes[k_]["name"] if kind == "route" and k_ in self.routes else
                     self.place_name(k_) if kind in ("place", "start_place") else
                     m[0]["workout"].get("exact_label", k_) if kind == "exact" else k_)
            g = {"key": k_, "label": label, "n": len(m),
                 "first": m[0]["date"], "last": m[-1]["date"], "ids": [a["id"] for a in m],
                 "sports": dict(collections.Counter(a["sport"] for a in m).most_common())}
            if kind == "exact" or kind == "signature":
                g["family"] = collections.Counter(a["workout"]["family"] for a in m).most_common(1)[0][0]
                g["places"] = dict(collections.Counter(a.get("place_name") for a in m).most_common(3))
            out.append(g)
        return sorted(out, key=lambda g: (-g["n"], g["key"]))


_LOADED = {}


def load_index(person="adrian", rebuild=False):
    """Index from data/<person>/similarity_index.json; (re)built if missing, older than groups.json or rebuild"""
    D = ROOT / "data" / person
    f = D / "similarity_index.json"
    stale = not f.exists() or f.stat().st_mtime < (D / "groups.json").stat().st_mtime
    if rebuild or stale:
        doc = build(person, quiet=True)
        _LOADED.pop(person, None)
    elif person in _LOADED and _LOADED[person][0] == f.stat().st_mtime_ns:
        return _LOADED[person][1]
    else:
        doc = json.loads(f.read_text())
        if doc.get("version") != INDEX_VERSION:
            doc = build(person, quiet=True)
    ix = Index(doc)
    _LOADED[person] = (f.stat().st_mtime_ns, ix)
    return ix


def search(filters=None, person="adrian", **kw):
    return load_index(person).search(filters, **kw)


def nearest(activity_id, k=10, weights=None, person="adrian", **kw):
    return load_index(person).nearest(activity_id, k, weights, **kw)


def groups(kind, person="adrian", **kw):
    return load_index(person).groups(kind, **kw)


# ---------------------------------------------------------------- CLI
def _row(a):
    w = a["workout"]
    return (f"{a['id']:>12} {a['date']} {a['sport']:<5} {a.get('dist_km', 0):5.1f} km  {(a.get('place_name') or a.get('place') or '-')[:16]:<16} "
            f"{(a.get('region') or '-')[:10]:<10} {(a.get('route') or '-'):<4} {w.get('exact_key') or w.get('signature') or w['family']}")


def validate(ix):
    A = ix.acts
    print("== named places (main place = >= 40 % of the time inside; touched = >= 5 % or >= 3 min)")
    for nm, v in list(ix.places.items())[:20]:
        print(f"  {nm:<22} touched {v['n']:>4}  main {v['n_main']:>4}  ({v.get('kind')})")
    print("== regions")
    for nm, v in list(ix.regions.items())[:10]:
        print(f"  {nm:<22} touched {v['n']:>4}  main {v['n_main']:>4}  ({v.get('kind')})")
    b = ix.search({"place": "Bislet", "place_mode": "main"})
    print(f"== Bislet (main place): {len(b)}  by family {dict(collections.Counter(a['workout']['family'] for a in b))}")
    bs = ix.search({"place": "Bislet"})
    sb = [a for a in A.values() if a.get("start_place") == "Bislet"]
    print(f"   start cluster 'Bislet': {len(sb)}; of those with main place elsewhere: "
          f"{dict(collections.Counter(a.get('place') for a in sb if a.get('place') != 'Bislet').most_common(6))}; any touch: {len(bs)}")
    print("== Holmenkollen climbs")
    for rid, R in ix.routes.items():
        if R["is_climb"] or "Holmenkollen" in R["via"] or R["end_place"] in ("Holmenkollen", "Frognerseteren"):
            print(f"  {rid:<4} n={R['n']:<3} {R['name']}  ({R['sport']}, +{R['climb_m']} m)")
    hk = ix.search({"place": "Holmenkollen"})
    print(f"  touching Holmenkollen: {len(hk)} {dict(collections.Counter(a['sport'] for a in hk))}; "
          f"routes {dict(collections.Counter(a.get('route') for a in hk).most_common(5))}")
    print("== 6×1000-type (rep_count 5-7, rep length 900-1100 m)")
    for g in ix.groups("exact", filters={"rep_count": [5, 7], "rep_dist_m": [900, 1100]}):
        print(f"  {g['key']:<32} n={g['n']:<3} {g['first']}..{g['last']} {g['places']}")
    print("== workout tree (intervals)")
    for f in ix.groups("workout", min_n=2):
        if f["key"] != "run/intervals":
            continue
        for s in f["children"]:
            print(f"  {s['key']:<28} {s['n']:>3}   " + ", ".join(f"{e['key'].split(' · ')[-1] if e['key'] else '-'}: {e['n']}" for e in s["children"]))
    print("== largest exact groups (all families, n >= 3)")
    for g in ix.groups("exact", min_n=3)[:15]:
        print(f"  {g['key']:<40} n={g['n']:<3} {g['places']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("person")
    sp = ap.add_subparsers(dest="cmd", required=True)
    b = sp.add_parser("build"); b.add_argument("--workers", type=int, default=8)
    s = sp.add_parser("search")
    for k in ("sport", "family", "category", "signature", "exact", "route", "place", "region", "rest-mode",
              "date-from", "date-to", "distance-km", "duration-s", "rep-count", "reps-total", "rep-dist-m",
              "rep-time-s", "rep-len", "rest", "pace", "rep-pace", "text"):
        s.add_argument("--" + k)
    s.add_argument("--tol", type=float, default=0.1)
    s.add_argument("--main-part", action="store_true", help="rep filters on the main part only")
    s.add_argument("--limit", type=int)
    n = sp.add_parser("nearest"); n.add_argument("id", type=int); n.add_argument("-k", type=int, default=10)
    n.add_argument("--preset", default="balanced", choices=list(PRESETS))
    n.add_argument("-w", "--weight", action="append", default=[], metavar="COMP=W", help="override a preset weight")
    n.add_argument("--any-sport", action="store_true")
    g = sp.add_parser("groups"); g.add_argument("kind", choices=["place", "start_place", "region", "route", "family",
                                                                 "signature", "exact", "workout"])
    g.add_argument("--min-n", type=int, default=2); g.add_argument("--touched", action="store_true")
    sp.add_parser("validate")
    args = ap.parse_args()
    if args.cmd == "build":
        build(args.person, args.workers); return
    ix = load_index(args.person)
    if args.cmd == "search":
        m = {"sport": "sport", "family": "family", "category": "category", "signature": "signature", "exact": "exact_key",
             "route": "route", "place": "place", "region": "region", "rest_mode": "rest_mode", "date_from": "date_from",
             "date_to": "date_to", "distance_km": "distance_km", "duration_s": "duration_s", "rep_count": "rep_count",
             "reps_total": "reps_total", "rep_dist_m": "rep_dist_m", "rep_time_s": "rep_time_s", "rep_len": "rep_len",
             "rest": "rest_s", "pace": "pace", "rep_pace": "rep_pace", "text": "text"}
        f = {v: getattr(args, k) for k, v in m.items() if getattr(args, k) is not None}
        for k in ("rep_count", "reps_total"):
            if k in f and f[k].isdigit():
                f[k] = [int(f[k]), int(f[k])]
        f["tol"] = args.tol
        if args.main_part:
            f["part"] = "main"
        res = ix.search(f, limit=args.limit)
        for a in res:
            print(_row(a))
        print(f"{len(res)} activities")
    elif args.cmd == "nearest":
        W = dict(PRESETS[args.preset])
        for kv in args.weight:
            k, v = kv.split("="); W[k] = float(v)
        print("query: " + _row(ix.acts[args.id]))
        for r in ix.nearest(args.id, args.k, W, same_sport=not args.any_sport):
            print(f"{r['score']:.3f} " + _row(ix.acts[r["id"]]))
            print("        " + "; ".join(r["reasons"]))
    elif args.cmd == "groups":
        if args.kind == "workout":
            for f in ix.groups("workout", min_n=args.min_n):
                print(f"{f['key']} ({f['label']}) {f['n']}")
                for s in f["children"]:
                    print(f"    {s['key']}  {s['n']}")
                    for e in s["children"]:
                        if e["key"] and e["key"] != s["key"]:
                            print(f"        {e['key']}  {e['n']}")
        else:
            for g in ix.groups(args.kind, min_n=args.min_n, touched=args.touched):
                print(f"{g['n']:>4}  {g['label']:<50} {g['first']}..{g['last']}  {g['sports']}")
    elif args.cmd == "validate":
        validate(ix)


if __name__ == "__main__":
    main()

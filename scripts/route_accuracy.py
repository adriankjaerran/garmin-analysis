"""Phase 4: carry Bislet over to runs outside Bislet -> per-activity distance uncertainty.

Evidence used:
  races      Garmin total / record distance / raw GPS distance vs nominal (Copenhagen Half 2025
             and Rome 2019 certified; Lier 10k x2, Oslo 10k, Trondheim 5k nominal only).
             Rome's 1 km auto-laps: raw GPS distance per auto-lap vs the watch's 1000 m.
  clusters   repeated routes: same start and finish (<= 150 m, either direction), GPS distance
             within 10 %, shape within mean 40 m / max 100 m (symmetric, point-to-polyline), and points at the same
             fraction of the route within 30 m + 0.6 % of the length (p90; order check: no extra
             laps of a loop, which the set distance misses). Pairs count in the pooled precision.
             Run-to-run spread of the watch distance per watch = precision.
  osm        (top 3 clusters + races) OpenStreetMap ways within 25 m of the trace (Overpass,
             cached in data/<person>/osm_cache/), trace snapped onto them, length summed.
  bislet     GPS-mode manual laps at the line (gps_lap_analysis.json): best-case per-lap noise.
Model:   sigma(%) = sqrt(base[device][env]^2 + (k_jitter * max(0, jitter - j_ref[device]))^2
                         + (100 * end_m / distance)^2),
         base = sqrt(precision^2 + scale^2): precision from cluster deviations, scale from Bislet.
Output:  data/<person>/route_accuracy.json  (uncertainty_model, races, clusters, osm, activities)

Usage: uv run scripts/route_accuracy.py adrian [--no-osm] [--osm-fetch] [--osm-clusters N]
(OSM: cache only by default; result is informative only, see OSM_VERDICT)
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gps_quality import DEFAULT_MODEL, ENV_JITTER, hav, load, quality_from, uncertainty_pct  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RACES = {  # id: (label, metres, certified)
    20380305975: ("Copenhagen Half Marathon 2025", 21097.5, True),
    3533402624: ("Rome Marathon 2019", 42195.0, True),
    20867534653: ("Lier 10k 2025", 10000.0, False),
    11063941501: ("Lier 10k 2023", 10000.0, False),
    10959008247: ("Oslo 10k 2023", 10000.0, False),
    6728291215: ("Trondheim 5k 2021", 5000.0, False),
}
OUTDOOR_DIRS = ("running", "trail_running", "street_running")
N_RES = 200
END_TOL, MEAN_TOL, MAX_TOL, DIST_TOL = 150.0, 40.0, 100.0, 0.10
ALIGN_TOL, ALIGN_REL = 30.0, 0.006  # p90 of same-fraction distance <= 30 m + 0.6 % of the length
OVERPASS = "https://overpass-api.de/api/interpreter"
UA = "garmin-analysis/0.1 (personal running analysis; route length check)"


# ---------- helpers ----------
def q_(v, p):
    v = sorted(v)
    if not v:
        return None
    k = (len(v) - 1) * p
    i = int(k)
    return v[i] + (v[min(i + 1, len(v) - 1)] - v[i]) * (k - i)


def rsd(v):
    """robust sigma: 1.4826 * MAD."""
    if len(v) < 2:
        return None
    m = statistics.median(v)
    return 1.4826 * statistics.median(abs(x - m) for x in v)


def r(x, n=2):
    return None if x is None else round(x, n)


def to_xy(p):
    return (p[1] * 111320 * math.cos(math.radians(p[0])), p[0] * 110574)


def resample(pts, n):
    """n points evenly spaced along the polyline (xy)."""
    cum = [0.0]
    for a, b in zip(pts, pts[1:]):
        cum.append(cum[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    L = cum[-1]
    out, j = [], 0
    for k in range(n):
        s = L * k / (n - 1)
        while j < len(cum) - 2 and cum[j + 1] < s:
            j += 1
        seg = cum[j + 1] - cum[j]
        f = (s - cum[j]) / seg if seg > 0 else 0
        a, b = pts[j], pts[j + 1]
        out.append((a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1])))
    return out


def seg_dist(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    t = 0 if L2 == 0 else max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy), t


class Grid:
    """segments of a polyline bucketed in cells for nearest-segment queries."""

    def __init__(self, segs, cell=50.0):
        self.cell, self.g, self.segs = cell, defaultdict(list), segs
        for i, (a, b, *_rest) in enumerate(segs):
            x0, x1 = sorted((a[0], b[0])); y0, y1 = sorted((a[1], b[1]))
            for cx in range(int(x0 // cell), int(x1 // cell) + 1):
                for cy in range(int(y0 // cell), int(y1 // cell) + 1):
                    self.g[(cx, cy)].append(i)

    def near(self, p, rad):
        c = self.cell
        k = int(math.ceil(rad / c))
        cx, cy = int(p[0] // c), int(p[1] // c)
        seen = set()
        for i in range(cx - k, cx + k + 1):
            for j in range(cy - k, cy + k + 1):
                for s in self.g.get((i, j), ()):
                    if s not in seen:
                        seen.add(s)
                        yield s

    def nearest(self, p, rad):
        best, bi, bt = rad, None, None
        for s in self.near(p, rad):
            a, b = self.segs[s][0], self.segs[s][1]
            d, t = seg_dist(p, a, b)
            if d < best:
                best, bi, bt = d, s, t
        return best, bi, bt


def directed(A, gB, tol_max):
    """mean and max distance from points A to polyline B (early exit beyond tol_max)."""
    tot, mx = 0.0, 0.0
    for p in A:
        d, _, _ = gB.nearest(p, tol_max + 1)
        if d > tol_max:
            return None, d
        tot += d; mx = max(mx, d)
    return tot / len(A), mx


# ---------- load ----------
def load_all(person):
    acts = []
    for d in sorted((ROOT / "data" / person / "activities").iterdir()):
        parts = d.name.split("_", 2)
        if len(parts) < 3 or not parts[1].isdigit():
            continue
        typ = parts[2]
        if "running" not in typ:
            continue
        fit = d / f"{parts[1]}.fit"
        if not fit.exists():
            continue
        try:
            a = load(fit)
            q = quality_from(a)
        except Exception as e:  # partial download etc.
            print(f"skip {d.name}: {e}", file=sys.stderr)
            continue
        pos = [p for _, p, _ in a["rows"] if p is not None]
        acts.append({"id": int(parts[1]), "date": parts[0], "type": typ, "q": q, "a": a, "pos": pos})
    return acts


# ---------- races ----------
def race_table(acts):
    by = {x["id"]: x for x in acts}
    out = []
    for rid, (label, truth, cert) in RACES.items():
        x = by.get(rid)
        if not x:
            out.append({"id": rid, "race": label, "missing": True})
            continue
        q = x["q"]
        g, rec, gps = q["session_distance"], q["record_distance"], q["gps_distance"]
        row = {"id": rid, "date": x["date"], "race": label, "nominal": truth, "certified": cert,
               "device": q["device"], "env": q["env"], "jitter_m": q["jitter_m"],
               "garmin_total": r(g, 1), "record": r(rec, 1), "gps": r(gps, 1),
               "garmin_pct": r(100 * (g / truth - 1)), "record_pct": r(100 * (rec / truth - 1)),
               "gps_pct": r(100 * (gps / truth - 1)), "uncertainty_default_pct": uncertainty_pct(q, None)}
        laps = x["a"]["laps"]
        auto = [l for l in laps if l["trigger"] == 3 or abs(l["dist"] - 1000) < 1.5]
        if rid == 3533402624 or len(auto) >= 5:
            row["auto_laps"] = auto_lap_stats(x, laps)
        out.append(row)
    return out


def auto_lap_stats(x, laps):
    """per auto-lap: raw GPS distance between lap boundaries vs the watch's lap distance."""
    rows = x["a"]["rows"]
    res = []
    for i, l in enumerate(laps):
        a, b = l["start"], l["start"] + l["time"]
        pts = [p for t, p, _ in rows if a <= t <= b and p is not None]
        gd = sum(hav(p, q) for p, q in zip(pts, pts[1:]))
        res.append({"lap": i + 1, "dist": r(l["dist"], 1), "time": r(l["time"], 1),
                    "gps": r(gd, 1), "gps_minus_lap_pct": r(100 * (gd / l["dist"] - 1)) if l["dist"] > 0 else None})
    full = [z for z in res if z["dist"] and abs(z["dist"] - 1000) < 2]
    d = [z["gps_minus_lap_pct"] for z in full]
    pace = [z["time"] for z in full]
    return {"n": len(res), "n_1km": len(full), "sum_laps": r(sum(z["dist"] for z in res), 1),
            "gps_vs_lap_median_pct": r(statistics.median(d)) if d else None,
            "gps_vs_lap_p10_pct": r(q_(d, 0.1)), "gps_vs_lap_p90_pct": r(q_(d, 0.9)),
            "gps_vs_lap_robust_sd_pct": r(rsd(d)),
            "lap_time_median_s": r(statistics.median(pace), 1) if pace else None,
            "lap_time_robust_cv_pct": r(100 * rsd(pace) / statistics.median(pace)) if len(pace) > 2 else None,
            "laps": res}


# ---------- repeated routes ----------
def candidates(acts):
    out = []
    for x in acts:
        q = x["q"]
        if x["type"] not in OUTDOOR_DIRS or q["env"] in ("track", "indoor", "unknown"):
            continue
        if not q["gps_distance"] or q["gps_distance"] < 1500 or len(x["pos"]) < 100 or not q["session_distance"]:
            continue
        xy = [to_xy(p) for p in x["pos"]]
        res = resample(xy, N_RES)
        x["res"] = res
        x["grid"] = Grid([(a, b) for a, b in zip(res, res[1:])])
        x["se"] = (res[0], res[-1])
        out.append(x)
    return out


def same_route(a, b):
    ga, gb = a["q"]["gps_distance"], b["q"]["gps_distance"]
    if abs(ga / gb - 1) > DIST_TOL:
        return None
    (s1, e1), (s2, e2) = a["se"], b["se"]
    fwd = math.dist(s1, s2) <= END_TOL and math.dist(e1, e2) <= END_TOL
    rev = math.dist(s1, e2) <= END_TOL and math.dist(e1, s2) <= END_TOL
    if not (fwd or rev):
        return None
    # order check: points at the same fraction of the route must be close (rules out extra laps
    # of a loop and out-and-backs with a different turning point, which a set distance misses)
    A, B = a["res"], b["res"]
    al = [q_([math.dist(p, q) for p, q in zip(A, B2)], 0.9) for B2, ok in ((B, fwd), (B[::-1], rev)) if ok]
    if min(al) > ALIGN_TOL + ALIGN_REL * min(ga, gb):
        return None
    m1, x1 = directed(a["res"], b["grid"], MAX_TOL)
    if m1 is None:
        return None
    m2, x2 = directed(b["res"], a["grid"], MAX_TOL)
    if m2 is None:
        return None
    m = (m1 + m2) / 2
    return m if m <= MEAN_TOL else None


def cluster(cands):
    n = len(cands)
    nb = [dict() for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            m = same_route(cands[i], cands[j])
            if m is not None:
                nb[i][j] = m; nb[j][i] = m
    left, clusters = set(range(n)), []
    while True:
        best = max(left, key=lambda i: (len([j for j in nb[i] if j in left]),
                                        -sum(v for j, v in nb[i].items() if j in left)), default=None)
        if best is None:
            break
        mem = [best] + [j for j in nb[best] if j in left]
        if len(mem) < 2:
            break
        clusters.append((best, mem))
        left -= set(mem)
    return clusters


def cluster_stats(cands, clusters):
    out, devs = [], []
    for k, (med, mem) in enumerate(clusters):
        xs = [cands[i] for i in mem]
        c = {"cluster": k + 1, "n": len(xs), "medoid": cands[med]["id"],
             "start": [round(v, 5) for v in cands[med]["pos"][0]],
             "eras": {}, "members": []}
        for dev in sorted({x["q"]["device"] for x in xs}):
            ys = [x for x in xs if x["q"]["device"] == dev]
            w = [x["q"]["session_distance"] for x in ys]
            g = [x["q"]["gps_distance"] for x in ys]
            e = {"n": len(ys), "median_m": r(statistics.median(w), 1),
                 "robust_cv_pct": r(100 * rsd(w) / statistics.median(w)) if len(w) >= 3 else None,
                 "cv_pct": r(100 * statistics.stdev(w) / statistics.mean(w)) if len(w) >= 3 else None,
                 "gps_robust_cv_pct": r(100 * rsd(g) / statistics.median(g)) if len(g) >= 3 else None}
            c["eras"][dev] = e
            if len(ys) >= 2:
                mw = statistics.median(w)
                for x in ys:
                    d = 100 * (x["q"]["session_distance"] / mw - 1)
                    # leave-one-out scale correction for small n: dev from median of n points
                    devs.append({"id": x["id"], "cluster": k + 1, "n": len(ys), "device": dev,
                                 "env": x["q"]["env"], "jitter_m": x["q"]["jitter_m"],
                                 "heading_changes_per_km": x["q"]["heading_changes_per_km"],
                                 "speed_spikes_per_km": x["q"]["speed_spikes_per_km"],
                                 "pos_vs_field_pct": x["q"]["pos_vs_field_pct"],
                                 "dist_km": x["q"]["session_distance"] / 1000, "dev_pct": d})
        if len(c["eras"]) == 2 and all(v["n"] >= 2 for v in c["eras"].values()):
            c["new_vs_old_pct"] = r(100 * (c["eras"]["4315"]["median_m"] / c["eras"]["fr935"]["median_m"] - 1)) \
                if "4315" in c["eras"] and "fr935" in c["eras"] else None
        c["members"] = [{"id": x["id"], "date": x["date"], "device": x["q"]["device"], "env": x["q"]["env"],
                         "watch_m": r(x["q"]["session_distance"], 1), "gps_m": x["q"]["gps_distance"],
                         "jitter_m": x["q"]["jitter_m"]} for x in sorted(xs, key=lambda x: x["date"])]
        out.append(c)
    return out, devs


# ---------- OSM ----------
def overpass(trace, cache: Path, key: str, state):
    f = cache / f"{key}.json"
    if f.exists():
        return json.loads(f.read_text())
    if not state["fetch"] or state["requests"] >= 10:
        return None
    pts = trace[:: max(1, len(trace) // 250)] + [trace[-1]]
    line = ",".join(f"{p[0]:.5f},{p[1]:.5f}" for p in pts)
    qy = f'[out:json][timeout:60];way["highway"](around:25,{line});out geom;'
    if state["last"]:
        time.sleep(max(0, 2 - (time.time() - state["last"])))
    state["requests"] += 1
    try:
        req = urllib.request.Request(OVERPASS, data=urllib.parse.urlencode({"data": qy}).encode(),
                                     headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=90) as resp:
            js = json.loads(resp.read())
    except Exception as e:
        print(f"overpass {key}: {e}", file=sys.stderr)
        state["last"] = time.time()
        return None
    state["last"] = time.time()
    if js.get("elements"):  # don't cache empty answers (Overpass sometimes returns none)
        cache.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(js))
    return js


def map_match(trace, js, step=10.0, rad=20.0, stick=8.0):
    """Snap the trace (resampled every `step` m) onto OSM ways; length along the ways."""
    segs = []
    for w in js.get("elements", []):
        g = w.get("geometry") or []
        xy = [to_xy((p["lat"], p["lon"])) for p in g]
        s = 0.0
        for a, b in zip(xy, xy[1:]):
            segs.append((a, b, w["id"], s)); s += math.hypot(b[0] - a[0], b[1] - a[1])
    if not segs:
        return None
    grid = Grid(segs)
    xy = [to_xy(p) for p in trace]
    L = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(xy, xy[1:]))
    pts = resample(xy, max(3, int(L / step)))
    total, matched, prev, switches = 0.0, 0, None, 0  # prev = (way, along, xy)
    for p in pts:
        d, si, t = grid.nearest(p, rad)
        if si is not None and prev is not None and prev[0] is not None:
            # hysteresis: stay on the previous way if it's nearly as close
            best_same = None
            for s in grid.near(p, rad):
                if segs[s][2] == prev[0]:
                    ds, ts = seg_dist(p, segs[s][0], segs[s][1])
                    if best_same is None or ds < best_same[0]:
                        best_same = (ds, s, ts)
            if best_same and best_same[0] <= d + stick:
                d, si, t = best_same
        if si is None:
            cur = (None, None, p)
        else:
            a, b, wid, s0 = segs[si]
            proj = (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
            cur = (wid, s0 + t * math.hypot(b[0] - a[0], b[1] - a[1]), proj)
            matched += 1
        if prev is not None:
            if cur[0] is not None and cur[0] == prev[0] and abs(cur[1] - prev[1]) < 3 * step:
                total += abs(cur[1] - prev[1])
            elif cur[0] is not None and prev[0] is not None:
                # switching ways (parallel sidewalk/road, junction): count the trace step, not
                # the lateral jump between the two projections
                total += min(step, math.dist(cur[2], prev[2]))
                switches += 1
            else:
                total += math.dist(cur[2], prev[2])
        prev = cur
    return {"osm_length_m": round(total, 1), "trace_length_m": round(L, 1),
            "matched_share": round(matched / len(pts), 3), "way_switches": switches, "ways": len(js.get("elements", []))}


OSM_VERDICT = ("not used in the model: nearest-way snapping is unreliable. On the cached races the "
               "result swings by -5..+9 % with small rule changes (parallel sidewalk/road ways, crossing "
               "streets at junctions); a proper HMM map matcher would be needed.")


def osm_part(person, acts, cands, clusters, enabled, max_clusters=3, fetch=False):
    if not enabled:
        return {"skipped": "disabled (--no-osm)"}
    cache = ROOT / "data" / person / "osm_cache"
    state = {"requests": 0, "last": 0.0, "fetch": fetch}
    by = {x["id"]: x for x in acts}
    jobs = [(f"race_{rid}", by[rid], RACES[rid][1]) for rid in RACES if rid in by]
    for k, (med, mem) in enumerate(clusters[:max_clusters]):
        jobs.append((f"cluster{k + 1}_{cands[med]['id']}", cands[med], None))
    out = []
    for key, x, truth in jobs:
        js = overpass(x["pos"], cache, key, state)
        if js is None:
            out.append({"key": key, "id": x["id"], "error": "overpass unavailable"}); continue
        mm = map_match(x["pos"], js)
        if mm is None:
            out.append({"key": key, "id": x["id"], "error": "no ways"}); continue
        w = x["q"]["session_distance"]
        mm.update({"key": key, "id": x["id"], "date": x["date"], "watch_m": r(w, 1), "nominal": truth,
                   "watch_vs_osm_pct": r(100 * (w / mm["osm_length_m"] - 1)),
                   "osm_vs_nominal_pct": r(100 * (mm["osm_length_m"] / truth - 1)) if truth else None})
        out.append(mm)
    return {"requests": state["requests"], "verdict": OSM_VERDICT, "results": out}


# ---------- model ----------
def bislet_laps(person):
    f = ROOT / "data" / person / "gps_lap_analysis.json"
    if not f.exists():
        return {}
    laps = [l for l in json.loads(f.read_text())["laps"] if l["mode"] == "gps" and l.get("err_pct") is not None]
    out = {}
    for dev in sorted({l["device"] for l in laps}):
        th = [l for l in laps if l["device"] == dev and l["from"] == "through" and l["to"] == "through"]
        e = [l["err_pct"] for l in th]
        # per session: summed line-to-line laps (lane corrected) -> run-level scale error
        by = defaultdict(lambda: [0.0, 0.0])
        for l in th:
            by[l["id"]][0] += l["garmin"]; by[l["id"]][1] += l["expected"]
        sess = [100 * (g / x - 1) for g, x in by.values() if x > 1200]
        out[dev] = {"n_laps": len(e), "lap_median_pct": r(statistics.median(e)) if e else None,
                    "lap_robust_sd_pct": r(rsd(e)), "n_sessions_1200m": len(sess),
                    "session_pcts": [r(s) for s in sess]}
    return out


def fit_model(devs, bislet, races):
    m = json.loads(json.dumps(DEFAULT_MODEL))
    m["fitted"] = {}
    # scale term: Bislet best case. Line-to-line session sums if available, else 0.5 % (plan)
    sess = [s for v in bislet.values() for s in v["session_pcts"]]
    # RMS (not robust sd): a constant bias counts as error here
    scale = max(0.4, math.sqrt(statistics.fmean(x * x for x in sess))) if len(sess) >= 3 else 0.5
    m["fitted"]["scale_pct"] = r(scale)
    m["fitted"]["scale_source"] = f"Bislet GPS-mode sessions (>= 1.2 km of line-to-line laps), n={len(sess)}" \
        if len(sess) >= 3 else "plan default (Bislet +-0.5 %)"
    # precision per device/env from cluster deviations (median of n points -> inflate by n/(n-1))
    prec = {}
    for dev in sorted({d["device"] for d in devs}):
        prec[dev] = {}
        for env in ("open", "urban_or_trees"):
            v = [d["dev_pct"] * math.sqrt(d["n"] / (d["n"] - 1)) for d in devs if d["device"] == dev and d["env"] == env]
            if len(v) >= 8:
                prec[dev][env] = {"n": len(v), "robust_sd_pct": r(rsd(v)), "p90_abs_pct": r(q_([abs(x) for x in v], 0.9))}
    m["fitted"]["precision"] = prec
    # jitter slope: |dev| vs jitter above reference, per device (least squares through ref)
    for dev, p in prec.items():
        vv = [d for d in devs if d["device"] == dev and d["jitter_m"] is not None]
        if len(vv) < 10:
            continue
        jr = q_([d["jitter_m"] for d in vv], 0.5)
        m["j_ref"][dev] = r(jr)
        xs = [max(0.0, d["jitter_m"] - jr) for d in vv]
        ys = [abs(d["dev_pct"]) * math.sqrt(d["n"] / (d["n"] - 1)) * 1.2533 for d in vv]  # E|N(0,s)| = 0.798 s
        sxx = sum(x * x for x in xs)
        base = statistics.median(ys)
        k = sum(x * (y - base) for x, y in zip(xs, ys)) / sxx if sxx > 0 else 0.0
        m["fitted"][f"k_jitter_{dev}"] = r(k, 3)
    ks = [v for kk, v in m["fitted"].items() if kk.startswith("k_jitter_")]
    m["k_jitter"] = r(max(0.0, statistics.median(ks)), 3) if ks else m["k_jitter"]
    # base[dev][env] = sqrt(precision^2 + scale^2); track = scale (best case); unknown = 2x open
    for dev in ("fr935", "4315"):
        b = m["base"].setdefault(dev, dict(m["base"]["default"]))
        p = prec.get(dev, {})
        if "open" in p:
            b["open"] = r(math.hypot(p["open"]["robust_sd_pct"], scale))
        if "urban_or_trees" in p:
            b["urban_or_trees"] = r(max(b["open"], math.hypot(p["urban_or_trees"]["robust_sd_pct"], scale)))
        else:
            b["urban_or_trees"] = r(max(b["urban_or_trees"], 1.5 * b["open"]))
        b["track"] = r(scale)
        b["unknown"] = r(2 * b["open"])
    m["base"]["default"] = dict(m["base"].get("4315", m["base"]["default"]))
    m["j_ref"]["default"] = m["j_ref"].get("4315", m["j_ref"]["default"])
    return m


def bias_check(devs, races, clusters_out, bislet):
    res = {}
    cert = [x for x in races if x.get("certified") and "garmin_pct" in x]
    nom = [x for x in races if not x.get("certified") and "garmin_pct" in x]
    res["certified_races_garmin_pct"] = [x["garmin_pct"] for x in cert]
    res["nominal_races_garmin_pct"] = [x["garmin_pct"] for x in nom]
    res["bislet_session_pct"] = {k: v["session_pcts"] for k, v in bislet.items()}
    res["bislet_lap_median_pct"] = {k: v["lap_median_pct"] for k, v in bislet.items()}
    eras = [c["new_vs_old_pct"] for c in clusters_out if c.get("new_vs_old_pct") is not None]
    res["clusters_new_vs_old_pct"] = eras
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("person")
    ap.add_argument("--no-osm", action="store_true")
    ap.add_argument("--osm-clusters", type=int, default=0, help="biggest clusters to map-match (max 3)")
    ap.add_argument("--osm-fetch", action="store_true", help="query Overpass for traces not in the cache (<= 10 requests)")
    args = ap.parse_args()
    t0 = time.time()
    acts = load_all(args.person)
    t_load = time.time() - t0
    races = race_table(acts)
    cands = candidates(acts)
    clusters = cluster(cands)
    clusters_out, devs = cluster_stats(cands, clusters)
    t_cl = time.time() - t0
    bis = bislet_laps(args.person)
    model = fit_model(devs, bis, races)
    model["env_jitter"] = dict(ENV_JITTER)
    osm = osm_part(args.person, acts, cands, clusters, not args.no_osm, min(3, args.osm_clusters), args.osm_fetch)
    # per-era summary of spread
    era = {}
    for dev in sorted({d["device"] for d in devs}):
        cvs = [c["eras"][dev]["robust_cv_pct"] for c in clusters_out
               if dev in c["eras"] and c["eras"][dev]["robust_cv_pct"] is not None]
        v = [d["dev_pct"] * math.sqrt(d["n"] / (d["n"] - 1)) for d in devs if d["device"] == dev]
        v3 = [d["dev_pct"] * math.sqrt(d["n"] / (d["n"] - 1)) for d in devs if d["device"] == dev and d["n"] >= 3]
        era[dev] = {"clusters_n3": len(cvs), "groups_n2plus": len({d["cluster"] for d in devs if d["device"] == dev}),
                    "runs": len(v), "runs_in_n3": len(v3), "pooled_robust_sd_pct_n3": r(rsd(v3)), "median_cluster_robust_cv_pct": r(statistics.median(cvs)) if cvs else None,
                    "pooled_robust_sd_pct": r(rsd(v)), "pooled_sd_pct": r(statistics.pstdev(v)) if len(v) > 1 else None, "pooled_p90_abs_pct": r(q_([abs(x) for x in v], 0.9))}
    acts_out = []
    for x in acts:
        q = {k: v for k, v in x["q"].items()}
        acts_out.append({"id": x["id"], "date": x["date"], "type": x["type"], **q,
                         "distance_uncertainty_pct": uncertainty_pct(q, model)})
    # in-sample check: share of cluster runs whose deviation from the cluster median is within
    # the predicted 1 sigma (scale part is common to a route, so this should be >= 68 %)
    qby = {x["id"]: x["q"] for x in acts}
    cov = [abs(d["dev_pct"]) * math.sqrt(d["n"] / (d["n"] - 1)) <= uncertainty_pct(qby[d["id"]], model) for d in devs]
    model["check"] = {"cluster_runs": len(cov), "within_1sigma_share": r(sum(cov) / len(cov)) if cov else None}
    by_env = defaultdict(list)
    for a in acts_out:
        by_env[(a["device"], a["env"])].append(a["distance_uncertainty_pct"])
    model["typical"] = {f"{d}/{e}": {"n": len(v), "median": r(statistics.median(v)), "p90": r(q_(v, 0.9))}
                        for (d, e), v in sorted(by_env.items())}
    out = {"generated": time.strftime("%Y-%m-%d %H:%M"), "n_activities": len(acts), "n_candidates": len(cands),
           "params": {"end_tol_m": END_TOL, "mean_tol_m": MEAN_TOL, "max_tol_m": MAX_TOL, "dist_tol": DIST_TOL, "align_p90_tol": f"{ALIGN_TOL} m + {100 * ALIGN_REL} % of length", "n_resample": N_RES},
           "uncertainty_model": model, "races": races, "spread_by_era": era, "clusters": clusters_out,
           "cluster_deviations": [{k: (r(v, 3) if isinstance(v, float) else v) for k, v in d.items()} for d in devs],
           "bislet": bis, "bias": bias_check(devs, races, clusters_out, bis), "osm": osm,
           "activities": acts_out,
           "runtime_s": {"load": r(t_load, 1), "load+cluster": r(t_cl, 1), "total": r(time.time() - t0, 1)}}
    f = ROOT / "data" / args.person / "route_accuracy.json"
    f.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(f"{len(acts)} activities, {len(cands)} outdoor candidates, {len(clusters_out)} clusters; "
          f"{out['runtime_s']} -> {f}")


if __name__ == "__main__":
    main()

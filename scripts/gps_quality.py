"""Per-activity GPS quality indicators and a distance uncertainty (1 sigma, %).

    from gps_quality import quality, uncertainty_pct
    q = quality(Path(".../<id>.fit"))            # dict of indicators (fast: own FIT reader)
    u = uncertainty_pct(q, model)                # model = route_accuracy.json["uncertainty_model"] or None

Indicators (only GPS points while moving count; see `quality`):
  rec_interval_s       median time between records with a position
  gap_share_5s         share of position gaps > 5 s
  pos_vs_field_pct     haversine sum of positions vs the watch distance field over the same span (%)
  jitter_m             median lateral residual of each point vs a local quadratic fit of its
                       neighbours (+-6 s, or +-12 s with smart recording), m
  heading_changes_per_km  turns > 60 deg between consecutive >= 3 m segments at running speed
  speed_spikes_per_km  segments where position speed > 1.5x distance-field speed + 1 m/s
  gps_metadata         share of records with a gps_metadata message (new watch only); the
                       messages hold only altitude/speed: satellites/accuracy aren't decodable
  env                  track / open / urban_or_trees / indoor / unknown (coarse guess)

The reader decodes only the FIT messages needed here (file_id, session, lap, record,
gps_metadata, device_info) with struct; it's ~10x faster than fitparse. On any decoding error
it falls back to fitparse.
"""

from __future__ import annotations

import calendar
import math
import statistics
import struct
from pathlib import Path

SEMI = 180 / 2**31
FIT_EPOCH = 631065600  # 1989-12-31 UTC in unix seconds
BISLET = (59.92527, 10.73306)
NEW_WATCH_FROM = "2026-02-18"

# base type -> (struct code, size, invalid value)
_BASE = {0x00: ("B", 1, 0xFF), 0x01: ("b", 1, 0x7F), 0x02: ("B", 1, 0xFF), 0x83: ("h", 2, 0x7FFF),
         0x84: ("H", 2, 0xFFFF), 0x85: ("i", 4, 0x7FFFFFFF), 0x86: ("I", 4, 0xFFFFFFFF),
         0x88: ("f", 4, None), 0x89: ("d", 8, None), 0x0A: ("B", 1, 0), 0x8B: ("H", 2, 0),
         0x8C: ("I", 4, 0), 0x8E: ("q", 8, 0x7FFFFFFFFFFFFFFF), 0x8F: ("Q", 8, 0xFFFFFFFFFFFFFFFF),
         0x90: ("Q", 8, 0)}
# global message -> {field number: name}
_WANT = {
    0: {1: "manufacturer", 2: "product", 4: "time_created"},
    18: {2: "start_time", 5: "sport", 6: "sub_sport", 7: "total_elapsed_time", 9: "total_distance"},
    19: {2: "start_time", 7: "total_elapsed_time", 9: "total_distance", 24: "lap_trigger"},
    20: {0: "lat", 1: "lon", 5: "distance", 6: "speed", 73: "enhanced_speed"},
    23: {2: "manufacturer", 4: "product"},
    160: {},
}
_NAMES = {0: "file_id", 18: "session", 19: "lap", 20: "record", 23: "device_info", 160: "gps_metadata"}
_SPORT = {1: "running", 0: "generic"}
_SUB = {0: "generic", 1: "treadmill", 2: "street", 3: "trail", 4: "track", 45: "indoor_running"}


def _read_fast(path: Path) -> dict:
    data = Path(path).read_bytes()
    out = {k: [] for k in _NAMES.values()}
    pos = 0
    while pos + 12 <= len(data):
        hsize = data[pos]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        if data[pos + 8:pos + 12] != b".FIT":
            raise ValueError("not a FIT file")
        p, end = pos + hsize, pos + hsize + size
        defs, last_ts = {}, 0
        while p < end:
            h = data[p]; p += 1
            if h & 0x80:  # compressed timestamp header
                local, off = (h >> 5) & 3, h & 0x1F
                ts = (last_ts & ~0x1F) + off + (0x20 if off < (last_ts & 0x1F) else 0)
                last_ts = ts
            elif h & 0x40:  # definition
                local = h & 0x0F
                arch = data[p + 1]
                gnum = struct.unpack_from(">H" if arch else "<H", data, p + 2)[0]
                n = data[p + 4]; p += 5
                fmt, names, inv, total = [">" if arch else "<"], [], [], 0
                want = _WANT.get(gnum)
                for i in range(n):
                    fnum, fsize, btype = data[p], data[p + 1], data[p + 2]; p += 3
                    code, bsize, invalid = _BASE.get(btype, ("s", 1, None))
                    if fsize == bsize and code != "s":
                        fmt.append(code)
                    else:
                        fmt.append(f"{fsize}s"); invalid = None
                    names.append(want.get(fnum, "timestamp" if fnum == 253 else None) if want is not None
                                 else ("timestamp" if fnum == 253 else None))
                    inv.append(invalid); total += fsize
                if h & 0x20:  # developer fields
                    nd = data[p]; p += 1
                    for i in range(nd):
                        dsize = data[p + 1]; p += 3
                        fmt.append(f"{dsize}x"); total += dsize
                defs[local] = (gnum, struct.Struct("".join(fmt)), names, inv, total)
                continue
            else:
                local, ts = h & 0x0F, None
            gnum, st, names, inv, total = defs[local]
            if gnum in _NAMES:
                vals = st.unpack_from(data, p)
                m = {}
                for nm, v, iv in zip(names, vals, inv):
                    if nm is not None and v != iv:
                        m[nm] = v
                if "timestamp" in m:
                    last_ts = m["timestamp"]
                elif ts is not None:
                    m["timestamp"] = ts
                out[_NAMES[gnum]].append(m)
            elif "timestamp" in names:  # keep last_ts in sync for compressed headers
                if True:
                    i = names.index("timestamp")
                    v = st.unpack_from(data, p)[i]
                    if v != inv[i]:
                        last_ts = v
            p += total
        pos = end + 2
    return out


def _read_fitparse(path: Path) -> dict:
    import fitparse
    ff = fitparse.FitFile(str(path))
    ren = {"position_lat": "lat", "position_long": "lon"}
    out = {k: [] for k in _NAMES.values()}
    for m in ff.get_messages(list(_NAMES.values())):
        d = {}
        for f in m:
            if f.value is None:
                continue
            v = f.value
            if hasattr(v, "timestamp"):
                v = calendar.timegm(v.utctimetuple()) - FIT_EPOCH
            d[ren.get(f.name, f.name)] = v
        if m.name == "record":
            for k, s in (("distance", 100), ("speed", 1000), ("enhanced_speed", 1000)):
                if k in d:
                    d[k] = d[k] * s
        out[m.name].append(d)
    for m in out["session"] + out["lap"]:
        for k, s in (("total_elapsed_time", 1000), ("total_distance", 100)):
            if k in m:
                m[k] = m[k] * s
    return out


def load(path: Path) -> dict:
    """Parsed activity: rows [(t, lat, lon, dist)], laps, session, device, sport, gps_meta count."""
    try:
        raw = _read_fast(path)
    except Exception:
        raw = _read_fitparse(path)
    recs = [r for r in raw["record"] if "timestamp" in r]
    if not recs:
        raise ValueError(f"{path}: no records")
    t0 = recs[0]["timestamp"]
    rows = []
    for r in recs:
        la, lo = r.get("lat"), r.get("lon")
        p = (la * SEMI, lo * SEMI) if la is not None and lo is not None and (la or lo) else None
        d = r.get("distance")
        rows.append((r["timestamp"] - t0, p, d / 100 if d is not None else None))
    rows.sort(key=lambda x: x[0])
    sess = raw["session"][0] if raw["session"] else {}
    prods = [d.get("product") or d.get("garmin_product") for d in raw["file_id"] + raw["device_info"]
             if (d.get("product") or d.get("garmin_product")) and d.get("manufacturer") in (1, "garmin", None)]
    prod = prods[0] if prods else None
    device = "fr935" if prod in (2691, "fr935") else (str(prod) if prod else "?")
    laps = [{"start": (m.get("start_time", t0) - t0), "time": m.get("total_elapsed_time", 0) / 1000,
             "dist": m.get("total_distance", 0) / 100, "trigger": m.get("lap_trigger")} for m in raw["lap"]]
    return {"rows": rows, "laps": laps, "t0_unix": t0 + FIT_EPOCH, "device": device,
            "sport": _SPORT.get(sess.get("sport"), sess.get("sport")),
            "sub_sport": _SUB.get(sess.get("sub_sport"), sess.get("sub_sport")),
            "session_distance": sess.get("total_distance", 0) / 100 if sess.get("total_distance") else None,
            "elapsed": sess.get("total_elapsed_time", 0) / 1000 if sess.get("total_elapsed_time") else None,
            "gps_meta": len(raw["gps_metadata"]), "n_records": len(recs)}


def hav(a, b):
    r = 6371008.8
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def _xy(pts, c):
    kx = 111320 * math.cos(math.radians(c[0]))
    return [((p[1] - c[1]) * kx, (p[0] - c[0]) * 110574) for p in pts]


def _det3(m):
    return (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
            + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))


def quality_from(a: dict) -> dict:
    rows = a["rows"]
    gp = [(t, p, d) for t, p, d in rows if p is not None]
    q = {"device": a["device"], "sport": a["sport"], "sub_sport": a["sub_sport"], "n_records": a["n_records"],
         "n_pos": len(gp), "session_distance": a["session_distance"],
         "gps_metadata": round(min(1.0, a["gps_meta"] / max(1, a["n_records"])), 3), "satellites": None}
    dist = [d for _, _, d in rows if d is not None]
    q["record_distance"] = round(dist[-1] - dist[0], 1) if len(dist) > 1 else None
    if len(gp) < 30:
        q.update(rec_interval_s=None, gap_share_5s=None, pos_vs_field_pct=None, jitter_m=None,
                 heading_changes_per_km=None, speed_spikes_per_km=None, gps_distance=None, bislet_share=None)
        q["env"] = "indoor" if len(gp) == 0 or a["sub_sport"] in ("treadmill", "indoor_running") else "unknown"
        return q
    c = gp[len(gp) // 2][1]
    ts = [t for t, _, _ in gp]
    xy = _xy([p for _, p, _ in gp], c)
    dts = [b - a_ for a_, b in zip(ts, ts[1:])]
    seg = [math.hypot(x2 - x1, y2 - y1) for (x1, y1), (x2, y2) in zip(xy, xy[1:])]
    # moving = segment speed 1.5..8 m/s and gap <= 10 s
    moving = [dt > 0 and dt <= 10 and 1.5 <= s / dt <= 8 for s, dt in zip(seg, dts)]
    mdts = [dt for dt, m in zip(dts, moving) if m]
    q["rec_interval_s"] = statistics.median(mdts) if mdts else statistics.median(dts)
    q["gap_share_5s"] = round(sum(dt > 5 for dt in dts) / len(dts), 3)
    gdist = sum(hav(p, q_) for (_, p, _), (_, q_, _) in zip(gp, gp[1:]))
    q["gps_distance"] = round(gdist, 1)
    # positions vs distance field over moving segments with distance-field values at both ends
    sp = sf = 0.0
    spikes = 0
    for i, m in enumerate(moving):
        d1, d2 = gp[i][2], gp[i + 1][2]
        if not m or d1 is None or d2 is None:
            continue
        sp += seg[i]; sf += d2 - d1
        if seg[i] / dts[i] > 1.5 * (d2 - d1) / dts[i] + 1.0:
            spikes += 1
    km = max(sf, 1.0) / 1000
    q["pos_vs_field_pct"] = round(100 * (sp / sf - 1), 2) if sf > 500 else None
    q["speed_spikes_per_km"] = round(spikes / km, 2) if sf > 500 else None
    # lateral jitter: residual of each point from a local quadratic fit (in time) of the track,
    # perpendicular to the local heading; quadratic so steady curves (track bends) don't count.
    # Window +-6 s (1 s recording) / +-12 s (smart recording), at least 2 points each side.
    win = 6 if q["rec_interval_s"] <= 1.5 else 12
    offs = []
    n = len(xy)
    lo = hi = 0
    for i in range(1, n - 1):
        if not (moving[i - 1] and moving[i]):
            continue
        while ts[lo] < ts[i] - win:
            lo += 1
        while hi < n and ts[hi] <= ts[i] + win:
            hi += 1
        if i - lo < 2 or hi - 1 - i < 2:
            continue
        S = [0.0] * 5; X = [0.0] * 3; Y = [0.0] * 3
        for j in range(lo, hi):
            if j == i:
                continue  # leave-one-out: predict point i from its neighbours
            tau = ts[j] - ts[i]; t2 = tau * tau
            S[0] += 1; S[1] += tau; S[2] += t2; S[3] += t2 * tau; S[4] += t2 * t2
            x, y = xy[j]
            X[0] += x; X[1] += x * tau; X[2] += x * t2
            Y[0] += y; Y[1] += y * tau; Y[2] += y * t2
        M = ((S[0], S[1], S[2]), (S[1], S[2], S[3]), (S[2], S[3], S[4]))
        det = _det3(M)
        if abs(det) < 1e-9:
            continue
        # intercept (value at tau=0) by Cramer's rule, slope for the heading
        ax = _det3(((X[0], S[1], S[2]), (X[1], S[2], S[3]), (X[2], S[3], S[4]))) / det
        ay = _det3(((Y[0], S[1], S[2]), (Y[1], S[2], S[3]), (Y[2], S[3], S[4]))) / det
        bx = _det3(((S[0], X[0], S[2]), (S[1], X[1], S[3]), (S[2], X[2], S[4]))) / det
        by = _det3(((S[0], Y[0], S[2]), (S[1], Y[1], S[3]), (S[2], Y[2], S[4]))) / det
        hn = math.hypot(bx, by)
        if hn < 1.0:
            continue
        offs.append(abs((xy[i][0] - ax) * by - (xy[i][1] - ay) * bx) / hn)
    q["jitter_m"] = round(statistics.median(offs), 2) if offs else None
    # sharp heading changes at running speed, over segments >= 3 m
    turns, prev = 0, None
    for i, m in enumerate(moving):
        if not m or seg[i] < 3:
            prev = None if not m else prev
            continue
        h = math.atan2(xy[i + 1][1] - xy[i][1], xy[i + 1][0] - xy[i][0])
        if prev is not None:
            d = abs((h - prev + math.pi) % (2 * math.pi) - math.pi)
            if d > math.radians(60):
                turns += 1
        prev = h
    q["heading_changes_per_km"] = round(turns / km, 2) if sf > 500 else None
    # environment
    kx = 111320 * math.cos(math.radians(BISLET[0]))
    near = sum(math.hypot((p[1] - BISLET[1]) * kx, (p[0] - BISLET[0]) * 110574) < 90 for _, p, _ in gp)
    q["bislet_share"] = round(near / len(gp), 3)
    q["env"] = env_guess(q)
    return q


# jitter thresholds (m) above which a run counts as urban/trees, per device (gap in the
# distribution: FR935 open runs <= 1.1 m, a group of forest/holiday runs 2.0-2.4 m; the new watch
# filters positions harder, 0.1-0.25 m). Copied to uncertainty_model["env_jitter"].
ENV_JITTER = {"fr935": 1.5, "default": 0.4}


def _key(q: dict, table: dict) -> str:
    """table key for this activity: its device, else by recording mode (smart recording ->
    the FR935 parameters, 1 s -> "default")."""
    if q.get("device") in table:
        return q["device"]
    smart = (q.get("rec_interval_s") or 1) > 1.5
    return "fr935" if smart and "fr935" in table else "default"


def env_guess(q: dict, model: dict | None = None) -> str:
    if q.get("n_pos", 0) < 30:
        return "indoor" if q.get("n_pos", 0) == 0 else "unknown"
    if q.get("sub_sport") == "track" or (q.get("bislet_share") or 0) > 0.6:
        return "track"
    th = (model or {}).get("env_jitter") or ENV_JITTER
    t = th[_key(q, th)]
    j = q.get("jitter_m")
    if j is None:
        return "unknown"
    return "urban_or_trees" if j > t else "open"


def quality(fit_path) -> dict:
    a = load(Path(fit_path))
    q = quality_from(a)
    q["start_unix"] = a["t0_unix"]
    return q


# default model: 1 sigma (%) per device era and environment, plus a term per extra jitter
DEFAULT_MODEL = {
    "form": "sqrt(base[device][env]^2 + (k_jitter * max(0, jitter_m - j_ref[device]))^2 + (100 * end_m / distance_m)^2)",
    "base": {"fr935": {"track": 0.6, "open": 1.0, "urban_or_trees": 1.5, "unknown": 2.0},
             "default": {"track": 0.5, "open": 0.8, "urban_or_trees": 1.2, "unknown": 2.0}},
    "k_jitter": 0.3, "j_ref": {"fr935": 0.45, "default": 0.2},
    "end_m": 5.0,  # start/stop position error, matters for short runs only
    "indoor_pct": 5.0,
}


def uncertainty_pct(q: dict, model: dict | None = None) -> float:
    """Distance uncertainty, 1 sigma in % of the watch distance."""
    m = model or DEFAULT_MODEL
    env = q.get("env") or env_guess(q, m)
    if env == "indoor":
        return float(m.get("indoor_pct", 5.0))
    base = m["base"][_key(q, m["base"])]
    b = base.get(env, base.get("unknown", 2.0))
    j = q.get("jitter_m")
    jr = m["j_ref"][_key(q, m["j_ref"])]
    extra = m["k_jitter"] * max(0.0, j - jr) if j is not None and env != "track" else 0.0
    d = q.get("session_distance") or q.get("record_distance") or 0
    ends = 100 * m.get("end_m", 5.0) / d if d > 100 else 0.0
    return round(math.sqrt(b * b + extra * extra + ends * ends), 2)

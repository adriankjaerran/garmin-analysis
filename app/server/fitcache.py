"""Pre-parse FIT files into a fast cache for the app.

    uv run garmin-app-cache adrian            # or: uv run python -m app.server.fitcache adrian
    uv run garmin-app-cache adrian --force    # rebuild everything

Writes (incremental; keyed by FIT mtime + size, plus groups.json mtime for the rep analysis):
  data/<p>/cache/series/<id>.npz   per-record arrays t (s from first record), lat, lon, hr, speed (m/s),
                                    alt, dist, cad (spm), power + `meta` JSON (laps, start time, reps, breaks)
  data/<p>/cache/tracks.json       {id: {poly, poly_lo, bbox, n}} simplified encoded polylines
                                    (poly: 4 m tolerance for day/activity mini maps, poly_lo: 20 m for the
                                    overview map)

FIT decoding reuses gps_quality._read_fast (pure-python struct reader, ~20x faster than fitparse).
Reps/breaks for interval runs (groups.json has `reps`) come from run_analysis.analyse (fitparse, slow,
run in a process pool once).
"""

from __future__ import annotations

import argparse
import json
import math
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np

from . import ROOT  # noqa: F401  (puts scripts/ on sys.path)
import gps_quality as gq  # noqa: E402
import session_groups as sg  # noqa: E402  (pool_swim; also registers the length / pool-length fields)

CACHE_VERSION = 4   # 3: pool swims from the FIT length messages (series, lap starts >= 0, meta.swim); 4: clock jumps fixed
REPS_SINCE_VERSION = 4   # rep analyses of older caches are redone (4: run_analysis takes clock jumps out)
FIT_EPOCH = gq.FIT_EPOCH
SEMI = gq.SEMI

# extend the fast reader with the fields the app needs (additive, same as session_groups.py does)
gq._WANT[20].update({2: "altitude", 3: "heart_rate", 4: "cadence", 7: "power", 53: "fractional_cadence",
                     78: "enhanced_altitude"})
gq._WANT[19].update({8: "total_timer_time", 13: "avg_speed", 14: "max_speed", 15: "avg_heart_rate",
                     16: "max_heart_rate", 21: "total_ascent", 22: "total_descent", 23: "intensity",
                     110: "enhanced_avg_speed"})
gq._WANT[21] = {0: "event", 1: "event_type"}
gq._NAMES[21] = "event"

COLS = ["t", "lat", "lon", "hr", "speed", "alt", "dist", "cad", "power"]
INTENSITY = {0: "active", 1: "rest", 2: "warmup", 3: "cooldown", 4: "recovery", 5: "interval", 6: "other"}
TRIGGER = {0: "manual", 1: "time", 2: "distance", 3: "position_start", 4: "position_lap", 5: "position_waypoint",
           6: "position_marked", 7: "session_end", 8: "fitness_equipment"}


def person_dir(person: str) -> Path:
    from . import ROOT
    return ROOT / "data" / person


# ---------------------------------------------------------------- polyline helpers
def encode_polyline(lat, lon, precision: int = 5) -> str:
    f = 10 ** precision
    out, plat, plon = [], 0, 0
    for la, lo in zip(lat, lon):
        ila, ilo = int(round(la * f)), int(round(lo * f))
        for v in (ila - plat, ilo - plon):
            v = ~(v << 1) if v < 0 else (v << 1)
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        plat, plon = ila, ilo
    return "".join(out)


def simplify(lat: np.ndarray, lon: np.ndarray, tol_m: float) -> np.ndarray:
    """Douglas-Peucker on a local metric projection; returns kept indices (sorted)."""
    n = len(lat)
    if n <= 2:
        return np.arange(n)
    c = math.cos(math.radians(float(np.nanmean(lat))))
    x = (lon - lon[0]) * 111320 * c
    y = (lat - lat[0]) * 111320
    keep = np.zeros(n, bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        dx, dy = x[b] - x[a], y[b] - y[a]
        L = math.hypot(dx, dy)
        px, py = x[a + 1:b] - x[a], y[a + 1:b] - y[a]
        d = np.abs(px * dy - py * dx) / L if L > 0 else np.hypot(px, py)
        i = int(np.argmax(d))
        if d[i] > tol_m:
            m = a + 1 + i
            keep[m] = True
            stack += [(a, m), (m, b)]
    return np.flatnonzero(keep)


# ---------------------------------------------------------------- FIT -> arrays
def _swim_arrays(swim: dict, recs: list, t0: int) -> dict:
    """Pool swim on a 1 s grid from the active lengths: speed = pool length / length time while swimming (NaN in
    rests, so charts show gaps), distance cumulative, cad = strokes per minute; HR from the records if any.
    The records of a pool swim come in bursts (one write per few lengths), so their own time base is unusable."""
    T = int(max(swim.get("elapsed_s") or 0, max((a + d for a, d, *_ in swim["lengths_detail"]), default=0))) + 1
    t = np.arange(T + 1)
    speed = np.full(T + 1, np.nan); cad = np.full(T + 1, np.nan)
    dist = np.full(T + 1, np.nan)
    pool, done = swim["pool_m"], 0.0
    dist[0] = 0.0
    for a, d, strokes, _ in swim["lengths_detail"]:
        a0, a1 = int(round(a)), int(round(a + d))
        if a1 <= a0 or a0 < 0:
            continue
        speed[a0:a1] = pool / d
        if strokes:
            cad[a0:a1] = strokes / d * 60
        dist[a0:a1 + 1] = done + pool * (np.arange(a0, a1 + 1) - a0) / max(a1 - a0, 1)
        done += pool
    hr = np.full(T + 1, np.nan)
    hv = [(r["timestamp"] - t0, r["heart_rate"]) for r in recs if r.get("heart_rate") and 30 < r["heart_rate"] < 230]
    if len(hv) >= 2:
        ht, hh = np.array(hv, float).T
        hr = np.interp(t, ht, hh, left=np.nan, right=np.nan)
    nan = np.full(T + 1, np.nan)
    return {"t": t.astype(np.int32), "lat": nan.copy(), "lon": nan.copy(), "hr": hr.astype(np.float32),
            "speed": speed.astype(np.float32), "alt": nan.astype(np.float32), "dist": dist.astype(np.float32),
            "cad": cad.astype(np.float32) if np.isfinite(cad).any() else np.zeros(0, np.float32),
            "power": np.zeros(0, np.float32)}


def read_fit(fit: Path) -> dict:
    raw = gq._read_fast(fit)
    recs = sorted((r for r in raw["record"] if "timestamp" in r), key=lambda r: r["timestamp"])
    if len(recs) < 2:
        raise ValueError("no records")
    jumps = sg.fit_clock_jumps(raw, recs)   # watch clock jumps: same time base as session_groups.read_series
    if jumps:
        recs = [{**r, "timestamp": gq.unjump(r["timestamp"], jumps)} for r in recs]
        raw = {**raw, "lap": [{**v, "start_time": gq.unjump(v["start_time"], jumps)} if v.get("start_time") else v
                              for v in raw["lap"]]}
    t0 = recs[0]["timestamp"]
    swim = sg.pool_swim(raw)
    if swim and raw["session"][0].get("start_time"):   # time origin = session start (records start late)
        t0 = min(t0, raw["session"][0]["start_time"])
        swim = sg.pool_swim(raw, t0)
    cols = {k: [] for k in COLS}
    last = None
    nan = float("nan")
    for r in recs:
        ts = r["timestamp"]
        if ts == last:
            continue
        last = ts
        cols["t"].append(ts - t0)
        la, lo = r.get("lat"), r.get("lon")
        ok = la is not None and lo is not None and (la or lo)
        cols["lat"].append(la * SEMI if ok else nan)
        cols["lon"].append(lo * SEMI if ok else nan)
        h = r.get("heart_rate")
        cols["hr"].append(h if h and 30 < h < 230 else nan)
        s = r.get("enhanced_speed", r.get("speed"))
        cols["speed"].append(s / 1000 if s is not None else nan)
        a = r.get("enhanced_altitude", r.get("altitude"))
        cols["alt"].append(a / 5 - 500 if a is not None else nan)
        d = r.get("distance")
        cols["dist"].append(d / 100 if d is not None else nan)
        c = r.get("cadence")
        cols["cad"].append(c * 2 + (r.get("fractional_cadence") or 0) / 64 if c is not None else nan)
        p = r.get("power")
        cols["power"].append(p if p is not None else nan)
    arr = {k: np.array(v, np.float64 if k in ("lat", "lon") else np.float32) for k, v in cols.items()}
    arr["t"] = arr["t"].astype(np.int32)
    if swim:
        arr = _swim_arrays(swim, recs, t0)
    # speed from distance when the speed field is missing (old files, indoor)
    elif np.isnan(arr["speed"]).all() and (~np.isnan(arr["dist"])).sum() > 2:
        d = np.interp(arr["t"], arr["t"][~np.isnan(arr["dist"])], arr["dist"][~np.isnan(arr["dist"])])
        v = np.gradient(d, np.maximum(arr["t"], 0).astype(float), edge_order=1) if len(d) > 2 else np.zeros_like(d)
        arr["speed"] = np.clip(v, 0, 15).astype(np.float32)
    for k in ("cad", "power"):
        if np.isnan(arr[k]).all():
            arr[k] = np.zeros(0, np.float32)  # absent column
    laps = []
    for v in raw["lap"]:
        st = v.get("start_time")
        sp = v.get("enhanced_avg_speed", v.get("avg_speed"))
        laps.append({
            "start": (st - t0) if st else None,
            "time": round(v["total_elapsed_time"] / 1000, 1) if v.get("total_elapsed_time") is not None else None,
            "timer": round(v["total_timer_time"] / 1000, 1) if v.get("total_timer_time") is not None else None,
            "dist": round(v["total_distance"] / 100, 1) if v.get("total_distance") is not None else None,
            "speed": round(sp / 1000, 3) if sp is not None else None,
            "hr": v.get("avg_heart_rate"), "maxhr": v.get("max_heart_rate"),
            "climb": v.get("total_ascent"), "desc": v.get("total_descent"),
            "intensity": INTENSITY.get(v.get("intensity")), "trigger": TRIGGER.get(v.get("lap_trigger")),
        })
        if swim:   # pool: a lap without distance is a rest at the wall; pace from the swimming time of its lengths
            L = laps[-1]
            L["kind"] = "rest" if not L["dist"] else "swim"
            if L["dist"] and L["start"] is not None and L["time"]:
                sw = sum(d for a, d, *_ in swim["lengths_detail"] if L["start"] - 1 <= a < L["start"] + L["time"])
                L["swim_s"] = round(sw, 1)
                L["pace_100m_s"] = round(sw / L["dist"] * 100, 1) if sw else None
    sess = raw["session"][0] if raw["session"] else {}
    meta = {"start_utc": int(t0 + FIT_EPOCH), "laps": laps, "sport": sess.get("sport"),
            "sub_sport": sess.get("sub_sport"), "cache_version": CACHE_VERSION}
    if swim:
        meta["swim"] = {**{k: v for k, v in swim.items() if k != "lengths_detail"},
                        "lengths": [{"start": round(a, 1), "time": d, "strokes": n, "stroke": st}
                                    for a, d, n, st in swim["lengths_detail"]], "n_lengths": swim["lengths"]}
    return {"arr": arr, "meta": meta}


def tracks_of(arr: dict) -> dict | None:
    ok = ~np.isnan(arr["lat"])
    if ok.sum() < 2:
        return None
    lat, lon = arr["lat"][ok], arr["lon"][ok]
    out = {"bbox": [round(float(lon.min()), 5), round(float(lat.min()), 5), round(float(lon.max()), 5),
                    round(float(lat.max()), 5)], "n": int(ok.sum())}
    for key, tol in (("poly", 4.0), ("poly_lo", 20.0)):
        k = simplify(lat, lon, tol)
        out[key] = encode_polyline(lat[k], lon[k])
    return out


def save_npz(path: Path, arr: dict, meta: dict) -> None:
    tmp = path.with_suffix(".tmp.npz")
    np.savez(tmp, meta=np.array(json.dumps(meta)), **arr)
    tmp.replace(path)


def load_npz(path: Path) -> tuple[dict, dict]:
    with np.load(path) as z:
        arr = {k: z[k] for k in z.files if k != "meta"}
        meta = json.loads(str(z["meta"]))
    return arr, meta


# ---------------------------------------------------------------- workers
REP_KEYS = ("reps", "breaks", "mode", "reps_err")   # added by the rep pass; kept when only the format changes


def carry_reps(meta: dict, old_npz: Path) -> dict:
    """copy the rep analysis of an older npz of the same FIT into a freshly parsed meta"""
    if old_npz.exists():
        try:
            _, om = load_npz(old_npz)
            meta.update({k: om[k] for k in REP_KEYS if k in om})
        except Exception:
            pass
    return meta


def _parse_job(job):
    aid, fit, out, keep_reps = job
    try:
        x = read_fit(Path(fit))
    except Exception as e:  # corrupt / empty files
        return aid, None, repr(e)[:120]
    if keep_reps:
        carry_reps(x["meta"], Path(out))
    save_npz(Path(out), x["arr"], x["meta"])
    return aid, tracks_of(x["arr"]), None


def _reps_job(job):
    aid, fit = job
    import run_analysis as ra
    try:
        x = ra.analyse(Path(fit))
    except Exception as e:
        return aid, None, repr(e)[:120]
    keep = ("start", "end", "time", "dist", "speed", "hr", "maxhr")
    return aid, {"reps": [{k: r.get(k) for k in keep} for r in x["reps"]],
                 "breaks": [{k: b.get(k) for k in keep + ("hr_peak", "hr_drop", "standing")} for b in x["breaks"]],
                 "mode": x["mode"]}, None


def build(person: str, force: bool = False, workers: int = 8, quiet: bool = False) -> dict:
    T0 = time.time()
    D = person_dir(person)
    sdir = D / "cache" / "series"
    sdir.mkdir(parents=True, exist_ok=True)
    mpath = D / "cache" / "series_manifest.json"
    tpath = D / "cache" / "tracks.json"
    manifest = json.loads(mpath.read_text()) if mpath.exists() and not force else {}
    old_reps = {}
    if manifest.get("_v") != CACHE_VERSION:   # new format: re-parse everything, keep rep analyses of unchanged FITs
        old_reps = {aid: m["reps_key"] for aid, m in manifest.items() if isinstance(m, dict) and m.get("reps_key")} \
            if manifest.get("_v", 0) >= REPS_SINCE_VERSION else {}
        manifest = {"_v": CACHE_VERSION}
    tracks = json.loads(tpath.read_text()) if tpath.exists() and not force and len(manifest) > 1 else {}
    fits = {p.stem: p for p in (D / "activities").glob("*/*.fit")}
    key = lambda p: f"{p.stat().st_mtime_ns}:{p.stat().st_size}"

    todo = [(aid, str(p), str(sdir / f"{aid}.npz"), old_reps.get(aid) == key(p)) for aid, p in fits.items()
            if (manifest.get(aid) or {}).get("key") != key(p)
            or ("err" not in manifest[aid] and not (sdir / f"{aid}.npz").exists())]
    errors = {aid: m["err"] for aid, m in manifest.items() if isinstance(m, dict) and "err" in m}
    with Pool(workers) as pool:
        for aid, tr, err in pool.imap_unordered(_parse_job, todo, chunksize=4):
            if err:
                errors[aid] = err
                manifest[aid] = {"key": key(fits[aid]), "err": err}
                continue
            manifest[aid] = {"key": key(fits[aid])}
            if old_reps.get(aid) == manifest[aid]["key"]:
                manifest[aid]["reps_key"] = old_reps[aid]
            if tr:
                tracks[aid] = tr
            else:
                tracks.pop(aid, None)

        # reps for interval runs
        gpath = D / "groups.json"
        groups = json.loads(gpath.read_text()) if gpath.exists() else {"activities": {}}
        gkey = str(gpath.stat().st_mtime_ns) if gpath.exists() else ""
        want = [aid for aid, a in groups["activities"].items() if a.get("reps") and aid in fits
                and aid in manifest and "err" not in manifest[aid]]
        rtodo = [(aid, str(fits[aid])) for aid in want if manifest[aid].get("reps_key") != manifest[aid]["key"]]
        for aid, res, err in pool.imap_unordered(_reps_job, rtodo, chunksize=2):
            p = sdir / f"{aid}.npz"
            arr, meta = load_npz(p)
            if res:
                meta.update(res)
            else:
                meta["reps_err"] = err
            save_npz(p, arr, meta)
            manifest[aid]["reps_key"] = manifest[aid]["key"]
    manifest["_groups"] = gkey
    for aid in [a for a in tracks if a not in fits]:
        tracks.pop(aid)
    tpath.write_text(json.dumps(tracks, separators=(",", ":")))
    mpath.write_text(json.dumps(manifest))
    res = {"parsed": len(todo), "reps": len(rtodo), "errors": errors, "tracks": len(tracks),
           "seconds": round(time.time() - T0, 1)}
    if not quiet:
        print(f"series cache: parsed {len(todo)} FIT, {len(rtodo)} rep analyses, {len(tracks)} tracks, "
              f"{len(errors)} errors, {res['seconds']} s -> {sdir}")
        for aid, e in list(errors.items())[:10]:
            print("  error", aid, e)
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("person", nargs="?", default="adrian")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    build(a.person, a.force, a.workers)


if __name__ == "__main__":
    main()

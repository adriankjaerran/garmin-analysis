"""Data access for the app: everything is loaded lazily once and reloaded when a source file's mtime
changes (cheap stat() per request). Per-activity series come from the npz cache built by fitcache.py
(a missing or stale npz is rebuilt on the fly from the FIT)."""

from __future__ import annotations

import glob
import json
import math
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

import numpy as np

from . import ROOT
from . import fitcache

FAMILY = {
    "running": "run", "track_running": "run", "treadmill_running": "run", "indoor_running": "run",
    "trail_running": "run", "street_running": "run",
    "cross_country_skiing_ws": "ski", "skate_skiing_ws": "ski", "backcountry_skiing": "ski",
    "hiking": "hike", "walking": "hike",
    "cycling": "bike", "road_biking": "bike", "mountain_biking": "bike", "indoor_cycling": "bike",
    "lap_swimming": "swim", "open_water_swimming": "swim",
}
SPORTS = ["run", "ski", "hike", "bike", "swim", "other"]


def family(type_key: str | None) -> str:
    return FAMILY.get(type_key or "", "other")


def _r(v, n=2):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f):
        return None
    return round(f, n) if n else int(round(f))


def _json(p: Path):
    return json.loads(p.read_text())


class Store:
    def __init__(self, person: str):
        self.person = person
        self.dir = ROOT / "data" / person
        self.cache_dir = self.dir / "cache"
        self._memo: dict[str, tuple[Any, Any]] = {}
        self._series: OrderedDict[str, tuple[Any, dict, dict]] = OrderedDict()
        self._lock = threading.RLock()

    # ------------------------------------------------------------ mtime memo
    def _memoize(self, name: str, paths: Callable[[], list[Path]], build: Callable[[], Any]):
        ps = paths()
        sig = tuple((str(p), p.stat().st_mtime_ns) for p in ps if p.exists())
        with self._lock:
            hit = self._memo.get(name)
            if hit and hit[0] == sig:
                return hit[1]
        val = build()
        with self._lock:
            self._memo[name] = (sig, val)
        return val

    # ------------------------------------------------------------ sources
    def groups(self) -> dict:
        p = self.dir / "groups.json"
        return self._memoize("groups", lambda: [p], lambda: _json(p) if p.exists() else
                             {"fields": {}, "places": {}, "areas": {}, "routes": {}, "activities": {}})

    def tracks(self) -> dict:
        p = self.cache_dir / "tracks.json"
        return self._memoize("tracks", lambda: [p], lambda: _json(p) if p.exists() else {})

    def folders(self) -> dict[str, Path]:
        d = self.dir / "activities"

        def build():
            return {p.name.split("_")[1]: p for p in d.glob("*_*_*") if p.is_dir()}
        return self._memoize("folders", lambda: [d], build)

    def activities(self) -> list[dict]:
        idx = self.dir / "activities_index.json"
        gp = self.dir / "groups.json"
        tp = self.cache_dir / "tracks.json"
        return self._memoize("activities", lambda: [idx, gp, tp, self.dir / "activities"], self._build_activities)

    def _build_activities(self) -> list[dict]:
        g = self.groups()["activities"]
        tracks = self.tracks()
        folders = self.folders()
        out = []
        for a in _json(self.dir / "activities_index.json"):
            aid = str(a["activityId"])
            ga = g.get(aid) or {}
            tk = a["activityType"]["typeKey"]
            folder = folders.get(aid)
            out.append({
                "id": aid,
                "start": a["startTimeLocal"],
                "start_gmt": a.get("startTimeGMT"),
                "date": a["startTimeLocal"][:10],
                "name": a.get("activityName"),
                "type": tk,
                "sport": family(tk),
                "dist_km": _r((a.get("distance") or 0) / 1000, 2) or None,
                "dur_s": _r(a.get("duration"), 0),
                "moving_s": ga.get("moving_s") or _r(a.get("movingDuration"), 0),
                "hr": _r(a.get("averageHR"), 0), "maxhr": _r(a.get("maxHR"), 0),
                "elev": _r(a.get("elevationGain"), 0),
                "load": _r(a.get("activityTrainingLoad"), 0), "te": _r(a.get("aerobicTrainingEffect"), 1),
                "kcal": _r(a.get("calories"), 0),
                "location": a.get("locationName"),
                "lat": a.get("startLatitude"), "lon": a.get("startLongitude"),
                "place": ga.get("place"), "area": ga.get("area"), "route": ga.get("route"),
                "stype": ga.get("type"), "type_detail": ga.get("type_detail"), "sig": ga.get("sig"),
                "fit": bool(folder and any(folder.glob("*.fit"))),
                "track": aid in tracks,
            })
        out.sort(key=lambda x: x["start"], reverse=True)
        return out

    def activity_map(self) -> dict[str, dict]:
        acts = self.activities()
        with self._lock:
            hit = self._memo.get("activity_map")
            if hit and hit[0] is acts:
                return hit[1]
            m = {a["id"]: a for a in acts}
            self._memo["activity_map"] = (acts, m)
            return m

    def daily(self) -> dict[str, dict]:
        pat = str(self.dir / "ranges" / "daily_summaries_[0-9][0-9][0-9][0-9].json")

        def build():
            import build_report as br
            return {d["date"]: d for d in br.daily(self.dir)}
        return self._memoize("daily", lambda: [Path(p) for p in sorted(glob.glob(pat))], build)

    def sleep(self) -> dict[str, dict]:
        def paths():
            return [Path(p) for p in sorted(glob.glob(str(self.dir / "daily" / "*" / "sleep.json")) +
                                            glob.glob(str(self.dir / "ranges" / "sleep_summaries_*.json")))]

        def build():
            import build_report as br
            return {d["date"]: d for d in br.sleep(self.dir)}
        return self._memoize("sleep", paths, build)

    def intraday_hr(self, date: str) -> dict | None:
        p = self.dir / "daily" / date / "heart_rate.json"
        if not p.exists():
            return None
        h = _json(p)
        v = [[x[0], x[1]] for x in (h.get("heartRateValues") or []) if x[1] is not None]
        return {"rhr": h.get("restingHeartRate"), "values": v} if v else None

    # ------------------------------------------------------------ series
    def series(self, aid: str) -> tuple[dict, dict] | None:
        folder = self.folders().get(aid)
        fit = next(folder.glob("*.fit"), None) if folder else None
        if fit is None:
            return None
        npz = self.cache_dir / "series" / f"{aid}.npz"
        fm = fit.stat().st_mtime_ns
        with self._lock:
            hit = self._series.get(aid)
            if hit and hit[0] == fm:
                self._series.move_to_end(aid)
                return hit[1], hit[2]
        if npz.exists() and npz.stat().st_mtime_ns >= fm:
            arr, meta = fitcache.load_npz(npz)
        else:
            try:
                x = fitcache.read_fit(fit)
            except Exception:
                return None
            arr, meta = x["arr"], x["meta"]
            npz.parent.mkdir(parents=True, exist_ok=True)
            fitcache.save_npz(npz, arr, meta)
        with self._lock:
            self._series[aid] = (fm, arr, meta)
            while len(self._series) > 64:
                self._series.popitem(last=False)
        return arr, meta


# ---------------------------------------------------------------- series helpers
def _clean(a: np.ndarray, n: int) -> list:
    return [None if (x != x) else (round(float(x), n) if n else int(round(float(x)))) for x in a]


def smooth(v: np.ndarray, t: np.ndarray, window_s: float = 5.0) -> np.ndarray:
    """centred moving mean in time (NaN-aware) on irregular samples"""
    if len(v) < 3:
        return v
    ok = ~np.isnan(v)
    if not ok.any():
        return v
    tt = t.astype(float)
    c = np.concatenate([[0], np.cumsum(np.where(ok, v, 0.0))])
    k = np.concatenate([[0], np.cumsum(ok)])
    lo = np.searchsorted(tt, tt - window_s / 2, "left")
    hi = np.searchsorted(tt, tt + window_s / 2, "right")
    n = k[hi] - k[lo]
    with np.errstate(invalid="ignore", divide="ignore"):
        out = (c[hi] - c[lo]) / n
    out[n == 0] = np.nan
    return out


def resample(arr: dict, max_points: int = 3600) -> tuple[list[str], dict]:
    """Regular time grid (step = ceil(duration / max_points) s); columns interpolated over their valid
    samples; gaps longer than max(10 s, 3 steps) to the nearest valid sample are null. Speed is
    5 s-smoothed (or one step) before resampling."""
    t = arr["t"].astype(float)
    T = float(t[-1]) if len(t) else 0.0
    step = max(1, math.ceil(T / max_points))
    grid = np.arange(0, T + 1e-9, step)
    gap = max(10.0, 3.0 * step)
    out = {"t": [int(x) for x in grid]}
    cols = ["t"]
    spec = {"lat": 6, "lon": 6, "hr": 0, "speed": 3, "alt": 1, "dist": 1, "cad": 0, "power": 0}
    for k, nd in spec.items():
        v = arr.get(k)
        if v is None or len(v) != len(t):
            continue
        v = v.astype(float)
        if k in ("speed", "cad", "power"):
            v = smooth(v, t, max(5.0, float(step)))
        ok = ~np.isnan(v)
        if ok.sum() < 2:
            continue
        tv = t[ok]
        g = np.interp(grid, tv, v[ok])
        i = np.clip(np.searchsorted(tv, grid), 1, len(tv) - 1)
        near = np.minimum(np.abs(grid - tv[i - 1]), np.abs(tv[i] - grid))
        g[near > gap] = np.nan
        out[k] = _clean(g, nd)
        cols.append(k)
    return cols, out


def track_points(arr: dict, max_points: int = 4000) -> dict | None:
    ok = ~np.isnan(arr["lat"])
    if ok.sum() < 2:
        return None
    t = arr["t"].astype(float)
    sp = smooth(arr["speed"].astype(float), t, 5.0) if len(arr["speed"]) == len(t) else np.full(len(t), np.nan)
    idx = np.flatnonzero(ok)
    if len(idx) > max_points:
        idx = idx[np.linspace(0, len(idx) - 1, max_points).astype(int)]
    return {
        "t": [int(x) for x in t[idx]],
        "lat": _clean(arr["lat"][idx], 6), "lon": _clean(arr["lon"][idx], 6),
        "speed": _clean(sp[idx], 2),
        "hr": _clean(arr["hr"][idx].astype(float), 0),
        "alt": _clean(arr["alt"][idx].astype(float), 1),
    }

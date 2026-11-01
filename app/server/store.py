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
SPORT_LABEL = {"run": "Run", "ski": "Ski", "hike": "Hike", "bike": "Bike", "swim": "Swim", "other": "Other"}


def family(type_key: str | None) -> str:
    return FAMILY.get(type_key or "", "other")


def type_label(type_key: str | None) -> str | None:
    """readable Garmin activity type ('lap_swimming' -> 'Pool swim'), same labels as the similarity index"""
    import similarity as sim
    return sim.atype_label(type_key)


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


def _clean_sig(sig: str | None) -> bool:
    """a rep signature that names the structure ("6×1000", "1×1300 + 2×3000"), not "8 reps (mixed …)" """
    return bool(sig) and "×" in sig and "mixed" not in sig and " reps" not in sig


def workout_name(type_key: str | None, w: dict, together: dict | None) -> str | None:
    """The one workout label of an activity (docs/app.md#workout-label):
    1. its own rep signature when it names the structure ("15×400", "3×1800 + 2×900");
    2. else, done with others as intervals: the shared structure of that Together group ("1×1300 + 2×3000" for a
       multisport recording of the same session, or "3×1800 + 2×900" where this watch saw "8 reps (mixed …)");
    3. else its own signature ("8 reps (mixed 1:17–7:40)");
    4. else the workout family ("Easy run", "Hike", "Ski easy") or the Garmin type."""
    sig = w.get("signature") if w.get("rep_count") else None
    if _clean_sig(sig):
        return sig
    if together and together.get("kind") == "intervals" and _clean_sig(together.get("workout")):
        return together["workout"]
    if sig:
        return sig
    return w.get("family_label") or type_label(type_key)


def rep_pace(reps: list | None, w: dict, sport: str, track: bool = False) -> float | None:
    """average speed of the main reps (m/s) = total rep distance / total rep time — the one "avg rep pace" of the
    app (Home, Activities, Closest, activity page). groups.json reps are [time_s, dist_m, speed, hr]; the main part
    is the similarity index's (`main_part` of `rep_parts`). Reps without a distance (Bislett indoor laps): the
    index's median rep speed. On a track (`track`) each rep counts as its set distance (session_groups.track_set_m:
    a 416 m GPS rep is 400 m)."""
    if not reps or not w.get("rep_count"):
        return None
    try:
        import similarity as sim
        parts = sim.rep_parts(reps, sport == "ski")
        g = parts[w.get("main_part") or 0] if len(parts) == len(w.get("parts") or parts) else max(parts, key=len)
    except Exception:  # noqa: BLE001 — a label helper must not take the list down
        return w.get("rep_speed_ms")
    ts = [reps[j][0] for j in g if reps[j][0] and reps[j][1]]
    ds = [((sim.sg.track_set_m(reps[j][1]) or reps[j][1]) if track else reps[j][1]) for j in g if reps[j][0] and reps[j][1]]
    if not ts or sum(ts) <= 0:
        return w.get("rep_speed_ms")
    return round(sum(ds) / sum(ts), 3)


def rep_hr(reps: list | None, w: dict, sport: str) -> int | None:
    """mean HR over the same main reps as rep_pace (groups.json reps [time_s, dist_m, speed, hr]), time-weighted"""
    if not reps or not w.get("rep_count"):
        return None
    try:
        import similarity as sim
        parts = sim.rep_parts(reps, sport == "ski")
        g = parts[w.get("main_part") or 0] if len(parts) == len(w.get("parts") or parts) else max(parts, key=len)
    except Exception:  # noqa: BLE001
        g = range(len(reps))
    xs = [(reps[j][0], reps[j][3]) for j in g if len(reps[j]) > 3 and reps[j][0] and reps[j][3]]
    tt = sum(t for t, _ in xs)
    return int(round(sum(t * h for t, h in xs) / tt)) if tt else None


BISLETT_LAP_M = 546.5   # indoor Bislett: n whole laps = n x 546.5 m - 7.5 m (session_groups)


def easy_speeds(G: dict) -> list[tuple[str, float]]:
    """(date, m/s) of every trusted-distance easy run (groups.json type "easy"), sorted by date"""
    out = []
    for v in G.values():
        if v.get("sport") == "run" and v.get("type") == "easy" and v.get("dist_trusted") and v.get("moving_s") and v.get("dist_km"):
            sp = v["dist_km"] * 1000 / v["moving_s"]
            if 1.5 < sp < 6:
                out.append((v["date"], sp))
    return sorted(out)


def easy_speed_at(speeds: list[tuple[str, float]], date: str) -> float | None:
    """median easy-run speed of the 365 days before `date` (all-time median when there are fewer than 5)"""
    if not speeds:
        return None
    import datetime as _dt
    lo = (_dt.date.fromisoformat(date) - _dt.timedelta(days=365)).isoformat()
    win = [s for d, s in speeds if lo <= d <= date]
    return float(np.median(win if len(win) >= 5 else [s for _, s in speeds]))


def indoor_dist_estimate(ga: dict, easy_ms: float | None) -> tuple[float, str] | None:
    """Estimated distance (km, note) of an indoor Bislett session whose watch distance is made up: the rep laps
    (whole laps x 546.5 m - 7.5 m) + the moving time before the first and after the last rep at the person's
    easy pace (median easy run of the year before). Breaks between reps count as standing (0 m). None when a rep
    has no known lap count or there is no easy pace."""
    ib = ga.get("indoor_bislett") or {}
    rl = ib.get("rep_laps") or []
    if not rl or not easy_ms:
        return None
    rm = ib.get("rep_m") or [None] * len(rl)
    reps_m = []
    for (t0, d, n), m in zip(rl, rm):
        m = m or (n * BISLETT_LAP_M - 7.5 if n else None)
        if not m:
            return None
        reps_m.append(m)
    first = rl[0][0]
    last = rl[-1][0] + rl[-1][1]
    dur = ga.get("dur_s") or last
    pauses = ga.get("pauses") or []   # [t, dur_s, ...]: stopped time outside the rep block doesn't count
    p_out = sum(max(0.0, min(p[0] + p[1], first) - p[0]) + max(0.0, p[0] + p[1] - max(p[0], last)) for p in pauses)
    jog_s = max(0.0, first + max(0.0, dur - last) - p_out)
    km = (sum(reps_m) + jog_s * easy_ms) / 1000
    note = (f"estimate: {len(reps_m)} reps = {sum(reps_m) / 1000:.2f} km (whole indoor laps) + {round(jog_s / 60)} min "
            f"warm-up / cool-down at your easy pace ({int(1000 / easy_ms // 60)}:{int(1000 / easy_ms % 60):02d} /km); "
            "breaks counted as standing. The watch said "
            f"{ga.get('dist_km') or 0:.2f} km, which isn't trusted indoors")
    return round(km, 1), note


def _json(p: Path):
    return json.loads(p.read_text())


def volume_km(a: dict) -> float:
    """km that count in totals: the trusted distance, else 0 (an untrusted distance or its estimate doesn't)"""
    return 0.0 if a.get("dist_trusted") is False else (a.get("dist_km") or 0.0)


def moving_speed(a: dict) -> float | None:
    """moving speed (m/s) from a trusted distance only"""
    t = a.get("moving_s") or a.get("dur_s")
    return round(a["dist_km"] * 1000 / t, 3) if a.get("dist_trusted") is not False and a.get("dist_km") and t else None


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

    def index(self):
        """similarity.Index (scripts/similarity.py): the one source for place / route names and place membership.
        Cached in-process by similarity.load_index (reloaded on mtime, rebuilt when older than groups.json).
        None if it can't be built (no groups.json)."""
        import similarity as sim
        try:
            return sim.load_index(self.person)
        except FileNotFoundError:
            return None

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
        sx = self.dir / "similarity_index.json"
        tj = ROOT / "data" / "together.json"   # cross-person groups (scripts/together.py): the `together` link
        return self._memoize("activities", lambda: [idx, gp, tp, sx, tj, self.dir / "activities"], self._build_activities)

    def together_links(self) -> dict[str, dict]:
        """{activity id: {gid, label, kind, role, with: [person, ...]}} for this person's activities in a visible
        together group (data/together.json; role "other" = did a different workout alongside: no link)"""
        import together as tg
        try:
            L = tg.links()
        except Exception:  # noqa: BLE001 — a broken / half-written together.json must not take the activity list down
            return {}
        try:
            doc = tg.load(missing_ok=True) or {}
        except Exception:  # noqa: BLE001
            doc = {}
        gw = {g["gid"]: g.get("workout") for g in doc.get("groups", [])}
        return {aid: {**v, "workout": gw.get(v["gid"])} for (p, aid), v in L.items() if p == self.person}

    def _build_activities(self) -> list[dict]:
        g = self.groups()["activities"]
        tracks = self.tracks()
        folders = self.folders()
        ix = self.index()
        X = ix.acts if ix else {}
        R = ix.routes if ix else {}
        gpl = self.groups().get("places", {})
        pname = (lambda p: ix.place_name(p)) if ix else (lambda p: (gpl.get(p) or {}).get("name", p))
        tog = self.together_links()
        speeds = easy_speeds(g)
        out = []
        for a in _json(self.dir / "activities_index.json"):
            aid = str(a["activityId"])
            ga = g.get(aid) or {}
            tk = a["activityType"]["typeKey"]
            folder = folders.get(aid)
            x = X.get(int(aid)) or {}
            sp = ga.get("place")
            pl = x.get("place") or sp       # main place (index); start cluster without an index entry
            rt = ga.get("route")
            sw = ga.get("swim")
            # distance trust (groups.json dist_ok: GPS-backed or a pool swim's lengths); None = not analysed
            trusted = True if sw and sw.get("dist_m") else ga.get("dist_trusted")
            est = indoor_dist_estimate(ga, easy_speed_at(speeds, ga.get("date") or a["startTimeLocal"][:10])) \
                if trusted is False and ga.get("indoor_bislett") else None
            out.append({
                "id": aid,
                "start": a["startTimeLocal"],
                "start_gmt": a.get("startTimeGMT"),
                "date": a["startTimeLocal"][:10],
                "name": a.get("activityName"),
                "type": tk,
                "type_label": type_label(tk),
                "sport": family(tk),
                # pool swims: valid lengths x pool length (glitch lengths dropped, see session_groups.pool_swim)
                "dist_km": _r(sw["dist_m"] / 1000, 2) if sw and sw.get("dist_m") else _r((a.get("distance") or 0) / 1000, 2) or None,
                # dist_trusted false: dist_km is the watch's (inflated) number, kept for compatibility; lists show
                # dist_est_km ("≈ 9.2 km", note in dist_note) or "—", and km totals / pace leave it out (volume_km)
                "dist_trusted": trusted, "dist_est_km": est[0] if est else None,
                "dist_note": est[1] if est else ("no trusted distance: no GPS (the watch's distance is a guess)" if trusted is False else None),
                "dur_s": _r(a.get("duration"), 0),
                "moving_s": ga.get("moving_s") or _r(a.get("movingDuration"), 0),
                # whole-activity average HR (Garmin summary, incl. pauses); Compare's hr_avg is moving-only
                "hr": _r(a.get("averageHR"), 0), "maxhr": _r(a.get("maxHR"), 0),
                "elev": _r(a.get("elevationGain"), 0),
                "load": _r(a.get("activityTrainingLoad"), 0), "te": _r(a.get("aerobicTrainingEffect"), 1),
                "kcal": _r(a.get("calories"), 0),
                "location": a.get("locationName"),
                "lat": a.get("startLatitude"), "lon": a.get("startLongitude"),
                # place = main place id (where it mostly happens, similarity index), start_place = start cluster id
                "place": pl, "place_name": pname(pl), "place_src": x.get("place_src"),
                "start_place": sp, "start_place_name": pname(sp),
                "region": x.get("region"), "area": ga.get("area"),
                "route": rt, "route_name": (R.get(rt) or {}).get("name") or (self.groups()["routes"].get(rt) or {}).get("name") if rt else None,
                "stype": ga.get("type"), "type_detail": ga.get("type_detail"), "sig": ga.get("sig"),
                # the one workout name for every page (Home, Activities, Day, activity page, Together rows):
                # see workout_name(); workout_detail = the same-workout label with rest / route ("15×400 · rest ~1:00")
                "workout_label": workout_name(tk, x.get("workout") or {}, tog.get(aid)),
                "workout_detail": ((x.get("workout") or {}).get("exact_label") or (x.get("workout") or {}).get("signature")),
                # average rep pace of the main reps = total rep distance / total rep time (m/s), see rep_pace()
                "rep_pace_ms": rep_pace(ga.get("reps"), x.get("workout") or {}, family(tk),
                                        # track reps count as their set distance; indoor Bislett laps (1086 m) are already exact
                                        ga.get("type") == "intervals_track" and not ga.get("indoor_bislett")),
                "rep_hr": rep_hr(ga.get("reps"), x.get("workout") or {}, family(tk)),
                "swim": {k: sw.get(k) for k in ("pool_m", "lengths", "dist_m", "active_s", "rest_s", "pace_100m_s",
                                                "strokes_per_length", "bad_lengths", "n_rests", "longest_rest_s")} if sw else None,
                "fit": bool(folder and any(folder.glob("*.fit"))),
                "track": aid in tracks,
                "together": tog.get(aid),   # {gid, label, kind, role, with} when run with others (null otherwise)
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
        arr = meta = None
        fresh = npz.exists() and npz.stat().st_mtime_ns >= fm
        if fresh:
            arr, meta = fitcache.load_npz(npz)
            if meta.get("cache_version") != fitcache.CACHE_VERSION:   # older cache format: re-read the FIT
                arr = meta = None
        if arr is None:
            try:
                x = fitcache.read_fit(fit)
            except Exception:
                return None
            arr, meta = x["arr"], (fitcache.carry_reps(x["meta"], npz) if fresh else x["meta"])
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

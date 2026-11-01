"""Analysis of a FIT activity: reps (fast segments), breaks, km splits, Garmin laps.

Reps are detected from speed alone, independent of the lap button, so they can be
compared with Garmin's own laps and activity summary.

Detector v2 (`analyse`), see docs/run-analysis.md:
1. Distance field linearly interpolated to a 1 Hz grid (no forward fill; FR935 smart-recording
   gaps are marked and widen the boundary uncertainty).
2. Change points in the distance-derived speed: PELT on a piecewise-constant (L2) model.
3. Segments whose mean speed is above max(2.5, 0.72 * p90 of moving speed) are fast; fast runs
   are merged across gaps <= 8 s, runs < 20 s are dropped.
4. Each rep boundary is refined to 0.05 s by fitting a continuous two-slope model (constant speed
   before / after a kink) to the raw distance records around it.
5. Lag compensation per mode/watch (calibration.json `lag_compensation`, else defaults) is
   subtracted. The speed jump is the boundary; lap presses are a cross-check and a label.
Detector v1.1 (`analyse_v1`) is kept for comparison (scripts/evaluate_detector.py).
"""

from __future__ import annotations

import json
import math
import statistics
from pathlib import Path

import fitparse
import numpy as np

MIN_REP_S = 20      # shorter fast segments are noise
MERGE_GAP_S = 8     # slow-downs shorter than this stay inside a rep
SMOOTH_S = 5

VERSION = "2.0"
# Seconds the distance-derived speed change lags the true change (Phase 2 defaults), by mode;
# overridden by calibration.json lag_compensation["<mode>/<device>"] = {"start_s", "end_s", ...}.
DEFAULT_LAGS = {"gps": {"start_s": 1.25, "end_s": 1.7}, "track": {"start_s": 0.4, "end_s": 0.0}}
BASE_UNC_S = {"gps": 0.6, "track": 0.7}  # ~68 % |error| vs press with 1 s recording (evaluate_detector.py, LOSO)
PEN_K = 4.0          # PELT penalty = PEN_K * sigma^2 * ln(n)
MIN_SEG_S = 2        # shortest PELT segment
REFINE_W = 10.0      # half window (s) of the local kink fit (tuned on Bislet: 6..30 tried)
REFINE_SEARCH = 3.0  # kink searched within +-3 s of the change point
CONFIRM_S = 1.0      # press within this of the detected boundary = confirmed
PRESS_SEARCH_S = 10.0
CHUNK_GAP_S = 30.0   # recording pauses longer than this split the change-point search


def _fill(values):
    """Forward/backward fill None so every second has a value."""
    out, last = [], None
    for v in values:
        last = v if v is not None else last
        out.append(last)
    first = next((v for v in out if v is not None), 0)
    return [first if v is None else v for v in out]


def _press_times(raw_laps, t0):
    """Sub-second lap start times. Lap start_time is whole seconds, total_elapsed_time has ms:
    where cumulative elapsed times are consistent with floor(t) = start_time use them, else +0.5 s
    (same method as gps_lap_analysis.press_times)."""
    cum, lo, hi, out = 0.0, -1.0, 2.0, []
    for v in raw_laps:
        st = (v["start_time"] - t0).total_seconds()
        lo, hi = max(lo, st - cum), min(hi, st + 1 - cum)
        out.append((cum, st))
        cum += v.get("total_elapsed_time") or 0
    if out and lo <= hi:
        delta = (lo + hi) / 2
        return [c + delta for c, _ in out]
    return [st + 0.5 for _, st in out]


def _unjump_clock(recs, laps, sessions):
    """take watch clock jumps (gps_quality.clock_jumps, the same rule as session_groups.read_series) out of the
    record and lap timestamps, in place"""
    import calendar
    import datetime as _dt
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    import gps_quality as gq
    sec = lambda d: calendar.timegm(d.utctimetuple()) + d.microsecond / 1e6   # naive UTC datetimes, no local DST
    ts = [sec(r["timestamp"]) for r in recs if "timestamp" in r]
    one = sessions[0] if len(sessions) == 1 else {}
    st = one.get("start_time")
    jumps = gq.clock_jumps(ts, one.get("total_elapsed_time"), sec(st) if st else None)
    if not jumps:
        return
    for m in [*recs, *laps]:
        for k in ("timestamp", "start_time"):
            if k in m:
                m[k] -= _dt.timedelta(seconds=sec(m[k]) - gq.unjump(sec(m[k]), jumps))


def _read(fit_path: Path):
    ff = fitparse.FitFile(str(fit_path))
    msgs = lambda name: [{f.name: f.value for f in m if f.value is not None} for m in ff.get_messages(name)]
    recs = msgs("record")
    raw = msgs("lap")
    sess = (msgs("session") or [{}])[0]
    _unjump_clock(recs, raw, msgs("session"))
    t0 = recs[0]["timestamp"]
    t_starts = _press_times(raw, t0)
    laps = []
    for i, v in enumerate(raw):
        laps.append({
            "start": round((v["start_time"] - t0).total_seconds()),
            "t_start": round(t_starts[i], 2),  # sub-second lap start (= press time for laps > 1)
            "time": round(v.get("total_elapsed_time") or 0, 1),
            "timer": round(v.get("total_timer_time") or 0, 1),
            "dist": round(v.get("total_distance") or 0, 1),
            "hr": v.get("avg_heart_rate"),
            "maxhr": v.get("max_heart_rate"),
            "speed": v.get("enhanced_avg_speed", v.get("avg_speed")),
            "intensity": str(v.get("intensity") or ""),
            "trigger": str(v.get("lap_trigger") or ""),
        })
    dev = msgs("device_info")
    device = next((str(d.get("garmin_product") or d.get("product")) for d in dev if d.get("garmin_product") or d.get("product")), "?")
    mode = "track" if str(sess.get("sub_sport")) == "track" else "gps"
    return recs, t0, laps, mode, device


def load(fit_path: Path):
    recs, t0, laps, _, _ = _read(fit_path)
    return recs, t0, laps


def analyse_v1(fit_path: Path) -> dict:
    """Detector v1.1 (threshold on 5 s smoothed speed, whole seconds). Kept for comparison."""
    recs, t0, laps = load(fit_path)
    # resample to one row per second
    end = int((recs[-1]["timestamp"] - t0).total_seconds())
    hr, sp, dist = [None] * (end + 1), [None] * (end + 1), [None] * (end + 1)
    for x in recs:
        i = int((x["timestamp"] - t0).total_seconds())
        hr[i] = x.get("heart_rate")
        sp[i] = x.get("enhanced_speed", x.get("speed"))
        dist[i] = x.get("distance")
    hr, dist = _fill(hr), _fill(dist)
    sp = [0.0 if v is None else v for v in sp]
    # The watch's speed field lags ~3-4 s behind; speed from the distance field doesn't.
    if dist[0] is not None:
        sp = [0.0] + [max(0.0, (dist[i] or 0) - (dist[i - 1] or 0)) for i in range(1, len(dist))]
    n = len(sp)
    smooth = [statistics.fmean(sp[max(0, i - SMOOTH_S // 2): i + SMOOTH_S // 2 + 1]) for i in range(n)]

    moving = sorted(v for v in smooth if v > 1.0)
    p90 = moving[int(len(moving) * 0.9)] if moving else 0
    threshold = max(2.5, 0.72 * p90)

    # fast segments
    segs, start = [], None
    for i, v in enumerate(smooth + [0]):
        if v >= threshold and start is None:
            start = i
        elif v < threshold and start is not None:
            segs.append([start, i - 1])
            start = None
    merged = []
    for s in segs:
        if merged and s[0] - merged[-1][1] <= MERGE_GAP_S:
            merged[-1][1] = s[1]
        else:
            merged.append(s)
    reps = [s for s in merged if s[1] - s[0] + 1 >= MIN_REP_S]

    def stats(a, b):
        seg_hr = [h for h in hr[a: b + 1] if h]
        d = (dist[b] or 0) - (dist[max(0, a - 1)] or 0)
        secs = b - a + 1
        return {
            "start": a, "end": b, "time": secs, "dist": round(d, 1),
            "speed": round(d / secs, 3) if secs else None,
            "hr": round(statistics.fmean(seg_hr)) if seg_hr else None,
            "maxhr": max(seg_hr) if seg_hr else None,
            "hr_start": hr[a], "hr_end": hr[b],
        }

    rep_rows = [stats(a, b) for a, b in reps]
    breaks = []
    for prev, nxt in zip(rep_rows, rep_rows[1:]):
        a, b = prev["end"] + 1, nxt["start"] - 1
        if b < a:
            continue
        row = stats(a, b)
        peak = max(h for h in hr[max(0, prev["end"] - 5): prev["end"] + 6] if h)
        row["hr_peak"] = peak
        row["hr_60"] = hr[min(a + 60, b)] if b - a >= 60 else None
        row["hr_drop"] = peak - (hr[b] or peak)
        row["standing"] = sum(1 for v in smooth[a: b + 1] if v < 0.5)
        breaks.append(row)
    for i, rp in enumerate(rep_rows):
        rp["n"] = i + 1
        rp["break_after"] = breaks[i] if i < len(breaks) else None

    # match Garmin active laps to detected reps by overlap
    for lap in laps:
        a, b = lap["start"], lap["start"] + int(lap["time"]) - 1
        best, ov = None, 0
        for rp in rep_rows:
            o = min(b, rp["end"]) - max(a, rp["start"]) + 1
            if o > ov:
                best, ov = rp["n"], o
        lap["rep"] = best if lap["intensity"] == "active" else None

    # whole-km splits from distance
    km, splits, last_t = 1000.0, [], 0
    for i, d in enumerate(dist):
        if d is not None and d >= km:
            seg_hr = [h for h in hr[last_t: i + 1] if h]
            splits.append({"km": int(km / 1000), "time": i - last_t, "hr": round(statistics.fmean(seg_hr)) if seg_hr else None})
            last_t, km = i, km + 1000

    total_rep_time = sum(r["time"] for r in rep_rows)
    total_break_time = sum(b["time"] for b in breaks)
    return {
        "version": "1.1",
        "threshold": round(threshold, 2),
        "reps": rep_rows,
        "breaks": breaks,
        "laps": laps,
        "km_splits": splits,
        "totals": {
            "dist": round(dist[-1] or 0, 1),
            "elapsed": end,
            "moving": sum(1 for v in smooth if v >= 1.0),
            "reps": len(rep_rows),
            "rep_time": total_rep_time,
            "rep_dist": round(sum(r["dist"] for r in rep_rows), 1),
            "break_time": total_break_time,
            "hr": round(statistics.fmean([h for h in hr if h])),
            "maxhr": max(h for h in hr if h),
        },
    }


# ---------------------------------------------------------------- detector v2

_CAL_CACHE: dict = {}


def _calibration_for(fit_path: Path):
    """data/<person>/calibration.json for a FIT file under data/<person>/activities/<folder>/ (cached by mtime)."""
    p = Path(fit_path).resolve().parents[2] / "calibration.json"
    if not p.exists():
        return None
    key = (str(p), p.stat().st_mtime)
    if key not in _CAL_CACHE:
        _CAL_CACHE.clear()
        _CAL_CACHE[key] = json.loads(p.read_text())
    return _CAL_CACHE[key]


def _distance_uncertainty(fit_path: Path, calibration: dict | None) -> dict:
    """Per-activity distance uncertainty (1 sigma, %) from GPS quality indicators (gps_quality.py),
    model from calibration.json `uncertainty_model` (route_accuracy.py). No correction factor: no stable
    bias was found (see docs/distance-calibration.md)."""
    try:
        import gps_quality
        q = gps_quality.quality(fit_path)
        model = (calibration or _calibration_for(fit_path) or {}).get("uncertainty_model")
        q["env"] = gps_quality.env_guess(q, model)
        return {"distance_uncertainty_pct": gps_quality.uncertainty_pct(q, model), "gps_env": q["env"],
                "gps_jitter_m": q.get("jitter_m")}
    except Exception as e:  # never break the analysis for the uncertainty estimate
        return {"distance_uncertainty_pct": None, "gps_env": None, "uncertainty_error": str(e)[:80]}


def lag_for(mode: str, device: str, calibration: dict | None) -> dict:
    """{"start_s", "end_s", "source"} for this mode/watch: calibration lag_compensation, else defaults."""
    # detector_lags: offsets of *this* detector's change points vs presses (evaluate_detector.py);
    # lag_compensation: step-model lag of the distance-field signal (gps_lap_analysis.py).
    for tname in ("detector_lags", "lag_compensation"):
        table = (calibration or {}).get(tname) or {}
        for key in (f"{mode}/{device}", f"{mode}/all", mode):
            e = table.get(key)
            if e and e.get("start_s") is not None and e.get("end_s") is not None:
                return {**e, "source": f"calibration:{tname}:{key}"}
    return {**DEFAULT_LAGS[mode], "source": f"default:{mode}"}


def pelt(y, pen: float, min_size: int = MIN_SEG_S):
    """Change points of a piecewise-constant mean (L2 cost), PELT with pruning. Returns segment ends."""
    y = np.asarray(y, float)
    n = len(y)
    s1 = np.concatenate([[0.0], np.cumsum(y)])
    s2 = np.concatenate([[0.0], np.cumsum(y * y)])
    F = np.full(n + 1, np.inf)
    F[0] = -pen
    last = np.zeros(n + 1, int)
    R = np.array([0])
    for t in range(min_size, n + 1):
        ok = R <= t - min_size
        cand = R[ok]
        L = t - cand
        c = s2[t] - s2[cand] - (s1[t] - s1[cand]) ** 2 / L
        tot = F[cand] + c + pen
        k = int(np.argmin(tot))
        F[t], last[t] = tot[k], cand[k]
        R = np.concatenate([cand[F[cand] + c <= F[t]], R[~ok], [t]])
    ends, t = [], n
    while t > 0:
        ends.append(t)
        t = last[t]
    return ends[::-1]


def _kink(tr, dr, b, lo, hi):
    """Refine a boundary near b: fit D(t) = c + v1*min(t-tau, 0) + v2*max(t-tau, 0) to the raw
    distance records in [b-W, b+W] (clipped to [lo, hi]); tau on a 0.05 s grid. Returns tau, v1, v2."""
    m = (tr >= max(b - REFINE_W, lo)) & (tr <= min(b + REFINE_W, hi))
    t, d = tr[m], dr[m]
    if len(t) < 4:
        return float(b), None, None
    taus = np.arange(max(b - REFINE_SEARCH, t[1]), min(b + REFINE_SEARCH, t[-2]) + 1e-9, 0.05)
    if not len(taus):
        return float(b), None, None
    best = (np.inf, float(b), None, None)
    for tau in taus:
        X = np.column_stack([np.ones_like(t), np.minimum(t - tau, 0), np.maximum(t - tau, 0)])
        coef = np.linalg.lstsq(X, d, rcond=None)[0]
        sse = float(((X @ coef - d) ** 2).sum())
        if sse < best[0]:
            best = (sse, float(tau), float(coef[1]), float(coef[2]))
    return best[1], best[2], best[3]


def _local_gap(tr, tau):
    """Longest record spacing (s) within +-2 s of tau."""
    i, j = np.searchsorted(tr, tau - 2), np.searchsorted(tr, tau + 2)
    i, j = max(1, i), min(len(tr) - 1, j)
    return float(np.max(np.diff(tr[i - 1: j + 1]))) if j >= i else 1.0


def _check(dt):
    if dt is None:
        return None
    if abs(dt) <= CONFIRM_S:
        return "confirmed"
    return f"pressed {'late' if dt > 0 else 'early'} by {abs(dt):.1f} s"


def detect(fit_path: Path, calibration: dict | None = None, data=None):
    """Rep boundaries (v2). Returns (reps, context); reps carry raw and compensated times.
    `data`: optional pre-read _read(fit_path) result."""
    recs, t0, laps, mode, device = data or _read(fit_path)
    if calibration is None:
        calibration = _calibration_for(fit_path)
    lag = lag_for(mode, device, calibration)
    end = int((recs[-1]["timestamp"] - t0).total_seconds())
    pts = sorted(((x["timestamp"] - t0).total_seconds(), x["distance"]) for x in recs if x.get("distance") is not None)
    hr = [None] * (end + 1)
    for x in recs:
        hr[int((x["timestamp"] - t0).total_seconds())] = x.get("heart_rate")
    hr = _fill(hr)
    ctx = {"laps": laps, "mode": mode, "device": device, "lag": lag, "hr": hr, "end": end}
    if len(pts) < 10:
        ctx.update(tr=np.array([0.0, 1.0]), dr=np.zeros(2), grid_d=np.zeros(end + 1), v=np.zeros(end + 1), smooth=np.zeros(end + 1), threshold=2.5)
        return [], ctx
    tr = np.array([p[0] for p in pts])
    dr = np.maximum.accumulate(np.array([p[1] for p in pts], float))
    grid_d = np.interp(np.arange(end + 1), tr, dr)
    v = np.diff(grid_d)                          # v[i]: speed over [i, i+1]
    v_full = np.concatenate([[0.0], v])          # per second as in v1 (speed into second i)
    smooth = np.convolve(v_full, np.ones(SMOOTH_S) / SMOOTH_S, mode="same")
    moving = smooth[smooth > 1.0]
    p90 = float(np.quantile(moving, 0.9, method="lower")) if len(moving) else 0
    threshold = max(2.5, 0.72 * p90)

    dv = np.diff(v)
    sigma = max(0.2, 1.4826 * float(np.median(np.abs(dv - np.median(dv)))) / math.sqrt(2)) if len(dv) else 0.2
    # PELT per chunk of continuous recording (no change point search across pauses > CHUNK_GAP_S)
    pen = PEN_K * sigma ** 2 * math.log(max(len(v), 2))
    gaps = np.nonzero(np.diff(tr) > CHUNK_GAP_S)[0]
    starts = [0] + [int(math.ceil(tr[i + 1])) for i in gaps]
    stops = [int(tr[i]) for i in gaps] + [len(v)]
    segs = []
    for ca, cb in zip(starts, stops):
        if cb - ca < 2 * MIN_SEG_S:
            continue
        a = ca
        for b in pelt(v[ca:cb], pen):
            segs.append((a, ca + b, float(v[a: ca + b].mean())))
            a = ca + b
    fast = []
    for a, b, m in segs:
        if m >= threshold:
            if fast and a - fast[-1][1] <= MERGE_GAP_S:
                fast[-1][1] = b
            else:
                fast.append([a, b])
    fast = [s for s in fast if s[1] - s[0] >= MIN_REP_S]
    ctx.update(tr=tr, dr=dr, grid_d=grid_d, v=v, smooth=smooth, threshold=threshold, sigma=sigma, segments=len(segs), fast=fast)

    base = BASE_UNC_S[mode]
    reps = []
    for k, (a, b) in enumerate(fast):
        lo = (fast[k - 1][1] + a) / 2 if k else 0.0
        hi = (b + fast[k + 1][0]) / 2 if k + 1 < len(fast) else float(end)
        ts, _, vs = _kink(tr, dr, a, lo, (a + b) / 2)
        te, ve, _ = _kink(tr, dr, b, (a + b) / 2, hi)
        gs, ge = _local_gap(tr, ts), _local_gap(tr, te)
        reps.append({
            "start_detected": round(ts, 2), "end_detected": round(te, 2),
            "start": round(max(0.0, ts - lag["start_s"]), 2), "end": round(min(float(end), te - lag["end_s"]), 2),
            "start_uncertainty_s": round(base + max(0.0, gs - 1) / 2, 2),
            "end_uncertainty_s": round(base + max(0.0, ge - 1) / 2, 2),
            "record_gap_start_s": round(gs, 1), "record_gap_end_s": round(ge, 1),
        })
    return reps, ctx


def analyse(fit_path: Path, calibration: dict | None = None) -> dict:
    """Detector v2. `calibration`: calibration.json contents (default: read next to the data folder)."""
    reps, ctx = detect(fit_path, calibration)
    laps, hr, tr, dr, smooth, end = ctx["laps"], ctx["hr"], ctx["tr"], ctx["dr"], ctx["smooth"], ctx["end"]
    D = lambda t: float(np.interp(t, tr, dr))
    H = lambda t: hr[min(end, max(0, int(round(t))))]

    def hr_stats(a, b):
        seg = [h for h in hr[max(0, math.ceil(a)): int(b) + 1] if h]
        return {"hr": round(statistics.fmean(seg)) if seg else None, "maxhr": max(seg) if seg else None,
                "hr_start": H(a), "hr_end": H(b)}

    # lap intervals (sub-second) and presses (manual lap ends)
    spans = []
    for i, lap in enumerate(laps):
        a = lap["t_start"]
        b = laps[i + 1]["t_start"] if i + 1 < len(laps) else a + lap["time"]
        spans.append((a, b))
    presses = [spans[i][1] for i, lap in enumerate(laps[:-1]) if lap["trigger"] == "manual"]
    nearest = lambda t: min(presses, key=lambda p: abs(p - t), default=None)

    rep_rows = []
    for n, r in enumerate(reps, 1):
        a, b = r["start"], r["end"]
        secs = b - a
        d_det = D(r["end_detected"]) - D(r["start_detected"])
        best, ov = None, 0.0
        for i, (la, lb) in enumerate(spans):
            o = min(b, lb) - max(a, la)
            if o > ov:
                best, ov = i, o
        lap = laps[best] if best is not None else None
        ps, pe = nearest(a), nearest(b)
        ps = ps if ps is not None and abs(ps - a) <= PRESS_SEARCH_S else None
        pe = pe if pe is not None and abs(pe - b) <= PRESS_SEARCH_S else None
        dts = None if ps is None else round(ps - a, 2)
        dte = None if pe is None else round(pe - b, 2)
        use_lap = (ctx["mode"] == "track" and lap is not None and lap["intensity"] == "active"
                   and ov >= 0.5 * max(secs, spans[best][1] - spans[best][0]))
        dist = lap["dist"] if use_lap else round(d_det, 1)
        checks = [f"start {c}" for c in [_check(dts)] if c and c != "confirmed"] + \
                 [f"end {c}" for c in [_check(dte)] if c and c != "confirmed"]
        rep_rows.append({
            "start": a, "end": b, "time": round(secs, 2), "dist": round(dist, 1),
            "speed": round(dist / secs, 3) if secs > 0 else None,
            **hr_stats(a, b),
            "n": n,
            "boundary_source": "detected+compensated",
            "dist_source": "lap" if use_lap else "detected",
            "dist_detected": round(d_det, 1),
            "start_detected": r["start_detected"], "end_detected": r["end_detected"],
            "start_uncertainty_s": r["start_uncertainty_s"], "end_uncertainty_s": r["end_uncertainty_s"],
            "record_gap_start_s": r["record_gap_start_s"], "record_gap_end_s": r["record_gap_end_s"],
            "press_start_dt": dts, "press_end_dt": dte,
            "start_check": _check(dts), "end_check": _check(dte),
            "flag": "; ".join(checks) or None,
            "lap": best + 1 if best is not None else None,
        })

    breaks = []
    for prev, nxt in zip(rep_rows, rep_rows[1:]):
        a, b = prev["end"], nxt["start"]
        if b <= a:
            continue
        row = {"start": a, "end": b, "time": round(b - a, 2),
               "dist": round(D(nxt["start_detected"]) - D(prev["end_detected"]), 1), **hr_stats(a, b)}
        row["speed"] = round(row["dist"] / row["time"], 3)
        e = int(round(a))
        row["hr_peak"] = max((h for h in hr[max(0, e - 5): e + 6] if h), default=None)
        ia, ib = max(0, math.ceil(a)), int(b)
        row["hr_60"] = hr[min(ia + 60, ib)] if ib - ia >= 60 else None
        row["hr_drop"] = (row["hr_peak"] - (hr[ib] or row["hr_peak"])) if row["hr_peak"] else None
        row["standing"] = int(np.sum(smooth[ia: ib + 1] < 0.5))
        breaks.append(row)
    for i, rp in enumerate(rep_rows):
        rp["break_after"] = breaks[i] if i < len(breaks) else None

    for i, lap in enumerate(laps):
        a, b = spans[i]
        best, ov = None, 0.0
        for rp in rep_rows:
            o = min(b, rp["end"]) - max(a, rp["start"])
            if o > ov:
                best, ov = rp["n"], o
        lap["rep"] = best if lap["intensity"] == "active" else None

    grid_d = ctx["grid_d"]
    splits, last_t = [], 0
    for k in range(1, int(grid_d[-1] // 1000) + 1):
        i = int(np.searchsorted(grid_d, 1000.0 * k))  # first second with distance >= k km
        seg_hr = [h for h in hr[last_t: i + 1] if h]
        splits.append({"km": k, "time": i - last_t, "hr": round(statistics.fmean(seg_hr)) if seg_hr else None})
        last_t = i

    hrs = [h for h in hr if h]
    return {
        "version": VERSION,
        "threshold": round(ctx["threshold"], 2),
        "mode": ctx["mode"], "device": ctx["device"],
        "lag_compensation": {k: ctx["lag"][k] for k in ("start_s", "end_s", "source")},
        "reps": rep_rows,
        "breaks": breaks,
        "laps": laps,
        "km_splits": splits,
        "totals": {
            "dist": round(float(dr[-1]), 1),
            "elapsed": end,
            "moving": int(np.sum(smooth >= 1.0)),
            "reps": len(rep_rows),
            "rep_time": round(sum(r["time"] for r in rep_rows), 1),
            "rep_dist": round(sum(r["dist"] for r in rep_rows), 1),
            "break_time": round(sum(b["time"] for b in breaks), 1),
            "hr": round(statistics.fmean(hrs)) if hrs else None,
            "maxhr": max(hrs) if hrs else None,
            **_distance_uncertainty(fit_path, calibration),
        },
    }

"""Hidden GPS lag in normal (GPS) mode, measured with manual lap presses at Bislet.

Truth: the lap button (assumed pressed at the finish line) and the Bislet track model from
calibrate_distance.py. In that model the finish line (end of the home straight) is at arc
s = 284.39 m and Adrian runs towards increasing s. For every manual lap press:

  pos_offset  raw GPS position at the press, along the track, minus the line (m);
              negative = GPS still behind the line = position lags
  pos_dt      time the GPS position crosses the line minus press time (s); positive = lag
  book_m/s    distance field at the press minus Garmin's cumulative lap distance: how much
              the watch's lap boundary is behind the press (m, and s at the current speed)
  onset       rep starts: time after the press until GPS position / distance field have moved
              5 m (onset5_*) and until their speed reaches half the rep speed (half_*)
  stop        rep ends: time after the press until GPS / distance-field speed halves
Per session: xcorr = lag of distance-field speed behind GPS along-track speed (s).
Step model (rep starts/ends, any place on the track): lag of each signal's speed step vs the press
(step_lag: least-squares ramp-step fit). Signals: pos (GPS along-track speed), dist (distance-field
speed), speed (speed field), cad, power. -> lag_table (per mode/device/signal/kind, session-bootstrap
CI, pace slopes), lag_vs_pace (points), lag_compensation (dist signal; for the rep detector).
Per lap: Garmin lap distance vs track geometry, corrected for the GPS lane offset.

Press kinds (from GPS speed 8 s before/after): through (running on), end (rep end, slows
down), start (rep start from standing/walking), other. Track-mode sessions are included as a
reference; their positions are snapped to the track, so they say nothing about raw GPS.

Press time: lap start_time is whole seconds; total_elapsed_time has ms. Where the cumulative
elapsed times are consistent with floor(press) = start_time we use them (sub-second),
otherwise start_time + 0.5 s. If Garmin rounds instead of truncating, all times shift 0.5 s.

Usage: uv run python scripts/gps_lap_analysis.py adrian  -> data/<person>/gps_lap_analysis.json
Needs data/<person>/calibration.json (from calibrate_distance.py).
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import statistics
from pathlib import Path

import random

import fitparse

from calibrate_distance import HALF_STRAIGHT, LAP, R1, SEMI, Track, hav

ROOT = Path(__file__).resolve().parent.parent
S_LINE = 4 * HALF_STRAIGHT + math.pi * R1  # 284.39 m
LANE_LO, LANE_HI = -4.0, 12.0  # lane offsets accepted as "on the track"
MAX_GAP = 6  # s; no interpolation across longer gaps
STEP = 0.1  # s; time resolution of searches


def load(fit: Path):
    ff = fitparse.FitFile(str(fit))
    recs = [{f.name: f.value for f in m if f.value is not None} for m in ff.get_messages("record")]
    t0 = recs[0]["timestamp"]
    rows = [{"t": (x["timestamp"] - t0).total_seconds(), "dist": x.get("distance"),
             "pos": (x["position_lat"] * SEMI, x["position_long"] * SEMI) if "position_lat" in x and "position_long" in x else None,
             "speed": x.get("enhanced_speed", x.get("speed")),
             "cad": x["cadence"] + (x.get("fractional_cadence") or 0) if "cadence" in x else None,
             "power": x.get("power")}
            for x in recs]
    laps = [{f.name: f.value for f in m if f.value is not None} for m in ff.get_messages("lap")]
    sess = [{f.name: f.value for f in m if f.value is not None} for m in ff.get_messages("session")][0]
    dev = [{f.name: f.value for f in m if f.value is not None} for m in ff.get_messages("device_info")]
    prod = next((str(d.get("garmin_product") or d.get("product")) for d in dev if d.get("garmin_product") or d.get("product")), "?")
    return rows, laps, sess, prod, t0


def press_times(laps, t0):
    """[(press time, lap that ends, cumulative Garmin distance)], and the timing method used."""
    cum, cum_d, lo, hi, raw = 0.0, 0.0, -1.0, 2.0, []
    for a, b in zip(laps, laps[1:]):
        cum += a.get("total_elapsed_time") or 0
        cum_d += a.get("total_distance") or 0
        st = (b["start_time"] - t0).total_seconds()
        lo, hi = max(lo, st - cum), min(hi, st + 1 - cum)
        raw.append((cum, st, a, cum_d))
    if lo <= hi:
        delta = (lo + hi) / 2
        return [(c + delta, a, d) for c, _, a, d in raw], "elapsed"
    return [(st + 0.5, a, d) for _, st, a, d in raw], "start_time+0.5"


class Series:
    """Linear interpolation over irregular time stamps (None outside the data or across gaps)."""

    def __init__(self, ts, vs):
        self.ts, self.vs = ts, vs

    def __call__(self, t):
        i = bisect.bisect_left(self.ts, t)
        if i < len(self.ts) and self.ts[i] == t:
            return self.vs[i]
        if i == 0 or i == len(self.ts) or self.ts[i] - self.ts[i - 1] > MAX_GAP:
            return None
        t1, t2 = self.ts[i - 1], self.ts[i]
        return self.vs[i - 1] + (self.vs[i] - self.vs[i - 1]) * (t - t1) / (t2 - t1)


def track_series(rows, track):
    """Unwrapped arc position S(t) along lane 1 (on-track points only), lane offsets, distance field D(t)."""
    ts, S, offs, prev = [], [], [], None
    for r in rows:
        if not r["pos"]:
            continue
        s, o = track.project(r["pos"])
        if not LANE_LO <= o <= LANE_HI:
            continue
        u = s if prev is None else prev + (s - prev + LAP / 2) % LAP - LAP / 2
        ts.append(r["t"]); S.append(u); offs.append(o); prev = u
    d = [(r["t"], r["dist"]) for r in rows if r["dist"] is not None]
    return Series(ts, S), offs, Series([a for a, _ in d], [b for _, b in d])


def field_series(rows, key):
    """Series of a record field (speed, cad, power), or None if the file doesn't have it."""
    d = [(r["t"], r[key]) for r in rows if r.get(key) is not None]
    return Series([a for a, _ in d], [b for _, b in d]) if len(d) >= 100 else None


def speed(f, t, h=1.0):
    a, b = f(t - h), f(t + h)
    return None if a is None or b is None else (b - a) / (2 * h)


def first_time(f, t_from, t_to, cond):
    """First t in [t_from, t_to] where f(t) satisfies cond."""
    for k in range(int(round((t_to - t_from) / STEP)) + 1):
        t = t_from + k * STEP
        v = f(t)
        if v is not None and cond(v):
            return t
    return None


def crossing(S, tp, target, reach=15):
    """Time nearest to tp at which S(t) passes target."""
    best = None
    for sign in (1, -1):
        for k in range(int(reach / STEP)):
            t = tp + sign * k * STEP
            a, b = S(t), S(t + sign * STEP)
            if a is None or b is None:
                break
            if (a - target) * (b - target) <= 0 and a != b:
                tc = t + sign * STEP * (target - a) / (b - a)
                best = tc if best is None or abs(tc - tp) < abs(best - tp) else best
                break
    return best


def xcorr_lag(S, D, max_lag=6):
    """Lag (s) of distance-field speed behind GPS along-track speed: 1 Hz cross-correlation, parabolic peak."""
    vs, vd = {}, {}
    for t in range(math.ceil(S.ts[0]), int(S.ts[-1])):
        a, b = speed(S, t, 0.5), speed(D, t, 0.5)
        if a is not None and b is not None:
            vs[t], vd[t] = a, b
    scores = {}
    for lag in range(-max_lag, max_lag + 1):
        pairs = [(vs[k], vd[k + lag]) for k in vs if k + lag in vd]
        if len(pairs) < 100:
            return None
        scores[lag] = statistics.correlation(*zip(*pairs))
    k = max(scores, key=scores.get)
    if abs(k) == max_lag:
        return float(k)
    a, b, c = scores[k - 1], scores[k], scores[k + 1]
    return round(k + 0.5 * (a - c) / (a - 2 * b + c), 2)


def rel(t, tp):
    return None if t is None else round(t - tp, 2)


def analyse_press(S, D, tp, cum_garmin):
    s_p, s_b, s_a = S(tp), S(tp - 8), S(tp + 8)
    if None in (s_p, s_b, s_a):
        return None
    vb, va = (s_p - s_b) / 8, (s_a - s_p) / 8
    kind = ("through" if vb > 2.5 and va > 2.5 else "start" if vb < 1.0 and va > 1.5 else
            "end" if vb > 2.5 and va < 2.0 else "other")
    off = (s_p - S_LINE + LAP / 2) % LAP - LAP / 2  # vs the nearest pass of the line
    row = {"kind": kind, "v_before": round(vb, 2), "v_after": round(va, 2), "pos_offset": round(off, 2),
           "at_line": abs(off) <= 30}  # else pressed elsewhere (e.g. the 200 m start)
    d_p = D(tp)
    if d_p is not None:
        row["book_m"] = round(d_p - cum_garmin, 2)
        row["book_s"] = round((d_p - cum_garmin) / vb, 2) if vb > 2.5 else None
    if not row["at_line"]:
        return row
    if kind in ("through", "end"):
        s12, s4 = S(tp - 12), S(tp - 4)  # pace from a window that doesn't touch the press
        row["v"] = round((s4 - s12) / 8, 2) if s12 is not None and s4 is not None else round(vb, 2)
        row["pos_dt"] = rel(crossing(S, tp, s_p - off), tp)
    if kind == "end":
        for name, f in (("stop_pos", S), ("stop_dist", D)):
            row[name] = rel(first_time(lambda t: speed(f, t), tp - 5, tp + 12, lambda v: v <= vb / 2), tp)
    if kind == "start":
        s10, s20 = S(tp + 10), S(tp + 20)
        v_rep = va if s10 is None or s20 is None else (s20 - s10) / 10
        row["v"] = round(v_rep, 2)
        for name, f in (("pos", S), ("dist", D)):
            base = f(tp - 1)
            if base is None:
                continue
            row[f"onset5_{name}"] = rel(first_time(f, tp - 5, tp + 12, lambda v: v - base >= 5), tp)
            row[f"half_{name}"] = rel(first_time(lambda t: speed(f, t), tp - 5, tp + 12, lambda v: v >= v_rep / 2), tp)
    return row


RAMP = 1.0  # s; fixed ramp width of the step model
TAU_LO, TAU_HI = -6.0, 8.0  # s; lag search range
MIN_R2 = 0.6


def step_lag(sig, tp, kind, win=(-10.0, 15.0), min_jump=None):
    """Lag (s) of a speed-like signal's step vs the press tp, by least squares on a step model.

    sig(t) is sampled every STEP s over tp + win. Model: a + b * h(t - tp - tau) with h a 0..1 ramp
    of width RAMP centred on tau; levels a, b are fitted (so absolute scale doesn't matter, e.g.
    cadence) for each tau in [TAU_LO, TAU_HI], best tau refined with a parabola through the SSE.
    Rejected (None) if the signal has < 80 % coverage, the step goes the wrong way (up for 'start',
    down for 'end'), |b| < min_jump, R^2 < MIN_R2, or the optimum is at the edge of the range.
    Returns {"lag", "r2", "rms", "lo", "hi"} (lo/hi = fitted level before/after)."""
    n = int(round((win[1] - win[0]) / STEP)) + 1
    pts = [(win[0] + k * STEP, sig(tp + win[0] + k * STEP)) for k in range(n)]
    pts = [(x, y) for x, y in pts if y is not None]
    if len(pts) < 0.8 * n:
        return None
    xs, ys = [x for x, _ in pts], [y for _, y in pts]
    my = statistics.fmean(ys)
    sst = sum((y - my) ** 2 for y in ys)
    if sst <= 0:
        return None
    taus = [TAU_LO + k * STEP for k in range(int(round((TAU_HI - TAU_LO) / STEP)) + 1)]
    fits = []
    for tau in taus:
        h = [min(1.0, max(0.0, (x - tau) / RAMP + 0.5)) for x in xs]
        mh = statistics.fmean(h)
        shh = sum((v - mh) ** 2 for v in h)
        if shh <= 0:
            fits.append((math.inf, 0, 0)); continue
        b = sum((v - mh) * (y - my) for v, y in zip(h, ys)) / shh
        a = my - b * mh
        fits.append((sst - b * b * shh, a, b))  # SSE of the least-squares fit
    k = min(range(len(taus)), key=lambda i: fits[i][0])
    if k in (0, len(taus) - 1):
        return None
    sse, a, b = fits[k]
    if (b <= 0) if kind == "start" else (b >= 0):
        return None
    if min_jump is not None and abs(b) < min_jump:
        return None
    r2 = 1 - sse / sst
    if r2 < MIN_R2:
        return None
    e1, e2, e3 = fits[k - 1][0], sse, fits[k + 1][0]
    den = e1 - 2 * e2 + e3
    tau = taus[k] + (0.5 * STEP * (e1 - e3) / den if den > 0 else 0.0)
    return {"lag": round(tau, 2), "r2": round(r2, 3), "rms": round(math.sqrt(sse / len(xs)), 3),
            "lo": round(a, 2), "hi": round(a + b, 2)}


def step_signals(S, D, F):
    """name -> (signal, min_jump). GPS along-track speed, distance-field speed, record fields."""
    out = {"pos": (lambda t: speed(S, t, 0.5), 1.0), "dist": (lambda t: speed(D, t, 0.5), 1.0)}
    for name, f, mj in (("speed", F.get("speed"), 1.0), ("cad", F.get("cad"), None), ("power", F.get("power"), None)):
        if f is not None:
            out[name] = (f, mj)
    return out


def press_steps(S, D, F, tp, kind):
    """Step lags of every signal at a start/end press, plus the rep pace v and the speed change dv."""
    if kind == "start":
        a, b = S(tp + 10), S(tp + 20)
        v = None if a is None or b is None else (b - a) / 10
        a0, b0 = S(tp - 6), S(tp - 1)
        v0 = None if a0 is None or b0 is None else (b0 - a0) / 5
    else:
        a, b = S(tp - 12), S(tp - 4)
        v = None if a is None or b is None else (b - a) / 8
        a0, b0 = S(tp + 8), S(tp + 14)
        v0 = None if a0 is None or b0 is None else (b0 - a0) / 6
    if v is None or v0 is None:
        return None
    res = {}
    for name, (sig, mj) in step_signals(S, D, F).items():
        r = step_lag(sig, tp, kind, min_jump=mj)
        if r:
            res[name] = r
    return {"v": round(v, 2), "dv": round(abs(v - v0), 2), "lags": res}


def boot_median_ci(by_session, n_boot=2000, level=0.9, seed=1):
    """Bootstrap CI of the pooled median, resampling sessions (lists of lags) with replacement."""
    groups = [g for g in by_session if g]
    if len(groups) < 2:
        return None, None
    rng = random.Random(seed)
    meds = []
    for _ in range(n_boot):
        pool = [x for g in rng.choices(groups, k=len(groups)) for x in g]
        meds.append(statistics.median(pool))
    meds.sort()
    lo, hi = (1 - level) / 2, (1 + level) / 2
    return round(meds[int(lo * n_boot)], 2), round(meds[min(n_boot - 1, int(hi * n_boot))], 2)


def lag_tables(steps):
    """lag_table rows, lag_vs_pace points, extra regressions and lag_compensation from per-press step fits."""
    pts = [{"id": p["id"], "mode": p["mode"], "device": p["device"], "kind": p["kind"], "signal": sig,
            "v": p["v"], "dv": p["dv"], "lag": r["lag"], "dist_m": round(r["lag"] * p["dv"], 2), "r2": r["r2"]}
           for p in steps for sig, r in p["lags"].items()]
    table, regs, comp = [], {}, {}
    keys = sorted({(x["mode"], x["device"], x["signal"], x["kind"]) for x in pts},
                  key=lambda k: (k[0], k[1], ["pos", "dist", "speed", "cad", "power"].index(k[2]), k[3]))
    for mode, dev, sig, kind in keys:
        P = [x for x in pts if (x["mode"], x["device"], x["signal"], x["kind"]) == (mode, dev, sig, kind)]
        lags = [x["lag"] for x in P]
        sids = sorted({x["id"] for x in P})
        lo, hi = boot_median_ci([[x["lag"] for x in P if x["id"] == s] for s in sids])
        o = ols([(x["v"], x["lag"]) for x in P])
        ow = ols(within_session(P, "v", "lag", min_n=3))
        om = ols([(x["v"], x["dist_m"]) for x in P])
        table.append({"mode": mode, "device": dev, "signal": sig, "kind": kind, "median": round(statistics.median(lags), 2),
                      "p10": round(q(lags, 0.1), 2), "p90": round(q(lags, 0.9), 2), "n": len(lags), "n_sessions": len(sids),
                      "ci_lo": lo, "ci_hi": hi, "slope_s_per_mps": o and o["slope"], "slope_se": o and o["slope_se"],
                      "slope_within_s_per_mps": ow and ow["slope"], "slope_within_se": ow and ow["slope_se"],
                      "dist_m_median": round(statistics.median(x["dist_m"] for x in P), 2),
                      "dist_m_slope_m_per_mps": om and om["slope"], "dist_m_slope_se": om and om["slope_se"]})
        tag = f"step/{mode}/{dev}/{sig}/{kind}"
        regs[f"{tag} lag~v"], regs[f"{tag} lag~v within session"], regs[f"{tag} dist_m~v"] = o, ow, om
    for mode, dev in sorted({(x["mode"], x["device"]) for x in pts}):
        row = {}
        for kind in ("start", "end"):
            L = [x["lag"] for x in pts if (x["mode"], x["device"], x["signal"], x["kind"]) == (mode, dev, "dist", kind)]
            row[f"{kind}_s"] = round(statistics.median(L), 2) if L else None
            row[f"n_{kind}"] = len(L)
        comp[f"{mode}/{dev}"] = row
    vs_pace = [{k: x[k] for k in ("mode", "device", "signal", "kind", "v", "lag")} for x in pts]
    return table, vs_pace, regs, comp


def lap_vs_geometry(S, offs, D, a, b, lap):
    """Garmin lap distance vs lane-corrected geometry for a lap whose ends are both at a 100 m mark."""
    sa, sb = S(a), S(b)
    if sa is None or sb is None or b - a < 20:
        return None
    if any(S(t) is None for t in range(int(a) + 1, int(b), 2)):  # must stay on the track
        return None
    near = lambda s: abs((s - S_LINE + 50) % 100 - 50)
    if near(sa) > 15 or near(sb) > 15:
        return None
    lane1 = round((sb - sa) / 100) * 100
    if lane1 <= 0:
        return None
    i, j = bisect.bisect_left(S.ts, a), bisect.bisect_right(S.ts, b)
    o = statistics.median(offs[i:j])
    expected = lane1 * (1 + 2 * math.pi * max(0.0, o) / LAP)
    garmin = lap.get("total_distance") or 0
    return {"time": round(b - a, 1), "lane1": lane1, "lane_offset": round(o, 2), "expected": round(expected, 1),
            "garmin": round(garmin, 1), "dist_field": round((D(b) or 0) - (D(a) or 0), 1),
            "v": round(expected / (b - a), 2), "err_m": round(garmin - expected, 1),
            "err_pct": round(100 * (garmin / expected - 1), 2)}


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))]


def summ(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    return {"median": round(statistics.median(xs), 2), "p10": round(q(xs, 0.1), 2), "p90": round(q(xs, 0.9), 2), "n": len(xs)}


def ols(pairs):
    """y = intercept + slope * x, with the slope's standard error."""
    pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
    if len(pairs) < 8:
        return None
    xs, ys = zip(*pairs)
    b, a = statistics.linear_regression(xs, ys)
    mx = statistics.fmean(xs)
    res = sum((y - a - b * x) ** 2 for x, y in pairs)
    se = math.sqrt(res / (len(xs) - 2) / sum((x - mx) ** 2 for x in xs))
    return {"intercept": round(a, 2), "slope": round(b, 2), "slope_se": round(se, 2), "n": len(xs)}


def within_session(rows, kx, ky, min_n=5):
    """(x, y) pairs with each session's mean removed: pace effect without between-session GPS bias."""
    out = []
    for sid in {r["id"] for r in rows}:
        P = [(r[kx], r[ky]) for r in rows if r["id"] == sid and r.get(kx) is not None and r.get(ky) is not None]
        if len(P) >= min_n:
            mx, my = statistics.fmean(x for x, _ in P), statistics.fmean(y for _, y in P)
            out += [(x - mx, y - my) for x, y in P]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("person")
    d = ROOT / "data" / ap.parse_args().person.lower()
    cal = json.loads((d / "calibration.json").read_text())
    track = object.__new__(Track)  # the model of calibrate_distance.py, from its saved parameters
    track.c, track.theta = tuple(cal["centre"]), math.radians(cal["theta_deg"])
    track.u = (math.cos(track.theta), math.sin(track.theta))

    sessions, presses, laps_out, steps = [], [], [], []
    for f in sorted((d / "activities").glob("*/*.fit")):
        if "running" not in f.parent.name:
            continue
        rows, laps, sess, prod, t0 = load(f)
        first = next((r["pos"] for r in rows if r["pos"]), None)
        if not first or hav(first, track.c) > 400 or sum(str(x.get("lap_trigger")) == "manual" for x in laps) < 3:
            continue
        S, offs, D = track_series(rows, track)
        F = {k: field_series(rows, k) for k in ("speed", "cad", "power")}
        if len(S.ts) < 100:
            continue
        mode = "track" if str(sess.get("sub_sport")) == "track" else "gps"
        base = {"id": int(f.stem), "date": f.parent.name[:10], "mode": mode, "device": prod}
        pts, timing = press_times(laps, t0)
        mine = []
        for i, (tp, lap, cum_d) in enumerate(pts):
            if str(lap.get("lap_trigger")) == "manual":
                r = analyse_press(S, D, tp, cum_d)
                if r:
                    mine.append({**base, "press": i + 1, "t": round(tp, 2), **r})
                    if r["kind"] in ("start", "end"):
                        st = press_steps(S, D, F, tp, r["kind"])
                        if st:
                            steps.append({**base, "press": i + 1, "kind": r["kind"], "at_line": r["at_line"], **st})
        presses += mine
        kinds = {p["press"]: p["kind"] for p in mine}
        if mode == "gps":
            bounds = [0.0] + [tp for tp, _, _ in pts]
            for i, (a, b) in enumerate(zip(bounds, bounds[1:])):
                r = lap_vs_geometry(S, offs, D, a, b, laps[i])
                if r:
                    laps_out.append({**base, "lap": i + 1, "from": kinds.get(i, "session_start"), "to": kinds.get(i + 1), **r})
        through = [p for p in mine if p["kind"] == "through" and p["at_line"]]
        sessions.append({**base, "presses": len(pts), "timing": timing, "lane_offset": round(statistics.median(offs), 2),
                         "xcorr_dist_vs_pos": xcorr_lag(S, D) if mode == "gps" else None,
                         "through_v": summ([p["v"] for p in through]),
                         "through_pos_dt": summ([p.get("pos_dt") for p in through])})

    # summaries
    summary, regs = {}, {}
    keys = ("pos_offset", "pos_dt", "book_m", "book_s", "onset5_pos", "onset5_dist", "half_pos", "half_dist",
            "stop_pos", "stop_dist", "v")
    groups = [(m, None) for m in ("gps", "track")] + [("gps", dev) for dev in sorted({p["device"] for p in presses if p["mode"] == "gps"})]
    for mode, dev in groups:
        for kind in ("through", "end", "start"):
            K = [p for p in presses if p["mode"] == mode and p["kind"] == kind and p["at_line"] and dev in (None, p["device"])]
            for k in keys if dev is None else keys[:2] + keys[3:-1]:
                s = summ([p.get(k) for p in K])
                if s:
                    summary[f"{mode}/{dev or 'all'}/{kind}/{k}"] = s
            # constant time lag: pos_dt flat in v, pos_offset = -lag * v; constant distance: the reverse
            if kind == "through":
                regs[f"{mode}/{dev or 'all'}/through pos_offset~v"] = ols([(p.get("v"), p.get("pos_offset")) for p in K])
                regs[f"{mode}/{dev or 'all'}/through pos_dt~v"] = ols([(p.get("v"), p.get("pos_dt")) for p in K])
                regs[f"{mode}/{dev or 'all'}/through pos_dt~v within session"] = ols(within_session(K, "v", "pos_dt"))
    summary["gps/xcorr_dist_vs_pos (per session)"] = summ([s["xcorr_dist_vs_pos"] for s in sessions])
    lap_groups = {"line_to_line_running": lambda x: x["from"] == "through" and x["to"] == "through",
                  "rep_start_to_end": lambda x: x["from"] == "start" and x["to"] == "end"}
    for name, keep in lap_groups.items():
        L = [x for x in laps_out if keep(x)]
        for k in ("err_pct", "err_m", "lane_offset"):
            summary[f"lap/{name}/{k}"] = summ([x[k] for x in L])
        regs[f"lap/{name} err_m~v"] = ols([(x["v"], x["err_m"]) for x in L])
        regs[f"lap/{name} err_pct~v"] = ols([(x["v"], x["err_pct"]) for x in L])

    lag_table, lag_vs_pace, step_regs, lag_comp = lag_tables(steps)
    regs.update(step_regs)
    out = {"line_s": round(S_LINE, 2), "summary": summary, "regressions": regs, "sessions": sessions,
           "presses": presses, "laps": laps_out, "lag_table": lag_table, "lag_vs_pace": lag_vs_pace,
           "lag_compensation": lag_comp, "step_fits": steps}
    (d / "gps_lap_analysis.json").write_text(json.dumps(out, indent=1))

    print(f"line at s = {S_LINE:.2f} m")
    for s in sessions:
        dt = s["through_pos_dt"]
        print(f"  {s['date']} {s['mode']:5} {s['device']:6} presses {s['presses']:3}  lane {s['lane_offset']:+.1f} m  "
              f"timing {s['timing']:14}  xcorr {s['xcorr_dist_vs_pos']}  "
              + (f"through: v {s['through_v']['median']:.2f}  pos_dt {dt['median']:+.2f} ({dt['p10']:+.2f}..{dt['p90']:+.2f}, n {dt['n']})" if dt else ""))
    print(f"\n{'metric':44} {'median':>7} {'p10':>7} {'p90':>7} {'n':>4}")
    for k, s in summary.items():
        if s:
            print(f"{k:44} {s['median']:7.2f} {s['p10']:7.2f} {s['p90']:7.2f} {s['n']:4}")
    print("\nregressions  y = intercept + slope * v [m/s]")
    for k, r in regs.items():
        if r:
            print(f"  {k:40} intercept {r['intercept']:+7.2f}  slope {r['slope']:+6.2f} ± {r['slope_se']:.2f}  n {r['n']}")
    print(f"\nstep-model lag vs press (s); slope = d lag / d v pooled | within session; dist_m = lag * dv")
    print(f"{'group':30} {'med':>6} {'p10':>6} {'p90':>6} {'n':>4} {'ses':>3} {'90% CI':>14} {'slope':>13} {'within':>13} {'m':>6} {'m slope':>13}")
    fmt = lambda b, se: f"{b:+5.2f}±{se:4.2f}" if b is not None else "-"
    for r in lag_table:
        ci = f"{r['ci_lo']:+.2f}..{r['ci_hi']:+.2f}" if r["ci_lo"] is not None else "-"
        print(f"{r['mode'] + '/' + r['device'] + '/' + r['signal'] + '/' + r['kind']:30} {r['median']:+6.2f} {r['p10']:+6.2f} "
              f"{r['p90']:+6.2f} {r['n']:4} {r['n_sessions']:3} {ci:>14} {fmt(r['slope_s_per_mps'], r['slope_se']):>13} "
              f"{fmt(r['slope_within_s_per_mps'], r['slope_within_se']):>13} {r['dist_m_median']:+6.2f} "
              f"{fmt(r['dist_m_slope_m_per_mps'], r['dist_m_slope_se']):>13}")
    print("\nlag_compensation (distance field vs press):", json.dumps(lag_comp))


if __name__ == "__main__":
    main()

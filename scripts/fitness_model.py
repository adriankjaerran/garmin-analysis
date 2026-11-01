"""Fitness / shape model: per-session performance estimates and smoothed shape per discipline.

Usage: uv run scripts/fitness_model.py adrian [--eval] [--workers 8]
Writes data/<person>/fitness.json (see docs/fitness-model.md). Needs data/<person>/groups.json
(scripts/session_groups.py). Per-session features are cached in data/<person>/cache/fitness_model.pkl
(keyed by FIT mtime+size and FEATURE_VERSION).

Scale: Daniels VDOT (ml/kg/min-equivalent, "running VO2max as shown by performance"). Every VDOT
maps to equivalent race times (`race_time`), e.g. VDOT 60 = 10k in 35:22.

Disciplines (see docs):
  interval  short/fast running: W'-balance model (critical speed CS + D' from the VDOT, Skiba
            recovery) on grade-adjusted speed: the lowest VDOT for which the whole session was
            physically possible. Rest periods enter through W' recovery (work:rest).
  long      long-distance running: Daniels VDOT of the best continuous grade-adjusted effort >= 20 min
            (races, tempo) + an HR-based aerobic estimate from steady running (speed per %HRR, Swain).
  ski       XC/roller ski: mechanical power from climb rate + rolling friction + drag, converted to
            VO2 with an assumed gross efficiency, scored like a run (best sustained demand / Daniels %VO2max).
  overall   precision-weighted mix of the disciplines as % of each one's personal best.
Running (interval + long) is published from one coupled distance curve (`smooth_curve`, `CURVE`): VDOT(km, t) =
theta_t + slope_t * log2(km/5); interval = the curve at 5 km, long = at the 10k-half log-mean (~14.5 km), and the
race predictor (fitness.json `predictor`, any distance) reads the same curve.

Two variants: "pace" (performance only; HR is diagnostics) and "hr" (same scores, but HR effort decides whether a
hard session was maximal: sub-max -> lower bound, else weighted by p(maximal), FR935 HR discounted). See `apply_hr_intent`.

Importable API (for a backend): `build(person)` -> fitness doc; `load_features`, `collect_evidence`,
`score_session`, `smooth_series`, `forecast_errors`, `smooth_curve`, `curve_predict`, `evidence_km`; physiology `vdot_from_race`, `race_time`, `speed_for`,
`cs_dprime`; model pieces `gap_factor`, `wbal_vdot`, `continuous_vdot`, `ski_effort`, `hr_aerobic`,
`session_series`, `session_features`. Back-tests: scripts/fitness_eval.py (`run(person)`).
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import glob
import json
import math
import pickle
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import session_groups as sg  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MODEL_VERSION = 2       # fitness.json schema/model; 2: running from the coupled distance curve, `predictor` block
FEATURE_VERSION = 1     # per-session feature cache key (bump only when session_features changes)
NEW_WATCH_FROM = "2026-02-18"

P = {
    # running
    "grade_window_s": 45,        # grade over +-45 s of the 9 s smoothed altitude (road-interval test-retest SD
                                 # 2.5 at +-20 s, 2.0 at +-45 s, 3.1 without grade; +-10 s inflated hill reps by 16)
    "grade_clip": 0.20,
    "vz_glitch": 1.0,            # m/s: altitude steps faster than this (running) are barometer glitches -> grade 0
    "downhill_share": 0.5,       # share of Minetti's downhill saving we believe (Minetti overstates it at speed)
    "v_max_run": 8.0,            # m/s: faster grid speeds are GPS spikes
    "wbal_dt": 2,                # s: W'bal step
    "tau_scale": 0.35,           # multiplier on Skiba's recovery time constant (fitted: rep-length bias ~0, best forecast)
    "interval_offset": -2.5,     # VDOT added to W'bal interval scores so they match 5k/10k race VDOT
    "tempo_offset": 3.0,         # VDOT added to tempo (>= 20 min continuous) scores: tempo is run below race effort
    "cross_check": 3.0,          # long-run evidence > interval shape + 3 VDOT + 2 sd is implausible (mislabelled / GPS)
    "dcp_ref_w": 250.0,          # W: Skiba's DCP for a 'full' rest, expressed as share of CS
    "cont_min_s": 1200,          # long-discipline continuous effort >= 20 min
    # HR
    "hr_max": 206,               # profile (Garmin HR_MAX zones); data p98 194..207 depending on year/watch
    "hr_rest_default": 50,
    "hr_chunk_s": 180,
    "hr_warmup_s": 600,
    "hr_min_hrr": 0.45,          # below this %HRR the HR-VO2 line is unreliable
    "hr_max_cv": 0.12,
    # HR-intent variant ("pace+hr"): HR effort decides how maximal a session labelled hard really was
    "hr_intent": {
        "thr": {"interval": 0.85, "long": 0.85, "ski": 0.82},  # %HRR of a maximal effort (work-HR p90 / last 2/3 of the window)
        "sd": 0.04,                                           # spread of maximal-effort %HRR around thr
        "trust": {"fr935": {"interval": 0.3, "steady": 0.5},  # FR935 wrist HR under-reads interval bursts erratically; 0.5: never demotes alone
                  "4315": {"interval": 1.0, "steady": 1.0}},
        "demote_p": 0.5,                                      # p(maximal) below this: hard -> bound (in-sample choice, see docs)
    },
    # ski
    "ski_eff": 0.16,             # gross efficiency (Sandbakk et al.: 15-18 % roller ski)
    "ski_mu": {"roller?": 0.025, "snow": 0.035, "unknown": 0.03},
    "ski_cda_per_kg": 0.5 / 75,  # CdA m² per kg body mass
    "rho": 1.2,
    # smoother
    "q_per_day": {"interval": 0.05, "long": 0.02, "ski": 0.05},  # VDOT² per day random walk: best one-step predictive log-lik, capped at 0.05
    "prior_sd": 6.0,
}
DISCIPLINES = ("interval", "long", "ski")
STALE_DAYS = 90        # current shape without a race / hard session (weight > 0.5) for longer than this: `stale`
LABEL = {"interval": "Interval shape (short/fast running)", "long": "Long-distance running shape",
         "ski": "Ski shape (roller + snow)", "overall": "Overall (all disciplines, % of personal best)"}


# ------------------------------------------------------------------ physiology (Daniels & Gilbert 1979)
def daniels_vo2(v_ms):
    """O2 cost (ml/kg/min) of running at v m/s on the flat."""
    v = np.asarray(v_ms, float) * 60
    return -4.60 + 0.182258 * v + 0.000104 * v * v


def daniels_pct(t_s):
    """Share of VO2max sustainable for a race of t seconds."""
    t = np.asarray(t_s, float) / 60
    return 0.8 + 0.1894393 * np.exp(-0.012778 * t) + 0.2989558 * np.exp(-0.1932605 * t)


def vdot_from_perf(v_ms, t_s):
    return daniels_vo2(v_ms) / daniels_pct(t_s)


def vdot_from_race(dist_m, time_s):
    return float(vdot_from_perf(dist_m / time_s, time_s))


def speed_for(vdot, t_s):
    """Speed (m/s) a runner of this VDOT holds for t seconds."""
    target = np.asarray(vdot, float) * daniels_pct(t_s)
    a, b, c = 0.000104, 0.182258, -4.60 - target
    return (-b + np.sqrt(b * b - 4 * a * c)) / (2 * a) / 60


def race_time(vdot, dist_m):
    """Equivalent race time (s) for a distance."""
    lo, hi = dist_m / 12.0, dist_m / 1.0
    for _ in range(60):
        m = 0.5 * (lo + hi)
        if speed_for(vdot, m) * m > dist_m:
            hi = m
        else:
            lo = m
    return 0.5 * (lo + hi)


_VG = np.arange(20.0, 95.01, 0.5)


def _cs_table():
    t = np.linspace(180, 900, 40)
    X = np.c_[np.ones_like(t), 1 / t]
    cs, dp = [], []
    for V in _VG:
        c, d = np.linalg.lstsq(X, speed_for(V, t), rcond=None)[0]
        cs.append(c); dp.append(d)
    return np.array(cs), np.array(dp)


_CS, _DP = _cs_table()


def cs_dprime(vdot):
    """Critical speed (m/s) and D' (m) implied by a VDOT: CS model fitted to Daniels' 3-15 min speeds."""
    return np.interp(vdot, _VG, _CS), np.interp(vdot, _VG, _DP)


def vdot_from_cs(cs):
    return float(np.interp(cs, _CS, _VG))


# ------------------------------------------------------------------ grade / ski mechanics
def minetti_cost(i):
    """Minetti et al. 2002 energy cost of running (J/kg/m) at grade i (fraction)."""
    i = np.asarray(i, float)
    return 155.4 * i**5 - 30.4 * i**4 - 43.3 * i**3 + 46.3 * i**2 + 19.5 * i + 3.6


def gap_factor(grade, downhill_share=None):
    """Equivalent-flat-speed multiplier: Minetti uphill, a share of Minetti's saving downhill."""
    ds = P["downhill_share"] if downhill_share is None else downhill_share
    g = np.clip(np.asarray(grade, float), -P["grade_clip"], P["grade_clip"])
    f = minetti_cost(g) / 3.6
    return np.where(g < 0, 1 + ds * (f - 1), f)


def ski_vo2_demand(v, vz, mu, eff=None):
    """VO2 demand (ml/kg/min) of skiing at v m/s with vertical speed vz m/s: (climb + friction + drag) / eff."""
    eff = P["ski_eff"] if eff is None else eff
    pm = 9.81 * vz + mu * 9.81 * v + 0.5 * P["rho"] * P["ski_cda_per_kg"] * v**3
    return 3.5 + np.maximum(pm, 0) / eff * 60 / 20.9


# ------------------------------------------------------------------ per-session series
def _shift_diff(x, w):
    out = np.zeros_like(x)
    if len(x) > 2 * w:
        out[w:-w] = x[2 * w:] - x[:-2 * w]
        out[:w], out[-w:] = out[w], out[-w - 1]
    return out


def session_series(fit, act):
    """1 Hz series from the FIT: v (m/s), vg (grade-adjusted / ski VO2 demand), hr, legs (pause-free spans)."""
    G = sg.to_grid(sg.read_series(Path(fit)))
    sg.indoor_grid(G, act)   # Bislett indoor: reps of n x 546.5 m - 7.5 m, 0 between (watch distance is made up there)
    T = G["T"]
    v = np.clip(G["v"], 0, P["v_max_run"] if act["sport"] == "run" else 20.0)
    flat = act.get("atype") in ("track_running", "treadmill_running", "indoor_running") or act.get("place") == "Bislet"
    w = P["grade_window_s"]
    grade = np.zeros(T + 1)
    if "alt" in G and not flat:
        dh, dd = _shift_diff(G["alt"], w), _shift_diff(G["d"], w)
        grade = np.where(dd > 30, dh / np.maximum(dd, 1), 0.0)
        if act["sport"] == "run":
            jump = np.abs(np.diff(G["alt"], prepend=G["alt"][0])) > P["vz_glitch"]
            if jump.any():  # zero the grade within the window around a glitch
                bad = np.convolve(jump.astype(float), np.ones(2 * w + 1), mode="same") > 0
                grade[bad] = 0.0
    alt = G["alt"] if "alt" in G and not flat else np.zeros(T + 1)
    if act["sport"] == "run":
        vg = v * gap_factor(grade)
    else:  # per-second power without the climb term; climb enters per window as net altitude change
        vg = ski_vo2_demand(v, 0.0, P["ski_mu"].get(act.get("surface") or "unknown", 0.03)) - 3.5
    pauses = sg.find_pauses(G)
    legs, cur = [], 0
    for a, b in pauses:
        if a > cur:
            legs.append((cur, a))
        cur = b
    if cur < T:
        legs.append((cur, T + 1))
    return {"T": T, "v": v, "vg": vg, "grade": grade, "alt": alt, "hr": G.get("hr"), "legs": legs, "d": G["d"],
            "moving": ~G["stopped"]}


# ------------------------------------------------------------------ W' balance interval model
def wbal_min(vg, cs, dp, dt=None, tau_scale=None):
    """Lowest W'bal / D' over the session for candidate (cs, dp) arrays. Depletion (v-CS)*dt above CS;
    below CS exponential recovery with Skiba's tau = 316 + 546 exp(-0.01 DCP), DCP = dcp_ref * (CS-v)/CS."""
    dt = P["wbal_dt"] if dt is None else dt
    ts = P["tau_scale"] if tau_scale is None else tau_scale
    n = len(vg) // dt
    x = vg[: n * dt].reshape(n, dt).mean(1)
    cs = np.asarray(cs, float); dp = np.asarray(dp, float)
    W = dp.copy(); Wmin = np.ones_like(dp)
    k = 0.01 * P["dcp_ref_w"]
    # skip leading/trailing stretches where nobody is above the lowest CS
    hot = np.nonzero(x > cs.min())[0]
    if not len(hot):
        return Wmin
    for xi in x[hot[0]: hot[-1] + 1]:
        over = xi - cs
        tau = ts * (316 + 546 * np.exp(-k * np.maximum(-over, 0) / cs))
        W = np.where(over > 0, W - over * dt, dp - (dp - W) * np.exp(-dt / tau))
        np.minimum(Wmin, W / dp, out=Wmin)
    return Wmin


def wbal_vdot(vg, tau_scale=None, lo=25.0, hi=90.0):
    """Lowest VDOT whose (CS, D') make the whole session possible (min W'bal >= 0), coarse-to-fine grid."""
    for _ in range(3):
        grid = np.linspace(lo, hi, 9)
        cs, dp = cs_dprime(grid)
        m = wbal_min(vg, cs, dp, tau_scale=tau_scale)
        ok = np.nonzero(m >= 0)[0]
        if not len(ok):
            return None
        j = ok[0]
        if j == 0:
            return float(lo) if lo <= 25.0 else float(grid[0])
        # linear interpolation of the crossing
        lo, hi = grid[j - 1], grid[j]
        last = (m[j - 1], m[j])
    return float(lo + (hi - lo) * (-last[0]) / (last[1] - last[0]))


# ------------------------------------------------------------------ continuous efforts
def best_window(x, legs, D):
    """Best mean of x over D s inside one pause-free leg: (mean, start) or (None, None)."""
    best, at = None, None
    for a, b in legs:
        if b - a < D:
            continue
        c = np.concatenate([[0.0], np.cumsum(x[a:b])])
        m = (c[D:] - c[:-D]) / D
        i = int(np.argmax(m))
        if best is None or m[i] > best:
            best, at = float(m[i]), a + i
    return best, at


CONT_D = (1200, 1800, 2700, 3600, 5400)


def continuous_vdot(S, durations=CONT_D):
    """Daniels VDOT of the best continuous grade-adjusted window for each duration; returns best."""
    out = None
    for D in durations:
        m, at = best_window(S["vg"], S["legs"], D)
        if m is None:
            continue
        vd = float(vdot_from_perf(m, D))
        if out is None or vd > out["vdot"]:
            out = {"vdot": vd, "dur_s": D, "start": at, "speed": m}
    return out


SKI_D = (1200, 1800, 2700, 3600, 5400)


def ski_effort(S, durations=SKI_D):
    """Ski: best sustained VO2 demand over D s of *moving* time (pauses >= 15 s cut out: the Holmenkollen
    climb has 8-15 min of traffic-light / regroup stops), scored with Daniels' %VO2max for D.
    Demand per window = 3.5 + (g * net climb / D + mean(friction + drag power)) / eff. The net altitude
    change of the window is used, not the per-second climb rate clipped at 0, because clipping rectifies
    barometer noise (hysteresis climb on the Holmenkollen route: 361..568 m for the same ~390 m net climb).
    Windows are >= 20 min because the FR935's altitude over a single leg is off by up to +-40 %."""
    keep = np.zeros(S["T"] + 1, bool)
    for a, b in S["legs"]:
        keep[a:b] = True
    idx = np.nonzero(keep)[0]
    n = len(idx)
    vg, alt, d = S["vg"][idx], S["alt"][idx], S["d"][idx]
    c = np.concatenate([[0.0], np.cumsum(vg)])
    k = 9.81 / P["ski_eff"] * 60 / 20.9
    out = None
    for D in durations:
        if n <= D:
            continue
        m = 3.5 + (c[D:n] - c[:n - D]) / D + k * (alt[D:] - alt[:-D]) / D
        i = int(np.argmax(m))
        vd = float(m[i] / daniels_pct(D))
        if out is None or vd > out["vdot"]:
            out = {"vdot": vd, "dur_s": D, "start": int(idx[i]), "end": int(idx[i + D]), "demand": float(m[i]),
                   "climb_m": round(float(alt[i + D] - alt[i])), "speed": float((d[i + D] - d[i]) / D)}
    return out


# ------------------------------------------------------------------ heart rate
def hr_rest_series(D):
    """{date: resting HR} with a 30-day rolling median (robust to bad nights / not worn)."""
    raw = {}
    for f in sorted(glob.glob(str(D / "ranges" / "daily_summaries_*.json"))):
        try:
            for d in json.loads(Path(f).read_text())["data"]:
                r = (d.get("heartRate") or {}).get("restingValue")
                if r:
                    raw[d["calendarDate"]] = r
        except Exception:
            continue
    if not raw:
        return {}
    days = sorted(raw)
    o = np.array([dt.date.fromisoformat(x).toordinal() for x in days]); v = np.array([raw[x] for x in days], float)
    out = {}
    for x, oo in zip(days, o):
        m = np.abs(o - oo) <= 15
        out[x] = float(np.median(v[m]))
    return out


def hr_aerobic(S, sport, hr_rest, hr_max=None):
    """Swain-style VO2max from steady chunks: 3.5 + (VO2 - 3.5) / %HRR, median over chunks.
    Also efficiency factor (vg per beat) and aerobic decoupling (EF first vs second half)."""
    hr = S["hr"]
    if hr is None:
        return None
    hm = P["hr_max"] if hr_max is None else hr_max
    C = P["hr_chunk_s"]
    est, ef, tmid, hrr_l = [], [], [], []
    for a, b in S["legs"]:
        a0 = max(a, P["hr_warmup_s"])
        for s in range(a0, b - C + 1, C):
            x = S["vg"][s: s + C]; h = hr[s: s + C]
            if np.isnan(h).mean() > 0.1:
                continue
            mx = float(x.mean())
            if sport == "run":
                if mx < 1.8 or x.std() / mx > P["hr_max_cv"]:
                    continue
                # steady state: the 3 min before the chunk at a similar speed (HR lags ~1 min)
                pre = S["vg"][max(a, s - 180): s]
                if len(pre) < 120 or abs(pre.mean() / mx - 1) > 0.1:
                    continue
                vo2 = float(daniels_vo2(mx))
            else:
                if S["v"][s: s + C].mean() < 1.5 or S["v"][s: s + C].min() < 0.5:
                    continue
                vo2 = 3.5 + mx + 9.81 / P["ski_eff"] * 60 / 20.9 * (S["alt"][s + C - 1] - S["alt"][s]) / C
                if vo2 < 15:
                    continue
                mx = vo2
            hh = float(np.nanmean(h))
            hrr = (hh - hr_rest) / (hm - hr_rest)
            ef.append(mx / hh); tmid.append(s + C / 2)
            if hrr < P["hr_min_hrr"] or hrr > 1.0:
                continue
            est.append(3.5 + (vo2 - 3.5) / hrr); hrr_l.append(hrr)
    out = {"n_chunks": len(est)}
    if est:
        out.update(vdot=float(np.median(est)), iqr=float(np.subtract(*np.percentile(est, [75, 25]))),
                   hrr=float(np.median(hrr_l)))
    if len(ef) >= 8:
        ef = np.array(ef); h2 = len(ef) // 2
        out["ef"] = float(np.median(ef))
        out["decoupling_pct"] = float(100 * (np.median(ef[:h2]) / np.median(ef[h2:]) - 1))
    return out


def effort_hrr(S, a, b, hr_rest, hr_max=None):
    """%HRR of the 90th percentile HR in [a, b) (effort indicator for pace scores)."""
    hr = S["hr"]
    if hr is None:
        return None
    h = hr[a:b]; h = h[~np.isnan(h)]
    if len(h) < 30:
        return None
    hm = P["hr_max"] if hr_max is None else hr_max
    return float((np.percentile(h, 90) - hr_rest) / (hm - hr_rest))


# ------------------------------------------------------------------ per-session features (cached)
TAU_VARIANTS = (0.15, 0.25, 0.35, 0.5, 1.0)


def session_features(fit, act, hr_rest):
    """Everything the model needs from one FIT (independent of the other sessions)."""
    S = session_series(fit, act)
    f = {"T": S["T"], "moving_s": int(S["moving"].sum()), "dist_m": float(S["d"][-1])}
    hr = S["hr"]
    if act["sport"] == "run":
        f["wbal"] = {str(k): wbal_vdot(S["vg"], tau_scale=k) for k in TAU_VARIANTS}
        f["wbal_flat"] = wbal_vdot(S["v"])            # without grade adjustment (evaluation)
        c = continuous_vdot(S)
        if c:
            c["hrr"] = effort_hrr(S, c["start"] + c["dur_s"] // 3, c["start"] + c["dur_s"], hr_rest)
            f["cont"] = c
        # effort indicator for the interval score: HR in the hardest part (seconds above 85 % of the
        # session's 95th-percentile grade-adjusted speed, from 60 s into the first one)
        vg = S["vg"]; mv = S["moving"]
        if mv.sum() > 120:
            thr = 0.85 * np.percentile(vg[mv], 95)
            hot = np.nonzero(vg > thr)[0]
            if len(hot) > 60 and hr is not None:
                hh = hr[hot]; hh = hh[~np.isnan(hh)]
                if len(hh) > 30:
                    f["work_hr_p90"] = float(np.percentile(hh, 90))
                    f["work_hrr"] = float((f["work_hr_p90"] - hr_rest) / (P["hr_max"] - hr_rest))
                f["work_s"] = int(len(hot))
    else:
        e = ski_effort(S)
        if e:
            e["hrr"] = effort_hrr(S, e["start"] + (e["end"] - e["start"]) // 3, e["end"], hr_rest)
            f["ski"] = e
    # HR-speed line: not for Bislett indoor (speed is synthetic or made up there)
    f["hr_aer"] = hr_aerobic(S, act["sport"], hr_rest) if not act.get("indoor_bislett") else None
    if hr is not None:
        h = hr[~np.isnan(hr)]
        f["hr_max_s"] = float(h.max()) if len(h) else None
    return f


def _feat_job(job):
    fit, act, hr_rest = job
    try:
        return fit, session_features(fit, act, hr_rest)
    except Exception as e:  # noqa: BLE001
        return fit, {"err": repr(e)[:120]}


# ------------------------------------------------------------------ evidence per session
SIG = {  # observation SD (VDOT) per evidence kind; from test-retest pairs (docs/fitness-model.md)
    "race": 1.0, "int_track": 1.5, "int_road": 2.0, "int_10k": 2.0, "tempo": 2.0, "cont_bound": 2.5,
    "ski_hard": 2.5, "ski_bound": 3.0, "hr_aer": 4.0,
}
NOMINAL = [(5000, "5k"), (10000, "10k"), (21097.5, "half"), (42195, "marathon")]


def race_nominal(dist_m):
    for d, nm in NOMINAL:
        if abs(dist_m / d - 1) <= 0.035:
            return d, nm
    return None, None


def score_session(act, f, tau=None, hr_b=0.0, hr_ref=0.9, use_hr_aer=False):
    """Evidence items of one session: [{discipline, score, kind, sigma, components}].
    kind: race (maximal), hard (meant to be hard: robust below), bound (lower bound: only counts if above the
    current estimate). `hr_b` > 0 adds hr_b * (hr_ref - work %HRR) to interval scores (evaluation only)."""
    tau = P["tau_scale"] if tau is None else tau
    t, sport, out = act["type"], act["sport"], []
    if "err" in f:
        return out
    if sport == "run":
        ind = act.get("indoor_bislett")
        if ind and not any(ind.get("rep_m") or []):
            return out
        if not ind and (not act.get("dist_trusted") or act.get("atype") in ("treadmill_running", "indoor_running")):
            return out
        track = act.get("atype") == "track_running" or act.get("place") == "Bislet" or bool(ind)
        fw = f.get("wbal_flat")
        comp = {"score_without_grade": fw + P["interval_offset"] if fw is not None and t.startswith("intervals") else None,
                "work_hrr": f.get("work_hrr")}
        if t == "race?":
            nom, nm = race_nominal(f["dist_m"])
            mov = act.get("moving_s") or f["moving_s"]
            if nom:
                v = vdot_from_race(nom, mov)
                c = {"race": nm, "time_s": mov, **comp}
                if nom <= 7000:
                    out.append(("interval", v, "race", SIG["race"], c))
                else:
                    out.append(("long", v, "race", SIG["race"], c))
                    if nom == 10000:
                        out.append(("interval", v, "hard", SIG["int_10k"], c))
        elif t.startswith("intervals"):
            w = f["wbal"].get(str(tau)) if isinstance(f.get("wbal"), dict) else None
            if w is not None:
                reps, br = act.get("reps") or [], act.get("breaks_s") or []
                rep_s = float(np.median([r[0] for r in reps])) if reps else None
                rest_s = float(np.median(br)) if br else None
                c = {"sig": act.get("sig"), "n_reps": len(reps), "rep_s": rep_s, "rest_s": rest_s,
                     "work_rest": round(rep_s / rest_s, 2) if rep_s and rest_s else None,
                     "rep_speed": float(np.median(rv)) if (rv := [r[2] for r in reps if r[2]]) else None, "tau_scale": tau, **comp}
                w = w + P["interval_offset"]
                if hr_b and f.get("work_hrr") is not None:
                    w = w + hr_b * (hr_ref - f["work_hrr"])
                out.append(("interval", w, "hard", SIG["int_track" if track else "int_road"], c))
        cont = f.get("cont") if not ind else None   # indoor: only the whole-lap reps carry distance
        if cont and t != "race?":
            kind = "hard" if t == "tempo" else "bound"
            out.append(("long", cont["vdot"] + (P["tempo_offset"] if kind == "hard" else 0.0), kind, SIG["tempo" if kind == "hard" else "cont_bound"],
                        {"window_s": cont["dur_s"], "gap_speed": round(cont["speed"], 3), "hrr": cont.get("hrr")}))
        ha = f.get("hr_aer") or {}
        if use_hr_aer and ha.get("vdot") and ha["n_chunks"] >= 3 and t in ("easy", "steady", "long", "tempo"):
            out.append(("long", ha["vdot"], "hard", SIG["hr_aer"], {"hr_based": True, "n_chunks": ha["n_chunks"]}))
    elif sport == "ski":
        e = f.get("ski")
        if e and act.get("dist_trusted", True):
            kind = "hard" if t in ("ski_hard", "ski_long_race?", "ski_intervals") else "bound"   # ski_intervals: hard sessions with reps
            out.append(("ski", e["vdot"], kind, SIG["ski_hard" if kind == "hard" else "ski_bound"],
                        {"window_s": e["dur_s"], "climb_m": e["climb_m"], "speed": round(e["speed"], 2),
                         "demand": round(e["demand"], 1), "surface": act.get("surface"), "hrr": e.get("hrr")}))
    return [{"discipline": d, "score": float(s), "kind": k, "sigma": sg_, "components": c} for d, s, k, sg_, c in out]


# ------------------------------------------------------------------ HR intent (variant "hr")
VARIANTS = {"pace": "Performance only (speed, altitude, distance, breaks)",
            "hr": "Performance + heart rate (HR decides how maximal each hard session was)"}


def watch_of(date):
    return "4315" if date >= NEW_WATCH_FROM else "fr935"


def hr_intent(e):
    """p(maximal) of a hard evidence item from its HR effort, blended with the device's trust:
    p_eff = trust * Phi((hrr - thr) / sd) + (1 - trust). None without HR."""
    c = e["components"]
    h = c.get("work_hrr") if e["discipline"] == "interval" else c.get("hrr")
    if h is None:
        return None, None
    H = P["hr_intent"]
    p = 0.5 * (1 + math.erf((h - H["thr"][e["discipline"]]) / (H["sd"] * math.sqrt(2))))
    t = H["trust"][watch_of(e["date"])]["interval" if e["discipline"] == "interval" else "steady"]
    return t * p + (1 - t), h


def apply_hr_intent(ev):
    """Variant "hr": hard (not race) evidence with p(maximal) < demote_p becomes a lower bound; other hard
    evidence keeps weight in proportion to p (sigma / sqrt(0.5 + 0.5 p)): clearly maximal sessions count fully."""
    out = []
    for e in ev:
        e = {**e, "components": dict(e["components"])}
        if e["kind"] == "hard":
            p, h = hr_intent(e)
            if p is not None:
                e["components"]["p_max"] = p
                if p < P["hr_intent"]["demote_p"]:
                    e["kind"] = "bound"
                    e["sigma"] = SIG["ski_bound" if e["discipline"] == "ski" else "cont_bound"]
                    e["components"]["hr_intent"] = f"sub-max HR ({h:.2f} HRR) -> lower bound"
                else:
                    e["sigma"] = e["sigma"] / math.sqrt(0.5 + 0.5 * p)
        out.append(e)
    return out


def variant_evidence(acts, feats, variant="pace", **kw):
    ev = collect_evidence(acts, feats, **kw)
    return apply_hr_intent(ev) if variant == "hr" else ev


# ------------------------------------------------------------------ smoother
ROBUST = {"race": (2.5, 2.5), "hard": (1.0, 2.5), "bound": (None, 4.0)}  # (below, above) in sigmas
BOUND_Z = 2.0   # a lower bound only counts when it is > 2 sigma above the estimate (else: no ratchet)


def _weight(r, sig, kind):
    """Robust weight (0..1) of an observation with residual r = y - estimate."""
    lo, hi = ROBUST[kind]
    z = r / sig
    if kind == "bound" and z <= BOUND_Z:
        return 1e-6
    if z < 0:
        return 1.0 if -z <= lo else (lo / -z) ** 2
    return 1.0 if z <= hi else (hi / z) ** 2


def smooth_series(obs, day0, day1, q, prior_sd=None, iters=5):
    """Random-walk Kalman filter + RTS smoother on a daily grid with asymmetric robust weights.
    obs: list of (ordinal day, score, sigma, kind). Returns (days, mean, sd, weights per obs, forecast list).
    forecast: one-step-ahead (filtered, before the obs) mean/sd for every obs (for evaluation)."""
    prior_sd = P["prior_sd"] if prior_sd is None else prior_sd
    n = day1 - day0 + 1
    obs = sorted(obs, key=lambda o: o[0])
    if not obs:
        return np.arange(day0, day1 + 1), np.full(n, np.nan), np.full(n, np.nan), [], []
    good = [o[1] for o in obs if o[3] != "bound"] or [o[1] for o in obs]
    m0 = float(np.median(good[:5]))
    byday = collections.defaultdict(list)
    for j, o in enumerate(obs):
        byday[o[0] - day0].append(j)
    w = np.ones(len(obs))
    for it in range(iters):
        mf, vf, mp, vp = np.zeros(n), np.zeros(n), np.zeros(n), np.zeros(n)
        m, v = m0, prior_sd**2
        fc = [None] * len(obs)
        for k in range(n):
            if k:
                v = v + q
            mp[k], vp[k] = m, v
            for j in byday.get(k, ()):
                y, s, kind = obs[j][1], obs[j][2], obs[j][3]
                fc[j] = (m, math.sqrt(v))
                if it == 0:  # online robust weight from the forecast residual
                    w[j] = _weight(y - m, math.sqrt(s * s + v), kind)
                R = s * s / max(w[j], 1e-6)
                K = v / (v + R)
                m, v = m + K * (y - m), (1 - K) * v
            mf[k], vf[k] = m, v
        ms, vs = mf.copy(), vf.copy()
        for k in range(n - 2, -1, -1):
            C = vf[k] / vp[k + 1]
            ms[k] = mf[k] + C * (ms[k + 1] - mp[k + 1])
            vs[k] = vf[k] + C * C * (vs[k + 1] - vp[k + 1])
        if it == 0:
            fc0 = fc
        neww = np.array([_weight(o[1] - ms[o[0] - day0], o[2], o[3]) for o in obs])
        if np.allclose(neww, w) and it:
            break
        w = neww
    return np.arange(day0, day1 + 1), ms, np.sqrt(vs), w.tolist(), fc0


def forecast_errors(obs, q, kinds=("race", "hard")):
    """One-step-ahead errors (y - forecast) for obs of these kinds, forward filter only."""
    if not obs:
        return np.array([]), np.array([])
    d0, d1 = min(o[0] for o in obs) - 1, max(o[0] for o in obs)
    _, _, _, _, fc = smooth_series(obs, d0, d1, q, iters=1)
    obs_s = sorted(obs, key=lambda o: o[0])
    e, s = [], []
    for o, f in zip(obs_s, fc):
        if o[3] in kinds and f is not None:
            e.append(o[1] - f[0]); s.append(math.sqrt(f[1] ** 2 + o[2] ** 2))
    return np.array(e[3:]), np.array(s[3:])  # first few: prior only


# ------------------------------------------------------------------ coupled distance curve (race predictor)
# One shared running fitness trend plus a personal distance curve: VDOT(km, t) = theta_t + slope_t * x,
# x = log2(km / 5). theta (VDOT at 5 km) is a random walk with the interval q, slope (VDOT change per doubling of
# distance) a slow mean-reverting walk towards 0 (shrunk unless the evidence is strong and recent). Every running item (race / hard / bound) is an observation of the curve at its own
# effective distance (`evidence_km`). Choice of q / x-mapping / reference: scripts/shape_split_eval.py (model D),
# docs/fitness-model.md "Coupled distance curve".
CURVE = {
    "ref_km": 5.0,
    "q_theta": P["q_per_day"]["interval"],   # VDOT² / day, as the interval shape
    "q_slope": 0.005,                        # (VDOT / doubling)² / day: out-of-sample choice (eval table in docs)
    "slope_prior_sd": 1.5,                   # slope ~ N(0, 1.5²) VDOT per doubling: prior and (slope_revert) stationary sd
    "slope_revert": True,                    # slope = AR(1) towards 0 (not a random walk): the shrinkage towards the
                                             # Daniels default (0) holds at every date, so an old distance profile fades
                                             # (time constant 2·sd²/q ≈ 900 days); scripts/slope_prior_eval.py
    "interval_km": 5.0,                      # interval session scores are calibrated (offset -2.5) to 5k races
    "tempo_km": None,                        # None: the offset-calibration distance below; a number: fixed km
    "tempo_cal_km": (10.0, 21.0975),         # tempo_offset +3.0 was fitted against the 10k / half long shape:
                                             # tempo scores are 10k-half equivalents -> x = mean log2 of these
    "long_ref_km": (10.0, 21.0975),          # `long` discipline = curve at the mean x of 10k and half (~14.5 km)
    "race_sd": SIG["race"],                  # VDOT noise of a single race day (added for race predictions)
    "extrap_sd_per_doubling": 1.0,           # extra sd per doubling outside the evidenced distance range (assumed)
    "km_range": (0.8, 100.0),                # accepted prediction distances
}
DIST_LABEL = {3.0: "3k", 5.0: "5k", 10.0: "10k", 21.0975: "Half", 42.195: "Marathon"}


def curve_x(km):
    return math.log2(km / CURVE["ref_km"])


def _mean_x(kms):
    return float(np.mean([curve_x(k) for k in kms]))


def evidence_km(e):
    """Effective race distance (km) of a running evidence item for the distance curve; None = not on the curve.
    race: its nominal distance. interval (hard): CURVE interval_km (the session scores are calibrated to 5k races).
    tempo (hard long): the tempo offset's calibration distance (10k-half mean, ~14.5 km): the +3.0 lifts tempo scores
    to the 10k/half level, so placing them at the window's own distance would count the slope twice.
    long bound: the grade-adjusted distance covered in the scored window (= the race distance whose Daniels time is
    the window at that score's VDOT). The 10k race's duplicate interval/hard item is skipped (counted once, as race)."""
    c = e.get("components") or {}
    if e["discipline"] == "interval":
        if "race" in c:
            return None if e["kind"] != "race" else {"5k": 5.0}.get(c["race"], 5.0)
        return CURVE["interval_km"]
    if e["discipline"] != "long" or e["kind"] == "implausible":
        return None
    if "race" in c:
        return {"5k": 5.0, "10k": 10.0, "half": 21.0975, "marathon": 42.195}[c["race"]]
    if c.get("hr_based"):
        return None
    if e.get("type") == "tempo":       # hard (or demoted to a bound by the HR-intent variant): offset applied
        if CURVE["tempo_km"] == "window":   # evaluation only: distance at race pace (score VDOT) for the window
            return float(speed_for(e["score"], c["window_s"]) * c["window_s"] / 1000)
        if CURVE["tempo_km"]:
            return float(CURVE["tempo_km"])
        return CURVE["ref_km"] * 2 ** _mean_x(CURVE["tempo_cal_km"])
    if c.get("window_s") and c.get("gap_speed"):
        return float(c["window_s"] * c["gap_speed"] / 1000)
    return None


def smooth_curve(obs, day0, day1, q_theta=None, q_slope=None, slope0=0.0, slope_sd=None, prior_sd=None, iters=5,
                 revert=None):
    """2-state (theta, slope) robust Kalman filter + RTS smoother on a daily grid; observation y = theta + x * slope.
    obs: list of (ordinal day, score, sigma, kind, x). Same robust scheme as `smooth_series` (weights from the online
    forecast on pass 1, then from the smoothed curve at each item's x). Returns (days, mean (n, 2), cov (n, 2, 2),
    weights per obs (sorted by day), forecasts [(mean, sd) of the curve at the item's x before it])."""
    q_t = CURVE["q_theta"] if q_theta is None else q_theta
    q_s = CURVE["q_slope"] if q_slope is None else q_slope
    s_sd = CURVE["slope_prior_sd"] if slope_sd is None else slope_sd
    revert = CURVE.get("slope_revert", False) if revert is None else revert
    # mean-reverting slope (AR(1) towards 0 with stationary sd = slope_sd): phi^2 * sd^2 + q = sd^2. phi = 1: random walk
    ph = math.sqrt(1 - q_s / s_sd ** 2) if revert and s_sd ** 2 > q_s else 1.0
    prior_sd = P["prior_sd"] if prior_sd is None else prior_sd
    n = day1 - day0 + 1
    obs = sorted(obs, key=lambda o: o[0])
    if not obs:
        return np.arange(day0, day1 + 1), np.full((n, 2), np.nan), np.full((n, 2, 2), np.nan), [], []
    good = [o[1] - o[4] * slope0 for o in obs if o[3] != "bound"] or [o[1] for o in obs]
    m0 = float(np.median(good[:5]))
    byday = collections.defaultdict(list)
    for j, o in enumerate(obs):
        byday[o[0] - day0].append(j)
    w = np.ones(len(obs))
    fc0 = None
    for it in range(iters):
        mf = np.zeros((n, 2)); Pf = np.zeros((n, 3))   # filtered cov as (p11, p12, p22)
        a, b = m0, slope0
        p11, p12, p22 = prior_sd ** 2, 0.0, s_sd ** 2
        fc = [None] * len(obs)
        for k in range(n):
            if k:
                b *= ph
                p11 += q_t; p12 *= ph; p22 = ph * ph * p22 + q_s
            for j in byday.get(k, ()):
                y, s, kind, x = obs[j][1], obs[j][2], obs[j][3], obs[j][4]
                pred = a + x * b
                vx = p11 + 2 * x * p12 + x * x * p22
                fc[j] = (pred, math.sqrt(vx))
                if it == 0:
                    w[j] = _weight(y - pred, math.sqrt(s * s + vx), kind)
                S = vx + s * s / max(w[j], 1e-6)
                c1, c2 = p11 + x * p12, p12 + x * p22
                k1, k2 = c1 / S, c2 / S
                r = y - pred
                a, b = a + k1 * r, b + k2 * r
                p11, p12, p22 = p11 - k1 * c1, p12 - k1 * c2, p22 - k2 * c2
            mf[k] = (a, b); Pf[k] = (p11, p12, p22)
        # RTS, transition F = diag(1, ph): predicted cov of k+1 = F Pf_k F' + Q, gain C = Pf_k F' (pred cov)^-1
        ms = mf.copy(); Ps = Pf.copy()
        for k in range(n - 2, -1, -1):
            f11, f12, f22 = Pf[k]
            q11, q12, q22 = f11 + q_t, ph * f12, ph * ph * f22 + q_s
            det = q11 * q22 - q12 * q12
            i11, i12, i22 = q22 / det, -q12 / det, q11 / det
            g11, g12, g21, g22 = f11, ph * f12, f12, ph * f22          # Pf F'
            C11 = g11 * i11 + g12 * i12; C12 = g11 * i12 + g12 * i22
            C21 = g21 * i11 + g22 * i12; C22 = g21 * i12 + g22 * i22
            d0_, d1_ = ms[k + 1, 0] - mf[k, 0], ms[k + 1, 1] - ph * mf[k, 1]
            ms[k, 0] = mf[k, 0] + C11 * d0_ + C12 * d1_
            ms[k, 1] = mf[k, 1] + C21 * d0_ + C22 * d1_
            e11, e12, e22 = Ps[k + 1, 0] - q11, Ps[k + 1, 1] - q12, Ps[k + 1, 2] - q22
            # C E Cᵀ
            t11 = C11 * e11 + C12 * e12; t12 = C11 * e12 + C12 * e22
            t21 = C21 * e11 + C22 * e12; t22 = C21 * e12 + C22 * e22
            Ps[k] = (f11 + t11 * C11 + t12 * C12, f12 + t11 * C21 + t12 * C22, f22 + t21 * C21 + t22 * C22)
        if it == 0:
            fc0 = fc
        neww = np.array([_weight(o[1] - (ms[o[0] - day0, 0] + o[4] * ms[o[0] - day0, 1]), o[2], o[3]) for o in obs])
        if it and np.allclose(neww, w):
            break
        w = neww
    cov = np.stack([np.stack([Ps[:, 0], Ps[:, 1]], 1), np.stack([Ps[:, 1], Ps[:, 2]], 1)], 1)
    return np.arange(day0, day1 + 1), ms, cov, w.tolist(), fc0


def curve_at(mean, cov, x):
    """Curve value and state sd at x (arrays over days or one day)."""
    mean = np.asarray(mean, float); cov = np.asarray(cov, float)
    m = mean[..., 0] + x * mean[..., 1]
    v = cov[..., 0, 0] + 2 * x * cov[..., 0, 1] + x * x * cov[..., 1, 1]
    return m, np.sqrt(np.maximum(v, 0))


def curve_predict(theta, slope, var_theta, var_slope, cov, km, evidence_km=None, race_sd=None, extrap_sd=None, flat=False):
    """Race prediction at `km` from one day's curve state: {km, x, vdot, sd_state, sd (state ⊕ race-day noise ⊕
    extrapolation), doublings_outside, extrapolated}. Outside the evidenced range [min, max] km the sd grows by
    `extrap_sd` per doubling. Beyond the longest evidence a positive slope stops (VDOT held, e.g. marathon =
    half VDOT): carrying it further would predict a marathon VDOT above the half. A negative slope keeps applying.
    flat=True (distance curve switched off): slope 0, i.e. the Daniels equivalents of theta (the 5 km VDOT)."""
    if flat:
        slope, var_slope, cov = 0.0, 0.0, 0.0
    race_sd = CURVE["race_sd"] if race_sd is None else race_sd
    extrap_sd = CURVE["extrap_sd_per_doubling"] if extrap_sd is None else extrap_sd
    x = curve_x(km)
    xm = x                                   # x the mean is read at
    out = 0.0
    if evidence_km:
        lo, hi = curve_x(evidence_km[0]), curve_x(evidence_km[1])
        out = max(lo - x, x - hi, 0.0)
        if slope > 0:                        # beyond the longest evidence no further gain (a negative slope still applies)
            xm = min(x, hi)
    m = theta + xm * slope
    vs = max(var_theta + 2 * xm * cov + xm * xm * var_slope, 0.0)
    sd = math.sqrt(vs + race_sd ** 2 + (extrap_sd * out) ** 2)
    return {"km": km, "x": x, "vdot": m, "sd_state": math.sqrt(vs), "sd": sd,
            "doublings_outside": out, "extrapolated": out > 0.05}


def dist_label(km):
    for k, nm in DIST_LABEL.items():
        if abs(km - k) < 1e-3:
            return nm
    return f"{km:g} km"


def slope_text(slope):
    a = abs(slope)
    if a < 0.3:
        return f"{slope:+.1f} VDOT per doubling of distance: about equally strong at short and long races"
    side = "longer" if slope > 0 else "shorter"
    return f"{slope:+.1f} VDOT per doubling of distance: relatively stronger at {side} races"


def curve_obs(ev):
    """(items, obs) of the running evidence that feeds the distance curve; obs = (day, score, sigma, kind, x)."""
    items, obs = [], []
    for e in ev:
        if e["discipline"] not in ("interval", "long") or e["kind"] == "implausible":
            continue
        km = evidence_km(e)
        if km is None or km <= 0:
            continue
        e["km"] = km
        items.append(e)
        obs.append((e["day"], e["score"], e["sigma"], e["kind"], curve_x(km)))
    return items, obs


# ------------------------------------------------------------------ pipeline
def load_features(person, workers=8, progress=True):
    """(activities from groups.json, {id: features}) with a per-FIT cache."""
    D = ROOT / "data" / person
    acts = json.loads((D / "groups.json").read_text())["activities"]
    fits = {p.stem: p for p in (D / "activities").glob("*/*.fit")}
    rest = hr_rest_series(D)
    cfile = D / "cache" / "fitness_model.pkl"
    cfile.parent.mkdir(exist_ok=True)
    cache = pickle.loads(cfile.read_bytes()) if cfile.exists() else {}
    ck = (FEATURE_VERSION, sg.READER_VERSION, json.dumps({k: v for k, v in P.items() if k != "hr_intent"}, sort_keys=True, default=str))
    if cache.get("_v") != ck:
        cache = {"_v": ck}
    key = lambda p: (p.stat().st_mtime_ns, p.stat().st_size)
    jobs, feats = [], {}
    for i, a in acts.items():
        if a["sport"] not in ("run", "ski") or i not in fits:
            continue
        c = cache.get(i)
        hr0 = rest.get(a["date"], P["hr_rest_default"])
        if c and c["key"] == key(fits[i]) and c.get("hr_rest") == hr0 and c.get("indoor") == a.get("indoor_bislett"):
            feats[i] = c["f"]
        else:
            jobs.append((str(fits[i]), {k: v for k, v in a.items() if k != "similar"}, hr0))
    if jobs:
        if workers > 1 and len(jobs) > 4:
            with Pool(workers) as pool:
                res = pool.map(_feat_job, jobs, chunksize=4)
        else:
            res = [_feat_job(j) for j in jobs]
        for (fit, a, hr0), (_, f) in zip(jobs, res):
            i = Path(fit).stem
            feats[i] = f
            cache[i] = {"key": key(Path(fit)), "hr_rest": hr0, "f": f, "indoor": a.get("indoor_bislett")}
        cfile.write_bytes(pickle.dumps(cache))
    if progress:
        print(f"features: {len(feats)} sessions ({len(jobs)} recomputed)")
    return acts, feats, rest


def collect_evidence(acts, feats, **kw):
    ev = []
    for i, f in feats.items():
        a = acts[i]
        for e in score_session(a, f, **kw):
            e.update(id=i, date=a["date"], day=dt.date.fromisoformat(a["date"]).toordinal(), type=a["type"])
            ev.append(e)
    ev.sort(key=lambda e: (e["day"], e["id"]))
    return ev


def _obs(ev, disc):
    return [(e["day"], e["score"], e["sigma"], e["kind"]) for e in ev if e["discipline"] == disc and e["kind"] != "implausible"]


def cross_check(ev, interval_series):
    """Long-distance evidence far above the interval shape of the same day is physically implausible (a runner's
    VDOT over 20-60 min doesn't exceed the 5k-ish VDOT by much): mark it `implausible` (e.g. a ski session saved
    as a run, 2026-01-20: 30 min at 3:09/km)."""
    days, m, s, _ = interval_series
    for e in ev:
        if e["discipline"] != "long" or e["kind"] == "race":
            continue
        k = int(np.clip(e["day"] - days[0], 0, len(days) - 1))
        lim = m[k] + P["cross_check"] + 2 * math.sqrt(s[k] ** 2 + e["sigma"] ** 2)
        if e["score"] > lim:
            e["kind"] = "implausible"
            e["components"]["implausible"] = f"> interval shape {m[k]:.1f} + {lim - m[k]:.1f}"


def is_10k_dup(e):
    """The interval/hard copy of a 10k race (kept by collect_evidence for the per-discipline back-tests; the published
    model counts a 10k once, as a long race on the distance curve)."""
    return e["discipline"] == "interval" and e["type"] == "race?" and "race" in e["components"] and e["kind"] != "race"


def _model(ev, today):
    """Shape per discipline + overall + the distance-curve predictor from evidence items (sets weight / shape_at /
    km on them). Running (interval, long) comes from one coupled distance curve (`smooth_curve`): interval = the curve
    at 5 km (theta), long = the curve at the 10k-half reference. Ski: its own random-walk smoother.
    Returns (disciplines, predictor block or None, disciplines with the distance curve off: long = theta and race
    equivalents / race prediction = Daniels equivalents of theta; the same fit, only the read-out differs)."""
    t1 = today.toordinal()
    series, best, curve_sub = {}, {}, None
    # 1) separate interval smoother: only for the cross-check of long items (implausibly fast for this runner)
    iobs = _obs(ev, "interval")
    if iobs:
        d0 = min(o[0] for o in iobs) - 7
        days, m, s, _, _ = smooth_series(iobs, d0, t1, P["q_per_day"]["interval"])
        cross_check(ev, (days, m, s, None))
    # 2) coupled distance curve on every running item
    items, cobs = curve_obs(ev)
    pred_block, series_off, best_off = None, {}, {}
    if cobs:
        d0 = min(o[0] for o in cobs) - 7
        days, M, C, w, _ = smooth_curve(cobs, d0, t1)
        for e, wi, o in zip(items, w, sorted(cobs, key=lambda o: o[0])):
            k = e["day"] - d0
            e["weight"] = round(float(wi), 3)
            e["shape_at"] = round(float(M[k, 0] + o[4] * M[k, 1]), 2)   # the curve at the item's own distance
        x_long = _mean_x(CURVE["long_ref_km"])
        for disc, x in (("interval", 0.0), ("long", x_long)):
            m, s = curve_at(M, C, x)
            sub = [e for e in items if e["discipline"] == disc]
            series[disc] = (days, m, s, sub)
            best[disc] = float(m[s <= np.median(s)].max())
        series_off = {**series, "long": (*series["interval"][:3], series["long"][3])}
        best_off = {**best, "long": best["interval"]}
        curve_sub = items
        pred_block = _predictor_block(days, M, C, items, t1)
    # 3) ski
    sub = [e for e in ev if e["discipline"] == "ski" and e["kind"] != "implausible"]
    if sub:
        obs = _obs(ev, "ski")
        d0 = min(o[0] for o in obs) - 7
        days, m, s, w, _ = smooth_series(obs, d0, t1, P["q_per_day"]["ski"])
        for e, wi in zip(sorted(sub, key=lambda e: e["day"]), w):
            e["weight"] = round(float(wi), 3)
            e["shape_at"] = round(float(m[e["day"] - d0]), 2)
        series["ski"] = (days, m, s, sub)
        best["ski"] = float(m[s <= np.median(s)].max())
    if not series:
        return {}, None, {}
    out = _assemble(series, best, pred_block, curve_sub, today, False)
    out_off = _assemble({**series, **series_off}, {**best, **best_off}, pred_block, curve_sub, today, True) if pred_block else out
    return out, pred_block, out_off


def _assemble(series, best, pred_block, curve_sub, today, flat):
    """disciplines + overall from the smoothed series (`_model`); flat: the distance curve switched off."""
    t1 = today.toordinal()
    # overall: precision-weighted mean of % of personal best over disciplines with independent evidence. Interval
    # and long are two readings of one curve (same theta), so running enters once: the mean of their % of best,
    # with the sd of a fully correlated mean.
    start = min(v[0][0] for v in series.values())
    days_all = np.arange(start, t1 + 1)
    num = np.zeros(len(days_all)); den = np.zeros(len(days_all))
    parts = []
    if "interval" in series:
        days = series["interval"][0]
        pi, si = [100 * series[d][1] / best[d] for d in ("interval", "long")], [100 * series[d][2] / best[d] for d in ("interval", "long")]
        parts.append((days, (pi[0] + pi[1]) / 2, (si[0] + si[1]) / 2))
    if "ski" in series:
        days, m, s, _ = series["ski"]
        parts.append((days, 100 * m / best["ski"], 100 * s / best["ski"]))
    for days, pct, psd in parts:
        off = days[0] - start
        wgt = 1 / psd**2
        num[off:] += wgt * pct; den[off:] += wgt
    ov_m, ov_s = num / den, 1 / np.sqrt(den)

    def weekly(days, m, s, sub=None):
        out = []
        for k in range(len(days) - 1, -1, -7):  # weeks ending on `today`'s weekday, newest first
            j = k
            row = {"date": dt.date.fromordinal(int(days[j])).isoformat(), "mean": round(float(m[j]), 2),
                   "sd": round(float(s[j]), 2)}
            if sub is not None:
                n = sum(1 for e in sub if days[j] - 7 < e["day"] <= days[j] and e["kind"] != "bound")
                last = max((e["day"] for e in sub if e["day"] <= days[j] and e["weight"] > 0.5), default=None)
                row["n"] = n
                row["days_since_evidence"] = int(days[j] - last) if last else None
            out.append(row)
        return out[::-1]

    def conf(sd):
        return "high" if sd < 1.0 else "medium" if sd < 2.0 else "low"

    def last_ev(sub):
        return max((e["day"] for e in sub if e["kind"] in ("race", "hard") and (e.get("weight") or 0) > 0.5), default=None)

    disc_out = {}
    for disc in DISCIPLINES:
        if disc not in series:
            continue
        days, m, s, sub = series[disc]
        cur_m, cur_s = float(m[-1]), float(s[-1])
        eq = {}
        if disc in ("interval", "long") and pred_block:
            # race equivalents from the curve at each distance (the same numbers as the race prediction)
            eq = {nm: pred_block["_now_flat" if flat else "_now"][nm]["time_s"] for nm in ("5k", "10k", "half", "marathon")}
        yearly = {}
        for y in sorted({dt.date.fromordinal(int(d)).year for d in days}):
            mk = np.array([dt.date.fromordinal(int(d)).year == y for d in days])
            sub_y = [e for e in sub if e["date"][:4] == str(y) and e["kind"] != "bound"]
            obs_mk = mk & (s < 2.0)
            yearly[str(y)] = {"mean": round(float(m[mk].mean()), 1), "max": round(float(m[mk].max()), 1),
                              "sd_median": round(float(np.median(s[mk])), 2), "n_evidence": len(sub_y),
                              "mean_well_observed": round(float(m[obs_mk].mean()), 1) if obs_mk.sum() >= 14 else None,
                              "days_well_observed": int(obs_mk.sum())}
        # staleness: the latest race / hard evidence that counted (weight > 0.5). For interval / long that is any
        # item on the distance curve (they share theta); long also reports its own latest item (10k+ race / tempo)
        last = last_ev(curve_sub if disc in ("interval", "long") else sub)
        dse = int(t1 - last) if last is not None else None
        cur = {"date": today.isoformat(), "mean": round(cur_m, 2), "sd": round(cur_s, 2),
               "confidence": conf(cur_s), "pct_of_best": round(100 * cur_m / best[disc], 1),
               "race_equivalents_s": eq or None,
               "last_evidence_date": dt.date.fromordinal(last).isoformat() if last is not None else None,
               "days_since_evidence": dse, "stale": dse is None or dse > STALE_DAYS}
        if disc == "long":
            own = last_ev(sub)
            cur["last_distance_evidence_date"] = dt.date.fromordinal(own).isoformat() if own is not None else None
            cur["ref_km"] = CURVE["ref_km"] if flat else round(CURVE["ref_km"] * 2 ** _mean_x(CURVE["long_ref_km"]), 2)
        disc_out[disc] = {
            "label": LABEL[disc], "unit": "VDOT" if disc != "ski" else "VDOT-equivalent (ml/kg/min)",
            "q_per_day": P["q_per_day"][disc] if disc == "ski" else CURVE["q_theta"],
            "model": "distance_curve" if disc != "ski" else "random_walk",
            **({"distance_curve": not flat} if disc != "ski" else {}),
            "personal_best": round(best[disc], 2),
            "current": cur,
            "yearly": yearly,
            "weekly": weekly(days, m, s, sub),
        }
    pred = race_prediction(pred_block, flat)
    disc_out["overall"] = {
        "label": LABEL["overall"], "unit": "% of personal best (precision-weighted over disciplines)",
        "current": {"date": today.isoformat(), "mean": round(float(ov_m[-1]), 1), "sd": round(float(ov_s[-1]), 1),
                    # the running race prediction from the distance curve (race_prediction)
                    "race_prediction": pred, "race_equivalents_s": pred["race_equivalents_s"] if pred else None},
        "yearly": {str(y): round(float(ov_m[np.array([dt.date.fromordinal(int(d)).year == y for d in days_all])].mean()), 1)
                   for y in sorted({dt.date.fromordinal(int(d)).year for d in days_all})},
        "weekly": weekly(days_all, ov_m, ov_s),
    }
    return disc_out


PRED_DIST = [(5000, "5k"), (10000, "10k"), (21097.5, "half"), (42195, "marathon")]


def _predictor_block(days, M, C, items, t1):
    """fitness.json `predictor`: daily RTS-smoothed curve state from the first evidence to today + the evidence."""
    first = min(e["day"] for e in items)
    k0 = int(first - days[0])
    strong = [e for e in items if e["kind"] in ("race", "hard") and (e.get("weight") or 0) > 0.5]
    ekm = [min(e["km"] for e in strong), max(e["km"] for e in strong)] if strong else [CURVE["ref_km"]] * 2
    daily = [{"date": dt.date.fromordinal(int(days[k])).isoformat(), "theta": round(float(M[k, 0]), 3),
              "slope": round(float(M[k, 1]), 3), "var_theta": round(float(C[k, 0, 0]), 4),
              "var_slope": round(float(C[k, 1, 1]), 5), "cov": round(float(C[k, 0, 1]), 5)} for k in range(k0, len(days))]
    evid = [{"date": e["date"], "id": int(e["id"]), "km": round(e["km"], 3), "vdot": round(e["score"], 2),
             "kind": e["kind"], "weight": e.get("weight"), "discipline": e["discipline"], "sigma": e["sigma"]}
            for e in items if e["kind"] != "bound" or (e.get("weight") or 0) >= 0.05]
    last = max((e["day"] for e in strong), default=None)
    t = daily[-1]
    now, now_flat = {}, {}
    for dist, nm in PRED_DIST:
        for flat, tgt in ((False, now), (True, now_flat)):
            p = curve_predict(t["theta"], t["slope"], t["var_theta"], t["var_slope"], t["cov"], dist / 1000, ekm, flat=flat)
            tgt[nm] = {**p, "time_s": round(race_time(p["vdot"], dist)),
                       "range_s": [round(race_time(p["vdot"] + p["sd"], dist)), round(race_time(p["vdot"] - p["sd"], dist))]}
    return {"ref_km": CURVE["ref_km"], "x": "log2(km/5)", "q": {"theta": CURVE["q_theta"], "slope": CURVE["q_slope"]},
            "slope_prior_sd": CURVE["slope_prior_sd"], "slope_revert": CURVE["slope_revert"], "race_sd": CURVE["race_sd"],
            "evidence_km": [round(ekm[0], 3), round(ekm[1], 3)],
            "extrap_sd_per_doubling": CURVE["extrap_sd_per_doubling"],
            "x_mapping": {"race": "nominal distance", "interval": f"{CURVE['interval_km']:g} km (scores calibrated to 5k races)",
                          "tempo": f"{CURVE['ref_km'] * 2 ** _mean_x(CURVE['tempo_cal_km']):.2f} km (10k-half mean: the tempo offset's calibration)"
                          if not CURVE["tempo_km"] else f"{CURVE['tempo_km']}",
                          "bound": "grade-adjusted distance of the scored window"},
            "long_ref_km": round(CURVE["ref_km"] * 2 ** _mean_x(CURVE["long_ref_km"]), 3),
            "last_evidence_date": dt.date.fromordinal(last).isoformat() if last is not None else None,
            "daily": daily, "evidence": evid, "_now": now, "_now_flat": now_flat}


def race_prediction(block, flat=False):
    """The running race prediction from the distance curve today (None without running evidence; flat: the curve
    switched off, slope 0 = Daniels equivalents of theta):
    {vdot (at 10k), sd (state ⊕ race-day noise, at 10k), vdot_by_distance, sd_by_distance, extrapolated (per
    distance), race_equivalents_s per distance from the curve, range_10k_s [fast, slow] (±1 sd), weights {} (kept for
    the old schema: the interval / long blend it described is gone), model "distance_curve"}."""
    if not block:
        return None
    now = block["_now_flat" if flat else "_now"]
    t = block["daily"][-1]
    p10 = now["10k"]
    return {"vdot": round(p10["vdot"], 2), "sd": round(p10["sd"], 2), "weights": {}, "model": "distance_curve",
            "distance_curve": not flat, "theta": t["theta"], "slope": 0.0 if flat else t["slope"],
            "vdot_by_distance": {nm: round(p["vdot"], 2) for nm, p in now.items()},
            "sd_by_distance": {nm: round(p["sd"], 2) for nm, p in now.items()},
            "extrapolated": {nm: p["extrapolated"] for nm, p in now.items()},
            "race_equivalents_s": {nm: p["time_s"] for nm, p in now.items()},
            "ranges_s": {nm: p["range_s"] for nm, p in now.items()},
            "range_10k_s": p10["range_s"]}


def build(person, workers=8, today=None, progress=True):
    """The whole fitness doc (what fitness.json holds): variant "pace" in `disciplines` / `sessions`, variant "hr"
    in `variants.hr` and `sessions[].hr`."""
    acts, feats, rest = load_features(person, workers, progress)
    ev = [e for e in collect_evidence(acts, feats) if not is_10k_dup(e)]   # a 10k counts once (long race)
    ev_hr = apply_hr_intent(ev)          # copies, before cross_check marks the pace list
    today = today or dt.date.today()
    disc_out, predictor, disc_off = _model(ev, today)
    disc_hr, _, disc_hr_off = _model(ev_hr, today)
    if predictor:
        predictor.pop("_now", None); predictor.pop("_now_flat", None)
    hr_by = {(e["id"], e["discipline"]): e for e in ev_hr}
    sessions = []
    for e in ev:
        sessions.append({"id": int(e["id"]), "date": e["date"], "discipline": e["discipline"], "type": e["type"],
                         "score": round(e["score"], 2), "kind": e["kind"], "sigma": e["sigma"],
                         "weight": e.get("weight"), "shape_at": e.get("shape_at"),
                         **({"km": round(e["km"], 3)} if e.get("km") else {}),
                         "confidence": round(float(e.get("weight") or 0) * (SIG["race"] / e["sigma"]) ** 2, 3),
                         "components": _clean(e["components"])})
        h = hr_by.get((e["id"], e["discipline"]))
        if h is not None:
            sessions[-1]["hr"] = {"kind": h["kind"], "sigma": round(h["sigma"], 2), "weight": h.get("weight"),
                                  "shape_at": h.get("shape_at"),
                                  "p_max": round(h["components"]["p_max"], 3) if "p_max" in h["components"] else None,
                                  "note": h["components"].get("hr_intent")}
    hr_diag = {}
    for i, f in feats.items():
        ha = f.get("hr_aer") or {}
        if ha.get("ef") or ha.get("vdot"):
            hr_diag[i] = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in ha.items()}
    return {"built": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "model_version": MODEL_VERSION,
            "params": P, "curve": CURVE, "sigma": SIG, "scale": "Daniels VDOT; race_time(vdot, dist) gives equivalent race times",
            "hr_used_in_score": False, "disciplines": disc_out,
            "variants": {"pace": {"label": VARIANTS["pace"], "hr_used_in_score": False},
                         "hr": {"label": VARIANTS["hr"], "hr_used_in_score": True, "disciplines": disc_hr}},
            # the same model read with the distance curve switched off (per-person setting distance_curve = false,
            # data/<person>/settings.json): the server swaps these in for `disciplines` / `variants.hr.disciplines`
            "curve_off": {"label": "Distance curve off: same VDOT at every distance (Daniels equivalents of the 5 km shape)",
                          "disciplines": disc_off, "hr_disciplines": disc_hr_off},
            "predictor": predictor, "sessions": sessions, "hr_diagnostics": hr_diag}


def _clean(d):
    out = {}
    for k, v in d.items():
        if v is None:
            continue
        out[k] = round(v, 3) if isinstance(v, float) else v
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("person")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--eval", action="store_true", help="run the back-tests (docs/fitness-model.md)")
    args = ap.parse_args()
    T0 = time.time()
    doc = build(args.person, args.workers)
    out = ROOT / "data" / args.person / "fitness.json"
    js = json.dumps(doc, ensure_ascii=False, separators=(",", ":"), allow_nan=False, default=float)
    out.write_text(js)
    print(f"wrote {out} ({len(js) / 1e6:.2f} MB) {time.time() - T0:.1f}s")
    for disc, d in doc["disciplines"].items():
        c = d["current"]
        print(f"  {disc:9s} now {c['mean']:.1f} ± {c['sd']:.1f}  yearly " +
              " ".join(f"{y}:{(v['mean'] if isinstance(v, dict) else v)}" for y, v in d["yearly"].items()))
    if args.eval:
        import fitness_eval  # noqa: F401  (scripts/fitness_eval.py)
        fitness_eval.run(args.person)


if __name__ == "__main__":
    main()

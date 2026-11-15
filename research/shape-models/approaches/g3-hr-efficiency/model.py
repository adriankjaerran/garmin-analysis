"""G3: HR-pace efficiency of easy / steady runs as a fitness signal, device-corrected, calibrated time-forward.

Per split (features.py): VO2 cost of the GAP speed, %HRR, minutes in, |grade|. Per split VDOT-equivalent
    x = 3.5 + (vo2 + d*(mid_min - 20) + g*|grade|% - b*(hrr - h0) - 3.5) / h0
i.e. the O2 cost the runner would run at h0 %HRR (b = ml/kg/min per unit HRR; Swain's line through rest would be
b = VO2max - 3.5 ~ 50; the observed cross-run slope is 15-30 because HR noise dilutes it), corrected for cardiac drift
(d) and for the HR cost of hills that GAP misses (g); d and g (and b when "fit") come from an OLS with quarter fixed
effects on the person's own splits before the target day. Run value = mean of its splits.

Calibration (all on history, day < target day):
  cal="prod": each run is compared with the production curve's theta (5 km VDOT) as known *before that run's day*
      (harness._prod_state); per device: offset c_dev = robust mean of (x - theta) over the last `cal_days`, shrunk to
      the person's pooled offset (k runs), run noise sd_dev = robust SD of the same residuals. -> y = x - c_dev is on
      the theta scale; a robust local-level Kalman filter gives theta_hr(t) with sd.
  cal="none": as "targets" without the target offset (population Swain line: patrik's only race has no prior
      target, so this is the only HR-only option there).
  cal="targets": device offsets from the HR data alone (each run vs the median of the other devices' runs within
      +-60 d; only identifiable when devices interleave, e.g. rise's strap), then one offset from past targets
      (vdot_target - theta_hr_raw(target day)). Production is not used at all.
Predictions:
  hr_alone      theta_hr (+ production slope * log2(km/5) when curve=True)
  hr_blend      inverse-variance mean of production_curve and hr_alone (hr variance x `w_inflate`)
  hr_stream     production smooth_curve with every run added as an observation (y, sigma_dev * s_mult, x = 0)
"""
from __future__ import annotations

import functools
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
import common as CM  # noqa: E402
import harness as H  # noqa: E402

DEFAULT = dict(b=35.0, h0=0.70, ref_min=20.0, drift="fit", grade="fit", src=("easy",), cal="prod", cal_days=365,
               k_shrink=10.0, q=0.01, robust_z=2.5, model_sd=1.5, curve=True, w_inflate=1.0, s_mult=1.0,
               sd_floor=2.0, min_runs=5, max_age=60, devices="all", stream_kind="race")
D_DEFAULT, G_DEFAULT = 0.04, 0.9


@functools.lru_cache(maxsize=1)
def splits():
    p = CM.OUT / "g3" / "splits.parquet"
    if not p.exists():
        import features
        features.main()
    k = pd.read_parquet(p)
    k["ga100"] = k.grade_abs_mean * 100
    k["q"] = pd.to_datetime(k.date).dt.to_period("Q").astype(str)
    return k


def _cfg(**kw):
    c = dict(DEFAULT); c.update(kw)
    return c


def _key(c):
    return tuple(sorted((k, tuple(v) if isinstance(v, list) else v) for k, v in c.items()))


@functools.lru_cache(maxsize=100000)
def theta_prod(person, day):
    st = H._prod_state(person, int(day))
    if st is None:
        return None
    m, cv, ekm = st
    return float(m[0]), float(m[1]), float(cv[0, 0])


def fit_corrections(s, c):
    """d (drift, ml per min), g (ml per % |grade|), b (ml per unit HRR) from vo2 ~ quarter FE + hrr + mid + ga."""
    d, g, b = D_DEFAULT, G_DEFAULT, c["b"]
    need_fit = c["drift"] == "fit" or c["grade"] == "fit" or c["b"] == "fit"
    if need_fit and len(s) >= 150:
        Q = pd.get_dummies(s.q, dtype=float).to_numpy()
        X = np.column_stack([Q, s.hrr.to_numpy(), s.mid_min.to_numpy(), s.ga100.to_numpy()])
        beta, *_ = np.linalg.lstsq(X, s.vo2.to_numpy(), rcond=None)
        bh, dm, ga = beta[-3:]
        if c["drift"] == "fit":
            d = float(np.clip(-dm, 0.0, 0.12))
        if c["grade"] == "fit":
            g = float(np.clip(-ga, 0.0, 2.0))
        if c["b"] == "fit":
            b = float(np.clip(bh, 10.0, 60.0))
    if c["drift"] == "none":
        d = 0.0
    if c["grade"] == "none":
        g = 0.0
    return d, g, (b if b != "fit" else 25.0)


def _robust_mean_sd(r):
    r = np.asarray(r, float)
    if len(r) == 0:
        return 0.0, None
    med = float(np.median(r))
    mad = float(np.median(np.abs(r - med))) * 1.4826
    sd = max(mad, 0.5)
    z = (r - med) / sd
    w = np.minimum(1.0, 2 / np.maximum(np.abs(z), 1e-9))   # Huber-ish mean
    return float(np.sum(w * r) / np.sum(w)), sd


def run_values(person, day, c):
    s = splits()
    s = s[(s.person == person) & (s.day < day) & s.src.isin(c["src"])]
    if c["devices"] != "all":
        s = s[s.hr_device.isin(c["devices"])]
    if s.empty:
        return None, None
    d, g, b = fit_corrections(s, c)
    h0 = c["h0"]
    x = 3.5 + (s.vo2 + d * (s.mid_min - c["ref_min"]) + g * s.ga100 - b * (s.hrr - h0) - 3.5) / h0
    s = s.assign(x=x)
    R = s.groupby("activity_id").agg(day=("day", "first"), x=("x", "mean"), n=("x", "size"), dev=("hr_device", "first"),
                                     trust=("trust_steady", "first")).reset_index().sort_values("day")
    return R, (d, g, b)


def kalman(days, y, sd, upto, q, z=2.5, sd0=10.0, query=()):
    """Robust local-level filter. Returns (mean, var) at day `upto` (after all obs < upto) and, for each day in
    `query`, the filtered (mean, var) using obs strictly before that day."""
    order = np.argsort(days, kind="stable")
    days, y, sd = np.asarray(days)[order], np.asarray(y)[order], np.asarray(sd)[order]
    qs = sorted(set(int(t) for t in query))
    out = {}
    m, v, last = None, None, None
    qi = 0
    for i in range(len(days)):
        while qi < len(qs) and qs[qi] <= days[i]:
            out[qs[qi]] = (m, None if v is None else v + q * (qs[qi] - last)); qi += 1
        if m is None:
            m, v, last = float(y[i]), sd0 ** 2, days[i]
            # first obs: initialise around it with a wide prior
            v = 1.0 / (1 / sd0 ** 2 + 1 / sd[i] ** 2)
            continue
        v = v + q * (days[i] - last); last = days[i]
        S = v + sd[i] ** 2
        r = y[i] - m
        zz = abs(r) / math.sqrt(S)
        w = 1.0 if zz <= z else (z / zz) ** 2
        S = v + sd[i] ** 2 / w
        K = v / S
        m, v = m + K * r, (1 - K) * v
    while qi < len(qs):
        out[qs[qi]] = (m, None if v is None else v + q * (qs[qi] - last)); qi += 1
    if m is None:
        return None, out
    return (m, v + q * (upto - 1 - last)), out


@functools.lru_cache(maxsize=20000)
def hr_state(person, day, ckey):
    c = dict(ckey)
    R, corr = run_values(person, day, c)
    if R is None or len(R) < c["min_runs"]:
        return None
    if c["cal"] == "prod":
        th = [theta_prod(person, dd) for dd in R.day]
        R["theta"] = [t[0] if t else np.nan for t in th]
        cal = R[R.theta.notna() & (R.day >= day - c["cal_days"])] if c["cal_days"] else R[R.theta.notna()]
        if len(cal) < c["min_runs"]:
            return None
        res = cal.x - cal.theta
        c_all, sd_all = _robust_mean_sd(res)
        offs, sds = {}, {}
        for dev in R.dev.unique():
            r = res[cal.dev == dev]
            n = len(r)
            cm, sdm = _robust_mean_sd(r) if n else (c_all, sd_all)
            offs[dev] = (n * cm + c["k_shrink"] * c_all) / (n + c["k_shrink"]) if n else c_all
            sds[dev] = max(((n * (sdm or sd_all) ** 2 + 5 * sd_all ** 2) / (n + 5)) ** 0.5, c["sd_floor"])
        R["y"] = R.x - R.dev.map(offs)
        R["sd"] = R.dev.map(sds)
        (m, v), _ = kalman(R.day.to_numpy(), R.y.to_numpy(), R.sd.to_numpy(), day, c["q"], c["robust_z"])
        last_day = int(R.day.max())
        return dict(theta=m, var=v, offs=offs, sds=sds, corr=corr, n=len(R), c_all=c_all, sd_all=sd_all,
                    last_day=last_day, runs=R)
    # cal == "targets": device offsets from interleaved runs, then one offset from past targets
    offs = {}
    devs = R.dev.value_counts()
    ref = devs.index[0]
    for dev in devs.index:
        if dev == ref:
            offs[dev] = 0.0; continue
        rr = []
        for r in R[R.dev == dev].itertuples():
            o = R[(R.dev == ref) & (abs(R.day - r.day) <= 60)]
            if len(o) >= 3:
                rr.append(r.x - o.x.median())
        n = len(rr)
        offs[dev] = (n * float(np.median(rr)) / (n + c["k_shrink"])) if n else 0.0
    R["y0"] = R.x - R.dev.map(offs)
    sd_all = _robust_mean_sd(R.y0 - R.y0.rolling(9, center=True, min_periods=3).median())[1] or 5.0
    sd_all = max(sd_all, c["sd_floor"])
    if c["cal"] == "none":     # population Swain line, no calibration at all (device offsets from interleaving only)
        (m, v), _ = kalman(R.day.to_numpy(), R.y0.to_numpy(), np.full(len(R), sd_all), day, c["q"], c["robust_z"])
        return dict(theta=m, var=v, offs=offs, sds={"all": sd_all}, corr=None, n=len(R), c_all=0.0, sd_all=sd_all,
                    last_day=int(R.day.max()), runs=R)
    T = H.history(person, day).targets
    (m, v), at = kalman(R.day.to_numpy(), R.y0.to_numpy(), np.full(len(R), sd_all), day, c["q"], c["robust_z"],
                         query=T.day.tolist())
    rows = [(t.vdot, at[int(t.day)][0], t.weight) for t in T.itertuples()
            if at.get(int(t.day)) and at[int(t.day)][0] is not None]
    if len(rows) < 2:
        return None
    rr = np.array([a - b for a, b, _ in rows]); ww = np.array([w for *_, w in rows])
    rr, ww = rr[-c.get("cal_n", 30):], ww[-c.get("cal_n", 30):]
    off = float(np.sum(rr * ww) / np.sum(ww))
    cal_sd = float(np.sqrt(np.sum(ww * (rr - off) ** 2) / np.sum(ww)))
    return dict(theta=m + off, var=v + cal_sd ** 2 / len(rr), offs=offs, sds={"all": sd_all}, corr=None, n=len(R),
                c_all=off, sd_all=sd_all, last_day=int(R.day.max()), runs=R, cal_sd=cal_sd)


def hr_alone(person, hist, target, **kw):
    c = _cfg(**kw)
    st = hr_state(person, hist.day, _key(c))
    if st is None or hist.day - st["last_day"] > c["max_age"]:
        return None
    th, var = st["theta"], st["var"]
    slope = 0.0
    if c["curve"] and c["cal"] == "prod":
        tp = theta_prod(person, hist.day)
        slope = tp[1] if tp else 0.0
    x = math.log2(float(target["km"]) / 5.0)
    return {"vdot": th + slope * x, "sd": math.sqrt(var + c["model_sd"] ** 2 + 1.0)}


def hr_blend(person, hist, target, **kw):
    c = _cfg(**kw)
    p = H.production_curve(person, hist, target)
    h = hr_alone(person, hist, target, **kw)
    if p is None:
        return h
    if h is None:
        return p
    wp = 1 / p["sd"] ** 2
    wh = 1 / (h["sd"] ** 2 * c["w_inflate"])
    v = (wp * p["vdot"] + wh * h["vdot"]) / (wp + wh)
    return {"vdot": v, "sd": math.sqrt(1 / (wp + wh))}


@functools.lru_cache(maxsize=4096)
def _stream_state(person, day, ckey):
    c = dict(ckey)
    fm = CM.import_production()
    a = H.history(person, day).acts
    a = a[a.prod_discipline.isin(["interval", "long"]) & a.prod_kind.isin(["hard", "race", "bound"]) &
          a.prod_km.notna() & a.prod_score.notna()]
    if a.empty:
        return None
    obs = [(int(r.day), float(r.prod_score), float(r.prod_sigma), r.prod_kind, fm.curve_x(float(r.prod_km)))
           for r in a.itertuples()]
    st = hr_state(person, day, ckey)
    if st is not None:
        R = st["runs"]
        obs += [(int(r.day), float(r.y), float(r.sd) * c["s_mult"], c["stream_kind"], 0.0) for r in R.itertuples()
                if r.day >= obs[0][0]]
    d0 = min(o[0] for o in obs)
    days, M, Cv, w, _ = fm.smooth_curve(obs, d0, day - 1)
    ev = [2 ** o[4] * fm.CURVE["ref_km"] for o in obs if o[3] in ("race", "hard") and o[2] < 3.0]
    ekm = (min(ev), max(ev)) if ev else None
    return M[-1], Cv[-1], ekm


def hr_stream(person, hist, target, **kw):
    c = _cfg(**kw)
    st = _stream_state(person, hist.day, _key(c))
    if st is None:
        return None
    fm = CM.import_production()
    m, cv, ekm = st
    p = fm.curve_predict(m[0], m[1], cv[0, 0], cv[1, 1], cv[0, 1], float(target["km"]), ekm)
    return {"vdot": float(p["vdot"]), "sd": float(p["sd"])}


def make(fn, name=None, **kw):
    f = functools.partial(fn, **kw)
    f.__name__ = name or fn.__name__
    return f


predict = make(hr_blend, "g3_hr_blend")       # G9 entry point


# ------------------------------------------------------------------ residual shift: prod + w * (hr - prod)
@functools.lru_cache(maxsize=100000)
def _pair(person, day, km, ckey):
    """(hr_alone, production) VDOT predictions at `km` from history before `day` (both time-forward)."""
    hist = H.history(person, day)
    tgt = {"km": km}
    h = hr_alone(person, hist, tgt, **dict(ckey))
    p = H.production_curve(person, hist, tgt)
    if h is None or p is None:
        return None
    return h["vdot"], p["vdot"], p["sd"]


def hr_shift(person, hist, target, w=None, lam=40.0, pool=True, w_max=0.6, **kw):
    """prod + w * (hr - prod). w fixed, or (w=None) learned time-forward: ridge slope of (actual - prod) on
    (hr - prod) over all past targets (this person; with pool=True also the other persons' targets before this
    day), shrunk to 0 by `lam` (VDOT^2), clipped to [0, w_max]."""
    c = _cfg(**kw)
    ck = _key(c)
    pr = _pair(person, hist.day, float(target["km"]), ck)
    if pr is None:
        return H.production_curve(person, hist, target)
    h, p, psd = pr
    if w is None:
        su = suv = 0.0
        for per in (CM.PERSONS if pool else (person,)):
            T = H.history(per, hist.day).targets
            for t in T.itertuples():
                q = _pair(per, int(t.day), float(t.km), ck)
                if q is None:
                    continue
                u, v = q[0] - q[1], t.vdot - q[1]
                su += t.weight * u * u; suv += t.weight * u * v
        w = float(np.clip(suv / (su + lam), 0.0, w_max))
    return {"vdot": p + w * (h - p), "sd": psd}

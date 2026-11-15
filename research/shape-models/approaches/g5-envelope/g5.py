"""G5: upper-envelope and censored ("Tobit") models of running fitness on best efforts.

Every run's best GAP effort is a *lower bound* on capacity; only some are near-maximal. Models here:
  * env_quantile  - time-decayed weighted high quantile of 5k-equivalent effort VDOT (upper envelope)
  * evt_gumbel    - Gumbel fit to (time-weighted) weekly maxima of 5k-equivalent effort VDOT
  * tobit         - censored state-space model on a 2-D grid (theta = 5 km fitness, slope = VDOT per doubling of
                    distance). Each effort says y = theta + slope*log2(km/5) - u + eps with u = 0 with probability
                    pi (maximal) and u ~ Uniform(0, U) otherwise ("fitness >= y"); pi from workout type, HR and the
                    race flag. Forward (causal) grid filter; no smoothing, read on the target day.

Run (repo root):  /tmp/shape-venv/bin/python research/shape-models/approaches/g5-envelope/g5.py [--quick]
All models only read `hist` (rows with day < target day).
"""
from __future__ import annotations

import functools
import json
import math
import sys
from dataclasses import dataclass, field, asdict, replace
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.special import ndtr

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
import common as C  # noqa: E402
import harness as H  # noqa: E402

OUTDIR = C.OUT / "g5"
DIST_BG = {3000.0: "3k", 5000.0: "5k", 10000.0: "10k", 21097.5: "HM"}

# ------------------------------------------------------------------ configuration


@dataclass(frozen=True)
class Cfg:
    # evidence streams
    use_bests: bool = True            # best GAP efforts of non-race runs (censored)
    use_intervals: bool = True        # production W'-balance interval session scores (prod_kind hard, 5 km equiv)
    use_races: bool = True            # official races (exact)
    min_m: float = 3000.0             # shortest best effort used (shorter ones are anaerobic / reps)
    gps_bias: float = 1.0             # GPS best efforts read this much VDOT high (race watch vs official: ~ +0.9)
    # censoring model
    U: float = 20.0                   # sub-maximal shortfall ~ Uniform(0, U) VDOT
    sig_best: float = 2.0             # noise of a maximal best effort
    sig_race: float = 1.0
    pi_race: float = 0.97             # P(official race is a maximal effort); else a lower bound like any run
    pi_scale: float = 1.0             # multiplies the workout pi table (sensitivity knob)
    pi_int: float = 0.7               # P(interval session score is maximal)
    U_int: float = 12.0
    hr_gate: bool = True              # HR (trusted device) raises / lowers pi
    p_out: float = 0.02               # upward-outlier probability (GPS, mislabelled) for bests
    # dynamics
    q_theta: float = 0.05             # VDOT^2 / day random walk (production)
    slope_sd: float = 1.5             # slope prior / stationary sd (production)
    slope_tau: float = 900.0          # AR(1) time constant of the slope (days)
    flat: bool = False                # slope fixed at 0 (Daniels equivalents)
    race_sd: float = 1.0              # race-day noise added to the predictive sd
    tempo_shortfall: bool = False     # hard_effort targets: subtract the person's typical shortfall of the kind


PI_WORKOUT = {"race": 0.95, "race_auto": 0.8, "time_trial": 0.6, "tempo": 0.35, "intervals": 0.03, "steady": 0.04,
              "long": 0.04, "easy": 0.01, "other": 0.04}

TH = np.arange(25.0, 80.01, 0.25)        # theta grid (5 km VDOT)
SL = np.arange(-4.0, 4.01, 0.25)         # slope grid (VDOT per doubling of distance)


# ------------------------------------------------------------------ evidence


def _pi_of(r, cfg):
    p = PI_WORKOUT.get(r.workout, 0.03) * cfg.pi_scale
    if cfg.hr_gate and r.hr_ok and (r.hr_cover or 0) >= 0.8 and r.trust_steady >= 0.8 and pd.notna(r.hrr_last3):
        if r.hrr_last3 >= 0.90:
            p = max(p, 0.5)
        elif r.hrr_last3 >= 0.86:
            p = max(p, 0.25)
        elif r.hrr_last3 < 0.75:
            p = min(p, 0.01)
    return float(min(p, 0.95))


def run_bests(hist, cfg):
    """One row per (non-race, pace_ok) run: its best GAP effort >= min_m with the highest VDOT."""
    s = hist.segs
    s = s[(s.seg_kind == "best_gap") & s.pace_ok & s.usable & ~s.suspect & (s.target_m >= cfg.min_m - 1)
          & (s.workout != "race")]
    if s.empty:
        return s.assign(pi=[], x=[], y=[])
    s = s.loc[s.groupby("activity_id").vdot.idxmax()]
    a = hist.acts.set_index("activity_id")
    s = s.join(a[["trust_steady", "hr_ok", "not_race"]].rename(columns={"hr_ok": "hr_ok_a"}), on="activity_id")
    s = s.assign(hr_ok=s.hr_ok_a.fillna(False), trust_steady=s.trust_steady.fillna(0.0))
    s = s.assign(x=np.log2(s.target_m / 5000.0), y=s.vdot - cfg.gps_bias)
    s["pi"] = [_pi_of(r, cfg) for r in s.itertuples()]
    return s


def observations(hist, cfg):
    """List of (day, y, x, sigma, pi, U, p_out) sorted by day."""
    obs = []
    a = hist.acts
    if cfg.use_races:
        r = a[(a.is_official_race == True) & a.race_vdot.notna()]  # noqa: E712
        for x in r.itertuples():
            obs.append((int(x.day), float(x.race_vdot), math.log2(float(x.race_km) / 5), cfg.sig_race, cfg.pi_race, cfg.U, 0.0))
    if cfg.use_intervals:
        i = a[(a.prod_discipline == "interval") & (a.prod_kind == "hard") & a.prod_score.notna()
              & (a.is_official_race != True)]  # noqa: E712
        for x in i.itertuples():
            obs.append((int(x.day), float(x.prod_score), 0.0, float(x.prod_sigma), cfg.pi_int, cfg.U_int, 0.0))
    if cfg.use_bests:
        b = run_bests(hist, cfg)
        for x in b.itertuples():
            obs.append((int(x.day), float(x.y), float(x.x), cfg.sig_best, float(x.pi), cfg.U, cfg.p_out))
    obs.sort(key=lambda o: o[0])
    return obs


# ------------------------------------------------------------------ censored grid filter


@functools.lru_cache(maxsize=256)
def _k_theta(dt, q):
    sd = math.sqrt(max(q * dt, 1e-9))
    d = TH[:, None] - TH[None, :]
    k = np.exp(-0.5 * (d / sd) ** 2)
    return k / k.sum(0, keepdims=True)          # column j: from TH[j]


@functools.lru_cache(maxsize=256)
def _k_slope(dt, sd0, tau):
    a = math.exp(-dt / tau)
    sd = max(sd0 * math.sqrt(1 - a * a), 0.02)
    d = SL[:, None] - a * SL[None, :]
    k = np.exp(-0.5 * (d / sd) ** 2)
    return k / k.sum(0, keepdims=True)


def _lik(y, x, sig, pi, U, p_out, slope_grid):
    mu = TH[:, None] + slope_grid[None, :] * x
    z = y - mu
    max_c = np.exp(-0.5 * (z / sig) ** 2) / (sig * math.sqrt(2 * math.pi))
    sub_c = (ndtr((z + U) / sig) - ndtr(z / sig)) / U
    L = pi * max_c + (1 - pi) * sub_c
    if p_out > 0:
        L = (1 - p_out) * L + p_out / 40.0      # flat outlier density over a ~40 VDOT range
    return L


def run_filter(obs, day_end, cfg):
    """Forward filter to `day_end` (exclusive of obs on/after it). Returns the posterior grid (TH x SL) or None."""
    if not obs:
        return None
    sg = np.zeros(1) if cfg.flat else SL
    ps = np.exp(-0.5 * (sg / cfg.slope_sd) ** 2) if not cfg.flat else np.ones(1)
    P = np.ones((len(TH), 1)) * (ps / ps.sum())[None, :]
    P /= P.sum()
    last = obs[0][0]
    for o in obs:
        d = o[0]
        if d >= day_end:
            break
        if d > last:
            P = _k_theta(d - last, cfg.q_theta) @ P
            if not cfg.flat:
                P = P @ _k_slope(d - last, cfg.slope_sd, cfg.slope_tau).T
            last = d
        P = P * _lik(o[1], o[2], o[3], o[4], o[5], o[6], sg)
        P /= P.sum()
    dt = day_end - 1 - last
    if dt > 0:
        P = _k_theta(dt, cfg.q_theta) @ P
        if not cfg.flat:
            P = P @ _k_slope(dt, cfg.slope_sd, cfg.slope_tau).T
    return P


_FCACHE = {}


def filter_state(person, day, cfg):
    key = (person, day, cfg)
    if key not in _FCACHE:
        h = H.history(person, day)
        _FCACHE[key] = run_filter(observations(h, cfg), day, cfg)
    return _FCACHE[key]


def read_curve(P, km, cfg):
    sg = np.zeros(1) if cfg.flat else SL
    mu = TH[:, None] + sg[None, :] * math.log2(km / 5.0)
    m = float((P * mu).sum())
    v = float((P * (mu - m) ** 2).sum())
    return m, math.sqrt(v)


def make_tobit(cfg=Cfg(), name=None):
    def f(person, hist, target):
        P = filter_state(person, hist.day, cfg)
        if P is None:
            return None
        m, s = read_curve(P, float(target["km"]), cfg)
        if cfg.tempo_shortfall and target["tier"] == "hard_effort":
            m -= _tempo_gap(person, hist, target, cfg)
        return {"vdot": m, "sd": math.sqrt(s * s + cfg.race_sd ** 2)}
    f.__name__ = name or "tobit"
    return f


def _tempo_gap(person, hist, target, cfg):
    """Expected shortfall of a hard_effort of this workout kind: median over the person's earlier hard_effort
    targets (same kind: tempo vs other) of (filter mean on that day - effort VDOT), shrunk to 0 with 2 pseudo-obs."""
    t = hist.targets
    t = t[t.tier == "hard_effort"]
    kind = target["workout"] in ("tempo",)
    t = t[(t.workout == "tempo") == kind]
    g = []
    for r in t.itertuples():
        P = filter_state(person, int(r.day), cfg)
        if P is None:
            continue
        g.append(read_curve(P, float(r.km), cfg)[0] - float(r.vdot))
    if not g:
        return 0.0
    g = np.clip(np.array(g), 0, None)
    return float(np.median(g) * len(g) / (len(g) + 2))


# ------------------------------------------------------------------ envelope quantile and EVT


def _effort_table(hist, cfg, slope=0.0):
    """5k-equivalent effort VDOTs (day, y5, w) of bests (+ races, + interval scores)."""
    rows = []
    for o in observations(hist, cfg):
        rows.append((o[0], o[1] - slope * o[2]))
    return pd.DataFrame(rows, columns=["day", "y5"])


def wquantile(x, w, q):
    o = np.argsort(x); x = np.asarray(x)[o]; w = np.asarray(w)[o]
    c = (np.cumsum(w) - 0.5 * w) / w.sum()
    return float(np.interp(q, c, x))


def make_quantile(q=0.95, half_life=60.0, cfg=Cfg(use_intervals=True), horizon=365, name=None):
    def f(person, hist, target):
        e = _effort_table(hist, cfg)
        e = e[e.day >= hist.day - horizon]
        if len(e) < 3:
            return None
        w = 0.5 ** ((hist.day - e.day) / half_life)
        return {"vdot": wquantile(e.y5.to_numpy(), w.to_numpy(), q), "sd": 2.5}
    f.__name__ = name or f"quantile_q{q}_h{half_life}"
    return f


def make_gumbel(period=7, n_weeks=26, ret=8.0, cfg=Cfg(use_intervals=True), name=None):
    """Gumbel (method of moments, recency-weighted) on per-`period`-day maxima over the last n_weeks periods.
    Prediction = return level of `ret` periods (the level exceeded once every `ret` periods)."""
    def f(person, hist, target):
        e = _effort_table(hist, cfg)
        e = e[e.day >= hist.day - period * n_weeks]
        if e.empty:
            return None
        e = e.assign(b=(hist.day - 1 - e.day) // period)
        mx = e.groupby("b").y5.max()
        if len(mx) < 4:
            return None
        w = 0.5 ** (mx.index.to_numpy() / (n_weeks / 2))
        m = np.average(mx, weights=w); v = np.average((mx - m) ** 2, weights=w)
        beta = math.sqrt(6 * v) / math.pi; mu = m - 0.5772 * beta
        lvl = mu - beta * math.log(-math.log(1 - 1 / ret))
        return {"vdot": float(lvl), "sd": float(max(beta * 1.28, 1.0))}
    f.__name__ = name or f"gumbel_r{ret}"
    return f


# ------------------------------------------------------------------ runner


def table(results, persons=("adrian", "rise", "patrik")):
    """MAE per tier x person from the per-target predictions (+ race a+r, race NLL / 1-sd coverage a+r)."""
    rows = []
    for r in results:
        P = r["preds"]; P = P[P.pred.notna()]
        e = (P.pred - P.actual).abs()
        row = {"model": r["name"]}
        for tier in ("race", "interval", "hard_effort"):
            for p in persons + ("all",):
                m = (P.tier == tier) & ((P.person == p) if p != "all" else True)
                row[f"{tier[:4]} {p[:3]}"] = round(float(e[m].mean()), 2) if m.any() else np.nan
        m = (P.tier == "race") & P.person.isin(["adrian", "rise"])
        row["race a+r"] = round(float(e[m].mean()), 3)
        row["race bias a+r"] = round(float((P.pred - P.actual)[m].mean()), 2)
        if P.sd.notna().any():
            x = P[m & P.sd.notna()]
            sd = np.maximum(x.sd.astype(float), 0.3); z = (x.pred - x.actual) / sd
            row["race nll"] = round(float(np.mean(0.5 * z ** 2 + np.log(sd) + 0.919)), 2)
            row["race cov"] = round(float((z.abs() <= 1).mean()), 2)
        rows.append(row)
    return pd.DataFrame(rows)


def ev(fn, name, persons=("adrian", "rise", "patrik")):
    return H.evaluate(fn, persons=persons, name=name)


if __name__ == "__main__":
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40)
    res = [ev(H.production_curve, "production_curve"), ev(H.naive_best_recent, "naive_best_recent_60d")]
    res.append(ev(make_tobit(Cfg()), "tobit"))
    print(table(res).to_string(index=False))

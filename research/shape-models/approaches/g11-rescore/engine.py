"""G11 copy of G9's combined engine (g9-combined/combined.py) extended with (1) every production item per
(activity, discipline) (A1 fix, via harness.prod_items), (2) an optional static per-person interval shortfall state
dI (interval items y = theta - dI + e; interval-tier targets read theta - dI), (3) an optional per-person slope mean.
With int_delta False and no per-person slope mean it matches combined.py + cands._prod_items (check in variants.py).

Original docstring: G9 combined candidate: production distance curve (G1 re-implementation) + G5 censored best-effort stream (B)
+ G1 faster slope (S) + G8 shared slope mean (M) + G5 hard_effort shortfall read-out (E), and tempo-placement
variants (T1 own distance + learned shortfall state, T2 tempo only as censored best). Spec: REPORT.md section 1.

    import combined as CB
    fn = CB.make(CB.SPEC)                 # predict(person, hist, target) -> {"vdot", "sd"}

State (curve2): theta (5 km VDOT, RW q_theta), b~ = slope - slope_mean (AR(1) to 0), optional delta (tempo shortfall,
static). Observation y = theta + x*(b~ + slope_mean) [- delta for T1 tempos], x = log2(km/5). Production items use
the production asymmetric IRLS weights with RTS-smoothed residuals (as production / G1). Censored bests are folded in
by assumed-density filtering: the likelihood depends on mu = h's only, so the exact 1-D posterior moments of mu
(quadrature) give the Gaussian update of the full state.
"""
from __future__ import annotations

import functools
import math
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from scipy.special import ndtr

HERE = Path(__file__).resolve().parent
SM = HERE.parents[1]
for p in (SM, SM / "approaches" / "g1-kalman", SM / "approaches" / "g5-envelope"):
    sys.path.insert(0, str(p))
sys.dont_write_bytecode = True
import common as C  # noqa: E402
import harness as H  # noqa: E402
import g1  # noqa: E402
import g5  # noqa: E402

FAR = 10 ** 7


@dataclass(frozen=True)
class Spec:
    q_theta: float = 0.05
    q_slope: float = 0.005
    slope_sd: float = 1.5
    slope_mean: float = 0.0
    prior_sd: float = 6.0
    bests: bool = False
    shortfall: bool = False            # E: hard_effort read-out
    tempo: str = "prod"                # prod | own (T1) | censored (T2)
    delta_mu: float = 2.0
    delta_sd: float = 2.0
    # G5 stream settings
    min_m: float = 1600.0
    gps_bias: float = 1.0
    U: float = 20.0
    sig_best: float = 2.0
    p_out: float = 0.02
    iters: int = 5
    race_sd: float = 1.0
    extrap_sd: float = 1.0
    int_delta: bool = False            # V4-V7: static interval shortfall state
    int_delta_mu: float = 0.0
    int_delta_sd: float = 1.5
    slope_mean_person: tuple = ()      # V8: (("adrian", 0.0), ...) overrides slope_mean per person
    tests_as_races: bool = False       # V9: G10 high-confidence extras as race items (replace that run's items)


@functools.lru_cache(maxsize=8)
def _tests(person):
    import pandas as pd
    X = pd.read_parquet(C.OUT / "g10" / "extra_targets.parquet")
    X = X[(X.person == person) & (X.confidence == "high")].sort_values("day")
    return [(int(r.day), float(r.vdot), float(r.km), int(r.activity_id)) for r in X.itertuples()]


PROD = Spec()
SPEC = Spec(q_slope=0.04, slope_sd=2.0, slope_mean=0.75, bests=True, shortfall=True)   # pre-registered C


# ------------------------------------------------------------------ evidence
def slope_mean_of(person, sp):
    return dict(sp.slope_mean_person).get(person, sp.slope_mean)


@functools.lru_cache(maxsize=8)
def _prod_items(person):
    ev = H.prod_items(person)
    tempo = ((ev.discipline == "long") & (ev.kind == "hard")).to_numpy()
    inter = ((ev.discipline == "interval") & (ev.kind == "hard")).to_numpy()
    own_km = (ev.gap_speed * ev.window_s / 1000).to_numpy(float)
    return {"day": ev.day.to_numpy(int), "y": ev.score.to_numpy(float), "s": ev.sigma.to_numpy(float),
            "kind": ev.kind.to_numpy(str), "km": ev.km.to_numpy(float), "tempo": tempo, "inter": inter,
            "own_km": own_km, "aid": ev.activity_id.to_numpy()}


@functools.lru_cache(maxsize=32)
def _bests(person, min_m, gps_bias):
    h = H.history(person, FAR)
    b = g5.run_bests(h, replace(g5.Cfg(), min_m=min_m, gps_bias=gps_bias))
    pk = h.acts.set_index("activity_id")[["prod_kind", "prod_discipline"]]
    b = b.join(pk, on="activity_id").sort_values("day")
    return b


def evidence(person, day, sp: Spec):
    """List of observations before `day`: dicts day, y, km, s, kind, typ (prod | tempo_own | best), pi."""
    P = _prod_items(person)
    out = []
    tests = [t for t in _tests(person) if t[0] < day] if sp.tests_as_races else []
    taids = {t[3] for t in _tests(person)} if sp.tests_as_races else set()
    for d_, v_, km_, aid_ in tests:
        out.append(dict(day=d_, y=v_, km=km_, s=1.0, kind="race", typ="prod"))
    for j in range(int(np.searchsorted(P["day"], day, "left"))):
        if int(P["aid"][j]) in taids:
            continue
        if P["tempo"][j] and sp.tempo == "censored":
            continue
        if P["tempo"][j] and sp.tempo == "own":
            out.append(dict(day=int(P["day"][j]), y=float(P["y"][j]) - 3.0, km=float(P["own_km"][j]), s=float(P["s"][j]),
                            kind="hard", typ="tempo_own"))
        else:
            out.append(dict(day=int(P["day"][j]), y=float(P["y"][j]), km=float(P["km"][j]), s=float(P["s"][j]),
                            kind=str(P["kind"][j]), typ="int" if P["inter"][j] else "prod"))
    if sp.bests:
        b = _bests(person, sp.min_m, sp.gps_bias)
        b = b[b.day < day]
        scored = b.prod_kind.isin(["hard", "race"])
        if sp.tempo == "censored":
            scored &= ~((b.prod_discipline == "long") & (b.prod_kind == "hard"))
        if taids:
            scored |= b.activity_id.isin(taids)
        for r in b[~scored].itertuples():
            out.append(dict(day=int(r.day), y=float(r.y), km=float(r.target_m) / 1000, s=sp.sig_best, kind="best",
                            typ="best", pi=float(r.pi)))
    out.sort(key=lambda o: (o["day"], o["typ"] == "best"))
    return out


# ------------------------------------------------------------------ filter
def idx(sp: Spec):
    """state size and indices: theta 0, slope 1, tempo delta (T1), interval delta (dI)."""
    k = 2; it = ii = None
    if sp.tempo == "own":
        it = k; k += 1
    if sp.int_delta:
        ii = k; k += 1
    return k, it, ii


def _system(sp: Spec):
    ph = math.sqrt(max(1 - sp.q_slope / sp.slope_sd ** 2, 0.0))
    nd, it, ii = idx(sp)
    f = [1.0, ph]; q = [sp.q_theta, sp.q_slope]; p0 = [sp.prior_sd ** 2, sp.slope_sd ** 2]
    if it is not None:
        f.append(1.0); q.append(0.0); p0.append(sp.delta_sd ** 2)
    if ii is not None:
        f.append(1.0); q.append(0.0); p0.append(sp.int_delta_sd ** 2)
    F = np.diag(f); Q = np.diag(q); P0 = np.diag(p0)
    return F, Q, P0, g1.Steps(F, Q)


_SYS = {}


def _best_moments(y, s, pi, U, p_out, m, v):
    """Posterior mean / var of mu ~ N(m, v) under the censored likelihood of y."""
    sd = math.sqrt(v)
    mu = m + sd * np.linspace(-7, 7, 281)
    z = y - mu
    L = pi * np.exp(-0.5 * (z / s) ** 2) / (s * 2.5066282746) + (1 - pi) * (ndtr((z + U) / s) - ndtr(z / s)) / U
    L = (1 - p_out) * L + p_out / 40.0
    w = L * np.exp(-0.5 * ((mu - m) / sd) ** 2)
    w /= w.sum()
    mp = float(w @ mu)
    return mp, float(w @ (mu - mp) ** 2)


def fit(person, day, sp: Spec):
    O = evidence(person, day, sp)
    if not O:
        return None
    if sp not in _SYS:
        _SYS[sp] = _system(sp)
    F1, Q1, P0, steps = _SYS[sp]
    nd, it_, ii_ = idx(sp)
    smean = slope_mean_of(person, sp)
    n = len(O)
    days = np.array([o["day"] for o in O])
    x = np.array([g1.cx(o["km"]) for o in O])
    yy = np.array([o["y"] for o in O]) - smean * x          # remove the slope mean: b~ is AR(1) to 0
    Hs = []
    for o, xi in zip(O, x):
        h = np.zeros(nd); h[0] = 1.0; h[1] = xi
        if o["typ"] == "tempo_own":
            h[it_] = -1.0
        if o["typ"] == "int" and ii_ is not None:
            h[ii_] = -1.0
        Hs.append(h)
    prod = np.array([o["typ"] != "best" for o in O])
    good = [yy[j] for j in range(n) if prod[j] and O[j]["kind"] != "bound"]
    good = good or [yy[j] for j in range(n) if prod[j]]          # production rule: bounds if no hard / race item
    if good:
        lvl = float(np.median(good[:5]))
    else:                                                          # only censored bests (no production evidence)
        lvl = float(np.quantile(yy, 0.9))
    m_init = np.zeros(nd); m_init[0] = lvl
    if it_ is not None:
        m_init[it_] = sp.delta_mu
    if ii_ is not None:
        m_init[ii_] = sp.int_delta_mu
    ud = np.unique(days); K = len(ud)
    jl = [np.nonzero(days == d)[0] for d in ud]
    w = np.ones(n)
    for it in range(sp.iters):
        mf = np.zeros((K, nd)); Pf = np.zeros((K, nd, nd)); mp = np.zeros_like(mf); Pp = np.zeros_like(Pf)
        Fs = [None] * K
        m, P = m_init.copy(), P0.copy()
        for k in range(K):
            if k:
                F, Q = steps(int(ud[k] - ud[k - 1]))
                m = F @ m; P = F @ P @ F.T + Q; Fs[k] = F
            mp[k], Pp[k] = m, P
            for j in jl[k]:
                h = Hs[j]; o = O[j]
                pred = float(h @ m); Ph = P @ h; vx = max(float(h @ Ph), 1e-9)
                if prod[j]:
                    if it == 0:
                        w[j] = g1.w_prod((yy[j] - pred) / math.sqrt(o["s"] ** 2 + vx), o["kind"], {})
                    S = vx + o["s"] ** 2 / max(w[j], 1e-6)
                    Kg = Ph / S
                    m = m + Kg * (yy[j] - pred); P = P - np.outer(Kg, Ph)
                else:
                    mpo, vpo = _best_moments(yy[j], o["s"], o["pi"], sp.U, sp.p_out, pred, vx)
                    m = m + Ph * (mpo - pred) / vx
                    P = P - np.outer(Ph, Ph) * (vx - vpo) / vx ** 2
            mf[k], Pf[k] = m, P
        ms = mf.copy()
        for k in range(K - 2, -1, -1):
            G = Pf[k] @ Fs[k + 1].T @ np.linalg.inv(Pp[k + 1])
            ms[k] = mf[k] + G @ (ms[k + 1] - mp[k + 1])
        kof = np.searchsorted(ud, days)
        neww = w.copy()
        for j in range(n):
            if prod[j]:
                neww[j] = g1.w_prod((yy[j] - float(Hs[j] @ ms[kof[j]])) / O[j]["s"], O[j]["kind"], {})
        if it and np.allclose(neww, w):
            break
        w = neww
    F, Q = steps(int(day - 1 - ud[-1]))
    m = F @ mf[-1]; P = F @ Pf[-1] @ F.T + Q
    ev = [o["km"] for o, wt, pj in zip(O, w, prod) if pj and o["kind"] in ("race", "hard") and wt > 0.5]
    return {"m": m, "P": P, "ekm": (min(ev), max(ev)) if ev else None, "smean": smean, "ii": ii_}


_FITS = {}


def cached_fit(person, day, sp):
    k = (person, int(day), sp)
    if k not in _FITS:
        _FITS[k] = fit(person, int(day), sp)
    return _FITS[k]


def read(st, km, sp: Spec, interval_score=False):
    """Production read-out rules on the full slope b = b~ + slope_mean."""
    m, P = st["m"], st["P"]
    nd = len(m)

    def hv(xx):
        h = np.zeros(nd); h[0] = 1.0; h[1] = xx
        return h
    x = g1.cx(km); xm, out = x, 0.0
    if st["ekm"]:
        lo, hi = g1.cx(st["ekm"][0]), g1.cx(st["ekm"][1])
        out = max(lo - x, x - hi, 0.0)
        if x > hi and (m[1] + st["smean"]) > 0:
            xm = hi
    h = hv(xm)
    if interval_score and st["ii"] is not None:
        h[st["ii"]] = -1.0
    v = float(h @ m) + st["smean"] * xm
    vs = max(float(h @ P @ h), 0.0)
    return {"vdot": v, "sd": math.sqrt(vs + sp.race_sd ** 2 + (sp.extrap_sd * out) ** 2), "sd_state": math.sqrt(vs),
            "theta": float(m[0]), "slope": float(m[1] + st["smean"]), "slope_sd": math.sqrt(P[1, 1]),
            "dI": float(m[st["ii"]]) if st["ii"] is not None else 0.0,
            "dT": float(m[2]) if sp.tempo == "own" else 0.0}


def _shortfall(person, hist, target, sp):
    t = hist.targets
    t = t[t.tier == "hard_effort"]
    kind = target["workout"] == "tempo"
    t = t[(t.workout == "tempo") == kind]
    g = []
    for r in t.itertuples():
        st = cached_fit(person, int(r.day), sp)
        if st is not None:
            g.append(read(st, float(r.km), sp)["vdot"] - float(r.vdot))
    if not g:
        return 0.0
    g = np.clip(np.array(g), 0, None)
    return float(np.median(g) * len(g) / (len(g) + 2))


def make(sp: Spec, name=None):
    def predict(person, hist, target):
        st = cached_fit(person, hist.day, sp)
        if st is None:
            return None
        r = read(st, float(target["km"]), sp, interval_score=target.get("tier") == "interval")
        if sp.shortfall and target.get("tier") == "hard_effort":
            r["vdot"] -= _shortfall(person, hist, target, sp)
        return r
    predict.__name__ = name or "combined"
    return predict


predict = make(SPEC, "g11_engine_C")

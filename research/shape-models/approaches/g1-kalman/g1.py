"""G1: Kalman-family state-space models of running shape (VDOT) on the production evidence stream.

One generic linear-Gaussian engine (event-based: the daily random walk is integrated exactly over the gaps between
evidence days), robust IRLS observation weights (production asymmetric rule or Student-t / skewed-t EM weights),
RTS smoother for the weight iterations, and a set of state-space "structures":

  curve2   production: theta (5 km VDOT, random walk) + slope per doubling (AR(1) to 0); y = theta + x*slope
  curve3   + curvature c (AR(1) to 0): y = theta + x*slope + x^2*c
  curvevol + static volume gain b: y = theta + x*(slope + b*z_t), z_t = log run hours 28 d (centred)
  llt      local linear trend: theta' = theta + v, v AR(1) (damped velocity), + slope
  anchors  separate fitness states at anchor distances (e.g. 5 km and 21.1 km) with a correlated random walk
           (corr rho), optional mean reversion of the anchors towards their mean (kappa), observation = linear
           interpolation in x between anchors ("linear") or the nearest anchor only ("nearest" = separate disciplines)

Process noise can grow with low training load (q_t = q * (1 + a * max(0, 1 - h28/h_ref))) or in run gaps.
x = log2(km / 5). Evidence = production per-session items (activities.prod_*), as harness._prod_state uses.

Run from the repo root with /tmp/shape-venv/bin/python.
"""
from __future__ import annotations

import functools
import math
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
import common as C  # noqa: E402
import harness as Hn  # noqa: E402

REF_KM = 5.0
GAP_DAYS = 7
X_HM = math.log2(21.0975 / REF_KM)
SIG_FLOOR = 0.3


def cx(km):
    return math.log2(km / REF_KM)


# ------------------------------------------------------------------ robust weights
PROD_ROBUST = {"race": (2.5, 2.5), "hard": (1.0, 2.5), "bound": (None, 4.0)}
BOUND_Z = 2.0


def w_prod(z, kind, cfg):
    """Production asymmetric weight of standardised residual z = (y - est) / sd."""
    lo, hi = PROD_ROBUST[kind]
    if kind == "bound" and z <= BOUND_Z:
        return 1e-6
    if z < 0:
        return 1.0 if -z <= lo else (lo / -z) ** 2
    return 1.0 if z <= hi else (hi / z) ** 2


def w_student(z, kind, cfg):
    """Student-t EM weight, normalised to 1 at z = 0 (sigma stays the core sd): w = 1 / (1 + z^2/nu); skewed: separate (nu, scale) below / above the curve per kind.
    A residual below the curve (sub-maximal session) is scaled by `scale_lo` (z / scale_lo) and uses nu_lo."""
    if kind == "bound":
        return w_prod(z, kind, cfg)
    nu_lo, nu_hi, sc_lo, sc_hi = cfg["t"][kind]
    if z < 0:
        zz = z / sc_lo
        return 1.0 / (1.0 + zz * zz / nu_lo) / sc_lo ** 2
    zz = z / sc_hi
    return 1.0 / (1.0 + zz * zz / nu_hi) / sc_hi ** 2


# ------------------------------------------------------------------ model config
@dataclass(frozen=True)
class Cfg:
    structure: str = "curve2"          # curve2 | curve3 | curvevol | llt | anchors
    q_theta: float = 0.05              # VDOT^2/day on the level (theta or the common anchor shock)
    q_slope: float = 0.005
    slope_sd: float = 1.5              # stationary sd of the slope (AR(1)); also its prior sd
    prior_sd: float = 6.0
    curv_sd: float = 0.5               # curve3: stationary sd of curvature (VDOT per doubling^2)
    q_curv: float = 0.0005
    vol_sd: float = 1.0                # curvevol: prior sd of the volume gain b (VDOT per doubling per unit z)
    vel_sd: float = 0.01               # llt: stationary sd of the velocity (VDOT/day)
    vel_tau: float = 60.0              # llt: velocity decorrelation time (days)
    anchors: tuple = (0.0, X_HM)       # anchors (x)
    rho: float = 0.8                   # anchors: correlation of the daily shocks
    q_anchor: float = 0.05             # anchors: per-anchor daily variance
    kappa: float = 0.0                 # anchors: per-day pull of each anchor towards the anchor mean
    hmap: str = "linear"               # anchors: linear | nearest
    load_a: float = 0.0                # extra q on the level: q*(load_a * max(0, 1 - h28/h_ref)) per day
    load_hi: float = 0.0               # extra q on the level when load is high: q*load_hi*max(0, h28/h_ref - 1)
    gap_q: float = 0.0                 # extra q per day when no run in the last 7 days
    robust: str = "prod"               # prod | t
    t: tuple = None                    # dict kind -> (nu_lo, nu_hi, sc_lo, sc_hi) (as tuple of items for hashing)
    iters: int = 5
    race_sd: float = 1.0
    extrap_sd: float = 1.0
    hold_beyond: bool = True           # positive slope stops beyond the longest evidence (production rule)
    sig_scale: tuple = ()              # ((kind, factor), ...) multiplies the evidence sigma per kind

    def tdict(self):
        return dict(self.t) if self.t else {}


T_DEFAULT = (("race", (4.0, 4.0, 1.0, 1.0)), ("hard", (1.0, 4.0, 1.0, 1.0)))


# ------------------------------------------------------------------ structures
def build(cfg: Cfg):
    """Per-day transition F1, base process noise Q1, extra-noise direction u (F1 u = u), prior builder, H(x, z)."""
    s = cfg.structure
    if s in ("curve2", "curve3", "curvevol"):
        ph = math.sqrt(max(1 - cfg.q_slope / cfg.slope_sd ** 2, 0.0)) if cfg.slope_sd ** 2 > cfg.q_slope else 1.0
        F = [1.0, ph]; Q = [cfg.q_theta, cfg.q_slope]; P0 = [cfg.prior_sd ** 2, cfg.slope_sd ** 2]
        if s == "curve3":
            pc = math.sqrt(max(1 - cfg.q_curv / cfg.curv_sd ** 2, 0.0))
            F.append(pc); Q.append(cfg.q_curv); P0.append(cfg.curv_sd ** 2)
        if s == "curvevol":
            F.append(1.0); Q.append(0.0); P0.append(cfg.vol_sd ** 2)
        F1 = np.diag(F); Q1 = np.diag(Q); P0 = np.diag(P0)
        u = np.zeros(len(F)); u[0] = 1.0
        if s == "curve3":
            H = lambda x, z: np.array([1.0, x, x * x])
        elif s == "curvevol":
            H = lambda x, z: np.array([1.0, x, x * z])
        else:
            H = lambda x, z: np.array([1.0, x])
        m0f = lambda lvl: np.r_[lvl, np.zeros(len(F) - 1)]
        return F1, Q1, u, P0, m0f, H
    if s == "llt":
        ph = math.sqrt(1 - cfg.q_slope / cfg.slope_sd ** 2)
        pv = math.exp(-1.0 / cfg.vel_tau)
        F1 = np.array([[1.0, 1.0, 0.0], [0.0, pv, 0.0], [0.0, 0.0, ph]])
        Q1 = np.diag([cfg.q_theta, cfg.vel_sd ** 2 * (1 - pv * pv), cfg.q_slope])
        P0 = np.diag([cfg.prior_sd ** 2, cfg.vel_sd ** 2, cfg.slope_sd ** 2])
        u = np.array([1.0, 0.0, 0.0])
        H = lambda x, z: np.array([1.0, 0.0, x])
        m0f = lambda lvl: np.array([lvl, 0.0, 0.0])
        return F1, Q1, u, P0, m0f, H
    if s == "anchors":
        xa = np.asarray(cfg.anchors, float); n = len(xa)
        R = cfg.rho ** np.abs(np.subtract.outer(np.arange(n), np.arange(n)))
        Q1 = cfg.q_anchor * R
        F1 = np.eye(n) - cfg.kappa * (np.eye(n) - np.ones((n, n)) / n)
        u = np.ones(n)
        # prior = production prior (level sd prior_sd, slope sd slope_sd) mapped to the anchors
        P0 = cfg.prior_sd ** 2 * np.ones((n, n)) + cfg.slope_sd ** 2 * np.outer(xa, xa)

        def H(x, z):
            h = np.zeros(n)
            if cfg.hmap == "nearest":
                h[int(np.argmin(np.abs(xa - x)))] = 1.0
                return h
            j = int(np.clip(np.searchsorted(xa, x) - 1, 0, n - 2))
            f = (x - xa[j]) / (xa[j + 1] - xa[j])
            h[j] = 1 - f; h[j + 1] = f
            return h
        m0f = lambda lvl: np.full(n, lvl)
        return F1, Q1, u, P0, m0f, H
    raise ValueError(s)


class Steps:
    """F_d, Q_d for d-day gaps (exact composition of the daily step), cached."""

    def __init__(self, F1, Q1):
        self.F1, self.Q1 = F1, Q1
        self.cache = {0: (np.eye(len(F1)), np.zeros_like(Q1)), 1: (F1, Q1)}

    def __call__(self, d):
        if d in self.cache:
            return self.cache[d]
        h = d // 2
        Fa, Qa = self(h); Fb, Qb = self(d - h)
        F = Fb @ Fa; Q = Fb @ Qa @ Fb.T + Qb
        self.cache[d] = (F, Q)
        return F, Q


@functools.lru_cache(maxsize=64)
def _built(cfg):
    F1, Q1, u, P0, m0f, H = build(cfg)
    return F1, Q1, u, P0, m0f, H, Steps(F1, Q1)


# ------------------------------------------------------------------ evidence
@functools.lru_cache(maxsize=8)
def person_data(person):
    """All production evidence items of a person (sorted by day) and the daily run-hours series."""
    A, _, _ = Hn.load_data()
    a = A[A.person == person]
    ev = a[a.prod_discipline.isin(["interval", "long"]) & a.prod_kind.isin(["hard", "race", "bound"]) &
           a.prod_km.notna() & a.prod_score.notna()].sort_values(["day", "start_local"])
    obs = {"day": ev.day.to_numpy(int), "y": ev.prod_score.to_numpy(float), "s": ev.prod_sigma.to_numpy(float),
           "kind": ev.prod_kind.to_numpy(str), "x": np.array([cx(k) for k in ev.prod_km], float)}
    # daily moving hours of running (all runs, any quality), and rolling 28 d before each day (days < d)
    d0 = int(a.day.min()) - 60; d1 = int(a.day.max()) + 400
    hrs = np.zeros(d1 - d0 + 1)
    np.add.at(hrs, a.day.to_numpy(int) - d0, a.moving_s.fillna(0).to_numpy(float) / 3600)
    cs = np.r_[0, np.cumsum(hrs)]
    idx = np.arange(d1 - d0 + 1)
    h28 = cs[idx] - cs[np.maximum(idx - 28, 0)]                     # hours on days [d-28, d-1]
    ran = (hrs > 0).astype(float); cr = np.r_[0, np.cumsum(ran)]
    gap = (cr[idx] - cr[np.maximum(idx - GAP_DAYS, 0)]) == 0  # no run in the last gap_days days
    return obs, d0, h28, gap




def obs_before(person, day):
    obs, d0, h28, gap = person_data(person)
    k = int(np.searchsorted(obs["day"], day, "left"))
    return {key: v[:k] for key, v in obs.items()}, d0, h28, gap


# ------------------------------------------------------------------ fit
def fit(person, day, cfg: Cfg):
    """Robust Kalman fit on evidence of days < day; state read at day - 1.
    Returns dict(m, P, w, x, kind, ekm, fc) or None."""
    o, d0, h28, gap = obs_before(person, day)
    n = len(o["day"])
    if n == 0:
        return None
    F1, Q1, u, P0, m0f, H, steps = _built(cfg)
    days, y, s, kind, x = o["day"], o["y"], o["s"].copy(), o["kind"], o["x"]
    for kk, f in cfg.sig_scale:
        s[kind == kk] *= f
    # load / gap extra noise (cumulative per day, on the level direction u)
    hist_h = h28[: day - d0]
    href = float(np.median(hist_h[hist_h > 0])) if (hist_h > 0).any() else 1.0
    extra = np.zeros(len(h28))
    if cfg.load_a:
        extra += cfg.q_theta * cfg.load_a * np.clip(1 - h28 / href, 0, None)
    if cfg.load_hi:
        extra += cfg.q_theta * cfg.load_hi * np.clip(h28 / href - 1, 0, None)
    if cfg.gap_q:
        extra += cfg.gap_q * gap
    cex = np.r_[0, np.cumsum(extra)]
    UU = np.outer(u, u)
    # z (volume) per obs and at the end
    zser = np.log1p(h28) - float(np.mean(np.log1p(hist_h[-730:]))) if cfg.structure == "curvevol" else None
    zs = zser[days - d0] if zser is not None else np.zeros(n)
    Hs = [H(x[j], zs[j]) for j in range(n)]
    good = [y[j] for j in range(n) if kind[j] != "bound"] or list(y)
    m_init = m0f(float(np.median(good[:5])))
    wfun = w_student if cfg.robust == "t" else w_prod
    wcfg = {"t": cfg.tdict()}
    ud = np.unique(days)
    jl = [np.nonzero(days == d)[0] for d in ud]
    K = len(ud)
    w = np.ones(n)
    fc0 = None
    for it in range(cfg.iters):
        mf = np.zeros((K, len(m_init))); Pf = np.zeros((K, len(m_init), len(m_init)))
        mp = np.zeros_like(mf); Pp = np.zeros_like(Pf); Fs = [None] * K
        m, P = m_init.copy(), P0.copy()
        fc = [None] * n
        for k in range(K):
            if k:
                dd = int(ud[k] - ud[k - 1])
                F, Q = steps(dd)
                ex = cex[ud[k] - d0] - cex[ud[k - 1] - d0]
                m = F @ m; P = F @ P @ F.T + Q + ex * UU
                Fs[k] = F
            mp[k], Pp[k] = m, P
            for j in jl[k]:
                h = Hs[j]
                pred = float(h @ m); Ph = P @ h; vx = float(h @ Ph)
                fc[j] = (pred, math.sqrt(max(vx, 0)))
                if it == 0:
                    w[j] = wfun((y[j] - pred) / math.sqrt(s[j] ** 2 + vx), kind[j], wcfg)
                S = vx + s[j] ** 2 / max(w[j], 1e-6)
                Kg = Ph / S
                m = m + Kg * (y[j] - pred)
                P = P - np.outer(Kg, Ph)
            mf[k], Pf[k] = m, P
        # RTS
        ms, Ps = mf.copy(), Pf.copy()
        for k in range(K - 2, -1, -1):
            F = Fs[k + 1]
            G = Pf[k] @ F.T @ np.linalg.inv(Pp[k + 1])
            ms[k] = mf[k] + G @ (ms[k + 1] - mp[k + 1])
            Ps[k] = Pf[k] + G @ (Ps[k + 1] - Pp[k + 1]) @ G.T
        if it == 0:
            fc0 = fc
        kof = np.searchsorted(ud, days)
        neww = np.array([wfun((y[j] - float(Hs[j] @ ms[kof[j]])) / s[j], kind[j], wcfg) for j in range(n)])
        if it and np.allclose(neww, w):
            break
        w = neww
    # propagate the filtered end state to day - 1
    dd = int(day - 1 - ud[-1])
    F, Q = steps(dd)
    ex = cex[day - 1 - d0] - cex[ud[-1] - d0]
    m = F @ mf[-1]; P = F @ Pf[-1] @ F.T + Q + ex * UU
    ev = [5.0 * 2 ** x[j] for j in range(n) if kind[j] in ("race", "hard") and w[j] > 0.5]
    zend = float(zser[day - 1 - d0]) if zser is not None else 0.0
    return {"m": m, "P": P, "w": w, "ekm": (min(ev), max(ev)) if ev else None, "fc": fc0, "obs": o, "s": s,
            "z": zend, "H": H}


def read(st, km, cfg: Cfg):
    """Prediction {vdot, sd} at km with production's extrapolation rules."""
    H, m, P = st["H"], st["m"], st["P"]
    x = cx(km)
    xm, out = x, 0.0
    if st["ekm"]:
        lo, hi = cx(st["ekm"][0]), cx(st["ekm"][1])
        out = max(lo - x, x - hi, 0.0)
        if cfg.hold_beyond and x > hi:
            vx_, vh_ = float(H(x, st["z"]) @ m), float(H(hi, st["z"]) @ m)
            if vx_ > vh_:
                xm = hi
    h = H(xm, st["z"])
    v = float(h @ m); vs = max(float(h @ P @ h), 0.0)
    return {"vdot": v, "sd": math.sqrt(vs + cfg.race_sd ** 2 + (cfg.extrap_sd * out) ** 2), "sd_state": math.sqrt(vs)}


_FITS = {}


def cached_fit(person, day, cfg):
    k = (person, int(day), cfg)
    if k not in _FITS:
        _FITS[k] = fit(person, int(day), cfg)
    return _FITS[k]


def make_predict(cfg: Cfg, name=None):
    def predict(person, hist, target):
        st = cached_fit(person, hist.day, cfg)
        if st is None:
            return None
        return read(st, float(target["km"]), cfg)
    predict.__name__ = name or cfg.structure
    return predict


# ------------------------------------------------------------------ in-history diagnostics (for tuning without races)
def one_step_ll(person, cfg, kinds=("hard", "race"), skip=3, end_day=None):
    """One-step-ahead forecast log-lik / MAE of the race + hard items (forward filter, pass-1 weights) on all evidence
    before end_day. Used to tune knobs without looking at the race targets."""
    obs, *_ = person_data(person)
    end = int(obs["day"][-1]) + 1 if end_day is None else end_day
    st = fit(person, end, replace(cfg, iters=1))
    if st is None:
        return np.nan, np.nan, 0
    o = st["obs"]; s = st["s"]
    e, ll = [], []
    for j, f in enumerate(st["fc"]):
        if o["kind"][j] in kinds and f is not None:
            sd = math.sqrt(f[1] ** 2 + s[j] ** 2); z = (o["y"][j] - f[0]) / sd
            e.append(abs(o["y"][j] - f[0])); ll.append(-0.5 * min(z * z, 9) - math.log(sd) - 0.919)
    return float(np.mean(e[skip:])), float(np.mean(ll[skip:])), len(e[skip:])


# ------------------------------------------------------------------ time-forward model selection
@functools.lru_cache(maxsize=200000)
def fc_ll_sum(person, day, cfg, kinds=("hard", "race"), skip=3):
    """Sum and count of one-step-ahead forecast log-liks (z clipped at 3) of the race + hard items before `day`."""
    st = fit(person, int(day), replace(cfg, iters=1))
    if st is None:
        return 0.0, 0
    o, s = st["obs"], st["s"]
    ll = []
    for j, f in enumerate(st["fc"]):
        if o["kind"][j] in kinds and f is not None:
            sd = math.sqrt(f[1] ** 2 + s[j] ** 2); z = (o["y"][j] - f[0]) / sd
            ll.append(-0.5 * min(z * z, 9) - math.log(sd) - 0.919)
    ll = ll[skip:]
    return float(np.sum(ll)), len(ll)


def make_auto_predict(cands, name="auto", pool=("adrian", "rise", "patrik")):
    """At each target day pick the candidate config with the best pooled one-step forecast log-lik on the history
    (all persons' evidence strictly before the day), then predict with it. No race outcome is used for the choice
    beyond races as ordinary past evidence items."""
    chosen = {}

    def predict(person, hist, target):
        d = int(hist.day)
        if d not in chosen:
            sc = [sum(fc_ll_sum(p, d, c)[0] for p in pool) for c in cands]
            chosen[d] = int(np.argmax(sc))
        cfg = cands[chosen[d]]
        st = cached_fit(person, d, cfg)
        if st is None:
            return None
        return read(st, float(target["km"]), cfg)
    predict.__name__ = name
    predict.chosen = chosen
    return predict

"""G2: hierarchical effects model (pooled across persons) as one linear-Gaussian state-space model.

    y_i = theta_p(t) + slope_p(t) * x_i + sum_k X_ik (beta_k + u_pk) + e_i,   e_i ~ N(0, sigma_kind^2 / w_i)

  theta_p   per-person fitness (VDOT at 5 km, interval-on-track scale), daily random walk q_theta
  slope_p   per-person VDOT per doubling of distance, AR(1) towards 0 (production CURVE settings)
  beta_k    shared effects (static states with a N(mean, sd^2) prior)
  u_pk      per-person deviations from beta_k (random effects, prior N(0, tau_k^2)): partial pooling
  w_i       robust weights (production's asymmetric scheme or symmetric Huber), from the online innovation
            (mode "online") or iterated against the RTS-smoothed fit of the history (mode "smooth")

The whole thing is a Kalman filter on the augmented state, so the posterior of every effect (and its covariance) is
exact given the weights and hyperparameters, and the filter is causal: a prediction for day d is read from the state
after all observations of days < d (all persons; pooling only through other persons' past). Effect sizes for the
report are read from the fit on all data (final state = posterior given everything).
"""
from __future__ import annotations

import functools
import math
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1])); sys.path.insert(0, str(HERE))
import common as C  # noqa: E402
import obs as OBS  # noqa: E402

PERSONS = C.PERSONS


@dataclass(frozen=True)
class Config:
    gap: str = "min"                  # observation scale: "min" (production minetti-half GAP), "strava", "raw"
    kappa: bool = True                # estimate kappa * g (residual GAP correction; g = GAP shift of the chosen scale)
    heat_base: float = 24.0           # heat = max(0, wrist_temp_max - heat_base); NaN -> 0
    temp_linear: bool = False         # add a linear (temp - 24) term too
    use_eff: bool = True              # hard continuous efforts enter the fit (False: only for nothing; effects stay at prior)
    include_sub: bool = False         # steady / long best 5 km as sub-maximal observations with intent offsets
    robust: str = "asym"              # "asym" (production: hard lo 1.0 / hi 2.5 sigma; race 2.5 / 2.5), "sym" (2.5 / 2.5)
    mode: str = "online"              # "online" (one causal pass) | "smooth" (weights iterated on the RTS fit per day)
    iters: int = 3
    q_theta: float = 0.05
    q_slope: float = 0.005
    slope_sd: float = 1.5
    theta0: float = 50.0
    theta0_sd: float = 8.0
    tau: float = 1.0                  # random-effect sd (VDOT) of the per-person deviations; 0 = complete pooling
    tau_scale: tuple = ()             # ((effect, tau), ...) overrides
    sig: tuple = (("race", 1.0), ("int_track", 1.5), ("int_indoor_track", 1.5), ("int_road", 2.0), ("eff", 2.0),
                  ("sub", 3.5))
    sig_mult: float = 1.0
    effects: tuple = ("race", "race_track", "int_road", "int_indoor", "eff_road", "tempo", "time_trial", "hr_hard",
                      "steady", "long", "kappa", "heat")
    pooled: tuple = ("race", "int_road", "int_indoor", "eff_road", "tempo", "heat")
    race_sd: float = 1.0              # race-day noise added to race prediction sd
    extrap_sd: float = 1.0


PRIOR = {  # effect: (mean, sd)
    "race": (0.0, 3.0), "race_track": (0.0, 2.0), "int_road": (0.0, 3.0), "int_indoor": (0.0, 3.0),
    "eff_road": (0.0, 3.0), "tempo": (0.0, 5.0), "time_trial": (0.0, 5.0), "hr_hard": (0.0, 5.0),
    "steady": (-10.0, 8.0), "long": (-10.0, 8.0), "kappa": (0.0, 0.5), "heat": (0.0, 0.5), "temp_lin": (0.0, 0.5),
}
TAU_DEFAULT = {"kappa": 0.2, "heat": 0.15, "temp_lin": 0.1}


def effect_list(cfg):
    e = [k for k in cfg.effects if (k != "kappa" or cfg.kappa) and (k not in ("steady", "long") or cfg.include_sub)]
    if cfg.temp_linear:
        e.append("temp_lin")
    return e


def design_row(r, cfg, effects):
    """Covariate dict for one observation / target row (keys: effects)."""
    X = {}
    kind, surf, intent = r["kind"], r["surface"], r["intent"]
    if kind == "race":
        X["race"] = 1.0
        if surf in ("track", "indoor_track"):
            X["race_track"] = 1.0
    elif kind == "int":
        if surf == "road":
            X["int_road"] = 1.0
        elif surf == "indoor_track":
            X["int_indoor"] = 1.0
    else:
        if surf not in ("track", "indoor_track"):
            X["eff_road"] = 1.0
        if intent in ("tempo", "time_trial", "hr_hard", "steady", "long"):
            X[intent] = 1.0
    X = {k: v for k, v in X.items() if k in effects}
    X.update({k: 0.0 for k in effects if k not in X})
    if "kappa" in X:
        X["kappa"] = float(r.get("g", 0.0) or 0.0)
    t = r.get("temp")
    if t is not None and t == t:
        if "heat" in X:
            X["heat"] = max(0.0, float(t) - cfg.heat_base)
        if "temp_lin" in X:
            X["temp_lin"] = float(t) - 24.0
    return X


class Layout:
    def __init__(self, cfg):
        self.cfg = cfg
        self.effects = effect_list(cfg)
        self.pooled = [k for k in cfg.pooled if k in self.effects]
        names = []
        for p in PERSONS:
            names += [f"theta:{p}", f"slope:{p}"]
        names += [f"b:{k}" for k in self.effects]
        for p in PERSONS:
            names += [f"u:{p}:{k}" for k in self.pooled]
        self.names = names
        self.ix = {n: i for i, n in enumerate(names)}
        self.n = len(names)
        tau = dict(TAU_DEFAULT); tau.update(dict(cfg.tau_scale))
        m0 = np.zeros(self.n); P0 = np.zeros(self.n)
        for p in PERSONS:
            m0[self.ix[f"theta:{p}"]] = cfg.theta0; P0[self.ix[f"theta:{p}"]] = cfg.theta0_sd ** 2
            P0[self.ix[f"slope:{p}"]] = cfg.slope_sd ** 2
            for k in self.pooled:
                s = tau.get(k, cfg.tau) if k in tau else cfg.tau
                P0[self.ix[f"u:{p}:{k}"]] = max(s, 1e-4) ** 2
        for k in self.effects:
            m0[self.ix[f"b:{k}"]], s = PRIOR[k]; P0[self.ix[f"b:{k}"]] = s * s
        self.m0, self.P0 = m0, np.diag(P0)
        self.theta_ix = np.array([self.ix[f"theta:{p}"] for p in PERSONS])
        self.slope_ix = np.array([self.ix[f"slope:{p}"] for p in PERSONS])
        self.ph = math.sqrt(1 - cfg.q_slope / cfg.slope_sd ** 2)

    def h(self, person, x, X):
        h = np.zeros(self.n)
        h[self.ix[f"theta:{person}"]] = 1.0
        h[self.ix[f"slope:{person}"]] = x
        for k, v in X.items():
            if v:
                h[self.ix[f"b:{k}"]] = v
                if k in self.pooled:
                    h[self.ix[f"u:{person}:{k}"]] = v
        return h

    def transition(self, dt):
        """F (diagonal, as vector) and Q (diagonal) for dt days."""
        f = np.ones(self.n); q = np.zeros(self.n)
        if dt <= 0:
            return f, q
        c = self.cfg
        q[self.theta_ix] = c.q_theta * dt
        phd = self.ph ** dt
        f[self.slope_ix] = phd
        q[self.slope_ix] = c.slope_sd ** 2 * (1 - phd * phd)
        return f, q


def _weight(z, kind, robust):
    lo, hi = (2.5, 2.5) if (kind == "race" or robust == "sym") else (1.0, 2.5)
    if z < 0:
        return 1.0 if -z <= lo else (lo / -z) ** 2
    return 1.0 if z <= hi else (hi / z) ** 2


def prepare(cfg):
    """Observation table with y on the configured GAP scale, g, sigma and design vectors."""
    O = OBS.build(include_sub=cfg.include_sub).copy()
    if not cfg.include_sub:
        O = O[O.kind != "sub"]
    if not cfg.use_eff:
        O = O[O.kind != "eff"]
    if cfg.gap == "min":
        O["yv"] = O["y"]; O["g"] = O["g_min"]
    elif cfg.gap == "strava":
        O["yv"] = O["y_raw"] + O["g_str"]; O["g"] = O["g_str"]
    else:  # raw: kappa then multiplies the minetti-half shift (kappa = 1 means minetti-half is right)
        O["yv"] = O["y_raw"]; O["g"] = O["g_min"]
    sig = dict(cfg.sig)
    O["sig"] = [cfg.sig_mult * (sig.get(f"int_{s}", 1.5) if k == "int" else sig[k]) for k, s in zip(O.kind, O.surface)]
    return O.reset_index(drop=True)


class Fit:
    """Kalman filter (+ optional iterated RTS smoothing for the robust weights) on the observations O (sorted by day)."""

    def __init__(self, cfg, O, L=None):
        self.cfg, self.O = cfg, O
        self.L = L or Layout(cfg)
        L = self.L
        self.H = np.array([L.h(r.person, r.x, design_row(r._asdict(), cfg, L.effects)) for r in O.itertuples()]) \
            if len(O) else np.zeros((0, L.n))
        self.y = O["yv"].to_numpy(float); self.s2 = O["sig"].to_numpy(float) ** 2
        self.day = O["day"].to_numpy(int); self.kind = O["kind"].to_numpy()

    def _filter(self, w=None, snapshots=False):
        L, cfg = self.L, self.cfg
        m, P = L.m0.copy(), L.P0.copy()
        days = np.unique(self.day)
        online = w is None
        w = np.ones(len(self.y)) if w is None else w
        self.innov = np.full((len(self.y), 2), np.nan)   # online one-step-ahead (residual, predictive sd)
        store = []    # per obs-day: (day, m_pred, P_pred, m_filt, P_filt, f)
        snaps = {}
        last = None
        for d in days:
            if last is None:
                f, q = np.ones(L.n), np.zeros(L.n)
            else:
                f, q = L.transition(d - last)
            m = f * m; P = (f[:, None] * P * f[None, :]) + np.diag(q)
            mp, Pp = m.copy(), P.copy()
            if snapshots:
                snaps[int(d)] = (mp, Pp)          # state before day d's obs, transitioned to day d
            for j in np.flatnonzero(self.day == d):
                h = self.H[j]
                Ph = P @ h; v = float(h @ Ph); r = self.y[j] - float(h @ m)
                self.innov[j] = (r, math.sqrt(v + self.s2[j]))
                if online:
                    w[j] = _weight(r / math.sqrt(v + self.s2[j]), self.kind[j], cfg.robust)
                S = v + self.s2[j] / max(w[j], 1e-6)
                K = Ph / S
                m = m + K * r; P = P - np.outer(K, Ph); P = 0.5 * (P + P.T)
            store.append((int(d), mp, Pp, m.copy(), P.copy(), f))
            last = d
        return store, w, snaps

    def _smooth(self, store):
        n = len(store)
        ms = [None] * n; Ps = [None] * n
        ms[-1], Ps[-1] = store[-1][3], store[-1][4]
        for k in range(n - 2, -1, -1):
            _, _, _, mf, Pf, _ = store[k]
            _, mp1, Pp1, _, _, f1 = store[k + 1]
            G = np.linalg.solve(Pp1.T, (Pf * f1[None, :]).T).T      # Pf F' Pp1^-1
            ms[k] = mf + G @ (ms[k + 1] - mp1)
            Ps[k] = Pf + G @ (Ps[k + 1] - Pp1) @ G.T
        return {store[k][0]: (ms[k], Ps[k]) for k in range(n)}

    def run(self):
        """Fit on all of O. Returns final (m, P) and the weights; mode smooth iterates the weights on the RTS fit."""
        store, w, _ = self._filter()
        if self.cfg.mode == "smooth" and len(store) > 1:
            for _ in range(self.cfg.iters):
                sm = self._smooth(store)
                res = np.array([self.y[j] - self.H[j] @ sm[self.day[j]][0] for j in range(len(self.y))])
                sd = np.sqrt(self.s2)
                w_new = np.array([_weight(res[j] / sd[j], self.kind[j], self.cfg.robust) for j in range(len(res))])
                if np.allclose(w_new, w, atol=1e-3):
                    break
                w = w_new
                store, _, _ = self._filter(w=w)
        self.store, self.w = store, w
        self.m, self.P = store[-1][3], store[-1][4]
        self.last_day = store[-1][0]
        return self

    def online_snapshots(self):
        """One causal pass with online weights: {day: (m_pred, P_pred)} before each obs-day's observations."""
        _, w, snaps = self._filter(snapshots=True)
        self.w = w
        return snaps

    def effects(self):
        """DataFrame of pooled effects and per-person totals (mean, sd)."""
        L = self.L; rows = []
        for k in L.effects:
            i = L.ix[f"b:{k}"]
            rows.append({"effect": k, "who": "pooled", "mean": self.m[i], "sd": math.sqrt(self.P[i, i])})
            if k in L.pooled:
                for p in PERSONS:
                    j = L.ix[f"u:{p}:{k}"]
                    v = self.P[i, i] + self.P[j, j] + 2 * self.P[i, j]
                    rows.append({"effect": k, "who": p, "mean": self.m[i] + self.m[j], "sd": math.sqrt(v)})
        return pd.DataFrame(rows)

    def contrast(self, a, b, person=None):
        """mean, sd of effect a - effect b (pooled, or the person's total effect)."""
        L = self.L
        c = np.zeros(L.n)
        for k, s in ((a, 1), (b, -1)):
            if k is None:
                continue
            c[L.ix[f"b:{k}"]] += s
            if person and k in L.pooled:
                c[L.ix[f"u:{person}:{k}"]] += s
        return float(c @ self.m), float(math.sqrt(c @ self.P @ c))


# ------------------------------------------------------------------ prediction
def _ghat(O_hist, gap):
    """VDOT GAP shift per m/km of climb (through the origin) from past non-flat observations: g ~ b * climb_per_km."""
    col = {"min": "g_min", "strava": "g_str", "raw": "g_min"}[gap]
    o = O_hist[(~O_hist.flat) & O_hist.climb_per_km.notna() & (O_hist.climb_per_km > 0)]
    if len(o) < 5:
        return {"g_min": 0.1, "g_str": 0.03}
    out = {}
    for c in ("g_min", "g_str"):
        x = o.climb_per_km.clip(upper=30).to_numpy(); y = o[c].to_numpy()
        out[c] = float((x @ y) / (x @ x))
    return out


def predict_from_state(L, cfg, m, P, person, target, O_hist):
    """Prediction (vdot, sd) on the target's own scale (race raw, interval prod score, hard effort minetti GAP)."""
    tier = target["tier"]
    kind = {"race": "race", "interval": "int", "hard_effort": "eff"}[tier]
    km = float(target["km"])
    intent = "race" if kind == "race" else ("intervals" if kind == "int" else
                                              (target["workout"] if target["workout"] in ("tempo", "time_trial") else "hr_hard"))
    surf = target["surface"]
    flat = bool(target.get("flat_course")) or surf in ("track", "indoor_track", "treadmill")
    climb = target.get("climb_per_km")
    climb = 0.0 if flat or climb is None or climb != climb else min(float(climb), 30.0)
    gb = _ghat(O_hist, cfg.gap)
    g_min, g_str = gb["g_min"] * climb, gb["g_str"] * climb
    g_var = {"min": g_min, "strava": g_str, "raw": g_min}[cfg.gap]
    # evidence range in x for this person
    oh = O_hist[O_hist.person == person]
    xs = oh.x.to_numpy() if len(oh) else np.array([0.0])
    lo, hi = float(xs.min()), float(xs.max())
    x = math.log2(km / 5.0)
    slope = m[L.ix[f"slope:{person}"]]
    xm = min(x, hi) if slope > 0 else x
    out = max(lo - x, x - hi, 0.0)
    X = design_row({"kind": kind, "surface": surf, "intent": intent, "g": g_var, "temp": target.get("wrist_temp_max")},
                   cfg, L.effects)
    h = L.h(person, xm, X)
    mu = float(h @ m); v = float(h @ P @ h)
    # convert from the model's observation scale to the target's scale
    if cfg.gap == "raw":
        y_raw = mu
    else:
        y_raw = mu - g_var
    target_scale = {"race": y_raw, "int": y_raw + g_min, "eff": y_raw + g_min}[kind]
    sig = dict(cfg.sig)
    s_obs = cfg.race_sd if kind == "race" else (sig.get(f"int_{surf}", 1.5) if kind == "int" else sig["eff"])
    sd = math.sqrt(max(v, 0) + s_obs ** 2 + (cfg.extrap_sd * out) ** 2)
    return target_scale, sd


class Predictor:
    """predict_fn for the harness. mode online: one causal pass, snapshot per day. mode smooth: refit (with weights
    iterated on the RTS fit) on all observations of days < target day, cached per day."""

    def __init__(self, cfg=Config()):
        self.cfg = cfg
        self.O = prepare(cfg)
        self.L = Layout(cfg)
        if cfg.mode == "online":
            self.fit = Fit(cfg, self.O, self.L)
        self._cache = {}
        self.__name__ = f"g2[{cfg.gap},{cfg.robust},{cfg.mode}]"

    def state_at(self, day):
        """(m, P, O_hist) from observations with day < `day`, transitioned to day - 1."""
        if day in self._cache:
            return self._cache[day]
        O_hist = self.O[self.O.day < day]
        if O_hist.empty:
            return None
        if self.cfg.mode == "online":
            last = int(O_hist.day.max())
            m, P = self._filtered_after(last)
            fv, q = self.L.transition(day - 1 - last)
            m = fv * m; P = fv[:, None] * P * fv[None, :] + np.diag(q)
        else:
            fit = Fit(self.cfg, O_hist.reset_index(drop=True), self.L).run()
            fv, q = self.L.transition(day - 1 - fit.last_day)
            m = fv * fit.m; P = fv[:, None] * fit.P * fv[None, :] + np.diag(q)
        self._cache[day] = (m, P, O_hist)
        return self._cache[day]

    def _filtered_after(self, d):
        if not hasattr(self, "_filt"):
            store, _, _ = self.fit._filter()
            self._filt = {s[0]: (s[3], s[4]) for s in store}
        return self._filt[int(d)]

    def __call__(self, person, hist, target):
        st = self.state_at(int(hist.day))
        if st is None:
            return None
        m, P, O_hist = st       # a person without history gets the prior (theta0 +- theta0_sd) plus pooled effects
        v, sd = predict_from_state(self.L, self.cfg, m, P, person, target, O_hist)
        if target["tier"] == "hard_effort" and not self.cfg.use_eff:
            off, s_off = self.eff_offset(target, int(hist.day))
            v += off; sd = math.sqrt(sd * sd + s_off * s_off)
        return {"vdot": v, "sd": sd}

    def eff_offset(self, target, day, n0=3.0):
        """Two-stage intent offset for hard-effort targets when hard efforts are kept out of the fitness fit: shrunk mean
        residual (prior 0 with n0 pseudo-observations) of past hard efforts of the same intent and surface class (all
        persons, days < day) against the core model's prediction made before each of them."""
        intent = target["workout"] if target["workout"] in ("tempo", "time_trial") else "hr_hard"
        road = target["surface"] not in ("track", "indoor_track")
        if not hasattr(self, "_effres"):
            E = OBS.build(include_sub=False)
            E = E[E.kind == "eff"].copy()
            res = []
            for r in E.itertuples():
                st = self.state_at(int(r.day))
                if st is None:
                    res.append(np.nan); continue
                tgt = {"tier": "hard_effort", "km": r.km, "workout": r.intent if r.intent != "hr_hard" else "easy",
                       "surface": r.surface, "flat_course": r.flat, "climb_per_km": r.climb_per_km,
                       "wrist_temp_max": r.temp}
                p, _ = predict_from_state(self.L, self.cfg, st[0], st[1], r.person, tgt, st[2])
                res.append(r.y - p)
            E["res"] = res
            E["road"] = ~E.surface.isin(["track", "indoor_track"])
            self._effres = E.dropna(subset=["res"])
        E = self._effres
        e = E[(E.day < day) & (E.intent == intent) & (E.road == road)]
        s = float(e.res.sum()) if len(e) else 0.0
        n = len(e)
        sd_e = 3.0
        return s / (n + n0), sd_e / math.sqrt(n + n0)


# ------------------------------------------------------------------ entry points for G9 (ensembling)
_PRED = {}


def predict(person, hist, target):
    """G2 recommended predict_fn: production curve on production evidence with the time-forward G2 heat correction
    (prodcorr_heat). Races / intervals / hard efforts."""
    if "heat" not in _PRED:
        from prodcorr import ProdCorr
        _PRED["heat"] = ProdCorr(corr=("heat",), name="prodcorr_heat")
    return _PRED["heat"](person, hist, target)


def predict_core(person, hist, target):
    """G2's own pooled state-space model without hard efforts in the fitness fit, with two-stage intent offsets for
    hard-effort targets (g2_core; best on the hard_effort tier, worse on races)."""
    if "core" not in _PRED:
        _PRED["core"] = Predictor(replace(Config(), use_eff=False, effects=("race", "int_road", "int_indoor", "kappa",
                                                                            "heat"),
                                          pooled=("race", "int_road", "int_indoor", "heat")))
    return _PRED["core"](person, hist, target)

"""G4 training-load response models. Exposes `predict(person, hist, target)` (the recommended variant) and the
factory `make(...)` for the variants in REPORT.md.

Load regressors u(day) are EWMAs (CTL/ATL-style, load of days strictly before `day`, units per UNIT[metric] per day)
of run load + ski * ski_load + other * other_load (load.py). Variants:
  - kalman+load (b): production curve filter + static load coefficients beta (prior N(mu, sd), learned time-forward
    per person): y = theta_RW + x*slope + sum beta_i u_i(day)
  - mean-reverting (b'): theta = c + d, d AR(1) (time constant ar_tau), so in gaps the estimate decays towards
    c + beta*u (the load-implied level)
  - banister (a): pure impulse-response p = p0 + k1*g(tau1) - k2*h(tau2) (+ slope*x) as a robust Bayesian regression
    (= the filter with q_theta ~ 0 and an exponential forgetting window via a small q)
  - taper (c): + gamma * TSB (ctl42 - atl7) on race / hard-effort targets only
"""
from __future__ import annotations

import functools
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[1]))
import kf  # noqa: E402
import load as L  # noqa: E402

UNIT = {"trimp": 10.0, "km": 1.0, "h": 0.1}   # u = ewma / UNIT: beta in VDOT per (10 TRIMP | 1 km | 0.1 h) per day


def make(taus=(42,), beta_mu=None, beta_sd=None, metric="trimp", ski=0.5, other=0.25, q_theta=None, ar_tau=None,
         ar_sd=3.0, taper=0.0, taper_metric=None, name=None):
    taus = tuple(taus)
    beta_mu = tuple(beta_mu) if beta_mu is not None else (0.0,) * len(taus)
    beta_sd = tuple(beta_sd) if beta_sd is not None else (0.5,) * len(taus)
    tm = taper_metric or metric

    def regs(person):
        out = []
        for t in taus:
            if isinstance(t, tuple):     # ("busso", tau2, tau3): variable-gain fatigue, in (UNIT)^2
                d0, arr = L.busso(person, t[1], t[2], metric=metric, ski=ski, other=other)
                out.append(lambda d, d0=d0, arr=arr: float(arr[min(max(int(d) - d0, 0), len(arr) - 1)]) / UNIT[metric] ** 2)
            else:
                out.append(lambda d, t=t: L.at(person, d, t, metric=metric, ski=ski, other=other) / UNIT[metric])
        return out

    @functools.lru_cache(maxsize=4096)
    def state(person, day):
        obs = kf.prod_obs(person, day)
        if not obs:
            return None
        st = kf.run_filter(obs, regs(person), beta_mu, beta_sd, q_theta=q_theta, ar_tau=ar_tau, ar_sd=ar_sd,
                           read_day=day - 1)
        return st, kf.evidence_range(obs, st["w"])

    def predict(person, hist, target):
        s = state(person, hist.day)
        if s is None:
            return None
        st, ekm = s
        u = np.array([f(hist.day) for f in regs(person)])
        th, sl, vt, vs, cv = kf.readout(st, u)
        if taper and target["tier"] in ("race", "hard_effort"):
            tsb = (L.at(person, hist.day, 42, metric=tm, ski=ski, other=other) -
                   L.at(person, hist.day, 7, metric=tm, ski=ski, other=other)) / UNIT[tm]
            th += taper * tsb
        p = kf.FM.curve_predict(th, sl, vt, vs, cv, float(target["km"]), ekm)
        return {"vdot": p["vdot"], "sd": p["sd"]}

    predict.__name__ = name or f"g4_{metric}_{'-'.join(map(str, taus))}"
    predict.state = state
    return predict


def beta_path(pred_fn, person, days):
    """Posterior mean / sd of the load coefficients at each day (time-forward)."""
    out = []
    for d in days:
        s = pred_fn.state(person, d)
        if s is None:
            continue
        st = s[0]; ib = st["ib"]
        out.append((d, st["x"][ib:].copy(), np.sqrt(np.diag(st["P"])[ib:])))
    return out


# G4 representative for ensembling (G9): production curve + CTL42 (TRIMP, ski x0.5, other x0.25) regressor,
# beta ~ N(0, 1) per person, learned time-forward. Not better than production beyond noise (REPORT.md).
predict = make(taus=(42,), beta_sd=(1.0,), name="g4_kalman_ctl42")

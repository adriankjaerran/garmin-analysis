"""G4: event-based robust Kalman filter + RTS smoother for the production distance curve, extended with
training-load regressors (static coefficients learned time-forward) and an optional mean-reverting theta.

State: [theta (RW, or AR deviation d), slope (AR(1) to 0, production), (c: level the AR deviation reverts to),
beta_1..beta_m (static load coefficients)]. Observation: y = theta + x*slope (+ c) + sum_i beta_i * u_i(day).
With no regressors and no AR it reproduces scripts/fitness_model.smooth_curve (production model_version 3):
same IRLS asymmetric robust weights (pass 0 from the one-step forecast, later passes from the smoothed curve).
"""
from __future__ import annotations

import functools
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
import common as C  # noqa: E402
import harness as H  # noqa: E402

FM = C.import_production()


@functools.lru_cache(maxsize=4096)
def prod_obs(person, day):
    """Production evidence (as harness._prod_state) of days < day: tuple of (day, score, sigma, kind, x)."""
    a = H.history(person, day).acts
    a = a[a.prod_discipline.isin(["interval", "long"]) & a.prod_kind.isin(["hard", "race", "bound"]) &
          a.prod_km.notna() & a.prod_score.notna()]
    return tuple(sorted(((int(r.day), float(r.prod_score), float(r.prod_sigma), r.prod_kind, FM.curve_x(float(r.prod_km)))
                         for r in a.itertuples()), key=lambda o: o[0]))


def run_filter(obs, regs=(), beta_mu=(), beta_sd=(), q_theta=None, ar_tau=None, ar_sd=3.0, iters=5, read_day=None,
               regs_read=None):
    """obs: sorted (day, y, sigma, kind, x). regs: list of callables day -> u. Returns dict with the state mean / cov at
    read_day (transitioned from the last obs), weights, and h-vectors for the read-out."""
    q_t = FM.CURVE["q_theta"] if q_theta is None else q_theta
    q_s = FM.CURVE["q_slope"]; s_sd = FM.CURVE["slope_prior_sd"]
    ph = math.sqrt(1 - q_s / s_sd ** 2)
    prior_sd = FM.P["prior_sd"]
    m = len(regs); ar = ar_tau is not None
    n = 2 + (1 if ar else 0) + m
    ib = 2 + (1 if ar else 0)
    days = sorted({o[0] for o in obs})
    byday = {}
    for j, o in enumerate(obs):
        byday.setdefault(o[0], []).append(j)
    U = {d: np.array([f(d) for f in regs]) for d in days}
    good = [o[1] for o in obs if o[3] != "bound"] or [o[1] for o in obs]
    good_d = [o[0] for o in obs if o[3] != "bound"][:5] or [obs[0][0]]
    bmu = np.asarray(beta_mu, float); bsd = np.asarray(beta_sd, float)
    m0 = float(np.median(good[:5]))
    if m:   # level net of the prior-mean load effect at the first evidence
        m0 -= float(np.mean([bmu @ U[d] if d in U else bmu @ np.array([f(d) for f in regs]) for d in good_d]))
    x0 = np.zeros(n); P0 = np.zeros((n, n))
    if ar:
        phd = math.exp(-1.0 / ar_tau)
        x0[0] = 0.0; P0[0, 0] = ar_sd ** 2; x0[2] = m0; P0[2, 2] = prior_sd ** 2
    else:
        x0[0] = m0; P0[0, 0] = prior_sd ** 2
    P0[1, 1] = s_sd ** 2
    if m:
        x0[ib:] = bmu; P0[ib:, ib:] = np.diag(bsd ** 2)

    def trans(dt):
        F = np.eye(n); Q = np.zeros((n, n))
        F[1, 1] = ph ** dt; Q[1, 1] = s_sd ** 2 * (1 - ph ** (2 * dt))
        if ar:
            F[0, 0] = phd ** dt; Q[0, 0] = ar_sd ** 2 * (1 - phd ** (2 * dt))
        else:
            Q[0, 0] = q_t * dt
        return F, Q

    def hvec(d, x):
        h = np.zeros(n); h[0] = 1; h[1] = x
        if ar:
            h[2] = 1
        if m:
            h[ib:] = U[d]
        return h

    w = np.ones(len(obs))
    for it in range(iters):
        xs_f, Ps_f, xs_p, Ps_p, Fs = [], [], [], [], []
        x, P = x0.copy(), P0.copy()
        prev = None
        for d in days:
            if prev is not None:
                F, Q = trans(d - prev)
                x = F @ x; P = F @ P @ F.T + Q
            else:
                F = np.eye(n)
            xs_p.append(x.copy()); Ps_p.append(P.copy()); Fs.append(F)
            for j in byday[d]:
                _, y, s, kind, xx = obs[j]
                h = hvec(d, xx)
                pred = h @ x; vx = h @ P @ h
                if it == 0:
                    w[j] = FM._weight(y - pred, math.sqrt(s * s + vx), kind)
                S = vx + s * s / max(w[j], 1e-6)
                K = P @ h / S
                x = x + K * (y - pred); P = P - np.outer(K, h @ P)
            xs_f.append(x.copy()); Ps_f.append(P.copy())
            prev = d
        xs_s = [None] * len(days); xs_s[-1] = xs_f[-1]
        for k in range(len(days) - 2, -1, -1):
            G = Ps_f[k] @ Fs[k + 1].T @ np.linalg.inv(Ps_p[k + 1])
            xs_s[k] = xs_f[k] + G @ (xs_s[k + 1] - xs_p[k + 1])
        di = {d: k for k, d in enumerate(days)}
        neww = np.array([FM._weight(o[1] - hvec(o[0], o[4]) @ xs_s[di[o[0]]], o[2], o[3]) for o in obs])
        if it and np.allclose(neww, w):
            break
        w = neww
    x, P = xs_f[-1], Ps_f[-1]
    if read_day is not None and read_day > days[-1]:
        F, Q = trans(read_day - days[-1])
        x = F @ x; P = F @ P @ F.T + Q
    return {"x": x, "P": P, "w": w, "n": n, "ar": ar, "ib": ib, "m": m}


def readout(st, u_target):
    """(theta_eff, slope, var_theta, var_slope, cov) at the target with regressor values u_target."""
    n = st["n"]; h0 = np.zeros(n); h0[0] = 1
    if st["ar"]:
        h0[2] = 1
    if st["m"]:
        h0[st["ib"]:] = u_target
    h1 = np.zeros(n); h1[1] = 1
    x, P = st["x"], st["P"]
    return float(h0 @ x), float(x[1]), float(h0 @ P @ h0), float(h1 @ P @ h1), float(h0 @ P @ h1)


def evidence_range(obs, w):
    ev = [2 ** o[4] * FM.CURVE["ref_km"] for o, wt in zip(obs, w) if o[3] in ("race", "hard") and wt > 0.5]
    return (min(ev), max(ev)) if ev else None

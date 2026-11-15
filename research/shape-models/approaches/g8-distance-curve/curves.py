"""G8: personal speed-duration curves with a time-varying level.

Every evidence item is an equivalent all-out performance (distance d, time T) (production per-session evidence,
re-derived from the 1 Hz series per GAP variant: see features.py):
  race      official distance + time
  interval  W'-balance session score S (+ interval_offset -0.5) placed at 5 km (production) or at its work duration
  tempo     best window VDOT + 3.0 (tempo_offset) at 14.5 km (production convention)
  bound     best 20-90 min window (lower bound), at its own GAP distance

Curve families (shape psi, level L_t a random walk; fixed shape = psi constant, posterior on a grid; slowly varying
shape = the psi likelihood discounted with a half-life):
  flat    VDOT(d) = L                                         (Daniels' default, no personal curve)
  prod    VDOT(d) = L + s log2(d / 5 km)                      (production's curve; here with a grid posterior on s)
  riegel  ln v(T) = L - b ln(T / 1200 s)                      (personal Riegel exponent k = 1 / (1 - b))
  cs2     v(T)   = CS (1 + tau / T)                           (critical speed + D' = CS * tau, 2-parameter)
  pt      v(T)   = c (1 - E ln(T / 420 s) + tau / T)          (Peronnet-Thibault-type 3-parameter: aerobic level,
                                                               endurance index E (long), anaerobic reserve tau (short))
plus `prodK` = production's own 2-state (theta, slope OU) Kalman smoother (fitness_model.smooth_curve) run on the
same evidence (to test GAP / placement variants with the production filter).

Leakage: every prediction is computed from hist (rows with day < target day) only.
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
import common as C  # noqa: E402

fm = C.import_production()
FEATS = C.OUT / "g8" / "session_feats.parquet"
TREF, TC = 1200.0, 420.0
INT_OFF, TEMPO_OFF = fm.P["interval_offset"], fm.P["tempo_offset"]
TEMPO_KM = 5.0 * 2 ** np.mean([math.log2(10 / 5), math.log2(21.0975 / 5)])
Q_VDOT = fm.CURVE["q_theta"]            # VDOT^2 / day
RACE_SD = fm.CURVE["race_sd"]
EXTRAP_SD = fm.CURVE["extrap_sd_per_doubling"]
WIN = (1200, 1800, 2700, 3600, 5400)


@functools.lru_cache(maxsize=1)
def _feats():
    F = pd.read_parquet(FEATS)
    return F.set_index("activity_id")


# ------------------------------------------------------------------ evidence
def evidence(acts, gap="mh", place="5k", dpm=None):
    """Evidence items from activity rows (hist.acts). Returns DataFrame day, kind, d, T, sigma, src, + covariates."""
    F = _feats()
    a = acts[acts.prod_discipline.isin(["interval", "long"]) & acts.prod_kind.isin(["hard", "race", "bound"]) &
             acts.prod_score.notna()]
    rows = []
    for r in a.itertuples():
        aid = str(r.activity_id)
        f = F.loc[aid] if aid in F.index else None
        base = {"day": int(r.day), "aid": aid, "sigma": float(r.prod_sigma), "terrain": r.terrain,
                "climb_per_km": (r.climb_m / r.dist_km) if r.dist_km else np.nan, "surface": r.surface,
                "flat_course": bool(r.flat_course)}
        if r.prod_kind == "race":
            if r.prod_discipline == "interval" and r.is_official_race is False:
                pass
            km = float(r.race_km) if r.race_km == r.race_km and r.race_km else float(r.prod_km)
            T = float(r.official_s) if r.official_s == r.official_s and r.official_s else float(C.race_time(r.prod_score, km * 1000))
            rows.append({**base, "kind": "race", "src": "race", "d": km * 1000, "T": T})
        elif r.prod_discipline == "interval":
            if f is None:
                continue
            if dpm is not None and dpm != 1.0:
                s = f.get(f"wb_dp{dpm}")
            else:
                s = f.get(f"wb_{gap}")
            if s is None or not np.isfinite(s):
                continue
            s = float(s) + INT_OFF
            if place == "5k":
                d = 5000.0
            else:      # work duration: Daniels distance at score s held for the seconds above CS
                tw = float(np.clip(f.get("sec_above_cs") or 1000.0, 300, 3000))
                d = float(C.speed_for(s, tw) * tw)
            rows.append({**base, "kind": "hard", "src": "interval", "d": d, "T": float(C.race_time(s, d)),
                         "rep_s": r.prod_rep_s, "rest_s": r.prod_rest_s})
        else:   # long: tempo (hard) or bound
            if f is None:
                continue
            best = None
            for D in WIN:
                v = f.get(f"win{D}_{gap}")
                if v is None or not np.isfinite(v):
                    continue
                vd = float(C.vdot(v * D, D))
                if best is None or vd > best[0]:
                    best = (vd, D, v)
            if best is None:
                continue
            vd, D, v = best
            if r.prod_kind == "hard":
                s = vd + TEMPO_OFF
                d = TEMPO_KM * 1000
                rows.append({**base, "kind": "hard", "src": "tempo", "d": d, "T": float(C.race_time(s, d))})
            else:
                rows.append({**base, "kind": "bound", "src": "bound", "d": v * D, "T": float(D)})
    E = pd.DataFrame(rows)
    if E.empty:
        return E
    E["vdot"] = C.vdot(E.d.to_numpy(), E["T"].to_numpy())
    return E.sort_values(["day", "kind"]).reset_index(drop=True)


# ------------------------------------------------------------------ families
def _daniels_fit():
    """Shape parameters of each family fitted to Daniels' curve (VDOT 45 / 52 / 60), log-speed least squares, over the
    5k-half range (15-100 min) for riegel / cs2 and over 5-100 min for pt (its anaerobic term needs the short end)."""
    from scipy.optimize import least_squares
    out = {}
    V = np.array([45.0, 52.0, 60.0])

    def fit(fun, x0, t0):
        T = np.geomspace(t0, 6000, 40)
        lv = np.log(np.concatenate([C.speed_for(v, T) for v in V]))
        TT = np.tile(T, len(V)); grp = np.repeat(np.arange(len(V)), len(T))

        def res(p):
            return p[:len(V)][grp] + fun(TT, *p[len(V):]) - lv
        return least_squares(res, np.r_[np.zeros(len(V)) + 1.3, x0]).x[len(V):]
    out["riegel"] = fit(lambda t, b: -b * np.log(t / TREF), [0.07], 900)
    out["cs2"] = fit(lambda t, tau: np.log1p(abs(tau) / t) - np.log1p(abs(tau) / TREF), [40.0], 900)
    out["pt"] = fit(lambda t, E, tau: np.log(_pt_rho(t, E, tau)), [0.06, 30.0], 300)
    return out


def _pt_rho(T, E, tau):
    T = np.asarray(T, float)
    num = 1 - E * np.log(np.maximum(T, TC) / TC) + tau / T
    den = 1 - E * np.log(TREF / TC) + tau / TREF
    return num / den


DANIELS = _daniels_fit()
# VDOT -> ln speed conversion at a 10k effort around VDOT 52 (for priors / q)
_K = float(np.log(C.speed_for(52.5, 2400) / C.speed_for(51.5, 2400)))


def family(name, prior_sd_slope=1.5):
    """(space, grid (n_grid, n_par), log-prior per grid point, f(P, d, T)) with f broadcasting over P[..., j] /
    d / T. The prior on psi is equivalent to production's slope prior N(0, prior_sd_slope^2) VDOT per doubling."""
    db_sd = prior_sd_slope * _K / math.log(2)     # slope (VDOT / doubling) <-> exponent change: db = s K / ln 2
    if name == "flat":
        return "vdot", np.zeros((1, 1)), np.zeros(1), lambda P, d, T: 0.0 * P[..., 0] * np.ones_like(d, float)
    if name.startswith("prod"):     # "prod" or "prod+1.0": prior mean of the slope (shared population curve)
        mu = float(name[4:]) if len(name) > 4 else 0.0
        g = np.arange(-5, 5.01, 0.25)[:, None]
        return "vdot", g, -0.5 * ((g[:, 0] - mu) / prior_sd_slope) ** 2, lambda P, d, T: np.log2(d / 5000) * P[..., 0]
    if name == "riegel":
        b0 = DANIELS["riegel"][0]
        g = (b0 + np.linspace(-4, 4, 41) * db_sd)[:, None]
        return "lnv", g, -0.5 * ((g[:, 0] - b0) / db_sd) ** 2, lambda P, d, T: -np.log(T / TREF) * P[..., 0]
    if name == "cs2":
        t0 = abs(DANIELS["cs2"][0])
        g = (t0 * np.exp(np.linspace(-2.5, 2.5, 41)))[:, None]
        return "lnv", g, -0.5 * (np.log(g[:, 0] / t0) / 0.7) ** 2, \
            lambda P, d, T: np.log1p(P[..., 0] / T) - np.log1p(P[..., 0] / TREF)
    if name == "pt":
        E0, tau0 = DANIELS["pt"]
        tau0 = max(tau0, 5.0)
        Es = E0 + np.linspace(-3, 3, 21) * db_sd
        taus = tau0 * np.exp(np.linspace(-2, 2, 15))
        g = np.array([(e, t) for e in Es for t in taus])
        lp = -0.5 * ((g[:, 0] - E0) / db_sd) ** 2 - 0.5 * (np.log(g[:, 1] / tau0) / 0.7) ** 2
        return "lnv", g, lp, lambda P, d, T: np.log(_pt_rho(T, P[..., 0], P[..., 1]))
    raise ValueError(name)


def _wvec(rv, sig, kind):
    """production _weight, vectorised over the grid."""
    lo, hi = fm.ROBUST[kind]
    z = rv / sig
    w = np.ones_like(z)
    if kind == "bound":
        w = np.where(z <= fm.BOUND_Z, 1e-6, np.where(z <= hi, 1.0, (hi / np.maximum(z, 1e-9)) ** 2))
        return w
    w = np.where(z < 0, np.where(-z <= lo, 1.0, (lo / np.maximum(-z, 1e-9)) ** 2),
                 np.where(z <= hi, 1.0, (hi / np.maximum(z, 1e-9)) ** 2))
    return w


def _t_logpdf(z, nu=4.0):
    from scipy.special import gammaln
    return gammaln((nu + 1) / 2) - gammaln(nu / 2) - 0.5 * math.log(nu * math.pi) - (nu + 1) / 2 * np.log1p(z * z / nu)


def run_filter(E, fam, q_scale=1.0, obs_space="distance"):
    """Forward robust Kalman filter of the level for every grid psi (vectorised over the grid). Every item is
    observed as the VDOT at its distance d (obs_space "distance": the model's time for d is solved at the current
    level, extended Kalman step; fixed-distance items must not use their own observed time as the regressor, see
    REPORT "errors in variables"). obs_space "time" (lnv families only): ln v observed at the item's own T (biased,
    kept for the record). Returns final m, P (per grid), ll (n_items, n_grid; race / hard items only)."""
    space, G, lp, h = fam
    d, T = E.d.to_numpy(), E["T"].to_numpy()
    yv = E.vdot.to_numpy()
    q = Q_VDOT * q_scale * (1.0 if space == "vdot" else _K ** 2)
    sig = E.sigma.to_numpy()
    kinds = E.kind.to_numpy(); days = E.day.to_numpy()
    ng = G.shape[0]
    lin_time = space == "lnv" and obs_space == "time"
    if lin_time:
        Hm = h(G[None, :, :], d[:, None], T[:, None])
        kk = np.log(C.speed_for(yv + 0.5, T) / C.speed_for(yv - 0.5, T))
    good = np.nonzero(kinds != "bound")[0][:5]
    if not len(good):
        good = np.arange(min(5, len(E)))
    if space == "vdot":
        m = np.median(yv[good, None] - h(G[None, :, :], d[good, None], T[good, None]), axis=0)
    else:   # level = ln speed at TREF: invert at the first good items (time-based start is fine for the start value)
        m = np.median(np.log(d[good] / T[good])[:, None] - h(G[None, :, :], d[good, None], T[good, None]), axis=0)
    P = np.full(ng, (fm.P["prior_sd"] * (1.0 if space == "vdot" else _K)) ** 2)
    ll = np.zeros((len(E), ng))
    resid = np.zeros((len(E), ng))
    last = days[0]
    eps = 1e-3 if space == "lnv" else 0.1
    for i in range(len(E)):
        P = P + q * (days[i] - last); last = days[i]
        if lin_time:
            pred = (m + Hm[i]) / kk[i]; Hj = np.full(ng, 1 / kk[i]); y = np.log(d[i] / T[i]) / kk[i]
        else:
            pred = _vdot_at(space, h, G, m, d[i])
            Hj = (_vdot_at(space, h, G, m + eps, d[i]) - pred) / eps if space == "lnv" else np.ones(ng)
            y = yv[i]
        r = y - pred
        S = Hj * Hj * P + sig[i] ** 2
        sdv = np.sqrt(S)
        resid[i] = r
        w = _wvec(r, sdv, kinds[i])
        if kinds[i] != "bound":
            ll[i] = _t_logpdf(r / sdv) - np.log(sdv)
        Sw = Hj * Hj * P + sig[i] ** 2 / np.maximum(w, 1e-6)
        K = P * Hj / Sw
        m = m + K * r
        P = P - K * Hj * P
    return {"m": m, "P": P, "last": last, "ll": ll, "days": days, "resid": resid, "space": space, "G": G, "lp": lp,
            "h": h, "kinds": kinds, "d": d}


def _vdot_at(fam_space, h, G, m, d):
    """VDOT at distance d for each grid point, level m (array over grid)."""
    n = G.shape[0]
    if fam_space == "vdot":
        return m + h(G, np.full(n, float(d)), np.full(n, np.nan))
    lo = np.full(n, math.log(d / 8.0)); hi = np.full(n, math.log(d / 1.5))
    dd = np.full(n, float(d))
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        T = np.exp(mid)
        far = np.exp(m + h(G, dd, T)) * T > d
        hi = np.where(far, mid, hi); lo = np.where(far, lo, mid)
    T = np.exp(0.5 * (lo + hi))
    return C.vdot(dd, T)


def posterior(F, day, half_life=None, temper=1.0):
    lp = F["lp"].copy()
    if half_life:
        wdec = 0.5 ** ((day - F["days"]) / half_life)
        lp = lp + temper * (wdec[:, None] * F["ll"]).sum(0)
    else:
        lp = lp + temper * F["ll"].sum(0)
    w = np.exp(lp - lp.max())
    return w / w.sum()


def predict_from(F, day, km, half_life=None, temper=1.0, cap=True, dmax=None, dmin=None):
    """Mixture prediction (VDOT mean, sd) at km from a filtered state F (items before `day`)."""
    w = posterior(F, day, half_life, temper)
    keep = w > 1e-4 * w.max()
    G, m, P = F["G"][keep], F["m"][keep], F["P"][keep]
    P = P + Q_VDOT * (1.0 if F["space"] == "vdot" else _K ** 2) * (day - 1 - F["last"])
    d = km * 1000
    v = _vdot_at(F["space"], F["h"], G, m, d)
    eps = math.sqrt(1e-4) if F["space"] == "lnv" else 0.01
    dv = (_vdot_at(F["space"], F["h"], G, m + eps, d) - v) / eps
    sd_state = np.abs(dv) * np.sqrt(P)
    out = 0.0
    if dmax is not None and d > dmax * 1.001:
        out = math.log2(d / dmax)
        if cap:
            vm = _vdot_at(F["space"], F["h"], G, m, dmax)
            v = np.where(v > vm, vm, v)
    if dmin is not None and d < dmin * 0.999:
        out = max(out, math.log2(dmin / d))
    ww = w[keep] / w[keep].sum()
    mean = float((ww * v).sum())
    var_mix = float((ww * (sd_state ** 2 + (v - mean) ** 2)).sum())
    sd = math.sqrt(var_mix + RACE_SD ** 2 + (EXTRAP_SD * out) ** 2)
    return mean, sd


def psi_summary(F, day, half_life=None, temper=1.0):
    """Posterior mean / sd of each shape parameter, and of the implied VDOT(HM) - VDOT(5k) at the current level."""
    w = posterior(F, day, half_life, temper)
    G = F["G"]
    mean = (w[:, None] * G).sum(0); sd = np.sqrt((w[:, None] * (G - mean) ** 2).sum(0))
    keep = w > 1e-4 * w.max()
    v5 = _vdot_at(F["space"], F["h"], G[keep], F["m"][keep], 5000)
    vh = _vdot_at(F["space"], F["h"], G[keep], F["m"][keep], 21097.5)
    v15 = _vdot_at(F["space"], F["h"], G[keep], F["m"][keep], 1500)
    ww = w[keep] / w[keep].sum()
    dl = vh - v5; ds = v15 - v5
    return {"psi_mean": mean, "psi_sd": sd,
            "long_minus_5k": float((ww * dl).sum()), "long_minus_5k_sd": float(np.sqrt((ww * (dl - (ww * dl).sum()) ** 2).sum())),
            "short_minus_5k": float((ww * ds).sum()), "short_minus_5k_sd": float(np.sqrt((ww * (ds - (ww * ds).sum()) ** 2).sum())),
            "v5": float((ww * v5).sum())}


# ------------------------------------------------------------------ harness predict functions
def evidence_range(E):
    x = E[E.kind.isin(["race", "hard"])]
    return (float(x.d.min()), float(x.d.max())) if len(x) else (None, None)


@functools.lru_cache(maxsize=20000)
def _state(person, day, famname, gap, place, q_scale, dpm=None, obs_space="distance"):
    import harness as Hh
    h = Hh.history(person, day)
    E = evidence(h.acts, gap=gap, place=place, dpm=dpm)
    if E.empty or not (E.kind != "bound").any():
        return None
    F = run_filter(E, family(famname), q_scale, obs_space)
    F["range"] = evidence_range(E)
    return F


def make_predict(famname, gap="mh", place="5k", half_life=None, temper=1.0, cap=True, q_scale=1.0, dpm=None,
                 obs_space="distance"):
    def predict(person, hist, target):
        F = _state(person, hist.day, famname, gap, place, q_scale, dpm, obs_space)
        if F is None:
            return None
        dmin, dmax = F["range"]
        v, sd = predict_from(F, hist.day, float(target["km"]), half_life, temper, cap, dmax, dmin)
        return {"vdot": v, "sd": sd}
    predict.__name__ = f"{famname}_{gap}_{place}_hl{half_life}_t{temper}_cap{int(cap)}" + (f"_dp{dpm}" if dpm else "") + \
        ("" if obs_space == "distance" else "_obsT")
    return predict


# production 2-state filter on our evidence (GAP / placement variants)
@functools.lru_cache(maxsize=20000)
def _prodk_state(person, day, gap, place, q_slope=None, slope_sd=None):
    import harness as Hh
    h = Hh.history(person, day)
    E = evidence(h.acts, gap=gap, place=place)
    if E.empty:
        return None
    obs = [(int(r.day), float(r.vdot), float(r.sigma), r.kind, fm.curve_x(r.d / 1000)) for r in E.itertuples()]
    d0 = min(o[0] for o in obs)
    days, M, Cv, w, _ = fm.smooth_curve(obs, d0, day - 1, q_slope=q_slope, slope_sd=slope_sd)
    obs_s = sorted(obs, key=lambda o: o[0])
    ev = [2 ** o[4] * 5.0 for o, wt in zip(obs_s, w) if o[3] in ("race", "hard") and wt > 0.5]
    return M[-1], Cv[-1], ((min(ev), max(ev)) if ev else None)


def make_prodk(gap="mh", place="5k", q_slope=None, slope_sd=None, cap=True):
    def predict(person, hist, target):
        st = _prodk_state(person, hist.day, gap, place, q_slope, slope_sd)
        if st is None:
            return None
        m, cv, ekm = st
        if not cap and ekm:
            ekm = (ekm[0], 1e9)
        p = fm.curve_predict(m[0], m[1], cv[0, 0], cv[1, 1], cv[0, 1], float(target["km"]), ekm)
        return {"vdot": float(p["vdot"]), "sd": float(p["sd"])}
    predict.__name__ = f"prodK_{gap}_{place}" + (f"_qs{q_slope}" if q_slope is not None else "") + \
        (f"_sd{slope_sd}" if slope_sd is not None else "") + ("" if cap else "_nocap")
    return predict

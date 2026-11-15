"""G7: smoothed fitness estimators that use the learned p(maximal) / shortfall per run, evaluated with the harness.

Estimators (all on the production 2-state distance curve, fm.smooth_curve + fm.curve_predict, unless noted):
  g7_rules       : G7 candidate set with production-like rules (int hard; tempo +3 hard; other runs bounds) - control
  g7_p           : candidates with p >= PMIN are 'hard' obs with sigma / sqrt(p), y corrected by the learned
                   shortfall E[r | maximal, x] (bias); p < PMIN -> lower bound (sigma 2.5)
  g7_p_nobias / g7_p_nobound / g7_soft (every candidate hard with R = sigma^2 / p) / g7_roll (weighted rolling WLS)
  prod_drop_*    : production evidence ablations (no bounds, no hilly road intervals, no GPS-glitch sessions)

    /tmp/shape-venv/bin/python research/shape-models/approaches/g7-run-weighting/model.py
"""
from __future__ import annotations

import functools
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[1]))
import common as C  # noqa: E402
import harness as H  # noqa: E402
import classifier as K  # noqa: E402

fm = C.import_production()
PMIN = 0.5
SIG_CONT = 2.0
SIG_BOUND = 2.5


def _obs_rules(Dp):
    obs = []
    for r in Dp.itertuples():
        if r.src == "race":
            obs.append((r.day, r.y, 1.0, "race", r.x))
        elif r.src == "int":
            obs.append((r.day, r.y, float(r.prod_sigma) if pd.notna(r.prod_sigma) else 2.0, "hard", r.x))
        elif r.workout == "tempo":
            obs.append((r.day, r.y + 3.0, 2.0, "hard", r.x))
        else:
            obs.append((r.day, r.y, SIG_BOUND, "bound", r.x))
    return obs


def _obs_p(Dp, P, B, pmin=PMIN, bias=True, bounds=True, soft=False):
    obs = []
    for i, r in zip(Dp.index, Dp.itertuples()):
        if r.src == "race":
            obs.append((r.day, r.y, 1.0, "race", r.x)); continue
        p = float(P[i]); sig = float(r.prod_sigma) if (r.src == "int" and pd.notna(r.prod_sigma)) else SIG_CONT
        y = r.y - (float(B[i]) if bias else 0.0)
        if soft:
            obs.append((r.day, y, sig / math.sqrt(max(p, 0.02)), "hard", r.x))
        elif p >= pmin:
            obs.append((r.day, y, sig / math.sqrt(p), "hard", r.x))
        elif bounds:
            obs.append((r.day, r.y, SIG_BOUND, "bound", r.x))
    return obs


def _curve_state(obs, day):
    obs = [o for o in obs if np.isfinite(o[1])]
    if not any(o[3] in ("race", "hard") for o in obs):
        return None
    d0 = min(o[0] for o in obs)
    _, M, Cv, w, _ = fm.smooth_curve(obs, d0, day - 1)
    obs_s = sorted(obs, key=lambda o: o[0])
    ev = [2 ** o[4] * 5.0 for o, wt in zip(obs_s, w) if o[3] in ("race", "hard") and wt > 0.5]
    return M[-1], Cv[-1], ((min(ev), max(ev)) if ev else None)


def _predict_state(st, km):
    if st is None:
        return None
    m, cv, ekm = st
    p = fm.curve_predict(m[0], m[1], cv[0, 0], cv[1, 1], cv[0, 1], float(km), ekm)
    return {"vdot": float(p["vdot"]), "sd": float(p["sd"])}


@functools.lru_cache(maxsize=None)
def _state(person, day, variant, model, fs, pmin, label_gap):
    D = K.load()
    Dp = D[(D.person == person) & (D.day < day)]
    if Dp.empty:
        return None
    if variant == "rules":
        return _curve_state(_obs_rules(Dp), day)
    pb = K.p_at(day, model=model, fs=fs, label_gap=label_gap)
    if pb is None:                                   # before the first checkpoint: production-like rules
        return _curve_state(_obs_rules(Dp), day)
    P, B = pb
    kw = {"p": {}, "p_nobias": {"bias": False}, "p_nobound": {"bounds": False}, "soft": {"soft": True}}[variant]
    if variant == "roll":
        return ("roll", Dp, P, B)
    return _curve_state(_obs_p(Dp, P, B, pmin=pmin, **kw), day)


def make(variant, model="logit", fs="nodelta", pmin=PMIN, label_gap=K.LABEL_GAP):
    def f(person, hist, target):
        return _predict_state(_state(person, hist.day, variant, model, fs, pmin, label_gap), target["km"])
    f.__name__ = f"g7_{variant}[{model}/{fs},pmin={pmin}]" if variant != "rules" else "g7_rules"
    return f


# ------------------------------------------------------------------ weighted rolling estimate (no Kalman)
def make_roll(model="logit", fs="nodelta", tau=45.0, window=150, pmin=0.2):
    """WLS of y_adj = theta + s*x over the last `window` days; weight = p * exp(-age/tau) / sigma^2; slope prior
    N(0, 1.5^2); races weight 1/1^2. sd = sqrt(posterior var + 1)."""
    def f(person, hist, target):
        D = K.load()
        day = hist.day
        Dp = D[(D.person == person) & (D.day < day) & (D.day >= day - window)]
        pb = K.p_at(day, model=model, fs=fs)
        if Dp.empty or pb is None:
            return None
        P, B = pb
        rows = []
        for i, r in zip(Dp.index, Dp.itertuples()):
            if r.src == "race":
                p, y, sig = 1.0, r.y, 1.0
            else:
                p = float(P[i]); y = r.y - float(B[i])
                sig = float(r.prod_sigma) if (r.src == "int" and pd.notna(r.prod_sigma)) else SIG_CONT
            if p < pmin or not np.isfinite(y):
                continue
            rows.append((y, r.x, p * math.exp(-(day - r.day) / tau) / sig ** 2))
        if len(rows) < 2:
            return None
        y = np.array([a[0] for a in rows]); x = np.array([a[1] for a in rows]); w = np.array([a[2] for a in rows])
        Xd = np.stack([np.ones_like(x), x], 1)
        A = Xd.T @ (w[:, None] * Xd) + np.diag([1e-6, 1 / 1.5 ** 2])
        bvec = Xd.T @ (w * y)
        th = np.linalg.solve(A, bvec)
        # one robust pass: downweight obs far below the fit (sub-max efforts that slipped through)
        res = y - Xd @ th
        w2 = w * np.where(res < -2.0, (2.0 / -res) ** 2, 1.0)
        A = Xd.T @ (w2[:, None] * Xd) + np.diag([1e-6, 1 / 1.5 ** 2])
        th = np.linalg.solve(A, Xd.T @ (w2 * y))
        Ci = np.linalg.inv(A)
        xt = math.log2(float(target["km"]) / 5.0)
        xm = min(xt, x.max()) if th[1] > 0 else xt
        v = Ci[0, 0] + 2 * xm * Ci[0, 1] + xm * xm * Ci[1, 1]
        return {"vdot": float(th[0] + xm * th[1]), "sd": float(math.sqrt(v + 1.0))}
    f.__name__ = f"g7_roll[{model}/{fs},tau={tau}]"
    return f


# ------------------------------------------------------------------ production evidence ablations
@functools.lru_cache(maxsize=None)
def _prod_drop_state(person, day, drop):
    a = H.history(person, day).acts
    a = a[a.prod_discipline.isin(["interval", "long"]) & a.prod_kind.isin(["hard", "race", "bound"]) &
          a.prod_km.notna() & a.prod_score.notna()]
    if "bounds" in drop:
        a = a[a.prod_kind != "bound"]
    if "hilly_road_int" in drop:
        a = a[~((a.prod_discipline == "interval") & (a.surface == "road") & a.terrain.isin(["hilly", "steep"]))]
    if "glitch" in drop:
        a = a[~a.quality.fillna("").str.contains("gps_glitch")]
    if a.empty:
        return None
    obs = [(int(r.day), float(r.prod_score), float(r.prod_sigma), r.prod_kind, fm.curve_x(float(r.prod_km)))
           for r in a.itertuples()]
    return _curve_state(obs, day)


@functools.lru_cache(maxsize=None)
def _prod_p_state(person, day, psrc, model, fs, pmin, promote):
    """Production evidence (prod_score / prod_km / prod_kind) re-weighted by p(maximal) of the same run:
    hard items with p < pmin -> lower bound (sigma 2.5), else sigma / sqrt(0.5 + 0.5 p) (production's HR-intent
    mechanism); promote: bound items with p >= promote become hard at their own km (y - shortfall, sigma 2/sqrt(p))."""
    a = H.history(person, day).acts
    a = a[a.prod_discipline.isin(["interval", "long"]) & a.prod_kind.isin(["hard", "race", "bound"]) &
          a.prod_km.notna() & a.prod_score.notna()]
    if a.empty:
        return None
    D = K.load()
    Dp = D[(D.person == person) & (D.day < day)]
    idx = dict(zip(Dp.activity_id, Dp.index))
    if psrc == "hr_intent":
        P, B = K.p_hr_intent(D), np.zeros(len(D))
    else:
        pb = K.p_at(day, model=model, fs=fs)
        if pb is None:
            return _prod_drop_state(person, day, ())
        P, B = pb
    obs = []
    for r in a.itertuples():
        kind, y, sig, x = r.prod_kind, float(r.prod_score), float(r.prod_sigma), fm.curve_x(float(r.prod_km))
        i = idx.get(r.activity_id)
        if i is not None and kind == "hard":
            p = float(P[i])
            if p < pmin:
                kind, sig = "bound", SIG_BOUND
            else:
                sig = sig / math.sqrt(0.5 + 0.5 * p)
        elif i is not None and kind == "bound" and promote is not None and P[i] >= promote:
            kind, y, sig = "hard", y - float(B[i]), SIG_CONT / math.sqrt(float(P[i]))
        obs.append((int(r.day), y, sig, kind, x))
    return _curve_state(obs, day)


def make_prod_p(psrc="learned", model="logit", fs="nodelta", pmin=0.5, promote=None):
    def f(person, hist, target):
        return _predict_state(_prod_p_state(person, hist.day, psrc, model, fs, pmin, promote), target["km"])
    f.__name__ = (f"prod_p[{psrc}" + (f",{model}/{fs}" if psrc == "learned" else "") +
                  f",pmin={pmin}" + (f",promote={promote}" if promote is not None else "") + "]")
    return f


def make_prod_drop(drop):
    def f(person, hist, target):
        return _predict_state(_prod_drop_state(person, hist.day, drop), target["km"])
    f.__name__ = "prod_drop_" + "+".join(drop)
    return f


def run_all(persons=("adrian", "rise", "patrik")):
    models = [H.naive_best_recent, H.production_curve, make("rules"),
              make("p", "logit", "nodelta"), make("p", "logit", "full"), make("p", "lgbm", "nodelta"),
              make("p", "lgbm", "full"), make("p_nobias", "logit", "nodelta"), make("p_nobound", "logit", "nodelta"),
              make("soft", "logit", "nodelta"), make("soft", "logit", "full"), make("p", "logit", "hr_only"),
              make_roll("logit", "nodelta"), make_roll("logit", "full"),
              make_prod_drop(("bounds",)), make_prod_drop(("hilly_road_int",)), make_prod_drop(("glitch",)),
              make_prod_p("hr_intent"), make_prod_p("learned", "logit", "nodelta"),
              make_prod_p("learned", "logit", "full"), make_prod_p("learned", "lgbm", "full"),
              make_prod_p("learned", "logit", "nodelta", promote=0.7),
              make_prod_p("learned", "logit", "full", promote=0.7),
              make_prod_p("learned", "logit", "nodelta", pmin=0.3), make_prod_p("learned", "logit", "nodelta", pmin=0.7),
              make_prod_p("learned", "logit_split", "nodelta"), make_prod_p("learned", "logit_split", "full"),
              make_prod_p("learned", "logit_split", "nodelta", pmin=0.3),
              make("p", "logit_split", "nodelta"), make("soft", "logit_split", "nodelta")]
    res = []
    for m in models:
        r = H.evaluate(m, persons=persons, name=m.__name__)
        print(m.__name__, r["headline"], r["seconds"], "s", flush=True)
        res.append(r)
    return res


def table(res, persons=("adrian", "rise")):
    rows = []
    for r in res:
        P_ = r["preds"]
        P_ = P_[P_.pred.notna()].assign(e=lambda d: d.pred - d.actual)
        def mae(m):
            return float(P_[m].e.abs().mean()) if m.any() else np.nan
        race = P_.tier == "race"; ar = race & P_.person.isin(persons)
        x = P_[ar & P_.sd.notna()]
        z = (x.e / x.sd.astype(float).clip(lower=0.3)) if len(x) else pd.Series(dtype=float)
        rows.append({"model": r["name"], "race adrian": mae(race & (P_.person == "adrian")),
                     "race rise": mae(race & (P_.person == "rise")), "race a+r": mae(ar), "n": int(ar.sum()),
                     "race bias a+r": float(P_[ar].e.mean()),
                     "race patrik": mae(race & (P_.person == "patrik")),
                     "interval": mae(P_.tier == "interval"), "hard_effort": mae(P_.tier == "hard_effort"),
                     "interval a+r": mae((P_.tier == "interval") & P_.person.isin(persons)),
                     "hard_effort a+r": mae((P_.tier == "hard_effort") & P_.person.isin(persons)),
                     "race cover1sd": float((z.abs() <= 1).mean()) if len(z) else np.nan})
    return pd.DataFrame(rows)


if __name__ == "__main__":
    res = run_all()
    T = table(res)
    out = K.F.G7
    T.to_csv(out / "harness_table.csv", index=False)
    pd.concat([r["preds"].assign(model=r["name"]) for r in res]).to_parquet(out / "harness_preds.parquet", index=False)
    with pd.option_context("display.width", 250, "display.float_format", "{:.2f}".format):
        print(T.to_string(index=False))


# G9 entry point: production evidence re-weighted by the learned p(maximal) (split logit, run features only).
# Scores within noise of production_curve (REPORT.md); kept for ensembling, not as an improvement.
predict = make_prod_p("learned", "logit_split", "nodelta", pmin=0.5)

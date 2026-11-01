"""Experiment: are separate interval / long running shapes needed, or is one (merged or coupled) shape as good?

Models (all on the VDOT scale, same per-item sigma, kinds and robust weights as fitness_model):
  A        separate: interval and long smoothed independently (production).            A-blend: production
           `race_prediction` (1/sd² blend of the two shapes) used for every race.
  B        merged: one random walk, all running evidence (interval + long items). The 10k race appears in
           production as both a long `race` and an interval `hard` item; merged/coupled keep only the race item.
  B'(c)    merged with a fixed offset c for long items (long obs = θ + c). c is chosen in-sample -> optimistic.
  C(qg)    coupled 2-state Kalman (θ, gap): interval obs = θ, long obs = θ + gap; gap is a random walk with
           variance qg per day (qg = 0: constant offset learned from data), prior gap ~ N(0, 3²).
  D(qs)    coupled distance curve (production since fitness_model MODEL_VERSION 2, `fm.smooth_curve`): state
           (θ = VDOT at 5 km, slope per doubling of distance), every item observed at its effective distance
           x = log2(km/5) (`fm.evidence_km`); races predicted at their own distance. Variants: interval items at
           7.07 km (the −2.5 offset was fitted on 5k + 10k races), tempo at 10k / half / the window's race-pace
           distance (with the +3.0 offset or a re-fitted +1.0 / 0.0).

Out-of-sample metrics (no look-ahead except the `implausible` cross-check flag, which production computes once):
  - race prediction: model fitted on data strictly before the race day (robust smoother, last day = forward
    estimate); ≤7 km races use the interval-equivalent, 10k/half the long-equivalent. Errors = pred − race.
  - calibration: share of races within ±1 / ±2 predictive sd (sqrt(shape sd² + race sigma²)).
  - one-step-ahead forecast of race/hard items (forward filter), interval and long targets separately.
  - current values today.

Usage: uv run scripts/shape_split_eval.py [person ...]     (default: adrian patrik)
Does not write any files.
"""

from __future__ import annotations

import datetime as dt
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fitness_model as fm  # noqa: E402

Q_TH = fm.P["q_per_day"]["interval"]   # θ random walk (0.05) for merged / coupled
QG_GRID = (0.0, 1e-4, 1e-3, 5e-3, 0.02)
QS_GRID = (0.0, 5e-4, 1e-3, 2e-3, 5e-3, 0.02)   # slope random walk of model D
GAP_PRIOR_SD = 3.0
TODAY = dt.date.today()


# ------------------------------------------------------------------ 2-state robust Kalman smoother (θ, gap)
def smooth2(obs, day0, day1, q_th, q_g, g0=0.0, g_sd=GAP_PRIOR_SD, iters=5):
    """obs: (day, y, sigma, kind, h) with h = 0 (y ~ θ) or 1 (y ~ θ + gap).
    Same structure as fm.smooth_series: robust weights from the online forecast on pass 1, then from the RTS
    smoothed estimate. Returns (mean (n,2), cov (n,2,2), weights, forecasts [(mean, sd) of H·x before each obs])."""
    n = day1 - day0 + 1
    obs = sorted(obs, key=lambda o: o[0])
    good = [o[1] - o[4] * g0 for o in obs if o[3] != "bound"] or [o[1] for o in obs]
    m0 = float(np.median(good[:5]))
    byday = {}
    for j, o in enumerate(obs):
        byday.setdefault(o[0] - day0, []).append(j)
    w = np.ones(len(obs))
    Q = np.diag([q_th, q_g])
    for it in range(iters):
        mf, Pf, mp, Pp = np.zeros((n, 2)), np.zeros((n, 2, 2)), np.zeros((n, 2)), np.zeros((n, 2, 2))
        a, b = m0, g0
        p11, p12, p22 = fm.P["prior_sd"] ** 2, 0.0, g_sd ** 2
        fc = [None] * len(obs)
        for k in range(n):
            if k:
                p11 += q_th; p22 += q_g
            mp[k] = (a, b); Pp[k] = ((p11, p12), (p12, p22))
            for j in byday.get(k, ()):
                y, s, kind, h = obs[j][1], obs[j][2], obs[j][3], obs[j][4]
                pred = a + h * b
                vx = p11 + 2 * h * p12 + h * h * p22
                fc[j] = (pred, math.sqrt(vx))
                if it == 0:
                    w[j] = fm._weight(y - pred, math.sqrt(s * s + vx), kind)
                S = vx + s * s / max(w[j], 1e-6)
                c1, c2 = p11 + h * p12, p12 + h * p22          # P Hᵀ
                k1, k2 = c1 / S, c2 / S
                r = y - pred
                a, b = a + k1 * r, b + k2 * r
                p11, p12, p22 = p11 - k1 * c1, p12 - k1 * c2, p22 - k2 * c2
            mf[k] = (a, b); Pf[k] = ((p11, p12), (p12, p22))
        ms, Ps = mf.copy(), Pf.copy()
        for k in range(n - 2, -1, -1):   # RTS (identity transition)
            C = Pf[k] @ np.linalg.inv(Pf[k] + Q)
            ms[k] = mf[k] + C @ (ms[k + 1] - mp[k + 1])
            Ps[k] = Pf[k] + C @ (Ps[k + 1] - Pp[k + 1]) @ C.T
        if it == 0:
            fc0 = fc
        neww = np.array([fm._weight(o[1] - (ms[o[0] - day0, 0] + o[4] * ms[o[0] - day0, 1]), o[2], o[3]) for o in obs])
        if it and np.allclose(neww, w):
            break
        w = neww
    return ms, Ps, w, fc0


# ------------------------------------------------------------------ evidence
def evidence(person, drop_ids=()):
    """Running evidence items with the production `implausible` cross-check applied (full-data interval shape)."""
    acts, feats, _ = fm.load_features(person, progress=False)
    ev = [e for e in fm.collect_evidence(acts, feats) if e["discipline"] in ("interval", "long") and e["id"] not in drop_ids]
    iobs = fm._obs(ev, "interval")
    d0 = min(o[0] for o in iobs) - 7
    days, m, s, _, _ = fm.smooth_series(iobs, d0, TODAY.toordinal(), fm.P["q_per_day"]["interval"])
    fm.cross_check(ev, (days, m, s, None))
    ev = [e for e in ev if e["kind"] != "implausible"]
    for e in ev:
        e["is_10k_dup"] = e["discipline"] == "interval" and e["type"] == "race?" and e["kind"] == "hard"
        e["key"] = (e["id"], e["discipline"])
    return ev


# ------------------------------------------------------------------ models: fit(ev, end_day, iters) -> predictor
class Separate:
    name = "A separate"

    def fit(self, ev, end, iters=5):
        out = {}
        for disc in ("interval", "long"):
            obs = fm._obs(ev, disc)
            if not obs:
                out[disc] = None; continue
            d0 = min(o[0] for o in obs) - 7
            _, m, s, _, fc = fm.smooth_series(obs, d0, end, fm.P["q_per_day"][disc], iters=iters)
            keys = [e["key"] for e in sorted([e for e in ev if e["discipline"] == disc], key=lambda e: e["day"])]
            out[disc] = (float(m[-1]), float(s[-1]), dict(zip(keys, fc)))
        return out

    def now(self, F, h):
        f = F["long" if h else "interval"]
        return (f[0], f[1]) if f else None

    def fc(self, F, e):
        f = F[e["discipline"]]
        return f[2].get(e["key"]) if f else None


def blend_v1(parts):
    """fitness_model v1 `race_prediction`: 1/sd² blend of the interval and long shapes, sd incl. their spread."""
    if not parts:
        return None
    w = np.array([1 / s ** 2 for _, s in parts]); m = np.array([mm for mm, _ in parts])
    W = float(w.sum()); mb = float((w * m).sum() / W)
    return mb, math.sqrt(1 / W + float((w * (m - mb) ** 2).sum()) / W)


class Blend(Separate):
    name = "A-blend (v1 race_prediction)"

    def now(self, F, h):
        return blend_v1([(v[0], v[1]) for v in F.values() if v])

    fc = None   # no per-item forecast (same as A)


class Merged:
    def __init__(self, c=0.0, q=Q_TH, label=None):
        self.c, self.q = c, q
        self.name = label or (f"B merged (q={q})" if c == 0 else f"B' merged, long offset {c:+.1f} (in-sample)")

    def fit(self, ev, end, iters=5):
        items = sorted([e for e in ev if not e["is_10k_dup"]], key=lambda e: e["day"])
        if not items:
            return None
        obs = [(e["day"], e["score"] - (self.c if e["discipline"] == "long" else 0), e["sigma"], e["kind"]) for e in items]
        _, m, s, _, fc = fm.smooth_series(obs, items[0]["day"] - 7, end, self.q, iters=iters)
        return float(m[-1]), float(s[-1]), dict(zip([e["key"] for e in items], fc))

    def now(self, F, h):
        return (F[0] + h * self.c, F[1]) if F else None

    def fc(self, F, e):
        f = F[2].get(e["key"]) if F else None
        return (f[0] + (self.c if e["discipline"] == "long" else 0), f[1]) if f else None


class Coupled:
    def __init__(self, qg):
        self.qg = qg
        self.name = f"C coupled (q_gap={qg:g})"

    def fit(self, ev, end, iters=5):
        items = sorted([e for e in ev if not e["is_10k_dup"]], key=lambda e: e["day"])
        if not items:
            return None
        obs = [(e["day"], e["score"], e["sigma"], e["kind"], int(e["discipline"] == "long")) for e in items]
        m, P, _, fc = smooth2(obs, items[0]["day"] - 7, end, Q_TH, self.qg, iters=iters)
        return m[-1], P[-1], dict(zip([e["key"] for e in items], fc))

    def now(self, F, h):
        if F is None:
            return None
        m, P, _ = F
        return float(m[0] + h * m[1]), math.sqrt(P[0, 0] + 2 * h * P[0, 1] + h * h * P[1, 1])

    def gap(self, F):
        return float(F[0][1]), math.sqrt(F[1][1, 1])

    def fc(self, F, e):
        return F[2].get(e["key"]) if F else None


class Curve:
    """D: coupled distance curve (fm.smooth_curve): VDOT(km) = theta + slope * log2(km / 5); every item at its
    effective distance (fm.evidence_km, with CURVE overrides), races predicted at their own distance."""

    def __init__(self, q_s=None, interval_km=None, tempo_km=None, tempo_offset=None, label=None):
        self.q_s, self.over = q_s, {k: v for k, v in (("interval_km", interval_km), ("tempo_km", tempo_km)) if v is not None}
        self.tempo_offset = tempo_offset
        self.name = label or f"D curve (q_s={q_s if q_s is not None else fm.CURVE['q_slope']:g})"

    def items(self, ev):
        old = dict(fm.CURVE)
        fm.CURVE.update(self.over)
        try:
            out = []
            for e in ev:
                if e["is_10k_dup"]:
                    continue
                km = fm.evidence_km(e)
                if km is None:
                    continue
                y = e["score"]
                if self.tempo_offset is not None and e["type"] == "tempo" and e["discipline"] == "long":
                    y = y - fm.P["tempo_offset"] + self.tempo_offset
                out.append((e, km, y))
        finally:
            fm.CURVE.clear(); fm.CURVE.update(old)
        return sorted(out, key=lambda t: t[0]["day"])

    def fit(self, ev, end, iters=5):
        it = self.items(ev)
        if not it:
            return None
        obs = [(e["day"], y, e["sigma"], e["kind"], fm.curve_x(km)) for e, km, y in it]
        _, m, P, _, fc = fm.smooth_curve(obs, it[0][0]["day"] - 7, end, q_slope=self.q_s, iters=iters)
        return m[-1], P[-1], {e["key"]: (f, y) for (e, _, y), f in zip(it, fc)}

    def at(self, F, km):
        m, P, _ = F
        x = fm.curve_x(km)
        return float(m[0] + x * m[1]), math.sqrt(P[0, 0] + 2 * x * P[0, 1] + x * x * P[1, 1])

    def now(self, F, h):   # h = 0: 5 km, 1: long reference (10k-half mean)
        if F is None:
            return None
        return self.at(F, 5.0 if not h else fm.CURVE["ref_km"] * 2 ** fm._mean_x(fm.CURVE["long_ref_km"]))

    def race(self, F, r):
        return self.at(F, {"5k": 5.0, "10k": 10.0, "half": 21.0975, "marathon": 42.195}[r["components"]["race"]]) if F else None

    def fc(self, F, e):
        f = F[2].get(e["key"]) if F else None
        return f[0] if f else None

    def target(self, F, e):   # the score this model saw for the item (tempo offset variants)
        f = F[2].get(e["key"]) if F else None
        return f[1] if f else e["score"]


# ------------------------------------------------------------------ evaluation
def races_of(ev):
    """One target per race session: the race item (interval race ≤7 km, long race 10k+)."""
    return [e for e in ev if e["kind"] == "race"]


def race_eval(model, ev, targets):
    rows = []
    for r in targets:
        past = [e for e in ev if e["day"] < r["day"]]
        if len([e for e in past if e["kind"] != "bound"]) < 3:
            continue
        F = model.fit(past, r["day"] - 1)
        p = model.race(F, r) if hasattr(model, "race") else model.now(F, int(r["discipline"] == "long"))
        if p:
            sd_pred = math.sqrt(p[1] ** 2 + r["sigma"] ** 2)
            rows.append({"date": r["date"], "race": r["components"]["race"], "y": r["score"], "pred": p[0], "sd": p[1],
                         "err": p[0] - r["score"], "z": (r["score"] - p[0]) / sd_pred})
    return rows


def summary(rows):
    if not rows:
        return "n=0"
    e = np.array([r["err"] for r in rows]); z = np.abs([r["z"] for r in rows])
    return (f"n={len(e)} mean {e.mean():+.2f}  MAE {np.abs(e).mean():.2f}  RMSE {np.sqrt((e**2).mean()):.2f}  "
            f"in±1sd {np.mean(z <= 1):.0%} in±2sd {np.mean(z <= 2):.0%}")


def forecast_eval(model, ev):
    """One-step-ahead (forward filter, pass-1 robust weights) errors on race/hard targets, skipping the first 3 per group."""
    if model.fc is None:
        return {}
    F = model.fit(ev, max(e["day"] for e in ev), iters=1)
    out = {}
    for disc in ("interval", "long"):
        tg = sorted([e for e in ev if e["discipline"] == disc and e["kind"] in ("race", "hard") and not e["is_10k_dup"]],
                    key=lambda e: e["day"])[3:]
        err, ll = [], []
        for e in tg:
            f = model.fc(F, e)
            if f is None:
                continue
            s = math.sqrt(f[1] ** 2 + e["sigma"] ** 2)
            y = model.target(F, e) if hasattr(model, "target") else e["score"]
            err.append(y - f[0]); z = min(abs(err[-1]) / s, 3)
            ll.append(-0.5 * z * z - math.log(s))
        out[disc] = (len(err), float(np.mean(err)) if err else float("nan"),
                     float(np.mean(np.abs(err))) if err else float("nan"), float(np.mean(ll)) if ll else float("nan"))
    return out


def fmt_t(s):
    s = int(round(s)); h, r = divmod(s, 3600); m, sec = divmod(r, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def run(person):
    print(f"\n{'=' * 100}\n{person}  (today {TODAY})")
    ev_all = evidence(person)
    # adrian 2025-10-24 second 5k (19:01, same day as an 18:01): probably not all-out
    soft = [e["id"] for e in ev_all if e["kind"] == "race" and e["date"] == "2025-10-24" and e["components"].get("time_s", 0) > 1120]
    n_long_hard = sum(1 for e in ev_all if e["discipline"] == "long" and e["kind"] in ("race", "hard"))
    n_int_hard = sum(1 for e in ev_all if e["discipline"] == "interval" and e["kind"] in ("race", "hard") and not e["is_10k_dup"])
    print(f"evidence: interval race/hard {n_int_hard}, long race/hard {n_long_hard}, races {len(races_of(ev_all))}"
          f"{', soft race ' + str(soft) if soft else ''}")

    # B' offset: in-sample constant (C with q_gap=0 on all data) -> rounded
    cg = Coupled(0.0); c_ins = round(cg.gap(cg.fit(ev_all, TODAY.toordinal()))[0], 1)
    models = ([Separate(), Blend(), Merged(), Merged(q=0.02), Merged(c=c_ins)] + [Coupled(q) for q in QG_GRID]
              + [Curve(q) for q in QS_GRID]
              + [Curve(interval_km=7.07, label="D int@7.07km (5k/10k cal.)"),
                 Curve(tempo_km=10.0, label="D tempo@10k"),
                 Curve(tempo_km=21.0975, label="D tempo@half"),
                 Curve(tempo_km="window", label="D tempo@window, +3.0"),
                 Curve(tempo_km="window", tempo_offset=1.0, label="D tempo@window, +1.0"),
                 Curve(tempo_km="window", tempo_offset=0.0, label="D tempo@window, +0.0")])

    variants = [("all races", ev_all, races_of(ev_all))]
    if soft:
        ev_ns = [e for e in ev_all if e["id"] not in soft]
        variants.append(("without soft 5k (dropped from evidence and targets)", ev_ns, races_of(ev_ns)))

    for label, ev, targets in variants:
        print(f"\n--- race prediction, {label} (forward: data strictly before race day) ---")
        per = {}
        for mdl in models:
            per[mdl.name] = race_eval(mdl, ev, targets)
            print(f"{mdl.name:<38} {summary(per[mdl.name])}")
            for kind in ("short", "long"):
                sub = [r for r in per[mdl.name] if (r["race"] == "5k") == (kind == "short")]
                if sub and len(sub) < len(per[mdl.name]):
                    print(f"{'':<40}{kind:>5} races: {summary(sub)}")
        if per[models[0].name]:
            print("\nper race: race VDOT | pred (sd) per model")
            names = [m.name for m in models]
            print(f"{'date':<11}{'race':<6}{'VDOT':>6} " + " ".join(f"{n.split(' ')[0] + (n.split('=')[-1].rstrip(')') if 'q_gap' in n else ''):>12}" for n in names))
            for i, r in enumerate(per[names[0]]):
                cells = []
                for n in names:
                    rr = next((x for x in per[n] if x["date"] == r["date"] and x["race"] == r["race"] and abs(x["y"] - r["y"]) < 1e-6), None)
                    cells.append(f"{rr['pred']:6.1f}({rr['sd']:.1f})" if rr else f"{'-':>12}")
                print(f"{r['date']:<11}{r['race']:<6}{r['y']:6.1f} " + " ".join(f"{c:>12}" for c in cells))

    print("\n--- one-step-ahead forecast of race/hard items (forward filter; first 3 per group skipped) ---")
    print(f"{'model':<38}{'interval n / mean / MAE / loglik':>36}{'long n / mean / MAE / loglik':>36}")
    for mdl in models:
        fe = forecast_eval(mdl, ev_all)
        if not fe:
            continue
        cell = lambda t: f"{t[0]:>4} {t[1]:+6.2f} {t[2]:6.2f} {t[3]:7.3f}"
        print(f"{mdl.name:<38}{cell(fe['interval']):>36}{cell(fe['long']):>36}")

    print(f"\n--- current values ({TODAY}) ---")
    print(f"{'model':<38}{'interval-eq':>14}{'long-eq':>14}{'10k':>9}{'half':>10}{'gap':>13}")
    for mdl in models:
        F = mdl.fit(ev_all, TODAY.toordinal())
        i, l = mdl.now(F, 0), mdl.now(F, 1)
        g = f"{mdl.gap(F)[0]:+.1f}±{mdl.gap(F)[1]:.1f}" if isinstance(mdl, Coupled) else ""
        v10 = v21 = l[0]
        if isinstance(mdl, Curve):   # own value per distance; "gap" column = slope per doubling
            v10, v21 = mdl.at(F, 10.0)[0], mdl.at(F, 21.0975)[0]
            g = f"s {F[0][1]:+.1f}±{math.sqrt(F[1][1, 1]):.1f}"
        print(f"{mdl.name:<38}{i[0]:8.1f}±{i[1]:.1f} {l[0]:8.1f}±{l[1]:.1f} "
              f"{fmt_t(fm.race_time(v10, 10000)):>8} {fmt_t(fm.race_time(v21, 21097.5)):>9} {g:>12}")


if __name__ == "__main__":
    for p in sys.argv[1:] or ["adrian", "patrik"]:
        if (fm.ROOT / "data" / p / "groups.json").exists():
            run(p)
        else:
            print(f"{p}: no data, skipped")

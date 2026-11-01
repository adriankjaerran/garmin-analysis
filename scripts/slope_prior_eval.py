"""Experiment: how strongly should the distance curve's slope (VDOT per doubling of distance) be shrunk towards 0?

Grid over the slope prior sd (prior slope ~ N(0, sd²)) and the slope random-walk q (CURVE q_slope) ("RW"); the
same with a mean-reverting slope ("OU": AR(1) towards 0 whose stationary sd is the prior sd, so the shrinkage holds
at every date, not only at the first evidence: fm.smooth_curve revert=True); plus
variants that give interval sessions less say in the slope: "int σ×k" = interval-session sigma multiplied by k
(they pin theta at 5 km less firmly, so the 5k races weigh more against the long items).
Same out-of-sample metrics as scripts/shape_split_eval.py (model D): forward race prediction (data strictly before
the race day; with and without adrian's soft 2025-10-24 19:01 5k), calibration (share of races within ±1 / ±2
predictive sd), one-step-ahead forecasts of interval / long race+hard items, today's slope ± sd. Also the
races-only slope (curve fitted on race items only, weak prior) for reference.

Usage: uv run scripts/slope_prior_eval.py [person ...]     (default: adrian patrik)
Does not write any files.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fitness_model as fm  # noqa: E402
import shape_split_eval as sse  # noqa: E402

SD_GRID = (0.5, 0.75, 1.0, 1.5, 2.0, 3.0)
QS_GRID = (0.0, 1e-4, 3e-4, 1e-3)
OU_SD = (0.75, 1.0, 1.5, 2.0)            # mean-reverting slope: stationary sd
OU_Q = (1e-3, 3e-3, 1e-2)


class CurveP(sse.Curve):
    """sse.Curve with a slope prior sd, and optional interval-sigma inflation."""

    def __init__(self, slope_sd, q_s, int_sig=1.0, revert=False, label=None):
        super().__init__(q_s=q_s, label=label or ("OU " if revert else "RW ") + f"sd {slope_sd:g} q {q_s:g}"
                         + (f" int σ×{int_sig:g}" if int_sig != 1 else ""))
        self.slope_sd, self.int_sig, self.revert = slope_sd, int_sig, revert

    def fit(self, ev, end, iters=5):
        it = self.items(ev)
        if not it:
            return None
        obs = [(e["day"], y, e["sigma"] * (self.int_sig if e["discipline"] == "interval" and e["kind"] != "race" else 1.0),
                e["kind"], fm.curve_x(km)) for e, km, y in it]
        _, m, P, _, fc = fm.smooth_curve(obs, it[0][0]["day"] - 7, end, q_slope=self.q_s, slope_sd=self.slope_sd,
                                     iters=iters, revert=self.revert)
        return m[-1], P[-1], {e["key"]: (f, y) for (e, _, y), f in zip(it, fc)}


def races_only_slope(ev, end):
    """Today's slope ± sd from a curve fitted on race items only (prior sd 3, q_slope 0.001)."""
    races = [e for e in ev if e["kind"] == "race"]
    km = {"5k": 5.0, "10k": 10.0, "half": 21.0975, "marathon": 42.195}
    obs = [(e["day"], e["score"], e["sigma"], e["kind"], fm.curve_x(km[e["components"]["race"]])) for e in races]
    if len({o[4] for o in obs}) < 2:
        return None
    _, m, P, _, _ = fm.smooth_curve(obs, min(o[0] for o in obs) - 7, end, slope_sd=3.0, q_slope=1e-3)
    return float(m[-1, 1]), float(math.sqrt(P[-1, 1, 1]))


def _ll(rr):
    """mean log predictive density of the races (Gaussian, z clipped at 3), higher = better"""
    return float(np.mean([-0.5 * min(abs(r["z"]), 3) ** 2 - math.log(r["err"] / -r["z"] if r["z"] else 1.0) for r in rr
                          if r["z"]])) if rr else float("nan")


def run(person):
    print(f"\n{'=' * 110}\n{person}  (today {sse.TODAY})")
    ev_all = sse.evidence(person)
    soft = [e["id"] for e in ev_all if e["kind"] == "race" and e["date"] == "2025-10-24" and e["components"].get("time_s", 0) > 1120]
    ev_ns = [e for e in ev_all if e["id"] not in soft]
    end = sse.TODAY.toordinal()
    print("races:", ", ".join(f"{e['date']} {e['components']['race']} {e['score']:.1f}" for e in sse.races_of(ev_all)))
    for lab, ev in (("all races", ev_all), ("without soft 5k", ev_ns)):
        ro = races_only_slope(ev, end)
        print(f"races-only curve slope today ({lab}): {ro[0]:+.2f} ± {ro[1]:.2f}" if ro else "races-only slope: n/a (one distance)")
    km = {"5k": 5.0, "10k": 10.0, "half": 21.0975, "marathon": 42.195}
    rec = [(fm.curve_x(km[e["components"]["race"]]), e["score"]) for e in sse.races_of(ev_ns) if e["date"] >= "2025-01-01"]
    if len({x for x, _ in rec}) > 1:
        print(f"least-squares slope through the 2025 races (without soft 5k): {np.polyfit(*np.array(rec).T, 1)[0]:+.2f}")

    models = [CurveP(sd, q) for sd in SD_GRID for q in QS_GRID]
    models += [CurveP(sd, q, revert=True) for sd in OU_SD for q in OU_Q]
    models += [CurveP(sd, 1e-3, int_sig=k) for sd in (1.0, 3.0) for k in (1.5, 2.0)]
    has_races = bool(sse.races_of(ev_all)) and len(sse.races_of(ev_all)) > 1
    print(f"{'model':<24}{'races MAE all/nosoft/10k+':>27}{'bias':>7}{'±1/±2 sd':>10}{'logsc':>7}"
          f"{'int fc MAE/ll':>16}{'long fc n/MAE/ll':>20}{'slope today all/nosoft':>26}{'5k/10k/half/mar (all)':>24}")
    out = []
    for mdl in models:
        ra, rn = (sse.race_eval(mdl, ev_all, sse.races_of(ev_all)), sse.race_eval(mdl, ev_ns, sse.races_of(ev_ns))) if has_races else ([], [])
        mae = lambda rr: float(np.mean([abs(r["err"]) for r in rr])) if rr else float("nan")
        lg = [r for r in ra if r["race"] != "5k"]
        z = np.abs([r["z"] for r in ra])
        fe = sse.forecast_eval(mdl, ev_all)
        F = mdl.fit(ev_all, end); Fn = mdl.fit(ev_ns, end)
        s, ssd = float(F[0][1]), math.sqrt(F[1][1, 1])
        vd = [fm.curve_predict(F[0][0], s, F[1][0, 0], F[1][1, 1], F[1][0, 1], k, (5.0, 21.0975))["vdot"] for k in (5, 10, 21.0975, 42.195)]
        print(f"{mdl.name:<24}{mae(ra):9.2f} {mae(rn):6.2f} {mae(lg):6.2f}   "
              f"{(np.mean([r['err'] for r in ra]) if ra else float('nan')):+6.2f} "
              f"{(np.mean(z <= 1) if ra else float('nan')):4.0%}/{(np.mean(z <= 2) if ra else float('nan')):4.0%} {_ll(ra):6.2f} "
              f"{fe['interval'][2]:7.2f}/{fe['interval'][3]:6.3f} {fe['long'][0]:5d}/{fe['long'][2]:5.2f}/{fe['long'][3]:6.3f} "
              f"{s:+8.2f}±{ssd:.2f} / {float(Fn[0][1]):+5.2f}   " + "/".join(f"{v:.1f}" for v in vd))
        out.append(mdl.name)
    return out


if __name__ == "__main__":
    for p in sys.argv[1:] or ["adrian", "patrik"]:
        if (fm.ROOT / "data" / p / "groups.json").exists():
            run(p)
        else:
            print(f"{p}: no data, skipped")

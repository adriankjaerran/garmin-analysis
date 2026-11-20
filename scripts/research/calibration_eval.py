"""Shared running calibration pooled over the calibration persons (scripts/research/calibration_set.py), with every other person
as out-of-sample validation. Same criteria as the per-person tables of scripts/fitness_eval.py:

  tau_scale        rep-length bias: slope of (session score - median of sessions within 30 days) vs log2(median rep
                   length), pooled over persons; chosen = zero crossing (log tau interpolation)
  interval_offset  -median(session W'bal - race VDOT) over sessions 6 weeks before / 2 weeks after 5k / 10k races
  tempo_offset     median residual of tempo items vs the distance curve (in-sample; > 0: offset too large)
  forecast MAE     one-step forecast of interval sessions (fitness_eval's q 0.012 smoother), per tau

Pooling: each person's items enter with that person's calibration weight (0 = validation only). Rows per person
are printed too, so one person's pull on the pooled number is visible.

Usage: uv run scripts/research/calibration_eval.py [--persons a,b] [--validate c]     (prints only)
"""

from __future__ import annotations

import datetime as dt
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # scripts/ (engine modules)
sys.path.insert(0, str(Path(__file__).resolve().parent))          # scripts/research/ (siblings)
import calibration_set as cs  # noqa: E402
import fitness_model as fm  # noqa: E402


def _day(a):
    return dt.date.fromisoformat(a["date"]).toordinal()


def sessions_races(person):
    acts, feats, _ = fm.load_features(person, progress=False)
    races, ints = [], []
    for i, f in feats.items():
        a = acts[i]
        if "err" in f or a["sport"] != "run":
            continue
        off = a.get("official_race") if (a.get("official_race") or {}).get("sport") == "run" else None
        if off:
            races.append({"day": _day(a), "race": fm.race_nominal(off["km"] * 1000)[1], "vdot": fm.vdot_from_race(off["km"] * 1000, off["official_s"])})
        elif a["type"] == "race?":
            nom, nm = fm.race_nominal(f["dist_m"])
            if nom:
                races.append({"day": _day(a), "race": nm, "vdot": fm.vdot_from_race(nom, a.get("moving_s") or f["moving_s"])})
        ovr = (a.get("override") or {}).get("fitness")
        if a["type"] in ("intervals_track", "intervals_road") and isinstance(f.get("wbal"), dict) and a.get("dist_trusted") and not ovr:
            reps = a.get("reps") or []
            ints.append({"id": i, "day": _day(a), "track": a.get("atype") == "track_running" or fm.sg.flat_track(a.get("track")),
                         "wbal": f["wbal"], "rep_s": float(np.median([r[0] for r in reps])) if reps else None})
    ints.sort(key=lambda s: s["day"])
    return ints, races


def tau_parts(ints, races, k):
    near = [s["wbal"][k] - r["vdot"] for r in races if r["race"] in ("5k", "10k") for s in ints
            if 0 < r["day"] - s["day"] <= 42 or 0 < s["day"] - r["day"] <= 14]
    res, lr = [], []
    for s in ints:
        nb = [x["wbal"][k] for x in ints if abs(x["day"] - s["day"]) <= 30 and x["id"] != s["id"]]
        if len(nb) >= 3 and s["rep_s"]:
            res.append(s["wbal"][k] - float(np.median(nb))); lr.append(math.log2(s["rep_s"]))
    e, _ = fm.forecast_errors([(s["day"], s["wbal"][k], 1.5 if s["track"] else 2.0, "hard") for s in ints], 0.012)
    return near, res, lr, list(e)


def tempo_residuals(person):
    acts, feats, _ = fm.load_features(person, progress=False)
    ev = [e for e in fm.collect_evidence(acts, feats) if not fm.is_10k_dup(e)]
    today = dt.date.today().toordinal()
    iobs = fm._obs(ev, "interval")
    if not iobs:
        return []
    days, m, s, _, _ = fm.smooth_series(iobs, min(o[0] for o in iobs) - 7, today, fm.P["q_per_day"]["interval"])
    fm.cross_check(ev, (days, m, s, None))
    items, obs = fm.curve_obs(ev)
    d0 = min(o[0] for o in obs) - 7
    _, M, _, _, _ = fm.smooth_curve(obs, d0, today)
    return [e["score"] - (M[e["day"] - d0, 0] + o[4] * M[e["day"] - d0, 1]) for e, o in zip(items, obs)
            if e.get("type") == "tempo" and e["kind"] == "hard"]


def _wmedian(x, w):
    x, w = np.asarray(x, float), np.asarray(w, float)
    if not len(x) or w.sum() <= 0:
        return None
    o = np.argsort(x); c = np.cumsum(w[o])
    return float(x[o][np.searchsorted(c, 0.5 * c[-1])])


def pooled(parts, weights, k):
    near, wn, res, lr, wr, fc, wf = [], [], [], [], [], [], []
    for p, w in weights.items():
        a, b, c, d = parts[p][k]
        near += a; wn += [w] * len(a); res += b; lr += c; wr += [w] * len(b); fc += d; wf += [w] * len(d)
    sl = None
    if sum(1 for x in wr if x > 0) > 5:
        sl = float(np.polyfit(lr, res, 1, w=np.sqrt(wr))[0])
    mae = float(np.average(np.abs(fc), weights=wf)) if sum(wf) > 0 else None
    return {"vs_race": _wmedian(near, wn), "n_pairs": sum(1 for x in wn if x > 0), "slope": sl,
            "n_slope": sum(1 for x in wr if x > 0), "fc_mae": mae}


def zero_crossing(rows):
    ks = [k for k in rows if rows[k]["slope"] is not None]
    for k1, k2 in zip(ks, ks[1:]):
        s1, s2 = rows[k1]["slope"], rows[k2]["slope"]
        if s1 >= 0 >= s2 and s1 != s2:
            f = s1 / (s1 - s2)
            return math.exp(math.log(float(k1)) + f * (math.log(float(k2)) - math.log(float(k1))))
    return None


def main():
    args = sys.argv[1:]
    cal = args[args.index("--persons") + 1].split(",") if "--persons" in args else cs.calibration_persons()
    val = args[args.index("--validate") + 1].split(",") if "--validate" in args else cs.validation_persons()
    weights = {p: (cs.calibration_weight(p) if "--persons" not in args else 1.0) for p in cal}
    taus = [str(t) for t in fm.TAU_VARIANTS]
    parts, tempo = {}, {}
    for p in cal + val:
        ints, races = sessions_races(p)
        parts[p] = {k: tau_parts(ints, races, k) for k in taus}
        tempo[p] = tempo_residuals(p)
    print(f"calibration persons (weights): {weights}; validation only: {val}")
    print(f"{'set':28s} " + " ".join(f"{'tau ' + k:>27s}" for k in taus) + "   (vs race / rep-length slope / forecast MAE)")
    sets = [(f"POOLED {'+'.join(cal)}", weights)] + [(p, {p: 1.0}) for p in cal + val] + \
           [(f"(what-if) pooled + {v}", {**weights, v: 1.0}) for v in val]
    for nm, w in sets:
        rows = {k: pooled(parts, w, k) for k in taus}
        cells = []
        for k in taus:
            r = rows[k]
            f = lambda v: "-" if v is None else f"{v:+.2f}"
            mae = "-" if r["fc_mae"] is None else f"{r['fc_mae']:.2f}"
            cells.append(f"{f(r['vs_race']):>7s} {f(r['slope']):>7s} {mae:>6s}")
        zc = zero_crossing(rows)
        r35 = rows[str(fm.P["tau_scale"])]
        tr = [x for p in w if w[p] > 0 for x in tempo[p]]
        print(f"{nm:28s} " + " ".join(f"{c:>27s}" for c in cells) +
              f"   tau0 {zc and round(zc, 2)}  offset@{fm.P['tau_scale']} {r35['vs_race'] is not None and round(-r35['vs_race'], 2)} "
              f"(pairs {r35['n_pairs']})  tempo resid {round(float(np.median(tr)), 2) if tr else None} (n {len(tr)})")


if __name__ == "__main__":
    main()

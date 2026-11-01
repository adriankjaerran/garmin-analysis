"""How well does the interval W'-balance model capture rest length? (analysis only, changes nothing)

Usage: uv run scripts/rest_model_eval.py [person] [--no-cache]

1. Model predictions: "just possible" rep pace for synthetic sessions at VDOT 50/53/56 and rest 60..240 s
   (standing and jogging), s/km gained per extra minute of rest, for several tau_scale values.
2. Data: residuals of adrian's interval sessions (score - shape) vs rest length / work:rest / rep length, for
   tau_scale variants and a model without any rest effect; leave-one-out shapes; bootstrap CIs; within-rep-
   distance (fixed-effect) comparisons.
Caches FIT-derived extras in /tmp/rest_model_eval_<person>.pkl.
"""

from __future__ import annotations

import collections
import glob
import math
import pickle
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fitness_model as fm  # noqa: E402

OFF = fm.P["interval_offset"]
TAUS = (0.1, 0.15, 0.25, 0.35, 0.5, 0.7, 1.0, 2.0)
RNG = np.random.default_rng(1)


# ------------------------------------------------------------------ 1. synthetic sessions
def synth(n, rep, rest_s, v, rest_v=0.0, timed=False, warm_v=2.8, warm_s=600, cool_s=300):
    """1 Hz speed series: warm-up, n reps (rep = metres, or seconds if timed) at v with rest_s at rest_v."""
    rep_s = rep if timed else rep / v
    parts = [np.full(warm_s, warm_v)]
    for k in range(n):
        whole = int(rep_s); frac = rep_s - whole
        parts.append(np.full(whole, v))
        if frac > 0:   # partial second at the blended speed keeps the distance exact
            parts.append(np.array([v * frac + rest_v * (1 - frac)]))
        if k < n - 1:
            parts.append(np.full(int(rest_s), rest_v))
    parts.append(np.full(cool_s, warm_v))
    return np.concatenate(parts)


def just_possible_speed(vdot_shape, n, rep, rest_s, rest_v=0.0, timed=False, tau=None):
    """Rep speed at which the model scores the session exactly vdot_shape (after the interval offset)."""
    cs, dp = fm.cs_dprime(vdot_shape - OFF)
    cs, dp = np.array([cs]), np.array([dp])
    lo, hi = cs[0], cs[0] * 1.8
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        if fm.wbal_min(synth(n, rep, rest_s, mid, rest_v, timed), cs, dp, tau_scale=tau)[0] >= 0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


SESSIONS = [("6x1000", 6, 1000, False), ("5x1000", 5, 1000, False), ("6x800", 6, 800, False),
            ("10x400", 10, 400, False), ("15x400", 15, 400, False), ("3x1800", 3, 1800, False),
            ("11x1:15", 11, 75, True)]
RESTS = (60, 90, 120, 180, 240)


def pace(v):
    return 1000 / v


def fmt_pace(s):
    return f"{int(s // 60)}:{s % 60:04.1f}"


def part1(jog_v):
    print("\n=== 1. Model: 'just possible' rep pace (min:s/km) vs rest, tau_scale 0.35 ===")
    out = {}
    for V in (50, 53, 56):
        for rest_kind, rv in (("standing", 0.0), (f"jog {jog_v:.1f} m/s", jog_v)):
            print(f"\nVDOT {V} (shape scale), rest {rest_kind}")
            print(f"{'session':9s} " + " ".join(f"{r:>7d}s" for r in RESTS) + "   s/km per +60 s: 60→120  120→180  180→240")
            for name, n, rep, timed in SESSIONS:
                p = [pace(just_possible_speed(V, n, rep, r, rv, timed)) for r in RESTS]
                out[(V, rest_kind, name)] = p
                g = lambda a, b: p[RESTS.index(a)] - p[RESTS.index(b)]
                print(f"{name:9s} " + " ".join(f"{fmt_pace(x):>8s}" for x in p)
                      + f"   {g(60, 120):6.1f}  {g(120, 180):6.1f}  {g(180, 240):6.1f}")
    # sensitivity to tau_scale: 6x1000 and 10x400 at VDOT 53, standing
    print("\nSensitivity to tau_scale (VDOT 53, standing): s/km gained 90→150 s rest (typical +1 min)")
    print(f"{'session':9s} " + " ".join(f"{t:>6}" for t in TAUS))
    for name, n, rep, timed in SESSIONS:
        row = []
        for t in TAUS:
            a = pace(just_possible_speed(53, n, rep, 90, 0.0, timed, tau=t))
            b = pace(just_possible_speed(53, n, rep, 150, 0.0, timed, tau=t))
            row.append(a - b)
        print(f"{name:9s} " + " ".join(f"{x:6.1f}" for x in row))
    print("\nCS/D' limit for ONE rep with full recovery (what infinite rest converges to), VDOT 53:")
    cs, dp = fm.cs_dprime(53 - OFF)
    for d in (400, 539, 800, 1000, 1800):
        t = (d - dp) / cs
        print(f"  {d:5d} m: {t:5.1f} s = {fmt_pace(1000 * t / d)}/km")
    # VDOT -> s/km conversion at the rep for reference
    v53 = fm.speed_for(53, 210); v54 = fm.speed_for(54, 210)
    print(f"\nScale: 1 VDOT ≈ {pace(v53) - pace(v54):.1f} s/km at ~3:30 min efforts (Daniels curve, VDOT 53)")
    return out


# ------------------------------------------------------------------ 2. data
def extras(person, acts, ids, use_cache=True):
    """Per session: wbal score for extra tau values, rest speed / standing share (run_analysis breaks)."""
    cf = Path(f"/tmp/rest_model_eval_{person}.pkl")
    cache = pickle.loads(cf.read_bytes()) if cf.exists() and use_cache else {}
    fits = {Path(p).stem: p for p in glob.glob(str(fm.ROOT / "data" / person / "activities" / "*" / "*.fit"))}
    import run_analysis as ra
    changed = False
    for i in ids:
        if i in cache and set(TAUS) <= set(cache[i]["wbal"]):
            continue
        a = acts[i]
        S = fm.session_series(fits[i], a)
        e = {"wbal": {t: fm.wbal_vdot(S["vg"], tau_scale=t) for t in TAUS}}
        # no-rest-effect model: rests count as nothing; the reps as one continuous effort at the median rep pace
        try:
            x = ra.analyse(fits[i])
            br = x["breaks"]
            e["rest_time"] = float(sum(b["time"] for b in br)) or None
            e["rest_dist"] = float(sum(b["dist"] or 0 for b in br))
            e["rest_stand"] = float(sum(b["standing"] for b in br))
        except Exception as ex:  # noqa: BLE001
            e["err"] = repr(ex)[:80]
        cache[i] = e; changed = True
    if changed:
        cf.write_bytes(pickle.dumps(cache))
    return cache


def ols(X, y):
    X1 = np.c_[np.ones(len(y)), X]
    b, *_ = np.linalg.lstsq(X1, y, rcond=None)
    return b


def huber(X, y, k=1.5, it=30):
    X1 = np.c_[np.ones(len(y)), X]
    w = np.ones(len(y))
    for _ in range(it):
        W = np.sqrt(w)
        b, *_ = np.linalg.lstsq(X1 * W[:, None], y * W, rcond=None)
        r = y - X1 @ b
        s = 1.4826 * np.median(np.abs(r - np.median(r))) or 1.0
        w = np.minimum(1, k * s / np.maximum(np.abs(r), 1e-9))
    return b


def boot(fn, n, B=2000, groups=None):
    """Bootstrap (sessions, or whole groups when given) of fn(index array) -> vector; returns 2.5/97.5 %."""
    res = []
    if groups is None:
        for _ in range(B):
            res.append(fn(RNG.integers(0, n, n)))
    else:
        g = collections.defaultdict(list)
        for j, k in enumerate(groups):
            g[k].append(j)
        keys = list(g)
        for _ in range(B):
            pick = RNG.integers(0, len(keys), len(keys))
            res.append(fn(np.concatenate([g[keys[p]] for p in pick])))
    res = np.array(res)
    return np.nanpercentile(res, 2.5, axis=0), np.nanpercentile(res, 97.5, axis=0)


def loo_shapes(acts, feats, scores_by_id, tau_key):
    """Interval shape on each session's day from a smoother refitted without it (races + other sessions).
    scores_by_id overrides the interval-session scores (any model); other interval evidence (races) stays."""
    base = fm.collect_evidence(acts, feats)
    obs = []
    for e in base:
        if e["discipline"] != "interval" or e["kind"] == "implausible":
            continue
        if e["type"].startswith("intervals"):
            if e["id"] not in scores_by_id or scores_by_id[e["id"]] is None:
                continue
            obs.append((e["day"], scores_by_id[e["id"]], e["sigma"], e["kind"], e["id"]))
        else:
            obs.append((e["day"], e["score"], e["sigma"], e["kind"], e["id"]))
    d0, d1 = min(o[0] for o in obs) - 7, max(o[0] for o in obs) + 1
    out = {}
    for o in obs:
        if o[4] not in scores_by_id:
            continue
        sub = [x[:4] for x in obs if x[4] != o[4]]
        days, m, *_ = fm.smooth_series(sub, d0, d1, fm.P["q_per_day"]["interval"])
        out[o[4]] = float(m[o[0] - d0])
    return out


def rep_class(m):
    for c in (400, 539, 800, 1000, 1086, 1200, 1632, 1800):
        if abs(m / c - 1) < 0.04:
            return c
    return None


def part2(person, use_cache):
    import json
    acts, feats, _ = fm.load_features(person, progress=False)
    doc = json.loads((fm.ROOT / "data" / person / "fitness.json").read_text())
    S = [s for s in doc["sessions"] if s["discipline"] == "interval" and s["type"].startswith("intervals")]
    ids = [str(s["id"]) for s in S]
    X = extras(person, acts, ids, use_cache)
    rows = []
    for s in S:
        i = str(s["id"]); a = acts[i]; c = s["components"]; e = X[i]
        reps = a.get("reps") or []
        if not reps or not c.get("rest_s"):
            continue
        rep_m = float(np.median([r[1] for r in reps if r[1]])) if any(r[1] for r in reps) else None
        work_s = float(sum(r[0] for r in reps))
        v_rep = c["rep_speed"]
        indoor = bool(a.get("indoor_bislett"))
        rest_v = 0.0 if indoor else (e["rest_dist"] / e["rest_time"] if e.get("rest_time") else None)
        stand = 1.0 if indoor else (e["rest_stand"] / e["rest_time"] if e.get("rest_time") else None)
        rows.append({"id": i, "date": s["date"], "sig": c["sig"], "track": s["sigma"] == 1.5, "indoor": indoor,
                     "n": c["n_reps"], "rep_s": c["rep_s"], "rest_s": c["rest_s"], "wr": c["rep_s"] / c["rest_s"],
                     "rep_m": rep_m, "cls": rep_class(rep_m) if rep_m else None, "v": v_rep,
                     "rest_v": rest_v, "stand": stand, "score": s["score"], "shape_at": s["shape_at"],
                     "wbal": {t: (e["wbal"][t] + OFF if e["wbal"][t] is not None else None) for t in TAUS},
                     # no rest effect: reps joined into one continuous effort at the median rep speed
                     "norest": float(fm.vdot_from_perf(v_rep, work_s)) if v_rep else None})
    R = rows
    n = len(R)
    print(f"\n=== 2. Data: {n} interval sessions with reps and rests ===")
    rv = [r["rest_v"] for r in R if r["rest_v"] is not None and not r["indoor"]]
    st = [r["stand"] for r in R if r["stand"] is not None and not r["indoor"]]
    print(f"rest speed (outdoor/GPS, n={len(rv)}): median {np.median(rv):.2f} m/s, p25 {np.percentile(rv, 25):.2f}, "
          f"p75 {np.percentile(rv, 75):.2f}; standing share median {np.median(st):.2f}; "
          f"indoor Bislet (model: standing) n={sum(r['indoor'] for r in R)}")
    jog = [r for r in R if not r["indoor"] and r["rest_v"] is not None]
    print(f"  rest type: standing (<0.8 m/s) {sum(r['rest_v'] < 0.8 for r in jog)}, walk (0.8-1.8) "
          f"{sum(0.8 <= r['rest_v'] < 1.8 for r in jog)}, jog (>=1.8) {sum(r['rest_v'] >= 1.8 for r in jog)}")
    print(f"rest_s: median {np.median([r['rest_s'] for r in R]):.0f}, range {min(r['rest_s'] for r in R):.0f}-"
          f"{max(r['rest_s'] for r in R):.0f}; corr(rest_s, log2 rep_s) = "
          f"{np.corrcoef([r['rest_s'] for r in R], [math.log2(r['rep_s']) for r in R])[0, 1]:.2f}")

    # models: tau variants + no-rest; LOO shape per model
    models = {f"tau {t}": {r["id"]: r["wbal"][t] for r in R} for t in TAUS}
    models["no rest effect"] = {r["id"]: r["norest"] for r in R}
    print("computing leave-one-out shapes ...", flush=True)
    loo = {m: loo_shapes(acts, feats, sc, m) for m, sc in models.items()}

    def design(sel, key):
        rest_min = np.array([r["rest_s"] / 60 for r in sel])
        return {"rest per +1 min": rest_min, "log2 rest": np.log2(rest_min * 60),
                "work:rest": np.array([r["wr"] for r in sel]), "log2 rep_s": np.log2([r["rep_s"] for r in sel])}

    print("\nResidual (score − leave-one-out shape) regressed on one predictor at a time; Huber slope [95 % bootstrap CI]")
    print("Positive slope on rest = longer rests produce better scores than the model credits (model gives too little rest benefit).")
    hdr = f"{'model':15s} {'n':>3s} {'res sd':>6s} " + " ".join(f"{k:>24s}" for k in ("rest per +1 min", "log2 rest", "work:rest", "log2 rep_s"))
    print(hdr)
    summary = {}
    for m in models:
        sel = [r for r in R if models[m][r["id"]] is not None and r["id"] in loo[m]]
        y = np.array([models[m][r["id"]] - loo[m][r["id"]] for r in sel])
        D = design(sel, m)
        cells = []
        for k, x in D.items():
            b = huber(x[:, None], y)[1]
            lo, hi = boot(lambda ix: huber(x[ix][:, None], y[ix])[1], len(y), B=600)
            cells.append(f"{b:+6.2f} [{lo:+5.2f},{hi:+5.2f}]")
            summary[(m, k)] = (b, lo, hi)
        rsd = 1.4826 * np.median(np.abs(y - np.median(y)))
        print(f"{m:15s} {len(y):3d} {rsd:6.2f} " + " ".join(f"{c:>24s}" for c in cells))

    # joint: rest + rep length (separates rest from the rep-length bias)
    print("\nJoint Huber fit residual ~ rest_min + log2(rep_s) [95 % CI]:")
    for m in ("tau 0.15", "tau 0.25", "tau 0.35", "tau 0.5", "tau 1.0", "no rest effect"):
        sel = [r for r in R if models[m][r["id"]] is not None and r["id"] in loo[m]]
        y = np.array([models[m][r["id"]] - loo[m][r["id"]] for r in sel])
        Xj = np.c_[[r["rest_s"] / 60 for r in sel], np.log2([r["rep_s"] for r in sel])]
        b = huber(Xj, y)
        lo, hi = boot(lambda ix: huber(Xj[ix], y[ix]), len(y), B=600)
        print(f"  {m:15s} rest {b[1]:+.2f} [{lo[1]:+.2f},{hi[1]:+.2f}] VDOT/min   rep-len {b[2]:+.2f} [{lo[2]:+.2f},{hi[2]:+.2f}] per doubling")

    # in-sample residual from fitness.json (as stored)
    y = np.array([r["score"] - r["shape_at"] for r in R]); x = np.array([r["rest_s"] / 60 for r in R])
    b = huber(x[:, None], y)[1]; lo, hi = boot(lambda ix: huber(x[ix][:, None], y[ix])[1], len(y), B=600)
    print(f"\nfitness.json residual (score − shape_at, in-sample, tau 0.35) vs rest: {b:+.2f} [{lo:+.2f},{hi:+.2f}] VDOT/min, n={len(y)}")

    # within rep-distance class (fixed effects): only classes with >= 4 sessions and rest spread >= 30 s
    print("\n=== Within same rep distance (group-demeaned), the cleanest test ===")
    groups = collections.defaultdict(list)
    for r in R:
        if r["cls"]:
            groups[r["cls"]].append(r)
    keep = {k: g for k, g in groups.items() if len(g) >= 4 and np.ptp([r["rest_s"] for r in g]) >= 30}
    for k, g in sorted(keep.items()):
        rs = [r["rest_s"] for r in g]
        print(f"  {k} m: n={len(g)}, rest {min(rs):.0f}-{max(rs):.0f} s (median {np.median(rs):.0f}), "
              f"{'indoor' if all(r['indoor'] for r in g) else 'mixed/outdoor'}")
    sel = [r for g in keep.values() for r in g if r["rest_s"] <= 300]   # one 538 s rest (539 m) has huge leverage
    gid = [r["cls"] for r in sel]

    def fe(vals, xs):
        vals = np.array(vals, float); xs = np.array(xs, float)
        for k in set(gid):
            ix = np.array([gg == k for gg in gid])
            vals[ix] -= vals[ix].mean(); xs[ix] -= xs[ix].mean()
        return vals, xs

    print(f"\nPooled within-class slope (VDOT per +1 min rest), rests <= 300 s, n={len(sel)}, Huber, bootstrap over sessions:")
    print(f"{'quantity':52s} slope  [95 % CI]")
    p_of = {}
    for lab, ys in [
        ("empirical: no-rest score − LOO shape(0.35)", [r["norest"] - loo["tau 0.35"][r["id"]] for r in sel]),
        ("empirical: no-rest score − LOO shape(no-rest)", [r["norest"] - loo["no rest effect"][r["id"]] for r in sel]),
        ("model 0.35 credit: (no-rest − wbal0.35) score", [r["norest"] - r["wbal"][0.35] for r in sel]),
        ("model 0.25 credit", [r["norest"] - r["wbal"][0.25] for r in sel]),
        ("model 0.5 credit", [r["norest"] - r["wbal"][0.5] for r in sel]),
        ("residual tau 0.35 (score − LOO shape)", [r["wbal"][0.35] - loo["tau 0.35"][r["id"]] for r in sel]),
        ("residual tau 0.25", [r["wbal"][0.25] - loo["tau 0.25"][r["id"]] for r in sel]),
        ("residual tau 0.5", [r["wbal"][0.5] - loo["tau 0.5"][r["id"]] for r in sel]),
        ("residual tau 1.0", [r["wbal"][1.0] - loo["tau 1.0"][r["id"]] for r in sel]),
    ]:
        yv, xv = fe(ys, [r["rest_s"] / 60 for r in sel])
        b = huber(xv[:, None], yv)[1]
        bo = float(np.polyfit(xv, yv, 1)[0])

        def f(ix, yv=ys):
            # re-demean inside the bootstrap sample
            yy = np.array(yv, float)[ix]; xx = np.array([sel[j]["rest_s"] / 60 for j in ix]); gg = [gid[j] for j in ix]
            for k in set(gg):
                m_ = np.array([q == k for q in gg])
                yy[m_] -= yy[m_].mean(); xx[m_] -= xx[m_].mean()
            return huber(xx[:, None], yy)[1]
        lo, hi = boot(f, len(sel), B=1000)
        p_of[lab] = (b, lo, hi)
        print(f"{lab:52s} {b:+.2f}  [{lo:+.2f},{hi:+.2f}]   (OLS {bo:+.2f})")
    # per class
    print("\nPer class (OLS slope VDOT/min; empirical = no-rest score − LOO shape(0.35); model = 0.35 credit):")
    sub = dict(sorted(keep.items()))
    sub["1000 track"] = [r for r in keep.get(1000, []) if r["track"]]
    sub["1000 road"] = [r for r in keep.get(1000, []) if not r["track"]]
    sub["1086 indoor"] = [r for r in keep.get(1086, []) if r["indoor"]]
    for k, g in sub.items():
        if len(g) < 4:
            continue
        x = np.array([r["rest_s"] / 60 for r in g])
        ye = np.array([r["norest"] - loo["tau 0.35"][r["id"]] for r in g])
        ym = np.array([r["norest"] - r["wbal"][0.35] for r in g])
        be = np.polyfit(x, ye, 1)[0]; bm = np.polyfit(x, ym, 1)[0]
        lo, hi = boot(lambda ix: np.polyfit(x[ix], ye[ix], 1)[0] if np.ptp(x[ix]) > 0 else np.nan, len(g), B=1000)
        rr = np.array([r["wbal"][0.35] - loo["tau 0.35"][r["id"]] for r in g])
        br = np.polyfit(x, rr, 1)[0]
        lo2, hi2 = boot(lambda ix: np.polyfit(x[ix], rr[ix], 1)[0] if np.ptp(x[ix]) > 0 else np.nan, len(g), B=1000)
        print(f"  {str(k):>11s} n={len(g):2d} rest {x.min() * 60:.0f}-{x.max() * 60:.0f} s: empirical {be:+.2f} [{lo:+.2f},{hi:+.2f}]   "
              f"model 0.35 {bm:+.2f}   residual 0.35 {br:+.2f} [{lo2:+.2f},{hi2:+.2f}]")
    # start-clock confound: on a fixed cycle (group leaves every ~6 min) rest = cycle - rep time, so faster reps
    # mechanically get longer rests. Use the cycle (rep + rest) as an instrument: slope on cycle / slope of rest on cycle.
    print("\nStart-clock check (within class, rests <= 300 s): corr(rep_s, rest_s) and IV estimate with cycle = rep + rest:")
    for k, g in sub.items():
        g = [r for r in g if r["rest_s"] <= 300]
        if len(g) < 8:
            continue
        rep = np.array([r["rep_s"] for r in g]); rest = np.array([r["rest_s"] for r in g]); cyc = (rep + rest) / 60
        ye = np.array([r["norest"] - loo["tau 0.35"][r["id"]] for r in g])
        ym = np.array([r["norest"] - r["wbal"][0.35] for r in g])

        def iv(ix, y):
            if np.ptp(cyc[ix]) == 0:
                return np.nan
            fs = np.polyfit(cyc[ix], rest[ix] / 60, 1)[0]
            return np.polyfit(cyc[ix], y[ix], 1)[0] / fs if abs(fs) > 0.2 else np.nan
        ix0 = np.arange(len(g))
        fs = np.polyfit(cyc, rest / 60, 1)[0]
        lo, hi = boot(lambda ix: iv(ix, ye), len(g), B=1000)
        lo2, hi2 = boot(lambda ix: iv(ix, ye - ym), len(g), B=1000)
        print(f"  {str(k):>11s} n={len(g):2d} corr(rep,rest) {np.corrcoef(rep, rest)[0, 1]:+.2f}  first stage {fs:.2f} min rest/min cycle  "
              f"IV empirical {iv(ix0, ye):+.2f} [{lo:+.2f},{hi:+.2f}]  model {iv(ix0, ym):+.2f}  IV residual {iv(ix0, ye - ym):+.2f} [{lo2:+.2f},{hi2:+.2f}] VDOT/min")
    # convert to s/km
    v = fm.speed_for(53, 210); v1 = fm.speed_for(54, 210)
    spv = pace(v) - pace(v1)
    b, lo, hi = p_of["empirical: no-rest score − LOO shape(0.35)"]
    bm = p_of["model 0.35 credit: (no-rest − wbal0.35) score"][0]
    print(f"\nIn s/km (1 VDOT ≈ {spv:.1f} s/km at ~3:30/km reps): data {b * spv:+.1f} [{lo * spv:+.1f},{hi * spv:+.1f}] s/km per +1 min rest, "
          f"model 0.35 {bm * spv:+.1f}")
    # rest-type check: indoor (standing) vs outdoor jog residuals
    print("\nResidual (tau 0.35, LOO) by rest type:")
    for lab, f_ in (("indoor Bislet (standing assumed)", lambda r: r["indoor"]),
                    ("outdoor standing/walk (<1.8 m/s)", lambda r: not r["indoor"] and r["rest_v"] is not None and r["rest_v"] < 1.8),
                    ("outdoor jog (>=1.8 m/s)", lambda r: not r["indoor"] and r["rest_v"] is not None and r["rest_v"] >= 1.8)):
        y = np.array([r["wbal"][0.35] - loo["tau 0.35"][r["id"]] for r in R if f_(r)])
        if len(y):
            lo, hi = boot(lambda ix: np.median(y[ix]), len(y), B=1000)
            print(f"  {lab:34s} n={len(y):3d} median {np.median(y):+.2f} [{lo:+.2f},{hi:+.2f}]")
    print("\nshort reps (<=90 s) vs medium (150-300 s) vs long (>300 s), tau 0.35 LOO residual medians:")
    for lab, a_, b_ in (("<=90 s", 0, 90), ("90-150 s", 90, 150), ("150-300 s", 150, 300), (">300 s", 300, 1e9)):
        y = np.array([r["wbal"][0.35] - loo["tau 0.35"][r["id"]] for r in R if a_ < r["rep_s"] <= b_])
        if len(y):
            lo, hi = boot(lambda ix: np.median(y[ix]), len(y), B=1000)
            print(f"  {lab:10s} n={len(y):3d} median {np.median(y):+.2f} [{lo:+.2f},{hi:+.2f}]")
    return R


def main():
    person = next((a for a in sys.argv[1:] if not a.startswith("-")), "adrian")
    use_cache = "--no-cache" not in sys.argv
    acts, feats, _ = fm.load_features(person, progress=False)
    # typical jog speed in rests from the data (for the synthetic jog case) is filled after part 2; use 2.2 m/s default
    R = part2(person, use_cache)
    jog = [r["rest_v"] for r in R if not r["indoor"] and r["rest_v"] is not None and r["rest_v"] >= 1.8]
    part1(round(float(np.median(jog)), 1) if jog else 2.2)


if __name__ == "__main__":
    main()

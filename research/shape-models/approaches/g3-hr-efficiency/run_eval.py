"""Evaluate G3 variants with the shared harness. Usage: run_eval.py [quick|grid|all]"""
import sys, json, time
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[1]))
import pandas as pd
import harness as H
import model as M

PERS = ("adrian", "rise", "patrik")


def row(res):
    S = res["summary"]; hl = res["headline"]
    def g(t, p):
        x = res["preds"]; x = x[(x.tier == t) & (x.person == p) & x.pred.notna()]
        return f"{(x.pred - x.actual).abs().mean():.2f} ({len(x)})" if len(x) else None
    def ga(t):
        x = res["preds"]; x = x[(x.tier == t) & x.person.isin(["adrian", "rise"]) & x.pred.notna()]
        return f"{(x.pred - x.actual).abs().mean():.2f} ({len(x)})" if len(x) else None
    out = {"model": res["name"]}
    for p in PERS:
        out[f"race {p}"] = g("race", p)
    out["race a+r"] = ga("race"); out["interval a+r"] = ga("interval"); out["hard a+r"] = ga("hard_effort")
    out["interval patrik"] = g("interval", "patrik"); out["hard patrik"] = g("hard_effort", "patrik")
    S2 = S[(S.person == "all") & (S.band == "all")].set_index("tier")
    if "cover_1sd" in S2:
        out["cover1sd race"] = round(float(S2.loc["race", "cover_1sd"]), 2) if "race" in S2.index else None
    return out


def ev(fn, name, **kw):
    t = time.time()
    r = H.evaluate(M.make(fn, name, **kw), persons=PERS, name=name)
    print(f"{name}: {r['headline']}  ({time.time()-t:.0f}s)", flush=True)
    return r


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "quick"
    res = [H.evaluate(H.production_curve, persons=PERS, name="production_curve"),
           H.evaluate(H.naive_best_recent, persons=PERS, name="naive_best_recent_60d")]
    if mode == "final":
        res.append(ev(M.hr_alone, "hr_alone (cal=prod, default)"))
        res.append(ev(M.hr_alone, "hr_alone cal=targets (no production)", cal="targets"))
        res.append(ev(M.hr_alone, "hr_alone cal=none (population Swain)", cal="none"))
        res.append(ev(M.hr_blend, "hr_blend inverse-variance", w_inflate=1.0))
        res.append(ev(M.hr_blend, "hr_blend w_inflate=4", w_inflate=4.0))
        res.append(ev(M.hr_stream, "hr_stream s_mult=1", s_mult=1.0))
        res.append(ev(M.hr_stream, "hr_stream s_mult=2", s_mult=2.0))
        res.append(ev(M.hr_shift, "hr_shift w=0.15", w=0.15))
        res.append(ev(M.hr_shift, "hr_shift w=0.3", w=0.3))
        res.append(ev(M.hr_shift, "hr_shift w=learned (time-forward)"))
        res.append(ev(M.hr_shift, "hr_shift cal=targets w=learned", cal="targets"))
        out = M.CM.OUT / "g3"; out.mkdir(exist_ok=True)
        pd.concat([r["preds"].assign(model=r["name"]) for r in res]).to_parquet(out / "preds_final.parquet", index=False)
    if mode == "quick":
        res.append(ev(M.hr_alone, "hr_alone"))
        res.append(ev(M.hr_alone, "hr_alone_targets", cal="targets"))
        res.append(ev(M.hr_blend, "hr_blend"))
        res.append(ev(M.hr_stream, "hr_stream"))
    if mode == "grid":   # one-at-a-time sensitivity around DEFAULT
        GRID = {"q": [0.003, 0.03, 0.1], "b": [20.0, 50.0, "fit"], "h0": [0.62, 0.78], "drift": ["none"],
                "grade": ["none"], "cal_days": [180, None], "k_shrink": [2.0, 50.0], "robust_z": [99.0],
                "max_age": [21, 365], "sd_floor": [3.5], "devices": [("fenix3_hr/strap", "4315/wrist")]}
        res.append(ev(M.hr_alone, "alone default"))
        res.append(ev(M.hr_alone, "alone curve=False", curve=False))
        for k, vs in GRID.items():
            for v in vs:
                res.append(ev(M.hr_alone, f"alone {k}={v}", **{k: v}))
    if mode == "combine":
        for w in (0.5, 1.0, 2.0, 4.0, 8.0):
            res.append(ev(M.hr_blend, f"blend w_inflate={w}", w_inflate=w))
        for sm in (0.7, 1.0, 1.5, 2.0, 3.0):
            res.append(ev(M.hr_stream, f"stream s_mult={sm}", s_mult=sm))
        res.append(ev(M.hr_stream, "stream kind=hard", stream_kind="hard"))
        res.append(ev(M.hr_stream, "stream trusted devices only", devices=("fenix3_hr/strap", "4315/wrist")))
    tab = pd.DataFrame([row(r) for r in res])
    tab.to_csv(HERE / f"results_{mode}.csv", index=False)
    print(tab.to_string(index=False))

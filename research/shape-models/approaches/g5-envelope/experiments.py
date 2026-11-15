"""G5 experiments: ablations, sensitivity, envelope quantile and EVT baselines.

/tmp/shape-venv/bin/python research/shape-models/approaches/g5-envelope/experiments.py [group ...]
groups: main ablate sens quant evt  (default: all). Tables -> data/_research/shape/g5/*.csv, preds -> *.parquet
"""
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g5  # noqa: E402
from g5 import Cfg, H, ev, make_gumbel, make_quantile, make_tobit, table  # noqa: E402

g5.OUTDIR.mkdir(parents=True, exist_ok=True)
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40)
B = Cfg()


def run(group, items):
    res = [ev(fn, name) for name, fn in items]
    t = table(res)
    print(f"\n### {group}\n" + t.to_string(index=False), flush=True)
    t.to_csv(g5.OUTDIR / f"{group}.csv", index=False)
    pd.concat([r["preds"].assign(model=r["name"]) for r in res]).to_parquet(g5.OUTDIR / f"{group}_preds.parquet")
    return res


GROUPS = {
    "main": lambda: [("production_curve", H.production_curve), ("naive_best_recent_60d", H.naive_best_recent),
                     ("tobit", make_tobit(B)),
                     ("tobit_bests_only (no prod scores)", make_tobit(replace(B, use_intervals=False))),
                     ("tobit_intervals_only (no bests)", make_tobit(replace(B, use_bests=False))),
                     ("tobit +tempo shortfall", make_tobit(replace(B, tempo_shortfall=True)))],
    "ablate": lambda: [
        ("tobit", make_tobit(B)),
        ("flat (slope 0)", make_tobit(replace(B, flat=True))),
        ("no HR gate", make_tobit(replace(B, hr_gate=False))),
        ("no outlier comp", make_tobit(replace(B, p_out=0.0))),
        ("gps_bias 0", make_tobit(replace(B, gps_bias=0.0))),
        ("intervals exact (pi_int .97)", make_tobit(replace(B, pi_int=0.97))),
        ("bests all censored (pi*0, no HR)", make_tobit(replace(B, pi_scale=0.0, hr_gate=False))),
        ("bests >=1mi", make_tobit(replace(B, min_m=1600.0))),
        ("bests >=5k", make_tobit(replace(B, min_m=5000.0))),
        ("no races as obs", make_tobit(replace(B, use_races=False))),
    ],
    "sens": lambda: [(f"{k}={v}", make_tobit(replace(B, **{k: v}))) for k, vs in [
        ("pi_scale", [0.5, 2.0]), ("pi_int", [0.4, 0.85]), ("U", [10.0, 30.0]), ("sig_best", [1.5, 3.0]),
        ("gps_bias", [0.5, 1.5]), ("q_theta", [0.02, 0.1]), ("slope_sd", [0.75, 3.0])] for v in vs],
    "quant": lambda: [(f"quantile q{q} h{h}{' bests-only' if not iv else ''}",
                       make_quantile(q, h, replace(B, use_intervals=iv)))
                      for q in (0.9, 0.95, 1.0) for h in (30.0, 60.0, 120.0) for iv in (True, False)],
    "evt": lambda: [(f"gumbel ret{r} n{n}{' bests-only' if not iv else ''}",
                     make_gumbel(ret=r, n_weeks=n, cfg=replace(B, use_intervals=iv)))
                    for r in (4.0, 13.0) for n in (13, 26) for iv in (True, False)],
    "combo": lambda: [
        ("tobit", make_tobit(B)),
        ("pi_race 0.85", make_tobit(replace(B, pi_race=0.85))),
        ("pi_race 0.6", make_tobit(replace(B, pi_race=0.6))),
        ("U 15", make_tobit(replace(B, U=15.0))),
        ("U 25", make_tobit(replace(B, U=25.0))),
        ("min 1mi", make_tobit(replace(B, min_m=1600.0))),
        ("min 1mi, slope_sd 3", make_tobit(replace(B, min_m=1600.0, slope_sd=3.0))),
        ("min 1k", make_tobit(replace(B, min_m=1000.0))),
        ("min 1mi, U 25", make_tobit(replace(B, min_m=1600.0, U=25.0))),
        ("min 1mi +tempo shortfall", make_tobit(replace(B, min_m=1600.0, tempo_shortfall=True))),
    ],
}


def retable(group):
    P = pd.read_parquet(g5.OUTDIR / f"{group}_preds.parquet")
    res = [{"name": m, "preds": P[P.model == m]} for m in P.model.unique()]
    t = table(res); t.to_csv(g5.OUTDIR / f"{group}.csv", index=False)
    print(f"\n### {group}\n" + t.to_string(index=False))


if __name__ == "__main__":
    args = sys.argv[1:] or list(GROUPS)
    if args[0] == "retable":
        for gname in args[1:]:
            retable(gname)
        sys.exit()
    for gname in args:
        run(gname, GROUPS[gname]())

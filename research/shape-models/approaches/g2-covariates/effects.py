"""G2: effect-size tables (full-data fits; mode smooth = robust weights iterated on the RTS-smoothed fit).

    /tmp/shape-venv/bin/python research/shape-models/approaches/g2-covariates/effects.py
Writes data/_research/shape/g2/effects_*.csv (summary tables only).
"""
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1])); sys.path.insert(0, str(HERE))
import common as C  # noqa: E402
from model import Config, Fit, prepare, PERSONS  # noqa: E402

OUTD = C.OUT / "g2"; OUTD.mkdir(parents=True, exist_ok=True)
pd.set_option("display.width", 220)

EST = replace(Config(), mode="smooth", include_sub=True, iters=6)

CONTRASTS = [  # name, plus-effects, minus-effects
    ("race vs track interval session (theta)", ["race"], []),
    ("race vs road interval session", ["race"], ["int_road"]),
    ("race (road) vs tempo on road", ["race"], ["tempo", "eff_road"]),
    ("race (road) vs time trial on track", ["race"], ["time_trial"]),
    ("race (road) vs high-HR effort on track", ["race"], ["hr_hard"]),
    ("track race vs road race", ["race_track"], []),
    ("road vs track interval session", ["int_road"], []),
    ("indoor (Bislett) vs track interval session", ["int_indoor"], []),
    ("GPS road effort vs track effort (same intent)", ["eff_road"], []),
    ("tempo (track) vs interval session", ["tempo"], []),
    ("time trial (track) vs interval session", ["time_trial"], []),
    ("high-HR effort (track) vs interval session", ["hr_hard"], []),
    ("steady run best 5k (road) vs interval session", ["steady", "eff_road"], []),
    ("long run best 5k (road) vs interval session", ["long", "eff_road"], []),
    ("kappa: share of minetti-half GAP shift that is spurious", ["kappa"], []),
    ("heat: per degC wrist temp above 24", ["heat"], []),
]


def contrast(f, plus, minus, person=None):
    L = f.L; c = np.zeros(L.n)
    for ks, s in ((plus, 1), (minus, -1)):
        for k in ks:
            if f"b:{k}" not in L.ix:
                return np.nan, np.nan
            c[L.ix[f"b:{k}"]] += s
            if person and k in L.pooled:
                c[L.ix[f"u:{person}:{k}"]] += s
    return float(c @ f.m), float(np.sqrt(c @ f.P @ c))


def table(cfg, label):
    O = prepare(cfg); f = Fit(cfg, O).run()
    rows = []
    for name, pl, mi in CONTRASTS:
        r = {"effect": name}
        for who in ("pooled",) + PERSONS:
            m, s = contrast(f, pl, mi, None if who == "pooled" else who)
            r[who] = m; r[who + "_sd"] = s
        rows.append(r)
    D = pd.DataFrame(rows); D.insert(0, "fit", label)
    nobs = O.groupby(["person", "kind"]).size().unstack(fill_value=0)
    return D, f, nobs


def fmt(D):
    out = []
    for _, r in D.iterrows():
        out.append({"effect": r["effect"], **{w: f"{r[w]:+.2f} ± {r[w + '_sd']:.2f}" for w in ("pooled",) + PERSONS}})
    return pd.DataFrame(out)


if __name__ == "__main__":
    main = {}
    for lab, cfg in (("asym", EST), ("sym", replace(EST, robust="sym"))):
        D, f, nobs = table(cfg, lab)
        main[lab] = D
        print(f"\n=== {lab} (robust weights: {'production asymmetric' if lab == 'asym' else 'symmetric Huber'})")
        print(fmt(D).to_string(index=False))
        D.to_csv(OUTD / f"effects_{lab}.csv", index=False)
    print(nobs)
    # sensitivity of the headline effects to hyperparameters
    keys = [0, 1, 2, 5, 6, 7, 14, 15]
    sens = []
    grid = {"base": {}, "tau=0 (complete pooling)": dict(tau=0.0), "tau=0.5": dict(tau=0.5), "tau=2": dict(tau=2.0),
            "tau=5 (~no pooling)": dict(tau=5.0), "q_theta=0.02": dict(q_theta=0.02), "q_theta=0.10": dict(q_theta=0.10),
            "sigma x1.5": dict(sig_mult=1.5), "no sub-max obs": dict(include_sub=False),
            "no hard-effort obs": dict(use_eff=False), "gap=strava": dict(gap="strava"),
            "heat base 20": dict(heat_base=20.0), "online weights": dict(mode="online")}
    for robust in ("asym", "sym"):
        for nm, kw in grid.items():
            D, _, _ = table(replace(EST, robust=robust, **kw), nm)
            r = {"robust": robust, "setting": nm}
            for i in keys:
                r[D.effect[i][:28]] = f"{D.pooled[i]:+.2f}"
            sens.append(r)
    S = pd.DataFrame(sens)
    print(S.to_string(index=False))
    S.to_csv(OUTD / "effects_sensitivity.csv", index=False)

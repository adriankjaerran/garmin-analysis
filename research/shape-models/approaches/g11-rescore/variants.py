"""G11 task-3 variants (pre-registered in PREREG.md) on engine.py, plus production / C / T1 / S as references.
    /tmp/shape-venv/bin/python research/shape-models/approaches/g11-rescore/score.py variants
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import engine as E  # noqa: E402
import harness as H  # noqa: E402

C_ = E.SPEC
T1 = replace(C_, tempo="own")
S_ = replace(E.PROD, q_slope=0.04, slope_sd=2.0)
DI = dict(int_delta=True, int_delta_mu=0.0, int_delta_sd=1.5)

SPECS = {
    "C": C_,
    "T1": T1,
    "S": S_,
    "V1 C,M=0": replace(C_, slope_mean=0.0),
    "V2 T1,M=0": replace(T1, slope_mean=0.0),
    "V3a C-B": replace(C_, bests=False),
    "V3b T1-B": replace(T1, bests=False),
    "V4 P+dI": replace(E.PROD, **DI),
    "V5 S+dI": replace(S_, **DI),
    "V6 T1-B+dI,M=0": replace(T1, bests=False, slope_mean=0.0, **DI),
    "V7 T1+dI,M=0": replace(T1, slope_mean=0.0, **DI),
    "V8 C,M_adrian=0 (post hoc)": replace(C_, slope_mean_person=(("adrian", 0.0),)),
}
MODELS = {"production": H.production_curve, **{k: E.make(v, k) for k, v in SPECS.items()}}

TESTS = {
    "V9 P+tests": replace(E.PROD, tests_as_races=True),
    "V9 S+tests": replace(S_, tests_as_races=True),
    "V9 C+tests": replace(C_, tests_as_races=True),
    "V9 T1+tests": replace(T1, tests_as_races=True),
    "V10 P+dI,tight slope": replace(E.PROD, slope_sd=0.5, q_slope=0.005 * (0.5 / 1.5) ** 2, **DI),
}
ADDENDUM = {"production": H.production_curve, **{k: E.make(v, k) for k, v in TESTS.items()}}

ADD2 = {
    "P+M": replace(E.PROD, slope_mean=0.75),
    "P+B": replace(E.PROD, bests=True),
    "P+B3k": replace(E.PROD, bests=True, min_m=3000.0),
    "C B3k": replace(C_, min_m=3000.0),
    "T1 B3k": replace(T1, min_m=3000.0),
    "P+T1tempo": replace(E.PROD, tempo="own"),
    "S+T1tempo": replace(S_, tempo="own"),
}
ADDENDUM2 = {"production": H.production_curve, **{k: E.make(v, k) for k, v in ADD2.items()}}

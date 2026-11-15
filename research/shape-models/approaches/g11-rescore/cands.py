"""G11 candidates on the rebuilt (A1-fixed) dataset.

G9's `combined` engine reads production items from activities.prod_* (one item per run). After the A1 fix a run can
carry two items (interval score + long bound), so `_prod_items` is replaced here by a reader of
data/_research/shape/prod_items.parquet (harness.prod_items). G9's files are not modified.

    import cands; cands.CANDS  -> {name: predict_fn}
"""
from __future__ import annotations

import functools
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SM = HERE.parents[1]
sys.dont_write_bytecode = True
for p in (SM, SM / "approaches" / "g9-combined", SM / "approaches" / "g5-envelope"):
    sys.path.insert(0, str(p))
import common as C  # noqa: E402
import harness as H  # noqa: E402
import combined as CB  # noqa: E402
import g5  # noqa: E402


@functools.lru_cache(maxsize=8)
def _prod_items_all(person):
    ev = H.prod_items(person)
    tempo = ((ev.discipline == "long") & (ev.kind == "hard")).to_numpy()
    own_km = (ev.gap_speed * ev.window_s / 1000).to_numpy(float)
    return {"day": ev.day.to_numpy(int), "y": ev.score.to_numpy(float), "s": ev.sigma.to_numpy(float),
            "kind": ev.kind.to_numpy(str), "km": ev.km.to_numpy(float), "tempo": tempo, "own_km": own_km,
            "aid": ev.activity_id.to_numpy()}


CB._prod_items = _prod_items_all          # engine now sees every (id, discipline) item, as production does

SPEC_C = CB.SPEC
SPEC_T1 = replace(CB.SPEC, tempo="own")
SPEC_S = replace(CB.PROD, q_slope=0.04, slope_sd=2.0)
G5 = g5.make_tobit(replace(g5.Cfg(), min_m=1600.0, tempo_shortfall=True), "g5_tobit")


def mean_of(f1, f2, name):
    def f(person, hist, target):
        a, b = f1(person, hist, target), f2(person, hist, target)
        if a is None or b is None:
            return a or b
        return {"vdot": 0.5 * (a["vdot"] + b["vdot"]), "sd": 0.5 * (a["sd"] + b["sd"])}
    f.__name__ = name
    return f


CANDS = {
    "production": H.production_curve,
    "C": CB.make(SPEC_C, "C"),
    "T1": CB.make(SPEC_T1, "T1"),
    "S (G1 fast slope)": CB.make(SPEC_S, "S"),
    "G5": G5,
}
CANDS["mean(C,G5)"] = mean_of(CANDS["C"], G5, "mean(C,G5)")

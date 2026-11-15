"""Extrapolation beyond the longest evidence.

    /tmp/shape-venv/bin/python research/shape-models/approaches/g8-distance-curve/extrap.py

(A) Short-only history: every 10k / half race and every HM+ / 10k hard effort of adrian + rise (+ patrik) is
    predicted from history with all evidence longer than 7 km removed (no 10k / half races, no tempos, no long
    windows): the curve must extrapolate from the ~5 km level. Compares each family's *default* shape (Daniels fit)
    and shared non-Daniels slopes (prod with prior mean +0.5 / +1.0 per doubling), plus the other runner's current
    shape as the prior mean (pooling, other's history up to the same day).
(B) Normal history, races beyond the longest race / hard evidence so far (doublings outside > 0): cap vs no cap.
Writes data/_research/shape/g8/extrap.csv.
"""
from __future__ import annotations

import functools
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1])); sys.path.insert(0, str(HERE))
import common as C  # noqa: E402
import curves as K  # noqa: E402
import harness as H  # noqa: E402

MAXD = 7000.0


@functools.lru_cache(maxsize=4096)
def _short_state(person, day, fam, prior_mean=0.0):
    h = H.history(person, day)
    E = K.evidence(h.acts)
    if E.empty:
        return None
    E = E[E.d <= MAXD].reset_index(drop=True)
    if E.empty or not (E.kind != "bound").any():
        return None
    f = list(K.family(fam))
    if fam == "prod" and prior_mean:
        f[2] = -0.5 * ((f[1][:, 0] - prior_mean) / 1.5) ** 2
    F = K.run_filter(E, tuple(f))
    F["range"] = K.evidence_range(E)
    return F


def _pooled_prior(person, day):
    other = "rise" if person == "adrian" else "adrian"
    st = K._prodk_state(other, day, "mh", "5k")
    return 0.0 if st is None else float(st[0][1])


def run_A():
    T = H.targets(("adrian", "rise", "patrik"), ("race", "hard_effort"))
    T = T[T.km > 7].reset_index(drop=True)
    specs = [("flat (Daniels)", "flat", 0.0), ("prod prior 0", "prod", 0.0), ("prod prior +0.5", "prod", 0.5),
             ("prod prior +1.0", "prod", 1.0), ("prod prior = other runner's slope", "prod", "pool"),
             ("riegel default", "riegel", 0.0), ("cs2 default", "cs2", 0.0), ("pt default", "pt", 0.0)]
    rows = []
    for t in T.itertuples():
        r = {"person": t.person, "date": str(t.date.date()), "tier": t.tier, "km": round(t.km, 1), "actual": round(t.vdot, 2)}
        for name, fam, pm in specs:
            pmv = _pooled_prior(t.person, t.day) if pm == "pool" else pm
            F = _short_state(t.person, t.day, fam, round(pmv, 2))
            if F is None:
                r[name] = np.nan; continue
            v, sd = K.predict_from(F, t.day, t.km, cap=False)
            r[name] = round(v, 2)
        rows.append(r)
    D = pd.DataFrame(rows)
    out = []
    for name, *_ in specs:
        e = D[name] - D.actual
        for grp, m in (("race adrian+rise", (D.tier == "race") & D.person.isin(["adrian", "rise"])),
                       ("hard 10k+ (all)", D.tier == "hard_effort")):
            out.append({"model": name, "set": grp, "n": int(m.sum()), "mae": round(e[m].abs().mean(), 2),
                        "bias": round(e[m].mean(), 2)})
    S = pd.DataFrame(out).pivot(index="model", columns="set", values=["mae", "bias"])
    return D, S


def run_B():
    """Races outside the evidenced range with normal history: production cap vs none."""
    P = pd.read_parquet(C.OUT / "g8" / "eval_all.parquet")
    keep = ["production_curve", "prodK nocap", "prod hl=365", "prod hl=365 nocap", "pt hl=365", "pt hl=365 nocap",
            "riegel hl=365", "riegel hl=365 nocap"]
    P = P[(P.tier == "race") & P.model.isin(keep)]
    rows = []
    for t in H.targets(("adrian", "rise", "patrik"), ("race",)).itertuples():
        st = H._prod_state(t.person, t.day)
        if st is None or st[2] is None:
            continue
        hi = st[2][1]
        out = math.log2(t.km / hi) if t.km > hi * 1.001 else 0.0
        x = P[P.target_id == t.target_id]
        r = {"person": t.person, "date": str(t.date.date()), "km": round(t.km, 1), "longest evid km": round(hi, 1),
             "doublings out": round(out, 2), "actual": round(t.vdot, 1)}
        for m in keep:
            v = x[x.model == m].pred
            r[m] = round(float(v.iloc[0]), 1) if len(v) else np.nan
        rows.append(r)
    return pd.DataFrame(rows)


def main():
    D, S = run_A()
    with pd.option_context("display.width", 250, "display.max_columns", 30):
        print(D.to_string(index=False)); print(S.to_string())
        B = run_B(); print(B.to_string(index=False))
    D.to_csv(C.OUT / "g8" / "extrap_A.csv", index=False); S.to_csv(C.OUT / "g8" / "extrap_A_summary.csv")
    B.to_csv(C.OUT / "g8" / "extrap_B.csv", index=False)


if __name__ == "__main__":
    main()

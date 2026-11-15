"""Per-runner short-vs-long profile with uncertainty, and its stability over time.

    /tmp/shape-venv/bin/python research/shape-models/approaches/g8-distance-curve/profiles.py

At checkpoints (1 Jan / 1 Jul of each year and today) and using only data before the checkpoint:
  * prodK: production 2-state filter (slope OU) -> slope s ± sd (VDOT per doubling) and HM - 5k = 2.08 s
  * prod / riegel / pt grid posteriors, fixed shape (all history) and discounted (half-life 365 d): HM - 5k ± sd;
    pt also 1500 m - 5k (short end; weakly identified: almost no evidence below 5 km)
  * adrian - rise difference of the HM - 5k gap (independent posteriors).
Writes data/_research/shape/g8/profiles.csv.
"""
from __future__ import annotations

import datetime as dt
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1])); sys.path.insert(0, str(HERE))
import common as C  # noqa: E402
import curves as K  # noqa: E402

DX = math.log2(21.0975 / 5)


def checkpoints(person):
    A = C.load("activities", (person,))
    y0 = A.date.min().year + 1
    out = []
    for y in range(y0, 2027):
        for mth in (1, 7):
            d = dt.date(y, mth, 1)
            if d <= dt.date(2026, 11, 4):
                out.append(d)
    out.append(dt.date(2026, 11, 5))
    return out


def main():
    rows = []
    for p in ("adrian", "rise", "patrik"):
        for d in checkpoints(p):
            day = d.toordinal()
            st = K._prodk_state(p, day, "mh", "5k")
            if st is None:
                continue
            m, cv, ekm = st
            r = {"person": p, "date": d.isoformat(), "prodK theta5k": round(m[0], 1),
                 "prodK slope": round(m[1], 2), "prodK slope sd": round(math.sqrt(cv[1, 1]), 2),
                 "prodK HM-5k": round(DX * m[1], 2), "prodK HM-5k sd": round(DX * math.sqrt(cv[1, 1]), 2)}
            for fam in ("prod", "riegel", "pt"):
                F = K._state(p, day, fam, "mh", "5k", 1.0)
                if F is None:
                    continue
                for hl in (None, 365):
                    s = K.psi_summary(F, day, hl)
                    tag = f"{fam} {'fixed' if hl is None else 'hl365'}"
                    r[f"{tag} HM-5k"] = round(s["long_minus_5k"], 2)
                    r[f"{tag} HM-5k sd"] = round(s["long_minus_5k_sd"], 2)
                    if fam == "pt":
                        r[f"{tag} 1500-5k"] = round(s["short_minus_5k"], 2)
                        r[f"{tag} 1500-5k sd"] = round(s["short_minus_5k_sd"], 2)
            rows.append(r)
            print(r, flush=True)
    T = pd.DataFrame(rows)
    T.to_csv(C.OUT / "g8" / "profiles.csv", index=False)
    # adrian - rise at common checkpoints
    a = T[T.person == "adrian"].set_index("date"); b = T[T.person == "rise"].set_index("date")
    common = a.index.intersection(b.index)
    D = pd.DataFrame(index=common)
    for col in ("prodK HM-5k", "prod fixed HM-5k", "prod hl365 HM-5k", "pt fixed HM-5k", "pt hl365 HM-5k"):
        D[col] = (a.loc[common, col] - b.loc[common, col]).round(2)
        D[col + " sd"] = np.sqrt(a.loc[common, col + " sd"] ** 2 + b.loc[common, col + " sd"] ** 2).round(2)
    print(D.to_string())
    D.to_csv(C.OUT / "g8" / "profiles_diff.csv")


if __name__ == "__main__":
    main()

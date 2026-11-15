"""Summary table from the saved per-target predictions (data/_research/shape/g8/eval_<set>.parquet).

    /tmp/shape-venv/bin/python research/shape-models/approaches/g8-distance-curve/tables.py [set] [model-substring ...]
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
import common as C  # noqa: E402


def table(P):
    rows = []
    for m, x in P.groupby("model", sort=False):
        e = x.pred - x.actual
        z = e / np.maximum(x.sd, 0.3)

        def mae(mask):
            mm = mask & x.pred.notna()
            return round(float(e[mm].abs().mean()), 2) if mm.any() else np.nan
        ar = x.person.isin(["adrian", "rise"])
        r, i, h = x.tier == "race", x.tier == "interval", x.tier == "hard_effort"
        mm = r & ar & x.pred.notna()
        rows.append({
            "model": m,
            "race adrian (5)": mae(r & (x.person == "adrian")), "race rise (5)": mae(r & (x.person == "rise")),
            "race a+r (10)": mae(r & ar), "race 10k (5)": mae(r & ar & (x.band == "10k")),
            "race HM+ (4)": mae(r & ar & (x.band == "HM+")),
            "race bias": round(float(e[mm].mean()), 2),
            "race 1sd cov": round(float((z[mm].abs() <= 1).mean()), 2) if x.sd[mm].notna().any() else np.nan,
            "interval a+r": mae(i & ar), "hard a+r (15)": mae(h & ar), "hard all (19)": mae(h),
            "hard 10k+ (3)": mae(h & (x.km > 7)),
            "patrik race": mae(r & (x.person == "patrik")),
        })
    return pd.DataFrame(rows)


def main():
    s = sys.argv[1] if len(sys.argv) > 1 else "all"
    P = pd.read_parquet(C.OUT / "g8" / f"eval_{s}.parquet")
    T = table(P)
    if len(sys.argv) > 2:
        T = T[T.model.apply(lambda m: any(k in m for k in sys.argv[2:]))]
    T.to_csv(C.OUT / "g8" / f"table_{s}.csv", index=False)
    print(md(T))


def md(T):
    f = lambda v: "–" if (isinstance(v, float) and np.isnan(v)) else (f"{v:.2f}" if isinstance(v, float) else str(v))
    out = ["| " + " | ".join(T.columns) + " |", "|" + "---|" * len(T.columns)]
    out += ["| " + " | ".join(f(v) for v in r) + " |" for r in T.itertuples(index=False)]
    return "\n".join(out)


if __name__ == "__main__":
    main()

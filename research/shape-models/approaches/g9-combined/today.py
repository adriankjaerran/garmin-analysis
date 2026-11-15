"""Current estimates (target day 2026-11-05: history = everything up to 2026-11-04) from production, C and C+T1.

    /tmp/shape-venv/bin/python research/shape-models/approaches/g9-combined/today.py
"""
import datetime as dt
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import combined as CB  # noqa: E402
import common as C  # noqa: E402
import harness as H  # noqa: E402

DAY = dt.date(2026, 11, 5).toordinal()
MODELS = {"production": None, "C": CB.SPEC, "C+T1": replace(CB.SPEC, tempo="own")}
DIST = {"5k": 5.0, "10k": 10.0, "HM": 21.0975}

rows = []
for p in C.PERSONS:
    h = H.history(p, DAY)
    for name, sp in MODELS.items():
        extra = ""
        for lab, km in DIST.items():
            tgt = {"km": km, "tier": "race", "workout": "race"}
            r = H.production_curve(p, h, tgt) if sp is None else CB.make(sp)(p, h, tgt)
            v, s = r["vdot"], r["sd"]
            t, tf, ts = (C.race_time(x, km * 1000) for x in (v, v + s, v - s))
            rows.append({"person": p, "model": name, "dist": lab, "vdot": round(v, 1), "sd": round(s, 1),
                         "time": C.fmt_time(t), "range ±1sd": f"{C.fmt_time(tf)}–{C.fmt_time(ts)}"})
        if sp is not None:
            st = CB.cached_fit(p, DAY, sp)
            r5 = CB.read(st, 5.0, sp)
            extra = f"theta {r5['theta']:.1f} slope {r5['slope']:+.2f} ± {r5['slope_sd']:.2f}"
            if sp.tempo == "own":
                extra += f", tempo shortfall delta {st['m'][2]:.2f} ± {np.sqrt(st['P'][2, 2]):.2f}"
            print(p, name, extra, "evidence km range", st["ekm"])
T = pd.DataFrame(rows)
(HERE / "results").mkdir(exist_ok=True)
T.to_csv(HERE / "results" / "today.csv", index=False)
W = T.assign(cell=T.vdot.map("{:.1f}".format) + " ± " + T.sd.map("{:.1f}".format) + " (" + T.time + ", " + T["range ±1sd"] + ")")
print(W.pivot_table(index=["person", "model"], columns="dist", values="cell", aggfunc="first").to_string())

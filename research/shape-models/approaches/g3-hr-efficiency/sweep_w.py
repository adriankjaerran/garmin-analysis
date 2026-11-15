"""Sensitivity of prod + w*(hr - prod) to w (cal=prod and cal=targets); MAE per tier, adrian + rise."""
import sys, warnings
from pathlib import Path
warnings.filterwarnings("ignore")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[1]))
import pandas as pd, harness as H, model as M

rows = []
for cal in ("prod", "targets"):
    for w in (0.0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 1.0):
        P = H.evaluate(M.make(M.hr_shift, "s", w=w, cal=cal), persons=("adrian", "rise"))["preds"]
        P = P[P.pred.notna()]
        r = {"cal": cal, "w": w}
        for (t, p), g in P.groupby(["tier", "person"]):
            r[f"{t[:4]} {p[:3]}"] = round(float((g.pred - g.actual).abs().mean()), 2)
        r["race a+r"] = round(float((P[P.tier == "race"].pred - P[P.tier == "race"].actual).abs().mean()), 2)
        rows.append(r)
T = pd.DataFrame(rows); print(T.to_string(index=False)); T.to_csv(HERE / "results_sweep_w.csv", index=False)

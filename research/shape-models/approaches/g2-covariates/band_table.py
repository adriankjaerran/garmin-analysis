"""G2: per tier x person x band MAE (bias) table from the saved prediction files (run.py eval first)."""
import sys
from pathlib import Path
import pandas as pd
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
import common as C  # noqa: E402
import harness as H  # noqa: E402

MODELS = sys.argv[1:] or ["production_curve", "g2", "g2_race_heat", "g2_core", "prodcorr_heat", "prodcorr_all"]
rows = []
for m in MODELS:
    P = pd.read_parquet(C.OUT / "g2" / f"preds_{m}.parquet").drop(columns="model")
    S = H.summarize(P)
    for _, r in S.iterrows():
        rows.append({"model": m, "tier": r.tier, "person": r.person, "band": r.band, "n": int(r.n_pred),
                     "cell": f"{r.mae:.2f} ({r.bias:+.2f})"})
D = pd.DataFrame(rows)
X = D.pivot_table(index=["tier", "person", "band", "n"], columns="model", values="cell", aggfunc="first")[MODELS]
X = X.reset_index()
print("| " + " | ".join(X.columns) + " |"); print("|" + "---|" * len(X.columns))
for _, r in X.iterrows():
    print("| " + " | ".join(str(v) for v in r.values) + " |")

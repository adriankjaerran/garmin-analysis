"""Score production_curve, G5 tobit (recommended), G1 mobile slope, G8 predict (+ the audit's all-items production)
on official races + extra targets. /tmp/shape-venv/bin/python research/shape-models/approaches/g10-audit/run_ext.py
Writes data/_research/shape/g10/ext_preds.parquet, ext_summary.csv, ext_paired.csv."""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
APP = HERE.parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[1]))
import common as C  # noqa: E402
import harness as H  # noqa: E402
from harness_ext import evaluate_ext, paired, summary_ext  # noqa: E402
from prod_fixed import production_curve_all  # noqa: E402


def load(folder, mod, alias):
    sys.path.insert(0, str(APP / folder))
    spec = importlib.util.spec_from_file_location(alias, APP / folder / f"{mod}.py")
    m = importlib.util.module_from_spec(spec); sys.modules[alias] = m; spec.loader.exec_module(m)
    sys.path.remove(str(APP / folder))
    return m


g5 = load("g5-envelope", "g5", "g5mod")
g1 = load("g1-kalman", "model", "g1model")
g8 = load("g8-distance-curve", "model", "g8model")

MODELS = [("production_curve", H.production_curve),
          ("g5_tobit_1mi_shortfall", g5.make_tobit(replace(g5.Cfg(), min_m=1600.0, tempo_shortfall=True))),
          ("g1_mobile_slope", g1.predict),
          ("g8_prodK_qs0.02", g8.predict),
          ("audit_prod_all_items", production_curve_all)]

if __name__ == "__main__":
    pd.set_option("display.width", 250); pd.set_option("display.max_rows", 300)
    out = C.OUT / "g10"
    allP, allS, allD = [], [], []
    for inc, tag in ((("high",), "official+high"), (("high", "medium"), "official+high+medium")):
        res = [evaluate_ext(f, include=inc, name=n) for n, f in MODELS]
        base = res[0]
        S = pd.concat([summary_ext(r) for r in res]).assign(set_def=tag)
        D = pd.concat([paired(r, base) for r in res[1:]]).assign(set_def=tag)
        allS.append(S); allD.append(D)
        allP.append(pd.concat([r["preds"].assign(model=r["name"], set_def=tag) for r in res]))
        print(f"\n##### {tag}")
        print(S.pivot_table(index=["person", "set"], columns="model", values="mae", sort=False).round(2).to_string())
        print(S[S.model == "production_curve"][["person", "set", "n"]].to_string(index=False))
        print(D.round(3).to_string(index=False))
    pd.concat(allP).to_parquet(out / "ext_preds.parquet", index=False)
    pd.concat(allS).to_csv(out / "ext_summary.csv", index=False)
    pd.concat(allD).to_csv(out / "ext_paired.csv", index=False)
    P = pd.concat(allP)
    P = P[(P.set_def == "official+high+medium") & (P.tier_src == "extra")]
    print(P.pivot_table(index=["target_id", "person", "confidence", "actual"], columns="model", values="pred_filled").round(2).to_string())

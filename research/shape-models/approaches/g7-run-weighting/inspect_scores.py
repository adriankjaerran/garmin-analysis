"""G7: sanity tables - example runs with their time-forward scores, calibration, logit coefficients, per-race errors.
    /tmp/shape-venv/bin/python research/shape-models/approaches/g7-run-weighting/inspect_scores.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[1]))
import classifier as K  # noqa: E402
import features as F  # noqa: E402

pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30)
E = pd.read_parquet(F.G7 / "classifier_eval.parquet")
pn, pf, ph = "p::logit/nodelta", "p::logit/full", "p::hr_intent (prod formula)"
E["p_rule"] = E["p::prod_rules (int+tempo=1)"]
cols = ["person", "date", "workout", "src", "km", "y", "prev_est", "hrr_eff", "trust", "r_final", pn, pf, ph]
ev = E[E.ev].copy()
rng = np.random.default_rng(1)
pick = []
for p in ("adrian", "rise"):
    x = ev[ev.person == p]
    pick += list(x[x.workout == "tempo"].index[-2:]) + list(x[x.workout == "steady"].sample(2, random_state=1).index)
    pick += list(x[x.workout == "easy"].sample(1, random_state=2).index) + list(x[x.workout == "long"].sample(1, random_state=3).index)
    xi = x[x.src == "int"].sort_values(pn)
    pick += list(xi.index[:2]) + list(xi.index[-1:])
pick += list(ev[ev.workout == "time_trial"].index) + list(ev[(ev.person == "patrik") & (ev.workout == "tempo")].index[-1:])
ex = E.loc[sorted(set(pick)), cols].sort_values(["person", "date"])
print("example runs (time-forward p; r_final = y - reference smoothed on all data; label = r_final >= -1.5)")
print(ex.rename(columns={pn: "p_nodelta", pf: "p_full", ph: "p_hrintent"}).round(2).to_string(index=False))
ex.to_csv(F.G7 / "examples.csv", index=False)

print("\ncalibration (logit/nodelta, time-forward, all non-race candidates)")
m = ev[pn].notna()
b = pd.cut(ev.loc[m, pn], [0, .05, .2, .5, .8, 1.0001])
print(ev[m].groupby(b, observed=True).agg(n=("label_final", "size"), p_mean=(pn, "mean"), obs_rate=("label_final", "mean"),
                                          r_median=("r_final", "median")).round(2).to_string())

print("\nlogit/nodelta standardized coefficients (last checkpoint)")
D = K.load(); X = K.select(F.feature_matrix(D), "nodelta")
c = max(K.checkpoints(D)); c = [k for k in K.fitted(model="logit", fs="nodelta")][-1]
r = K.residual_vs_ref(D, c); is_race = (D.src == "race").to_numpy()
tr = (D.day < c).to_numpy() & (~np.isnan(r) | is_race)
yl = ((r[tr] >= -1.5) | is_race[tr]).astype(int)
mdl = K.make_model("logit").fit(X[tr], yl)
coef = pd.Series(mdl[-1].coef_[0], index=X.columns).sort_values()
print(coef.round(2).to_string())

P = pd.read_parquet(F.G7 / "harness_preds.parquet")
r = P[(P.tier == "race") & P.pred.notna()].assign(err=lambda d: d.pred - d.actual)
t = r.pivot_table(index=["person", "date", "km", "actual"], columns="model", values="err")
keep = ["production_curve", "prod_p[hr_intent,pmin=0.5]", "prod_p[learned,logit/nodelta,pmin=0.5]",
        "prod_p[learned,logit/nodelta,pmin=0.3]", "g7_rules", "g7_p[logit/nodelta,pmin=0.5]", "g7_soft[logit/nodelta,pmin=0.5]"]
print("\nper-race error (pred - actual)")
print(t[keep].round(2).to_string())

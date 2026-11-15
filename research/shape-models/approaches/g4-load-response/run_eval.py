"""G4 evaluation: candidate load-response variants vs production through harness.evaluate, gap analysis.
    /tmp/shape-venv/bin/python research/shape-models/approaches/g4-load-response/run_eval.py
Writes data/_research/shape/g4/preds.parquet and prints the tables used in REPORT.md."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[1]))
import harness as H  # noqa: E402
import kf  # noqa: E402
import load as L  # noqa: E402
import model as M  # noqa: E402

PERS = ("adrian", "rise", "patrik")
CANDS = {
    "kalman+ctl42 (RW, b)": dict(taus=(42,), beta_sd=(1.0,)),
    "kalman+ctl42+atl7 (RW, b)": dict(taus=(42, 7), beta_sd=(1.0, 1.0)),
    "kalman+ctl42 prior N(1,0.5) (b)": dict(taus=(42,), beta_mu=(1.0,), beta_sd=(0.5,)),
    "kalman+ctl42 ski=0 (b)": dict(taus=(42,), beta_sd=(1.0,), ski=0.0),
    "kalman+ctl42 ski=1 (b)": dict(taus=(42,), beta_sd=(1.0,), ski=1.0),
    "kalman+ctl42 km (b)": dict(taus=(42,), beta_sd=(1.0,), metric="km"),
    "AR180 sd2 + ctl42 (b')": dict(taus=(42,), beta_sd=(1.5,), ar_tau=180, ar_sd=2.0),
    "AR365 sd4 + ctl42 (b')": dict(taus=(42,), beta_sd=(1.5,), ar_tau=365, ar_sd=4.0),
    "AR365 sd4, no load": dict(taus=(), ar_tau=365, ar_sd=4.0),
    "banister ctl42-atl7 (a)": dict(taus=(42, 7), beta_sd=(2.0, 2.0), q_theta=1e-6),
    "banister 28/5 drift q=0.01 (a)": dict(taus=(28, 5), beta_sd=(2.0, 2.0), q_theta=0.01),
    "busso 42/(7,14) (a)": dict(taus=(42, ("busso", 7, 14)), beta_sd=(2.0, 2.0), q_theta=1e-6),
    "prod + taper 0.5*TSB (c)": dict(taus=(), taper=0.5),
    "prod + taper -0.5*TSB (c)": dict(taus=(), taper=-0.5),
}


def gap_of(person, day):
    obs = [o for o in kf.prod_obs(person, day) if o[3] in ("hard", "race")]
    return day - obs[-1][0] if obs else np.nan


def main():
    res = [H.evaluate(H.naive_best_recent, persons=PERS, name="naive_best_recent_60d"),
           H.evaluate(H.production_curve, persons=PERS, name="production_curve")]
    for nm, kw in CANDS.items():
        res.append(H.evaluate(M.make(**kw), persons=PERS, name=nm))
    rows = []
    for r in res:
        h = r["headline"]; S = r["summary"]
        def g(t, p, c="mae"):
            x = S[(S.tier == t) & (S.person == p) & (S.band == "all")]
            return float(x.iloc[0][c]) if len(x) else np.nan
        rp = r["preds"]; ar = rp[rp.person.isin(["adrian", "rise"])]
        def mae(t):
            x = ar[(ar.tier == t) & ar.pred.notna()]
            return float((x.pred - x.actual).abs().mean())
        rows.append({"model": r["name"], "race A": h["race_mae_adrian"], "race R": h["race_mae_rise"],
                     "race A+R": h["race_mae_adrian_rise"], "race patrik": g("race", "patrik"),
                     "interval A+R": mae("interval"), "hard A+R": mae("hard_effort"),
                     "interval all": h["interval_mae"], "hard all": h["hard_effort_mae"],
                     "race nll": g("race", "all", "nll") if "nll" in S else np.nan,
                     "race cov1": g("race", "all", "cover_1sd") if "cover_1sd" in S else np.nan})
    tab = pd.DataFrame(rows)
    print(tab.to_string(index=False, float_format="{:.2f}".format))
    P = pd.concat([r["preds"].assign(model=r["name"]) for r in res])
    T = H.load_data()[2].set_index("target_id")
    P["day"] = P.target_id.map(T.day)
    gaps = {(p, d): gap_of(p, d) for p, d in P[["person", "day"]].drop_duplicates().itertuples(index=False)}
    P["gap"] = [gaps[(p, d)] for p, d in zip(P.person, P.day)]
    P["ski_h_gap"] = [float(np.sum(L.series(p, "h", 1.0, 0.0)[1][int(d - g) - L.series(p, "h", 1.0, 0.0)[0]:
                                                              int(d) - L.series(p, "h", 1.0, 0.0)[0]]) -
                            np.sum(L.series(p, "h", 0.0, 0.0)[1][int(d - g) - L.series(p, "h", 0.0, 0.0)[0]:
                                                                 int(d) - L.series(p, "h", 0.0, 0.0)[0]]))
                      if g == g else np.nan for p, d, g in zip(P.person, P.day, P.gap)]
    P["err"] = P.pred - P.actual
    (L.G4).mkdir(parents=True, exist_ok=True)
    P.to_parquet(L.G4 / "preds.parquet", index=False)
    print("\n--- after gaps >= 42 d without hard/race evidence (adrian + rise, all tiers): MAE / bias")
    Q = P[(P.gap >= 42) & P.person.isin(["adrian", "rise"]) & P.pred.notna()]
    g = Q.groupby("model").agg(n=("err", "size"), mae=("err", lambda e: e.abs().mean()), bias=("err", "mean"))
    print(g.loc[tab.model].round(2).to_string())
    print("\n--- those targets (production vs RW+ctl42 vs AR180+ctl42)")
    W = Q[Q.model.isin(["production_curve", "kalman+ctl42 (RW, b)", "AR180 sd2 + ctl42 (b')"])].pivot_table(
        index=["person", "date", "tier", "gap", "ski_h_gap", "actual"], columns="model", values="pred")
    print(W.round(1).to_string())
    return tab, P


if __name__ == "__main__":
    main()

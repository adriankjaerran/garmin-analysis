"""Ensembles, robustness and today's estimates for G9 (reads data/_research/shape/g9/{configs,members/*}.parquet).

    /tmp/shape-venv/bin/python research/shape-models/approaches/g9-combined/analyze.py
Writes results/*.csv / *.md (small tables) and data/_research/shape/g9/all_preds.parquet.
"""
import datetime as dt
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import binomtest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import combined as CB  # noqa: E402
import common as C  # noqa: E402
import harness as H  # noqa: E402
from run_eval import CONFIGS, metrics  # noqa: E402

G9 = C.OUT / "g9"
RES = HERE / "results"; RES.mkdir(exist_ok=True)
rng = np.random.default_rng(9)
MEMBERS = ["production", "g1", "g2", "g3", "g4", "g5", "g6", "g7", "g8"]


def md(df, fmt="{:.2f}"):
    f = lambda v: fmt.format(v) if isinstance(v, (float, np.floating)) and not pd.isna(v) else ("" if pd.isna(v) else str(v))
    lines = ["| " + " | ".join(map(str, df.columns)) + " |", "|" + "---|" * len(df.columns)]
    lines += ["| " + " | ".join(f(v) for v in r) + " |" for r in df.itertuples(index=False)]
    return "\n".join(lines)


def load():
    cf = pd.read_parquet(G9 / "configs.parquet")
    mem = pd.concat([pd.read_parquet(G9 / "members" / f"{m}.parquet") for m in MEMBERS])
    mem["model"] = "m:" + mem.model
    base = cf[cf.model == "production"].set_index("target_id")
    allp = pd.concat([cf, mem])
    W = allp.pivot_table(index="target_id", columns="model", values="pred")
    SD = allp.pivot_table(index="target_id", columns="model", values="sd")
    return allp, W, SD, base


def ens(W, SD, cols, how):
    X = W[cols]
    if how == "mean":
        return X.mean(axis=1), None
    if how == "median":
        return X.median(axis=1), None
    if how == "ivw":
        S = SD[cols].where(SD[cols].notna(), np.nan)
        w = 1 / np.maximum(S, 0.3) ** 2
        w = w.where(X.notna())
        p = (X * w).sum(axis=1) / w.sum(axis=1)
        return p, np.sqrt(1 / w.sum(axis=1))


def frame(base, pred, sd, name):
    P = base[["person", "date", "tier", "band", "km", "weight", "actual", "note"]].copy()
    P["pred"] = pred.reindex(P.index); P["sd"] = sd.reindex(P.index) if sd is not None else np.nan
    P["model"] = name
    return P.reset_index()


def main():
    allp, W, SD, base = load()
    mcols = ["m:" + m for m in MEMBERS]
    # sd for mean ensembles: mean of member sds (members without sd are ignored)
    msd = SD[mcols].mean(axis=1)
    E = {}
    E["ens_mean_all"] = (ens(W, SD, mcols, "mean")[0], msd)
    E["ens_median_all"] = (ens(W, SD, mcols, "median")[0], msd)
    E["ens_ivw_all"] = ens(W, SD, [c for c in mcols if SD[c].notna().any()], "ivw")
    E["ens_prod_g5"] = (W[["m:production", "m:g5"]].mean(axis=1), SD[["m:production", "m:g5"]].mean(axis=1))
    E["ens_C_g5"] = (W[["C (pre-registered)", "m:g5"]].mean(axis=1), SD[["C (pre-registered)", "m:g5"]].mean(axis=1))
    E["ens_C_prod"] = (W[["C (pre-registered)", "production"]].mean(axis=1), SD[["C (pre-registered)", "production"]].mean(axis=1))
    ensP = pd.concat([frame(base, p, s, n) for n, (p, s) in E.items()])
    keep = allp[allp.model.isin(mcols)]
    P = pd.concat([allp[~allp.model.isin(mcols)], keep, ensP], ignore_index=True)
    P.to_parquet(G9 / "all_preds.parquet", index=False)

    # ---- member + ensemble table
    rows = [metrics(P[P.model == m], m) for m in mcols + list(E)]
    T = pd.DataFrame(rows)
    T.round(3).to_csv(RES / "ensembles.csv", index=False)
    cols = ["model", "race adrian", "race rise", "race a+r", "race patrik", "race bias a+r", "race ±1sd", "race ±2sd",
            "interval", "hard_effort"]
    print("\n### members and ensembles\n" + md(T[cols]))

    # ---- robustness vs production
    cands = ["P+S (fast slope)", "P+B (bests)", "C (pre-registered)", "C-B", "C+T1 (tempo own dist + learned shortfall)",
             "C+T2 (tempo as censored best)", "m:g5", "ens_mean_all", "ens_C_g5"]
    prodE = P[P.model == "production"].set_index("target_id")
    rob = []
    for c in cands:
        X = P[P.model == c].set_index("target_id")
        for tier in ("race", "interval", "hard_effort"):
            ids = prodE.index[(prodE.tier == tier) & (prodE.person.isin(["adrian", "rise"]) if tier == "race" else True)]
            ids = [i for i in ids if pd.notna(X.pred.get(i)) and pd.notna(prodE.pred.get(i))]
            a = (X.loc[ids, "pred"] - X.loc[ids, "actual"]).abs().to_numpy()
            b = (prodE.loc[ids, "pred"] - prodE.loc[ids, "actual"]).abs().to_numpy()
            d = a - b
            wins = int((d < -1e-9).sum()); losses = int((d > 1e-9).sum())
            p = binomtest(wins, wins + losses).pvalue if wins + losses else np.nan
            bs = rng.integers(0, len(d), (10000, len(d)))
            ci = np.percentile(d[bs].mean(axis=1), [5, 95])
            row = {"model": c, "tier": tier, "n": len(d), "MAE": a.mean(), "prod MAE": b.mean(), "diff": d.mean(),
                   "boot 90% lo": ci[0], "boot 90% hi": ci[1], "wins": wins, "losses": losses, "sign p": p}
            if tier == "interval":       # 90-day block bootstrap per person
                dd = prodE.loc[ids, ["person", "date"]].assign(d=d)
                dd["blk"] = dd.person + (pd.to_datetime(dd.date).map(dt.date.toordinal) // 90).astype(str)
                g = dd.groupby("blk").d.agg(["sum", "count"])
                bi = rng.integers(0, len(g), (10000, len(g)))
                s = g["sum"].to_numpy()[bi].sum(axis=1) / g["count"].to_numpy()[bi].sum(axis=1)
                row["block90 lo"], row["block90 hi"] = np.percentile(s, [5, 95])
            rob.append(row)
    R = pd.DataFrame(rob)
    R.round(3).to_csv(RES / "robustness.csv", index=False)
    print("\n### paired vs production\n" + md(R))

    # ---- per-race table
    races = prodE[(prodE.tier == "race")].sort_values(["person", "date"])
    show = ["production", "P+S (fast slope)", "P+B (bests)", "C (pre-registered)", "C-B",
            "C+T1 (tempo own dist + learned shortfall)", "C+T2 (tempo as censored best)", "m:g5", "ens_mean_all",
            "ens_C_g5"]
    PR = pd.DataFrame({"race": [f"{r.person} {r.note or ''} {str(r.date)[:10]}".strip() for r in races.itertuples()],
                       "actual": races.actual.round(2).to_numpy()}, index=races.index)
    for m in show:
        X = P[P.model == m].set_index("target_id")
        PR[m] = (X.pred - X.actual).reindex(races.index).round(2)
        PR[m + " z"] = ((X.pred - X.actual) / X.sd).reindex(races.index).round(2)
    PR.to_csv(RES / "per_race.csv")
    print("\n### per race (pred - actual)\n" + md(PR[["race", "actual"] + show]))

    # ---- leave-one-race-out influence and selection; without rise's Bilbao / Copenhagen halves
    ar = races[races.person.isin(["adrian", "rise"])]
    err = {m: (P[P.model == m].set_index("target_id").pred - ar.actual).reindex(ar.index).abs()
           for m in P.model.unique()}
    err = pd.DataFrame(err)
    lo = []
    for i in ar.index:
        rest = err.drop(i)
        lo.append({"left out": PR.loc[i, "race"], **{m: rest[m].mean() - rest["production"].mean() for m in show[1:]}})
    LO = pd.DataFrame(lo)
    LO.round(2).to_csv(RES / "loro_influence.csv", index=False)
    print("\n### MAE(model) - MAE(production) on the other 9 races\n" + md(LO))
    sel_pool = {"configs": list(CONFIGS), "configs+members+ensembles": [c for c in err.columns]}
    for lab, pool in sel_pool.items():
        e = []
        picks = []
        for i in ar.index:
            pick = err.drop(i)[pool].mean().idxmin(); picks.append(pick)
            e.append(err.loc[i, pick])
        print(f"LORO selection over {lab}: MAE {np.mean(e):.3f}; picks {pd.Series(picks).value_counts().to_dict()}")
    halves = [i for i in ar.index if PR.loc[i, "race"].startswith("rise") and ("Bilbao" in PR.loc[i, "race"] or
                                                                              "Copenhagen" in PR.loc[i, "race"])]
    wo = err.drop(halves)
    print("\n### race MAE without rise's Bilbao + Copenhagen halves (8 races)")
    print(wo[show].mean().round(3).to_string())
    wo[show].mean().round(3).to_csv(RES / "without_rise_halves.csv")


if __name__ == "__main__":
    pd.set_option("display.width", 250)
    main()

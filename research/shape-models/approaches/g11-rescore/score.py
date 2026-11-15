"""Score G11 candidates on the A1-fixed dataset: official races, official + high extras (G10 harness_ext), interval
and hard_effort tiers, per person; paired |error| differences vs production with sign tests.

    /tmp/shape-venv/bin/python research/shape-models/approaches/g11-rescore/score.py [variants]
Writes data/_research/shape/g11/preds_<tag>.parquet and research/.../g11-rescore/results/*_<tag>.csv
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import binomtest

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent / "g10-audit"))
import cands  # noqa: E402
import common as C  # noqa: E402
import harness as H  # noqa: E402
from harness_ext import evaluate_ext  # noqa: E402

OUT = C.OUT / "g11"; OUT.mkdir(parents=True, exist_ok=True)
RES = HERE / "results"; RES.mkdir(exist_ok=True)


def run(models):
    rows = []
    for name, f in models.items():
        a = H.evaluate(f, persons=("adrian", "rise", "patrik"), name=name, verbose=True)["preds"]
        a = a.assign(set=np.where(a.tier == "race", "official", a.tier))
        x = evaluate_ext(f, include=("high",), name=name)["preds"]
        x = x[x.tier_src == "extra"].assign(tier="race", set="extra_high")
        p = pd.concat([a, x], ignore_index=True).assign(model=name)
        p["err"] = p.pred_filled - p.actual
        rows.append(p)
        print(name, "done", flush=True)
    return pd.concat(rows, ignore_index=True)


SETS = {"official": ["official"], "official+high": ["official", "extra_high"], "extra_high": ["extra_high"],
        "interval": ["interval"], "hard_effort": ["hard_effort"]}
PERS = {"adrian": ["adrian"], "rise": ["rise"], "patrik": ["patrik"], "a+r": ["adrian", "rise"],
        "all": ["adrian", "rise", "patrik"]}


def tables(P, base="production", n_boot=4000, seed=11):
    rng = np.random.default_rng(seed)
    B = P[P.model == base].set_index("target_id")
    mae, pair = [], []
    for s, sl in SETS.items():
        for pn, pl in PERS.items():
            g = P[P.set.isin(sl) & P.person.isin(pl)]
            if g.empty:
                continue
            for m, gm in g.groupby("model", sort=False):
                e = gm.err.dropna()
                mae.append({"set": s, "person": pn, "model": m, "n": len(e), "mae": e.abs().mean(), "bias": e.mean()})
                if m == base:
                    continue
                gm = gm.set_index("target_id")
                d = (gm.err.abs() - B.loc[gm.index, "err"].abs()).dropna().to_numpy()
                w, l = int((d < -0.05).sum()), int((d > 0.05).sum())
                bs = rng.choice(d, (n_boot, len(d))).mean(1) if len(d) > 1 else np.array([d.mean()])
                pair.append({"set": s, "person": pn, "model": m, "n": len(d), "mean_d": d.mean(),
                             "boot90_lo": np.quantile(bs, .05), "boot90_hi": np.quantile(bs, .95), "wins": w,
                             "losses": l, "sign_p": binomtest(w, w + l).pvalue if w + l else np.nan})
    return pd.DataFrame(mae), pd.DataFrame(pair)


def show(M, D, persons=("adrian", "rise", "patrik", "a+r", "all")):
    with pd.option_context("display.width", 250, "display.max_rows", 500, "display.float_format", "{:.2f}".format):
        for s in SETS:
            t = M[(M.set == s)].pivot_table(index="model", columns="person", values="mae", sort=False)
            n = M[(M.set == s) & (M.model == M.model.iloc[0])].set_index("person").n
            t = t[[p for p in persons if p in t.columns]]
            t.columns = [f"{c} ({int(n[c])})" for c in t.columns]
            print(f"\n## MAE {s}\n{t.to_string()}")
        print(D[D.person.isin(["adrian", "rise", "a+r", "all"])].to_string(index=False))


if __name__ == "__main__":
    tag = sys.argv[1] if len(sys.argv) > 1 else "cands"
    if tag == "cands":
        models = cands.CANDS
    else:
        import variants
        models = {"addendum": variants.ADDENDUM, "addendum2": variants.ADDENDUM2}.get(tag, variants.MODELS)
    P = run(models)
    P.to_parquet(OUT / f"preds_{tag}.parquet", index=False)
    M, D = tables(P)
    M.round(3).to_csv(RES / f"mae_{tag}.csv", index=False); D.round(3).to_csv(RES / f"paired_{tag}.csv", index=False)
    show(M, D)

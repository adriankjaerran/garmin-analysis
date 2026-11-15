"""Extended evaluation: official races + extra near-maximal targets (data/_research/shape/g10/extra_targets.parquet).
Imports the shared harness (unchanged): same rolling-origin rule (history = rows with day < target day), same
predict_fn signature, same fill rule (naive_best_recent).

    import sys; sys.path.insert(0, "research/shape-models/approaches/g10-audit")
    from harness_ext import evaluate_ext, summary_ext, paired
    r = evaluate_ext(my_predict, name="mine")                  # official + high-confidence extras
    r = evaluate_ext(my_predict, include=("high", "medium"))   # sensitivity: + medium
    print(summary_ext(r)); print(paired(r, evaluate_ext(H.production_curve, name="production_curve")))

Extra targets are passed to predict_fn with tier = "race" (they stand in for races: the question is the runner's
all-out capacity at that distance); `workout`, surface etc. are those of the run. `tier_src` in the output says
"race" (official) or "extra". Adrian's verdicts go in data/_research/shape/g10/confirmations.json as
{"<target_id>": "confirm" | "reject"}: confirm includes a target whatever its confidence, reject drops it.
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
import common as C  # noqa: E402
import harness as H  # noqa: E402

EXTRA = C.OUT / "g10" / "extra_targets.parquet"
CONFIRM = C.OUT / "g10" / "confirmations.json"


def ext_targets(persons=("adrian", "rise", "patrik"), include=("high",)):
    R = H.targets(persons, ("race",)).assign(tier_src="race", confidence="official")
    X = pd.read_parquet(EXTRA)
    X = X[X.person.isin(persons)]
    dec = json.loads(CONFIRM.read_text()) if CONFIRM.exists() else {}
    d = X.target_id.map(dec).fillna(X.decision)
    X = X[(X.confidence.isin(include) & (d != "reject")) | (d == "confirm")].assign(tier_src="extra")
    return pd.concat([R, X], ignore_index=True).sort_values(["person", "day"]).reset_index(drop=True)


def evaluate_ext(predict_fn, persons=("adrian", "rise", "patrik"), include=("high",), name=None, fill_fn=None):
    name = name or getattr(predict_fn, "__name__", "model")
    fill_fn = H.naive_best_recent if fill_fn is None else fill_fn
    TT = ext_targets(persons, include)
    rows = []; t0 = time.time()
    for _, t in TT.iterrows():
        tgt = {k: t[k] for k in H.TARGET_KEYS if k in t.index}
        tgt["tier"] = "race"
        h = H.history(t["person"], t["day"])
        try:
            v, sd = H._to_vdot(predict_fn(t["person"], h, dict(tgt)), t["km"])
        except Exception as e:  # noqa: BLE001
            print(f"  {name}: {t['target_id']} failed: {e!r}")
            v, sd = None, None
        fv = v if v is not None else H._to_vdot(fill_fn(t["person"], h, dict(tgt)), t["km"])[0]
        rows.append({"target_id": t["target_id"], "person": t["person"], "date": t["date"], "tier_src": t["tier_src"],
                     "confidence": t["confidence"], "band": t["band"], "km": t["km"], "actual": t["vdot"], "pred": v,
                     "sd": sd, "pred_filled": fv})
    P = pd.DataFrame(rows)
    return {"name": name, "preds": P, "seconds": round(time.time() - t0, 1)}


def summary_ext(res, col="pred_filled"):
    """MAE / bias per person (official only, extras only, all) on `col` (filled by default so models with different
    coverage score the same targets)."""
    P = res["preds"]; out = []
    for p in ["adrian", "rise", "patrik", "adrian+rise"]:
        g0 = P[P.person.isin(["adrian", "rise"])] if p == "adrian+rise" else P[P.person == p]
        for sub, g in (("official", g0[g0.tier_src == "race"]), ("extra", g0[g0.tier_src == "extra"]), ("all", g0)):
            g = g[g[col].notna()]
            if not len(g):
                continue
            e = g[col] - g["actual"]
            out.append({"model": res["name"], "person": p, "set": sub, "n": len(g), "mae": e.abs().mean(),
                        "bias": e.mean()})
    return pd.DataFrame(out)


def paired(res, base, persons=("adrian", "rise", "adrian+rise"), col="pred_filled", n_boot=4000, seed=0):
    """Paired comparison of |error| vs a baseline on the same targets: mean d = |e_model| - |e_base| (negative =
    model better), its SE, a bootstrap 90 % interval, wins / losses (|d| > 0.05) and the exact two-sided sign-test p."""
    from scipy.stats import binomtest
    a = res["preds"].set_index("target_id"); b = base["preds"].set_index("target_id")
    j = a.join(b[[col]], rsuffix="_b", how="inner")
    j = j[j[col].notna() & j[col + "_b"].notna()]
    j["d"] = (j[col] - j.actual).abs() - (j[col + "_b"] - j.actual).abs()
    rng = np.random.default_rng(seed); out = []
    for p in persons:
        g = j[j.person.isin(["adrian", "rise"])] if p == "adrian+rise" else j[j.person == p]
        for sub, gg in (("official", g[g.tier_src == "race"]), ("all", g)):
            d = gg.d.to_numpy()
            if not len(d):
                continue
            bs = rng.choice(d, (n_boot, len(d))).mean(1)
            w, l = int((d < -0.05).sum()), int((d > 0.05).sum())
            out.append({"model": res["name"], "vs": base["name"], "person": p, "set": sub, "n": len(d),
                        "mean_d": d.mean(), "se": d.std(ddof=1) / math.sqrt(len(d)) if len(d) > 1 else np.nan,
                        "boot90_lo": np.quantile(bs, 0.05), "boot90_hi": np.quantile(bs, 0.95), "wins": w,
                        "losses": l, "sign_p": binomtest(w, w + l).pvalue if w + l else np.nan})
    return pd.DataFrame(out)

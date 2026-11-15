"""Shared, leakage-free evaluation harness for running-shape models.

    /tmp/shape-venv/bin/python research/shape-models/harness.py            # baselines, all persons
    /tmp/shape-venv/bin/python research/shape-models/harness.py --persons adrian,rise --tiers race

Protocol (rolling origin, time-forward): every target (data/_research/shape/targets.parquet) is predicted from the
data of days strictly before the target's day (history day < target day: a same-day warm-up is not visible).
`predict_fn(person, hist, target)` gets
  hist:   History with .acts / .segs / .targets (pandas views of rows with day < target day; .targets includes
          the past outcomes), .day (target day ordinal), .person, .key = (person, day) for caching
  target: dict of what is known before the effort: person, date, day, tier, km, band, surface, terrain,
          flat_course, climb_per_km, grade_abs_mean, workout, hr_device, trust_*, place, track_name, atype, type,
          activity_id, target_id. Never the time / VDOT / HR of the effort.
and returns a VDOT (float), or {"vdot": v, "sd": s} / {"time_s": t}, or None (abstain).

Metrics per tier x person x band: n targets, n predicted, MAE / bias / RMSE in VDOT, mean |% time error|,
Gaussian NLL when an sd is given; `filled` metrics replace abstentions with the naive baseline so models with
different coverage can be compared on the same targets. Headline: race MAE pooled over adrian + rise.

Usage from a model script:
    import sys; sys.path.insert(0, "research/shape-models")
    from harness import evaluate, report
    res = evaluate(my_predict, persons=("adrian", "rise"), name="my-model")
    report(res)                      # prints tables; res["summary"] is a DataFrame, res["preds"] per target
"""
from __future__ import annotations

import argparse
import functools
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

TIERS = ("race", "interval", "hard_effort")
SKIP_FIRST = {"race": 0, "interval": 3, "hard_effort": 0}   # first k targets per person/tier: too little history
TARGET_KEYS = ["person", "date", "day", "tier", "km", "band", "surface", "terrain", "flat_course", "climb_per_km",
               "grade_abs_mean", "workout", "hr_device", "trust_interval", "trust_steady", "wrist_temp_max", "place",
               "track_name", "indoor_bislett", "atype", "type", "activity_id", "target_id", "weight"]


@functools.lru_cache(maxsize=1)
def _data():
    A = pd.read_parquet(C.OUT / "activities.parquet").sort_values(["person", "day", "start_local"]).reset_index(drop=True)
    S = pd.read_parquet(C.OUT / "segments.parquet").sort_values(["person", "day"]).reset_index(drop=True)
    T = pd.read_parquet(C.OUT / "targets.parquet").sort_values(["person", "day"]).reset_index(drop=True)
    per = {}
    for p in C.PERSONS:
        a = A[A.person == p].reset_index(drop=True)
        s = S[S.person == p].reset_index(drop=True)
        t = T[T.person == p].reset_index(drop=True)
        per[p] = (a, a["day"].to_numpy(), s, s["day"].to_numpy(), t, t["day"].to_numpy())
    return A, S, T, per


def load_data():
    """(activities, segments, targets) DataFrames (all persons)."""
    A, S, T, _ = _data()
    return A, S, T


@dataclass
class History:
    person: str
    day: int
    acts: pd.DataFrame
    segs: pd.DataFrame
    targets: pd.DataFrame

    @property
    def key(self):
        return (self.person, self.day)


def history(person, day):
    """Everything strictly before `day` (ordinal) for `person`."""
    a, ad, s, sd, t, td = _data()[3][person]
    return History(person, int(day), a.iloc[: np.searchsorted(ad, day, "left")],
                   s.iloc[: np.searchsorted(sd, day, "left")], t.iloc[: np.searchsorted(td, day, "left")])


def targets(persons=C.PERSONS, tiers=TIERS, skip_first=None):
    """Targets to evaluate, with the outcome columns (time_s, vdot) for scoring only."""
    T = _data()[2]
    sf = SKIP_FIRST if skip_first is None else skip_first
    out = []
    for p in persons:
        for tier in tiers:
            x = T[(T.person == p) & (T.tier == tier)].sort_values("day")
            out.append(x.iloc[sf.get(tier, 0):])
    return pd.concat(out, ignore_index=True) if out else T.iloc[:0]


def _to_vdot(r, km):
    if r is None:
        return None, None
    if isinstance(r, dict):
        sd = r.get("sd")
        if r.get("vdot") is not None:
            return float(r["vdot"]), sd
        if r.get("time_s") is not None:
            return float(C.vdot(km * 1000, r["time_s"])), sd
        return None, None
    v = float(r)
    return (v, None) if math.isfinite(v) else (None, None)


def evaluate(predict_fn, persons=("adrian", "rise", "patrik"), tiers=TIERS, name=None, fill_fn=None, skip_first=None,
             verbose=False):
    """Run `predict_fn` on every target (rolling origin). Returns {"name", "preds" (DataFrame), "summary"
    (DataFrame), "headline" (dict), "seconds"}."""
    name = name or getattr(predict_fn, "__name__", "model")
    fill_fn = naive_best_recent if fill_fn is None else fill_fn
    TT = targets(persons, tiers, skip_first)
    rows = []
    t0 = time.time()
    for _, t in TT.iterrows():
        tgt = {k: t[k] for k in TARGET_KEYS if k in t.index}
        h = history(t["person"], t["day"])
        try:
            v, sd = _to_vdot(predict_fn(t["person"], h, dict(tgt)), t["km"])
        except Exception as e:  # noqa: BLE001
            if verbose:
                print(f"  {name}: {t['target_id']} failed: {e!r}")
            v, sd = None, None
        fv, _ = _to_vdot(fill_fn(t["person"], h, dict(tgt)), t["km"]) if v is None else (v, sd)
        rows.append({"target_id": t["target_id"], "person": t["person"], "date": t["date"], "tier": t["tier"],
                     "band": t["band"], "km": t["km"], "weight": t["weight"], "actual": t["vdot"], "pred": v,
                     "sd": sd, "pred_filled": fv, "note": t.get("note")})
    P = pd.DataFrame(rows)
    secs = time.time() - t0
    S = summarize(P)
    return {"name": name, "preds": P, "summary": S, "headline": headline(P), "seconds": round(secs, 1)}


def _metrics(g):
    ok = g["pred"].notna()
    e = (g.loc[ok, "pred"] - g.loc[ok, "actual"]).astype(float)
    pt = C.pct_time_error(g.loc[ok, "pred"].astype(float).to_numpy(), g.loc[ok, "actual"].astype(float).to_numpy(),
                          g.loc[ok, "km"].astype(float).to_numpy() * 1000) if ok.any() else np.array([])
    fl = g["pred_filled"].notna()
    ef = (g.loc[fl, "pred_filled"] - g.loc[fl, "actual"]).astype(float)
    out = {"n": len(g), "n_pred": int(ok.sum()),
           "mae": float(e.abs().mean()) if ok.any() else np.nan,
           "bias": float(e.mean()) if ok.any() else np.nan,
           "rmse": float(np.sqrt((e ** 2).mean())) if ok.any() else np.nan,
           "mape_time": float(np.abs(pt).mean()) if len(pt) else np.nan,
           "mae_filled": float(ef.abs().mean()) if fl.any() else np.nan}
    sd_ok = ok & g["sd"].notna()
    if sd_ok.any():
        s = np.maximum(g.loc[sd_ok, "sd"].astype(float), 0.3)
        z = (g.loc[sd_ok, "pred"] - g.loc[sd_ok, "actual"]).astype(float) / s
        out["nll"] = float(np.mean(0.5 * z ** 2 + np.log(s) + 0.5 * math.log(2 * math.pi)))
        out["cover_1sd"] = float((z.abs() <= 1).mean())
    return pd.Series(out)


def summarize(P):
    parts = []
    for keys in (["tier"], ["tier", "person"], ["tier", "person", "band"]):
        m = P.groupby(keys, dropna=False).apply(_metrics, include_groups=False).reset_index()
        for k in ("tier", "person", "band"):
            if k not in m:
                m[k] = "all"
        parts.append(m)
    return pd.concat(parts, ignore_index=True)[["tier", "person", "band", "n", "n_pred", "mae", "bias", "rmse",
                                                 "mape_time", "mae_filled"] +
                                                [c for c in ("nll", "cover_1sd") if c in parts[0]]]


def headline(P):
    def mae(mask, col="pred"):
        x = P[mask & P[col].notna()]
        return round(float((x[col] - x["actual"]).abs().mean()), 3) if len(x) else None
    r = P.tier == "race"; ar = P.person.isin(["adrian", "rise"])
    return {"race_mae_adrian_rise": mae(r & ar), "race_mae_adrian": mae(r & (P.person == "adrian")),
            "race_mae_rise": mae(r & (P.person == "rise")), "race_mae_all": mae(r),
            "race_mae_all_filled": mae(r, "pred_filled"),
            "interval_mae": mae(P.tier == "interval"), "hard_effort_mae": mae(P.tier == "hard_effort"),
            "n_race": int(r.sum()), "n_race_pred": int((r & P.pred.notna()).sum())}


def report(res, detail=True):
    print(f"\n=== {res['name']}  ({res['seconds']} s)")
    print("headline:", res["headline"])
    S = res["summary"]
    show = S if detail else S[S.band == "all"]
    with pd.option_context("display.width", 200, "display.max_rows", 200, "display.float_format", "{:.2f}".format):
        print(show.to_string(index=False))


def compare(results, tier="race", persons=("adrian", "rise")):
    """Side-by-side MAE table of several evaluate() results."""
    rows = []
    for r in results:
        S = r["summary"]
        row = {"model": r["name"]}
        for p in list(persons) + ["all"]:
            x = S[(S.tier == tier) & (S.person == p) & (S.band == "all")]
            if len(x):
                row[f"{p} mae"] = x.iloc[0]["mae"]; row[f"{p} n"] = int(x.iloc[0]["n_pred"])
        rows.append(row)
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ baselines
def naive_best_recent(person, hist, target, days=60, fallback_days=365):
    """Max effort VDOT (prior targets of any tier: races, interval scores, hard efforts) in the last `days`;
    else the median of the last `fallback_days`; else None. Distance-independent."""
    t = hist.targets
    if t.empty:
        return None
    rec = t[t.day >= hist.day - days]
    if len(rec):
        return float(rec["vdot"].max())
    rec = t[t.day >= hist.day - fallback_days]
    return float(rec["vdot"].median()) if len(rec) else float(t["vdot"].iloc[-1])


def naive_last_race(person, hist, target):
    """VDOT of the last official race (any distance); None before the first."""
    t = hist.targets
    t = t[t.tier == "race"]
    return float(t["vdot"].iloc[-1]) if len(t) else None


@functools.lru_cache(maxsize=8)
def prod_items(person):
    """All production evidence items of a person (fitness.json interval / long items with kind hard / race / bound),
    one row per (activity_id, discipline), sorted by day and start time (data/_research/shape/prod_items.parquet).
    Since the 2026-11-05 rebuild (bug A1) a session can contribute both an interval score and a long bound."""
    I = pd.read_parquet(C.OUT / "prod_items.parquet")
    I = I[(I.person == person) & I.discipline.isin(["interval", "long"]) & I.kind.isin(["hard", "race", "bound"]) &
          I.km.notna() & I.score.notna()]
    return I.sort_values(["day", "start_local", "discipline"]).reset_index(drop=True)


@functools.lru_cache(maxsize=4096)
def _prod_state(person, day):
    """Production distance-curve state (scripts/fitness_model.smooth_curve, CURVE as in production) fitted on the
    production per-session evidence (activities.prod_*) of days < day; read on day - 1."""
    fm = C.import_production()
    a = prod_items(person)
    a = a[a.day < day]
    if a.empty:
        return None
    obs = [(int(r.day), float(r.score), float(r.sigma), r.kind, fm.curve_x(float(r.km))) for r in a.itertuples()]
    d0 = min(o[0] for o in obs)
    days, M, Cv, w, _ = fm.smooth_curve(obs, d0, day - 1)
    obs_s = sorted(obs, key=lambda o: o[0])
    ev = [2 ** o[4] * fm.CURVE["ref_km"] for o, wt in zip(obs_s, w) if o[3] in ("race", "hard") and wt > 0.5]
    ekm = (min(ev), max(ev)) if ev else None
    return M[-1], Cv[-1], ekm


def production_curve(person, hist, target, flat=False):
    """Reproduction of the production model (model_version 3): coupled distance curve, prediction at the target
    distance with the production extrapolation rules. Uses production per-session scores (offsets fitted in-sample
    on races: slight optimism)."""
    st = _prod_state(person, hist.day)
    if st is None:
        return None
    fm = C.import_production()
    m, cv, ekm = st
    p = fm.curve_predict(m[0], m[1], cv[0, 0], cv[1, 1], cv[0, 1], float(target["km"]), ekm, flat=flat)
    return {"vdot": float(p["vdot"]), "sd": float(p["sd"])}


def production_flat(person, hist, target):
    """Production curve read at 5 km (theta) for every distance (the removed 'curve off')."""
    return production_curve(person, hist, target, flat=True)


BASELINES = {"naive_best_recent_60d": naive_best_recent, "naive_last_race": naive_last_race,
             "production_curve": production_curve, "production_flat": production_flat}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--persons", default=",".join(C.PERSONS))
    ap.add_argument("--tiers", default=",".join(TIERS))
    ap.add_argument("--models", default=",".join(BASELINES))
    ap.add_argument("--save", default=None, help="write the per-target predictions of all models to this parquet")
    args = ap.parse_args()
    persons = tuple(args.persons.split(",")); tiers = tuple(args.tiers.split(","))
    res = []
    for m in args.models.split(","):
        r = evaluate(BASELINES[m], persons=persons, tiers=tiers, name=m)
        report(r, detail=False)
        res.append(r)
    for tier in tiers:
        print(f"\n--- {tier} MAE (VDOT) by model")
        print(compare(res, tier, persons).to_string(index=False, float_format="{:.2f}".format))
    if args.save:
        pd.concat([r["preds"].assign(model=r["name"]) for r in res]).to_parquet(args.save, index=False)


if __name__ == "__main__":
    main()

"""production_curve with the evidence read straight from fitness.json (all interval/long items), to measure the
prep_data dict-overwrite bug: `prod = {id: s ...}` keeps one item per activity id, so the 32 sessions that carry both an
interval (hard) score and a long (bound) item lose their interval score in activities.parquet (and in the interval tier).

    from prod_fixed import production_curve_all, production_curve_kept
`production_curve_kept` uses the same items as activities.parquet (must reproduce harness.production_curve exactly);
`production_curve_all` adds the lost interval items. History rule as the harness: items with day < target day.
"""
from __future__ import annotations

import datetime as dt
import functools
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
import common as C  # noqa: E402
import harness as H  # noqa: E402


@functools.lru_cache(maxsize=None)
def _items(person, which):
    A = H.load_data()[0]
    kept = {(int(r.activity_id), r.prod_discipline) for r in A[A.person == person].itertuples() if r.prod_discipline}
    out = []
    for s in json.loads((C.DATA / person / "fitness.json").read_text())["sessions"]:
        if s.get("discipline") not in ("interval", "long") or s.get("kind") not in ("hard", "race", "bound"):
            continue
        if s.get("km") is None or s.get("score") is None:
            continue
        if which == "kept" and (int(s["id"]), s["discipline"]) not in kept:
            continue
        out.append((dt.date.fromisoformat(s["date"][:10]).toordinal(), float(s["score"]), float(s["sigma"]), s["kind"],
                    float(s["km"])))
    return sorted(out)


@functools.lru_cache(maxsize=4096)
def _state(person, day, which):
    fm = C.import_production()
    obs = [(d, sc, sg, k, fm.curve_x(km)) for d, sc, sg, k, km in _items(person, which) if d < day]
    if not obs:
        return None
    d0 = min(o[0] for o in obs)
    days, M, Cv, w, _ = fm.smooth_curve(obs, d0, day - 1)
    obs_s = sorted(obs, key=lambda o: o[0])
    ev = [2 ** o[4] * fm.CURVE["ref_km"] for o, wt in zip(obs_s, w) if o[3] in ("race", "hard") and wt > 0.5]
    return M[-1], Cv[-1], ((min(ev), max(ev)) if ev else None)


def _pred(person, hist, target, which):
    st = _state(person, hist.day, which)
    if st is None:
        return None
    fm = C.import_production()
    m, cv, ekm = st
    p = fm.curve_predict(m[0], m[1], cv[0, 0], cv[1, 1], cv[0, 1], float(target["km"]), ekm)
    return {"vdot": float(p["vdot"]), "sd": float(p["sd"])}


def production_curve_kept(person, hist, target):
    return _pred(person, hist, target, "kept")


def production_curve_all(person, hist, target):
    return _pred(person, hist, target, "all")

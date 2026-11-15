"""G2: observation table for the hierarchical effects model (one row per effort observation, all persons).

Every row's y and covariates depend only on its own session (no outcome of other days), so the table can be built
once from the full dataset and used by a causal (time-forward) filter: a prediction for day d only reads rows with
day < d.

Kinds
  int    interval session: production W'bal score (prod_score; GAP minetti-half on road), x = 0 (5 km)
  race   official race: Daniels VDOT of official km / time (raw, not grade-adjusted), x = log2(km / 5)
  eff    hard continuous effort (= the harness hard_effort rows): best GAP effort >= 3 km in a tempo / time trial
         session or a high-HR (>= 0.88 HRR last third, trusted device) non-interval session
  sub    (optional) best GAP 5 km of a steady / long run: sub-maximal by design, gets its own intent offset

Columns: person, day, activity_id, kind, intent, surface, y (VDOT on the minetti-half GAP scale, races converted with
their own gap_ratio), y_raw, g_min / g_str (VDOT added by minetti-half / Strava GAP vs raw speed), km, x, temp
(wrist_temp_max), climb_per_km, target_id (harness target id when the row is a harness target).
"""
from __future__ import annotations

import functools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
import common as C  # noqa: E402
from harness import load_data  # noqa: E402


def _vs(v, t):
    return C.vdot_from_speed(np.asarray(v, float), np.asarray(t, float))


@functools.lru_cache(maxsize=2)
def build(include_sub=False):
    A, S, T = load_data()
    A = A.set_index("activity_id", drop=False)
    rows = []
    # ---- intervals: production score; GAP shift = mean over the session's reps
    reps = S[(S.seg_kind == "rep") & S.speed.notna() & (S.dur_s > 20)].copy()
    reps["g_min"] = _vs(reps.gap_speed, reps.dur_s) - _vs(reps.speed, reps.dur_s)
    reps["g_str"] = _vs(reps.gap_speed_strava, reps.dur_s) - _vs(reps.speed, reps.dur_s)
    rg = reps.groupby("activity_id")[["g_min", "g_str", "grade_abs_mean"]].mean()
    for t in T.itertuples():
        a = A.loc[t.activity_id]
        base = dict(person=t.person, day=int(t.day), date=t.date, activity_id=t.activity_id, surface=t.surface,
                    km=float(t.km), temp=t.wrist_temp_max, climb_per_km=t.climb_per_km, target_id=t.target_id,
                    tier=t.tier, flat=bool(t.flat_course) or t.surface in ("track", "indoor_track", "treadmill"))
        if t.tier == "interval":
            g = rg.loc[t.activity_id] if t.activity_id in rg.index else None
            gm = 0.0 if base["flat"] or g is None else float(g.g_min)
            gs = 0.0 if base["flat"] or g is None else float(g.g_str)
            rows.append({**base, "kind": "int", "intent": "intervals", "y": float(t.vdot), "y_raw": float(t.vdot) - gm,
                         "g_min": gm, "g_str": gs})
        elif t.tier == "race":
            v = t.km * 1000 / t.time_s
            gr = a.gap_ratio if a.gap_ratio == a.gap_ratio and not base["flat"] else 1.0
            grs = a.gap_ratio_strava if a.gap_ratio_strava == a.gap_ratio_strava and not base["flat"] else 1.0
            gm = float(_vs(v * gr, t.time_s) - t.vdot); gs = float(_vs(v * grs, t.time_s) - t.vdot)
            rows.append({**base, "kind": "race", "intent": "race", "y": float(t.vdot) + gm, "y_raw": float(t.vdot),
                         "g_min": gm, "g_str": gs})
        else:
            lab = t.target_id.split(":")[1]
            s = S[(S.activity_id == t.activity_id) & (S.seg_kind == "best_gap") & (S.label == lab)].iloc[0]
            raw = float(_vs(s.speed, s.dur_s)); gm = float(_vs(s.gap_speed, s.dur_s)) - raw
            gs = float(_vs(s.gap_speed_strava, s.dur_s)) - raw
            intent = t.workout if t.workout in ("tempo", "time_trial") else "hr_hard"
            rows.append({**base, "kind": "eff", "intent": intent, "y": float(t.vdot), "y_raw": float(t.vdot) - gm,
                         "g_min": gm, "g_str": gs})
    if include_sub:
        used = {r["activity_id"] for r in rows}
        b = S[(S.seg_kind == "best_gap") & (S.target_m == 5000) & S.pace_ok & ~S.suspect &
              S.workout.isin(["steady", "long"]) & ~S.activity_id.isin(used)]
        for s in b.itertuples():
            a = A.loc[s.activity_id]
            flat = bool(a.flat_course) or a.surface in ("track", "indoor_track")
            raw = float(_vs(s.speed, s.dur_s)); gm = float(_vs(s.gap_speed, s.dur_s)) - raw
            gs = float(_vs(s.gap_speed_strava, s.dur_s)) - raw
            rows.append(dict(person=s.person, day=int(s.day), date=s.date, activity_id=s.activity_id,
                             surface=a.surface, km=5.0, temp=a.wrist_temp_max,
                             climb_per_km=(s.climb_m or 0) / (s.dist_m / 1000), target_id=None, tier=None, flat=flat,
                             kind="sub", intent=s.workout, y=float(s.vdot), y_raw=float(s.vdot) - gm, g_min=gm,
                             g_str=gs))
    O = pd.DataFrame(rows)
    O["x"] = np.log2(O.km / 5.0)
    return O.sort_values(["day", "person"]).reset_index(drop=True)


if __name__ == "__main__":
    O = build(include_sub=True)
    pd.set_option("display.width", 200)
    print(O.groupby(["person", "kind", "intent", "surface"]).agg(n=("y", "size"), y=("y", "mean"),
                                                                 g=("g_min", "mean"), gs=("g_str", "mean")))
    print(O[["g_min", "g_str", "climb_per_km", "temp"]].describe())

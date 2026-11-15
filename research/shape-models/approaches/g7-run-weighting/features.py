"""G7: candidate evidence per run + features for "how maximal / informative was this run".

One candidate per usable run (`build_candidates`):
  src = race  : official race VDOT at the official km (always maximal; not classified)
        int   : production W'-balance interval score (prod_discipline interval, kind hard), km 5 (production scale)
        cont  : best continuous grade-adjusted effort >= 3 km in one pause-free leg (best_gap segment, max VDOT over
                3k/5k/10k/HM, not suspect), km = its distance. No offset (tempo +3 is NOT applied: the model learns it).
Features are all known from the run itself plus `prev_est` = production curve on the day before the run (data < run
day only, harness._prod_state), so they are leak-free for any later target.

    /tmp/shape-venv/bin/python research/shape-models/approaches/g7-run-weighting/features.py   # -> g7/candidates.parquet
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
import common as C  # noqa: E402
import harness as H  # noqa: E402

G7 = C.OUT / "g7"
G7.mkdir(parents=True, exist_ok=True)
CAND = G7 / "candidates.parquet"

WORKOUTS = ["easy", "steady", "long", "tempo", "intervals", "time_trial", "other", "unknown_nogps"]
SURFACES = ["road", "track", "indoor_track", "treadmill", "trail"]


def prev_estimate(person, day, km):
    st = H._prod_state(person, int(day))
    if st is None:
        return np.nan, np.nan
    m, cv, _ = st
    x = math.log2(km / 5.0)
    return float(m[0] + x * m[1]), float(math.sqrt(max(cv[0, 0] + 2 * x * cv[0, 1] + x * x * cv[1, 1], 0)))


def build_candidates():
    A, S, _ = H.load_data()
    A = A[A.usable].copy()
    b = S[(S.seg_kind == "best_gap") & (S.target_m >= 3000) & ~S.suspect & S.pace_ok]
    best = b.loc[b.groupby(["person", "activity_id"]).vdot.idxmax()].set_index(["person", "activity_id"])
    rows = []
    for r in A.itertuples():
        base = dict(person=r.person, activity_id=r.activity_id, date=r.date, day=int(r.day), workout=r.workout)
        if r.is_official_race and pd.notna(r.race_vdot):
            y, km, src = float(r.race_vdot), float(r.race_km), "race"
            hrr_eff, dur = r.prod_hrr, float(r.official_s) if pd.notna(r.official_s) else np.nan
        elif r.prod_discipline == "interval" and r.prod_kind == "hard" and pd.notna(r.prod_score):
            y, km, src = float(r.prod_score), 5.0, "int"
            hrr_eff, dur = r.prod_work_hrr, float(r.rep_total_m or np.nan) / max(r.rep_speed_mean or np.nan, 1e-6) \
                if pd.notna(r.rep_total_m) and pd.notna(r.rep_speed_mean) else np.nan
        elif (r.person, r.activity_id) in best.index and r.pace_ok:
            s = best.loc[(r.person, r.activity_id)]
            y, km, src = float(s.vdot), float(s.target_m) / 1000, "cont"
            hrr_eff, dur = s.hrr_last3, float(s.time_s)
        else:
            continue
        trust = r.trust_interval if src == "int" else r.trust_steady
        pe, psd = prev_estimate(r.person, r.day, km)
        rows.append({**base, "src": src, "y": y, "km": km, "x": math.log2(km / 5.0), "dur_s": dur,
                     "hrr_eff": hrr_eff, "trust": trust, "hr_ok": bool(r.hr_ok),
                     "hrr_mean": r.hrr_mean, "hr_p95_rel": (r.hr_p95 / r.hr_max_person) if r.hr_max_person else np.nan,
                     "decoupling": r.decoupling, "speed_cv": r.speed_cv, "te_aer": r.garmin_te_aer,
                     "te_ana": r.garmin_te_ana, "n_reps": r.n_reps, "work_rest": r.work_rest,
                     "rest_med": r.rest_med, "dist_km": r.dist_km, "surface": r.surface, "terrain": r.terrain,
                     "climb_per_km": (r.climb_m or 0) / max(r.dist_km, 0.1), "grade_abs_mean": r.grade_abs_mean,
                     "glitch_s": r.glitch_s, "whole_gap": r.vdot_whole_gap, "prev_est": pe, "prev_sd": psd,
                     "days_since_hard": r.days_since_hard, "tsb": r.tsb, "hr_device": r.hr_device,
                     "prod_kind": r.prod_kind, "prod_score": r.prod_score, "prod_km": r.prod_km,
                     "prod_sigma": r.prod_sigma, "prod_discipline": r.prod_discipline})
    D = pd.DataFrame(rows).sort_values(["person", "day"]).reset_index(drop=True)
    D.to_parquet(CAND, index=False)
    return D


def feature_matrix(D, with_delta=True):
    """Numeric design matrix (NaN allowed for lightgbm; logistic uses the imputed version)."""
    X = pd.DataFrame(index=D.index)
    X["is_int"] = (D.src == "int").astype(float)
    for w in WORKOUTS:
        X[f"w_{w}"] = (D.workout == w).astype(float)
    for s in SURFACES:
        X[f"s_{s}"] = (D.surface == s).astype(float)
    X["x"] = D.x
    X["log_dur"] = np.log(D.dur_s.clip(lower=60))
    hrr = D.hrr_eff.where(D.hr_ok)
    X["hrr_eff"] = hrr
    X["hrr_eff_trusted"] = ((hrr - 0.85) * D.trust).fillna(0.0)     # HR evidence scaled by device trust
    X["trust"] = D.trust
    X["hrr_mean"] = D.hrr_mean.where(D.hr_ok)
    X["hr_p95_rel"] = D.hr_p95_rel.where(D.hr_ok)
    X["decoupling"] = D.decoupling.clip(-0.5, 0.5)
    X["speed_cv"] = D.speed_cv
    X["te_aer"] = D.te_aer
    X["te_ana"] = D.te_ana
    X["n_reps"] = D.n_reps.clip(upper=20)
    X["work_rest"] = D.work_rest
    X["log_dist"] = np.log(D.dist_km.clip(lower=1))
    X["climb_per_km"] = D.climb_per_km.clip(upper=60)
    X["glitch"] = (D.glitch_s > 0).astype(float)
    X["whole_minus_y"] = D.whole_gap - D.y              # how much slower the whole run was than its best effort
    X["days_since_hard"] = D.days_since_hard.clip(upper=60)
    if with_delta:
        X["delta"] = (D.y - D.prev_est).clip(-25, 15)   # effort vs the current estimate (data before the run)
        X["delta_z"] = (X["delta"] / np.sqrt(D.prev_sd ** 2 + 4)).clip(-8, 6)
    return X


if __name__ == "__main__":
    D = build_candidates()
    print(D.groupby(["person", "src"]).size())
    print(D[["y", "prev_est", "hrr_eff"]].describe())

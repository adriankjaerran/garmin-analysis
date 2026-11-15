"""G3 features: steady km splits with HR -> per-split / per-run submaximal efficiency (Swain %HRR ~ %VO2R).

    /tmp/shape-venv/bin/python research/shape-models/approaches/g3-hr-efficiency/features.py   # writes g3/*.parquet

Per km split (segments seg_kind == "km") of easy / steady / long / other runs, plus the warm-up splits of interval /
tempo sessions that end before the first rep starts:
  vo2     = Daniels O2 cost of the split's GAP speed (ml/kg/min)
  hrr     = split mean %HRR (person max HR, 30-day median resting HR)
  est     = 3.5 + (vo2 - 3.5) / hrr      (Swain: %HRR ~ %VO2 reserve -> VO2max, on the VDOT scale)
Filters: run usable + pace_ok + hr_ok, split not suspect, >= 97 % moving, >= 90 % HR cover, starts >= 10 min in,
|grade| mean <= 8 %, 0.45 <= hrr <= 0.95, GAP speed 2-5.5 m/s, no treadmill / indoor (distance from laps / footpod).
Everything here is per split / per run (no cross-run fitting), so it is leakage-free; the models fit drift / device /
heat / calibration on history only.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
import common as C  # noqa: E402

OUTDIR = C.OUT / "g3"
EASY = ("easy", "steady", "long", "other")
WARMUP = ("intervals", "tempo")
RUN_COLS = ["activity_id", "hr_device", "hr_source", "trust_steady", "trust_interval", "hr_flags", "wrist_temp_max",
            "wrist_temp_min", "hr_rest_30d", "hr_max_person", "dist_km", "moving_s", "terrain", "decoupling",
            "ef_gap_per_hr", "start_local"]


def build_splits(min_start_s=600, min_moving=0.97, min_cover=0.9, max_grade=0.08, hrr_lo=0.45, hrr_hi=0.95):
    A = pd.read_parquet(C.OUT / "activities.parquet")
    S = pd.read_parquet(C.OUT / "segments.parquet")
    a_ok = A[A.usable & A.pace_ok & A.hr_ok & ~A.surface.isin(["treadmill", "indoor_track"])]
    k = S[(S.seg_kind == "km") & S.activity_id.isin(a_ok.activity_id) & ~S.suspect]
    # warm-up splits of interval / tempo sessions: end before the first rep starts
    reps = S[S.seg_kind == "rep"].groupby("activity_id").start_s.min().rename("first_rep_s")
    k = k.merge(reps, left_on="activity_id", right_index=True, how="left")
    k["end_s"] = k.start_s + k.dur_s
    is_easy = k.workout.isin(EASY)
    is_wu = k.workout.isin(WARMUP) & k.first_rep_s.notna() & (k.end_s <= k.first_rep_s - 30)
    k = k[is_easy | is_wu].copy()
    k["src"] = np.where(is_easy[k.index], "easy", "warmup")
    k = k[(k.start_s >= min_start_s) & (k.moving_share >= min_moving) & (k.hr_cover >= min_cover) &
          (k.grade_abs_mean <= max_grade) & k.hrr_mean.between(hrr_lo, hrr_hi) & k.gap_speed.between(2.0, 5.5)]
    k = k.merge(a_ok[RUN_COLS], on="activity_id", how="left")
    k["hrr"] = k.hrr_mean
    k["mid_min"] = (k.start_s + k.dur_s / 2) / 60.0
    k["vo2"] = C.daniels_vo2(k.gap_speed)
    k["vo2_strava"] = C.daniels_vo2(k.gap_speed_strava)
    k["vo2_raw"] = C.daniels_vo2(k.speed)
    k["est"] = 3.5 + (k.vo2 - 3.5) / k.hrr
    k["month"] = pd.to_datetime(k.date).dt.month
    keep = ["person", "activity_id", "date", "day", "src", "workout", "surface", "label", "start_s", "dur_s",
            "mid_min", "speed", "gap_speed", "gap_speed_strava", "grade_mean", "grade_abs_mean", "climb_m",
            "desc_m", "hr_mean", "hrr", "vo2", "vo2_strava", "vo2_raw", "est", "month"] + RUN_COLS[1:]
    return k[keep].sort_values(["person", "day", "activity_id", "start_s"]).reset_index(drop=True)


def main():
    OUTDIR.mkdir(parents=True, exist_ok=True)
    k = build_splits()
    k.to_parquet(OUTDIR / "splits.parquet", index=False)
    print(k.groupby(["person", "hr_device", "src"]).agg(splits=("est", "size"), runs=("activity_id", "nunique"),
                                                         est_med=("est", "median"), hrr_med=("hrr", "median")))


if __name__ == "__main__":
    main()

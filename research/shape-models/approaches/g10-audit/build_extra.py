"""G10: extra near-maximal ground-truth targets (not in races.json) for adrian / rise / patrik.

    /tmp/shape-venv/bin/python research/shape-models/approaches/g10-audit/build_extra.py
Writes data/_research/shape/g10/extra_targets.parquet (+ extra_targets.csv). Every candidate was picked by hand from
the screens in REPORT.md (activity names: all Garmin defaults, no race/test words; Garmin eventType: all
'uncategorized'; Garmin PR flags: current-PR holders only, none near-maximal; groups.json race? / tempo labels;
standalone efforts (best effort >= 60 % of the run) and high last-third HR on any device; a two-sided reference (the
published fitness.json smoothed curve, which sees the future) used only to describe each candidate, never as a target).

Value of a target: the run's best effort at a standard distance (raw distance on a track, GAP on roads) minus the GPS
over-read measured on the official races for the same person and surface (median of best-effort VDOT - official
VDOT): adrian road GAP 1.06 / track raw 0.85, rise road GAP 1.73 / track raw 3.24 (one race, fenix 3 at Bislett),
patrik road GAP 1.16 (pooled; his own race time is approximate). Watch track mode (atype track_running, 4315): 0.
`vdot_gps` keeps the uncorrected value. `confidence`: high = clear intent (standalone fixed-distance effort / labelled
tempo-test) AND clear effort evidence (HR >= 93 % of max at the end, even or negative split) AND a trustworthy
distance; medium = one of those is weak. `decision` = pending until Adrian confirms (confirmations.json, see
harness_ext.py). Rejected candidates are listed in REPORT.md, not stored.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
import common as C  # noqa: E402

OUT = C.OUT / "g10"
GPS = {("adrian", "road"): ("best_gap", 1.06), ("adrian", "track"): ("best", 0.85), ("adrian", "trackmode"): ("best", 0.0),
       ("rise", "road"): ("best_gap", 1.73), ("rise", "track"): ("best", 3.24), ("patrik", "road"): ("best_gap", 1.16)}

# (person, activity_id, label, surface key, confidence, rationale)
CANDS = [
    ("adrian", 10959008247, "10k", "road", "high",
     "2023-04-22 'tempo' 10.02 km continuous (no pause), max HR 204 = 99 % of 207, last-third HRR 0.93, best 10k covers "
     "98 % of the run; 15 days before Lier 10k 2023 (53.29) and at the same level: a 10k time trial."),
    ("adrian", 20994407266, "3k", "track", "high",
     "2025-11-15 Bislett, standalone 3.07 km run in 9:54 (labelled easy by the type rules), splits 3:18/3:14/3:11 "
     "(negative), HR 135 -> 193 in the last km, max 199 = 96 %: a 3000 m time trial 13 days after Lier 10k 2025."),
    ("adrian", 22363927895, "3k", "trackmode", "high",
     "2026-03-31 Bislett, watch track mode, exactly 3.00 km in 10:27, splits 3:26/3:34/3:26, HR to 190 (max 194 = 94 %) "
     "on the trusted 4315 (steady trust 1.0): a 3000 m test; rise ran a 3k test at Bislett the same evening."),
    ("adrian", 20781880143, "5k", "track", "medium",
     "2025-10-24 Bislett 5k in 18:01 (5.10 km GPS), even pace, max HR 192-193 = 93 %; the user said 'not a race' and it was "
     "followed 27 min later by a second 5k in 19:01, so possibly held back. 3 days before Bislett 10k (57.09)."),
    ("adrian", 22324591263, "1mi", "trackmode", "medium",
     "2026-03-28 Bislett track mode, 2.45 km run, first 2 km in 3:24/3:21 with HR 192-196 (95 % max, 4315): a 1 mi / 2k "
     "test; distance short of the curve's usual range and the run's shape (2 km hard + jog) is not certain."),
    ("adrian", 20142870530, "5k", "track", "medium",
     "2025-08-22 Bislett 'tempo' 8.11 km (with rise), best 5k 18:08, last-third HRR 0.87, max 193: hard continuous but a "
     "tempo inside a longer run, not a declared all-out."),
    ("adrian", 20309992118, "HM", "track", "medium",
     "2025-09-07 Bislett 'tempo' 21.86 km, best HM 1:21:17, max HR 188 (91 %), HRR 0.80: a half-marathon effort one week "
     "before Copenhagen Half (58.78); likely controlled (race rehearsal), so probably 1-2 VDOT sub-maximal."),
    ("adrian", 4151759347, "5k", "road", "medium",
     "2019-10-13 'tempo' 6.01 km, best 5k 20:16 (GAP), max HR 203 = 98 %, HRR 0.91: hard, but no race nearby to check it "
     "and the FR935 wrist HR is only trust 0.5."),
    ("rise", 22363939116, "3k", "track", "medium",
     "2026-03-31 Bislett, 3k at the end of a 7.1 km session in 11:22 GPS (km 3:30/3:54/3:58, fading), HR to 190-191 = her "
     "max: all-out effort, but the fenix 3 reads 5 % long at Bislett (10k 2026-10-23), so the value is uncertain (+-1.5)."),
    ("rise", 17216650765, "10k", "track", "medium",
     "2024-10-06 Bislett 'tempo' 10.37 km, best 10k 43:18 GPS, strap HR, last-third HRR 0.88; 13 days before Bilbao Half. "
     "GPS glitch seconds inside the effort (segment `suspect`) and fenix 3 track over-read: value uncertain."),
    ("rise", 20142876938, "5k", "track", "medium",
     "2025-08-22 Bislett 'tempo' with adrian, best 5k 20:52 GPS, strap HR, last-third HRR 0.90, max 182 (96 %): hard, "
     "but a tempo inside an 8 km session with a pause."),
    ("patrik", 19082390679, "5k", "road", "medium",
     "2024-04-21 'tempo' 5.00 km, max HR 203 = 98 %, HRR 0.91, best GAP 5k 21:18 covers the whole run; hilly road and one "
     "pause (the raw 5k has no pause-free leg, the value is GAP distance)."),
]


def main():
    A = pd.read_parquet(C.OUT / "activities.parquet"); S = pd.read_parquet(C.OUT / "segments.parquet")
    rows = []
    for person, aid, lab, sk, conf, why in CANDS:
        a = A[A.activity_id == aid].iloc[0]
        kind, corr = GPS[(person, sk)]
        s = S[(S.activity_id == aid) & (S.seg_kind == kind) & (S.label == lab)].iloc[0]
        km = C.STD_DIST[lab] / 1000
        v = float(s.vdot) - corr
        cpk = (s.climb_m or 0) / (s.dist_m / 1000) if s.dist_m > 0 else np.nan
        rows.append({"target_id": f"{aid}:extra_{lab}", "person": person, "activity_id": aid, "date": a.date,
                     "day": int(a.day), "tier": "extra", "km": km, "band": C.band_of(km),
                     "time_s": float(C.race_time(v, km * 1000)), "vdot": v, "vdot_gps": float(s.vdot),
                     "gps_corr": corr, "value_from": f"{kind} {lab} {s.time_s:.1f}s" + (" (GPS distance)" if corr else " (track mode)"),
                     "weight": 1.0 if conf == "high" else 0.5, "confidence": conf, "decision": "pending",
                     "rationale": why, "note": f"extra {conf}: {why[:60]}",
                     "surface": a.surface, "terrain": a.terrain, "flat_course": a.flat_course, "climb_per_km": cpk,
                     "grade_abs_mean": s.grade_abs_mean, "workout": a.workout, "hr_device": a.hr_device,
                     "trust_interval": a.trust_interval, "trust_steady": a.trust_steady, "wrist_temp_max": a.wrist_temp_max,
                     "place": a.place, "track_name": a.track_name, "indoor_bislett": a.indoor_bislett, "atype": a.atype,
                     "type": a.type})
    X = pd.DataFrame(rows).sort_values(["person", "date"]).reset_index(drop=True)
    OUT.mkdir(parents=True, exist_ok=True)
    X.to_parquet(OUT / "extra_targets.parquet", index=False)
    X.drop(columns=["rationale"]).to_csv(OUT / "extra_targets.csv", index=False)
    with pd.option_context("display.width", 250, "display.max_colwidth", 40):
        print(X[["target_id", "person", "date", "km", "vdot_gps", "gps_corr", "vdot", "time_s", "confidence", "workout"]]
              .round(2).to_string())


if __name__ == "__main__":
    main()

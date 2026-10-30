# How runs are analysed (`scripts/run_analysis.py`, detector v2)

Input: one FIT file. Output (dict used by the report): reps, breaks, Garmin laps matched to
reps, km splits, totals. Calibration of the results: [distance-calibration.md](distance-calibration.md).
Plan and status: [plans/timing-and-distance-accuracy.md](plans/timing-and-distance-accuracy.md).
v1.1 (threshold on 5 s-smoothed speed) is still available as `analyse_v1` for comparison.

## Steps
1. **Distance onto a 1 Hz grid by linear interpolation** (not forward fill: FR935 smart recording
   has 2–5 s gaps, and forward fill made fake speed steps). Speed = Δ distance per second. The
   `enhanced_speed` field is never used for timing (3–5 s late).
2. **Change points** with PELT on a piecewise-constant speed model. The search is split at
   recording pauses > 30 s.
3. **Rep segments** = segments whose mean speed ≥ max(2.5 m/s, 0.72 × p90 of moving speed). Gaps ≤ 8 s are
   merged and segments < 20 s are dropped.
4. **Sub-second boundaries:** each boundary is refined by fitting constant speed before / after
   to the raw distance records within ±10 s (`start_detected`, `end_detected`).
5. **Lag compensation** per mode/watch from `calibration.json` (`detector_lags`, else
   `lag_compensation`, else defaults) → `start`, `end`. Today: GPS/FR935 1.18 / 1.54 s, GPS/4315
   0.49 / 0.94 s, track 0 / 0 s (start / end).
6. **Lap press as cross-check** (Adrian's decision: the speed jump is the truth, a press can be
   early or late). A press within 1 s means `confirmed`, otherwise "pressed early/late by X s"
   (`start_check`, `end_check`, `flag`, `press_start_dt`, `press_end_dt` = press − boundary). Laps
   still decide which laps are reps (`intensity`).
7. **Distance:** in track mode with a matching active lap, rep `dist` = Garmin lap distance
   (`dist_source: "lap"`). Otherwise it's the distance field between the detected change points
   (`dist_source: "detected"`). `dist_detected` is always kept.
8. **Uncertainty:** `start_uncertainty_s` / `end_uncertainty_s` = base (GPS 0.6 s, track 0.7 s,
   68th percentile from the Bislet evaluation) + ½ × (local record gap − 1 s). `totals.distance_uncertainty_pct`
   (1σ) comes from `gps_quality.py` with the model in `calibration.json` `uncertainty_model`, plus
   `totals.gps_env` (track / open / urban_or_trees / unknown / indoor).
9. **Breaks**, **Garmin laps** (matched by largest overlap) and **km splits** work as in v1.1.
   A run counts as an interval session when it has `active` laps.

## Accuracy (Bislet, 58 sessions, 365 rep laps, leave-one-session-out; `evaluate_detector.py`)
| Mode / watch | \|start err\| v1.1 → v2 | \|duration err\| v1.1 → v2 | Rep distance vs geometry v1.1 → v2 |
|---|---|---|---|
| GPS / FR935 (232 reps) | 3.6 → **0.50 s** | 2.8 → **0.62 s** | −1.24 % → **+0.21 %** |
| GPS / 4315 (37) | 2.2 → **0.44 s** | 1.3 → **0.46 s** | −1.12 % → **−0.40 %** |
| Track / 4315 (89) | 1.05 → **0.44 s** | 1.8 → **0.82 s** | −0.78 % → **−0.06 %** (lap distance; detected alone +0.27 %) |

Targets (median |start| ≤ 0.5 s, median |duration| ≤ 1 s, distance within ±0.5 %) are met on the
median. In GPS mode only ~46 % of individual reps are within ±0.5 % (p10..p90 −1.2..+1.6 %), against 81 % in track mode.
In GPS mode the detected distance beats Garmin's lap distance (−0.7..−1.25 %, booked 1.5 s before the press).

## Pipeline order (calibration)
`gps_lap_analysis.py` → `evaluate_detector.py` → `route_accuracy.py` → `calibrate_distance.py` (merges
everything into `calibration.json`) → `build_report.py`.

## Signal quirks (from the FIT files; step-model lags vs lap press, see distance-calibration.md)
- `enhanced_speed` is smoothed by the watch and **3–5 s late** (ends later than starts).
- `cadence` and `power` are **4–6 s late**.
- `distance` is the best timing signal: in GPS mode ~0.4–1.6 s late depending on watch, in track mode
  model-corrected (stalls 1–3 s around lap marks, positions snapped, constant −1.1 m offset, so lane
  changes can't be seen afterwards).
- HR lags by physiology (tens of seconds). Use it for load and recovery, not timing.

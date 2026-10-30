# Plan: accurate rep timing and distance, calibrated on Bislet

Status: implemented (all phases; two data gaps remain, see Progress), 2026-10-30. Owner: Adrian. Related: [distance-calibration.md](../distance-calibration.md),
[run-analysis.md](../run-analysis.md), [fit-files.md](../fit-files.md).

## Progress
- [x] Phase 0a: watch eras found (FR935 → 2026-02-17, product 4315 from 2026-02-24); see [fit-files.md](../fit-files.md)
- [x] Phase 0b: priority download, 67 activities (all runs near Bislet, likely races), 135 calls, 3 min 19 s, no 429.
  100 FIT files on disk. `select_activities.py` builds the id lists.
- [x] Phase 0c: backfill of 382 outdoor + 10 indoor runs, 393 calls at 3 s, ~20 min, no 429. All 492 runs on disk.
- [x] Phase 1/2 for GPS mode: `gps_lap_analysis.py`, 41 sessions / 402 presses → **no steady hidden GPS lag**
  (≈ −0.3 s, ≲ 2 m); Garmin books lap distance 1.5 s before the press; GPS reacts 1–1.7 s late to
  speed changes; rep laps −0.9 % (see [distance-calibration.md](../distance-calibration.md))
- [x] Phase 2: step-model lag fit per signal/mode/watch with session-bootstrap CIs and pace regressions
  (`gps_lap_analysis.py` → `lag_table`, `lag_vs_pace`, `lag_compensation`). Distance field GPS/FR935 0.9/1.6 s,
  GPS/4315 0.4/0.8 s, track ≈ 0 (clamped). Speed field 3–5 s, cadence/power 4–6 s. Lag is constant in time, not pace-dependent.
- [ ] Phase 2: new-watch GPS-mode laps at Bislet. **Blocked on new data:** the backfill added none, so only 5
  sessions / ~40 start-end presses exist. Needs Adrian to run a few GPS-mode sessions at Bislet with lap at the line.
- [x] Phase 3: detector v2 (PELT change points, sub-second refit, `detector_lags` compensation, press
  cross-check + flags, per-boundary uncertainty, FR935 interpolation). `evaluate_detector.py`, LOSO on
  58 sessions / 365 reps: |start| 0.44–0.50 s, |duration| 0.46–0.82 s, distance −0.40..+0.21 % (median). Targets met on medians.
- [x] Phase 4: races (6, nominal/certified), repeated routes (3 clusters ≥ 3 runs, FR935 only, SD 0.70 %),
  uncertainty model, no stable bias, so no correction factor (`route_accuracy.py`, `gps_quality.py`). OSM tried and
  rejected (naive snapping unreliable). Gap: no repeated routes on the new watch.
- [x] Phase 5: `run_analysis.py` v2 with `distance_uncertainty_pct`, `calibration.json` merges all parameters,
  report (Session: boundary source, press check, uncertainty bands; Distance check: lag table, lag-vs-pace,
  detector v1.1 vs v2, races, routes), docs updated.
- [x] Docs: how to get data, onboard a runner, calibrate a watch: [onboarding-and-calibration.md](../onboarding-and-calibration.md)

Answers from Adrian (2026-10-30): no chip times or splits beyond the data; switched watch once
(found: 2026-02-18); download as much as possible but no long downloads yet.
Later the same day: full backfill OK for next session; Lier 10k 2025-11-02 and 2023-05-07, Oslo 10k
2023-04-22 and Trondheim 5k 2021-05-05 are races (no official times); GPS mode unknown (assume default).

## Questions
1. Is there a systematic lag at the start and end of a rep (or a run) that we don't see, e.g. ~2 s?
2. Does it depend on pace?
3. What's the most robust way to get accurate rep boundaries, rep distance and run distance?
4. Can the Bislet sessions (known geometry, lap button at the line) improve accuracy for runs
   *outside* Bislet?

## What we know today
| Fact | Evidence |
|---|---|
| The watch's **speed field lags the lap button by 4 s** (p10 3 s, p90 5 s) at rep starts | 89 active laps, 13 track sessions |
| Speed from the **distance field** reacts at once (median 0 s, p90 1 s) | same 89 laps |
| The **cadence field** isn't a good onset signal (median +5 s, spread −5..+8 s) | same 89 laps |
| In track mode the distance field **stalls 1–3 s around lap marks** and positions are snapped to the track (constant −1.1 m offset) | 27 Oct session, 13,941 track points |
| Garmin lap distance and the watch distance field agree with track geometry to about 0.1 % (median) in track mode | 80 reps, 11 sessions |
| Our detected reps (v1.1, distance-derived speed): start +1 s late, duration −1.8 s, distance −6 m (median) vs laps | 79 reps |
| Normal (GPS) mode at Bislet: watch is −2.0 %..+0.6 % of the lane-corrected expectation | 6 runs |
| Normal-mode Bislet runs have **~130 manually pressed laps** at the line (e.g. 52 on 2025-09-07, 24 on 2025-10-27) | lap triggers in FIT |
| Certified races: Garmin +0.5 % (Rome) and +1.2 % (Copenhagen) | 2 races |

So "a 2 s lag we don't see" isn't established. What we *can* say: the **speed field** is
~4 s late, the **distance field** isn't, and our detector is ~1 s late at the start and ~1 s
early at the end because of smoothing. Whether any lag remains in the distance field itself
(GPS filter) is the main open question, and only GPS-mode data can answer it, since track
mode is model-corrected.

## Approach

### Phase 0: data (≈ 1 evening, ~400 requests at 1 req/s)
- Download every track-mode session and every Bislet run with manual laps (all years), plus
  all runs that start or end near Bislet.
- Ask Adrian for **official chip times and splits** of races (Copenhagen Half 2025, Rome 2019,
  any others), and for routes with a known distance (e.g. a measured loop).
- Record watch model and GPS mode per activity (`device_info`, `device_settings`, and
  proprietary messages if they can be decoded). Accuracy differs between GPS only, all
  systems and multi-band.

### Phase 1: build a ground-truth table of transitions
One row per rep start and rep end (≈ 170 transitions in track mode, ≈ 260 in GPS mode):
- `t_press`: lap button time (truth for timing, ±0.3 s human reaction);
- `d_truth`: geometry distance for the lap (multiples of 200 m are exact with the track
  model; lane offset from the GPS projection in GPS mode);
- signals around the transition (−10..+15 s): distance field, speed field, cadence, power,
  raw GPS displacement (GPS mode), HR;
- context: pace of the rep, standing vs rolling start, mode (track/GPS), watch, date.

### Phase 2: measure lags and their pace dependence
- For each signal, estimate the lag vs `t_press` by **cross-correlating with a step model**
  (more robust than threshold crossing), then per transition type (start/end).
- Regress `lag_time ~ pace + start_type + mode` and `dist_error ~ pace` to test whether the
  error is **constant in time** (a filter delay: metres grow with pace) or **constant in
  distance** (snapping).
- In GPS mode, compare distance-field lag with raw-position displacement lag; that's the lag
  "we don't see".
- Output: a lag table per signal/mode with confidence intervals, plus plots in the report.

### Phase 3: detector v2 (rep boundaries)
Decision (Adrian, 2026-10-30): the **speed jump is the truth for when the rep starts/ends**, since a lap
press can be late or early. The lap press is a cross-check and a label, not the boundary.
1. **Change-point detection on distance-derived speed** (piecewise-constant model, e.g. PELT/
   binary segmentation), not a fixed threshold. It finds the jump, not the moment smoothing
   crosses a level.
2. **Lag compensation** per watch and mode from Phase 2 (GPS mode reacts ~1–1.7 s late to speed
   changes; track mode ~0–0.4 s).
3. **Lap press as a cross-check:** if press and change point agree within ~1 s, report it as
   confirmed. If they differ more, keep the change point and flag "pressed early/late by X s".
   The press still tells which laps are reps (`intensity`), and in track mode Garmin's lap distance
   is the best distance.
4. Speed field and cadence are for confirmation only. Never use them for timing.
5. FR935 smart recording (2–5 s gaps): interpolate distance, and report wider boundary uncertainty.

Evaluate leave-one-session-out on Bislet. Targets: median |start error| ≤ 0.5 s, median
|duration error| ≤ 1 s, rep distance within ±0.5 % of geometry.

### Phase 4: carry Bislet over to runs outside Bislet
What transfers and what doesn't:
- **Transfers (device-level):** signal lags and filter behaviour, detector parameters,
  speed-field correction. Same watch, same firmware: Bislet calibrates these well.
- **Partly transfers:** the GPS distance scale. Bislet is open sky; it gives the *best case*
  error (~±0.5 % after lane correction). Use the ~130 manual GPS-mode laps to estimate per-lap
  noise and bias.
- **Doesn't transfer:** urban canyons, trees and tunnels. Estimate these separately:
  - **repeated routes** (same start/finish, similar shape) give run-to-run spread, which is a
    precision estimate;
  - **map-matched route length** (OpenStreetMap footpaths) as an independent distance;
  - **certified races** with official splits give accuracy against truth;
  - per-activity GPS quality indicators (sharp heading changes, position jitter vs
    distance-derived speed, `gps_metadata`) as an uncertainty score.
- **Output:** no global "buffer". Each activity gets `distance`, `distance_uncertainty` (%) and
  `boundary_source` (lap / detected / detected+compensated). A correction factor only goes in
  if Phase 4 shows a stable bias in a given environment.

### Phase 5: put it in the product
- `run_analysis.py` v2: change-point detector, lag compensation, uncertainty fields.
- `calibrate_distance.py`: add the GPS-mode manual-lap table and the transition table;
  keep `calibration.json` as the single source of parameters.
- Report: Distance check gets lag plots and pace-dependence; Session shows where each
  boundary came from and an uncertainty band.
- Docs: update `run-analysis.md`, `distance-calibration.md`, this plan's status.

## Decisions needed from Adrian
1. Race list with official chip times and splits (and anything else with a known distance).
2. Watch model(s) and GPS setting used (and whether it changed over time).
3. On Bislet, is the lap button always pressed exactly at the line, and is the track-mode lane
   set to 1?
4. OK to run the Phase 0 download (~400 requests, about 7 minutes at 1 req/s)?

## Risks
- Track-mode data is model-corrected, so it validates timing but not raw GPS.
- Human lap-press error (±0.3 s) limits how far below ~0.3 s timing can be validated.
- Older watch (FR935, smart recording 1–4 s) needs its own parameters.
- Proprietary FIT messages may hold GPS-mode info we can't decode.

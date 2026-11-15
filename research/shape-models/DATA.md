# Shape-model research: dataset, targets and harness

## Changelog

**2026-11-05 (G11): bug A1 fixed, dataset rebuilt (v2).** `prep_data.load_person` used to build
`prod = {id: item}`, so for the 32 sessions that have both an `interval/hard` score and a `long/bound` item in
fitness.json (adrian 15, rise 15, patrik 2) only the bound survived. Now items are keyed on `(id, discipline)`:
- New `prod_items.parquet`: every production item, one row per `(person, activity_id, discipline)` (`kind, score,
  km, sigma, window_s, gap_speed, rep_s, rest_s, usable, start_local`). Read it with `harness.prod_items(person)`.
- `activities.prod_*` now holds the **interval** item when a run has both; the long/bound item of such runs is in the
  new `prod_bound_kind / prod_bound_score / prod_bound_km / prod_bound_sigma` columns (`prod_n_items` = 1 or 2).
  Models that read `activities.prod_*` directly now see these 32 interval scores but miss the 32 bounds; use
  `prod_items.parquet` to get all items, as production does.
- `harness._prod_state` (production_curve / production_flat) reads `prod_items.parquet`.
- Targets: 203 → 235 (interval adrian 110 → 125, rise 56 → 71, patrik 7 → 9; 164 → 196 scored). Race and
  hard_effort targets and segments are unchanged.
- Baselines (production_curve): race MAE adrian 0.89 → 0.91, rise 1.78 → 1.75, a+r 1.34 → 1.33; interval 2.28 (164)
  → 2.41 (196; on the old 164 targets 2.28 → 2.28; the 32 new targets are harder: 3.05); hard_effort 4.18 → 4.16.
  Reproduction vs fitness.json's published pre-race predictions: max |diff| 0.37 → **0.01** VDOT (was off mainly
  on rise's Copenhagen Half because of the missing interval scores).
- The v1 parquets are kept in `data/_research/shape/v1_backup/`. Round-1 / G9 / G10 numbers were computed on v1.
  Details: `approaches/g11-rescore/REPORT.md`.

Code: `research/shape-models/` (`common.py`, `prep_data.py`, `harness.py`). Data (health data, gitignored):
`data/_research/shape/` (`activities.parquet`, `segments.parquet`, `targets.parquet`, `prep_report.json`,
`baseline_preds.parquet`).

**Interpreter:** `/tmp/shape-venv/bin/python` (Python 3.14; numpy, pandas, scipy, scikit-learn, lightgbm, xgboost,
statsmodels, pyarrow, matplotlib, fitparse). The project venv (`uv run python`) has only numpy, so it can't read
parquet. If /tmp was wiped, rebuild it with:
`uv venv /tmp/shape-venv --python .venv/bin/python && uv pip install --python /tmp/shape-venv/bin/python numpy pandas scipy scikit-learn lightgbm xgboost statsmodels pyarrow matplotlib fitparse requests`.
Run everything from the repo root. Don't modify existing repo files: write new files only.

```
/tmp/shape-venv/bin/python research/shape-models/prep_data.py      # ~70 s (adrian's FR935 altitude levelling dominates)
/tmp/shape-venv/bin/python research/shape-models/harness.py        # baselines, ~7 s
```

## Sources and reuse of production code
Per person: `groups.json` (session types, places, tracks, reps, overrides), `activities_index.json` (Garmin summary),
FIT files, `fitness.json` (production per-session scores only), `profile/hr_settings.json` (device, trust, HR flags,
max / rest HR), `ranges/daily_summaries_*.json` (daily resting HR), and `data/races.json` (official results;
**only `sport == "run"` races are used**, ski races are ignored). The 1 Hz series is the production one
(`session_groups.read_series` / `to_grid` / `indoor_grid` / `track_rep_grid` / `find_pauses`; `fitness_model.level_alt`
and `gap_factor`), imported read-only.

**Running only:** groups.json `sport == "run"` (atype running, track_running, treadmill_running, indoor_running,
trail_running, one multi_sport run). Ski, hike, bike, swim etc. are excluded; their hours only enter the
`all_h_*` / `ski_h_28d` load features.

## Counts (build 2026-11-04)

| person | runs | usable | pace_ok | hr_ok | first – last | km | road / track / indoor_track / treadmill / trail / nogps |
|---|---|---|---|---|---|---|---|
| adrian | 495 | 481 | 405 | 481 | 2018-12-22 – 2026-11-03 | 4000 | 335 / 79 / 27 / 6 / 0 / 48 |
| rise | 179 | 177 | 136 | 173 | 2019-08-21 – 2026-11-03 | 1571 | 111 / 51 / 9 / 4 / 1 / 3 |
| patrik | 96 | 95 | 72 | 95 | 2021-01-23 – 2026-11-03 | 650 | 72 / 6 / 0 / 0 / 3 / 15 |

Workouts: adrian easy 196, intervals 126, steady 77, unknown_nogps 37, other 22, long 20, tempo 10, race 5,
time_trial 2; rise intervals 74, easy 48, steady 31, long 11, other 7, race 5, tempo 2; patrik easy 40, steady 13,
nogps 13, intervals 11, other 7, long 6, tempo 5, race 1. HR devices: adrian fr935/wrist 433, 4315/wrist 56,
3077/wrist 6 (borrowed); rise fenix3_hr/wrist 145 + strap; patrik 3077/wrist 96.

Segments: adrian best 1819 / best_gap 1839 / km 3612 / rep 961; rise 680 / 685 / 1460 / 828; patrik 345 / 358 / 608 / 79.

Targets (tier × band, before `SKIP_FIRST`):

| person | race short / 10k / HM+ | interval (short) | hard_effort short / 10k / HM+ |
|---|---|---|---|
| adrian | 1 / 3 / 1 | 110 | 11 / 1 / 1 |
| rise | 0 / 2 / 3 | 56 | 1 / 1 / 0 |
| patrik | 0 / 1 / 0 | 7 | 4 / 0 / 0 |

## Cleaning rules
- **Speed** from Δdistance on the 1 Hz grid, clipped at 8 m/s (production). `glitch_s` = grid seconds above 8 m/s
  (GPS spikes); runs with > 10 such seconds get `gps_glitch` (adrian 6, rise 27 (fenix 3), patrik 8). Segments with
  any glitch second inside, or VDOT > 80, are `suspect` (most are short best efforts; drop them for 400 m / 1 k work).
- **Pauses:** speed < 0.5 m/s for ≥ 15 s or timer stopped (production `find_pauses`). Best efforts are searched
  inside one pause-free leg only (no pause can make an effort look faster). km splits run on elapsed time;
  `moving_share` < 1 shows a pause inside.
- **Altitude / grade:** production rules: 9 s smoothed altitude, revisit levelling (`level_alt`, removes adrian's
  FR935 2024–25 barometer drift during reps), grade over ±45 s, clipped ±20 %, zeroed around barometer jumps
  > 1 m/s. Flat courses (track, Bislett indoor, treadmill, watch track mode) have grade 0. The Frogner stadion loop is
  road (hilly ~890 m loop), `frogner_loop` = True. `climb_m`/`desc_m` use a 3 m hysteresis on the levelled altitude;
  `climb_raw_m` on the unlevelled.
- **GAP:** `gap_speed` = production rule (Minetti 2002 uphill, half of Minetti's downhill saving);
  `gap_speed_strava` = Strava-style quadratic (`common.gap_factor_strava`). Both per second, averaged.
- **Distance trust:** treadmill and groups.json `dist_trusted == False` → `dist_untrusted` (pace unusable). GPS
  reads long: race best efforts are 0.2–2 % faster than the official times, rise's 2026-10-23 10k 5 % (watch
  10.51 km). Track reps are scaled to their set distance (production `track_rep_grid`); Bislett indoor reps come from
  laps (n × 546.5 m − 7.5 m), time-only indoor reps have no distance (`rep_dist_m` NaN).
- **HR:** `hr_ok` = HR present in ≥ 75 % of moving seconds and no hr_settings flag (`cadence_lock`, `hr_dropout`,
  `hr_high_for_pace`). Trust per device (`trust_interval` / `trust_steady`, 0..1) from hr_settings: FR935 wrist
  0.3 / 0.5 (under-reads hard reps), 4315 1.0 / 1.0, rise wrist 0.57 / 0.69, strap 0.84 / 0.88, patrik 0.42 / 0.58.
  %HRR uses the person's max HR and the 30-day median daily resting HR before the run (`hr_rest_30d`).
- **Usable** (`usable`): has a 1 Hz series, not `user_exclude` (session_overrides), not production-`implausible`
  (2026-01-20: ski saved as run), ≥ 1 km and ≥ 5 min, average < 6.5 m/s. `pace_ok` = usable and not
  `dist_untrusted` and not `gps_glitch`. Rows are never dropped; filter on these flags.
- **Temperature:** only the watch's wrist sensor (`wrist_temp_min/max`, Garmin summary): biased by body heat, no
  weather data exists. Use as a weak covariate only.

## `activities.parquet` (one row per run; `person`, `activity_id` unique)
- Identity / time: `person, activity_id, date, day` (date ordinal), `start_local`.
- Type: `atype` (Garmin), `type` / `type_detail` (session_groups), `workout` = race (official) | race_auto (race? not
  official, not in not_races; none at present) | time_trial (race? the user said was not a race: adrian's Bislett 5ks
  2025-10-24) | intervals | tempo | steady | long | easy | other | unknown_nogps; `sig` (rep signature), `n_reps`,
  `rep_time_med, rep_dist_med, rep_speed_mean, rest_med, work_rest, rep_total_m`.
- Place / surface: `place, area, route, track_name, surface` (track | indoor_track | treadmill | road | trail |
  unknown_nogps) + `surface_rule`, `terrain` (flat < 5, rolling < 12, hilly < 25, steep ≥ 25 m climb per km;
  flat courses flat), `flat_course, indoor_bislett, frogner_loop, lat, lon, turn_deg_per_km` (heading change per km:
  ovals ~900+), `speed_cv`. Trail vs road is **not** observable without map data: `trail` only from Garmin's
  trail_running type; hilliness is in `terrain` / `climb_m` / `grade_*`.
- Size: `dist_km, moving_s, elapsed_s, pause_s, n_pauses` (groups.json), `dist_km_garmin`, `grid_dist_m,
  grid_moving_s, grid_elapsed_s, n_legs, longest_leg_m, gps_share, glitch_s, max_step_m`.
- Elevation: `climb_m, desc_m, climb_raw_m, desc_raw_m, egain_garmin, eloss_garmin, alt_range_m, grade_abs_mean,
  grade_abs_p90, grade_share_gt4, gap_ratio` (Σ GAP speed / Σ speed, moving), `gap_ratio_strava`.
- HR: `avg_hr_garmin, max_hr_garmin, hr_mean, hr_p95, hr_peak12` (held 12 s), `hr_cover, hrr_mean,
  ef_gap_per_hr` (GAP m/s per bpm after 10 min), `decoupling` (EF first half / second half − 1), `hr_device,
  hr_source, trust_interval, trust_steady, hr_flags, hr_max_person, hr_rest_30d`.
- Garmin: `garmin_te_aer, garmin_te_ana, garmin_load` (Garmin's own training effect / load), `wrist_temp_*`.
- Races: `is_official_race, race_name, race_km, official_s, race_time_source, race_vdot, not_race`.
- Best efforts per run: `be_<d>_s` (raw distance) and `beg_<d>_s` (GAP distance) for d in 400m, 1k, 1mi, 3k, 5k,
  10k, HM (seconds; NaN when no pause-free leg is long enough).
- Whole run: `vdot_whole` (distance / moving time), `vdot_whole_gap` (× gap_ratio). Easy runs sit 8–25 below shape.
- Production per-session evidence (from fitness.json, pace variant): `prod_discipline` (interval | long),
  `prod_kind` (hard | race | bound | implausible), `prod_score` (VDOT: W′-balance interval score incl. offset −0.5;
  tempo best window incl. +3.0; race official VDOT; bound = best 20–90 min window), `prod_km` (effective distance on
  the curve), `prod_sigma`, `prod_window_s, prod_gap_speed, prod_rep_s, prod_rest_s, prod_work_rest, prod_work_hrr,
  prod_hrr`. They depend only on the session itself, except that the offsets were fitted in-sample on the races and
  `implausible` uses the cross-check against the interval shape. `shape_at`/`weight` (smoothed) are deliberately left out.
- Quality: `fit_error, quality` (comma list), `usable, pace_ok, hr_ok`.
- Load (days **strictly before** the run's date): `run_km_{7,28,42,365}d, run_h_{7,28}d, all_h_{7,28}d` (all
  sports), `ski_h_28d, n_runs_28d, n_hard_28d, trimp_7d, atl` (EWMA τ 7 d of daily TRIMP), `ctl` (τ 42 d), `tsb`
  (ctl − atl), `days_since_hard` (intervals / tempo / race / time trial), `days_since_run`. `trimp` = this run's
  Banister TRIMP (moving min × HRR × 0.64 e^(1.92 HRR); runs without usable HR get the median TRIMP/min of their
  workout type).

## `segments.parquet` (sub-run efforts)
`seg_kind`: `best` (fastest raw `target_m` in one pause-free leg), `best_gap` (fastest `target_m` of grade-adjusted
distance), `km` (consecutive 1000 m splits on elapsed time), `rep` (groups.json reps; `label` rep1..n).
Columns: `person, activity_id, date, day, seg_kind, label, target_m` (standard distance; for reps the set distance
if known), `time_s` (s to cover target_m; reps: rep time), `vdot` (Daniels VDOT of target_m in time_s; for best_gap
the GAP-equivalent VDOT), `start_s, dur_s, dist_m` (raw metres in the window), `speed, gap_speed, gap_speed_strava,
vdot_gap` (from mean GAP speed and duration), `grade_mean, grade_abs_mean, climb_m, desc_m, hr_mean, hr_max, hr_last3`
(mean HR over the last third), `hr_cover, hrr_mean, hrr_last3, moving_share, glitch_s, rep_dist_m, rest_after_s`,
plus the run's `usable, pace_ok, hr_ok, surface, workout` and `suspect`.
Caveats: FR935 (adrian until 2026-02-18) records every 2–5 s and patrik's 3077 every 4–6 s, so 400 m / 1 k bests are
coarse; HR lags tens of seconds (use `hr_last3` for reps, not `hr_mean`); a best effort in an interval session is a
rep (or rep + rest), not a continuous effort.

## Targets (`targets.parquet`) and tiers
Each target: `target_id, person, activity_id, date, day, tier, km, band, time_s, vdot, weight, note` plus covariates
known before the effort (`surface, terrain, flat_course, climb_per_km, grade_abs_mean, workout, hr_device, trust_*,
wrist_temp_max, place, track_name, indoor_bislett, atype, type`).
- **race** (primary, weight 1): official run results in races.json with a matched activity: official km and time
  (chip, or watch time where no chip time exists; patrik's Lier 10k 2023 is "around 42:00"). adrian 5 (5k, 3 × 10k,
  half), rise 5 (2 × 10k, 3 × half), patrik 1.
- **interval** (weight 0.5): production W′-balance score of each `hard` interval session, as a 5 km equivalent
  (`km` 5). Model-derived (it *is* the production evidence scale), so production-style models have a home advantage
  here; use it as a secondary check, not to pick models.
- **hard_effort** (weight 0.5): per non-interval session, the best GAP effort ≥ 3 km (3k / 5k / 10k / HM, the
  max-VDOT one) when the session is a tempo, time trial or auto race (intent), or the effort's last-third HR is
  ≥ 0.88 HRR with ≥ 80 % HR cover on a device with steady trust ≥ 0.8. Many tempos are sub-maximal (adrian's 2019–22
  tempos are 5–10 below his race level): the target's `workout` tells a model which kind it is.
- Bands: short ≤ 6 km, 10k 6–15 km, HM+ > 15 km (`common.band_of`).

## Harness (`harness.py`)
Rolling origin: each target is predicted from `history(person, day)` = rows with `day < target day` (≥ 1 day before;
same-day warm-ups and the effort itself are invisible). `SKIP_FIRST`: the first 3 interval targets per person are
not scored (no history); races and hard efforts are all scored.

```python
import sys; sys.path.insert(0, "research/shape-models")
from harness import evaluate, report, compare, production_curve
import common as C

def my_model(person, hist, target):
    # hist.acts / hist.segs / hist.targets: pandas views, day < target day; hist.key = (person, day) for caching
    s = hist.segs
    s = s[(s.seg_kind == "best_gap") & s.pace_ok & ~s.suspect & (s.day >= hist.day - 90)]
    if s.empty:
        return None                                   # abstain -> counted in n, not in n_pred; filled by the naive
    return {"vdot": float(s.vdot.quantile(0.95)), "sd": 2.0}   # or a float VDOT, or {"time_s": ...}

res = evaluate(my_model, persons=("adrian", "rise"), name="envelope95")
report(res); print(compare([res, evaluate(production_curve, persons=("adrian", "rise"))], "race"))
```
Output: `res["preds"]` (per target: actual, pred, sd, pred_filled), `res["summary"]` (tier × person × band: n,
n_pred, MAE, bias = mean(pred − actual), RMSE, mean |% time error|, `mae_filled`, NLL and 1-sd coverage when sd is
given), `res["headline"]` (race MAE adrian + rise pooled is the headline number). Expensive models should cache per
`hist.key` (`functools.lru_cache`) or per day; a model may fit anything it likes on `hist`, never on `load_data()`
rows at or after `hist.day`. Pooling across persons is allowed only through each person's own history up to the same
day (call `history(other, hist.day)` yourself).

## Baselines (2026-11-04)
`harness.py` (all tiers, all persons; MAE in VDOT, n = predicted):

| model | race adrian (5) | race rise (5) | race adrian+rise | race patrik (1) | interval (164) | hard_effort (19) |
|---|---|---|---|---|---|---|
| naive_best_recent_60d (max effort VDOT of the last 60 d) | 1.25 | 2.23 | 1.74 (10 of 10) | – (no history) | 3.34 | 4.65 |
| naive_last_race | 2.85 (4) | 2.81 (4) | 2.83 (8) | – | 3.26 | 5.57 |
| **production_curve** (reproduction of model_version 3) | **0.89** | **1.78** | **1.34** | 10.87 | **2.28** | **4.18** |
| production_flat (θ at every distance) | 1.58 | 1.94 | 1.76 | 10.87 | 2.28 | 4.28 |

% time error for production_curve races: adrian 1.35 %, rise 3.13 %. The reproduction matches fitness.json's published
pre-race predictions within 0.4 VDOT (it reads the same per-session scores; production also re-runs its cross-check).
patrik's only race (Lier 10k 2023) came after nothing but easy runs: every model is ~10 VDOT low there; treat
patrik as a qualitative check.

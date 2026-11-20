# Fitness / shape model (`scripts/fitness_model.py`)

`uv run scripts/fitness_model.py adrian [--eval]` → `data/adrian/fitness.json` (+ `fitness_eval.json` with
`--eval`, or run `scripts/fitness_eval.py adrian` on its own). Input is `groups.json` (run
`session_groups.py` first; it carries the session overrides and tracks) plus the FIT files, `data/races.json` (official
results, read on every build) and `profile/hr_settings.json` (run `hr_settings.py` first; other persons' files too, for
borrowed watches). Per-session features are cached in
`data/adrian/cache/fitness_model.pkl`, keyed by FIT mtime+size, the resting HR used and all parameters
in `P`. Timing: about 9 s cold (8 workers), 0.1 s cached. The eval takes 0.8 s.

## What it gives
Concept2 erg sessions (RowErg, SkiErg) are not in this model: they have their own pace / watts trend, [erg.md](erg.md).

Shape scores over time: **run** (one running number: the personal distance curve at 10 km, a 10k-equivalent VDOT)
and **ski** (rollerski + snow). There is also an **overall** score across disciplines. Each session becomes
0..n *evidence* items (running evidence is of type `interval` — interval sessions, 5k races — or `long` — tempo runs,
10k+ races, long-run bounds). A robust random-walk smoother turns them into a daily curve with an uncertainty
band. Running is one **coupled distance curve** (model_version 2, [below](#coupled-distance-curve-race-predictor)):
one shared fitness trend plus a personal VDOT-vs-distance slope, so `run` (= the curve at 10 km), the race
predictor (any distance) and the legacy read-outs `interval` (5 km) / `long` (~14.5 km) are readings of the same
model. The app shows only `run` ([One run number](#one-run-number), model_version 3).

**Scale: Daniels VDOT** ("running VO2max as shown by performance", ml/kg/min). Why:
- One number maps to equivalent race times (`race_time(vdot, dist)`). VDOT 55 = 5k 18:22, 10k 38:06,
  half 1:24:16. VDOT 60 = 17:03 / 35:22 / 1:18:09.
- The %VO2max-vs-duration curve handles effort length, which plain pace or Riegel can't.
- Ski is put on the same ml/kg/min scale through physics, so the numbers are comparable in kind. The
  absolute ski level depends on an assumed efficiency (see limitations). The trend is what matters.
- **Overall** = % of personal best per discipline, precision-weighted (1/sd²) across disciplines (running once,
  as `run`). Ski
  VDOT and run VDOT are never averaged directly. Overall leans on whichever discipline has fresh evidence
  (ski in winter, running in summer).

## Per-session scores
All running speeds come from the distance field on the 1 Hz grid (`session_groups.to_grid`), are
grade-adjusted and are capped at 8 m/s.

**Grade adjustment (GAP).** Equivalent flat speed = v · C(i)/C(0), with Minetti 2002
C(i) = 155.4i⁵ − 30.4i⁴ − 43.3i³ + 46.3i² + 19.5i + 3.6 J/kg/m.
- Only half of Minetti's downhill saving is used, because Minetti overstates it at running speed.
- Grade = Δaltitude/Δdistance over ±45 s of the 9 s-smoothed altitude, clipped to ±20 %.
- Barometer steps faster than 1 m/s zero the grade around them.
- **Altitude levelling** (`level_alt`, `P["alt_fix"]` = "level", since 2026-11-04): revisited positions must have the
  same altitude. Pairs of GPS points (every 5 s) within 10 m and ≥ 30 s apart (loops, out-and-back, standing still)
  give e(t₂) − e(t₁) = alt(t₂) − alt(t₁) for a smooth altitude error e(t) (piecewise linear, 30 s knots,
  first-difference penalty, pairs off by > 6 m after a fit dropped: bridges, GPS jumps); grade comes from alt − e.
  Without revisits e ≈ 0. Why: adrian's FR935 from 2024 reads 10–35 m high during hard reps and sinks back while
  standing after them (altitude change during the rests after reps: mean ≥ 5 m (up to 35 m) in 35 of his interval
  sessions 2024-04 – 2025-11; ≤ 1 m on the 4315, mostly ≤ 5 m in older FR935 data). 2024-04-14 (three closed ~1000 m road loops ~2.5 km north of Bislett, with patrik):
  +15…+25 m per loop raw, +0…+3 m levelled; patrik's watch on the same loops −1…+2 m. Effect on adrian's FR935 road
  sessions 2024–25: −1 to −5.6 (raw W′bal), 4315 sessions ±0.2. Check on the Frogner loop: with the reliable 4315
  barometer the grade adds +2.2…+2.5 over flat; adrian's FR935 2025-08-19 / 08-26 added +5.0 / +4.1 raw, +1.3 / +1.3
  levelled. The part of the artefact that repeats at the same place on every lap can't be seen by revisits, so
  levelling removes most but not all of it (2024-04-14: +9.7 over flat raw, +4.1 levelled; patrik +0.8).
- Runs on a track count as flat: groups.json `track` (where the reps / the moving time were: Bislett and Øya stadion
  ovals; [session-groups.md](session-groups.md)), the watch's track mode, treadmill / indoor. The **Frogner stadion
  loop** (~890 m, ~12 m up and down per lap) is a loop, not a flat track (`session_groups.LOOPS`, `flat_track`; user
  decision 2026-11-04): its sessions are `intervals_road`, grade-adjusted, road σ, but GPS reps still count as whole
  laps (890 / 1780 m, `track_reps`). The Dubai track is not a track (soft, hot: road). Until 2026-11-04 the
  start place decided (`place == "Bislet"`), so Rise's Bislett sessions (start Marienlyst) and Patrik's (start St.
  Hanshaugen) were grade-adjusted on a flat track with the road σ, and runs starting at Bislett and running elsewhere
  were flat.
- The window length was picked by road-interval test-retest (below). ±10 s inflated Dubai hill reps by
  up to 16 VDOT.

**Interval: W′-balance (critical speed) model.** Rest periods enter through recovery.
- CS and D′ come from the VDOT itself, by fitting v = CS + D′/t to Daniels' speeds for 3–15 min
  efforts. VDOT 50 / 55 / 60 → CS 4.12 / 4.46 / 4.78 m/s, D′ 152 / 164 / 176 m.
- Above CS, W′ drops by (v − CS)·dt.
- Below CS, W′ recovers exponentially with Skiba's τ = 316 + 546·e^(−0.01·DCP). DCP = 250 W · (CS − v)/CS
  expresses the recovery depth as a share of CS. The result is multiplied by `tau_scale` = 0.35, which
  gives τ ≈ 126 s standing and 165 s jogging at half CS.
- The session score is the lowest VDOT for which W′ never goes below 0 over the whole session: "the
  session was just possible". It's found on a coarse-to-fine grid over 2 s steps.
- Then `interval_offset` = −0.5 is added (re-fit 2026-11-04, "Offsets re-fit" below; −2.5 before).
- Synthetic 6×1000 @ 3:15 with standing rest scores 57.2 / 55.8 / 52.9 / 51.3 for 60 / 90 / 180 / 300 s
  rest. This is the work:rest effect: the same pace on shorter rest is worth more.
- **Track reps at their set distance**: in GPS-mode track sessions the speed inside each rep of groups.json
  `track_reps` is scaled so the rep covers its set distance (`session_groups.track_rep_grid`; rules in
  [session-groups.md](session-groups.md)): Patrik 2026-11-03 6×1000 logged 921–969 m (44.9 → 47.0), Rise 2026-10-20
  400s logged 411–467 m (54.5 → 49.5); Frogner loop reps at whole laps (877 m → 890, 1750 m → 1780). The watch's track
  mode is already at the set distance; Bislett indoor has its own lap grid. σ: `int_track` (1.5) when `track` is a flat
  track (not the Frogner loop), else `int_road` (2.0).
- Only `intervals_track` / `intervals_road` sessions get an interval score. Treadmill sessions are
  excluded because their distance is untrusted. Bislett indoor sessions (winter) score from their whole-lap
  reps only (n × 546.5 m − 7.5 m, speed 0 between reps); no continuous / HR-aerobic items from them.

**Races** (`race?` within 3.5 % of 5k / 10k / half / marathon): Daniels VDOT from the nominal distance
and moving time.
- ≤ 7 km counts for interval, with kind `race`.
- Longer races count for long (`race`). On the distance curve each race sits at its own distance, so a 10k is
  counted once. (`collect_evidence` also emits an interval/`hard` σ 2 copy of a 10k for the per-discipline
  back-tests in `fitness_eval.py`; `build` drops it, `is_10k_dup`.)

**Long: continuous efforts.** Daniels VDOT of the best grade-adjusted window of 20 / 30 / 45 / 60 / 90 min
inside one pause-free leg.
- Tempo runs are `hard` and get `tempo_offset` = +3.0. Steady runs (session_groups `steady`) are bounds like easy runs. Tempo is run below race effort: the median
  residual was −3.0 before the offset.
- Every other run is a `bound` (a lower bound only, e.g. a long easy run).
- **Cross-check:** long evidence above the interval shape + 3 + 2·sd is marked `implausible`. That's
  physically impossible for one runner. It caught two mislabelled sessions: 2022-02-03, 45 min at
  8 m/s, and 2026-01-20, 30 min at 3:09/km with HR ~150, probably skiing saved as a run.

**Ski: mechanical power → VO2.**
- Per window, P/m = g · net climb / D + mean(μ·g·v + ½ρ·CdA/m·v³), with μ = 0.025 (roller, May–Oct),
  0.035 (snow), 0.03 (unknown), and CdA/m = 0.5/75.
- VO2 = 3.5 + (P/m)/η · 60/20.9, with gross efficiency η = 0.16 (Sandbakk: 15–18 % on roller skis).
- The score is the best window of 20–90 min of *moving* time (pauses cut out), divided by Daniels
  %VO2max(D).
- The net altitude change per window is used instead of per-second climb clipped at 0. Clipping
  rectifies barometer noise: the per-second climb on the Holmenkollen route (R3) ranged 361–568 m for
  the same ~390 m net climb. The FR935's altitude on one leg is off by up to ±40 %, so windows are
  ≥ 20 min.
- `ski_hard` and `ski_intervals` sessions are `hard` (ski_intervals are hard sessions with detected reps,
  [session-groups.md](session-groups.md)), the rest are `bound`.
- **Plausibility filter** (`ski_implausible`): a scored window faster than `ski_v_max` = 7 m/s (25 km/h; Vasaloppet
  winners average ~7 m/s, the fastest real 20-min window in the data is 6.0 m/s) or with a net vertical rate above
  `ski_vz_max` = 0.3 m/s (1080 m/h; steepest real window 0.21 m/s) is marked `implausible` and dropped. It flags
  exactly two sessions, both rise's: 2024-03-03 (14235489452, 30 km/h for 20 min, score 87) and 2025-01-25
  (18096582511, 481 m net climb in 20 min, score 120). Those two had held rise's ski shape at 87 ± 7; now 51.5 ± 3.6.
  None for adrian / patrik.

**Official races (`data/races.json`, optional).** User-supplied chip times with the matched Garmin `activity_id`
per person (null = not in the data; skipped). `load_features` attaches them to the activities (`official_race`),
`score_session` uses them whatever the session was typed:
- **run**: a `race` at the official distance and chip time (`vdot_from_race`), on the curve at that distance
  (`race_km`); exempt from the cross-check. Rise's 10k 2026-10-23 (typed `tempo`, watch 10.51 km, had been
  rejected as implausible at 58.1) is now a 10k race, VDOT 52.2 (39:50).
- **not_races** (`{person: [{activity_id, note}]}`): auto-detected `race?` sessions the user says were not races
  become `steady` (lower bound only). adrian: the two Bislett 5ks of 2025-10-24 (19:01, 18:01).
- **tests** (`{person: [{activity_id, date, km, time_s, time, note}]}`, since 2026-11-05): user-confirmed all-out
  tests / time trials (`load_tests`, attached as `time_trial`). Scored exactly like an official run race (kind `race`,
  σ 1.0, `vdot_from_race(km, time_s)`, at its own distance `race_km`, no tempo item, never HR-demoted), with component
  `test` (e.g. "3k test") instead of `official`. They are **not** official races: not in fitness.json `races` (Races
  pages, race stars) nor in `calibration_set.py` / the official back-test. Why: adrian's interval sessions sit
  1.7–4 VDOT below his all-out level at every distance, so the model under-predicted his Bislett 3k tests and read
  the shortfall as a "stronger at long distances" slope; the tests are the only maximal short evidence he has
  (research/shape-models/SUMMARY.md, approaches/g11-rescore/REPORT.md §3). adrian: 10k road time trial 2023-04-22
  (10.02 km, 39:56 watch time, VDOT 52.2), Bislett 3k 2025-11-15 (3.00 km in 9:54, 59.5: the whole recorded run, 3.07 km
  GPS, taken as 3000 m since track GPS reads ~2 % long; the GPS-best 3000 m of 9:41 would give 61.0), Bislett 3000 m
  2026-03-31 (track mode, 10:27, 56.0). Effect: adrian's slope today +0.8 → −0.2 (5k 54.6 unchanged, half 56.3 → 54.3).
- **ski**: kind `hard` with σ `ski_race` = 2.0, the usual best-window score; never demoted by the HR variant.
  Why not `race`: `race` means "Daniels VDOT from official distance and time" (σ 1.0, symmetric), but a ski score is
  a 20–90 min window inside a 2–7 h paced race, so a low score is weak evidence (hard: robust below) while known
  intent earns a tighter σ than a training session (2.5). A whole-race score (mean demand over the moving / chip time
  ÷ Daniels %VO2max) was tried and not used: Daniels' curve floors at 80 % beyond ~3 h, so it falls with race length
  (adrian 41–49, Grenaderløpet lowest). Components: `official`, `official_s`, `race_km`.

## Smoother (`smooth_series`)
- Daily random walk θ_t = θ_{t−1} + N(0, q), Kalman filter + RTS smoother. In a gap the mean holds (or
  is interpolated between evidence) and the sd grows by √(q·days). That's the honest "no evidence"
  band.
- q (VDOT²/day) = 0.05 interval, 0.02 long, 0.05 ski. These are the best one-step-ahead predictive
  log-likelihood on a grid, capped at 0.05 (≈ 1.2 VDOT sd per month).
- Observation sd per kind (`SIG`, from test-retest below): race 1.0, track intervals 1.5, road intervals
  2.0, 10k-in-interval 2.0, tempo 2.0, bound 2.5, ski hard 2.5, ski bound 3.0.
- Asymmetric robust weights (IRLS, 5 passes). Training sessions are often not maximal, so a score far
  below the estimate is weak evidence. A score far above is either real or a glitch.

| kind | below the estimate | above |
|---|---|---|
| race | full weight to 2.5 sd, then (2.5/z)² | same |
| hard | full weight to 1.0 sd, then (1/z)² | full to 2.5 sd, then (2.5/z)² |
| bound | ignored | ignored up to +2 sd (no ratchet from noise), counted 2–4 sd, then (4/z)² |

## Stale shapes
- **Uncertainty grows without evidence.** The random walk adds q per day, so a discipline without new race / hard
  evidence keeps its last value and its sd grows by √(q·days). On the distance curve intervals inform long through θ,
  so only the slope goes stale without 10k+ evidence.
- **Staleness fields** in every `current`: `last_evidence_date` (latest race / hard item with weight > 0.5, the same
  rule as the API's `last_evidence`; for run / interval / long any item on the distance curve), `days_since_evidence`,
  `stale` (> `STALE_DAYS` = 90 days, or none). The app greys a stale value and says "last evidence 12 mo ago" (Home
  shape strip, Fitness table, activity Shape section), and hides its % of best / trend there.

## Coupled distance curve (race predictor)
Running is one model since model_version 2 (`smooth_curve`, parameters in `CURVE`, fitness.json `curve`):
- **State per day:** θ_t = VDOT at 5 km (the reference) and s_t = slope, the VDOT change per doubling of distance.
  The prediction at any distance is **VDOT(km, t) = θ_t + s_t · x** with x = log2(km / 5).
- θ is a random walk with the interval q (0.05 VDOT²/day). s is **mean-reverting towards 0** (AR(1), `slope_revert`):
  s_t = φ·s_{t−1} + noise with **q_s = 0.01** and stationary sd **1.75** (= the prior, `slope_prior_sd`), φ = √(1 − q_s/1.75²),
  time constant ≈ 2·1.75²/q_s ≈ 600 days (was q_s 0.005, sd 1.5, ≈ 900 days, until 2026-11-05; see below). The slope is shrunk towards the Daniels default (0, same VDOT at every
  distance) unless the evidence is strong and recent; an old distance profile fades back towards 0. Choice:
  "Slope shrinkage" below.
  2-state Kalman filter + RTS smoother on the daily grid, per-kind σ (`SIG`)
  and the same asymmetric robust IRLS weights as `smooth_series`, with each residual taken against the curve at the
  item's own distance.
- **Effective distance per item** (`evidence_km`; `sessions[].km`):
  - race: its nominal distance (5k / 10k / half / marathon; official races and tests: `race_km`, so a 3k test sits at
    3 km). A 10k counts once.
  - interval session (hard): **5 km**. The interval offset (−2.5 then, −0.5 since 2026-11-04) ties the session scores to the race level; 7.07 km (the
    log-mean of the 5k + 10k races it was fitted on) was tried: better race MAE only because of the soft 5k
    (without it 0.72 vs 0.73), slightly worse forecasts, and worse for patrik. So 5 km.
  - tempo (hard): **14.5 km**, the log-mean of 10k and half. The +3.0 tempo offset was fitted against the long shape,
    which came from 10k / half races, so a tempo score *is* a 10k-half equivalent. Placing it at the window's own
    race-pace distance (≈ 5 km for 20 min) while keeping +3.0 counts the slope twice: long races then come out
    +0.7 too high (MAE 1.52 vs 0.73, table). Window distance with a re-fitted offset (+1.0) is as good for adrian
    but in-sample, and much worse for patrik (long forecast error 5.4 vs 1.3).
  - long-run lower bound: the grade-adjusted distance of the scored window (the race distance whose Daniels time is
    the window at that score's VDOT). Bounds rarely count (they only pull when > 2 sd above the curve).
  - Ski stays a separate discipline (its own `smooth_series`).
- **Disciplines from the curve:** `run` = the curve at **10 km** (θ + s; `run.ref_km` 10) — the one running number
  the app shows ([One run number](#one-run-number)). Legacy read-outs, kept in the JSON (`legacy: true`) but not shown:
  `interval` = the curve at 5 km (θ), `long` = the curve at the log-mean of 10k and half (**14.5 km**,
  `long.current.ref_km`). All with the same schema (current / yearly / weekly, personal_best, stale fields). Their
  `race_equivalents_s` are the curve's per-distance times (= the race prediction), not the flat equivalents of one
  VDOT. `shape_at` of a running session = the curve at that session's distance on its day; `score_10k` = the score
  carried along that day's curve to 10 km (score − shape_at + the curve at 10 km), which is where the Fitness chart
  draws it against the run line.
- **Staleness** for run / interval / long: `last_evidence_date` = the latest race / hard item on the curve with weight
  > 0.5 (any distance: they share θ); `run` / `long` `current.last_distance_evidence_date` = the latest 10k+ race / tempo. The
  slope's sd grows towards its stationary 1.5 without long evidence, so the long band widens (adrian today: ± 2.1 vs
  ± 1.0 at 5 km).
- **Race prediction** (`overall.current.race_prediction`, schema kept): `vdot` / `sd` at 10k (sd = state ⊕ race-day
  noise `race_sd` 1.0), `race_equivalents_s` per distance from the curve (also `overall.current.race_equivalents_s`,
  Home's "Today 10k ≈ …"), `range_10k_s` ±1 sd; new: `vdot_by_distance`, `sd_by_distance`, `ranges_s`,
  `extrapolated`, `theta`, `slope`, `model: "distance_curve"`. `weights` is `{}` (the interval/long blend is gone).
- **Extrapolation:** outside the evidenced distance range (`evidence_km` = min / max km of race / hard items with
  weight > 0.5; adrian 5–21.1, patrik 5–14.5) the sd grows by `extrap_sd_per_doubling` = **1.0** VDOT per doubling
  and the distance is flagged `extrapolated` (adrian: marathon, 1.0 doubling; 3k, 0.74 below 5 km). The 1.0 is an
  assumption: there is no marathon evidence to fit it. The range is all-time, also for past dates.
- **No gain past the longest evidence:** beyond `evidence_km` max a positive slope stops, so the VDOT is held at its
  value there (adrian: marathon VDOT = half VDOT, 55.6 → 2:54:18 instead of 56.9 → 2:50:54). Carrying the slope further
  would predict a marathon above the half, optimistic without marathon training. A negative slope keeps applying
  (patrik's marathon still loses vs his 14.5 km level). The extrapolation sd is still added.
- **Overall** counts running once, as `run` % of best (model_version ≤ 2: the mean of interval and long %),
  precision-weighted with ski.

**Eval** (`scripts/research/shape_split_eval.py`, 2026-11-01, adrian, run with the random-walk slope of that day — q_s 0.001,
prior N(0, 3²); rerunning it now uses the mean-reverting `CURVE`; forward filter = data strictly before the race; forecast =
one-step-ahead of race / hard items, first 3 skipped; loglik with z clipped at 3). A = separate shapes (v1),
A-blend = v1 `race_prediction`, C = θ + gap (long obs = θ + gap), D = the distance curve.

| model | races MAE (7) | without soft 5k (6) | 10k / half races (4) | interval forecast MAE / loglik (124) | long forecast MAE / loglik (9) | today 5 km / long ref |
|---|---|---|---|---|---|---|
| A separate | 1.26 | 0.96 | 1.07 | 2.50 / −1.776 | 2.38 / −1.469 | 52.8 ± 1.0 / 58.4 ± 2.8 |
| A-blend (v1 race_prediction) | 2.04 | 1.40 | 2.06 | | | 53.5 ± 2.0 (one VDOT) |
| C q_gap 0.001 | 1.28 | 1.09 | 1.10 | 2.45 / −1.761 | 2.06 / −1.426 | 52.8 ± 1.0 / 55.6 ± 1.3 |
| C q_gap 0.005 | 1.23 | 1.06 | 1.03 | 2.46 / −1.762 | 2.16 / −1.379 | 52.8 ± 1.0 / 55.8 ± 1.8 |
| D q_s 0 | 1.16 | 0.73 | 0.91 | 2.47 / −1.765 | 2.04 / −1.461 | 52.8 ± 1.0 / 55.7 ± 1.2 |
| D q_s 0.0005 | 1.11 | 0.69 | 0.81 | 2.47 / −1.764 | 1.91 / −1.412 | 52.8 ± 1.0 / 55.8 ± 1.4 |
| **D q_s 0.001** | **1.12** | **0.73** | **0.84** | 2.47 / −1.764 | **1.90** / −1.394 | 52.8 ± 1.0 / 55.9 ± 1.5, s +2.0 ± 0.8 |
| D q_s 0.002 | 1.16 | 0.79 | 0.90 | 2.47 / −1.765 | 2.01 / −1.388 | 52.8 ± 1.0 / 56.0 ± 1.8 |
| D q_s 0.005 | 1.22 | 0.88 | 1.01 | 2.47 / −1.766 | 2.29 / −1.415 | 52.8 ± 1.0 / 56.2 ± 2.5 |
| D q_s 0.02 | 1.35 | 1.08 | 1.24 | 2.47 / −1.767 | 2.66 / −1.493 | 52.8 ± 1.0 / 56.7 ± 4.4 |
| D, interval at 7.07 km | 0.92 | 0.72 | 0.73 | 2.48 / −1.770 | 1.83 / −1.392 | 51.6 ± 1.1 / 55.5 ± 1.3 |
| D, tempo at 10k | 1.38 | 1.04 | 1.28 | 2.47 / −1.763 | 2.01 / −1.463 | |
| D, tempo at half | 1.21 | 0.79 | 0.99 | 2.47 / −1.766 | 2.06 / −1.401 | |
| D, tempo at window, +3.0 | 1.53 | 1.26 | 1.55 | 2.46 / −1.760 | 1.74 / −1.356 | |
| D, tempo at window, +1.0 (in-sample) | 1.12 | 0.72 | 0.83 | 2.48 / −1.767 | 1.52 / −1.217 | |

(Rows below the bold one: q_s 0.001, tempo at 14.5 km unless stated. Tempo variants change the tempo targets, so
their long forecast column isn't comparable with the others.) D ties or beats A and C on races, the 10k / half
races improve most (1.07 → 0.84), the long forecast improves (2.38 → 1.90), and interval stays as good (θ is the old
interval shape: today 52.8 ± 1.0 in both). patrik has no race to back-test; his single long forecast is 1.26 (D) vs
3.67 (A), today θ 48.7 ± 1.5, slope −0.8 ± 1.2.

### Slope shrinkage (`scripts/research/slope_prior_eval.py`, 2026-11-01)
adrian's random-walk slope was +2.0 per doubling; his 2025 races alone give +1.2 (least squares through 5k 56.3,
10k 57.1 / 59.4, half 58.8; a races-only curve: +2.5 with the soft 19:01 5k, +1.4 without). Grid: prior / stationary
sd × q_s, random walk (RW) vs mean-reverting (OU). Same out-of-sample metrics as above (forward race prediction from
data strictly before the race day, 7 races / 6 without the soft 5k / the 4 10k-half races; share of races within
±1 / ±2 predictive sd; one-step forecasts of the 9 long race / hard items; patrik has one long forecast and no race
to back-test):

| model | races MAE (7) | w/o soft 5k (6) | 10k / half (4) | ±1 / ±2 sd | long fc MAE / loglik (9) | patrik long fc (1) | adrian slope today |
|---|---|---|---|---|---|---|---|
| RW sd 3, q 0.001 (before) | 1.12 | 0.73 | 0.84 | 71 / 86 % | 1.90 / −1.394 | 1.26 | +1.96 ± 0.76 |
| RW sd 1, q 0.001 | 1.10 | 0.69 | 0.80 | 71 / 86 % | 1.98 / −1.426 | 2.15 | +1.92 ± 0.76 |
| RW sd 0.5, q 0.001 | 1.12 | 0.70 | 0.84 | 71 / 86 % | 2.01 / −1.440 | 2.43 | +1.90 ± 0.76 |
| RW sd 1, q 0.0003 | 1.23 | 0.78 | 1.03 | 71 / 86 % | 2.05 / −1.493 | 2.73 | +1.75 ± 0.54 |
| RW sd 1, q 0 | 1.36 | 0.86 | 1.25 | 71 / 86 % | 2.16 / −1.603 | 3.01 | +1.54 ± 0.37 |
| RW sd 0.5, q 0 | 1.59 | 1.00 | 1.65 | 43 / 71 % | 2.39 / −1.861 | 3.43 | +1.03 ± 0.32 |
| OU sd 1, q 0.003 | 1.29 | 0.81 | 1.12 | 71 / 86 % | 2.10 / −1.511 | 3.11 | +1.00 ± 0.86 |
| OU sd 1.5, q 0.003 | 1.13 | 0.70 | 0.84 | 71 / 86 % | 1.99 / −1.428 | 2.45 | +1.52 ± 1.01 |
| **OU sd 1.5, q 0.005** | **1.12** | **0.70** | **0.84** | 71 / 86 % | 2.01 / −1.422 | 2.48 | **+1.33 ± 1.18** |
| OU sd 1.5, q 0.01 | 1.16 | 0.73 | 0.90 | 71 / 86 % | 1.99 / −1.419 | 2.64 | +0.92 ± 1.37 |
| OU sd 2, q 0.003 | 1.14 | 0.74 | 0.86 | 71 / 86 % | 2.02 / −1.406 | 1.85 | +1.75 ± 1.08 |
| RW sd 1, q 0.001, interval σ ×1.5 | 1.11 | 0.80 | 0.84 | 71 / 100 % | 2.10 / −1.493 | 0.15 | +2.19 ± 0.78 |
| RW sd 1, q 0.001, interval σ ×2 | 1.11 | 0.80 | 0.87 | 71 / 100 % | 2.18 / −1.537 | 1.09 | +2.31 ± 0.80 |

- **A stronger prior alone does nothing** with a random-walk slope: the prior only applies at the first evidence
  (2018), and q_s adds its variance back over the years (sd 0.5 → 3 changes today's slope by 0.06).
- **Shrinking through a smaller q costs accuracy**: the 10k / half races get worse (0.84 → 1.03 at q 0.0003, 1.65 at
  q 0) and so does the long forecast, mostly because the 2025 half (58.8) followed two tempos at 59; at the time of
  the races every setting estimates a slope of ≈ +1.9. The races-only +1.2 is lower because there θ (the 5 km level)
  sits at the 5k race (56.3); the full model puts θ at 55.4 then (the interval sessions and the soft 5k pull it down).
- **Interval sessions with less weight** (σ × 1.5 / × 2) make the slope *larger* (+2.2 / +2.3) and the 6-race MAE
  worse: not used.
- **Chosen: mean-reverting slope, stationary sd 1.5, q_s 0.005** (`CURVE` `slope_revert`, `slope_prior_sd`,
  `q_slope`). OU makes the shrinkage hold at every date with no extra parameter (the prior sd becomes the stationary
  sd). It is within noise of the best on every adrian metric (races 1.12 vs 1.10, 10k / half 0.84 vs 0.80, long
  loglik −1.422 vs −1.394) and the most shrinking of the settings that are (q 0.01 starts to lose on the races;
  sd 1 loses 0.2 on the 10k / half races). Today's slope +1.33 ± 1.18 (it was +1.9 right after the autumn 2025
  races and has faded since: no 10k+ race in a year). patrik: his single long forecast goes 1.26 → 2.48 (any
  shrinkage does that: his slope moves −0.76 → −0.25; one item, so noise-level, but noted).
- **Raised 2026-11-05 to q_s 0.01, sd 1.75** together with the tests (above). The overnight shape-model research
  (research/shape-models/SUMMARY.md; variant "S" = q_s 0.04, sd 2.0) found faster slope adaptation neutral to slightly
  better on races; this is a smaller step. Research harness (time-forward, G11 fixed data; MAE VDOT, old → new incl.
  tests): official races adrian 0.91 → 0.86, rise 1.75 → 1.66 (adrian + rise 1.33 → 1.26); adrian's 3 tests as
  targets 1.83 → 1.61; interval tier 2.41 → 2.45 (expected: intervals are now read against a higher maximal level);
  hard_effort 4.16 → 4.16. Not significant (13 targets), so the choice rests on the mechanism.

### One run number
Since model_version 3 the app shows **one running number, "Run fitness"**: the personal distance curve read at
10 km, i.e. a 10k-equivalent VDOT (fitness.json `disciplines.run`). `run.current.mean` / `sd` are the 10k race
prediction's VDOT ± sd (state ⊕ race-day noise ⊕ extrapolation; = θ + s inside the evidenced range);
`run.current.sd_state` and the weekly band are the curve's state sd at 10 km. Race times per distance still come from
the curve at each distance (`race_equivalents_s`, Race predictor), so the one number costs no accuracy. Before, the
Fitness table showed Interval (5 km) and Long (~14.5 km) with identical 5k / 10k / half times, which was redundant.
The HR variant has `run` too (`variants.hr.disciplines.run`).

The per-person **"personal distance curve" on / off setting was removed** with it (`curve_off` in fitness.json,
`/api/<person>/settings`, `scripts/person_settings.py`, the Race predictor switch, `ref_flat` in form deltas). Off
meant the same VDOT (θ, the 5 km level) at every distance, and it is measurably worse. Out-of-sample on the official
races (forward filter: data strictly before race day, the pre-race prediction at the race distance; adrian + rise +
patrik pooled, 2026-11-04):

| prediction at the race distance | race MAE (VDOT) |
|---|---|
| **personal curve (production)** | **1.46** |
| flat at θ (curve off) | 2.37 |
| flat at the 10k level (θ + s at every distance) | 1.79 |
| shared slope +1.0 per doubling (no personal slope) | 1.54 |

With the curve off the halves came out 2–9 min too slow. A flat 10k-level number is better than flat θ but still
worse than reading the curve per distance, so the app shows the 10k level as the one number and keeps the curve for
every prediction. `curve_predict(..., flat=True)` is kept for these back-tests only.

The analysis table below (from the time the setting existed) shows what off did to the times:

2026-11-01, `GET /api/<person>/fitness/predict`, curve on / off:

| | 3k | 5k | 10k | half | marathon |
|---|---|---|---|---|---|
| adrian VDOT, curve on (+1.33 per doubling) | 51.9 ± 1.8* | 52.8 ± 1.4 | 54.2 ± 1.8 | 55.6 ± 2.8 | 55.6 ± 3.0* |
| adrian time, curve on | 11:11 | 19:01 | 38:35 | 1:23:29 | 2:54:18* |
| adrian time, curve off (52.8 everywhere) | 11:00* | 19:01 | 39:25 | 1:27:15 | 3:01:59* |
| patrik time, curve on (−0.25 per doubling) | 11:54* | 20:36 | 42:53 | 1:35:28* | 3:19:30* |
| patrik time, curve off (48.1 everywhere) | 11:56* | 20:36 | 42:42 | 1:34:35* | 3:16:51* |

\* extrapolated.

## User session corrections
`data/<person>/session_overrides.json` (format in [data-layout.md](data-layout.md)) reaches the model through
groups.json `override`: `fitness: exclude` → `score_session` returns no evidence; `fitness: bound` → its `hard` items
become `bound` (σ cont_bound / ski_bound; races stay races, `components.override = "bound"`). A `type` correction
changes the session type itself (e.g. easy → no interval score). patrik's 5 (2026-11-04 audit): 2026-09-10 easy run +
bike / vehicle tail (was intervals_road 60.4) excluded, 2025-06-28 run + 60 min walk → easy, 2024-04-14 3×1000 with
adrian on a ~1000 m road loop → intervals_road (was easy, unscored), 2025-05-15 and 2026-02-05 sub-maximal → bound.

## Track sessions and set distances (2026-11-04)
Before / after (track by rep location + `track_reps` + patrik's overrides; same code otherwise, HR settings
unchanged between the two runs):

| | adrian | rise | patrik |
|---|---|---|---|
| interval forecast MAE (n), `ev` one-step on the curve | 2.47 → **2.27** (123) | 2.71 → **2.62** (68) | 8.01 (9) → **2.72** (6) |
| — proposal only (overrides, no track change) | 2.47 | 2.71 | 2.26 |
| run races back-test VDOT MAE (n) | 0.94 → 1.09 (5) | 1.97 → **3.20** (5) | 10.86 → 10.86 (1) |
| Run fitness today | 53.73 → 54.10 | 51.00 → 50.39 | 47.16 → 46.81 |
| `calibration_eval` offset implied @ τ 0.35 (pooled −0.46 → +0.86) | −1.54 → +0.02 | +0.32 → +2.00 | −2.59 (1 pair) |

- Track interval scores drop ~1.5–2 VDOT for rise (grade adjustment on the flat oval and 400s / 1000s measured
  long by GPS were inflating them), ~0.5 for adrian's GPS-mode Bislett sessions; patrik's 2026-11-03 1000s go up.
- The shared `interval_offset` (−2.5) was calibrated on the inflated scores. With it unchanged, rise's
  intervals now sit ~2 below his races and the forward cross-check marks his 2025-08-22 tempo (51.0) implausible
  before Copenhagen Half 2025, whose pre-race prediction drops 48.5 → 43.5 (err −8.0): that one race makes the race
  back-test worse. With `interval_offset` −1.2 (what-if, not applied): races adrian 1.32 / rise 1.91, interval
  forecast 2.28 / 2.59. Resolved by the offsets re-fit in the next section (−0.5).
- Patrik's interval forecast (6 targets after the overrides) is noisy: 2.26 without the track change, 2.72 with it
  (2026-11-03 scored 47.0 instead of 44.9; 2026-10-06 Frogner 44.4 instead of 46.0).

## Øya, the Frogner loop, altitude levelling and the offsets re-fit (2026-11-04)
User decisions: Øya stadion (Trondheim, 400 m oval) is a track; the Frogner stadion loop is road with grade (reps
still at whole ~890 m laps); the Dubai track stays road; adrian's 6 runs of 2021 on patrik's watch are adrian's
(borrowed watch, below); rise's wrist trust stays at the estimated 0.57. Plus `level_alt` (above) for the barometer
artefact behind adrian's 2024-04-14 (61.2) and 2025-05-20 (53.4) jumps.

Metrics: run races = official races (data/races.json), forward prediction from data strictly before race day
(`fitness_eval.py --races`, `pre_race_prediction`), VDOT MAE; interval forecast = one-step-ahead forecast of the
distance curve for hard interval items (forward filter, first 3 skipped), MAE (n); road test-retest = SD from
consecutive hard road interval scores ≤ 14 days apart (pairs). patrik is validation only (1 race, 6 forecasts).

| step | races adrian (5) | races rise (5) | races patrik (1) | interval fc adrian | rise | patrik | road test-retest adrian / rise |
|---|---|---|---|---|---|---|---|
| as found (track detection + snapping, offsets −2.5 / +3.0) | 1.09 | 3.20 | 10.86 | 2.28 (122) | 2.62 (68) | 2.72 (6) | |
| + Øya track, Frogner = road loop, borrowed watch (features rebuilt) | 1.20 | 2.10 | 10.86 | 2.28 | 2.54 | 2.58 | 2.58 (16) / 3.34 (7) |
| + `level_alt` | 1.18 | 2.06 | 10.87 | 2.34 | 2.46 | 2.27 | 2.88 / 3.23 |
| + offsets −0.5 / +3.0 (**current**) | **0.91** | **1.75** | 10.87 | 2.34 | **2.42** | 2.33 | 2.88 / 3.23 |

- Frogner as road (grade counted on the hilly loop, road σ) takes rise's race MAE from 3.20 to 2.10 with the old
  offsets; adrian goes 1.09 → 1.20 in the same step (Øya sessions 2020–22 now flat track; not split further).
- `level_alt` is neutral on the pooled metrics (adrian's forecast +0.06, rise −0.08, patrik −0.31; races ±0.04;
  adrian's road test-retest 2.58 → 2.88 on 16 pairs, rise 3.34 → 3.23 on 7). Taken for the physical evidence
  (closed loops, standing still, the Frogner check above), not for the metrics. The `calibration_eval` rep-length
  zero crossing moves from τ 0.35 to ~0.30 pooled (adrian 0.32, rise 0.27); `tau_scale` left at 0.35.
- Jump sessions now: 2024-04-14 57.6 (flat-equivalent 53.5; was 61.2 with −2.5, i.e. +9.7 over flat, now +4.1);
  2025-05-20 51.5 (Frognerparken hill reps, really hilly: raw W′bal 55.9 → 52.0 vs 48.1 flat; was 53.4).
- Patrik's one race (10.9) is a validation outlier unaffected by any of this.

**Offsets re-fit** (grid on adrian + rise only; patrik reported, never minimised). Race MAE adrian / rise at tempo +3.0:

| interval_offset | −2.5 | −2.0 | −1.5 | −1.0 | −0.75 | −0.5 | −0.25 | 0.0 | +0.5 |
|---|---|---|---|---|---|---|---|---|---|
| adrian | 1.18 | 1.02 | 0.94 | 0.89 | 0.87 | **0.91** | 0.99 | 1.07 | 1.21 |
| rise | 2.06 | 1.97 | 1.87 | 1.81 | 1.78 | **1.75** | 1.72 | 1.69 | 1.63 |
| sum | 3.24 | 2.99 | 2.81 | 2.70 | 2.65 | **2.66** | 2.71 | 2.76 | 2.84 |
| mean error adrian / rise | −1.11 / −0.88 | | | −0.39 / −0.36 | −0.27 / −0.27 | **−0.15 / −0.18** | −0.03 / −0.09 | +0.09 / −0.01 | +0.33 / +0.17 |

- The interval forecast hardly moves with the offsets (adrian 2.33–2.35, rise 2.40–2.47, patrik 2.23–2.39 over the
  whole grid), so the races decide. The race sum is flat (within 0.06) from −1.0 to −0.25 and for tempo +2.5 … +3.5;
  −0.5 sits in the middle of that basin with both persons' mean error near 0. Tempo stays +3.0: +2.5 / +3.5 change
  the race MAE by ≤ 0.02; +4.0 is a cliff (at −2.5 the cross-check flips adrian: race MAE 2.23).
- Per race (−0.5 / +3.0): adrian 5k Øya 2021 +0.2, Lier 10k 2023 +1.0, Copenhagen Half 2025 −0.9, Bislett 10k 2025
  +0.7, Lier 10k 2025 −1.7; rise Bilbao Half 2024 +3.7, Copenhagen Half 2025 −2.6, Lier 10k 2025 −0.9, Oslo Half
  2026 +0.2, 10k 2026-10-23 −1.4. Rise's two halves (±3) dominate his MAE at every offset.
- `calibration_eval.py`'s in-sample rule (−median(session − race) within 6 weeks before / 2 after 5k / 10k races)
  still implies ≈ +1.0 pooled (adrian +0.05, rise +2.0); the forward back-test prefers −0.5. 10 races in total: the
  offset is uncertain by about ±0.5.

## Calibration set
Shared parameters (tau_scale, interval_offset, tempo_offset, SIG, distance-curve priors, HR / detection thresholds) are
chosen on the **calibration persons** only (`scripts/research/calibration_set.py`): ≥ 30 interval sessions with a trusted
distance and ≥ 2 running races (official in data/races.json or auto `race?` not in `not_races`) → weight 1, else 0
(validation only: reported, never minimised). `data/calibration.json` {"weights": {person: w}} overrides the rule.
2026-11-04: adrian (99 / 5) and rise (62 / 5) calibrate; patrik (11 / 1: few runs, 3–9 s smart recording, several
mislabelled sessions) is validation only. Personal quantities (shape, slope, easy pace, HR max / rest) always come from
the person's own data. `scripts/research/calibration_eval.py` pools tau / offset / tempo / forecast MAE over the calibration
persons with per-person and validation rows; `shape_split_eval.py` and `slope_prior_eval.py` default to the
calibration persons, then the validation persons (flagged VALIDATION ONLY).

## Evaluation (`scripts/fitness_eval.py`, numbers from 2026-10-31)
Data: 108 interval sessions (track 58, road 47 GPS), 7 races (5k ×3, 10k ×3, half ×1), 8 tempo, 189
long-run bounds, 15 hard + 100 other ski sessions. Evidence per year (non-bound): interval 4–8 per year
2019–2023, 16 in 2024, 40 in 2025, 28 in 2026. Long: 0–2 per year except 5 in 2025. Ski hard: 0–5 per year.

**Recovery τ (work:rest).** Interval sessions vs races within 6 weeks before / 2 weeks after (38 pairs),
residual slope vs log2(rep length) (rest-model bias), and one-step forecast error:

| tau_scale | sessions − race VDOT | slope per rep-length doubling | forecast MAE | track test-retest sd |
|---|---|---|---|---|
| 0.15 | +0.6 | +1.6 | 3.33 | 2.47 |
| 0.25 | +1.7 | +0.8 | 3.09 | 2.41 |
| **0.35** | +2.5 | **+0.3** | **3.03** | 2.35 |
| 0.5 | +3.6 | −0.3 | 3.08 | 2.27 |
| 1.0 (Skiba as published) | +5.7 | −1.3 | 3.63 | 3.07 |

- Skiba's cycling τ is too slow for these runs. Short-rep sessions (11×1:15) came out up to 6 VDOT
  above 1000s and above actual races.
- τ×0.35 removes the rep-length bias. That's consistent with faster W′ recovery in trained athletes
  (Bartram et al. 2018).
- The remaining +2.5 level offset becomes `interval_offset` = −2.5. It's in-sample: only 6 races, so the
  offset is uncertain by about ±1. Re-fit to −0.5 on 2026-11-04 (out-of-sample race back-test, "Offsets re-fit").

**Race prediction** (forward filter, only data before the race; per-discipline back-tests of the v1 separate
shapes, still what `fitness_eval.py` runs; the distance curve's eval is in the section above):

| model | races | mean error | MAE (VDOT) |
|---|---|---|---|
| interval shape → 5k / 10k | 6 | −0.4 | **1.86** (≈ 1:00 on 10k) |
| interval + HR effort adjustment (b = 10) | 6 | +0.7 | 2.37 |
| long shape → 10k / half | 4 | −0.3 | **1.03** |
| long + HR aerobic estimate (Swain) | 4 | −0.8 | 1.34 |

One 2025-10-24 Bislet 5k (19:01, VDOT 52.8) was run the same day as an 18:01 one. It's probably not
all-out. It accounts for +3.4 of the interval error.

**Running races back-test** (2026-11-04, after confirming races with the user: adrian 5 races, the Bislett 5ks
removed; rise 3 halves + 2 10ks; forward filter, data strictly before race day; times are watch times unless noted;
offsets −2.5 / +3.0, before the track changes and the re-fit: current per-race errors in "Offsets re-fit" above):

| person | race | predicted | actual | error |
|---|---|---|---|---|
| adrian | 5k Øya 2021 | 19:08 | 19:29 | −0:21 |
| adrian | Lier 10k 2023 | 38:56 | 39:08 | −0:12 |
| adrian | Copenhagen half 2025 | 1:20:51 | 1:19:33 | +1:18 |
| adrian | Bislett 10k 2025 | 36:43 | 36:54 | −0:11 |
| adrian | Lier 10k 2025 | 36:41 | 35:41 | +1:00 |
| rise | Bilbao half 2024 | 1:33:35 | 1:39:51 | −6:16 |
| rise | Copenhagen half 2025 | 1:34:02 | 1:29:12 | +4:50 |
| rise | Lier 10k 2025 | 40:49 | 40:09 | +0:40 |
| rise | Oslo half 2026 | 1:34:32 | 1:32:10 | +2:22 |
| rise | 10k 2026-10-23 | 40:24 | 39:50 (chip) | +0:34 |

MAE adrian 0.9 VDOT, rise 2.0 (10ks 0.9–1.0; halves 1.4–3.5, Bilbao was his first race with little data before
it). Before the halves were races they were scored as tempo (+3 offset, GAP on hilly courses: 55.1 / 54.5 vs race
VDOT 51.5 / 49.6), which pushed rise's slope to +2.7 and the pre-race 10k prediction to 38:55.

**Official races back-test** (`uv run scripts/fitness_eval.py --races`, 2026-11-04, data/races.json). Each race
predicted from data strictly before race day (forward filter). Ski: pre-race ski shape vs the race's own score;
minutes via time ∝ 1/(score − 3.5) on the same course (climb + friction power ∝ speed; drag ignored), so
1 VDOT ≈ 2.2 % of the time. Run: `pre_race_prediction` at the race distance vs the official time.

| race | adrian pred / score | err | ≈ min | rise pred / score | err | ≈ min |
|---|---|---|---|---|---|---|
| Holmenkollmarsjen 2025 (35 km) | 48.5 ± 2.5 / 51.6 | −3.0 | +9.5 | 44.2 ± 5.1 / 56.5 | −12.3 | +39.9 |
| Birkebeinerrennet 2025 (54 km) | 50.4 ± 1.8 / 48.1 | +2.3 | −11.8 | 53.5 ± 1.9 / 51.6 | +1.9 | −8.7 |
| Grenaderløpet 2026 (85 km) | 48.7 ± 3.1 / 41.7 | +7.0 | −64.0 | 50.3 ± 3.4 / 44.2 | +6.1 | −54.6 |
| Holmenkollmarsjen 2026 (52 km) | 45.9 ± 2.5 / 52.0 | −6.2 | +32.5 | 46.9 ± 2.4 / 50.9 | −4.0 | +20.4 |
| Vasaloppet 2026 (90 km) | 49.2 ± 1.7 / 50.1 | −0.9 | +7.4 | 49.2 ± 1.7 / 51.4 | −2.2 | +18.0 |
| Birkebeinerrennet 2026 (54 km) | 49.6 ± 1.5 / 51.4 | −1.8 | +8.6 | 50.2 ± 1.5 / 52.4 | −2.3 | +10.7 |
| 10k 2026-10-23 (run) | | | | 53.6 ± 1.7 / 52.2 | +1.4 | −0.9 (38:55 vs 39:50) |

(err = predicted − actual VDOT; ≈ min = predicted − official time, + = predicted too slow.)
- Ski MAE 4.2 VDOT over 12 race results (adrian 3.5, rise 4.8). Before the filter and races.json: 17.2 (adrian 3.8,
  rise 30.6, his glitch-inflated shape). Filter alone: 4.2. Races as `race` kind: 5.0.
- Grenaderløpet (6:53 / 7:01) scores ~7 low for both: a 7 h race's best 20 min is paced, not maximal. As `hard` it
  gets weight ≈ 0.05–0.09 in the published smoother, but the forward filter still pulls the next week's
  Holmenkollmarsjen prediction down (−6.2 / −4.0). Treating races > 5 h as bounds gave MAE 3.3 (not adopted:
  in-sample on 12 results, and Vasaloppet at 6 h scores normally).
- Rise's first race (HM 2025) had almost no ski evidence before it (shape sd 5.1).
- **Head-to-head** (official gap = adrian − rise; pre-race shape difference → gap ≈ T·Δshape/(shape − 3.5)): the
  pre-race shape picks the winner in 2 of 6 (Birken 2025, Birken 2026); the races' own scores in 4 of 6. Four of the
  six gaps are < 2 min (≈ 0.3–0.5 VDOT), below what the shape resolves (sd 1.5–3).

| race | shape diff (a − r) | predicted gap | race-score gap | official gap |
|---|---|---|---|---|
| Holmenkollmarsjen 2025 | +4.4 | adrian by 13:53 | rise by 13:14 | rise by 8:49 |
| Birkebeinerrennet 2025 | −3.1 | rise by 15:10 | rise by 17:47 | rise by 13:45 |
| Grenaderløpet 2026 | −1.6 | rise by 14:20 | rise by 26:37 | adrian by 8:26 |
| Holmenkollmarsjen 2026 | −1.1 | rise by 5:30 | adrian by 5:22 | adrian by 0:21 |
| Vasaloppet 2026 | −0.0 | rise by 0:15 | rise by 10:30 | adrian by 1:45 |
| Birkebeinerrennet 2026 | −0.6 | rise by 2:52 | rise by 4:56 | rise by 1:52 |

**Stability on repeated sessions.**
- Bislet 6×/5×1000 (17 sessions): test-retest sd **1.5 VDOT** (pairs ≤ 21 days apart). Medians are 56.9
  in 2024, 58.9 in 2025 and 54.6 in 2026 (raw, before the offset).
- All track intervals: 2.4. Road intervals: 2.4 (2.1 with GAP vs 2.5 without, so GAP helps once the
  window is ±45 s).
- Holmenkollen R3 (17 complete sessions): ski score sd 3.4. Correlation with log route speed
  r = **0.69**. The 2026 climbs are ~15 % faster (2,834–2,891 s moving vs 3,184–3,747 s) and score
  54–57 vs 45–53. Score noise is mostly FR935 altitude.
- Long-run bounds 4.4, ski bounds 4.6 (they mix intents).

## Variant "hr": performance + heart-rate intent
`fitness.json` holds two variants with the same per-session scores:
- **pace** (performance only: speed, altitude, distance, breaks): everything above. Stored in `disciplines` / `sessions`.
- **hr**: HR judges *intent*, meaning whether a session labelled hard was really maximal. Stored in `variants.hr.disciplines`
  and `sessions[].hr` = {kind, sigma, weight, shape_at, p_max, note, device, trust, flags?}.

HR never changes a score. The three ways it failed as a score input are in the next section. It only changes how much a hard
session counts (`apply_hr_intent`, parameters `P["hr_intent"]`):
- **Effort measure:** the work-HR p90 as %HRR for intervals; the HR over the last 2/3 of the scored window for long and ski.
- **p(maximal)** = Φ((hrr − thr)/0.04), with thr = 0.85 for interval and long, 0.82 for ski.
- **Device trust:** p_eff = trust·p + (1 − trust), with trust {interval, steady} of the activity's device (watch +
  wrist / strap) from `profile/hr_settings.json` (next section). adrian: 4315 trust 1; FR935 0.3 on intervals (erratic
  wrist HR in bursts), 0.5 on steady efforts. With trust ≤ 0.5, p_eff ≥ 0.5, so such a device's HR alone can never
  demote a session. Activities with implausible HR (flags) get trust 0: p_eff = 1, HR ignored.
- **%HRR** uses the person's own max HR (`max_hr`) and resting HR (30-day rolling median of Garmin's daily resting HR,
  else `rest_hr`). Before 2026-11-04 every person got adrian's 206 and his watch-change date (`HR_SETTINGS = False`
  restores that, for evaluation).
- **p_eff < 0.5:** the session becomes a lower bound (`bound`). Otherwise σ / √(0.5 + 0.5·p_eff), so a clearly maximal
  session counts fully and an uncertain one about half.
- Races aren't touched. Bounds are never promoted: easy and long runs with high HR still sit 8–25 VDOT below the shape,
  so high HR there doesn't mean a maximal effort.

Why only this: across all hard sessions, HR doesn't predict the interval residual (r = 0.12 on the 4315, −0.09 on the
FR935). It does on ski: `ski_hard` at ≥ 0.85 HRR sits on the shape, at 0.70–0.85 HRR it sits 4–5 VDOT below.

**Evaluation** (`fitness_eval.py` → `variants`). The same targets are used for both variants: races + sessions hard in both.
Forecasts are one step ahead, races are predicted with a forward filter, and R3 = correlation of the ski shape the day
*before* each Holmenkollen climb with that climb's log speed (independent of HR).

| | race MAE pace / hr | forecast MAE pace / hr | R3 corr pace / hr |
|---|---|---|---|
| interval (107, 6 races) | 1.80 / 1.82 | 2.90 / 2.90 | |
| long (9, 4 races) | 1.02 / 1.01 | 2.07 / 2.05 | |
| ski (11, 17 R3) | | 6.53 / 6.19 | 0.45 / **0.67** |

- **Demoted (2):** 2026-02-18 snow `ski_hard` (40.9 at 0.81 HRR, while the other 2026 ski sessions were 54–57)
  and 2026-07-23 road intervals (49.6 at 0.81 HRR on the 4315).
- **In-sample choice.** `demote_p` 0.5 and FR935 steady trust 0.5 were chosen from a 3×3 grid (`variants.sensitivity_hr`;
  now a floor on every device's steady trust, `hr_intent.steady_trust_min`, the same thing for adrian).
  The starting settings (0.3 / 0.8) were no better than pace (R3 0.40). The ski gain mostly comes from that one 2026-02-18
  session, so treat it as a hypothesis. Re-check as more 4315 sessions come in.
- Current shape is almost identical in both variants for adrian (2026-11-04: run 55.5 vs 55.4, ski 54.9 in both; the hr
  variant gets its own distance curve); rise's run is 50.8 pace vs 51.7 hr.

## Heart-rate settings per person and device
`scripts/hr_settings.py <person>` → `data/<person>/profile/hr_settings.json` (format: [data-layout.md](data-layout.md)).
Every run / ski FIT is scanned once (`cache/hr_scan.pkl`):
- **Device:** the recording watch (file_id product + serial) and the HR source: `strap` when device_info lists an
  external heart-rate sensor (ANT+ / BLE device type 120), else `wrist`. Keys like `fr935/wrist`, `4315/wrist`,
  `fenix3_hr/strap`. A device's `from`/`to` dates give the lookup for activities missing from the file.
- **Interval tracking** (`interval_track_corr`): on interval sessions, the best correlation of HR with the 30 s mean
  speed 0–40 s earlier. A sensor that follows the reps scores high; erratic wrist HR in bursts scores low.
- **Steady residual** (`steady_resid_sd_bpm`, diagnostics + flag): steady 3-min chunks (flat, speed CV ≤ 10 %, from
  10 min in); per run the median of HR − 20·speed (m/s) minus that of the device's other runs within ±90 days.
- **Cadence lock:** ≥ 50 % of moving HR samples within 3 bpm of the step cadence *and* HR following the cadence second
  by second (31 s high-pass correlation ≥ 0.3). Closeness alone isn't enough: at race effort HR ≈ cadence happens
  naturally (rise's tempo runs at 174 bpm / 175 spm, adrian's 2021-05-05 race at 192 / 191, high-pass correlation ≈ 0).
  No run in the current data meets both conditions.

Derived values:
- **trust** {interval, steady}: linear in the device's median interval tracking (≥ 5 interval sessions), through
  adrian's two anchors: FR935 (median 0.506) → 0.3 / 0.5, 4315 (0.86) → 1.0 / 1.0; clipped to 0.1–1. Fewer interval
  sessions: 0.5 / 0.5. One proxy for both kinds: the steady residual is too confounded by fitness and heat to rank devices.
- **flags** per activity (HR then ignored by the HR variant, by together's effort % and by session_groups' easy / steady
  split): `cadence_lock`; `hr_dropout` (> 25 % of moving time without HR); `hr_high_for_pace` (steady residual
  > max(15 bpm, 2.5 × the device's robust SD)).
- **max_hr** (one per person): per device the highest held-12 s run peak (unflagged runs) with ≥ 2 other sessions within
  3 bpm (`peak_hr`; a one-off spike doesn't count). The person's value is the highest `peak_hr` among devices with
  interval trust ≥ 0.5 and ≥ 10 runs with HR (a device that under-reads hard efforts can't set it, a spiky one needs
  confirmation); else the most trusted device's; else the watch's HR-zone max. Checked against 150–225 and the zone
  max (±10 bpm) → `max_from.check`. Runs only: wrist HR on skis spikes (adrian 2026-03-01: 218).
- **rest_hr**: median Garmin daily resting HR of the last 365 days, days worn ≥ 20 h.
- **manual**: single values in `manual` override the derived ones and survive rebuilds; `"source": "manual"` freezes the file.

Result (2026-11-04):

| person / device | runs+ski | from – to | interval tracking (n) | steady resid SD | trust int / steady | peak_hr | flags |
|---|---|---|---|---|---|---|---|
| adrian fr935/wrist | 554 | 2018-12 – 2026-02-18 | 0.506 (93) | 12.7 | 0.3 / 0.5 | 209 | 3 dropout, 2 high-for-pace |
| adrian 4315/wrist | 70 | 2026-02-18 – | 0.86 (33) | 9.2 | 1.0 / 1.0 | 207 | 1 dropout |
| adrian 3077/wrist (borrowed from patrik) | 6 | 2021-03 – 2021-09 | – | – | 0.42 / 0.58 (patrik's, same watch) | 193 | – |
| rise fenix3_hr/wrist | 225 | 2019-08 – | 0.643 (58) | 16.6 | 0.57 / 0.69 | 189 | 5 dropout, 2 high-for-pace |
| rise fenix3_hr/strap | 45 | 2024-10 – | 0.778 (15) | 7.4 | 0.84 / 0.88 | 186 | – |
| patrik 3077/wrist | 96 | 2021-01 – | 0.565 (12) | 12.7 | 0.42 / 0.58 | 208 | 1 dropout |

Person values: adrian max 207 (4315; zones 206; the old auto value 214 was a one-off FR935 peak), rest 48; rise max
189 (wrist; zones 192; old auto 200 came from a treadmill run now flagged high-for-pace), rest 56; patrik max 208
(his only device has trust 0.42 < 0.5, so "low-trust device"; zones 207), rest 54 (131 full-wear days).
**Borrowed watch.** adrian's 3077 entries (6 runs 2021-03-30 – 2021-09-03) carry the serial of patrik's watch
(3350348757): adrian's runs on a borrowed watch (user decision 2026-11-04). They stay in adrian's data and model (patrik
has no run on those days). `mark_borrowed` marks a device whose watch serial is on another person's device with the
same key and more sessions: `borrowed_from: "patrik"`; with < 5 interval sessions of its own it takes the owner's
measured trust for that watch (0.42 / 0.58: same optical sensor) instead of the 0.5 default, and it is never the
by-date fallback (`device_on`: dates in 2021 without an activity entry stay on the FR935). max HR is unaffected
(5 runs with HR < 10). Builds read the other persons' hr_settings.json, so rebuild the owner first when a new
borrowed watch shows up. rise's wrist trust (0.57) is the estimate; nothing caps it at 0.5.

**Before / after** (HR variant; `HR_SETTINGS` False = adrian's 206 and watch date for everyone; same targets for all three
rows = races + items hard in pace, old-hr and new-hr):

| person | discipline | race MAE pace / hr old / hr new | forecast MAE pace / hr old / hr new | R3 corr | demoted old → new |
|---|---|---|---|---|---|
| adrian | interval (125, 4 races) | 1.62 / 1.66 / 1.66 | 2.47 / 2.47 / 2.47 | | 2 → 2 |
| adrian | long (9, 4 races) | 1.02 / 1.01 / 1.01 | 2.07 / 2.05 / 2.05 | | |
| adrian | ski (17) | | 5.13 / 5.07 / 5.06 | 0.734 / 0.743 / 0.743 | |
| rise | interval (15, 2 races) | 2.73 / 2.84 / 2.19 | 2.56 / 2.59 / 2.48 | | 28 → 43 |
| rise | long (4, 5 races) | 2.47 / 2.54 / 2.48 | 1.81 / 1.89 / 1.82 | | |
| rise | ski (6) | | 3.76 / 3.82 / 3.76 | | |
| patrik | interval (9, 0 races) | – | 7.45 / 7.73 / 7.10 | | 1 → 0 |
| patrik | long (1) | – | 3.06 / 2.92 / 2.83 | | |

adrian is unchanged by construction (trust anchored on his watches, max 207 vs 206). rise and patrik improve slightly,
but on very few targets (2 races, 1–15 forecasts): not evidence yet, only "no harm".

## HR as a score input: rejected (evidence)
Three ways HR could help were tested. All three made predictions worse or added noise:
1. **HR as the final effort adjustment of interval scores**, y + b·(0.9 − work %HRR):
   - The forecast MAE gets worse as b grows: 3.03 (b = 0), 3.21 (5), 3.48 (10), 4.28 (20).
   - Race MAE goes from 1.86 to 2.37. FR935 test-retest goes from 2.56 to 3.12.
   - Work-HR effort doesn't explain session-to-session residuals (r = −0.04, n = 89).
   - Cause: on the **same session type** (Bislet 1000s) the FR935 shows a work %HRR median of 0.77 with
     p10–p90 0.65–0.92. The 4315 shows 0.94 (0.93–0.96). The wrist sensor under-reads hard intervals
     erratically, and the watch change alone shifts HR by ~+0.17 HRR.
   - New watch only (n = 23): b = 10–20 cuts test-retest from 2.42 to 2.1–2.2, with forecast MAE
     unchanged (1.66 → 1.63). That's promising but too small to act on. Re-run the eval at ≥ 50 sessions
     on the new watch. If b > 0 improves both metrics there, enable it for that watch only.
2. **HR-only aerobic estimate** (Firstbeat / Swain style: VO2max = 3.5 + (VO2(GAP speed) − 3.5) / %HRR on
   steady 3-min chunks of easy/long/tempo runs):
   - Test-retest sd 4.7 VDOT, 3× the Bislet 1000s.
   - Biased by intensity: 50.8 at 0.6–0.7 HRR vs 45.1 at 0.8–0.9.
   - Yearly medians miss the real trend: 46.4 in 2025, the best race year.
   - Quarterly correlation with the pace-based shape r = 0.12 (n = 22).
   - 4–12 VDOT below race VDOT in the 6 weeks before the 2025 races.
   - Adding it to long made race prediction worse (MAE 1.03 → 1.34).
3. **HR plausibility gate** for implausibly fast sessions: wrist HR spikes made the gate unreliable
   (the bogus 2026-01-20 run still had p90 HR 0.85 HRR). The cross-discipline check (above) does the job
   without HR.

So HR is reported as **diagnostics only**: `components.work_hrr` / `hrr` per session, and
`hr_diagnostics` (efficiency factor = GAP m/s per beat, aerobic decoupling, Swain estimate) per session.
`hr_used_in_score: false` for the pace variant (`variants.hr.hr_used_in_score: true`).

## fitness.json
```
{built, model_version (3), params (P), curve (CURVE), sigma (SIG), scale, hr_used_in_score,
 disciplines: {
   run|interval|long|ski: {label, unit, q_per_day, model (distance_curve | random_walk), personal_best,
     run only: ref_km 10; interval / long: legacy true (read-outs of the run curve, not shown in the app),
     current: {date, mean, sd, confidence (high <1 / medium <2 / low, from the state sd), pct_of_best,
               race_equivalents_s {5k, 10k, half, marathon} | null for ski,
               last_evidence_date, days_since_evidence, stale (> 90 days),
               run only: mean / sd = the 10k race prediction's vdot / sd, sd_state (curve sd at 10 km), ref_km 10;
               run / long: last_distance_evidence_date; long: ref_km},
     yearly: {YYYY: {mean, max, sd_median, n_evidence, mean_well_observed (days with sd<2), days_well_observed}},
     weekly: [{date, mean, sd, n (non-bound evidence that week), days_since_evidence}]},
   overall: {label, unit, current {date, mean, sd, race_prediction {vdot (10k), sd, weights {}, model, theta, slope,
             vdot_by_distance, sd_by_distance, extrapolated, race_equivalents_s, ranges_s, range_10k_s [fast, slow]}
             | null, race_equivalents_s (= the prediction's)},
             yearly {YYYY: mean}, weekly [{date, mean, sd}]}},
 variants: {pace: {label, hr_used_in_score}, hr: {label, hr_used_in_score, disciplines (same shape as above)}},
 predictor: {ref_km 5, x "log2(km/5)", q {theta, slope}, slope_prior_sd, slope_revert, race_sd, evidence_km [min, max],
             extrap_sd_per_doubling, x_mapping, long_ref_km, last_evidence_date,
             daily [{date, theta, slope, var_theta, var_slope, cov}] (RTS smoothed, first evidence → today),
             evidence [{date, id, km, vdot, kind, weight, discipline, sigma}] (race / hard + bounds with weight ≥ 0.05)} | null,
 races: [{name, date, sport (run|ski), km, official_s, official, activity_id (null = no Garmin activity), source (chip|watch),
          run only: predicted_s, predicted_range_s [fast, slow] (±1 sd), predicted_vdot, predicted_sd, actual_vdot,
          pct ((predicted − official) / predicted · 100, > 0 = faster than predicted), delta_s (official − predicted)}]
        (the person's data/races.json results, oldest first; see "Races and the pre-race prediction" below),
 sessions: [{id, date, discipline, type, score, kind (race|hard|bound|implausible), sigma, km (running: distance on the curve),
             weight (robust weight 0..1 the smoother gave it), shape_at (smoothed value that day; running: the curve at km),
             score_10k (running: score − shape_at + the curve at 10 km that day),
             confidence (evidence strength relative to a race = 1), components {...},
             hr {kind, sigma, weight, shape_at, p_max, note, device, trust {interval, steady}, flags?}}],
 hr_diagnostics: {activity id: {n_chunks, vdot, iqr, hrr, ef, decoupling_pct}},
 hr_settings: {source ("hr_settings.json" | "legacy"), max_hr, rest_hr, devices {key: {from, to, trust}}}}
```
Components:
- interval: sig, n_reps, rep_s, rest_s, work_rest, rep_speed, tau_scale, score_without_grade, work_hrr.
- long: window_s, gap_speed, hrr.
- race: race, time_s (+ official, race_km for data/races.json races; test, race_km for data/races.json `tests`).
- ski: window_s, climb_m, speed, demand, surface, hrr (+ official, official_s, race_km; implausible = reason).

**Races and the pre-race prediction** (`races`, `official_race_results(person, ev)`). Every build lists the person's
data/races.json results. Run races get the prediction the app would have given the evening before:
`pre_race_prediction(ev, date, km)` fits the distance-curve model on the evidence strictly before race day (the race
and everything after it left out), reads the curve on the day before at the race distance, and converts it to a time (`race_time`). This is the same forward filter as the
official-races back-test (`fitness_eval.py --races` calls it too), so the numbers match the table above. `pct` > 0 =
faster than predicted. Ski races carry results only: the ski model is too coarse for a race-time prediction. `source`
is "watch" when the results' note says the time is a watch time, else "chip". Served by `/api/races` and
`/api/community/races` (docs/app.md).

## API for a backend
`build(person, workers=8, today=None) -> doc` is the whole file. Lower level:
- `load_features(person)` → (acts, feats, rest). Cached.
- `apply_hr_intent(ev)` → the hr variant's evidence (copies of the pace evidence).
- `collect_evidence(acts, feats, tau=, hr_b=, use_hr_aer=)` and `score_session(act, feat)` → evidence items.
- `smooth_series(obs, day0, day1, q)` → (days, mean, sd, weights, forecasts). `forecast_errors(obs, q)`.
- Distance curve: `smooth_curve(obs, day0, day1, q_theta=, q_slope=, slope_sd=, revert=)` with obs (day, score, sigma,
  kind, x) → (days, mean (n, 2), cov (n, 2, 2), weights, forecasts); `curve_at(mean, cov, x)`; `curve_predict(theta,
  slope, var_theta, var_slope, cov, km, evidence_km, flat=False)` → {vdot, sd_state, sd, extrapolated, …} (flat: the
  distance curve off, slope 0); `evidence_km(item)`,
  `curve_x(km)`, `curve_obs(ev)`, `slope_text`, `dist_label`; parameters `CURVE`.
- Physiology helpers: `vdot_from_race`, `vdot_from_perf`, `race_time`, `speed_for`, `daniels_vo2`, `daniels_pct`,
  `cs_dprime`.
- Model pieces: `gap_factor`, `ski_vo2_demand`, `wbal_min`, `wbal_vdot`, `continuous_vdot`, `ski_effort`,
  `hr_aerobic`.
- Per FIT: `session_series(fit, act)` and `session_features(fit, act, hr_rest, hr_max=None)`. HR per activity: `hr_of(person, id, date)` (hr_settings.json, or the legacy constants).
- `pre_race_prediction(ev, date, km)` → {vdot, sd, time_s, range_s} from data before `date`;
  `official_race_results(person, ev)` → fitness.json `races`.
- `fitness_eval.run(person)` → the eval dict (its `variants` are served as `eval_variants` in `GET /api/fitness`).

## Shape trend (adrian, 2026-11-04, model_version 3, offsets −0.5 / +3.0)
| | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 | now |
|---|---|---|---|---|---|---|---|---|---|
| Run (VDOT at 10 km, yearly mean) | 49.8 | 52.4 | 49.9 | 49.2 | 54.0 | 55.4 | 56.1 (peak 58.4) | 56.0 | 55.5 ± 1.8 (10k ≈ 37:50, 95.0 % of best; slope +0.8 per doubling, no 10k+ evidence since 2025-11-02) |
| Ski (VDOT-eq.) | 39.6* | 41.5* | 43.5* | 46.3* | 49.1* | 49.9 | 50.0 | 53.2 | 54.9 ± 2.7 |
| Overall (% of best) | 81.5 | 86.1 | 84.0 | 84.2 | 92.0 | 93.5 | 94.8 | 96.1 | 96.0 ± 2.3 |

\* 0–2 pieces of evidence that year. The value is interpolated between evidence and the band is wide.
Check `n_evidence` / `days_well_observed`. Run evidence per year: 5–9 before 2024, 18 in 2024, 48 in 2025, 39 in 2026.

## Limitations / next steps
- **Intent is unknown.** A threshold session scores lower than a VO2max session at the same fitness. Confirmed all-out
  tests (data/races.json `tests`) are the fix for that where they exist; log future 3k / 5k tests there. Overnight
  research on the shape model (30+ variants, none clearly better than production; open suggestions such as shadow-running
  variant T1 for rise / patrik): research/shape-models/SUMMARY.md.
  The robust lower tail absorbs some of this, and residuals of hard sessions have median −0.4, sd 2.1.
  adrian's 2026 interval scores are lower because the Bislet 1000s are slower (3:22–3:33/km in 2026 vs 3:09–3:20 in autumn 2025, similar rests). The
  model can't tell "less fit" from "ran them as threshold".
- **Long evidence is sparse:** adrian has 0–2 tempos/races per year before 2025, none in 2026. The distance curve lets the
  intervals carry the level (θ), but the slope (how much better at long races) is only learned from 10k+ races and
  tempos (and tests); without them it fades towards 0 (mean-reverting, ≈ 600-day time constant) with a growing sd. Before 2025
  the slope is mostly last years' slope smoothed backwards (RTS), shrunk towards 0 further back.
- **Distance curve:** linear in log-distance is an assumption; the marathon (and anything > 21.1 km or < 5 km) is
  extrapolated with an assumed +1.0 VDOT sd per doubling, and a positive slope is held flat past the longest evidence
  (adrian's marathon = half VDOT, 2:54:18). Whether the marathon really holds that level is untested. Tempo / interval
  distances inherit the in-sample offsets.
- **Ski:**
  - μ and η are assumed, so the absolute level is uncertain by ±5–10 %. Snow `ski_hard` sessions sit
    8.8 below the shape (roller: −1.0), but snow easy sessions don't. That's not a stable enough signal
    for a surface offset.
  - Classic vs skate isn't in the files.
  - The FR935 barometer is the main noise source.
  - A route-reference altitude profile (from `compare_route`) would remove most of the noise on R3.
- No heat, wind or snow-condition correction. Treadmill sessions aren't scored.
- The calibration offsets (interval −0.5 since 2026-11-04, tempo +3.0) rest on 10 official races (adrian 5, rise 5)
  in a forward back-test; flat basin −1.0 … −0.25, so ±0.5. The in-sample window rule of `calibration_eval.py` implies
  ≈ +1.0.
- Known tracks: Bislett and Øya stadion (400 m ovals, flat) and the Frogner stadion loop (road with grade, reps at
  whole laps). The Dubai track and any other track count only in the watch's track mode.
- `level_alt` can't remove a barometer artefact that repeats at the same place on every lap (2024-04-14 keeps +4.1
  over flat; patrik's watch says ~+1). A DEM / route-reference altitude would.
- Mislabelled sessions are caught only when they're far too fast. A ski session saved as an easy run
  stays a harmless bound. Ski glitches are caught only by the speed / vertical-rate limits, not by a score check.
- Official ski races: n = 6 per person (12 results). Too few to fit anything; the ski race σ (2.0) and kind are design
  choices, not fitted.

## Form delta (`scripts/form_delta.py`)
"How well did this session go compared to the person's form": **delta = session score − shape on the day before,
from the shape model refitted without this session** (VDOT points; "+2.1 vs form" / "−1.3 vs form"). Used by the
Community pages.
```
uv run scripts/form_delta.py adrian <activity_id>   # one session (JSON)
uv run scripts/form_delta.py adrian --series        # weekly shape trend
uv run scripts/form_delta.py --together             # every participant of every group in data/together.json
```
```python
import form_delta as fd                 # scripts/ on sys.path
fd.form_delta(person, activity_id)      # -> dict | None
fd.form_deltas(person)                  # {activity id: dict} for every session with one
fd.form_series(person, since=None)      # [{date, run, interval, long, ski, overall, sd: {disc: sd}}], weekly, oldest first
fd.together_deltas()                    # together.json groups with a `form` per participant
```
`form_delta` → `{score, shape, delta, discipline, kind, confidence, text, date, ref_sd, z, weight, flags, basis}`.
- **Score:** the session's own score from `fitness.json` (race > hard item; then smaller σ, then interval). A 10k race
  uses its long/race item, a 5k its interval/race item. **None** for easy/long runs (bounds: only lower bounds, not a
  performance), hikes, rides and anything the model doesn't score (treadmill, multi_sport, implausible).
- **Reference:** `smooth_series` (same q, σ and robust weights as `fitness.json`) on every *other* evidence item of
  that discipline, read on the day before. Running (interval + long, fitness.json with `predictor` and
  `sessions[].km`): `smooth_curve` on every other running item, read on the day before **at the session's own
  distance** (`_curve_refs`; `CODE_VERSION` 6; same `CURVE` incl. the mean-reverting slope). (The flat θ reference
  of the removed distance-curve-off setting, `ref_flat`, is gone: [One run number](#one-run-number).) Long deltas therefore compare with the curve, which intervals inform:
  adrian's 2021 tempos are −7.5 because the intervals said he was fitter than those tempos. Leaving the session out stops it pulling its own reference (a good session
  otherwise looks smaller: `score − shape_at` under-states it). Chosen over the forward filter (only earlier
  sessions): with sparse data the forward filter swings after each odd session (patrik's 2026-09-10 glitch, before its override, raised
  his forward shape from 41 to 52, so the next three sessions looked 3–6 points bad) and it fits worse: median
  |delta| of hard sessions 2.1 (forward) vs 1.8 (leave-one-out) for adrian's intervals, 6.8 vs 5.4 for patrik's.
  Each other session enters the reference with the shape model's robust weight (`sigma / √weight`), so a session the
  model rejected (then patrik 2026-09-10, 60.4, weight < 0.5) doesn't inflate the reference (unweighted it pushed patrik's
  October reference to 57 → deltas −8 to −11). Cost: later sessions inform the reference, so an old delta can shift slightly when `fitness.json` is rebuilt.
- **Confidence** from the combined sd √(ref_sd² + σ²): high < 2, medium < 3, else low. **z** = delta / that sd.
- **Flags:** `hr_not_maximal` (the HR-intent variant demoted it), `downweighted` (model weight < 0.5),
  `sparse_reference` (ref_sd > 3), `outlier` (|z| > 3: confidence low), `no_reference` (the discipline's only
  race/hard item: shape/delta None, text "no earlier form to compare"). `outlier` + `downweighted` → confidence
  `none`, text "score unreliable" (delta still returned).
- **Cache:** reads only `fitness.json`; one refit per race/hard session (~1.5 s for adrian cold), cached in memory
  and in `data/<person>/cache/form_delta.json`, keyed by `fitness.json` mtime + size + `CODE_VERSION`.

Typical spread (2026-11-01, distance curve): adrian intervals median −0.7, p10–p90 −4.9…+2.0 (127); long −0.2,
p10–p90 −7.1…+2.2 (12); ski −2.6, p10 −12.6 (15, low confidence). patrik intervals median −3.4, p10 −11.5 (11);
long −0.1 (4).

**Limits.**
- It measures the session, not the day's fitness. Hard sessions are often not all-out, so deltas lean negative.
  Threshold/long-rep sessions score below VO2max sessions (adrian 3×1800 + 2×900: −2.5). A partial session scores
  only what that person ran: fewer reps mean a lower W′ demand and so a lower score (patrik's 10 of 15 × 400 still
  came out +2.9 because his 400s were fast).
- Noise: |delta| < ~2 (adrian) is within test-retest (track 1.5–2.4, road 2.4). Ski deltas carry the ski score noise
  (sd 3.4 on R3, snow hard sessions ~9 below): always low confidence.
- The score is only as good as the session label. patrik 2026-10-27 (a steady run split at ~10 s stops) is classed
  tempo by session_groups `recovery_share` (as intervals it scored −5.9): long evidence 47.1. Labels the rules get
  wrong need a user correction (`session_overrides.json`, "User session corrections" above): patrik 2026-09-10 (reps
  at 2:47–3:27 /km with HR 115–158, not on foot) was `intervals_road` 60.4, "score unreliable", and pulled his
  with-HR interval shape to 53.9; it is now easy + excluded. patrik's watch records every 3–9 s, which blurs short reps.
- No HR fallback for easy runs: the HR-aerobic estimate failed as a score (test-retest 4.7 VDOT, above), and the
  efficiency factor exists for only 36 of adrian's easy runs and 1 of patrik's, rounded to 3 decimals (≈ 5 % steps).
- HR settings per person and device since 2026-11-04 (above). Device trust comes from one proxy (how HR follows the
  reps) calibrated on adrian's two watches; rise's wrist sensor lands above 0.5 and so can demote sessions on its own
  (43 of rise's hard items are bounds in the HR variant, 28 before). patrik's HR is high for his pace on every run
  (median ~178 at 5:33 /km vs adrian's ~145) but consistent, without cadence lock: read as his own physiology, not flagged.

# Fitness / shape model (`scripts/fitness_model.py`)

`uv run scripts/fitness_model.py adrian [--eval]` → `data/adrian/fitness.json` (+ `fitness_eval.json` with
`--eval`, or run `scripts/fitness_eval.py adrian` on its own). Input is `groups.json` (run
`session_groups.py` first) plus the FIT files. Per-session features are cached in
`data/adrian/cache/fitness_model.pkl`, keyed by FIT mtime+size, the resting HR used and all parameters
in `P`. Timing: about 9 s cold (8 workers), 0.1 s cached. The eval takes 0.8 s.

## What it gives
Separate shape scores over time: **interval** (short/fast running), **long** (long-distance running) and
**ski** (rollerski + snow). There is also an **overall** score across disciplines. Each session becomes
0..n *evidence* items. A robust random-walk smoother turns them into a daily curve with an uncertainty
band.

**Scale: Daniels VDOT** ("running VO2max as shown by performance", ml/kg/min). Why:
- One number maps to equivalent race times (`race_time(vdot, dist)`). VDOT 55 = 5k 18:22, 10k 38:06,
  half 1:24:16. VDOT 60 = 17:03 / 35:22 / 1:18:09.
- The %VO2max-vs-duration curve handles effort length, which plain pace or Riegel can't.
- Ski is put on the same ml/kg/min scale through physics, so the numbers are comparable in kind. The
  absolute ski level depends on an assumed efficiency (see limitations). The trend is what matters.
- **Overall** = % of personal best per discipline, precision-weighted (1/sd²) across disciplines. Ski
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
- Track, treadmill and Bislet runs count as flat.
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
- Then `interval_offset` = −2.5 is added (calibration below).
- Synthetic 6×1000 @ 3:15 with standing rest scores 57.2 / 55.8 / 52.9 / 51.3 for 60 / 90 / 180 / 300 s
  rest. This is the work:rest effect: the same pace on shorter rest is worth more.
- Only `intervals_track` / `intervals_road` sessions get an interval score. Treadmill sessions are
  excluded because their distance is untrusted.

**Races** (`race?` within 3.5 % of 5k / 10k / half / marathon): Daniels VDOT from the nominal distance
and moving time.
- ≤ 7 km counts for interval, with kind `race`.
- Longer races count for long (`race`). A 10k also counts for interval, as `hard` with σ 2.

**Long: continuous efforts.** Daniels VDOT of the best grade-adjusted window of 20 / 30 / 45 / 60 / 90 min
inside one pause-free leg.
- Tempo runs are `hard` and get `tempo_offset` = +3.0. Tempo is run below race effort: the median
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
- `ski_hard` sessions are `hard`, the rest are `bound`.

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
  offset is uncertain by about ±1.

**Race prediction** (forward filter, only data before the race):

| model | races | mean error | MAE (VDOT) |
|---|---|---|---|
| interval shape → 5k / 10k | 6 | −0.4 | **1.86** (≈ 1:00 on 10k) |
| interval + HR effort adjustment (b = 10) | 6 | +0.7 | 2.37 |
| long shape → 10k / half | 4 | −0.3 | **1.03** |
| long + HR aerobic estimate (Swain) | 4 | −0.8 | 1.34 |

One 2025-10-24 Bislet 5k (19:01, VDOT 52.8) was run the same day as an 18:01 one. It's probably not
all-out. It accounts for +3.4 of the interval error.

**Stability on repeated sessions.**
- Bislet 6×/5×1000 (17 sessions): test-retest sd **1.5 VDOT** (pairs ≤ 21 days apart). Medians are 56.9
  in 2024, 58.9 in 2025 and 54.6 in 2026 (raw, before the offset).
- All track intervals: 2.4. Road intervals: 2.4 (2.1 with GAP vs 2.5 without, so GAP helps once the
  window is ±45 s).
- Holmenkollen R3 (17 complete sessions): ski score sd 3.4. Correlation with log route speed
  r = **0.69**. The 2026 climbs are ~15 % faster (2,834–2,891 s moving vs 3,184–3,747 s) and score
  54–57 vs 45–53. Score noise is mostly FR935 altitude.
- Long-run bounds 4.4, ski bounds 4.6 (they mix intents).

## HR decision: not used in the score (evidence)
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
`hr_used_in_score: false` in the doc.

## fitness.json
```
{built, model_version, params (P), sigma (SIG), scale, hr_used_in_score,
 disciplines: {
   interval|long|ski: {label, unit, q_per_day, personal_best,
     current: {date, mean, sd, confidence (high <1 / medium <2 / low), pct_of_best,
               race_equivalents_s {5k, 10k, half, marathon} | null for ski},
     yearly: {YYYY: {mean, max, sd_median, n_evidence, mean_well_observed (days with sd<2), days_well_observed}},
     weekly: [{date, mean, sd, n (non-bound evidence that week), days_since_evidence}]},
   overall: {label, unit, current {date, mean, sd}, yearly {YYYY: mean}, weekly [{date, mean, sd}]}},
 sessions: [{id, date, discipline, type, score, kind (race|hard|bound|implausible), sigma,
             weight (robust weight 0..1 the smoother gave it), shape_at (smoothed value that day),
             confidence (evidence strength relative to a race = 1), components {...}}],
 hr_diagnostics: {activity id: {n_chunks, vdot, iqr, hrr, ef, decoupling_pct}}}
```
Components:
- interval: sig, n_reps, rep_s, rest_s, work_rest, rep_speed, tau_scale, score_without_grade, work_hrr.
- long: window_s, gap_speed, hrr.
- race: race, time_s.
- ski: window_s, climb_m, speed, demand, surface, hrr.

## API for a backend
`build(person, workers=8, today=None) -> doc` is the whole file. Lower level:
- `load_features(person)` → (acts, feats, rest). Cached.
- `collect_evidence(acts, feats, tau=, hr_b=, use_hr_aer=)` and `score_session(act, feat)` → evidence items.
- `smooth_series(obs, day0, day1, q)` → (days, mean, sd, weights, forecasts). `forecast_errors(obs, q)`.
- Physiology helpers: `vdot_from_race`, `race_time`, `speed_for`, `daniels_vo2`, `daniels_pct`, `cs_dprime`,
  `vdot_from_cs`.
- Model pieces: `gap_factor`, `ski_vo2_demand`, `wbal_min`, `wbal_vdot`, `continuous_vdot`, `ski_effort`,
  `hr_aerobic`.
- Per FIT: `session_series(fit, act)` and `session_features(fit, act, hr_rest)`.
- `fitness_eval.run(person)` → the eval dict.

## Shape trend (2026-10-31)
| | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 | now |
|---|---|---|---|---|---|---|---|---|---|
| Interval (VDOT, yearly mean) | 47.7 | 50.9 | 51.4 | 49.9 | 52.3 | 54.2 | 54.9 (peak 56.6) | 54.0 | 53.0 ± 1.0 (5k ≈ 18:58, 93.5 % of best) |
| Long (VDOT) | 50.5 | 49.1* | 47.5 | 49.5 | 53.2 | 55.1 | 57.8 (peak 58.5) | 58.4* | 58.4 ± 2.8 (no long evidence in 2026) |
| Ski (VDOT-eq.) | 39.6* | 41.5* | 43.5* | 46.3* | 49.1* | 49.9 | 50.4 | 53.9 | 55.3 ± 2.8 |
| Overall (% of best) | 81.9 | 85.9 | 85.3 | 86.0 | 91.3 | 94.2 | 96.9 | 96.1 | 94.8 ± 1.5 |

\* 0–2 pieces of evidence that year. The value is interpolated between evidence and the band is wide.
Check `n_evidence` / `days_well_observed`.

## Limitations / next steps
- **Intent is unknown.** A threshold session scores lower than a VO2max session at the same fitness.
  The robust lower tail absorbs some of this, and residuals of hard sessions have median −0.4, sd 2.1.
  Interval shape in 2026 is lower because the Bislet 1000s are slower (3:22–3:33/km in 2026 vs 3:09–3:20 in autumn 2025, similar rests). The
  model can't tell "less fit" from "ran them as threshold".
- **Long shape is sparse:** 0–2 tempos/races per year before 2025, none in 2026. Its 2026 value is
  last year's carried forward with a growing sd. A coupled model (long = interval − slowly varying
  endurance gap) would let intervals inform it. Not done.
- **Ski:**
  - μ and η are assumed, so the absolute level is uncertain by ±5–10 %. Snow `ski_hard` sessions sit
    8.8 below the shape (roller: −1.0), but snow easy sessions don't. That's not a stable enough signal
    for a surface offset.
  - Classic vs skate isn't in the files.
  - The FR935 barometer is the main noise source.
  - A route-reference altitude profile (from `compare_route`) would remove most of the noise on R3.
- No heat, wind or snow-condition correction. Treadmill sessions aren't scored.
- The calibration offsets (interval −2.5, tempo +3.0) are in-sample on 6 and 8 cases.
- Mislabelled sessions are caught only when they're far too fast. A ski session saved as an easy run
  stays a harmless bound.

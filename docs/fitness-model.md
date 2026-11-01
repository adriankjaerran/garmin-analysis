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
  excluded because their distance is untrusted. Bislett indoor sessions (winter) score from their whole-lap
  reps only (n × 546.5 m − 7.5 m, speed 0 between reps); no continuous / HR-aerobic items from them.

**Races** (`race?` within 3.5 % of 5k / 10k / half / marathon): Daniels VDOT from the nominal distance
and moving time.
- ≤ 7 km counts for interval, with kind `race`.
- Longer races count for long (`race`). A 10k also counts for interval, as `hard` with σ 2.

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
  [session-groups.md](session-groups.md); 7 Holmenkollen climbs moved from ski_hard to ski_intervals and
  stay `hard`, so the scores didn't change), the rest are `bound`.

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

## Stale shapes and the one race prediction
- **Uncertainty grows without evidence.** The random walk adds q per day, so a discipline without new race / hard
  evidence keeps its last value and its sd grows by √(q·days): adrian's long shape (last race 2025-11-02, 364 days)
  is 58.4 ± 2.8 while interval is 52.8 ± 1.0; patrik's long was 44.2 ± 3.7 (923 days) before his 2026-10-27 tempo.
  The smoother itself is unchanged (q is fitted, see below), so form_delta (which refits the same smoother) stays
  consistent.
- **Staleness fields** in every `current`: `last_evidence_date` (latest race / hard item with weight > 0.5, the same
  rule as the API's `last_evidence`), `days_since_evidence`, `stale` (> `STALE_DAYS` = 90 days, or none). The app
  greys a stale value and says "last evidence 12 mo ago" (Home shape strip, Fitness table, activity Shape section),
  and hides its % of best / trend there.
- **One race prediction** (`race_prediction`, `disciplines.overall.current.race_prediction`; its
  `race_equivalents_s` is also `overall.current.race_equivalents_s`, which Home shows as "Today 10k ≈ 39:01"):
  the interval and long shapes averaged with weights 1/sd². A stale shape has a wide band, so it fades out by
  itself instead of giving a second prediction minutes apart (adrian: interval 89 %, long 11 % → VDOT 53.5 ± 2.0,
  10k 39:01, range 37:49–40:19; patrik: 55 / 45 % → 48.2 ± 1.3, 10k 42:40). sd = √(1/Σw + Σw·(mᵢ − m̄)²/Σw): the
  spread between the two shapes is added, so the band widens when they disagree (adrian's 10k-race long shape 58.4
  vs his session-based interval shape 52.8). It is one number for all distances; the per-discipline equivalents stay
  in the Fitness table (interval for 5k–10k, long for 10k–half).

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

## Variant "hr": performance + heart-rate intent
`fitness.json` holds two variants with the same per-session scores:
- **pace** (performance only: speed, altitude, distance, breaks): everything above. Stored in `disciplines` / `sessions`.
- **hr**: HR judges *intent*, meaning whether a session labelled hard was really maximal. Stored in `variants.hr.disciplines`
  and `sessions[].hr` = {kind, sigma, weight, shape_at, p_max, note}.

HR never changes a score. The three ways it failed as a score input are in the next section. It only changes how much a hard
session counts (`apply_hr_intent`, parameters `P["hr_intent"]`):
- **Effort measure:** the work-HR p90 as %HRR for intervals; the HR over the last 2/3 of the scored window for long and ski.
- **p(maximal)** = Φ((hrr − thr)/0.04), with thr = 0.85 for interval and long, 0.82 for ski.
- **Watch trust:** p_eff = trust·p + (1 − trust). 4315: trust 1. FR935: 0.3 on intervals (erratic wrist HR in bursts),
  0.5 on steady efforts. With trust ≤ 0.5, p_eff ≥ 0.5, so FR935 HR alone can never demote a session.
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
- **In-sample choice.** `demote_p` 0.5 and FR935 steady trust 0.5 were chosen from a 3×3 grid (`variants.sensitivity_hr`).
  The starting settings (0.3 / 0.8) were no better than pace (R3 0.40). The ski gain mostly comes from that one 2026-02-18
  session, so treat it as a hypothesis. Re-check as more 4315 sessions come in.
- Current shape is identical in both variants (interval 53.0, long 58.4, ski 55.3 vs 55.4). Yearly values differ by
  ≤ 0.2.

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
{built, model_version, params (P), sigma (SIG), scale, hr_used_in_score,
 disciplines: {
   interval|long|ski: {label, unit, q_per_day, personal_best,
     current: {date, mean, sd, confidence (high <1 / medium <2 / low), pct_of_best,
               race_equivalents_s {5k, 10k, half, marathon} | null for ski,
               last_evidence_date, days_since_evidence, stale (> 90 days)},
     yearly: {YYYY: {mean, max, sd_median, n_evidence, mean_well_observed (days with sd<2), days_well_observed}},
     weekly: [{date, mean, sd, n (non-bound evidence that week), days_since_evidence}]},
   overall: {label, unit, current {date, mean, sd, race_prediction {vdot, sd, weights {interval, long},
             race_equivalents_s, range_10k_s [fast, slow]} | null, race_equivalents_s (= the prediction's)},
             yearly {YYYY: mean}, weekly [{date, mean, sd}]}},
 variants: {pace: {label, hr_used_in_score}, hr: {label, hr_used_in_score, disciplines (same shape as above)}},
 sessions: [{id, date, discipline, type, score, kind (race|hard|bound|implausible), sigma,
             weight (robust weight 0..1 the smoother gave it), shape_at (smoothed value that day),
             confidence (evidence strength relative to a race = 1), components {...},
             hr {kind, sigma, weight, shape_at, p_max, note}}],
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
- `apply_hr_intent(ev)` / `variant_evidence(acts, feats, variant)` → the hr variant's evidence.
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

## Form delta (`scripts/form_delta.py`)
"How well did this session go compared to the person's form": **delta = session score − shape on the day before,
from the shape model refitted without this session** (VDOT points; "+2.1 vs form" / "−1.3 vs form"). Used by the
Community pages (plan decision 5).
```
uv run scripts/form_delta.py adrian <activity_id>   # one session (JSON)
uv run scripts/form_delta.py adrian --series        # weekly shape trend
uv run scripts/form_delta.py --together             # every participant of every group in data/together.json
```
```python
import form_delta as fd                 # scripts/ on sys.path
fd.form_delta(person, activity_id)      # -> dict | None
fd.form_deltas(person)                  # {activity id: dict} for every session with one
fd.form_series(person, since=None)      # [{date, interval, long, ski, overall, sd: {disc: sd}}], weekly, oldest first
fd.together_deltas()                    # together.json groups with a `form` per participant
```
`form_delta` → `{score, shape, delta, discipline, kind, confidence, text, date, ref_sd, z, weight, flags, basis}`.
- **Score:** the session's own score from `fitness.json` (race > hard item; then smaller σ, then interval). A 10k race
  uses its long/race item, a 5k its interval/race item. **None** for easy/long runs (bounds: only lower bounds, not a
  performance), hikes, rides and anything the model doesn't score (treadmill, multi_sport, implausible).
- **Reference:** `smooth_series` (same q, σ and robust weights as `fitness.json`) on every *other* evidence item of
  that discipline, read on the day before. Leaving the session out stops it pulling its own reference (a good session
  otherwise looks smaller: `score − shape_at` under-states it). Chosen over the forward filter (only earlier
  sessions): with sparse data the forward filter swings after each odd session (patrik's 2026-09-10 glitch raised
  his forward shape from 41 to 52, so the next three sessions looked 3–6 points bad) and it fits worse: median
  |delta| of hard sessions 2.1 (forward) vs 1.8 (leave-one-out) for adrian's intervals, 6.8 vs 5.4 for patrik's.
  Cost: later sessions inform the reference, so an old delta can shift slightly when `fitness.json` is rebuilt.
- **Confidence** from the combined sd √(ref_sd² + σ²): high < 2, medium < 3, else low. **z** = delta / that sd.
- **Flags:** `hr_not_maximal` (the HR-intent variant demoted it), `downweighted` (model weight < 0.5),
  `sparse_reference` (ref_sd > 3), `outlier` (|z| > 3: confidence low), `no_reference` (the discipline's only
  race/hard item: shape/delta None, text "no earlier form to compare"). `outlier` + `downweighted` → confidence
  `none`, text "score unreliable" (delta still returned).
- **Cache:** reads only `fitness.json`; one refit per race/hard session (~1.5 s for adrian cold), cached in memory
  and in `data/<person>/cache/form_delta.json`, keyed by `fitness.json` mtime + size + `CODE_VERSION`.

Typical spread (2026-11-01): adrian intervals median −0.7, p10–p90 −5.2…+2.0 (127); long +0.3 (12); ski −3.8,
p10 −12.6 (15, low confidence). patrik intervals median −3.0, p10 −12.7 (14 sessions over 3.5 years).

**Limits.**
- It measures the session, not the day's fitness. Hard sessions are often not all-out, so deltas lean negative.
  Threshold/long-rep sessions score below VO2max sessions (adrian 3×1800 + 2×900: −2.5). A partial session scores
  only what that person ran: fewer reps mean a lower W′ demand and so a lower score (patrik's 10 of 15 × 400 still
  came out +2.9 because his 400s were fast).
- Noise: |delta| < ~2 (adrian) is within test-retest (track 1.5–2.4, road 2.4). Ski deltas carry the ski score noise
  (sd 3.4 on R3, snow hard sessions ~9 below): always low confidence.
- The score is only as good as the session label. patrik 2026-10-27 (a steady run split at ~10 s stops, was scored as
  intervals: −5.9) is a tempo run since 2026-11-01 (session_groups `recovery_share`): long evidence 47.9, +3.7 vs form
  (low confidence, sparse reference). patrik 2026-09-10 (reps at 2:47–3:27 /km with HR 115–158, probably not on foot)
  is still `intervals_road`, scored 60.4: "score unreliable" (outlier + downweighted); in the with-HR variant it pulls
  patrik's interval shape up (53.9 vs 48.7 performance-only). patrik's watch records every 5 s, which blurs short reps.
- No HR fallback for easy runs: the HR-aerobic estimate failed as a score (test-retest 4.7 VDOT, above), and the
  efficiency factor exists for only 36 of adrian's easy runs and 1 of patrik's, rounded to 3 decimals (≈ 5 % steps).
- HR-intent parameters (HR max 206, watch trust by date) are adrian's and are applied to patrik too.

**Robust reference (2026-11-01):** each other session enters the leave-one-out reference with the shape model's robust
weight (`sigma / √weight`), so a session the model rejected (e.g. patrik 2026-09-10, 60.4, weight < 0.5) no longer
inflates the form the other sessions are compared with (it had pushed patrik's October reference to 57 → deltas −8 to −11).

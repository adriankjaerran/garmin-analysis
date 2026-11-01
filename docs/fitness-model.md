# Fitness / shape model (`scripts/fitness_model.py`)

`uv run scripts/fitness_model.py adrian [--eval]` → `data/adrian/fitness.json` (+ `fitness_eval.json` with
`--eval`, or run `scripts/fitness_eval.py adrian` on its own). Input is `groups.json` (run
`session_groups.py` first) plus the FIT files. Per-session features are cached in
`data/adrian/cache/fitness_model.pkl`, keyed by FIT mtime+size, the resting HR used and all parameters
in `P`. Timing: about 9 s cold (8 workers), 0.1 s cached. The eval takes 0.8 s.

## What it gives
Shape scores over time: **interval** (short/fast running), **long** (long-distance running) and
**ski** (rollerski + snow). There is also an **overall** score across disciplines. Each session becomes
0..n *evidence* items. A robust random-walk smoother turns them into a daily curve with an uncertainty
band. Running is one **coupled distance curve** (model_version 2, [below](#coupled-distance-curve-race-predictor)):
one shared fitness trend plus a personal VDOT-vs-distance slope, so interval (= the curve at 5 km), long (= the
curve at ~14.5 km) and the race predictor (any distance) are readings of the same model.

**Scale: Daniels VDOT** ("running VO2max as shown by performance", ml/kg/min). Why:
- One number maps to equivalent race times (`race_time(vdot, dist)`). VDOT 55 = 5k 18:22, 10k 38:06,
  half 1:24:16. VDOT 60 = 17:03 / 35:22 / 1:18:09.
- The %VO2max-vs-duration curve handles effort length, which plain pace or Riegel can't.
- Ski is put on the same ml/kg/min scale through physics, so the numbers are comparable in kind. The
  absolute ski level depends on an assumed efficiency (see limitations). The trend is what matters.
- **Overall** = % of personal best per discipline, precision-weighted (1/sd²) across disciplines (running once:
  interval and long are one curve). Ski
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
- Longer races count for long (`race`). On the distance curve each race sits at its own distance, so a 10k is
  counted once. (`collect_evidence` still emits the old interval/`hard` σ 2 copy of a 10k for the per-discipline
  back-tests; `build` drops it, `is_10k_dup`.)

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

## Stale shapes
- **Uncertainty grows without evidence.** The random walk adds q per day, so a discipline without new race / hard
  evidence keeps its last value and its sd grows by √(q·days). Before the distance curve adrian's long shape (last
  race 2025-11-02, 364 days) was 58.4 ± 2.8 while interval was 52.8 ± 1.0; now intervals inform long through θ and
  long is 55.9 ± 1.5 (only the slope is old).
- **Staleness fields** in every `current`: `last_evidence_date` (latest race / hard item with weight > 0.5, the same
  rule as the API's `last_evidence`; for interval / long any item on the distance curve), `days_since_evidence`,
  `stale` (> `STALE_DAYS` = 90 days, or none). The app greys a stale value and says "last evidence 12 mo ago" (Home
  shape strip, Fitness table, activity Shape section), and hides its % of best / trend there.

## Coupled distance curve (race predictor)
Running is one model since model_version 2 (`smooth_curve`, parameters in `CURVE`, fitness.json `curve`):
- **State per day:** θ_t = VDOT at 5 km (the reference) and s_t = slope, the VDOT change per doubling of distance.
  The prediction at any distance is **VDOT(km, t) = θ_t + s_t · x** with x = log2(km / 5).
- θ is a random walk with the interval q (0.05 VDOT²/day). s is a slow random walk, **q_s = 0.001** (≈ 0.6 VDOT per
  doubling per year), prior s ~ N(0, 3²). 2-state Kalman filter + RTS smoother on the daily grid, per-kind σ (`SIG`)
  and the same asymmetric robust IRLS weights as `smooth_series`, with each residual taken against the curve at the
  item's own distance.
- **Effective distance per item** (`evidence_km`; `sessions[].km`):
  - race: its nominal distance (5k / 10k / half / marathon). A 10k counts once (the old interval/hard copy is gone).
  - interval session (hard): **5 km**. The −2.5 offset ties the session scores to the race level; 7.07 km (the
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
- **Disciplines from the curve:** `interval` = the curve at 5 km (θ), `long` = the curve at the log-mean of 10k and
  half (**14.5 km**, `long.current.ref_km`), each with the same schema as before (current / yearly / weekly,
  personal_best, stale fields). Both `race_equivalents_s` are the curve's per-distance times (= the race
  prediction), not the flat equivalents of one VDOT. `shape_at` of a running session = the curve at that session's
  distance on its day.
- **Staleness** for interval / long: `last_evidence_date` = the latest race / hard item on the curve with weight > 0.5
  (any distance: the two share θ); `long.current.last_distance_evidence_date` = the latest 10k+ race / tempo. The
  slope's sd still grows without long evidence, so the long band widens slowly (adrian today: ± 1.5 vs ± 1.0 at 5 km).
- **Race prediction** (`overall.current.race_prediction`, schema kept): `vdot` / `sd` at 10k (sd = state ⊕ race-day
  noise `race_sd` 1.0), `race_equivalents_s` per distance from the curve (also `overall.current.race_equivalents_s`,
  Home's "Today 10k ≈ …"), `range_10k_s` ±1 sd; new: `vdot_by_distance`, `sd_by_distance`, `ranges_s`,
  `extrapolated`, `theta`, `slope`, `model: "distance_curve"`. `weights` is `{}` (the interval/long blend is gone).
- **Extrapolation:** outside the evidenced distance range (`evidence_km` = min / max km of race / hard items with
  weight > 0.5; adrian 5–21.1, patrik 5–14.5) the sd grows by `extrap_sd_per_doubling` = **1.0** VDOT per doubling
  and the distance is flagged `extrapolated` (adrian: marathon, 1.0 doubling; 3k, 0.74 below 5 km). The 1.0 is an
  assumption: there is no marathon evidence to fit it. The range is all-time, also for past dates.
- **No gain past the longest evidence:** beyond `evidence_km` max a positive slope stops, so the VDOT is held at its
  value there (adrian: marathon VDOT = half VDOT, 56.9 → 2:50:54 instead of 58.9 → 2:46:03). Carrying +2.0 further
  would predict a marathon above the half, optimistic without marathon training. A negative slope keeps applying
  (patrik's marathon still loses vs his 14.5 km level). The extrapolation sd is still added.
- **Overall** counts running once: the mean of interval and long % of best with the sd of a fully correlated mean
  (they are two readings of one curve), precision-weighted with ski.

**Eval** (`scripts/shape_split_eval.py`, 2026-11-01, adrian; forward filter = data strictly before the race; forecast =
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

Today (2026-11-01, `GET /api/adrian/fitness/predict`):

| | 3k | 5k | 10k | half | marathon |
|---|---|---|---|---|---|
| adrian VDOT | 51.4 ± 1.7* | 52.8 ± 1.4 | 54.8 ± 1.6 | 56.9 ± 2.1 | 56.9 ± 2.3* |
| adrian time (range) | 11:16 (10:57–11:36) | 19:01 (18:35–19:27) | 38:13 (37:17–39:12) | 1:21:49 (1:19:16–1:24:33) | 2:50:54 (2:45:08–2:57:06) |
| patrik time (range) | 11:42 (11:13–12:13) | 20:23 (19:46–21:03) | 42:50 (41:33–44:12) | 1:36:19* (1:32:08–1:40:56) | 3:23:07* (3:10:22–3:37:49) |

\* extrapolated. Before (v1): adrian one VDOT 53.5 ± 2.0 for every distance (10k 39:01, half 1:26:21), long shape
58.4 ± 2.8 (stale); patrik 48.2 ± 1.3 (10k 42:40).

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
- Current shape is identical in both variants (2026-11-01: interval 52.8, long 55.9, ski 55.3 vs 55.4; the hr variant
  gets its own distance curve). Yearly values differ by
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
{built, model_version (2), params (P), curve (CURVE), sigma (SIG), scale, hr_used_in_score,
 disciplines: {
   interval|long|ski: {label, unit, q_per_day, model (distance_curve | random_walk), personal_best,
     current: {date, mean, sd, confidence (high <1 / medium <2 / low), pct_of_best,
               race_equivalents_s {5k, 10k, half, marathon} | null for ski,
               last_evidence_date, days_since_evidence, stale (> 90 days),
               long only: last_distance_evidence_date, ref_km},
     yearly: {YYYY: {mean, max, sd_median, n_evidence, mean_well_observed (days with sd<2), days_well_observed}},
     weekly: [{date, mean, sd, n (non-bound evidence that week), days_since_evidence}]},
   overall: {label, unit, current {date, mean, sd, race_prediction {vdot (10k), sd, weights {}, model, theta, slope,
             vdot_by_distance, sd_by_distance, extrapolated, race_equivalents_s, ranges_s, range_10k_s [fast, slow]}
             | null, race_equivalents_s (= the prediction's)},
             yearly {YYYY: mean}, weekly [{date, mean, sd}]}},
 variants: {pace: {label, hr_used_in_score}, hr: {label, hr_used_in_score, disciplines (same shape as above)}},
 predictor: {ref_km 5, x "log2(km/5)", q {theta, slope}, slope_prior_sd, race_sd, evidence_km [min, max],
             extrap_sd_per_doubling, x_mapping, long_ref_km, last_evidence_date,
             daily [{date, theta, slope, var_theta, var_slope, cov}] (RTS smoothed, first evidence → today),
             evidence [{date, id, km, vdot, kind, weight, discipline, sigma}] (race / hard + bounds with weight ≥ 0.05)} | null,
 sessions: [{id, date, discipline, type, score, kind (race|hard|bound|implausible), sigma, km (running: distance on the curve),
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
- Distance curve: `smooth_curve(obs, day0, day1, q_theta=, q_slope=)` with obs (day, score, sigma, kind, x) →
  (days, mean (n, 2), cov (n, 2, 2), weights, forecasts); `curve_at(mean, cov, x)`; `curve_predict(theta, slope,
  var_theta, var_slope, cov, km, evidence_km)` → {vdot, sd_state, sd, extrapolated, …}; `evidence_km(item)`,
  `curve_x(km)`, `curve_obs(ev)`, `slope_text`, `dist_label`; parameters `CURVE`.
- Physiology helpers: `vdot_from_race`, `race_time`, `speed_for`, `daniels_vo2`, `daniels_pct`, `cs_dprime`,
  `vdot_from_cs`.
- Model pieces: `gap_factor`, `ski_vo2_demand`, `wbal_min`, `wbal_vdot`, `continuous_vdot`, `ski_effort`,
  `hr_aerobic`.
- Per FIT: `session_series(fit, act)` and `session_features(fit, act, hr_rest)`.
- `fitness_eval.run(person)` → the eval dict.

## Shape trend (2026-11-01, model_version 2)
| | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 | now |
|---|---|---|---|---|---|---|---|---|---|
| Interval (VDOT at 5 km, yearly mean) | 47.8 | 51.2 | 51.2 | 49.1 | 52.1 | 53.6 | 54.1 (peak 56.0) | 53.2 | 52.8 ± 1.0 (5k ≈ 19:01, 94.4 % of best) |
| Long (VDOT at 14.5 km) | 49.8* | 53.2* | 53.0 | 51.0* | 54.2 | 55.9* | 56.9 (peak 59.0) | 56.2* | 55.9 ± 1.5 (no 10k+ evidence in 2026; θ from intervals) |
| Ski (VDOT-eq.) | 39.6* | 41.5* | 43.5* | 46.3* | 49.1* | 49.9 | 50.4 | 53.9 | 55.3 ± 2.8 |
| Overall (% of best) | 81.1 | 86.7 | 87.9 | 86.6 | 92.0 | 93.9 | 95.6 | 95.6 | 95.4 ± 2.0 |

v1 (separate shapes, 2026-10-31): interval 2025 54.9, long 2025 57.8 / now 58.4 ± 2.8, overall now 95.5 ± 1.6. The
interval yearly values are 0.1–0.8 lower in v2 because a 10k no longer counts as an interval item at its own VDOT
(it sits at 10 km on the curve, one doubling up).

\* 0–2 pieces of evidence that year. The value is interpolated between evidence and the band is wide.
Check `n_evidence` / `days_well_observed`.

## Limitations / next steps
- **Intent is unknown.** A threshold session scores lower than a VO2max session at the same fitness.
  The robust lower tail absorbs some of this, and residuals of hard sessions have median −0.4, sd 2.1.
  Interval shape in 2026 is lower because the Bislet 1000s are slower (3:22–3:33/km in 2026 vs 3:09–3:20 in autumn 2025, similar rests). The
  model can't tell "less fit" from "ran them as threshold".
- **Long evidence is sparse:** 0–2 tempos/races per year before 2025, none in 2026. The distance curve lets the
  intervals carry the level (θ), but the slope (how much better at long races) is only learned from 10k+ races and
  tempos; it is carried forward with a slowly growing sd (q_s 0.001). Before 2025 the slope is mostly last years'
  slope smoothed backwards (RTS), so old long values (e.g. 2020: 53.2 vs 49.1 in v1) lean on recent races.
- **Distance curve:** linear in log-distance is an assumption; the marathon (and anything > 21.1 km or < 5 km) is
  extrapolated with an assumed +1.0 VDOT sd per doubling, and a positive slope is held flat past the longest evidence
  (adrian's marathon = half VDOT, 2:50:54). Whether the marathon really holds that level is untested. Tempo / interval
  distances inherit the in-sample offsets.
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
  that discipline, read on the day before. Running (interval + long, fitness.json with `predictor` and
  `sessions[].km`): `smooth_curve` on every other running item, read on the day before **at the session's own
  distance** (`_curve_refs`; `CODE_VERSION` 4). Long deltas therefore compare with the curve, which intervals inform:
  adrian's 2021 tempos are −7.5 (v1: −3.9) because the intervals said he was fitter than those tempos. Leaving the session out stops it pulling its own reference (a good session
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

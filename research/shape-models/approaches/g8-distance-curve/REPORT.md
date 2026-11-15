# G8: Speed–distance curve (critical speed / D′, Riegel, Peronnet–Thibault, Daniels + slope, grade adjustment)

(The agent's report file was blocked; the orchestrator wrote it from the agent's returned findings.)

**Answer: one fitness level plus a personal distance curve, not separate short and long fitness.** No curve family or grade-adjustment (GAP) formula beat production's race score by more than noise. The best variants reach 1.26–1.31 against production's 1.34.

## Files
- **Code** (in this folder): `curves.py` is the core. `run_eval.py` and `tables.py` reproduce the tables. `model.py` exposes `predict` and `MODELS`.
- **Data:** `data/_research/shape/g8/`. The full results table is `table_all.csv`.

## Method
1. **Evidence:** every production evidence item was re-derived from the 1 Hz series. It reproduces `prod_score` to within 0.005. Each item was computed under 4 GAP variants and 5 D′ multipliers.
2. **Curve families:** a level that varies over time plus a fitted shape, for five families:
   - flat Daniels (no curve)
   - Daniels + slope (production)
   - personal Riegel exponent
   - critical speed + D′ (2-parameter)
   - a Peronnet–Thibault-style 3-parameter curve
3. **Shape over time:** either fixed, or slowly varying (the shape forgets old evidence with a half-life of 730, 365 or 180 days).
4. **Check:** production's own filter on the same evidence matches `production_curve` exactly.

## Scores
Race MAE in VDOT, adrian + rise. Production scores 1.34 on all 10 races.

| model | race (10) | half and longer (4) |
|---|---|---|
| no curve (flat Daniels) | 1.62 | 2.41 |
| fixed personal shape, any family | 1.49–1.56 | 2.09–2.29 |
| slowly varying shape (half-life 180 d), any family | 1.26–1.34 | 1.59–1.65 |
| production filter with faster slope, q_s 0.02 (`model.predict`) | 1.31 | 1.76 |
| production filter, slope held fixed | 1.64 | 2.57 |

- **Curve family doesn't matter.** Fitted correctly, all families land within 0.1 of each other.
- **Fitting pitfall:** Riegel, critical-speed and Peronnet–Thibault fits that use the observed time of a fixed-distance result are biased. A weak session gets placed at a longer time, which inflated race MAE from about 1.55 to 1.84–1.95. The fix is to solve the model's time at the item's distance and compare VDOT there.
- **Critical speed + D′ alone fails over long distances.** It has no endurance decay. Its hard-effort MAE is about 4.7, against about 4.2 for production.
- **Interval tier (2.18–2.28) and hard-effort tier (3.99–4.35)** stay flat across all variants.
- **Changes that made no difference:**
  - placing intervals at their work duration instead of at 5 km
  - dropping the extrapolation cap (adrian's Copenhagen half favours no cap, rise's Bilbao half favours the cap)

## Per-runner short-vs-long profiles
Measured as half-marathon VDOT minus 5 km VDOT, on the interval-score scale.
- **Today:** adrian +2.0 ± 1.0, rise +1.6 ± 0.9. Both are relatively stronger at long distances.
- **adrian − rise:** +0.5 ± 1.3, so no detectable difference.
- **Short end:** a rep-length study puts each runner's best D′ multiplier at about 1 (adrian 0.96, rise 0.87). Their difference is −0.07 ± 0.77 VDOT per doubling of rep length, so neither is relatively stronger at short distances.
- **Races only:** rise looks 1–2 VDOT weaker than adrian on the 10k → half step, but that is within noise.
- **The profile isn't stable over time:** adrian's gap ran −3 in 2022 and +2 in 2026, largely because his 2021–22 tempos weren't all-out. This drift is why every fixed-shape model loses about 0.3.

## Extrapolating from short-only history
All evidence over 7 km was removed.
- Every textbook curve then under-predicts the 10k and half by about 2 VDOT for both runners (MAE 1.9–2.1).
- A shared prior slope of +1 per doubling brings that down to 1.18.
- So the tilt towards long distances is common to both runners, not personal. It is either a property of the interval-score scale or real physiology.

## Grade adjustment (GAP)
Production uses Minetti uphill plus half of the downhill saving.
- **Races:** all four formulas score 1.34–1.37 with the production filter. No difference.
- **Heart-rate fit on steady road runs:**
  - Any GAP beats no GAP by a wide margin.
  - Production's formula has the lowest HR residual for adrian (4315 watch: 5.31 bpm, vs 5.83 for Strava-style).
  - Full Minetti is narrowly lowest for rise and patrik.
  - Strava-style is the worst formula in every set. A per-person learned cost is no better.
  - This disagrees with G2, which preferred the Strava curve on VDOT residuals.
- **Road vs track intervals:** road sessions score 2–4 VDOT lower than track under every formula. Most of that gap isn't grade cost.

## Recommendation
1. Keep the current curve (Daniels + slope per doubling), the extrapolation cap and the current GAP.
2. Keep the slope varying over time; q_s 0.005–0.02 is fine.
3. Consider a shared slope prior of +0.5 to +1 per doubling instead of 0. It helps runners with interval-only history and new users, and is neutral otherwise. It was chosen on the short-only test. It overlaps with `interval_offset`, so if one changes, re-check the other.

## Open
- Is the shared +1 tilt real physiology, or an artefact of how interval sessions are calibrated?
- No marathon data exists, so marathon extrapolation couldn't be tested.
- The grid models make no prediction for patrik's only race, because he has no prior race or hard evidence. The production-filter variants miss it by 10.87, same as production.

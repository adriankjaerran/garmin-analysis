# G2: Covariate effects (surface, incline, race-day, workout intent, temperature)

The agent was blocked from writing this file; the orchestrator wrote it from the agent's findings.

**Headline:** the effect sizes are stable and informative, but none of them improves race prediction by more than noise. The best variant scores race MAE 1.27, against production's 1.34.

## Files
- **Code:** `model.py` and `prodcorr.py` (the models), plus `obs.py`, `effects.py`, `gap_check.py`, `run.py` and `band_table.py`.
- **Outputs:** `data/_research/shape/g2/`. This holds the effect and sensitivity CSVs, `gap_check.txt`, `eval_table.txt`, `band_table.md` (per person / tier / distance band) and the per-target prediction files.

## Method
- **Model:** one Kalman filter covers all three runners.
  - Each runner has a daily fitness random walk and a distance slope.
  - Shared effects each get a per-person random deviation (sd τ = 1 VDOT). This makes it a mixed model, and its uncertainties are exact given the settings.
- **Evidence:** 173 interval scores, 11 races and 19 hard efforts, optionally plus 79 steady/long runs.
- **Effect sizes** come from a fit on all data.
- **Predictions** are time-forward only, by one of two routes:
  - **Own model:** the filter's state on the day before the target.
  - **prodcorr:** production's evidence, corrected with the effects as estimated on that day, then run through production's curve unchanged.
- Nothing was tuned on races.

## Effect sizes
VDOT, pooled ± 1 sd. The reference is a track interval session.

| effect | estimate |
|---|---|
| race vs track interval session | +0.0 ± 1.0 |
| race vs road interval session | +1.6 to +2.2 ± 1.2 |
| race vs road tempo | +5 to +6 ± 1.5 (training efforts were sub-max) |
| race vs track time trial / high-HR effort | about +1 / about 0 ± 2 (n = 2 each) |
| road vs track interval session | −1.6 to −2.3 ± 0.8 |
| Bislett indoor vs track | −0.75 ± 0.9 (not significant) |
| track tempo | −2.2 ± 1.1 |
| steady / long run, best 5 km | −11 / −15.5 |
| heat, per °C of wrist temperature above 24 | −0.27 ± 0.14 (same under every setting) |
| grade-adjusted pace (GAP) | production over-corrects hills: only about 55–65 % of its correction is real (κ 0.35–0.47 ± 0.13). Strava's curve fits best (κ ≈ 0.1). |

- **Race-day bonus:** none, compared with a genuinely maximal training effort. The big race-vs-training gaps come from training efforts being sub-maximal.
- **Treadmill and trail can't be estimated.**
  - Treadmill distances are untrusted. rise has 3 sessions, and a crude estimate is about −3 ± 3.
  - The only "trail" runs are actually hikes.
- **Pooling:** complete pooling gives the same numbers. The choice that moves estimates most is the robust weighting (asymmetric vs symmetric), by 0.5–1.

## Scores
MAE VDOT. Race = adrian + rise. Production scores race 1.34, interval 2.28, hard_effort 4.18.

| model | race | interval | hard_effort |
|---|---|---|---|
| prodcorr_heat (production + heat correction) | 1.27 | 2.24 | 4.18 |
| prodcorr, GAP correction only | 1.39 | | |
| prodcorr, surface offsets only | 1.54 | 2.19 | |
| prodcorr, all corrections | 1.53 | 2.18 | |
| prodcorr, all + learned race offset | 1.97 | | |
| own model, race + heat | 1.33 | | |
| own model, all effects | 2.33 | 2.18 | 3.90 |
| g2_core (hard efforts kept out of the fitness fit) | 2.01 | | 3.50 |

- **Own-model variants:** changing τ, q and σ keeps race MAE within 2.17–2.48.
- **patrik:** the own model's score of about 1 on his race is luck. With no history, it falls back to its starting fitness of 50.

## Why it fails on races
- **Unstable intent offsets:** offsets learned from a few efforts don't hold up. Adrian's sub-max 2019–22 tempos inflated his 2023 fitness to 58.7, before a 53.3 race.
- **No long evidence:** the own model has no long-distance evidence, so it under-predicts half marathons.
- **Too few races:** with only 10 races, a ±1 correction moves individual races about as much as it fixes them.

## Recommendation
1. Treat the effect sizes as knowledge, not as new production parameters.
2. Heat is the only correction worth considering, and only once real air-temperature data exists.
3. GAP: switch to the Strava curve, or scale production's hill correction by about 0.6. That is a correctness fix (see G8), not a score gain.
4. Keep road and track interval sessions on one scale for race prediction. Use the ~2-point road offset when showing single sessions.
5. Add no race bonus. Production's +3 tempo offset is consistent with the data.

**For the combining step (G9):** `model.predict` (= prodcorr_heat), and `model.predict_core` (g2_core) for the hard_effort tier only.

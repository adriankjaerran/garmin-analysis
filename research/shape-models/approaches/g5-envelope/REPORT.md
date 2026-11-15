# G5: Upper-envelope and censored ("tobit") models

The agent wasn't allowed to write this file; the orchestrator wrote it from the agent's findings.

## Files
- `g5.py`: the models.
- `experiments.py`: experiment groups `main`, `ablate`, `sens`, `quant`, `evt` and `combo`. `experiments.py retable <group>` rebuilds a table from saved predictions.
- Tables and predictions are in `data/_research/shape/g5/`. All scoring went through `harness.evaluate`.

## Idea
Almost every run is a lower bound on fitness: the runner could have gone faster. Only a few runs are near-maximal. Sub-maximal efforts are therefore treated as censored observations rather than thrown away.

## Method (tobit)
- **State:** a forward filter on a 2-D grid of 5 km fitness θ and personal distance slope s.
  - Dynamics copy production: θ is a random walk with q = 0.05, and the slope is pulled back towards 0 with sd 1.5.
- **Evidence:**
  - Official races: treated as exact, σ = 1.
  - Production interval scores: probability of being maximal π = 0.7.
  - Every other run: its best effort over at least 1 mile on grade-adjusted pace (GAP), minus 1.0 VDOT because GPS reads long.
- **Observation model:** y = θ + s·log2(km/5) − u + ε.
  - With probability π (from workout type / HR), u = 0, meaning "fitness ≈ y".
  - Otherwise u ~ Uniform(0, 20), meaning "fitness ≥ y".

## Scores (MAE VDOT)
| model | race adrian | race rise | race adrian + rise | race patrik | interval | hard_effort |
|---|---|---|---|---|---|---|
| production_curve | 0.89 | 1.78 | 1.34 | 10.87 | 2.28 | 4.18 |
| tobit, defaults set beforehand (bests ≥ 3 km) | 0.81 | 1.55 | 1.18 | 3.43 | 2.35 | 3.64 |
| **tobit, bests ≥ 1 mi + tempo shortfall** | 1.08 | 0.78 | **0.93** | 3.43 | **2.23** | **3.30** |
| tobit, best efforts only (no interval scores) | | | 2.88 | | 3.61 | 4.19 |
| time-decayed quantile q0.95, 120-day half-life | | | 1.22 | | 3.58 | 4.77 |
| Gumbel on weekly maxima (best setting) | | | 2.68 | | 6.50 | 5.01 |

**Calibration:** tobit puts 80 % of races within its 1 sd band, vs 70 % for production.

## What worked
- **Adding censored best efforts on top of races + interval scores:** race MAE 1.69 → 1.18 and hard_effort 4.42 → 3.64, with the interval tier unchanged.
- **The learned slope:** it helps on the half-marathon races. With the slope fixed at 0, race MAE is 1.96.
- **patrik:** his race prediction moves from 38 to 45.6 (actual 49.1).
- **Tempo shortfall:** for hard_effort targets, subtracting each person's typical past gap between tempo efforts and fitness brings that tier down to 3.30.

## What didn't work
- **Envelopes built only from runs** (quantile, max, Gumbel, or tobit without interval scores) come out 2–9 VDOT too low. Runs over 3 km are almost never maximal, so lower bounds alone can't find the ceiling.
- **Extreme-value models** are unstable with so few maxima.
- **The HR gate** changes nothing, because few runs have trusted HR.

## Significance
Paired against production, the recommended model wins 6 of 10 races. The mean error difference is −0.40 ± 0.51 (SE). Most of the gain comes from one race, rise's Copenhagen Half 2025, so the race improvement is **not proven**.

## What was tuned on what
- Defaults were set beforehand.
- The 1-mile minimum was chosen on the interval tier.
- The 1.0 GPS correction was measured in-sample. Results barely change anywhere between 0 and 1.5.
- No knob was chosen on race MAE.

## Sensitivity
- **U** (how far below fitness an easy run can sit) is the only sensitive knob: U = 10 gives race MAE 1.94, U = 20 gives 1.18, U = 30 gives 1.45.
- **Race weight:** lowering races' probability of being all-out to 0.6–0.85, or dropping races as evidence, gives race MAE 0.91–1.08. That is mostly rise's weak Bilbao Half 2024, so it was left untuned.

## Recommendation
- Add censored best efforts as an extra evidence stream to the production curve, through a grid or moment-matched update.
- Don't replace the interval scores.
- The solid gains are hard_effort, patrik and the race uncertainty bands. The race-MAE gain is within noise.
- Drop pure quantile and extreme-value envelopes.

## Open questions
- **Better π:** G7's effort classifier could replace the workout-type table.
- **Race-day bonus:** every model under-predicts adrian's Lier 10k 2025 (59.4 actual, ~57.5 predicted). This overlaps G2.

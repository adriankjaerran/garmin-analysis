# G3: HR–pace efficiency from easy and steady runs

The agent wasn't allowed to write this file; the orchestrator wrote it from the agent's findings. The code is in this folder (`run_eval.py final` produces the table), along with the result tables `results_*.csv`. Derived data is in `data/_research/shape/g3/`.

**Headline:** easy-run HR efficiency does not improve race prediction over production.
- On its own it is clearly worse: race MAE 2.6–4.1 vs 1.34.
- Mixed with production, the best variant reaches 1.31 against production's 1.34, which is within the ~0.3 noise.
- Rise's races get worse in every combination.

## Method
1. **Splits:** km splits from easy, steady and long runs, starting at least 10 minutes into the run, filtered for HR coverage and grade.
2. **Speed at a fixed HR:** each split is converted to an equivalent speed at 70 % of heart-rate reserve (HRR), using the Swain mapping between %HRR and %VO2max.
3. **Corrections:** cardiac drift, and the extra HR cost of hills that grade-adjusted pace (GAP) misses. Both are fitted on history only.
4. **Per-device calibration:** each device gets its own offset and noise level. These are calibrated against production's fitness as it was known before each run.
5. **Smoothing:** a filter turns the calibrated splits into an HR-based fitness level.
6. **Combinations with production** (tested):
   - inverse-variance blend
   - the runs added as extra evidence to the production curve
   - prod + w·(HR − prod), with w fixed or learned time-forward

## Device findings
- **adrian, Forerunner 935 (FR935, 2018 – Feb 2026):** no fitness signal. Run-to-run noise is 7.5 VDOT and the correlation with fitness is 0.06. All of adrian's races were on this watch.
- **adrian, device 4315:** usable signal. Noise is 4.6 VDOT and the correlation is 0.34. Switching watches shifted readings by +3.0 VDOT, which the per-device offset absorbs.
- **rise:** on easy runs the wrist sensor reads about 10–15 bpm higher than the chest strap. That is +5.9 ± 1.4 VDOT-equivalent, measured on 8 paired runs. Only 10 strap easy runs exist, so this is only partly corrected.
- **patrik:** the uncalibrated formula reads him about 5 VDOT low. He has one usable HR run before his only race.
- **Drift and hill corrections:** help slightly, within noise.
- **Wrist temperature:** no usable heat effect, so none was applied.

## Scores (MAE VDOT)
| model | race adrian | race rise | race adrian+rise | interval | hard effort |
|---|---|---|---|---|---|
| production_curve | 0.89 | 1.78 | 1.34 | 2.25 | 3.41 |
| HR alone, calibrated on production | 2.49 | 2.75 | 2.62 | 2.49 | 4.55 |
| HR alone, no production | 3.21 | 3.02 | 3.12 | 2.38 | 3.77 |
| inverse-variance blend, HR weight ÷4 | 0.71 | 1.93 | 1.32 | 2.24 | 3.38 |
| runs added to production curve | 0.91 | 1.83 | 1.37 | 2.25 | 3.41 |
| prod + 0.15·(HR − prod) | 0.69 | 1.93 | 1.31 | 2.23 | 3.42 |
| same, w learned time-forward | 0.86 | 1.94 | 1.40 | 2.23 | 3.44 |

## Sensitivity
- **HR alone:** race MAE stays between 2.28 and 3.68 across all one-at-a-time grids. Nothing approaches 1.34.
- **Weight sweep:** adrian improves up to w ≈ 0.15 and then worsens. Rise worsens steadily from w = 0.
- **Interval tier:** gains are only 0.02–0.10.

## How informative easy runs are
- **Worth vs an interval session:** one interval session is worth about 7–9 easy runs on a good sensor, or about 18 on the FR935. In practice it's fewer, because the errors persist over time.
- **Learned slope:** (actual − prod) regressed on (HR − prod) over 183 targets gives 0.25 ± 0.11. That comes mostly from the interval tier. On races the slope is about zero for adrian and negative for rise.
- **Right weight:** HR deserves a weight of 0–0.15 relative to production.

## Recommendation
- Don't add easy-run HR to production now.
- Re-test once adrian has about 6 months of data and some races on the 4315. Use a weight of at most 0.15, per-device offsets, and no FR935 data.
- For the ensembling step (G9):
  - `model.predict` is the blend at full HR weight (race MAE 1.56).
  - `make(hr_shift, w=0.15)` is the near-neutral option.

## Open
- **Warm-ups:** warm-up splits of interval sessions were dropped, because almost none survive the 10-minute rule.
- **Rise's wrist offset:** whether to correct it in `hr_settings` is a production decision, left unchanged here.

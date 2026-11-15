# G6: Machine learning (LightGBM, XGBoost, random forest, extra trees, ridge, elastic net)

The agent wasn't allowed to write this file; the orchestrator wrote it from the agent's findings.

**Headline:** none of the learners beat the production curve on official races.

## What was built
- `features.py` builds 682 rows: all 203 harness targets plus 479 grade-adjusted (GAP) best efforts of at least 1 km from hard sessions.
  - Each row has about 115 features, computed only from `history(person, day)`.
  - Features: production state and prediction, interval scores, GAP and raw best efforts, training load, HR efficiency (device-weighted), surface and terrain mix, and the target's distance, kind, course, device and person.
- `model.py` holds 42 configurations, with `predict()` exposed for G9. The configurations vary:
  - the algorithm (LightGBM, XGBoost, random forest, extra trees, ridge, elastic net)
  - whether the model predicts VDOT directly or the residual over production
  - which rows are used for training
  - one pooled model vs separate short/long models
  - full vs small feature set
  - shrinking the residual
- `importance.py` gives TreeSHAP importances and ridge coefficients.
- Outputs are in `data/_research/shape/g6/`: `scores.csv`, `preds.parquet` and `importance.txt`.

## Leakage safeguards
- Features use only days before the row.
- A target on day d uses a model refit on rows before d, rounded down to a 28-day block.
- Same-day segments are excluded.
- Cross-validation inside ridge and elastic net sees training rows only.
- Configs with fewer than 60 training rows fall back to the production prediction. That applies to the 4 races before 2025, so ML actually predicts only 7 of the 10 adrian + rise races.

## Results (MAE VDOT; race = adrian + rise)
| model | race | interval | hard_effort |
|---|---|---|---|
| production_curve | 1.34 | 2.28 | 4.18 |
| ridge residual, small feature set, shrink 0.5 † | 1.49 | 2.28 | 4.28 |
| random forest residual, small feature set, shrink 0.5 † | 1.68 | 2.18 | 4.22 |
| elastic net residual, targets + race-day segments | 1.85 | 2.22 | 4.30 |
| random forest residual, targets + race-day segments | 2.19 | 2.12 | 4.04 |
| random forest direct, all hard segments | 4.62 | 2.37 | 3.02 |

† The small feature set and the 0.5 shrink were chosen after seeing race results, so those two rows are optimistic.

On the 7 races where ML is active: production 1.24, best ML 1.46.

## Findings
- **Races:** every honest config is 0.5–5 VDOT worse than production. Predictions run systematically low (bias −1.5 to −4.8). With about 11 race rows in total, the race-day level can't be learned, and offsets learned from intervals and segments don't carry over to races.
- **All hard segments as training rows:** this makes races much worse (3.6–6.3). Those rows are mostly sub-maximal reps and tempos that sit 5–9 VDOT below production.
- **Interval tier:** tree residual models beat production by 0.14–0.18, about 2 standard errors. That overstates the confidence, because these targets are autocorrelated, and this tier is on production's own scale.
- **Hard efforts:** models trained on all segments improve from 4.18 to 3.0–3.4. With n = 19 and a paired SE of about 1.2 this is suggestive only. The gain comes from learning that tempos and HR-hard efforts sit below shape, which is an effort-type offset rather than fitness.
- **Pooled vs separate short/long models:** no difference.
- **Direct vs residual:** residual is equal or better for every algorithm.

## What matters (mean |SHAP|)
Ranked:
1. Effort kind and distance dominate.
2. Next: how far recent GAP bests and interval maxima sit from production's fitness level.
3. Then: training load and HR efficiency.

Specific factors:
- **Incline:** modest effect. Flat or track courses come out about +0.3–0.5 VDOT vs hilly road even after grade adjustment, so GAP probably under-corrects hills a little.
- **HR device:** barely matters (|SHAP| about 0.1). The device coefficients mostly stand in for person and time period.
- **Surface of the target:** adds nothing beyond flat course and grade.

## Recommendation
- Keep production for race prediction; don't add a black-box correction on top of it.
- Take two things forward as explicit model terms:
  - an offset for tempo and HR-hard efforts
  - a small flat/track bonus
- `predict()` points at the shrunk ridge residual model (race 1.49), as a weak ensemble candidate only.
- This model has no sd of its own: every config passes through production's sd.

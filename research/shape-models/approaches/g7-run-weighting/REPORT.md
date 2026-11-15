# G7: Which runs are informative (run weighting / "was it maximal?")

The agent was blocked from writing this file; the orchestrator wrote it from the agent's findings.

**Headline:** none of the weightings beats production. The best variant ties it: race MAE 1.29–1.36 against 1.34, within the ±0.3 noise.

## What was built
- **`features.py`** builds one effort per usable run (532 in total). Each effort is one of:
  - an official race,
  - a production interval-session score at 5 km, or
  - the run's best grade-adjusted effort of at least 3 km.

  Each effort gets features for HR and HR trust, workout, surface, rep structure, drift, speed variation and climb. It also gets `delta`: the effort minus the production estimate from the day before.
- **`classifier.py`** learns how likely a run was maximal.
  - **Label:** a run counts as maximal if it lands within 1.5 VDOT of the production curve, smoothed only on data before the checkpoint. Races always count as maximal.
  - **Training:** refit every 61 days on all three persons' past runs.
  - **Models:** logistic regression, a separate logistic regression per run kind, and LightGBM.
  - **Shortfall:** a ridge model learns how far below the envelope an effort usually sits.
- **`model.py`** feeds the weights into three estimators and runs each through the harness:
  - the robust Kalman curve,
  - a soft version that uses every run with variance σ²/p,
  - a weighted rolling fit.
- **`inspect_scores.py`** prints example runs, calibration and per-race errors.
- Outputs are in `data/_research/shape/g7/`.

## Classifier quality
Scored on the following period: 440 non-race runs.
- **Easy vs real efforts:** run features separate easy, steady and long runs from real efforts well (AUC 0.92–0.93). But production's own rule (interval sessions and tempos count) already gets 0.90.
- **Within interval sessions, nothing measured on the run predicts maximality** (AUC 0.42–0.60). HR alone is at chance, and so is production's HR-intent formula (0.50).
- **Only `delta` predicts it** (AUC about 0.96). That is nearly circular, because production's robust weights already use it.
- **Calibration:** reasonable.

## Scores
Production scores race 1.34 (adrian + rise), interval 2.28, hard_effort 4.18.

| Variant | Race (adrian + rise) | Other tiers |
|---|---|---|
| Learned weights applied to production's own evidence | 1.29–1.36 | interval and hard_effort roughly unchanged |
| Same, with production's HR-intent formula | 1.57 | interval 2.44 |
| Aggressive demotion (cutoff 0.7) | 1.92 | |
| Rebuilt from raw best efforts | 1.5–1.8, biased low by 1–1.5 | |
| Soft version (every run in, weighted by p) | 1.62 (adrian 0.56, rise 2.7) | hard_effort 3.46 |
| Dropping lower-bound efforts / hilly road interval sessions / GPS-glitch sessions | 1.34 / 1.34 / 1.52 | |

- **Why the raw-effort rebuild loses:** it's where tempos sit on the distance curve, not the weights.
  - Production places tempos at about 14.5 km and adds +3 VDOT.
  - At their own distance, rise's curve slope turns negative, and her Copenhagen half error grows from −3 to between −6 and −8.
  - The production-rules version on the same efforts already scores 1.51.
- **The learned tempo shortfall helps:** 1.56 with it vs 1.65 without.

## Sanity check of example runs
- Easy, steady and long runs score about 0. Tempos and time trials score 0.84–0.99.
- **One clear failure:** adrian's 2026-06-09 Frogner interval session scores 0.05.
  - It was 3.6 VDOT above the envelope, with HR at 0.96 on the 4315 watch.
  - Its high pace variation reads as "easy run" to the linear model.
  - Adding `delta` and the per-kind model raises it to 0.62.

## Recommendation
- Keep production's inclusion rules.
- Drop the HR-intent ("pace+hr") variant as a candidate for production.
- Lower-bound efforts make no measurable difference either way. Keep GPS-glitch and hilly road interval sessions.
- `model.predict` exists for the combining step (G9) and behaves like production.
- One untested idea: soft inclusion (every run weighted by p, plus the learned shortfall) combined with production's tempo placement.

**What was tuned:** only the demotion cutoff (0.3 / 0.5 / 0.7), checked on all tiers. The 1.5 label gap and the σ values were fixed in advance.

## Key open issue
Where tempos sit on the distance curve (14.5 km with +3 VDOT, or their own distance) drives rise's half-marathon errors far more than any weighting.

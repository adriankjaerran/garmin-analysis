# G4 — Training-load response models

**Headline:** training load does not beat production by more than the ~0.3 VDOT race noise level. No production change is recommended.

(The agent's tool to write this file was blocked. The orchestrator wrote it from the agent's returned findings.)

## Method
All code is in this folder; `run_eval.py` reproduces every table below.
- `load.py` builds a daily load per person:
  - Run TRIMP / km / hours come from `activities.parquet`.
  - Ski and other sports come from Garmin's average HR in `activities_index.json`. Ski counts at ×0.5 and other sports at ×0.25 by default.
- `kf.py` is a new copy of the production filter. With load switched off it reproduces `production_curve` exactly (max difference 4e-14 VDOT).
  - Load enters as fixed per-person coefficients β with a prior.
  - β is learned time-forward only.
- `model.py` holds the variants:
  - `make(...)` builds a variant.
  - `predict` is the filter plus a 42-day load average (CTL42), usable as an ensemble member.
- Load settings were screened on the interval / hard_effort tiers only, never on races:
  - the time-constant (τ) grid
  - the load metric
  - the ski discount
- Data artefacts are in `data/_research/shape/g4/`: `daily_load.parquet` and `preds.parquet`.

Variants tested:
- **(a)** Pure Banister / Busso impulse-response models fitted to effort VDOTs.
- **(b)** Production filter plus fixed load terms: CTL42, optionally with a 7-day load (ATL7).
- **(b′)** Level that is pulled back toward a mean (mean-reverting) plus CTL42.
- **(c)** Race-day taper/freshness adjustment of ±0.5·TSB.

## Scores (MAE VDOT)
| model | race adrian / rise / adrian+rise | interval (adrian+rise) | hard_effort (adrian+rise) |
|---|---|---|---|
| production | 0.90 / 1.78 / 1.34 | 2.25 | 3.41 |
| (b) filter + CTL42 | 0.84 / 1.82 / 1.33 | 2.21 | 3.46 |
| (b) + CTL42 + ATL7 | 0.82 / 1.61 / 1.21 | 2.24 | 3.52 |
| (b′) mean-reverting level + CTL42 | 0.71 / 2.00 / 1.35 | 2.17 | 3.39 |
| (a) pure Banister | 0.88 / 2.28 / 1.58 | 2.42 | 3.48 |
| (a) Busso | 0.90 / 2.65 / 1.77 | 2.41 | 3.69 |
| (c) taper ±0.5·TSB | 1.35 (adrian+rise) | 2.25 | – |

- **ATL7 variant:** its 1.21 rests mostly on rise's Bilbao Half 2024, so it is not a real gain.
- **Pure Banister / Busso:** too confident on races. Only 45 % of results land inside the predicted ±1 sd band; a well-calibrated model would land about 68 % there.

## Fitted parameters
VDOT per 10 TRIMP/day of CTL42:

| person | β |
|---|---|
| adrian | +1.17 ± 0.44 |
| rise | +0.50 ± 0.59 |
| patrik | −0.5 ± 0.9 |

- adrian's β sits near 0 every June from 2020 to 2024. It only becomes clear from 2025, when his high-load season coincided with VDOT 57–59. That is why the load model rarely moves predictions.
- Fatigue terms (the 7-day load, the Busso fatigue gain) come out at about zero.
- Counting ski at full weight halves β and changes no score by more than 0.04.

## Long gaps without hard running (ski season, breaks)
15 targets come ≥ 42 days after the last hard effort.
- Production over-predicts these by +1.3 VDOT on average. That comes from four tempo efforts that weren't all-out.
- On the 11 interval sessions after a gap, the bias is only +0.2. There is no clear detraining over ski seasons or breaks.
- The change in load over the gap doesn't explain the surprise (r = 0.01). Adding ski load makes the correlation weaker.
- The mean-reverting variant reduces the post-gap bias, but it does just as well without load. That is shrinkage, not a load effect.

## Taper
The races weren't tapered: freshness on race day (TSB) looks like an ordinary training day. Race residual vs TSB is r ≈ −0.1, the opposite of a taper benefit. There is no signal to use.

## Recommendation
- Keep production's random walk.
- Don't add a detraining drift, a ski discount or a taper term.
- For the combining step (G9), `model.predict` is a near-production ensemble member. The most distinct useful variant is the mean-reverting one: `make(taus=(42,), beta_sd=(1.5,), ar_tau=180, ar_sd=2.0)` (interval MAE 2.17).

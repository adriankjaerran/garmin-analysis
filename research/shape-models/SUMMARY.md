# Shape-model research: overnight summary (2026-11-04 → 05)

**Goal:** find the best way to estimate running shape (Daniels VDOT) for Adrian, Ole Jørgen (`rise`) and Patrik. The questions were how to handle ground and incline, heart rate across devices, short vs long strength, race-day extra effort, and easy runs that say little about fitness.

**Short answer:** the current production model (one robust Kalman fitness plus a personal distance curve) is already close to the best this data supports. More than 30 variants across 11 approach groups were tested; none beats it reliably for everyone. Three things are worth changing, all small:
1. Add *tests / time trials* as their own evidence kind.
2. Optionally let the distance slope adapt faster.
3. Shadow-run one variant (T1) for Ole Jørgen and Patrik.

No production code was changed.

## How it was tested
- **Dataset** (`prep_data.py` → `data/_research/shape/`): running only, with ski races dropped.

  | | runs | km splits | per-run best efforts |
  |---|---|---|---|
  | Adrian | 495 | 3612 | 1819 |
  | Ole Jørgen | 179 | 1460 | 680 |
  | Patrik | 96 | 608 | 345 |

  Each run also has surface, terrain, HR device trust and training load. See `DATA.md`.
- **Harness** (`harness.py`): strictly time-forward. Each target is predicted from data before its day. Scoring tiers:
  - **race:** 10 official races for Adrian + Ole Jørgen, plus 1 for Patrik. This is the main metric.
  - **interval:** session scores, but on production's own scale, so production has a home advantage here.
  - **hard_effort:** tempos and time trials. Noisy, because many weren't all-out.
  - **extended** (`approaches/g10-audit/harness_ext.py`): official races plus 3 high-confidence unofficial efforts.
- **Noise floor:** with 10 races, differences under ~0.3 VDOT are noise. No model reached p < 0.05 against production on races.
- **Audit (G10):** no leakage found. It did find a data-prep bug that dropped 32 interval scores; it's fixed in G11, and the old data is kept in `v1_backup/`.

## Results by approach
Race MAE for Adrian + Ole Jørgen. Production scores **1.33** (after the fix; 1.34 before).

| # | Approach | Best race MAE | Verdict |
|---|---|---|---|
| G1 | Kalman variants: separate short/long fitness, 3-state, load noise, Student-t | 1.20 (faster slope) → 1.26 on fixed data | **One fitness + curve beats a short/long split.** Separate states help Adrian but hurt Ole Jørgen. Faster slope adaptation is a neutral, optional tweak. |
| G2 | Mixed-model effects: surface, hills, race day, intent, heat | 1.27 (heat) | Useful knowledge, no score gain (effect sizes below). |
| G3 | HR–pace efficiency on easy runs, per-device correction | 1.31 (blend) | No gain. HR alone: 2.6–3.1. |
| G4 | Training load (Banister/Busso, CTL drift, ski load, taper) | 1.33 | No gain. No detraining over ski seasons and no taper effect. |
| G5 | Censored "tobit" model: every run as a lower bound | 0.93 originally | Wins on Ole Jørgen and Patrik (10.9 → 3.4), but **worse for Adrian** once his 3k tests are included. |
| G6 | ML: LightGBM, XGBoost, random forest, extra trees, ridge, elastic net (direct and residual) | 1.49 at best | **Fails.** About 11 race rows can't teach the race level; predictions run 1.5–5 VDOT too low. |
| G7 | Classifier for "was this run maximal?" + weighting | 1.29–1.36 | Ties. Within interval sessions nothing measured, including HR, predicts all-out (AUC ~0.5). Current rules are fine. |
| G8 | Speed–distance curves (critical speed/D′, Riegel, Peronnet–Thibault, Daniels + slope), grade-adjustment formulas | 1.26–1.31 | **Curve family doesn't matter.** A slope that varies over time matters a lot (a fixed slope gives 1.5–1.6). Keep the current grade adjustment. |
| G9 | Combined model C, T1 variant, ensembles (settings fixed before scoring) | 1.03–1.13 | Not significant. Picking a model on 9 races and scoring the 10th is *worse* than production (1.41–1.67). |
| G10 | Leakage audit + extra ground truth | – | No leakage; A1 bug found; 3 high and 9 medium extra targets for you to confirm. |
| G11 | Bug fix, re-score everything, Adrian's 3k problem | – | Final table below. |

### Final table (G11, fixed data; MAE in VDOT)
| model | Adrian 5 races | Ole Jørgen 5 races | A+OJ | Adrian + 3 extras | interval | hard_effort |
|---|---|---|---|---|---|---|
| **production** | 0.91 | 1.75 | 1.33 | 1.26 | 2.41 | 4.16 |
| S (faster slope) | 0.89 | 1.64 | 1.26 | 1.26 | 2.42 | 4.13 |
| T1 (C + tempo at its own distance) | 0.91 | **1.31** (5/5 wins, p 0.06) | **1.11** | 1.78 | 2.42 | 3.54 |
| C (combined) | 0.96 | 1.31 | 1.13 | 1.87 | 2.42 | 3.45 |
| G5 tobit | 1.08 | 1.41 | 1.24 | 1.97 | 2.45 | **3.30** |
| mean(C, G5) | 1.02 | 1.23 | 1.12 | 1.92 | 2.42 | 3.36 |

Patrik has only one race, so his numbers are a qualitative check: G5 3.4, C/T1 7.1, production 10.9. He had only easy runs before it.

## What we learned about each factor
- **Short vs long:** model it as one fitness plus a personal slope per doubling of distance, and let the slope change over time. Both Adrian and Ole Jørgen currently look about +1.6–2 VDOT stronger at the half than at 5k on the interval scale, with no detectable difference between them. That tilt is partly an artefact: Adrian's interval sessions sit 1.7–4 VDOT below his all-out efforts at every distance. His true all-out curve is flat to slightly *short-favouring*, and that's why every model under-predicts his Bislett 3k tests.
- **Extra effort in races:** compared with a genuinely all-out training effort, there's no race-day bonus (+0.0 ± 1.0 vs a track interval session). Races look like a jump only because training is sub-maximal. Approximate offsets:
  - race vs road interval session: about +2
  - race vs road tempo: about +5–6
  - production's tempo offset of +3: consistent with the data
- **Easy / chill runs:** they contribute almost nothing to race prediction. As lower bounds (G5) they help runners with little hard data (Patrik) but drag Adrian's estimate down. As HR efficiency, one interval session is worth about 7–18 easy runs.
- **Heart rate and devices:**
  - Adrian's FR935 wrist HR carries **no fitness signal** (correlation 0.06). The 4315 is usable but reads +3 VDOT compared with it.
  - Ole Jørgen's wrist HR reads about 10–15 bpm higher than his chest strap on easy runs.
  - HR doesn't help tell an all-out session from a controlled one.
- **Incline:** some grade adjustment is essential (any formula beats none on HR fit). The current production formula (Minetti uphill, half the downhill credit) is best for Adrian. G2 suggests hills are slightly over-corrected, while G8's HR test disagrees. Leave it as is.
- **Ground/surface:**
  - Road interval sessions score about 2 VDOT below track, and most of that isn't grade.
  - Bislett indoor is about −0.75 vs outdoor track (not significant).
  - Treadmill and trail can't be estimated: treadmill distances are untrusted and there are essentially no real trail runs.
  - GPS reads 0.2–2 % long (about +0.85 VDOT on best efforts).
- **Heat:** about −0.27 VDOT per °C, but measured from wrist temperature. Revisit with real weather data.
- **Training load / taper / ski:** no usable signal.
- **ML (LightGBM etc.):** not with this amount of race data. Revisit when there are about 50+ races, or use it only for hard-effort display.

## Recommendation (current best approach)
**Keep production as the default.** Then:
1. **Add a "test / time trial" evidence kind:** race-like σ 1.0, at its own distance. Start with Adrian's 3 high-confidence efforts once you confirm them: the 10k on 2023-04-22 and the Bislett 3k on 2025-11-15 and 2026-03-31. This is the only change that fixes his short-distance miss without hurting his long races.
2. **Optional:** `q_slope` 0.005 → 0.04 and `slope_prior_sd` 1.5 → 2.0. It's neutral to slightly better, but widens the half-marathon uncertainty band.
3. **Leave alone:**
   - slope mean 0 (don't adopt +0.75)
   - `interval_offset` −0.5 and `tempo_offset` +3
   - no race bonus, HR-efficiency term, load term or ML correction
4. **Shadow-run T1 for Ole Jørgen and Patrik:** log its predictions next to production before races and switch if it keeps winning. It won all 5 of Ole Jørgen's races, but that isn't significant.
5. **Things to do:**
   - confirm or reject the 9 medium extra targets in `approaches/g10-audit/REPORT.md` (`confirmations.json`)
   - log future 3k/5k tests
   - rise's 2026-10-23 10k is labelled "chip" in the research target data, although races.json has no chip time; it's a cosmetic fix
   - the dropped-interval bug (A1) was only in the research `prep_data.py`; production's `fitness.json` is fine (the fixed reproduction matches its published predictions within 0.01 VDOT)

## Today's estimates (2026-11-05, from G9; VDOT, production → combined C)
| | 5k | 10k | Half |
|---|---|---|---|
| Adrian | 54.6 → 54.4 ±1.3 (≈18:32) | 55.4 → 55.5 ±2.2 (≈37:47) | 56.3 → 56.7 ±3.8 (≈1:22:05) |
| Ole Jørgen | 50.9 → 50.4 ±1.4 (≈19:48) | 51.5 → 51.8 ±1.6 (≈40:04) | 52.1 → 53.4 ±2.3 (≈1:26:29) |
| Patrik | 49.0 → 48.3 | 48.2 → 48.5 | 47.4 → 48.6 |

- **Adrian's 5k is probably too low:** his Bislett 3k tests point higher, and the model doesn't use them yet (see recommendation 1).
- **Ole Jørgen's half from C** sits above his recent halves (49.6, 51.5), so production's 52.1 is the safer number.

## Where things are
- `PLAN.md`: the original research plan. `DATA.md`: dataset, cleaning rules, changelog.
- `approaches/gN-*/REPORT.md`: one report per approach. Each folder has its code and usually a `model.py` with `predict`.
- `approaches/g11-rescore/REPORT.md` and `approaches/g9-combined/REPORT.md`: the most decision-relevant reports.
- **Data:** `data/_research/shape/` (gitignored), with the pre-fix data in `v1_backup/`.
- **Interpreter:** `/tmp/shape-venv/bin/python`. It lives in /tmp, so rebuild it per DATA.md if it's gone.
- **Report authorship:** subagents in G2–G8 were blocked from writing their own REPORT.md, so those reports were written by the orchestrator from the agents' returned findings.

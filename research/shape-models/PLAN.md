# Research plan: estimating running shape (VDOT-like) for adrian, rise (Ole Jørgen) and patrik

Goal: a better out-of-sample estimate of running fitness and race times than the production model
(`scripts/fitness_model.py`, model_version 3: robust Kalman distance curve VDOT = θ + slope·log2(km/5) on W′-balance
interval scores, tempo windows and races). Focus adrian and rise; patrik (96 runs, 1 race) is a qualitative check.

## Ground rules for every agent
- Data: `data/_research/shape/*.parquet` (columns: [DATA.md](DATA.md)); interpreter `/tmp/shape-venv/bin/python`.
- Evaluate **only** through `harness.evaluate` (rolling origin, history strictly before the target day). Report the
  `compare()` tables for tiers race / interval / hard_effort, persons adrian + rise (+ patrik line), plus
  `production_curve` and `naive_best_recent` in the same table.
- Headline: race MAE adrian + rise (10 races; production 1.34, adrian 0.89, rise 1.78). Only 10 races: an
  improvement < ~0.3 VDOT is noise. Don't tune more than 2–3 knobs on the races; tune on the interval / hard_effort
  tiers or on leave-one-race-out, and say what was tuned on what. The interval tier is on production's own scale
  (home advantage for production-style models): use it as a sanity check.
- Every model returns an sd when it can (NLL / 1-sd coverage are reported).
- Write new files only: code + a short `results/<group>.md` in `research/shape-models/<group>/`, data artefacts in
  `data/_research/shape/<group>/`. Don't edit existing repo files, `common.py`, `harness.py` or the parquet files
  (copy and extend instead; if you need a new dataset column, derive it in your own module).
- Known data facts worth reusing: FR935 wrist HR under-reads hard reps (trust 0.3); 4315 trustworthy (2026-02 →);
  rise has a chest strap from 2024-10; GPS reads long by 0.2–2 % (rise's 2026-10-23 10k by 5 %); FR935 / patrik's
  3077 sample every 2–6 s; easy runs sit 8–25 VDOT below shape; adrian's Bislett 5ks 2025-10-24 were not races.

## Approach groups (one agent each; no overlap in what they own)

### G1. State-space core: shared fitness + distance curve variants (owner of the Kalman family)
Re-implement the production 2-state curve inside the harness (start from `harness._prod_state`) and test structural
variants: (a) separate short / long fitness states with a correlated random walk instead of θ + slope; (b) a 3-state
curve (θ, slope, curvature in log-distance) or slope tied to training volume; (c) local-linear-trend θ (velocity
state) vs random walk; (d) process noise that grows in training gaps / with low load (q_t as a function of
`run_h_28d`); (e) Student-t / skewed observation model instead of IRLS asymmetric weights. Deliverable: a table of
variants vs production on all tiers and a recommendation whether "one fitness + personal curve" beats "separate short
/ long fitness".

### G2. Hierarchical effects model: surface, incline, device, race bonus, conditions
One regression / mixed model on all effort observations (races, interval scores, best_gap efforts, rep segments):
`y = fitness_person(t) + curve(distance) + β_surface + β_terrain/grade + β_treadmill + β_indoor + β_race
+ β_workout_intent + β_temp + ε`, fitness(t) as a spline or random walk, partial pooling of β across persons
(statsmodels MixedLM or a hand-rolled Gaussian model refit per target day). Estimates the race-day bonus (adrenaline:
race vs best training equivalent), treadmill / indoor / track offsets, how much GAP over- or under-corrects
(residual vs grade), and the hot-weather penalty (weak: wrist temperature only). Output: effect sizes with CIs and a
predict_fn that uses them.

### G3. HR–pace efficiency (submaximal fitness) with device correction
Use steady and easy runs: per run, GAP speed at a fixed %HRR (e.g. 70 / 80 % from km segments or a per-run HR-speed
regression), EF (`ef_gap_per_hr`) and decoupling; correct per device (`hr_device` offsets, trust weights, strap vs
wrist, cadence/flag filters) and for drift, heat and duration. Map efficiency to VDOT by calibration on the person's
own prior efforts (rolling regression), then (a) use alone and (b) as an extra observation stream into the G1 curve.
Production rejected HR as a score input (FR935); the question is whether a careful device-aware version helps rise
(strap) and adrian's 4315 period, and patrik (few hard efforts).

### G4. Training-load response: Banister impulse-response and fitness–fatigue
Model performance as p(t) = p0 + k1·Σ w(t−s)e^(−(t−s)/τ1) − k2·Σ w(t−s)e^(−(t−s)/τ2) from daily load (TRIMP, km, all-sport
hours incl. ski) fitted to the effort observations up to each target day (per person, with priors / shrinkage; τ
grid). Variants: load in km vs TRIMP vs time; ski hours counted at a discount; the Busso variable-gain model. Use (a)
standalone, (b) as the drift term / prior mean of the G1 state-space (fitness predicted to change with load between
sparse hard sessions), (c) taper/form (TSB) effect on race day.

### G5. Upper envelope / censored performance models
Treat every run as a censored observation of capacity: a run's best efforts are ≤ capacity, with an unknown "effort
fraction". Approaches: rolling quantile regression of best_gap VDOT (τ 0.9–0.98) over time and distance; a Tobit /
censored Kalman filter where non-maximal runs are lower bounds with an effort-probability; extreme-value (Gumbel) fit
of per-week maxima. Compare envelope estimates from 1 k / 3 k / 5 k bests vs race results. Key for patrik and for
periods without races or interval sessions.

### G6. Machine learning on engineered features (GBM / RF / regularised linear)
Predict target VDOT (or its residual vs `production_curve`) from features computable on `hist`: recent interval
scores (last / median / max over 14 / 42 / 90 d), best efforts per distance with recency, prod curve θ and slope, load
(ctl, atl, tsb, run_km_28d), HR efficiency trend, days since hard / race, surface / terrain / workout of the target,
person id. Training set = all targets (any person) with day < target day (expanding window; refit e.g. monthly and
cache). LightGBM / XGBoost with monotone constraints, random forest, ridge / quantile GBM for sd. Report feature
importance and whether the residual model beats production on races without hurting intervals.

### G7. Evidence selection: which runs are informative (effort classification and weighting)
Learn p(maximal | run features) and per-evidence noise: label = residual vs the forward production estimate, or
"on the envelope" (within 1 VDOT of the person's later race-equivalent); features = workout, sig, rep structure,
hrr_last3 with device trust, Garmin TE, pace variability, time of year, together-with-others. Plug the learned weights
/ σ into the G1 filter (replace the asymmetric fixed rules and the HR-intent variant) and evaluate. Also: should easy /
steady runs enter at all (as bounds), and does dropping road intervals on hilly courses or GPS-glitch sessions help?

### G8. Distance–performance curve and the short vs long question
Personal speed–duration models from best efforts and reps: critical speed / D′ (Monod, 3-parameter), Riegel exponent
per person, power-law / Daniels-with-personal-%VO2max curve; time-varying CS and D′ ("strength at short vs long").
Compare to production's slope-per-doubling and Daniels' default; test whether interval sessions should sit at 5 km
or at a rep-structure-dependent distance; predict 5k / 10k / half from CS–D′ directly. Also owns the GAP / incline
question for the curve (Minetti-half vs Strava vs a per-person learned grade cost from repeated hilly routes).

### G9. Ensembling, calibration and the race-day layer (starts after G1–G8 have first results)
Stack / average the predict_fns of G1–G8 (simple mean, inverse-variance, stacking fitted only on past targets),
calibrate the sd (coverage of 1 / 2 sd on races and hard efforts), and model race-day effects that sit on top of any
fitness estimate: race bonus (from G2), course elevation, taper (tsb), first-race inexperience (rise's Bilbao half),
watch vs chip time. Produce the final comparison table and a recommendation for production.

## Suggested parallelisation
- Start together: G1, G2, G3, G4, G5, G6, G7, G8 (8 agents). G9 starts when ≥ 3 groups have a working predict_fn
  (it imports their modules: each group exposes `predict(person, hist, target)` in `research/shape-models/<group>/model.py`).
- Overlap boundaries: G1 owns the filter mechanics, G7 owns the per-evidence weights fed into it, G2 owns covariate
  effects (surface / race / temp), G8 owns the distance curve shape and GAP, G4 owns load-driven dynamics, G3 owns
  HR, G5 owns censoring / envelopes, G6 owns black-box learners.

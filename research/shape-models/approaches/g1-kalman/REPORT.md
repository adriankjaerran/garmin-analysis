# G1: Kalman core variants (shared fitness + distance curve vs separate short / long states)

Code (this folder): `g1.py` (engine + structures), `run_variants.py` (round 1), `grid.py` (hyperparameter grids +
leave-one-race-out), `run_round2.py` / `run_round3.py` (robust / load extras, time-forward auto-selection, extended
slope grid), `final_tables.py` (tables below), `model.py` (`predict` = recommended variant, `predict_production`).
`results/*.csv` hold metrics only. All numbers come from `harness.evaluate` (rolling origin, history < target day),
persons adrian + rise + patrik. Run: `/tmp/shape-venv/bin/python research/shape-models/approaches/g1-kalman/final_tables.py` (~3 min).

## Summary
- The G1 engine reproduces `production_curve` exactly (max |Δpred| 1e-13 over all 194 targets), in ~2 s.
- **"One shared fitness + personal distance curve" beats "separate short / long fitness".** Separate states with a
  correlated random walk (any ρ, no pull between them) or nearest-state "disciplines": race a+r 1.46–1.64 vs 1.34.
  Separate states only match production when the gap between them is mean-reverting, which *is* the curve model in
  another parametrisation.
- **The one change worth keeping: a more mobile slope** (AR(1) slope q_s 0.04 instead of 0.005, stationary sd 2.0 instead of
  1.5, so the slope forgets in ~100 days, not ~900). Race a+r **1.20** (adrian 0.87, rise 1.53) vs 1.34. Interval,
  hard_effort and patrik are unchanged. The q_s / sd region was picked by the history-only one-step forecast
  log-lik (not by the races). It beats production in 14 of the 15 grid cells with q_s 0.01–0.16 and sd 2–4,
  but the gain (0.14) is below the ~0.3 noise level and comes mostly from one race (rise Copenhagen Half, −2.95 → −1.19).
- Did not help: curvature (3-state), slope tied to training volume, local linear trend, load- or gap-dependent
  process noise, Student-t (symmetric or skewed) instead of the production asymmetric IRLS weights. All are within
  ±0.15 of production or worse.

## How the variants work
Common to all: evidence = production per-session items (`activities.prod_*`: interval sessions at x = 0, tempos at 14.5 km,
races at their distance, long-run lower bounds at their window distance), each with the production σ (race 1.0,
interval 1.5–2.0, bound 2.5). x = log2(km / 5). Linear-Gaussian state space, filtered event by event: a d-day gap
uses the exact d-fold daily transition F_d = F^d, Q_d = Σ F^i Q F^iᵀ. Observation y_j = h(x_j)ᵀ s_t + ε_j,
ε_j ~ N(0, σ_j² / w_j). Robust weights w_j by IRLS: pass 1 from the one-step forecast residual, later passes from the
RTS-smoothed residual (as production). The state is read at target day − 1, and the prediction at the target distance uses
production's rules: race-day sd 1.0 added, +1.0 sd per doubling outside the evidenced range, a positive slope held
beyond the longest evidence.

| variant | state s_t | dynamics (per day) | observation h(x) |
|---|---|---|---|
| **curve2** (production) | θ (5 km VDOT), b (slope per doubling) | θ RW, q_θ 0.05; b AR(1) to 0: b' = φb + η, var η = q_s, stationary sd σ_b (φ² = 1 − q_s/σ_b²) | [1, x] |
| **mobile slope** | same | q_s 0.04, σ_b 2.0 (production 0.005, 1.5) | [1, x] |
| SL anchors (separate short / long) | f_5k, f_HM (VDOT at x = 0 and x = 2.08) | correlated RW: Q = q·[[1, ρ], [ρ, 1]], optional pull of each anchor to the anchor mean at rate κ | linear interpolation / extrapolation between the anchors ("linear"), or only the nearest anchor ("nearest" = production v1's separate interval / long disciplines) |
| 3 anchors | f_5k, f_10k, f_HM | as SL, Q_ij = q ρ^abs(i−j) | piecewise linear |
| curve3 | θ, b, c | c AR(1) to 0 (sd 0.25–1.0) | [1, x, x²] |
| curvevol | θ, b, β (static) | β constant (prior N(0, σ_β²)) | [1, x, x·z_t], z_t = log(1 + run h 28 d), centred on the 2-y mean: slope rises with volume |
| LLT | θ, v, b | θ' = θ + v; v AR(1) (sd 0.005–0.02/day, τ 30–90 d) | [1, 0, x] |
| load / gap q | curve2 | q_θ,t = q_θ (1 + a·max(0, 1 − h28/h_ref)) (low load) or + a·max(0, h28/h_ref − 1) (high load), h_ref = person's median; or + q_gap on days with no run in 7 d | [1, x] |
| Student-t | curve2 | – | w = 1 / (1 + (z/c)²/ν) (EM weight of a t-likelihood, normalised to 1 at z = 0), z = residual/sd; skewed: separate ν below (sub-maximal sessions) and above the curve, bounds as production |
| auto | curve2 | at every target day choose (q_s, σ_b) from a 35-cell grid by the pooled (all persons, history < day) one-step-ahead forecast log-lik of race + hard items | |

Relation between the two families: curve2 in anchor coordinates is a correlated RW (production: ρ ≈ 0.84 between the 5 k
and HM daily shocks) plus mean reversion of the HM − 5 k gap towards 0 (time constant ~900 d). "Separate short /
long" with ρ < 1 and no reversion lets the gap random-walk freely; with reversion it becomes the curve again.

## Results (harness, MAE in VDOT; n predicted)
Race bands for adrian + rise: 5k = 1 (adrian Øya 2021), 10k = 5, HM = 4. Interval targets are on production's own
scale. Patrik's only race (Lier 10k 2023, after easy running only) is ~10.9 low for every variant.

| production_flat | 1.58 | 1.94 | **1.76** | -1.20 | 0.05 | 1.46 | 2.55 | 2.45 / 0.55 | 10.87 | 2.25 | 2.28 | 3.52 | 4.28 |
| G1 curve2 = production re-impl. | 0.89 | 1.78 | **1.34** | -0.26 | 0.05 | 1.17 | 1.88 | 2.09 / 0.64 | 10.87 | 2.25 | 2.28 | 3.41 | 4.18 |
| SL: separate 5k/HM states, rho 0.8, no reversion | 0.76 | 2.21 | **1.48** | -0.12 | 0.05 | 1.14 | 2.26 | 2.15 / 0.55 | 10.87 | 2.26 | 2.28 | 3.54 | 4.26 |
| SL: rho 0, reversion 1/200 d | 0.79 | 1.67 | **1.23** | -0.39 | 0.01 | 1.20 | 1.58 | 2.08 / 0.64 | 10.87 | 2.25 | 2.26 | 3.44 | 4.24 |
| SL: rho 0.5, reversion 1/900 d | 0.74 | 1.93 | **1.33** | -0.11 | 0.01 | 1.16 | 1.89 | 2.09 / 0.64 | 10.87 | 2.25 | 2.28 | 3.49 | 4.23 |
| SL nearest (separate disciplines), rho 0.5 | 0.72 | 2.19 | **1.46** | -0.13 | 0.05 | 1.36 | 1.93 | 2.24 / 0.64 | 10.87 | 2.27 | 2.30 | 3.47 | 4.19 |
| 3 anchors 5k/10k/HM, rho 0.8, rev 1/900 | 0.96 | 1.60 | **1.28** | -0.03 | 0.00 | 1.03 | 1.91 | 2.20 / 0.64 | 10.87 | 2.24 | 2.26 | 3.50 | 4.25 |
| curve3 (curv sd 0.5, q 5e-4) | 0.86 | 1.73 | **1.30** | -0.24 | 0.05 | 1.13 | 1.82 | 2.09 / 0.64 | 10.87 | 2.25 | 2.27 | 3.42 | 4.19 |
| curvevol (slope ~ load, b sd 0.5) | 0.87 | 1.88 | **1.38** | -0.28 | 0.05 | 1.18 | 1.96 | 2.11 / 0.64 | 10.87 | 2.25 | 2.28 | 3.40 | 4.17 |
| LLT (vel sd 0.01, tau 30, q_theta 0.02) | 0.89 | 1.87 | **1.38** | -0.30 | 0.36 | 1.19 | 1.87 | 2.11 / 0.55 | 10.87 | 2.22 | 2.25 | 3.42 | 4.24 |
| load: q up when load low (a 1) | 0.94 | 1.84 | **1.39** | -0.28 | 0.13 | 1.20 | 1.94 | 2.10 / 0.73 | 10.87 | 2.25 | 2.28 | 3.41 | 4.18 |
| load: q up when load high (a 1) | 0.94 | 1.66 | **1.30** | -0.25 | 0.35 | 1.09 | 1.80 | 2.07 / 0.64 | 10.87 | 2.27 | 2.30 | 3.43 | 4.20 |
| gap: q +0.1/day after 7 d without run | 1.00 | 1.80 | **1.40** | -0.26 | 0.30 | 1.23 | 1.89 | 2.10 / 0.73 | 10.87 | 2.26 | 2.29 | 3.41 | 4.18 |
| Student-t symmetric nu 4 | 1.03 | 1.99 | **1.51** | -0.57 | 0.11 | 1.34 | 2.08 | 2.16 / 0.55 | 10.87 | 2.19 | 2.21 | 3.60 | 4.25 |
| skew-t hard nu(1 below, 30 above) | 1.02 | 1.60 | **1.31** | -0.02 | 0.47 | 0.99 | 1.92 | 2.08 / 0.64 | 10.87 | 2.27 | 2.30 | 3.40 | 4.25 |
| no robust weights (Gaussian) | 1.19 | 1.91 | **1.55** | -0.49 | 0.30 | 1.24 | 2.25 | 2.15 / 0.45 | 10.87 | 2.23 | 2.25 | 3.59 | 4.25 |
| **mobile slope (q_s 0.04, sd 2.0)** | 0.87 | 1.53 | **1.20** | -0.18 | 0.05 | 1.22 | 1.46 | 2.12 / 0.73 | 10.87 | 2.26 | 2.28 | 3.39 | 4.16 |
| auto: curve2 slope grid by history fc log-lik | 0.87 | 1.69 | **1.28** | -0.18 | 0.04 | 1.23 | 1.66 | 2.18 / 0.73 | 10.87 | 2.25 | 2.28 | 3.40 | 4.18 |
| mobile slope + skew-t | 1.01 | 1.39 | **1.20** | 0.10 | 0.47 | 1.05 | 1.56 | 2.13 / 0.73 | 10.87 | 2.29 | 2.32 | 3.41 | 4.23 |
| mobile slope + load-high q | 0.92 | 1.49 | **1.21** | -0.13 | 0.35 | 1.13 | 1.52 | 2.14 / 0.73 | 10.87 | 2.28 | 2.31 | 3.45 | 4.20 |

Per-race error (pred − actual, VDOT):

| model | adrian 5k Øya 2021 | adrian Lier 10k 2023 | adrian Copenhagen Half 2025 | adrian Bislett 10k 2025 | adrian Lier 10k 2025 | rise Bilbao Half 2024 | rise Copenhagen Half 2025 | rise Lier 10k 2025 | rise Oslo Half 2026 | rise 10k 2026-10-23 |
|---|---|---|---|---|---|---|---|---|---|---|
| production_curve (harness) | -0.05 | +1.02 | -0.91 | +0.77 | -1.72 | +3.63 | -2.95 | -0.94 | -0.01 | -1.40 |

Patrik / per-band of the other tiers (production → mobile slope): interval patrik 3.32 → 3.31 (4), hard_effort
patrik 7.07 → 7.04 (4), hard_effort adrian short 4.10 → 4.09 (11), 10k 0.54 → 0.04 (1), HM+ 0.09 → 0.46 (1); rise
hard_effort 10k 3.92 → 3.92, short 1.54 → 1.54. Interval NLL 3.43 → 3.41, 1-sd coverage 0.46 → 0.48. Race 1-sd
coverage 0.64 → 0.73 (sd slightly wider for long races).

Other runs (full lists in `results/variants_all.csv`, `round2_fixed.csv`, `grid_*.csv`), race a+r / interval a+r / hard a+r:
- SL linear, no reversion: ρ 0 1.50, 0.5 1.47, 0.8 1.48, 0.95 1.47, 1.0 1.64 (ρ = 1 = flat curve with one shock).
  SL nearest: ρ 0 1.46, 0.5 1.46, 0.8 1.49, 0.95 1.53. SL linear with reversion: best cell ρ 0, κ 1/200 1.23; grid of 24
  (ρ 0/0.5/0.8 × κ 1/100…1/900 × q 0.03/0.05) spans 1.23–1.70, median 1.46.
- 3 anchors: ρ 0.5 1.83, 0.8 1.76, 0.95 1.52; with reversion 1/900 1.28.
- curve3: 1.30–1.46 (curvature sd 0.25–1.0); the bigger the curvature, the worse for rise.
- curvevol: β sd 0.5 1.38, 1.0 1.50, 2.0 1.71: adrian improves (0.87 → 0.73), rise gets much worse (1.88 → 2.70).
- LLT: 12 cells 1.34–1.40; interval 2.22–2.27.
- Load-low q (a 0.5–4) 1.37–1.47, monotone worse; gap q 1.39–1.40; load-high q (a 1, 2) 1.30; q_θ 0.02 / 0.03 / 0.08 / 0.12:
  1.39 / 1.36 / 1.35 / 1.32 (q_θ lower: adrian and intervals better, rise worse).
- Robust: Student-t ν 4 sym 1.51, Cauchy 1.56, skewed ν(1,4) 1.45, ν(2,6) 1.41, ν(1,30) 1.31, ν(0.5,30) 1.33, no robust
  weights 1.55. Production rule grid (hard below cutoff 0.5/1/1.5/2.5 σ × above 2/2.5/4 σ × race 2/2.5/4): 1.22–1.49; the
  below-cutoff matters most (0.5 σ and 2.5 σ both worse), race cutoff has no effect, above 4 σ helps rise (1.23).

## Sensitivity: slope dynamics (curve2, the recommended knob)
Race MAE adrian + rise (10 races) over q_s (rows) × stationary slope sd σ_b (columns); production = (0.005, 1.5):

| q_s \ σ_b | 1.0 | 1.5 | 2.0 | 3.0 | 4.0 |
|---|---|---|---|---|---|
| 0.001 | 1.54 | 1.51 | 1.49 | 1.55 | 1.59 |
| 0.005 | 1.45 | *1.34* | 1.34 | 1.41 | 1.45 |
| 0.01 | 1.45 | 1.30 | 1.25 | 1.33 | 1.38 |
| 0.02 | 1.49 | 1.31 | 1.17 | 1.24 | 1.30 |
| 0.04 | 1.53 | 1.35 | **1.20** | 1.14 | 1.22 |
| 0.08 | 1.60 | 1.40 | 1.26 | 1.14 | 1.12 |
| 0.16 | 1.67 | 1.49 | 1.30 | 1.22 | 1.14 |

History-only criterion (mean one-step forecast log-lik of race + hard evidence items, adrian and rise averaged; higher = better):
best −2.442 at (0.02–0.04, 1.5–2.0), production −2.452, worst −2.481 (0.001, 4.0). The surface is flat (0.01 per
item), so it is a weak selector, but it points to the same diagonal as the races: the slope should move faster than
production allows. adrian is flat everywhere (0.79–1.15; 0.85–0.87 on the recommended diagonal); the gain is rise's
(1.79 → 1.45–1.53). Interval MAE is 2.24–2.26 in every cell (the slope doesn't touch x = 0), hard_effort 3.37–3.51.

Honest tuning estimates:
- Time-forward auto-selection (history log-lik picks (q_s, σ_b) each day from the 35-cell grid; mostly (0.04, 2.0) and (0.16, 1.5)): **1.28**
  (adrian 0.87, rise 1.69). On the 20-cell grid (q_s ≤ 0.04) it picked (0.04, 2.0) most often (89 target days): 1.20.
- Leave-one-race-out "tune on 9 races, score the 10th": curve2 grid 1.26, SL grid 1.34, robust-rule grid 1.39
  (production 1.34). Tuning the SL or robust knobs on the races doesn't generalise; the slope-dynamics knob a little.

## What worked, what didn't, and why (as far as 10 races allow)
- **Shared level, personal slope (production structure): keep.** Intervals (most of the evidence, at 5 km) carry the
  common fitness signal to 10k / half through the shared level. With separate short / long states the long state
  only learns from the few races / tempos and from the correlation, so it goes stale between long races. Freely diverging states hurt rise (2.07–2.39) but help adrian (0.60–0.76 vs 0.89: his 10k / half run ahead of his 5 km level, and independent states let them), so the pooled result is worse, and the adrian gain is 5 races.
- **Faster slope: small, consistent gain.** Production's slope keeps a distance profile ~900 days. The forecast
  likelihood and the races both prefer ~100–200 days with a somewhat larger stationary sd: the 10k / half level
  follows recent long evidence (rise's half races; adrian's 2025 autumn 10ks) without dragging the 5 km level along.
  Side effect: without long evidence the slope falls back towards 0 faster and its sd grows to 2.0. adrian today: slope +0.2 ± 2.0
  (production +0.8 ± 1.2), HM 55.0 vs 56.3. rise: +0.85 ± 1.1 (production +0.56 ± 0.66). Long-distance bands get wider.
- **Robust weights: production's asymmetric rule is better than Student-t.** A symmetric t (or no robustness) lets
  sub-maximal interval sessions pull the level down (bias −0.5); production's "full weight to 1 σ below, then
  (1/z)²; full weight to 2.5 σ above" is close to optimal in the rule grid. The best t variant (heavy-tailed below, ν 1;
  near-Gaussian above, ν 30) ties production (1.31) and adds nothing on top of the mobile slope (1.20 → 1.20).
- **Load-dependent q: no.** More noise in low-load periods / gaps makes the filter forget good evidence after
  holidays and injuries, and doesn't help predict the race after them (1.37–1.47). More noise in high-load blocks
  helps rise slightly (1.30) and costs adrian, with no gain on top of the mobile slope (1.21). G4's load-driven
  drift (mean, not variance) is the better place for load.
- **3-state curvature, slope ~ volume, LLT velocity: no.** Too few long races to identify a curvature or a volume
  gain (curvevol: adrian ↑, rise ↓↓). A velocity state extrapolates recent trends; it adds noise at race horizons.

## Recommendation
1. Keep "one shared fitness + personal distance curve" (θ + b·log2(km/5)). Don't go back to separate short / long
   fitness states.
2. Candidate production change, low risk: slope AR(1) **q_slope 0.04, slope_prior_sd 2.0** (`CURVE`). Race a+r
   1.34 → 1.20 (adrian 0.89 → 0.87, rise 1.78 → 1.53), intervals / hard efforts / patrik unchanged. Below the noise level on
   10 races, but chosen by a history-only criterion and stable over a broad region (any q_s 0.01–0.16 with sd 2–4 is
   1.12–1.38). Before adopting it, check how the wider long-distance band looks in the app, and re-check with G9's
   ensemble. `model.py` exposes it as `predict`.
3. Keep production's IRLS asymmetric weights and constant q_θ 0.05. Don't use Student-t or load / gap-dependent
   process noise.

Notes: PLAN.md asks for `<group>/model.py` and `results/<group>.md`. Per the task brief, everything sits in
`approaches/g1-kalman/` (`model.py`, `REPORT.md`). The engine (`g1.fit(person, day, cfg)`, `g1.read`) takes any
evidence of the same form, so G3 / G7 streams can be plugged in by extending `person_data`.

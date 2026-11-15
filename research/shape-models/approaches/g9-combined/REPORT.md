# G9: combined candidate model, ensembles and robustness

## 1. Pre-registered specification (written 2026-11-05 before any G9 score was computed)

Everything below was fixed before the combined model or any ablation, tempo variant or ensemble was scored.
Round-1 race numbers were known (they're in the round-1 reports); no G9 knob is chosen on races.

**Combined model `C` = production curve + B + S + M + E** (code: `combined.py`, engine = G1's re-implementation of
production, `g1.py`, which reproduces `production_curve` exactly).

| component | what | setting (source) |
|---|---|---|
| P (base) | production evidence stream (interval scores at 5 km, tempos at 14.5 km +3, races, long-run bounds), production σ, asymmetric IRLS robust weights, q_θ 0.05, prior sd 6, read at day − 1, race sd 1.0, +1 sd per doubling outside the evidenced range, positive slope held beyond the longest evidence | production / G1 |
| B | censored best-effort stream (G5): per pace_ok, usable, non-suspect run **that has no production hard/race item** (no double counting of interval sessions, tempos or races), the max-VDOT `best_gap` effort ≥ 1 mile; y = VDOT − 1.0 (GPS bias), at its own distance; likelihood π·N(0, 2²) + (1 − π)·Uniform-shortfall(0, 20) + 2 % flat outlier; π from G5's workout table + HR gate. Folded into the Kalman filter by exact 1-D moment matching (assumed-density update along h = [1, x]); IRLS weights are not applied to it | G5 (`min_m` 1600, `gps_bias` 1.0, U 20, σ 2, p_out 0.02) |
| S | faster slope: AR(1) q_s 0.04, stationary sd 2.0 | G1 (chosen on history log-lik) |
| M | shared slope mean +0.75 VDOT per doubling: the slope's prior and its AR(1) reverts to +0.75 instead of 0 | G8 (+0.5 … +1; midpoint, fixed a priori) |
| E | hard_effort read-out only: subtract the person's typical shortfall of the same kind (tempo vs other) = median over earlier hard_effort targets of max(0, model − actual), shrunk with 2 pseudo-observations to 0 (G5 rule). Doesn't touch race or interval predictions | G5 |

**Ablations** (each scored on all tiers): production; production + one of {B, S, M, E}; C minus one of {B, S, M, E};
sensitivity of M at +0.5 and +1.0 (reported, not used to pick).

**Tempo placement (task 2), two variants inside C, fixed beforehand:**
- `T1` own distance + learned shortfall: each production tempo enters at its own window distance
  (`prod_gap_speed × prod_window_s`) with y = window VDOT (= prod_score − 3) and a per-person static shortfall state δ
  (third state, prior N(2.0, 2.0²), no dynamics): y = θ + b·x − δ + ε. Prior 2.0 = production's implied shortfall at
  the window distance (+3 at 14.5 km minus ~1 VDOT of curve between ~6 and 14.5 km).
- `T2` tempo as censored best only: production tempo items dropped; tempo runs enter only through stream B (π 0.35).

**Ensembles (task 3), fixed beforehand:** members = production + round-1 `model.predict` of G1, G2, G3, G4, G6, G7,
G8 + G5's recommended tobit (`min_m` 1600 + tempo shortfall; G5 has no `model.py`).
`ens_mean_all` (mean of all 9), `ens_median_all`, `ens_ivw_all` (inverse-variance weights from each member's sd),
`ens_prod_g5` (mean of production and G5), `ens_C_g5` (mean of C and G5), `ens_C_prod` (mean of C and production).
Abstentions: a member that abstains is left out of the mean for that target.

**Robustness (task 4):** per-race error table; paired sign test and paired bootstrap (10 000 resamples; race: over
races; interval / hard_effort: over targets, interval also with 90-day block bootstrap) of MAE(C) − MAE(production);
leave-one-race-out influence (MAE difference with each race removed) and leave-one-race-out *selection* (pick the best
of the candidate list on 9 races, score the 10th); calibration = fraction of targets within ±1 sd and ±2 sd; results
with rise's Bilbao Half 2024 and Copenhagen Half 2025 removed.

**Decision rule (fixed beforehand):** adopt C (or a component) only if race MAE a+r improves on production, it wins
≥ 7 of 10 races or the bootstrap 90 % interval of the MAE difference excludes 0, interval and hard_effort don't get
worse by > 0.1, and the gain doesn't vanish without rise's two halves. Otherwise adopt only components that are
neutral-or-better on every tier and justified without race scores ("adopt parts").

Implementation note (after writing the spec, before scoring): `combined.py` with every component off reproduces
`production_curve` to 1e-13 on all 203 targets (a first draft initialised the level differently when only long-run
bounds exist, which moved patrik's race; fixed to production's rule before any comparison was used).

## 2. Files and how to run (repo root, `/tmp/shape-venv/bin/python`)
- `combined.py`: the model (`make(Spec)`, `SPEC` = pre-registered C, `PROD` = production, `predict`).
- `run_eval.py`: C, ablations, tempo variants (~1.5 min) → `results/configs.csv`, `data/_research/shape/g9/configs.parquet`.
- `run_members.py <member>`: round-1 `model.predict` members → `data/_research/shape/g9/members/*.parquet` (all
  reproduce their round-1 headline numbers).
- `analyze.py`: ensembles, paired tests, per-race table, leave-one-race-out → `results/{ensembles,robustness,per_race,loro_influence}.csv`.
- `today.py`: estimates for 2026-11-05 → `results/today.csv`. `results/without_rise_halves.csv`: race MAE with / without rise's halves for every model.

## 3. Results (harness, MAE in VDOT; race = adrian + rise, 10 races; interval 164, hard_effort 19 targets, all persons)

| model | race adrian | race rise | **race a+r** | race patrik | race bias | race ±1sd / ±2sd | interval | hard_effort |
|---|---|---|---|---|---|---|---|---|
| production | 0.89 | 1.78 | **1.34** | 10.87 | −0.26 | 0.70 / 1.00 | 2.28 | 4.18 |
| P + B (bests) | 0.93 | 1.62 | 1.28 | 8.01 | −0.07 | 0.70 / 1.00 | 2.27 | 3.76 |
| P + S (fast slope) | 0.87 | 1.53 | 1.20 | 10.87 | −0.18 | 0.80 / 1.00 | 2.28 | 4.16 |
| P + M (slope mean +0.75) | 0.87 | 1.75 | 1.31 | 9.89 | −0.10 | 0.70 / 1.00 | 2.29 | 4.07 |
| P + E (hard_effort read-out) | 0.89 | 1.78 | 1.34 | 10.87 | −0.26 | 0.70 / 1.00 | 2.28 | 3.95 |
| **C (pre-registered)** | 0.97 | 1.30 | **1.13** | 7.13 | +0.15 | 0.80 / 1.00 | 2.27 | **3.43** |
| C − B | 0.86 | 1.37 | 1.12 | 9.89 | +0.12 | 0.80 / 1.00 | 2.29 | 3.69 |
| C − S | 0.99 | 1.68 | 1.33 | 7.13 | +0.10 | 0.80 / 1.00 | 2.27 | 3.41 |
| C − M | 0.90 | 1.58 | 1.24 | 8.01 | −0.09 | 0.80 / 1.00 | 2.27 | 3.53 |
| C − E | 0.97 | 1.30 | 1.13 | 7.13 | +0.15 | 0.80 / 1.00 | 2.27 | 3.77 |
| C, M = +0.5 / +1.0 | 0.95 / 0.99 | 1.39 / 1.26 | 1.17 / 1.13 | 7.43 / 6.84 | | 0.80 / 1.00 | 2.27 / 2.26 | 3.45 / 3.40 |
| **C + T1** (tempo own distance + learned shortfall δ) | 0.87 | 1.19 | **1.03** | 7.13 | −0.19 | 0.90 / 1.00 | 2.25 | 3.52 |
| C + T2 (tempo only as censored best) | 1.05 | 1.01 | 1.03 | 7.13 | −0.85 | 0.90 / 1.00 | 2.24 | 3.55 |

Ablation reading:
- **S (fast slope) carries the whole race gain**: C − S 1.33 ≈ production; P + S alone 1.20.
- **B (censored bests) does nothing for races** (C − B 1.12 vs C 1.13; P + B 1.28 with only 3 of 10 race wins). Its
  value is elsewhere: hard_effort 4.18 → 3.76 alone, patrik's race 10.9 → 8.0 (7.1 in C). It is weaker than inside
  G5 (patrik 3.4) because production's long-run bounds pin patrik's level before the bests can lift it.
- **M (+0.75 slope mean)** is neutral-to-slightly-better everywhere; +0.5 / +1.0 change little (not used to pick).
- **E** only touches hard_effort (3.77 → 3.43 in C).
- **Tempo placement:** T1 learns δ (today adrian 1.8 ± 1.0, rise 0.9 ± 1.3, patrik 2.4 ± 1.3; prior 2 ± 2). Both T1
  and T2 reach 1.03. T1 keeps the bias near 0 and is the only variant that also improves without rise's halves (below).
  T2 runs 0.85 low (censoring tempos throws away their information at the long end). Inside C the sign problem G7 saw
  (rise's slope turning negative with tempos at their own distance) doesn't appear: the learned δ absorbs it.

Ensembles (members = production + round-1 `model.predict` of G1–G4, G6–G8 + G5's recommended tobit):

| ensemble | race adrian | race rise | race a+r | race patrik | race ±1sd / ±2sd | interval | hard_effort |
|---|---|---|---|---|---|---|---|
| mean of all 9 | 0.86 | 1.56 | 1.21 | 9.94 | 0.80 / 1.00 | **2.23** | 4.07 |
| median of all 9 | 0.91 | 1.80 | 1.35 | 10.87 | 0.70 / 1.00 | 2.27 | 4.17 |
| inverse-variance, all | 0.85 | 1.71 | 1.28 | 9.98 | 0.30 / 0.60 | 2.22 | 4.05 |
| mean(production, G5) | 0.91 | 1.11 | 1.01 | 7.15 | 0.90 / 1.00 | 2.22 | 3.73 |
| mean(C, G5) | 1.02 | 0.83 | 0.93 | 5.28 | 0.90 / 1.00 | 2.24 | 3.35 |
| mean(C, production) | 0.89 | 1.54 | 1.22 | 9.00 | 0.80 / 1.00 | 2.23 | 3.76 |
| (members: G5 alone) | 1.08 | 0.78 | 0.93 | 3.43 | 0.80 / 1.00 | 2.23 | 3.30 |

Most non-G5 members are near-copies of production: the median |difference| per target is ≤ 0.1 VDOT for G1, G2, G4, G7 and G8,
and about 0.4 for G3 and G6. Averaging them mostly averages noise. Every ensemble that beats 1.2 on races contains G5, and G5's race gain is rise's two halves
(below). The inverse-variance ensemble is overconfident (30 % within 1 sd).

## 4. Robustness

Paired vs production (diff = MAE(model) − MAE(production); bootstrap 90 % CI over targets; interval also with 90-day blocks):

| model | race diff [90 % CI] | race wins–losses | interval diff [block CI] | hard_effort diff [CI] | hard wins–losses |
|---|---|---|---|---|---|
| P + S | −0.14 [−0.49, +0.15] | 5–5 | +0.01 [−0.01, +0.03] | −0.02 [−0.09, +0.06] | 10–7 |
| P + B | −0.06 [−0.41, +0.24] | 3–7 | −0.00 [−0.10, +0.09] | −0.42 [−1.06, +0.11] | 13–6 |
| C | −0.21 [−0.71, +0.19] | 4–6 | −0.01 [−0.10, +0.09] | −0.75 [−1.61, +0.04] | 13–6 |
| C − B | −0.22 [−0.66, +0.10] | 6–4 | +0.01 [−0.01, +0.04] | −0.49 [−1.17, +0.16] | 13–6 |
| C + T1 | −0.31 [−0.64, +0.01] | 6–4 | −0.02 [−0.11, +0.07] | −0.66 [−1.52, +0.14] | 13–6 |
| C + T2 | −0.31 [−0.83, +0.14] | 5–5 | −0.04 [−0.14, +0.05] | −0.63 [−1.49, +0.19] | 13–6 |
| G5 | −0.41 [−1.25, +0.37] | 7–3 | −0.05 [−0.14, +0.04] | −0.88 [−1.73, −0.07] | 12–7 |
| mean of all 9 | −0.13 [−0.27, +0.01] | 6–4 | −0.05 [−0.09, −0.02] | −0.11 [−0.25, +0.03] | 13–6 |
| mean(C, G5) | −0.41 [−1.00, +0.14] | 6–4 | −0.04 [−0.13, +0.05] | −0.83 [−1.65, −0.04] | 12–7 |

No race comparison is significant (all sign-test p ≥ 0.34; every race CI includes 0). The only CIs that exclude 0:
G5 and mean(C, G5) on hard_effort, and the mean-of-all ensemble on intervals (−0.05, production's own scale).

Per-race error (pred − actual, VDOT):

| race | actual | production | P+S | C | C−B | C+T1 | C+T2 | G5 | mean all |
|---|---|---|---|---|---|---|---|---|---|
| adrian 5k Øya 2021 | 51.35 | −0.05 | −0.05 | −0.86 | −0.05 | −0.86 | −0.86 | −1.49 | −0.20 |
| adrian Lier 10k 2023 | 53.29 | +1.02 | +1.19 | +1.08 | +1.28 | +0.69 | −0.17 | +0.93 | +0.72 |
| adrian Copenhagen Half 2025 | 58.78 | −0.91 | −0.56 | +0.22 | −0.34 | +0.14 | −1.54 | +0.40 | −0.73 |
| adrian Bislett 10k 2025 | 57.09 | +0.77 | +0.60 | +0.95 | +0.76 | +0.91 | +0.90 | +0.61 | +0.77 |
| adrian Lier 10k 2025 | 59.37 | −1.72 | −1.95 | −1.75 | −1.88 | −1.76 | −1.76 | −1.98 | −1.91 |
| rise Bilbao Half 2024 | 45.20 | +3.63 | +3.32 | +4.19 | +3.62 | +2.47 | −0.97 | −0.37 | +3.09 |
| rise Copenhagen Half 2025 | 51.51 | −2.95 | −1.19 | −0.45 | −0.68 | −1.59 | −2.22 | −0.12 | −2.37 |
| rise Lier 10k 2025 | 51.72 | −0.94 | −0.88 | −0.72 | −0.70 | −0.75 | −0.75 | −0.92 | −1.06 |
| rise Oslo Half 2026 | 49.59 | −0.01 | −0.76 | −0.17 | +0.52 | −0.17 | −0.17 | +2.00 | +0.14 |
| rise 10k 2026-10-23 | 52.20 | −1.40 | −1.48 | −0.95 | −1.32 | −0.95 | −0.95 | +0.50 | −1.13 |
| patrik Lier 10k 2023 (not in a+r) | 49.06 | −10.87 | −10.87 | −7.13 | −9.89 | −7.13 | −7.13 | −3.43 | −9.94 |

**Dependence on rise's Bilbao / Copenhagen halves** (race MAE on the other 8 races; production 0.85):
C 0.84, C − B 0.86, P + S 0.93, C + T1 **0.78**, C + T2 0.89, G5 1.10, mean(C, G5) 0.89, mean of all 0.83.
Without those two races every candidate is within ±0.1 of production except G5 (worse) and C + T1 (0.07 better).
C's race gain is rise's Copenhagen Half (−2.95 → −0.45); G5's is both halves (it even gets worse elsewhere).

**Leave-one-race-out.** Influence: C − production on the other 9 races ranges −0.32 … +0.05 (+0.05 when Copenhagen is
left out); C + T1 is negative in all 10 folds (−0.19 … −0.43). Selection: picking the best model on 9 races and
scoring the 10th gives 1.41 when picking among the 14 configs and 1.67 when picking among configs + members +
ensembles, both *worse* than just using production (1.34). Choosing between these candidates by race scores doesn't
generalise.

**Calibration** (fraction within ±1 sd / ±2 sd): races production 0.70 / 1.00, C 0.80 / 1.00, C + T1 0.90 / 1.00 (race
sds slightly conservative, fine for 10 races). Intervals: all models ~0.47 / 0.77 (sd too narrow for single sessions;
same for production). Hard efforts: production 0.47 / 0.68; adding B narrows the sd and drops 1-sd coverage to
0.26–0.32 at the same 2-sd coverage. If B is adopted, its hard-effort / session sd needs widening.

## 5. Today (2026-11-05; history to 2026-11-04). VDOT ± sd (predicted time, ±1 sd range)

| person | model | 5k | 10k | HM |
|---|---|---|---|---|
| adrian | production | 54.6 ± 1.3 (18:29, 18:06–18:52) | 55.4 ± 1.8 (37:51, 36:50–38:55) | 56.3 ± 2.8 (1:22:36, 1:19:12–1:26:20) |
| adrian | C = C + T1 | 54.4 ± 1.3 (18:32, 18:10–18:56) | 55.5 ± 2.2 (37:47, 36:34–39:06) | 56.7 ± 3.8 (1:22:05, 1:17:34–1:27:12) |
| rise | production | 50.9 ± 1.4 (19:38, 19:11–20:07) | 51.5 ± 1.5 (40:20, 39:23–41:20) | 52.1 ± 1.8 (1:28:23, 1:25:50–1:31:05) |
| rise | C = C + T1 | 50.4 ± 1.4 (19:48, 19:20–20:17) | 51.8 ± 1.6 (40:04, 39:02–41:10) | 53.4 ± 2.3 (1:26:29, 1:23:20–1:29:54) |
| patrik | production | 49.0 ± 1.5 (20:17, 19:47–20:49) | 48.2 ± 1.6 (42:37, 41:26–43:52) | 47.4 ± 2.4 (1:35:47, 1:31:47–1:40:10) |
| patrik | C | 48.3 ± 1.4 (20:31, 20:01–21:02) | 48.5 ± 1.7 (42:23, 41:10–43:41) | 48.6 ± 2.1 (1:33:43, 1:30:19–1:37:24) |
| patrik | C + T1 | 48.5 ± 1.4 (20:28, 19:58–20:59) | 49.8 ± 2.1 (41:28, 40:01–43:02) | 49.8 ± 2.4 (1:31:49, 1:28:11–1:35:47) |

C and C + T1 agree to 0.01 VDOT for adrian and rise today (their tempos are old and races dominate). Slopes today
(C): adrian +1.09 ± 1.72, rise +1.43 ± 0.98, patrik +0.20 ± 1.06 VDOT per doubling. The models differ mainly at
HM: C is +0.4 (adrian) and +1.3 (rise) above production, with a wider adrian band (the faster slope forgets the
long end in ~100 days; adrian's last long evidence is from autumn 2025). Caveat for rise: C's HM 53.4 sits well
above her two most recent halves (Oslo 2026 49.6, Copenhagen 2025 51.5); it follows her 10k results (51.7, 52.2)
and the positive slope prior. Production's 52.1 is the more conservative HM number. 5k / 10k differ by ≤ 0.5 VDOT
(≤ 10 s / 5k) between the models for adrian and rise.

## 6. Recommendation: adopt parts, not C as a whole

The pre-registered decision rule fails for C: it improves race MAE 1.34 → 1.13 but wins only 4 of 10 races, the
bootstrap CI includes 0, and without rise's two halves it ties production (0.84 vs 0.85). None of the ensembles or
variants clears the rule either, and picking among them by race score does worse than production out of sample (1.41 / 1.67).

1. **Don't replace production with C or with an ensemble.** Ensembles of round-1 members add complexity for
   −0.05 on intervals; the ones that look good on races do so through G5 on rise's two halves.
2. **Adopt the censored best-effort stream (B), with a wider sd, for non-race use**: hard efforts (−0.4 alone,
   −0.75 in C), people without hard evidence (patrik 10.9 → 7.1), neutral on races and intervals. Its justification
   doesn't rest on races. Before production use, widen the predictive sd where B dominates (1-sd hard_effort coverage
   fell to ~0.3), and avoid double counting (only runs without a production hard / race item, as here).
3. **Adopt the shared slope mean +0.75 (M)**: neutral on all tiers, chosen a priori (G8's short-only test), helps
   short-only histories. Re-check `interval_offset` alongside it.
4. **Faster slope (S): optional, not proven.** It is the only component that moves races (1.34 → 1.20), but the
   gain is rise's Copenhagen Half; without rise's halves P + S is 0.93 vs 0.85. If adopted (G1's history-likelihood
   argument), accept wider HM bands.
5. **Tempo placement T1 (own distance + learned δ): the most promising next step, run it in shadow mode.** It is the most
   consistent variant (best race MAE 1.03, negative in all 10 leave-one-out folds, best without rise's halves 0.78,
   race 1-sd coverage 0.90, intervals 2.25), and it removes the arbitrary 14.5 km +3 rule. The evidence is still 10
   races (CI −0.64 … +0.01), so log its predictions next to production's for the next races before switching.
6. **E (hard-effort shortfall read-out)**: use it only when displaying "expected tempo / hard effort result". It is not a
   fitness change.

Not done / open: no stacking with learned weights (10 races are too few, and LORO selection already shows overfitting);
race-day layer (taper, course elevation, chip vs watch) not re-tested beyond G2 / G4's null results; T1 + B's sd
calibration not tuned.

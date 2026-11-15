# G11: A1 fix, rebuilt dataset, re-scoring and adrian's short-distance problem

Files (this folder, run from the repo root with `/tmp/shape-venv/bin/python`):
- `cands.py`: the G9 candidates on the fixed data. It swaps G9's item reader for `harness.prod_items`.
- `engine.py`: a copy of G9's `combined.py`, plus an interval shortfall state δI, a per-person slope mean, and "tests as races".
- `variants.py`: the task-3 variants.
- `score.py [cands|variants|addendum|addendum2]`: scoring, about 40 s each.
- `PREREG.md`: the variant specs, written before scoring, with two dated exploratory addenda.
- `results/*.csv`: `mae_all.csv`, `paired_all.csv` and `repro_vs_published.csv`.

Predictions are in `data/_research/shape/g11/`.

Shared files changed:
- `prep_data.py`: the A1 fix.
- `harness.py`: new `prod_items()`; `_prod_states` now reads every item.
- `DATA.md`: Changelog section.

The v1 parquets are in `data/_research/shape/v1_backup/`.

## 1. Bug A1 fixed, dataset rebuilt

Production items are now keyed on `(id, discipline)`. The new `prod_items.parquet` holds every item. `activities.prod_*` holds the interval item when a run has both, and the bound goes into `prod_bound_*`. Segments, race targets and hard_effort targets are byte-identical. The only changes are the `prod_*` columns and 32 new interval targets.

| production_curve | v1 | v2 (fixed) |
|---|---|---|
| race MAE adrian / rise / a+r / patrik | 0.89 / 1.78 / 1.34 / 10.87 | 0.91 / 1.75 / 1.33 / 10.87 |
| interval MAE (n) | 2.28 (164) | 2.41 (196). On the old 164: 2.28. The 32 new ones: 3.05, bias +0.6 |
| hard_effort MAE (19) | 4.18 | 4.16 |
| max \|repro − fitness.json published pre-race VDOT\| (11 races) | 0.37 (rise Copenhagen) | **0.01** |

The reproduction is now exact: G9's engine with every component off matches production_curve to 1e-13. Old candidate race predictions move by at most 0.36 (production), 0.31 (C) and 0.72 (S).

## 2. Re-scored candidates (v2; MAE in VDOT; filled predictions)

| model | adrian off. (5) | rise off. (5) | a+r off. (10) | patrik (1) | adrian off.+high (8) | a+r off.+high (13) | adrian 3 extras | interval a / r / all (122/68/196) | hard_effort a / r / p / all (13/2/4/19) |
|---|---|---|---|---|---|---|---|---|---|
| production | 0.91 | 1.75 | 1.33 | 10.87 | 1.26 | 1.45 | 1.83 | 2.38 / 2.47 / 2.41 | 3.51 / 2.67 / 6.99 / 4.16 |
| C | 0.96 | 1.31 | 1.13 | 7.13 | 1.87 | 1.65 | 3.39 | 2.42 / 2.40 / 2.42 | 3.29 / 2.46 / 4.44 / 3.45 |
| T1 | 0.91 | 1.31 | **1.11** | 7.13 | 1.78 | 1.60 | 3.24 | 2.41 / 2.41 / 2.42 | 3.42 / 2.46 / 4.45 / 3.54 |
| S (G1 fast slope) | 0.89 | 1.64 | 1.26 | 10.87 | 1.26 | **1.40** | 1.88 | 2.39 / 2.48 / 2.42 | 3.48 / 2.67 / 6.96 / 4.13 |
| G5 | 1.08 | 1.41 | 1.24 | **3.43** | 1.97 | 1.75 | 3.45 | 2.38 / 2.57 / 2.45 | 3.15 / 1.85 / 4.52 / **3.30** |
| mean(C, G5) | 1.02 | **1.23** | 1.12 | 5.28 | 1.92 | 1.65 | 3.42 | 2.39 / 2.46 / 2.42 | 3.20 / 2.15 / 4.48 / 3.36 |

Paired comparison against production: mean Δ|err| [bootstrap 90 %], then wins–losses and the sign-test p. Negative means the model beats production.

| model | official a+r | official rise | official+high adrian | official+high a+r | interval all | hard_effort all |
|---|---|---|---|---|---|---|
| C | −0.20 [−0.59, +0.14] 5–4 p 1.0 | −0.45 [−1.16, +0.10] 4–1 | **+0.61 [+0.10, +1.21]** 1–6 p 0.12 | +0.20 [−0.26, +0.68] 5–7 | +0.02 | −0.71 [−1.59, +0.15] 13–6 p 0.17 |
| T1 | −0.23 [−0.45, +0.01] 7–2 p 0.18 | **−0.44 [−0.68, −0.20] 5–0 p 0.06** | +0.52 [+0.00, +1.12] 2–5 | +0.15 [−0.23, +0.58] 7–5 | +0.01 | −0.62 [−1.51, +0.22] 13–6 |
| S | −0.07 [−0.38, +0.17] 3–5 p 0.73 | −0.12 | 0.00 [−0.17, +0.19] 4–3 | −0.04 [−0.28, +0.16] 5–6 | +0.01 | −0.03 |
| G5 | −0.09 [−0.69, +0.51] 6–4 p 0.75 | −0.35 | **+0.71 [+0.19, +1.26]** 3–5 | +0.31 [−0.28, +0.90] 6–7 | +0.04 | **−0.85 [−1.73, −0.03]** 12–7 p 0.36 |
| mean(C, G5) | −0.21 [−0.68, +0.23] 4–3 p 1.0 | −0.53 | **+0.66 [+0.15, +1.21]** 1–5 | +0.20 [−0.30, +0.73] 4–6 | +0.02 | −0.80 [−1.67, +0.04] 12–6 |

No sign test reaches p < 0.05 on races. Every candidate that contains the best-effort stream B (C, T1, G5 and their mean) gets clearly worse for adrian once his 3k tests count: the bootstrap interval excludes 0. Their race gains are rise's.

On v2, S's race gain over production shrinks from −0.14 (G9, v1) to −0.07. Part of what S fixed on v1 was the missing interval scores.

The hard_effort tier stays about 1 VDOT GPS-optimistic (G10 A2), so read its biases with that in mind.

Errors per target (pred − actual):

| target | prod | C | T1 | S | G5 |
|---|---|---|---|---|---|
| adrian 3k test 2025-11-15 (60.1) | −3.5 | −6.3 | −6.3 | −3.4 | −5.6 |
| adrian 3k test 2026-03-31 (56.1) | −1.6 | −2.4 | −2.4 | −1.2 | −2.5 |
| adrian 10k TT 2023-04-22 (52.3) | +0.4 | +1.5 | +1.0 | +1.0 | +2.3 |
| rise Copenhagen Half 2025 | −2.6 | −0.6 | −1.9 | −1.1 | −0.8 |
| rise Bilbao Half 2024 | +3.7 | +4.2 | +2.8 | +4.0 | +2.0 |

## 3. Adrian's under-prediction at short distances

I fixed the variants in `PREREG.md` before scoring them. The addenda were written after the earlier scores and are labelled exploratory.

| variant | adrian off. (5) | rise off. (5) | adrian 3 extras | 3k 11-15 / 3k 03-31 / HM 09-14 err | interval all | hard all |
|---|---|---|---|---|---|---|
| production | 0.91 | 1.75 | 1.83 | −3.5 / −1.6 / −0.9 | 2.41 | 4.16 |
| V1 C, slope mean 0 | 0.89 | 1.53 | 3.20 | −6.2 / −2.4 / +0.1 | 2.43 | 3.54 |
| V2 T1, slope mean 0 | 0.81 | 1.59 | 3.10 | −6.2 / −2.4 / −0.1 | 2.43 | 3.62 |
| V8 C, adrian's slope mean 0 (post hoc) | 0.89 | 1.31 | 3.20 | −6.2 / −2.4 / +0.1 | 2.43 | 3.53 |
| P+M (production + slope mean +0.75) | 0.89 | 1.85 | 2.10 | −3.7 / −1.7 / −0.7 | 2.42 | 4.04 |
| V3a C − B | 0.88 | 1.51 | 2.20 | −3.5 / −1.6 / −0.3 | 2.43 | 3.74 |
| P+B, P+B ≥ 3 km | 0.92, 0.94 | 1.68, 1.81 | 3.16, 3.29 | −5.9 / −2.5 ; −6.0 / −3.4 | 2.43, 2.51 | 3.72, 3.66 |
| V4 P + δI (interval shortfall) | 0.99 | 1.66 | 1.66 | −3.5 / −1.3 / −0.9 | 2.40 | 4.11 |
| V10 P + δI, tight slope sd 0.5 (expl.) | 1.23 | 1.77 | **1.18** | −2.4 / −0.3 / **−1.9** | 2.39 | 4.22 |
| V9 P + tests as races (expl.) | 0.91 | 1.75 | 1.61 | −3.5 / **−0.9** / −1.0 | 2.45 | 4.19 |
| V9 S + tests as races (expl.) | **0.81** | 1.64 | 1.64 | −3.4 / **−0.5** / −0.6 | 2.45 | 4.11 |

**A slope prior doesn't fix it.**
- Moving the slope mean between 0 and +0.75 changes the 3k predictions by at most 0.2 (V1, V2, V8, P+M).
- The person-specific prior (V8) only reproduces V1 for adrian.
- Production already has a slope mean of 0 and still misses the 2025-11-15 test by 3.5.
- On the 2025-11-15 test, getting the 3k right from production's θ (57.4) would take a slope of −3.7 per doubling.

**Most of C / T1 / G5's extra deficit is the best-effort stream B.**
- On the test days, C − B reads the same 3k as production. The full model C lowers θ by about 1.4 and steepens adrian's slope from +1.1 to +2.9.
- Restricting B to efforts of 3 km or more makes it worse, not better.
- Dropping the 2025-10-24 "time trial" 5ks, the obvious suspects with π 0.6, changes the 2025-11-15 error by only 0.06.
- So the cause is the cumulative pull of many low-π, sub-maximal bests, not one session.

**The underlying cause is an intent effect that the model reads as a curve.**
- adrian's maximal efforts in autumn 2025 show no "stronger at long distances" slope: HM 58.8 (Sep), 10k 59.4 (2 Nov), 3k 60.1 (15 Nov). A least-squares slope across all four maximal efforts (including the Bislett 10k of 57.1) is about −0.6 per doubling.
- His interval scores within 35 days sit 1.7–4.0 below every maximal effort, at every distance from 3k to HM: +4.0 and +2.6 for the 3k tests, +1.7 for the 5k, −0.4 to +2.3 for the 10ks and +3.1 for the HM. The gap doesn't grow with distance.
- His reps around the 3k test run about 5 VDOT below 3k-test pace, so the intervals are clearly sub-maximal.
- Production pins the short end with intervals at 5 km and the long end with races and tempos, so the interval shortfall turns into a positive slope.
- The fix in principle is a separate interval shortfall δI. But for adrian, δI is not identifiable: his only maximal short evidence after 2021 is the tests, which aren't evidence, so V4 learns δI ≈ 0.2 and changes nothing.
- Forcing the gap into δI with a tight slope prior (V10) fixes both 3ks (−2.4, −0.3) but costs the HM (−0.9 → −1.9). Adrian official MAE goes 0.91 → 1.23, which fails the pre-set criterion. His interval-to-race gap is not constant over time (about 1 in 2021–23, 2–3 in 2025), and the mobile positive slope is what currently absorbs that.

**What does work: feed all-out tests in as evidence (V9).** Entering the confirmed tests as race items (σ 1.0, own distance):
- cuts the next 3k error from −1.6 to −0.9 (production) or −0.5 (S);
- is neutral on the races (Lier 2023 goes from +1.01 to −0.92);
- makes intervals slightly worse (+0.07 for adrian), which is expected, because intervals are now read against a higher maximal level.

Only one scored target is downstream of a test, so this rests on the mechanism, not on power.

## 4. Conclusions and production recommendation

**Best approach for each person** (n is tiny everywhere, and no race difference is significant):

| person | best | evidence |
|---|---|---|
| adrian | **production, or S (+ tests as races)** | Official: production 0.91, S 0.89, S + tests 0.81. Official+high: production and S 1.26, S + tests 1.12. Every B model is worse on his short tests (+0.5 to +0.7, CI excludes 0). |
| rise | **T1** (C ties it at 1.31) | −0.44 vs production, 5–0 wins, p 0.06, CI [−0.68, −0.20]. Improves all 5 races, both halves included. No short tests exist to check the B damage. |
| patrik | G5 (3.4, against 7.1 for C / T1 and 10.9 for production) | One race after only easy runs: qualitative. |
| overall | **production stays the default** | Nothing beats it for every person. B-based models trade rise and patrik gains for adrian short-distance losses. A per-person choice isn't justified with 5 races each. |

**Production changes:**
1. **Add a "test / time trial" evidence kind.** Treat confirmed all-out efforts as `race` items: σ `race` 1.0, own distance, replacing the session's own item. Start with adrian's 3 high-confidence extras once he confirms them (`g10/confirmations.json` or a `tests` list in races.json). It is the only change that addresses the 3k problem without hurting the 10k / HM races.
2. **Optional, low risk:** `curve.q_slope` 0.005 → 0.04 and `curve.slope_prior_sd` 1.5 → 2.0 (S). It is neutral on every tier on v2: race −0.07, intervals +0.01, hard −0.03. It is best for adrian together with change 1. Expect wider HM bands. Its race gain is smaller than G9 reported on v1.

**Leave alone:**
- Slope mean / `slope_revert` stays at 0. Don't adopt G8 / G9's +0.75: P+M scores +0.04 on races, +0.09 with the extras, and moves adrian's 3ks the wrong way.
- `interval_offset` −0.5 and `tempo_offset` +3.0 stay as they are. Tempo at its own distance on the production engine (P + T1-tempo) is worse: race a+r 1.55. T1's gain needs the rest of C.
- No B stream in race predictions. P+B makes adrian's 3k −5.9 and his official+high +0.51, and B ≥ 3 km is no better.
- No δI state: it isn't identifiable without short maximal evidence.

**Shadow / monitor:**
- Run T1 (= C + tempo at own distance) in shadow for rise and patrik next to production, and check it on rise's next race.
- Log adrian's next 3k / 5k tests against production, S and S + tests.
- Track adrian's interval-to-race gap, which grew from about 1 to 2–3 between 2021–23 and 2025. If it keeps drifting, a slowly varying δI combined with regular tests is the principled model.
- Get adrian's confirmations of G10's 9 medium extras: it's the cheapest way to more power, and rise has none.
- Check why B's low-π bests drag adrian's level down (π for easy / steady runs, the GPS bias of 1.0) before using B anywhere.

**Not done:** no re-run of G2–G4 or G6–G8 members on v2, and no recalibration of the sd for B variants.

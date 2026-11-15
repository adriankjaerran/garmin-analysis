# G11 task 3: pre-registered short-distance variants (written 2026-11-05, before any variant was scored)

Known when writing this (diagnostics only, no variant scores): on the A1-fixed data, C / T1 / G5 under-predict
adrian's 3k tests by 2.4–6.3 and production / S by 1.2–3.5. State inspection on the test days: C's extra
short-end deficit comes from the censored best stream B (C−B reads the same 3k as production), not from the +0.75
slope mean (C−M changes the 3k by ≤ 0.2). Interval scores sit 1.7–4.0 below adrian's maximal efforts at every
distance (3k, 5k, 10k, HM), and his reps near the 3k tests run ~5 VDOT below 3k-test pace: intervals are sub-maximal.

Variants (all on the G9 engine with every production item; settings fixed here, none tuned afterwards):

| id | spec | question |
|---|---|---|
| V1 `C, M=0` | C with slope mean 0 | prior at 0 instead of +0.75 |
| V2 `T1, M=0` | T1 with slope mean 0 | same, on T1 |
| V3 `C−B` / `T1−B` | C / T1 without the best-effort stream | is B the short-end culprit? |
| V4 `P+δI` | production + static per-person interval shortfall state δI, prior N(0, 1.5²): interval items y = θ − δI + ε | intent effect on the production engine |
| V5 `S+δI` | V4 with G1's fast slope (q 0.04, sd 2) | |
| V6 `T1−B+δI, M=0` | T1 without B, slope mean 0, + δI | "intent" model: tempos and intervals both learned shortfalls, slope from maximal efforts + prior 0 |
| V7 `T1+δI, M=0` | V6 keeping B | |
| V8 `C, M_adrian=0` (POST HOC) | C with slope mean 0 for adrian, +0.75 for rise / patrik | person-specific prior; chosen after seeing adrian's 3k tests, so its adrian-extra score is in-sample |

Read-out: race / extra / hard_effort targets read the curve as before. **Interval-tier targets read θ − δI** (the
model's prediction of the next interval *score*, which is what the tier measures); for variants without δI nothing
changes. Primary evaluation for task 3: adrian official 10k / HM races must not get worse by > 0.1 MAE, and
interval / hard_effort must not get worse by > 0.1; the 3k tests are in-sample for the *motivation* of V4–V8 (they are
why the question was asked), so a gain on them alone is not evidence. rise's races are the out-of-sample check.

## Addendum (written after V1–V8 were scored, before V9 / V10 were run): exploratory, not pre-registered

V1–V8 showed: the slope mean barely moves the 3k predictions; B is what pushes C / T1 / G5 down at 3k; δI stays ≈ 0
for adrian (no maximal short evidence after 2021, so the interval–race gap can't be told apart from the slope).
Two follow-ups, settings fixed here:
- **V9 `P + tests as races`**: production evidence plus the 3 high-confidence extras (adrian 10k TT 2023-04-22, 3k
  tests 2025-11-15 and 2026-03-31) entered as `race` items (σ 1.0, own distance), replacing any production item of
  the same activity and excluded from B. Also `S + tests`, `C + tests`. Only targets *after* a test can change; the one
  scorable extra that changes is the 2026-03-31 3k (n = 1), plus official races after 2023-04-22.
- **V10 `P + δI, tight slope`**: V4 with the slope prior sd 0.5 (q_slope scaled to keep production's ratio:
  0.005 × (0.5 / 1.5)²), forcing the interval–race gap into δI instead of the slope.

## Addendum 2 (written after V9 / V10 were scored, before these were run): production-change candidates, exploratory

To make the production recommendation concrete, single components on the production engine and a B restricted to
≥ 3 km (the short bests are what drag adrian's 3k down). Settings fixed here: `P+M` (slope mean +0.75), `P+B`
(G5 bests ≥ 1 mile, as G9), `P+B3k` (bests ≥ 3 km), `C B3k`, `T1 B3k` (C / T1 with bests ≥ 3 km), `P+T1tempo`
(production + tempo at own distance with learned δ, no B / S / M), `S+T1tempo`.

# G10: harness audit, extra ground truth, re-scoring

Files (this folder): `audit.py` (all checks + spot-check), `prod_fixed.py` (production_curve on all fitness.json items,
to size bug A1), `build_extra.py` (the extra targets), `harness_ext.py` (wrapper: official + extra targets, paired
comparisons), `run_ext.py` (the scores below). Outputs are in `data/_research/shape/g10/`: `audit_checks.json`,
`audit_spotcheck.csv`, `lost_prod_items.csv`, `extra_targets.parquet` / `.csv`, `ext_preds.parquet`, `ext_summary.csv`
and `ext_paired.csv`. Run everything from the repo root with `/tmp/shape-venv/bin/python`. Each script takes under 10 s.
No shared file was changed.

## 1. Audit findings

| # | finding | severity | effect |
|---|---|---|---|
| A1 | **`prep_data.load_person` builds `prod = {id: session}`, so it keeps one item per activity.** 32 fitness.json sessions have both an `interval/hard` score and a `long/bound` item. In each case the dict keeps the bound and drops the interval score: adrian 15 (mean 51.4), rise 15 (47.7, 9 of them in Nov 2025 – May 2026), patrik 2 (`lost_prod_items.csv`). | **medium** | (a) The interval tier is missing 32 targets (164 scored, should be about 196). (b) Every model that reads `activities.prod_*` (production_curve, G1, G5, and probably G2–G7) never sees these 32 interval scores. Production does use them. With all items (`prod_fixed.production_curve_all`), race MAE changes by < 0.03 (largest per-race change: rise Copenhagen 48.55 → 48.92) and the interval tier by < 0.1, so the published baselines barely move. Models that weight interval scores more heavily (G5 with π 0.7) may be affected more; not tested. Fix: key on `(id, discipline)`. |
| A2 | **`hard_effort` targets are GPS best efforts with no over-read correction.** On the 10 official races (adrian + rise), the best effort over the race distance reads high by a median **+0.85 VDOT** raw and **+1.2 VDOT** on GAP. rise's fenix 3 at Bislett reads +3.2. | medium | The hard_effort tier is about 1 VDOT optimistic, so every model looks about 1 too low on it. Read hard_effort bias with this in mind. G5 already subtracts 1.0 from its evidence; other models don't. |
| A3 | **Target covariate `workout` comes from the effort itself.** The tempo / steady / easy type rules use that session's pace and HR. | low–medium | Mild target leakage on the hard_effort tier. G5's tempo shortfall keys on it. Races are unaffected (`race` comes from races.json). |
| A4 | **Some parameters were fitted in-sample on all data,** including future races: production `interval_offset` −0.5 and `tempo_offset` +3.0 (fitted against all races), hr_settings max HR and device trust, and the production `cross_check` (a two-sided smoother) that sets `implausible`. | low | Known and documented. The race scale of the interval scores is calibrated with hindsight, so absolute bias is slightly optimistic for production-scale models. `implausible` touches only one run (adrian 2022-02-03, a 55 km "run" that is clearly ski). |
| A5 | **rise's race "10k 2026-10-23" has no `source` in races.json, so prep fills in `"chip"`.** It is really the watch time (2389 s moving) of a 10 km at Bislett (track) where the fenix 3 measured 10.51 km. | low | Time and distance are probably right (25 laps), but the label is wrong. The race's best effort reads +3.2 VDOT, so don't use its GPS data as evidence. |
| A6 | **One hard_effort target sits on a `suspect` segment:** rise 2024-10-06 10k (6 glitch seconds). | low | One target. |
| A7 | **Unflagged impossible best effort:** adrian 2026-02-08 (`steady`, gps_share 0.40, in ski season) has 3k VDOT 73 and 5k GAP 66.5. It passes `pace_ok` and `~suspect`, because the threshold is VDOT > 80. | low | G5 is robust to it (adrian predictions change by < 0.002 when it is removed). Production never scored it. Suggest marking segments `suspect` when gps_share < 0.5 or VDOT > the person's best race + 8. |
| A8 | **`evaluate` swallows exceptions silently** unless verbose. | low | A crashing model turns into abstentions, which get filled with the naive model. Check `n_pred`. |

**Checks that passed:**
- **No future or same-day data:** across all 205 targets, `history()` returns only rows with day < target day, and never the target's own activity. `TARGET_KEYS` carries no outcome.
- **Load features:** they use days strictly before the run (code read). `hr_rest_30d` uses the 30 days before.
- **Races are not double-counted:** each official race is one production item (kind `race`; production's own `is_10k_dup` copy is not in the parquet). G5 adds races once and excludes them from its bests and interval items. A race's best effort isn't a hard_effort target either (races `continue`).
- **Race times:** all 11 run race targets match races.json (km and seconds). Ski races are correctly ignored.
- **GPS-long races:** race targets use the official km and time, so they are unaffected.
- **Dates:** `date` = `start_local` date for every run, so there are no UTC day shifts.

**Spot-check** (`audit_spotcheck.csv`): 10 targets, drawn at random within tiers (a plain random 10 picks only interval targets). 3 race, 4 interval and 3 hard_effort, traced back to the raw source.
- Race: `official_s` in races.json equals `time_s` (3 of 3).
- Interval: the fitness.json interval score equals the target VDOT (4 of 4).
- hard_effort: an independent best window computed from the FIT file's own distance field (fitparse, no grid) agrees with the harness raw best to within 1.0 s (3 of 3). The GAP target time differs from it only by the grade adjustment.
- In all 10, the history cut-off and the date are correct.

## 2. Extra ground truth: candidates for Adrian to confirm or reject

**Where I looked:**
- **Activity names:** all Garmin defaults ("Oslo Running", "Oslo Løping", …). No race, test, parkrun, karusell or stafett words. The only "løp" hit is rise's "Tromsø Terrengløp", which is a 7.5 km / 2.6 h hike.
- **Garmin `eventType`:** "uncategorized" on every run.
- **Garmin PR flags (`pr`):** only the current PR holders (5 runs), none near-maximal.
- **groups.json `race?` / tempo labels.**
- **Standalone efforts:** runs where the best effort covers ≥ 60 % of the run.
- **High HR:** high last-third HRR or max HR on any device.

To describe each candidate (never as the target value) I used the residual against the published two-sided fitness.json curve.

**How each value is built:** the best effort at a standard distance (raw on a track, GAP on roads), minus that person's measured GPS over-read on their official races:
- adrian: road GAP −1.06, track −0.85
- rise: road GAP −1.73, Bislett −3.24 (fenix 3)
- patrik: road −1.16
- watch track mode: 0

`vdot_gps` in the parquet keeps the uncorrected value.

**Confidence:**
- **high:** clear intent, clear maximal effort (HR ≥ 93 % of max at the end, even or negative split) **and** a trustworthy distance.
- **medium:** one of those three is weak.

| target_id | person | date | effort | VDOT (GPS → corrected) | conf. | rationale | confirm? |
|---|---|---|---|---|---|---|---|
| 10959008247:extra_10k | adrian | 2023-04-22 | 10k road "tempo", 39:06 GAP | 53.35 → **52.29** | **high** | Continuous 10.02 km, max HR 204 (99 %), HRR 0.93, 15 days before Lier 10k 2023 (53.29) and at the same level: a 10k time trial | ☐ |
| 20994407266:extra_3k | adrian | 2025-11-15 | Bislett 3k, 9:41 (run 3.07 km in 9:54) | 60.98 → **60.13** | **high** | Standalone 3 km on the track, splits 3:18/3:14/3:11, HR 135 → 193, max 199 (96 %), 13 days after Lier 10k 2025. Labelled "easy" by the type rules | ☐ |
| 22363927895:extra_3k | adrian | 2026-03-31 | Bislett 3000 m, track mode, 10:27 | 56.09 (no correction) | **high** | Exactly 3.00 km, splits 3:26/3:34/3:26, HR to 190 (max 194, 94 %) on the 4315 (trust 1.0). rise ran a 3k test there the same evening | ☐ |
| 20781880143:extra_5k | adrian | 2025-10-24 | Bislett 5k 18:01 | 57.41 → 56.56 | medium | Even pace, max HR 193 (93 %). User said "not a race", and a second 5k in 19:01 followed 27 min later, so possibly held back | ☐ |
| 22324591263:extra_1mi | adrian | 2026-03-28 | Bislett track mode, mile 5:26 | 54.20 | medium | 2 km at 3:24/3:21 with HR 192–196 (95 %). The shape of the test (2k? mile?) is unclear and the distance is short | ☐ |
| 20142870530:extra_5k | adrian | 2025-08-22 | Bislett "tempo", best 5k 18:08 | 55.81 → 54.96 | medium | Hard and continuous (HRR 0.87, max 193), but a tempo inside 8.1 km | ☐ |
| 20309992118:extra_HM | adrian | 2025-09-07 | Bislett "tempo", HM 1:21:17 | 57.33 → 56.48 | medium | A week before Copenhagen Half (58.78), max HR 188 (91 %), HRR 0.80: likely a controlled rehearsal | ☐ |
| 4151759347:extra_5k | adrian | 2019-10-13 | road "tempo", 5k 20:16 GAP | 49.06 → 48.00 | medium | Max HR 203 (98 %), HRR 0.91, but FR935 wrist HR (trust 0.5) and no race nearby to cross-check | ☐ |
| 22363939116:extra_3k | rise | 2026-03-31 | Bislett 3k at the end of 7.1 km, 11:22 GPS | 50.88 → 47.64 | medium | HR 190–191 (her max) and fading splits (3:30/3:54/3:58): all-out. The fenix 3 reads about 5 % long at Bislett, so the value is ±1.5 | ☐ |
| 17216650765:extra_10k | rise | 2024-10-06 | Bislett "tempo", 10k 43:18 GPS | 47.35 → 44.11 | medium | Strap HR, HRR 0.88, 13 days before Bilbao Half. GPS glitch seconds inside the effort, so the value is uncertain | ☐ |
| 20142876938:extra_5k | rise | 2025-08-22 | Bislett "tempo" (with adrian), 5k 20:52 GPS | 47.39 → 44.15 | medium | Strap HR, HRR 0.90, max 182 (96 %), but a tempo inside 8 km with a pause | ☐ |
| 19082390679:extra_5k | patrik | 2024-04-21 | road "tempo" 5.00 km, 21:18 GAP | 46.27 → 45.11 | medium | Max HR 203 (98 %), HRR 0.91, covers the whole run, but hilly and one pause (GAP distance) | ☐ |

**Considered and rejected** (not stored; tell me if any should be added):
- **adrian 2020-09-16 and 2020-10-22, Øya stadion 3.04 km in about 10:50:** "3k VDOT 54", but mean HR 144–148 and max 157–165. That is easy-run HR, so the GPS on the track probably measured long.
- **adrian 2025-10-24, second 5k (19:01):** a repeat run straight after the first.
- **adrian 2026-02-08:** GPS / ski artefact (A7).
- **adrian 2019-02-23 Trondheim 21.9 km (Garmin PR flag):** pauses, whole-run VDOT 40.
- **adrian 2026-10-10 (Garmin "VO2MAX" label):** a 3k inside 13 km at 53, sub-maximal.
- **rise 2026-05-09 "mile 53.6":** GAP artefact on a 1.8 km run.
- **rise 2026-10-13 (PR flag):** fastest 5k 22:05, sub-maximal.
- **patrik 2026-10-27 tempo:** 5k 44 inside 10 km, negative split from an easy start.
- **Remaining tempos:** 3–10 below the level, already in the hard_effort tier.

**Power.** Only **3 high** candidates exist, all adrian's. rise and patrik have none. The extended set is therefore 13 targets for adrian + rise (8 + 5), against 10 official. If Adrian confirms the medium ones, it grows to 21. Confirmations go in `data/_research/shape/g10/confirmations.json`, e.g. `{"20781880143:extra_5k": "confirm"}`. `harness_ext` picks them up automatically: a confirmed target is included whatever its confidence, and a rejected one is dropped.

## 3. Extended scores

`harness_ext.evaluate_ext` scores the extra targets with the harness's own history rule. Extra targets are passed with `tier = "race"`, so G5's hard_effort shortfall does not apply to them. Scores below are MAE in VDOT.

**Models:**
- production_curve
- G5 recommended: `make_tobit(Cfg(min_m=1600, tempo_shortfall=True))`
- G1 `model.predict` (mobile slope)
- G8 `model.predict` (q_s 0.02)
- audit_prod_all_items: production with A1 fixed

**Official + high:**

| set | n | production | G5 tobit | G1 | G8 | prod all items |
|---|---|---|---|---|---|---|
| adrian official | 5 | 0.89 | 1.08 | 0.87 | 0.91 | 0.91 |
| adrian extra (high) | 3 | 1.85 | 3.45 | 1.86 | 1.85 | 1.83 |
| adrian all | 8 | 1.25 | 1.97 | 1.24 | 1.26 | 1.26 |
| rise (official only) | 5 | 1.78 | 0.78 | 1.53 | 1.70 | 1.75 |
| adrian + rise all | 13 | **1.46** | 1.51 | **1.35** | 1.43 | 1.45 |
| patrik (official) | 1 | 10.87 | 3.43 | 10.87 | 10.87 | 10.87 |

**Official + high + medium** (sensitivity; medium values are uncertain):
- adrian + rise, all 21 targets: production 1.18, G5 1.59, G1 1.12, G8 1.15.
- Extras only: production 1.04, G5 2.20, G1 1.05, G8 1.01.

**Paired against production** (mean Δ|error|; negative = better; bootstrap 90 % interval; wins / losses):

| model | set | adrian | rise | adrian + rise |
|---|---|---|---|---|
| G5 tobit | official (10) | +0.19 | −1.00 | −0.41 [−1.20, +0.37], 6 / 3 |
| G5 tobit | official + high (13) | +0.72 [+0.16, +1.27] | −1.00 | +0.06 [−0.69, +0.73], 6 / 6 |
| G5 tobit | + medium (21) | +1.04 [+0.54, +1.57], 3 / 10 | −0.61 | +0.41 [−0.17, +0.95], 8 / 12 |
| G1 | official (10) | −0.03 | −0.26 | −0.14 [−0.48, +0.15], 5 / 4 |
| G1 | official + high (13) | −0.01 | −0.26 | −0.11 [−0.37, +0.14], 7 / 5 |
| G1 | + medium (21) | 0.00 | −0.16 | −0.06 [−0.24, +0.10], 8 / 6 |
| G8 | official + high (13) | +0.01 | −0.08 | −0.02 [−0.21, +0.16], 5 / 5 |
| prod all items | official + high (13) | +0.01 | −0.03 | −0.01 [−0.07, +0.05], 3 / 3 |

No sign test is anywhere near significant (all p ≥ 0.09).

**Predictions on the high extras** (actual → production / G5 / G1 / G8):
- **10k 2023-04-22:** 52.3 → 52.8 / 54.6 / 53.3 / 53.3
- **3k 2025-11-15:** 60.1 → 56.6 / 54.6 / 56.8 / 56.8
- **3k 2026-03-31:** 56.1 → 54.6 / 53.6 / 54.9 / 54.9

Every model under-predicts adrian's two 3k tests, by 1.2–3.5 (production, G1 and G8) and 2.5–5.6 (G5). The 2026-03-31 test is a track-mode 3.00 km with no GPS doubt.

## 4. Conclusions
- **The harness has no time leakage.** The one real bug is **A1**: 32 interval scores are dropped in prep. It barely moves race scores, but it removes 16 % of the interval evidence (rise's 2026 sessions especially) and 32 interval targets. Fix it in prep (key `(id, discipline)`) before final model comparisons. A2 (hard_effort targets about 1 VDOT GPS-optimistic) should be corrected, or at least stated, wherever hard_effort bias is reported.
- **Expanding the ground truth adds little power.** Names, Garmin flags and event types carry no race information. Only 3 high-confidence extras exist, all adrian's, so rise stays at 5 targets. With 13 targets, differences under about 0.4 VDOT still can't be separated. Adrian confirming the 9 medium candidates (and the rejected Øya 3ks, if they were tests) is the cheapest way to more power.
- **G1 (mobile slope)** stays marginally better than production on every set (−0.06 to −0.14). It never loses by more than noise but is never significant. **G8** is indistinguishable from production.
- **G5 tobit:** its race gain (−0.41) is all rise. On adrian it gets **worse** when short extras are added: +0.72 with the high extras, and adrian's 3k tests sit 2.5–5.6 below its prediction. Its learned slope tilts adrian too strongly towards long distances. With the extras, G5 vs production flips from −0.41 to +0.06 (high) or +0.41 (+ medium). **G5's headline race advantage doesn't generalise to adrian's short efforts.**
- **All models under-predict adrian at 3 km**, which is consistent with G8's finding that the "stronger at long distances" slope may be partly an artefact of the interval-score scale. G9 should check whether its combined model reads short distances through a positive slope.
- **Open:** Adrian's confirmations (table above), whether rise's fenix 3 Bislett correction (3.2, from one race) is right for her 2024–25 Bislett sessions, and whether A1 changes G5 / G2–G7 more than it changes production (not run: that needs a rebuilt parquet, which is outside my write scope).

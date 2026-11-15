# Erg fitness trend (`scripts/erg_fitness.py`)

`uv run scripts/erg_fitness.py adrian [--today YYYY-MM-DD] [--quiet]` → `data/adrian/erg_fitness.json` (≈ 0.03 s).
Reads only `data/<person>/concept2/` (`results.json` + `results/<id>/result.json`, from `scripts/fetch_concept2.py`),
through `concept2.load_result`: shared-machine sessions (friends taking turns) are **cleaned to your own turns** first
and scored as interval sessions ([concept2.md](concept2.md#cleaning-shared-machine-sessions)). Before cleaning, those
"40:00 at 1:43" pieces (everyone's metres) put the SkiErg 2k-equivalent near world-record level.
The app serves it at `GET /api/fitness/erg` and rebuilds it when a Concept2 input is newer than the file, when it was
built on an earlier day (the line runs to today) or with another `model_version`; `POST /api/fitness/rebuild`
rebuilds it too. Shown on Fitness (section **Erg**, `/fitness#erg`, `components/ErgSection.tsx`) and on Home's shape
line ("… · Row 1:45 · SkiErg 1:39").

User decision (2026-11): an own trend per machine, **Row** (RowErg) and **SkiErg**, in pace /500 m and watts: a
2k-equivalent pace from the best efforts, interval sessions scored on work watts. Honest and simple; **not on the VDOT
scale and not part of Overall.**

## Per-session score
- **Watts** = 2.80 / (s per m)³ (Concept2, both machines); pace = 500 · (2.80 / W)^(1/3).
- **Continuous piece** (JustRow, FixedTime/DistanceSplits, or an interval workout with one interval): its average
  pace over its distance.
- **Interval session**: the time-weighted average watts of the work intervals (intervals with time and distance > 0;
  rests and rest distance left out), turned back into a work pace. It counts as a continuous effort of an
  *effective duration* between one rep and all the work:
  `d_eff = rep_s · n^f`, `f = work / (work + rest between reps)` (the rest after the last rep doesn't count).
  No rest (f = 1) → the whole work time; long rests (f → 0) → one rep. 12×2:00 / 0:30 → f 0.81, d_eff ≈ 15 min;
  8×4:00 / 1:00 → ≈ 21 min; 4×4:00 / 1:00 → ≈ 12 min. A heuristic: it makes short-rest sessions worth more than
  long-rest ones at the same work pace; it is not fitted (no tests in the data yet).
- **2k-equivalent** by **Paul's law**: `pace_2k = pace(D) − 5 s · log2(D / 2000)`, +5 s /500 m per doubling of
  distance (D = the piece's distance, or d_eff at the work pace). It's the rowing rule of thumb and close to
  Riegel's 1.06 exponent; used for the SkiErg too (no better rule; SkiErg records follow it roughly).
- **Kinds** (σ = observation sd in s /500 m):

| kind | what | σ | smoother kind (`fitness_model.ROBUST`) |
|---|---|---|---|
| `test` | single continuous piece of 1:30–10:00 work, or a standard distance (500 / 1000 / 2000 / 5000 / 6000 / 10000 m ± 0.5 %), whose 2k-eq is at or above the first-pass trend − 1 σ | 1.5 | race: full weight both ways to 2.5 σ |
| `piece` | other continuous pieces (incl. test candidates below the trend) | 2.5 | hard: full weight to 1 σ below, 2.5 σ above |
| `intervals` | interval sessions | 3.0 | hard |
| `warmup` | same machine and day, 2k-eq > 3 s slower than the day's best | 3.0 | bound: ignored unless > 2 σ above |
| `ignored` | < 60 s or < 250 m of work; interval sessions with < 2 valid reps or < 50 % of the work time valid | — | — |

There is **no heart rate** in the data, so intent is unknown and most sessions are only lower bounds: the hard / bound
weights make the line an upper envelope (a session far below the line counts weakly; one above counts fully).

## Smoother
`fitness_model.smooth_series` (daily random walk, Kalman + RTS, asymmetric robust IRLS weights) on y = −pace_2k
(higher = better), q = 0.1 s²/day (≈ 1.7 s /500 m sd per month), prior sd 8 s, from the first scored session to
today. Two passes: test candidates are first smoothed as pieces, then promoted to `test` when at or above the trend.
The band is ±1 sd; it widens in gaps. `stale` = no piece / test / interval session with weight > 0.5 for > 90 days.

## Personal bests
From every non-ignored session's whole piece, work intervals and splits (a "piece"):
- `best_2k_equivalent`: the best session score (not warm-ups);
- `distances` (500 m … 10 km): the fastest pace over a piece of **at least** that distance (`exact` = the piece was that
  distance ± 1 %; otherwise the time is the pace × distance, a lower bound on what the distance would take);
- `durations` (1, 2, 4, 10, 20, 30, 40 min): the best average watts over a piece of at least that duration.
No stroke-level rolling bests (strokes.json is not read).

## erg_fitness.json
```
{built, today, model_version (1), params (P), scale,
 machines: {row|skierg: {label ("Row" | "SkiErg"), unit,
   current: {date, pace, sd, pace_fast, pace_slow, watts, watts_fast, watts_slow, pace_text, time_2k_s,
             last_evidence_date, days_since_evidence, stale, trend_90d_s (now − 90 days ago, < 0 = faster)},
   daily: [{date, pace, sd, pace_fast, pace_slow, watts}], weekly: [same, every 7th day ending today],
   personal_bests: {best_2k_equivalent {pace, watts, id, date, label, kind} | null,
                    distances [{m, pace, time_s, watts, id, date, label, what, piece_m, piece_s, exact}],
                    durations [{s, watts, pace, id, date, label, what, piece_s, exact}]},
   n_sessions, n_scored, first_date, last_date}},
 sessions: [{id (Concept2 result id = app activity id), date, start, machine, label ("12×2:00", "4×500m", "30:00";
             concept2.workout_name), workout_type, drag_factor, stroke_rate, kind, reason (ignored),
             work_s, work_m, rest_s, n_reps, work_share, pace, watts (work), d_eff_s, d_eff_m, pace_2k, watts_2k,
             pace_text, pace_2k_text, sigma, weight (robust weight 0..1), trend_pace (line that day),
             delta_s (pace_2k − trend_pace, > 0 = slower)}]}
```
Paces in s /500 m. Library: `build(person, today=None)`, `write(person)`, `score_result(r)`, `paul_2k`, `watts`,
`pace_from_watts`, `fmt_pace`. Tests: `tests/test_erg_fitness.py`.

## Numbers (adrian, 2026-11-07)
| | now (2k-eq) | watts | 2k ≈ | best 2k-eq |
|---|---|---|---|---|
| Row | 1:44.8 ± 1.7 /500 m | 304 W | 6:59 | 1:42.9 (2025-11-21, 12×2:00) |
| SkiErg | 1:38.9 ± 2.0 /500 m | 361 W | 6:36 | 1:30.3 (2026-02-19, 40:00) |

## Limitations
- **Resolved (2026-11): SkiErg "continuous" pieces were shared machines.** The 30–40 min SkiErg pieces at 1:43–1:46
  /500 m were friends taking turns (1 min on / 1 min off); cleaning keeps only your turns
  ([concept2.md](concept2.md#cleaning-shared-machine-sessions)). SkiErg is now ~1:46 (2k ≈ 7:03) instead of ~1:39.
  One all-out test (e.g. a 2k or 1k) per machine would still anchor both lines.
- The interval effective duration and Paul's law are rules of thumb, not fitted to this person.
- Without HR or tests, a period of only easy sessions keeps the line flat at the last strong evidence (with a
  growing band), it doesn't drop.

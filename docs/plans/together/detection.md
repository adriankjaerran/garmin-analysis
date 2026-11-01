# "Together" detection — findings, rules, prototype results

Prototype: `docs/plans/together/prototype/together_proto.py`. Run it from the repo root:
`uv run python -I docs/plans/together/prototype/together_proto.py --json /tmp/together/out/pairs.json --groups /tmp/together/out/together.json`.
Use `-I` because `/tmp/together` also holds other agents' scratch files, including an `inspect.py` that shadows the stdlib.
The prototype only reads `data/`: it calls the rep detectors directly and doesn't go through `compare.pkl`. A full run over adrian × patrik takes about 10 s.
Outputs: `/tmp/together/out/pairs.json` (every candidate pair with all metrics) and `/tmp/together/out/together.json` (groups in the proposed file format).

## 1. Candidates, and what the data shows

**Time base.** Each activity goes onto absolute UTC at 1 Hz: grid second 0 = **last** FIT record timestamp − its grid t.
This anchors on the post-jump clock, which the GPS has set.
**Finding: 12 activities have an index `startTimeGMT` that is days to months early** because of watch clock jumps:
- patrik: 10 activities, off by 0.4 d to 155 d
- adrian: 2 activities

So the candidate search must not trust the index for those files. The prototype corrects s/e from the FIT whenever the record span exceeds the elapsed time by more than 60 s.
This turned up 2 new candidates (2023-04-28 and 2026-08-10). Neither is together.

**Candidate rule.** The time windows overlap, with a 10 min margin. That gives 25 adrian × patrik pairs. Then:
- 18 have FIT + GPS on both sides
- 7 have no FIT on patrik's side (the 2023 US trip: "walking, no GPS", and one 7-day stopwatch)
- 2 have no time overlap after correction

Start distance alone is a poor signal. Patrik usually starts from home ("St. Hanshaugen", 700 m from Bislett) and runs to the track.
On 2026-10-06 he joined 22 min late from 1.8 km away, yet did all 5 reps with adrian. **Track co-location over time is the signal.**

**Co-location on a track is often chance.** Bislett is a 546 m oval, and two people doing *different* workouts there are within 100 m 77 % of the time (2026-10-27).
For two people doing the *same* 400 m reps, 25 m co-location is only 38–47 %, because pace differences open 30–120 m gaps by the end of each rep.
So "within X m" alone separates nothing on a track. Two fixes:
- **with each other** = 60 s rolling median distance ≤ 50 m **and** 60 s rolling median |Δspeed| ≤ 0.8 m/s. It is counted over the joint moving seconds; seconds where either side lacks GPS count as unknown/not-with.
- For intervals, **rep onsets** decide (§2).

Full metrics table (all candidates): §6.

## 2. Same-workout test (rules)

Inputs per activity:
- 1 Hz grid (`session_groups.read_series`/`to_grid`)
- type / sig / place from `groups.json`
- reps (only reps ≥ 30 s), taken from the first source that applies:
  - Bislett indoor: `indoor_bislett.rep_laps`. Positions within 400 m of Bislett are also masked as "no GPS" (stale fixes), and speed comes from `indoor_grid`.
  - `ski_intervals.analyse` for ski_intervals
  - `run_analysis.analyse` for interval types **and** for unclassified runs (multi_sport / run_other / other). Adrian's 2026-10-15 track session was a multi_sport file with no type.

**Rep "followed" (the core interval test).** For each rep of X (start s, after the pair's offset τ), Y follows it if all of these hold:
- Y has a speed step whose onset (crossing the midpoint of Y's speed over s−40…s−5 and s+10…s+60) is within ±12 s of X's onset
- Y's speed after ≥ 1.3 × before, and ≥ 2 m/s
- Y's speed after ≥ 0.7 × X's rep speed, so a jog isn't a rep
- Y is "on" during ≥ 70 % of the first min(dur, 120) s
- positions at s are ≤ 60 m apart, if both have GPS

This runs in both directions. It doesn't need Y's own detector to have found the rep. In 2024-04-14 patrik's file is typed `easy` with 0 reps, but he followed 4 of adrian's 5 reps.

**Coverage.** cov = max over the two sides of (rep time shared ÷ own rep time in the overlap). The side that did less decides, so 10 of adrian's 15×400 = 100 % of patrik's 10 reps.
Rep *time* is used, not count, because detectors count warm-up blocks and strides as reps: on 2026-10-15 patrik's 410 s warm-up was a "rep".

**Rules, in order** (computed only on the time overlap):
1. **together / same_workout override.** with ≥ 80 % of the joint moving time → side by side throughout, whatever the type labels say. The label is `same_workout` if ≥ 2 reps are shared, else `together`.
   - 2026-09-10: adrian `easy` vs patrik `intervals_road`. They ran together for 97 % of adrian's file; patrik's reps came after adrian stopped.
2. **Structured (either side has ≥ 2 reps in the overlap):**
   - ≥ 2 shared reps and cov ≥ 0.75 → **same_workout** (score = cov). "6 reps vs 5 of the same reps" lands here.
   - ≥ 2 shared reps and cov < 0.75 → **partly_together** (e.g. warm-up and 2 of 8 reps together).
   - otherwise → **same_place_diff_workout** if co-located at all (colo100 ≥ 20 % or ≥ 10 min with each other), noting any warm-up / cool-down done together; else not_together. *"He does a long 5k non-stop and I do 6 intervals"* = 2026-10-27, which lands here.
3. **Continuous** (no rep structure):
   - both `race?`, starts ≤ 120 s / ≤ 150 m → **same_race** (score 0.6). They ran the same event, mostly apart.
   - with ≥ 60 % → **together**
   - with ≥ 10 min but < 60 % → **partly_together**, with segments, e.g. "together from 15 to 42 min" = joined / split.
   - colo100 ≥ 30 % or starts ≤ 300 m apart → **same_place_diff_workout**
   - else not_together
4. **No GPS on one side:**
   - speed correlation ≥ 0.6 → `unverified`, else not_together
   - no FIT at all: index start points ≤ 500 m, starts ≤ 15 min apart and overlap ≥ 50 % → **unverified**

**Labels, with display proposals:**

| label | meaning | shown on the Together page |
|---|---|---|
| `same_workout` | intervals with the same reps started together (or side by side all the time with reps) | yes |
| `together` | continuous, side by side ≥ 60 % (≥ 80 % overrides types) | yes |
| `same_race` | same race start, ran apart | yes (own badge) — *user decision* |
| `partly_together` | ≥ 10 min with each other, or a shared part of a rep set | yes, with segment text ("together 15–42 min") |
| `unverified` | no track on one side; index start points and time match | yes, greyed "no GPS" — *user decision* |
| `same_place_diff_workout` | same place & time, different workouts (maybe a shared warm-up / cool-down) | no / collapsed "also there" — *user decision* |
| `not_together` | — | no |

Score 0–1 (shown as a bar): cov for structured, the with-share for continuous; 0.6 race, 0.5 unverified, ≤ 0.3 diff-workout.

**Decisions taken (please confirm):**
- warm-up together with different main sets = `same_place_diff_workout`, with "warm-up together N min" in the text, so not "together". The user's own example puts the main set first.
- someone joins halfway: if they then do ≥ 75 % of their reps with the other → `same_workout`, else `partly_together`.
- one person continues alone after the other stops: the label covers only the overlap; the share of each file (`share_of`) is kept for display ("patrik continued 45 min").

## 3. Groups for N people; storage; rebuild
- Pairs run over every pair of people (itertools.combinations), on index-level candidates only, so the cost scales with co-occurring activities, not N².
- Nodes = (person, activity id). Edges = pairs labelled same_workout / together / partly_together / same_race / unverified.
- **Group = connected component**: union-find in the prototype. A chain A–B (first half) + B–C (second half) is one group, and the per-pair labels are kept, so the UI can show who was with whom.
  A person may have 2 files in one group (split recording). Group label = the strongest edge; members and pairs are listed.
- **Place ids are per person.** adrian's `Oslo-5` (Bjølsen) ≠ patrik's `Oslo-5`, so a group stores `place: {person, id, name}`.
  It is chosen as the best named place of *any* member within 600 m of the joint start: known > landmark > geocode > generic.
  Without this, patrik's "St. Hanshaugen" home start would name Bislett sessions.
- **Group id**: `<local date>_<HHMM of the earliest member start>_<place id>` (the place id belongs to `place.person`; add the person, or use a slug of the display name, if two people's ids collide), e.g. `2026-10-20_2007_Bislet`.
  It is only stable if it is **persisted**: on rebuild, a component inherits the id of the old group it shares the most members with. A new person's earlier start otherwise changes the id.
  Keep an `aliases` map for ids that disappear (merged groups).
- **File**: `data/together.json` at the top level. That's fine: the app's `deps.people()` only lists `data/*/activities_index.json`, and `data/` is gitignored.
  Format (see `/tmp/together/out/together.json`):
  `{version, built, params, people, offsets:{"adrian:4315|patrik:3077": s}, groups:[{id, joint_start_utc, date, start_local, place, label, score, people, members:[{person,id,type,sig,km,dur_s}], pairs:[{a,b,label,score,why,with_pct,shared_reps,dstart_s,start_dist_m,overlap_s,tau_pos_s,tau_onset_s,speed_lag_s}]}], rejected:[…same_place_diff_workout / not_together pairs, for debugging…]}`.
  Put the cache in `data/_together/cache.pkl` (no activities_index.json there, so it's not a "person"). Cache entries:
  - per FIT: true start, which needs reading every FIT (~800 files ≈ 8 s → cache by mtime+size)
  - per pair: keyed by both FIT keys + both `groups.json` entries' type/indoor fields + `PARAM_VERSION`
- **Rebuild**: `scripts/together.py [--full]`, after `fetch_activities` + `session_groups` of any person. It only recomputes pairs that involve new or changed activities, then reruns union-find (cheap).
  The server can rebuild lazily when any input (`activities_index.json` / `groups.json` mtimes) is newer than `together.json.built`.

## 4. Clock / recording offsets between the watches
τ = how many seconds later patrik's file shows the same event. To align, shift patrik by −τ.
Products: adrian FR935 = 2691 (until early 2026), 4315 since then; patrik 3077 throughout, recording every 4–9 s (adrian every 1–3 s).

Three estimators:
- **pos**: position lag minimising the median distance while side by side, both > 2 m/s and ≤ 30 m apart. Best for continuous runs.
- **onset**: median of the shared rep-start differences.
- **xcorr**: lag of the 5 s speed cross-correlation, integer s. Coarse, and confounded by pace differences.

| watch pair | pair (date) | pos τ (n s, median dist) | onset τ | xcorr |
|---|---|---|---|---|
| 2691 × 3077 | 2021-01-23 easy together | −0.75 (2783 s, 8.3 m) | – | 0 |
| | 2024-04-07 partly | −1.0 (888 s, 5.0 m) | – | 3 |
| | 2024-04-14 same workout | −1.5 (1843 s, 7.6 m) | 0.0 (4 reps) | 2 |
| | 2025-11-11 same workout | 4.0 (212 s, 11 m; track, pace gap) | 1.0 (8 reps) | 4 |
| 4315 × 3077 | 2026-09-10 easy together | **+2.25 (3142 s, 4.3 m)** | – | 3 |
| | 2026-10-06 same workout | +2.25 (282 s) | +4.2 (5 reps) | 7 |
| | 2026-10-15 same workout | +2.0 (774 s, 9 m) | −0.5 (2 reps) | 3 |
| | 2026-10-20 same workout | +5.0 (230 s; track reps, patrik behind) | +4.0 (10 reps) | 7 |
| | 2026-10-27 cool-down only | +2.5 (322 s, 3.8 m) | – | 3 |

**Reading.**
- Offsets are small, a few seconds, and depend on the **watch pair**: about −1 s with adrian's FR935, about +2.3 s with his 4315. Patrik's watch was the same in both periods.
- Track sessions inflate τ because the slower runner trails, so a pace gap looks like a lag. Rep onsets during reps add patrik's 4–9 s sampling and the detector's lag compensation, which uses adrian's calibration for patrik too.
- Proposal: per pair, use pos τ if there are ≥ 600 s side by side with median distance ≤ 10 m. Else use the median pos τ of that watch pair (`offsets` in together.json). Else 0.
- The clocks themselves are GPS time, so these are recording/processing lags rather than real clock offsets. It doesn't matter for the "distance between us" chart, which needs exactly this effective offset.
- At 3–4 m/s, a 2–3 s offset = 8–12 m of fake "gap". Worth applying.

## 5. Result: groups found (adrian × patrik), and an eye check

**Counts:** 17 groups (27 candidate pairs): 5 same_workout · 4 together · 1 partly_together · 1 same_race · 6 unverified (all from the July 2023 US trip, patrik has no FIT).
Not shown: 2 same_place_diff_workout and 8 not_together.

**Eye check, case by case** (from 60 s / 120 s timelines of distance, speeds and rep windows; script `/tmp/together/out/dbg.py <adrian id> <patrik id> [step]`):
- **2026-10-20 Bislett, same_workout.**
  - Adrian 15×400 (77 s each), patrik 10×400 (84–89 s). The first 10 reps start together in both directions, onsets 3–5 s apart, positions 3–15 m apart.
  - Adrian then did 5 more reps alone while patrik jogged. Before that: 6 min warm-up ~120 m apart at the same speed (separate or one behind), and at the end a cool-down together.
  - ✓ This is the "6 vs 5 reps" case: patrik 10 of 15.
- **2026-10-15 Bislett, same_workout (0.79).**
  - Adrian's multi_sport file (no type) vs patrik `1×1300 + 2×3000`. They warmed up together for 12 min, then did two ~12 min blocks together, onsets 0–1 s apart.
  - Adrian ran a third block alone while patrik jogged (rejected by the 0.7 pace rule). ✓
- **2026-10-06 Frogner stadion, same_workout.** Patrik started 22 min after adrian, 1.8 km away, joined him, and both did all 5 of adrian's remaining reps (onsets 3–5 s, ≤ 14 m). Plausible ✓. The place comes from the joint start: Frogner stadion, not adrian's Bislett start.
- **2026-09-10 Bislett, together (97 %).**
  - Easy run side by side for 66 min, median 7 m apart.
  - Patrik's file then continues 45 min with fartlek reps alone, and his type is `intervals_road`. The label is per overlap, so it is correct. The page should say "patrik continued with reps".
- **2026-06-29 Weinan (China), together.** Hike, 4 h side by side. ✓
- **2025-11-11 Bislett, same_workout.** Adrian `6×800 + 2×400`, patrik "10 reps mixed". All 8 of adrian's reps were started by patrik (0–2 s). Patrik has 2 extra detector reps. ✓
- **2024-04-14 Bislett, same_workout (0.81).** Patrik's file is typed `easy` with 0 reps, but he followed 4 of adrian's 5 reps (3×1000 + 2×45 s), 81 % with each other. Shows why the follow test must not rely on the other side's type. ✓
- **2024-04-07 Bjølsen, partly_together.** Starts 496 m apart. Together from 15 to 42 min of the overlap, so they met up and then ran together to the end. ✓ Correct as "partly" (or "joined at 15 min").
- **2023-07-15 Riverside County, together.** Hike 4.8 h, 99 %. ✓
- **2023-05-07 Lier, same_race.** Starts 1 s and 8 m apart, then apart after 4 min (10 %). Adrian 39:08, patrik ~43 min. ✓ as "same race"; whether that counts as "together" is for the user.
- **2021-01-23 Trondheim, together (99 %).** ✓
- **2023-07-13/14/16/16/18/21 US trip, unverified.** Hikes, a bike ride, walks: index start points 11–300 m apart, starts ≤ 3.5 min apart, 98–100 % overlap. Almost certainly together, but there is no track to prove it.
- **2026-10-27 Bislett, same_place_diff_workout.**
  - Adrian 6×1000. Patrik ran continuously at 3.2–3.9 m/s for 40 min ("5 reps mixed 3:00–21:42" = a tempo / long run with a few pauses).
  - Patrik's speed doesn't change at adrian's rep starts (on/off ratio 1.0). 1 "shared" rep, 8 min with each other (start + cool-down). ✓ Exactly the user's "5k non-stop vs intervals" example.
- **2026-01-20 Bislett indoor, same_place_diff_workout.** Adrian 10×539 m indoor laps (GPS masked). Patrik joined 17 min late and ran a steady 3.4 m/s (accelerometer) for 30 min, with no steps at adrian's rep starts. ✓ different workouts.
- **Not together:**
  - 2026-10-13 (patrik no GPS, steady 2.6 m/s during adrian's 8×800)
  - 2025-05-25 (adrian ski intervals, patrik 1.9 km no-GPS run)
  - 2026-07-21 (Bodø vs Oslo)
  - 2026-06-16 (3.7 km apart)
  - 2023-04-28 (starts 3.3 km apart, 78 s close)
  - 2023-05-23 (7-day stopwatch)
  - 2025-09-27 and 2026-08-10 (no overlap after the time fix)

**Suspected false positives / negatives:**
- *FN?* **2026-10-13:** patrik ran without GPS (9.8 km, steady 2.6 m/s) during adrian's Tuesday Bislett intervals. If his speed comes from a bad accelerometer estimate that smears reps, he might have done the reps. The data doesn't show any steps at all, so it stays not_together.
  It could be shown as "same time, place unknown". *Ask the user.*
- *FN?* **2026-01-20:** after the indoor session, both files show ~30 min of walking/jogging at 1.3 m/s, about 10 m apart (visible before the Bislett GPS mask). It's not counted because adrian's positions near Bislett are masked.
  The label stays "different workout", but the mask should only cover the indoor stretch (rep span ± 5 min), not the whole session.
- *FP risk:* the follow test can be fooled by two people starting reps at the same moments on purpose but running different distances, e.g. 400s vs 1000s from the same line. That still counts as "same workout" (same start, same place). That fits "start the intervals at the same time and place" from the user's definition, but the signature differs.
  The UI should show both sigs side by side.
- *FP risk:* on a track, a 60 s window with close positions and equal speed can happen by chance (e.g. both jogging between reps). The rolling Δspeed rule removed most of it: 2026-10-27 dropped from 18 to 8 min "with".
  For continuous runs on a track, use with ≥ 60 % (not 10 min) before saying `partly_together`.
- *Detector-dependence:* shared reps need ≥ 2 reps from at least one side's detector. Two unstructured files doing an unprogrammed fartlek together end up as `together` through the with-share anyway.
- *Thresholds* were tuned on these 18 FIT pairs only: 5 interval sessions, 4 continuous runs, 3 hikes. Check them again when a 3rd person arrives, especially someone with a much faster or slower pace, where the 0.7 pace rule and the 50 m radius matter.

## 6. Tables

### All candidate pairs (adrian × patrik)
τ: + = patrik's file shows the same event later. with % = share of the joint moving time with each other (60 s rolling median distance ≤ 50 m and |Δv| ≤ 0.8 m/s). shared = reps of a followed by b / reps of b followed by a.

| date (adrian local) | adrian id / type / sig | patrik id / type / sig | Δstart s | start dist m | overlap s (% of a / b) | both GPS % | colo 25/50/100 % | with % (min) | reps a/b | shared (b in a / a in b) | τ pos s (n, dmed) | τ onset s | xcorr lag s (r) | label |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-10-27 20:07 | 24874084265 intervals_track 6×1000 | 24874001821 intervals_track 5 reps (mixed 3:00–21:42) | -176 | 74 | 2969 (82/94) | 100 | 23/40/77 | 17 (8) | 6/5 | 1/1 | 2.5 (322, 3.8) | – | 3 (0.2) | **same_place_diff_workout** |
| 2026-10-20 20:07 | 24795195699 intervals_track 15×400 | 24795299858 intervals_track 10×400 | 35 | 35 | 3324 (99/75) | 100 | 38/62/86 | 44 (19) | 15/10 | 10/10 | 5.0 (230, 4.0) | 4.0 | 7 (0.75) | **same_workout** |
| 2026-10-15 20:12 | 24737758736 multi_sport – | 24737909617 intervals_track 1×1300 + 2×3000 | -1 | 11 | 3881 (100/78) | 96 | 47/55/80 | 40 (23) | 4/3 | 2/2 | 2.0 (774, 9.0) | -0.5 | 3 (0.52) | **same_workout** |
| 2026-10-13 20:06 | 24713319874 intervals_track 8×800 | 24713898196 run_nogps – | -94 | – | 3204 (100/94) | 0 | –/–/– | – (0) | 8/0 | 0/0 | – (–, –) | – | 20 (0.08) | **not_together** |
| 2026-10-06 19:11 | 24631175030 intervals_track 3×1800 + 2×900 | 24632019253 intervals_road 8 reps (mixed 1:17–7:40) | 1329 | 1836 | 3757 (74/56) | 93 | 40/48/66 | 21 (8) | 5/8 | 5/5 | 2.25 (282, 5.1) | 4.2 | 7 (0.84) | **same_workout** |
| 2026-09-10 18:42 | 24313318009 easy – | 24444367861 intervals_road 9 reps (mixed 0:45–2:22) | -5 | 49 | 3948 (100/59) | 93 | 97/100/100 | 97 (59) | 0/0 | 0/0 | 2.25 (3142, 4.3) | – | 3 (0.88) | **together** |
| 2026-08-10 18:06 | 23925803195 ski_intervals 4×4:30 | 24002112369 easy – | 4375 | 2401 | 0 (0/0) | – | –/–/– | – (0) | – | –/– | – (–, –) | – | – (–) | **not_together** |
| 2026-07-21 11:13 | 23676819801 hike – | 24002107294 easy – | 4588 | 849772 | 5245 (53/97) | 100 | 0/0/0 | 0 (0) | 0/0 | 0/0 | – (–, –) | – | -20 (-0.09) | **not_together** |
| 2026-06-29 08:34 | 23449132487 hike – | 23463184408 run_other – | -606 | 68 | 14635 (100/96) | 96 | 89/99/100 | 88 (25) | 0/0 | 0/0 | – (–, –) | – | 10 (0.53) | **together** |
| 2026-06-16 15:56 | 23271578815 intervals_road 27×0:45 + 1×1:00 + 1×1200 | 23463156546 easy – | -613 | 3738 | 2524 (39/80) | 39 | 0/0/0 | 0 (0) | 0/0 | 0/0 | – (–, –) | – | -20 (0.08) | **not_together** |
| 2026-01-20 20:06 | 21612429612 intervals_track 10×539 | 21615492791 tempo – | 1035 | 377 | 4405 (81/11) | 3 | 100/100/100 | 0 (0) | 7/0 | 0/0 | – (–, –) | – | 19 (0.45) | **same_place_diff_workout** |
| 2025-11-11 19:37 | 20961161720 intervals_track 6×800 + 2×400 | 21445490294 intervals_track 10 reps (mixed 1:02–3:09) | -53 | 108 | 3518 (100/74) | 97 | 41/57/86 | 22 (9) | 8/10 | 8/7 | 4.0 (212, 11.3) | 1.0 | 4 (0.86) | **same_workout** |
| 2025-09-27 17:27 | 20520502285 easy – | 21445485836 easy – | -5256 | 5141448 | 0 (0/0) | – | –/–/– | – (0) | – | –/– | – (–, –) | – | – (–) | **not_together** |
| 2025-05-25 14:20 | 19230867986 ski_intervals 4×6:30 | 21445431707 run_nogps – | -116 | – | 654 (15/85) | 0 | –/–/– | – (0) | 0/0 | 0/0 | – (–, –) | – | 20 (0.26) | **not_together** |
| 2024-04-14 18:56 | 14901660080 intervals_road 3×1000 + 2×0:45 | 19082386807 easy – | 22 | 44 | 3133 (99/100) | 97 | 78/86/94 | 81 (37) | 5/0 | 4/0 | -1.5 (1843, 7.6) | 0.0 | 2 (0.84) | **same_workout** |
| 2024-04-07 15:51 | 14872648057 easy – | 19081643751 easy – | 32 | 496 | 2514 (99/87) | 96 | 47/54/60 | 44 (16) | 0/0 | 0/0 | -1.0 (888, 5.0) | – | 3 (0.83) | **partly_together** |
| 2023-07-21 06:42 | 11611773422 hike – | 11685712688 walking – | -202 | 22 | 9237 (98) | – | –/–/– | – (0) | – | –/– | – (–, –) | – | – (–) | **unverified** |
| 2023-07-18 12:17 | 11591526521 hike – | 11591796766 walking – | -158 | 93 | 15687 (100) | – | –/–/– | – (0) | – | –/– | – (–, –) | – | – (–) | **unverified** |
| 2023-07-16 18:05 | 11577768847 hike – | 11590228951 walking – | -22 | 12 | 4321 (100) | – | –/–/– | – (0) | – | –/– | – (–, –) | – | – (–) | **unverified** |
| 2023-07-16 16:39 | 11576884174 hike – | 11590225164 walking – | 69 | 32 | 4219 (100) | – | –/–/– | – (0) | – | –/– | – (–, –) | – | – (–) | **unverified** |
| 2023-07-15 14:16 | 11569602462 hike – | 11590222293 run_other – | -60 | 79 | 17381 (88/100) | 86 | 98/100/100 | 99 (139) | 0/0 | 0/0 | – (–, –) | – | -1 (0.84) | **together** |
| 2023-07-14 15:59 | 11569598572 cycling – | 11590205521 cycling – | 2 | 11 | 11359 (100) | – | –/–/– | – (0) | – | –/– | – (–, –) | – | – (–) | **unverified** |
| 2023-07-13 18:33 | 11569596771 hike – | 11590194667 walking – | -189 | 300 | 9091 (100) | – | –/–/– | – (0) | – | –/– | – (–, –) | – | – (–) | **unverified** |
| 2023-05-23 21:41 | 11266840039 stop_watch – | 11336093191 walking – | 289016 | – | 9006 (100) | – | –/–/– | – (0) | – | –/– | – (–, –) | – | – (–) | **not_together** |
| 2023-05-07 12:00 | 11063941501 race? – | 11063981514 race? – | 1 | 8 | 2347 (100/90) | 100 | 3/10/24 | 10 (3) | 0/0 | 0/0 | – (–, –) | – | -6 (0.05) | **same_race** |
| 2023-04-28 19:34 | 11063939051 easy – | 11063977957 easy – | 44 | 3270 | 1612 (97/46) | 100 | 2/5/9 | 5 (1) | 0/0 | 0/0 | – (–, –) | – | -9 (0.34) | **not_together** |
| 2021-01-23 15:27 | 6154356324 easy – | 6154407811 easy – | -34 | 67 | 3043 (100/99) | 100 | 95/100/100 | 99 (49) | 0/0 | 0/0 | -0.75 (2783, 8.3) | – | 0 (0.79) | **together** |

### Groups (proposed data/together.json content, newest first)

| group id | place | label | score | members (person: type sig km) | key numbers |
|---|---|---|---|---|---|
| 2026-10-20_2007_Bislet | Bislett | **same_workout** | 1 | adrian: intervals_track 15×400 8.81 km; patrik: intervals_track 10×400 9.07 km | 10 shared reps (reps 15/10, b_in_a), onset offset 4.0 s; with each other 44 % (19 min: [(7.0, 32.7), (43.2, 47.8), (51.5, 55.4)] min) |
| 2026-10-15_2012_Bislet | Bislett | **same_workout** | 0.79 | patrik: intervals_track 1×1300 + 2×3000 11.31 km; adrian: multi_sport 11.15 km | 2 shared reps (reps 4/3, b_in_a), onset offset -0.5 s; with each other 40 % (23 min: [(0.0, 12.0), (22.8, 28.6), (39.3, 43.0), (55.6, 64.7)] min) |
| 2026-10-06_1911_Frogner-stadion | Frogner stadion | **same_workout** | 1 | adrian: intervals_track 3×1800 + 2×900 11.74 km; patrik: intervals_road 8 reps (mixed 1:17–7:40) 12.28 km | 5 shared reps (reps 5/8, b_in_a), onset offset 4.2 s |
| 2026-09-10_1841_Bislet | Bislett | **together** | 0.97 | patrik: intervals_road 9 reps (mixed 0:45–2:22) 15.19 km; adrian: easy 9.34 km | with each other 97 % of the joint moving time |
| 2026-06-29_0824_Weinan-1 | 青柯坪 | **together** | 0.88 | patrik: run_other 5.92 km; adrian: hike 6.46 km | with each other 88 % of the joint moving time |
| 2025-11-11_1936_Bislet | Bislett | **same_workout** | 1 | patrik: intervals_track 10 reps (mixed 1:02–3:09) 9.15 km; adrian: intervals_track 6×800 + 2×400 9.42 km | 8 shared reps (reps 8/10, b_in_a), onset offset 1.0 s |
| 2024-04-14_1856_Bislet | Bislett | **same_workout** | 0.81 | adrian: intervals_road 3×1000 + 2×0:45 8.81 km; patrik: easy 8.71 km | with each other 81 % of the joint moving time, 4 shared reps (reps 5/0) |
| 2024-04-07_1551_Oslo-5 | Bjølsen | **partly_together** | 0.44 | adrian: easy 7.65 km; patrik: easy 8.62 km | with each other 44 % (16 min: [(15.2, 19.2), (22.9, 24.1), (27.8, 41.9)] min) |
| 2023-07-21_0639_Cochise-County-1 | Cochise County-1 | **unverified** | 0.5 | patrik: walking 9.0 km; adrian: hike 13.31 km | no FIT on one side: index start points 22 m / -202 s apart, overlap 98 % |
| 2023-07-18_1214_Lake-County-1 | Lake County-1 | **unverified** | 0.5 | patrik: walking 11.1 km; adrian: hike 10.83 km | no FIT on one side: index start points 93 m / -158 s apart, overlap 100 % |
| 2023-07-16_1805_Yavapai-County-1 | Yavapai County-1 | **unverified** | 0.5 | patrik: walking 2.17 km; adrian: hike 2.36 km | no FIT on one side: index start points 12 m / -22 s apart, overlap 100 % |
| 2023-07-16_1639_Coconino-County-1 | Bell Rock Loop Extension | **unverified** | 0.5 | adrian: hike 3.56 km; patrik: walking 2.02 km | no FIT on one side: index start points 32 m / 69 s apart, overlap 100 % |
| 2023-07-15_1415_Riverside-County-1 | Round Valley Trail | **together** | 0.99 | patrik: run_other 14.06 km; adrian: hike 15.7 km | with each other 99 % of the joint moving time |
| 2023-07-14_1559_Los-Angeles-County-1 | Santa Anita Wash Bike Path | **unverified** | 0.5 | adrian: cycling 30.29 km; patrik: cycling 36.71 km | no FIT on one side: index start points 11 m / 2 s apart, overlap 100 % |
| 2023-07-13_1830_Glendale-1 | Glendale-1 | **unverified** | 0.5 | patrik: walking 7.72 km; adrian: hike 6.9 km | no FIT on one side: index start points 300 m / -189 s apart, overlap 100 % |
| 2023-05-07_1200_Lier-1 | Sørum | **same_race** | 0.6 | adrian: race? 10.05 km; patrik: race? 10.09 km | both race?, starts 1 s / 8 m apart; with each other 10 % |
| 2021-01-23_1526_Trondheim-1 | Eventyrbyen | **together** | 0.99 | patrik: easy 8.97 km; adrian: easy 8.8 km | with each other 99 % of the joint moving time |

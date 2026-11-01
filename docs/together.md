# Together: workouts run with others (`scripts/together.py`)

Finds the workouts two or more people did together, groups them (any number of people) and compares them: per-rep
standings for interval sessions, gap / distance apart / splits for continuous runs, and a replay of everyone's
position on one clock. Served by `/api/together…` (below); the pages are [app.md](app.md) (`/together`, `/together/<gid>`; T3).
Plan and investigation: [plans/together-plan.md](plans/together-plan.md), [plans/together/detection.md](plans/together/detection.md),
[plans/together/page-design.md](plans/together/page-design.md).

```
uv run scripts/together.py build [--full]   # detect -> data/together.json (incremental; --full recomputes every pair)
uv run scripts/together.py list [--all]     # groups (--all: also the hidden near-misses)
uv run scripts/together.py show <gid>       # one group + its session results (standings, totals, breaks, splits)
uv run scripts/together.py validate         # generic checks on together.json and every session (exit 1 on failures)
```
Run `build` after `session_groups.py` (and `similarity.py`) for every person: it reads each person's
`activities_index.json`, `groups.json` and FIT files. A rebuild with nothing new takes ~0.1 s; a full rebuild of
adrian × patrik ~6 s (cold FIT start-time scan of every file ~5 s once, then cached).

```python
import together as tg                        # scripts/ on sys.path
tg.build(full=False)                         # -> the together.json document
tg.load(); tg.find(gid)                      # together.json (memoised by mtime); group by id (aliases resolved)
tg.session(gid)                              # session payload (cached in data/_together/sessions.pkl)
tg.replay(gid, step_s=2, max_points=4000)    # positions on the group clock
tg.list_payload(person=, sport=, kind=, label=, near=, place=, date_from=, date_to=, q=)   # the list endpoint
tg.links()                                   # {(person, activity id): {gid, label, kind, role, with}}
tg.people_info(); tg.hr_norm(person)         # display names / colours; HR max / rest for effort
```

## Files
| File | What |
|---|---|
| `data/together.json` | groups (cross-person, at the top of `data/`, not a person folder) |
| `data/people.json` | optional, hand-edited: `{id: {name, initials, colour, colour_dark}}`. Missing people / fields are automatic: name = folder name capitalised, colours from a fixed palette (blue, orange, green, purple, teal, ochre, red, slate: each ≥ 3:1 contrast on the light and the dark background), initials = the shortest name prefix no one else has |
| `data/<person>/profile/hr_settings.json` | `{max_hr, rest_hr}` for effort as % of HR reserve, written by `scripts/hr_settings.py` (max = highest HR held 12 s in a run, rest = median daily resting HR of the last 365 days; `"source": "manual"` = hand-edited, never overwritten). Without it: max (and rest, if set) from the watch's `heart_rate_zones.json`, effort as % of max HR, with a warning |
| `data/_together/cache.pkl` | FIT start times (by mtime + size), pair results (by both FIT keys + groups.json type / place / indoor fields + `PARAM_VERSION`) |
| `data/_together/sessions.pkl` | session payloads, by a fingerprint of the group record, the participants' FIT files / groups.json entries, HR settings, fitness.json, people.json and the engine code **as loaded by the process** (a hash taken at import: a long-running server with older code can't store old-format payloads under the new key) |

`app/server/deps.people()` only lists folders with an `activities_index.json`, so none of these is a "person".

## Detection
1. **Start times from the FIT.** The index's `startTimeGMT` is days to months early for files with a watch clock jump
   (12 activities: 10 of patrik's, 2 of adrian's). When the FIT record span exceeds the elapsed time by > 60 s the
   start is taken as last record − elapsed. On the 1 Hz grid (compare's store: `session_groups.read_series` with the
   clock-jump fix, Bislett indoor speed) grid second 0 = last record timestamp − its grid time, i.e. the post-jump
   clock that the GPS set.
2. **Candidates:** every pair of people (any number of people), activities whose time windows overlap (10 min margin).
3. **Per pair, on the time overlap at 1 Hz in UTC:**
   - **with each other** = 60 s rolling median distance ≤ 50 m **and** 60 s rolling median |Δspeed| ≤ 0.8 m/s, over the
     seconds where at least one of them moves (on a track, being close is often chance);
   - reps (≥ 30 s) from the compare engine's detectors for interval types, untyped runs / multisport files, ski
     intervals and Bislett indoor laps;
   - **rep followed:** the other's speed steps up within ±12 s of the rep start (after the clock offset), to ≥ 1.3 × the
     speed before and ≥ 2 m/s, ≥ 0.7 × the rep's own speed, "on" for ≥ 70 % of the first min(rep, 120 s), positions
     ≤ 60 m apart. Both directions; the other side's detector doesn't need to have found the rep;
   - coverage = the larger of the two sides' shared rep time ÷ own rep time.
4. **Pair label**, in order:
   - with each other ≥ 80 % of the joint moving time → `same_workout` (≥ 2 shared reps) or `together`;
   - structured (≥ 2 reps on a side): ≥ 2 shared reps and coverage ≥ 0.75 → `same_workout` (score = coverage), < 0.75 →
     `partly_together`; else `same_place_diff_workout` when co-located (≥ 20 % within 100 m or ≥ 10 min with each other),
     else `not_together`;
   - continuous: both `race?`, starts ≤ 120 s / 150 m apart → `same_race` (0.6); with ≥ 60 % → `together`; ≥ 10 min with
     each other → `partly_together`; co-located or starts ≤ 300 m apart → `same_place_diff_workout`; else `not_together`;
   - no GPS on one side: speed profiles correlate ≥ 0.6 → `unverified`; no FIT on one side: index start points ≤ 500 m,
     starts ≤ 15 min apart, overlap ≥ 50 % → `unverified` (0.5).
5. **Clock offset per pair** (s that b's file shows the same event later): the pair's own position lag (side by side,
   both > 2 m/s, ≤ 30 m) when it has ≥ 600 s of it with a median distance ≤ 10 m (`offset_src: position`), else the
   median of those trusted lags for the same **watch pair** (`watch_pair`: adrian 2691 × patrik 3077 −1.0 s,
   adrian 4315 × patrik 3077 +2.1 s), else the median rep-onset difference with ≥ 3 shared reps (`rep_onsets`), else 0.
   The label itself uses the pair's own position lag (or 0), so pairs stay independent and cacheable.
6. **Groups** = connected components over the shown labels (`same_workout`, `together`, `partly_together`,
   `same_race`, `unverified`): an A–B + B–C chain is one group, pair labels are kept. Group label = the strongest edge.
   A near-miss (`same_place_diff_workout`) partner of a group member joins it with role `other` (shown, not ranked, no
   link on its activity); near-miss pairs without a shown member become **hidden** groups (`hidden: true`, listed with
   `near=1`). Members' clock offsets are chained along the pair edges from the earliest member (offset 0).
7. **Place** = the best named place of any member (known > landmark > geocode > generic) within 600 m of the joint
   start (first shared rep / first second with each other), so patrik's home start doesn't name a Bislett session.
   A non-Latin name (geocoded abroad: "青柯坪") gets a Latin `name` from the same geocode answer
   (`data/<person>/cache/geocode.json`: the first Latin address part, suburb / town / city / … / state, e.g.
   "Huayin City"); the original is kept as `place.name_local` (shown next to it on the session page).
8. **Group id** `YYYY-MM-DD_HHMM_<place id>` (local start of the earliest member). Persisted: on rebuild a component
   keeps the id of the old group it shares the most members with (a new person with an earlier start doesn't change
   it); ids that disappear (merged groups) go into `aliases`, which the API resolves.

### Labels
| label | meaning | list |
|---|---|---|
| `same_workout` | intervals with the same reps started together (or side by side ≥ 80 % with ≥ 2 shared reps) | shown |
| `together` | continuous, side by side ≥ 60 % (≥ 80 % overrides the types); shown as **"Side by side"** (not "Together": that's the page) | shown |
| `same_race` | same race start, ran apart | shown |
| `partly_together` | ≥ 10 min with each other, or a shared part of a rep set; `participants[].together` has the span | shown |
| `unverified` | no track on one side; index start points and times match | shown, `no_track: true` |
| `same_place_diff_workout` | same place and time, different workouts (maybe a shared warm-up / cool-down) | hidden (`near=1`) |

**Sport** (`group.sport`): the majority family over the members with a specific type (untyped `run_other`, `other`,
`multi_sport` don't count, so a typed hike beats a "run" logged at 3.5 km/h); a tie goes to the earliest member. The
list's sport filter and chips use it. **Kind:** `intervals` (≥ 2 shared reps in a same_workout / partly_together pair),
`race`, else by the group sport: `continuous` (run), `hike`, `ride`, `ski`, `swim`, `other`. **Workout** (`group.workout`):
the anchor's for intervals / races, else a member of the group sport who didn't log it as intervals ("Hike" over "Walk").
**Role:** `full`; `partial` (a partly_together pair, or < 90 % of the anchor's reps followed); `other` (near-miss partner).

### The groups found (adrian × patrik, 27 candidate pairs)
| gid | label | people (role) |
|---|---|---|
| 2026-10-20_2007_Bislet | same_workout | adrian 15×400 (full), patrik 10×400 (partial) |
| 2026-10-15_2012_Bislet | same_workout 0.79 | patrik 1×1300 + 2×3000, adrian multisport file, a run since 2026-11-01 (partial: cut the 2nd 3000 at 2 km) |
| 2026-10-06_1911_Frogner-stadion | same_workout | adrian 3×1800 + 2×900 (adrian 5–0), patrik (joined 22 min late, all 5 reps) |
| 2026-09-10_1841_Bislet | together 0.97 | easy run; patrik continued alone with reps |
| 2026-06-29_0824_Weinan-1 | together 0.88 | hike |
| 2025-11-11_1936_Bislet | same_workout | adrian 6×800 + 2×400, patrik "10 reps mixed" |
| 2024-04-14_1856_Bislet | same_workout 0.81 | adrian 3×1000 + 2×0:45, patrik (typed easy, followed 4 of 5) |
| 2024-04-07_1551_Oslo-5 | partly_together | together 16–42 min |
| 2023-07-13 … 07-21 (6 groups) | unverified | US trip, patrik has no FIT |
| 2023-07-15_1415_Riverside-County-1 | together 0.99 | hike |
| 2023-05-07_1200_Lier-1 | same_race | adrian 39:08, patrik 43:26 |
| 2021-01-23_1526_Trondheim-1 | together 0.99 | easy run |
| hidden: 2026-10-27_2004_Bislet, 2026-01-20_2006_Bislet | same_place_diff_workout | 6×1000 vs patrik's tempo run (was misdetected as "5 reps (mixed)"); indoor laps vs steady run |

## `data/together.json`
```
{version: 1, param_version, built, people: [ids],
 offsets: {"adrian:4315|patrik:3077": {offset_s: 2.12, n: 2}, ...},     # trusted position lag per watch pair
 aliases: {old gid: gid},
 groups: [group, ...],                                                   # newest first
 rejected: [{a, b, label, why}]}                                         # not_together pairs (debugging)
group = {gid: "2026-10-20_2007_Bislet", date, start_local "20:07", start_local_iso, start_utc, end_utc, joint_start_utc,
  sport "run", sports [..], kind "intervals", label "same_workout", label_text "Same workout", score 1, hidden false,
  no_track false, place {name "Bislett", lat, lon, person "adrian", id "Bislet", src "known"}, workout "15×400",
  people ["adrian", "patrik"], n_people 2,
  participants [{person, activity_id "24795195699", role full|partial|other, anchor, name, type, sport, signature,
                 workout, n_reps, dist_km, dur_s, start_utc, end_utc, start_local, offset_s (subtract from this file's
                 UTC to get the group clock), clock_fixed, has_fit, product, sample_s,
                 together {from_min, to_min, segments [[a, b] min from the group start], text} | null}],
  pairs [{a: [person, id], b, label, score, why, with_pct, with_s, shared_reps, n_reps, follow {b_in_a, a_in_b},
          dstart_s, start_dist_m, overlap_s, tau_pos_s, tau_pos_n, tau_pos_dmed_m, tau_onset_s, offset_s, offset_src}]}
```

## Session engine
A thin layer on the per-person compare engine (`scripts/compare.py`, not forked): each participant's session comes from
`compare.store(person).session(id)` (1 Hz grid), main reps from `compare._reps_slots` (strides left out), breaks from
`compare._break_stats`. Everyone is put on the **group clock** (UTC − the participant's offset; t = 0 at `t0_utc`, the
earliest file start).

**Intervals**
- **Slots by clock time.** The anchor = the participant with the most reps (detection's anchor first); its reps are
  slots 1..n. Another person's rep joins a slot when the starts are ≤ max(8 s, 2 × the coarser recording interval)
  apart and the reps overlap > 50 %; unmatched reps get extra slots `k+1`, `k+2` after anchor rep k (shown, not ranked).
  A participant without a detected rep in a slot who **followed its start** (the onset test above) gets one from their
  speed step (`source: onset`), unless own detected reps overlap the slot (they did something else there).
- **Set distance per slot** (`target`): from the slot owner's signature when its counts match the reps
  (`3×1000 + 2×0:45` → 1000 m ×3, 45 s ×2; a signature time far from the rep's own duration → the own duration),
  else inferred: the shortest full rep distance in the slot rounded to a round number (100…5000 m list ± 4 %, or the
  nearest 100 m ± 5 %), time-defined when the durations agree and the distances don't.
- **Result for the set distance** (`result_s`), per cell (`method`):
  - `detector`: detected rep within 92 %…104 % (× GPS scale) of the set distance → the detected rep time. GPS error is
    taken as proportional (patrik's 400 m reps measure 392–416 m; adrian's track mode exactly 400).
  - `interpolated`: the detected rep runs on > 4 % past the set distance (a detector tail), or an onset rep → the time
    at which the person's distance since their own start reaches set distance × their **GPS scale** (median own rep
    distance ÷ set distance over their full reps, 0.95–1.05; `gps_scale`).
  - `merged`: a detected rep shorter than 92 % (the detector split it at a slowdown) → the stream time to the set
    distance when it's ≤ 1.3 × the slot's best **and** the stretch from the detected rep end to the set distance was
    run at ≥ 70 % of the rep's own speed (`_kept_running`, `MERGE_PACE`); the own extra reps inside it are absorbed.
    A slower stretch is the recovery: the watch measured the rep short and the set distance is only reached while
    walking (2026-10-06 Frogner: adrian's rep 2 was 6:43 to his lap press, the stream reached 1800 m at 9:48, so the
    replay showed patrik "passing" him at 1780 m). Then the cell is `partial`.
  - `partial` (◐): otherwise; shown with own time / distance, not ranked.
  - Set-time slots (`0:45`): metres covered in the set time (÷ GPS scale), ranked by metres; `result_s` = the time to
    cover the winner's metres at own pace (so totals add up).
- **Standings** per slot (≥ 2 results): rank, `gap_s` to the winner, `gap_m` (≈ gap × set distance ÷ time);
  **dead heat** when within `tie_s` = max(1 s, half the coarser median recording interval) of the group's best
  (`tie: true`, same rank).
- **Suspect reps** (`cell.suspect`: the reason): > 10 % faster than the person's median for the same set distance
  (≥ 3 such reps), e.g. adrian's 15th 400 m in 64.5 s (median 77.8 s): a short rep or a lap pressed early. Kept in
  totals, left out of best rep and fade; listed in `leaderboard[].suspect`.
- **Shared slots** = slots where every participant (not `other`) has a result. **Leaderboard**: total / average over
  the shared slots, `pace_s_km` (distance-weighted: total distance ÷ total time of the shared reps), gap to the
  leader, rep wins / ties (shared slots), best rep, `reps_done` / `of` (anchor slots), `extra_reps`; every full rep the
  person ran (also the ones only they ran): `all_n`, `all_total_s`, `all_avg_s` (one set distance only),
  `all_pace_s_km`; **fade** (last 3 vs first 3 raced reps, suspect ones left out: `fade_s` when they share the set
  distance, `fade_s_km` as pace; positive = slower at the end), average effort.
- **Cumulative** over the raced slots: total and gap to the leader after each; a person missing a slot drops out (null).
- **Rep close-ups** (`rep_closeups`, distance slots): per person `d_m` = metres since the own rep start × scale (to the
  set distance, capped; null after own finish) and `behind_m` = leader's `d_m` − own (along the track, not straight
  line; null after the leader's finish), on a 1 s grid, aligned at each person's **own** start (`own`, the default) and on
  the **clock** (`clock`: a late start counts; `start_delta_s`).
- **Breaks** between consecutive reps of each person (compare's `_break_stats`: duration, kind, HR peak, `drop_30/60/90`,
  `hr_next_start`) + `drop_30_pct` (of HR reserve, or of max HR) and `hr_next_start_pct`; `recovered_faster` = the
  person with the larger drop per break.
- **Effort** per rep: peak HR in the rep as % of HR reserve (`effort_mode: hrr`) or of max HR (`hrmax`, ⚠ caveat).

**Continuous** (together, partly together — only the span together —, race, hikes; for interval sessions only `apart`)
- **Gap over distance:** each person's position projected onto the reference's path in the overlap
  (`session_groups.project` onto `ref_polyline`), 50 m grid over the stretch everyone covered; gap = time at that
  point − the first person's on the **moving clock** (`clock: "moving"`: seconds in which anyone in the group moves,
  > 1 m/s, hikes > 0.25 m/s; `stopped_s` = the joint stops left out), so passing a point before / after a joint summit
  break isn't a 40 min gap. Races use plain elapsed time. Projection artefacts are nulled (`flagged` = count): a spike
  > 60 s off the 450 m running median, and (not races) a gap the positions can't support — more than
  (distance apart when the front passed + 50 m) ÷ 1.5 m/s (hike 0.25 m/s). **Who led** per 100 m (within 2 s = side
  by side, `person: null`). **km splits**: races on each person's own distance from their start (the whole race, also
  past the shared stretch), else along the common axis on the moving clock. `finish` = order at the end of the common
  stretch; `split_at_m` = where someone fell > 10 s behind for good (null if never).
- **Race** (`same_race`): the window is every participant's whole file (the faster one's file ends before the others
  finish); `race` = each person's own recorded race (elapsed time of the file), ranked. The page shows the "together"
  tiles and the distance-apart chart only when they were side by side ≥ 20 % of the time.
- **Distance apart** per pair every 5 s on the clock (straight line), `with` = the detection's with-each-other mask,
  `with_pct`, `within_20m_pct`, `median_apart_m` (while with each other).

Validation (2026-11-01, `together.py validate`: 0 failures over 19 groups; detected rep times equal each person's own
`compare --mode intervals` rep times, totals = sums, cumulative leader at 0):
2026-10-06 (fixed 2026-11-01): adrian's 1800s are 416.8 / 403.2 / 411.7 s and 900s 187.2 / 171.2 s (detector =
lap presses; they were 472 / 588 / 507 / 205 / 201 s "merged" because the 1754 m GPS reps had been snapped to 1600 m,
see app.md#rep-pace), adrian 5–0, +223.8 s over 5 reps (was "Patrik 2–3"); no other session changed.
2026-10-20: adrian 12:52.3, patrik 14:10.7 over 10 × 400 (+78.4 s, adrian 10–0, closest rep 10 +2.5 s; rep 1: patrik
≈ 54 m behind at 70 s); 2025-11-11: 6 shared reps, adrian 6–0 (rep 1 patrik ran 2 × 330 m, rep 2 split by his detector,
◐); 2024-04-14: patrik's reps from onsets (typed easy), +30–40 s per 1000 m (positions confirm 75–145 m behind at
adrian's finish), the 45 s rep a dead heat; 2026-10-06: patrik's split first 1800 merged (490 s vs 417 s);
2026-10-15: 1 shared 3000 m (adrian 691 s, patrik 737 s), adrian's 2nd cut at 2009 m (◐); race 2023-05-07 39:08 vs
43:26; 2026-09-10 easy run 97 % with each other, km splits within 2 s.

## API
All GET; they work unprefixed and under `/api/<person>/…` alike (the prefix only sets `viewer`; groups are global).
404 for an unknown gid (or no together.json), 422 for engine errors. Responses are memoised by together.json and every
input's mtime; engine calls share compare's lock. Warm ≈ 10 ms, cold session 0.05–0.3 s.

### `GET /api/together?person=&sport=&kind=&label=&near=1&place=&from=&to=&q=`
Filters: `person` (comma list, any of; default all), `sport` (families), `kind`, `label` (comma lists), `near=1` (also
hidden near-misses), `place` (substring of the place name / id), `from` / `to` (dates), `q` (all words in gid, date,
label, kind, workout, place, names).
```
{n, viewer: "patrik" | null, built,
 people: {adrian: {name: "Adrian", initials: "A", colour: "#2f5fd0", colour_dark: "#6f96ee", auto: false}, ...},
 h2h: [{a: "adrian", b: "patrik", sessions: 11, interval_sessions: 5, shared_reps: 26,
        rep_wins: {adrian: 25, patrik: 0}, rep_ties: 1, session_wins: {adrian: 5, patrik: 0},
        mean_gap_s: 21.8 /* b − a per shared rep: positive = a faster */}],
 groups: [{gid, date, start_local, start_utc, sport, sports, kind, label, label_text, score, hidden, no_track, place,
           workout, people, n_people,
           participants: [{person, activity_id, role, workout, signature, n_reps, dist_km, dur_s, together,
                           reps_done: 10, of: 15 /* intervals */}],
           headline: {kind: "intervals", winner: "adrian", rep_wins: {adrian: 10, patrik: 0}, rep_ties: 0,
                      shared_reps: 10, avg_gap_s_per_rep: 7.8, max_gap_s: 11.4, closest: {label: "10", gap_s: 2.5},
                      text: "Adrian 10–0 · 7.8 s faster per rep · closest: Rep 10, 2.5 s"}
                     | {kind: "race", winner, order, text: "Adrian 39:08 · Patrik 4:18 behind"}
                     | {kind: "together", with_pct, text: "side by side 97 % of the time" | "together 16–42 min"}
                     | {kind: "unverified", text: "Patrik has no watch file"}}]}
```

### `GET /api/together/{gid}`
```
{gid, requested_gid, viewer, kind, t0_utc, compute_s,
 group: <the together.json group>,
 participants: [{key: "patrik" /* "patrik#2" for a 2nd file */, person, activity_id, role, anchor, name, type, sport,
                 signature, workout, dist_km, dur_s, start_utc, start_local, offset_s, clock_fixed, has_fit, has_gps,
                 product, sample_s: 3.0, sample_p90_s: 8.0, together,
                 display: {name, initials, colour, colour_dark},
                 hr_norm: {max: 207, rest: null, mode: "hrmax", source: "watch_zones", warn},
                 shape: {score: 48.55, discipline: "interval", kind: "hard"} | null /* fitness.json */}],
 people: {person: display}, caveats: [{kind: sampling|clock|hr|no_gps|no_track|clock_jump|engine, person, text}],
 headline: {...as in the list},
 intervals: null | {
   anchor: "adrian", gps_scale: {adrian: 1.0, patrik: 1.024}, shared_slots: [0..9],
   slots: [{slot: 0, label: "1", anchor: true, owner: "adrian", target: {kind: "dist", value: 400, text: "400 m"},
            start_s: 555.8 /* group clock */, n_people: 2, shared: true}],
   reps: {person key: [cell | null per slot]},
     cell = {source: detector|onset, method: detector|interpolated|merged|partial|no_distance, partial,
             result_s: 86.5 /* ranked value */, time_s: 86.5, dist_m: 400, own_time_s: 86.5, own_dist_m: 412,
             start_s: 523.4 /* own file s */, clock_start_s: 555.8, scale: 0.9711, rank: 2, gap_s: 10.9, tie: false,
             tie_s: 1.5, suspect?: "reason", hr: 159, hr_peak: 184, hr_start: 120, effort_pct: 89, effort_mode: "hrmax"},
   standings: [{slot, label, order: [{person, result_s, rank, gap_s, gap_m, tie, tie_s}], partial: [keys], missing: [keys]}],
   leaderboard: [{rank, person, total_s: 850.7, avg_s, pace_s_km, gap_s: 78.4, reps_done: 10, extra_reps: 0, of: 15,
                  n_slots, reps_ranked, all_n, all_total_s, all_avg_s, all_pace_s_km, suspect: ["15"], rep_wins,
                  rep_ties, best: {slot, label, result_s}, fade_s, fade_s_km, avg_effort_pct, effort_mode}],
   cumulative: {slots: [slot indices], labels: ["1", ...], people: {key: [{total_s, gap_s} | null]}},
   breaks: {key: [{after_slot, after_label, next_slot, next_label, start_s, dur_s, dist_m, speed_kmh, standing_s, kind,
                   hr_peak, hr_30, drop_30, hr_60, drop_60, hr_90, drop_90, hr_min, hr_next_start, drop_30_pct,
                   hr_next_start_pct}]},
   recovered_faster: [{after_slot, after_label, person}],
   rep_closeups: [{slot: "1", available, leader: "adrian", target_m: 400,
                   own: {x0: 0, n: 89, people: {key: {d_m: [...], behind_m: [...], start_delta_s}}},
                   clock: {...same, x = s since the earliest start in the slot}}]},
 continuous: null | {available, why, reference, window_s: [a, b] /* group clock */, ref_length_m,
   clock: "moving" | "elapsed", stopped_s, split_at_m,
   gap_over_distance: {x_m: [0, 50, ...], x0_route_m, people: {key: [s behind the first | null, ...]}, flagged},
   led: [{from_m, to_m, person | null}], km_splits: [{km, people: {key: {time_s, rank, gap_s}}}],
   finish: [{person, rank, time_s, gap_s, clock_s}], common_m,
   race: [{person, rank, time_s, dist_km, gap_s}] /* same_race */,
   apart: [{a, b, t_s: [...], apart_m: [...], with: [bool], with_pct, within_20m_pct, median_apart_m}]}}
```
Interval sessions get `continuous` with `apart` only; `together`-labelled ones get the full continuous block.
About 20–130 kB per session (5–30 kB gzipped), dominated by the rep close-ups.

### `GET /api/together/{gid}/replay?step_s=2&max_points=4000`
```
{gid, available, t0_utc, step_s, note,
 people: {key: {person, role, t: [s on the group clock], lat: [...], lon: [...], hr: [...], dist_m: [...],
                route_m?: [metres along the shared route, continuous only; null outside the joint window]}},
 reps: [{slot, label, start_s, end_s}],      # rep windows on the same clock ("reps only" playback)
 segment_kind: "rep" | "km" | null,          # intervals: one segment per rep slot; continuous: per km of the route
 segments: [{i, kind, slot, label: "4", title: "Rep 4 · 400 m" | "km 3", target_m: 400 | null, from_m? /* km */,
             start_s, end_s /* earliest start, last finish, group clock */,
             people: {key: {start_s, time_s /* the time run in the replay */, scale, partial, result_s, rank}},
             passes: {own: [{x_s, at_m, who, whom}], clock: [...]},   # x_s = s into the segment on that axis
             total: {key: {total_s, gap_s, rank}} | null,             # total time so far, incl. this segment
             moves: [{person, from_rank, to_rank}] | null}],          # overall rank changes vs the previous one
 passes: [{t_s, at_m, who, whom, seg, label}],                        # the whole session on the group clock
 pass_rule: {min_lead_m: {rep: 2, km: 8, route: 10}, hold_s: {rep: 3, km: 8, route: 10}}}
```
Positions only where the participant has GPS (Bislett indoor laps are masked). 150–250 kB (35–75 kB gzipped);
segments + passes add ~0.05 s to a cold call.

- **Rep segments** (`_rep_segments`): every slot someone ran; per person the rep's group-clock start
  (`clock_start_s`) and the time at the set distance (`time_s`; partial reps: their own time). Metres into the rep =
  own distance since own start × the cell's `scale`, capped at the set distance, exactly at the line after a full
  finish. `total` = the session's `cumulative` (shared reps only, so extra reps have none); `moves` compares its
  ranks with the previous shared rep's.
- **Km segments** (`_km_segments`, continuous with `continuous.available`): each person projected onto the
  reference's path (`_route_progress`, the same projection as the gap over distance), km boundaries from where everyone
  is on it (a last piece < 300 m joins the last km); `total` = time from the person's start of km 1 to the end of
  this km. Session passes are computed over the whole joint window by metres along the route.
- **Passes** (`_passes`): per pair on a 1 s axis, the order flips and the new lead holds ≥ `min_lead_m` for
  ≥ `hold_s` consecutive seconds (GPS jitter / running side by side isn't a pass; the first established order isn't
  one); `x_s` / `at_m` = where the lead last changed sign. "own" = everyone from their own start (a virtual race on
  speed), "clock" = real time (a late start counts).

### Also
- `GET /api/together/people`: `{people: {id: display}}`; `GET /api/people` has the same as `info`.
- Activity rows (`/api/activities`, `/api/activities/{id}` → `activity.together`):
  `{gid, label, kind, role, with: [person, ...]}` when the activity is in a visible group (not as `other`), else null.
  Reloaded when together.json changes. `/api/search` rows carry the same `together` field (the 👥 marker in Activities).

## Pages (polish 2)
- List: `PageHeader` with `?` tips (labels explained; the sidebar badge = your sessions in the last 30 days);
  unverified rows say who has no watch file (one badge, no duplicate "no track").
- Session: rep labels "R3" / "Rep 3"; times with units ("45.3 s", ≥ 60 s "1:17.2"); gaps unsigned with words
  ("10.9 s behind"); "behind" charts plot the leader / front on top. Leaderboard Avg = time per rep + distance-weighted
  pace (`/km` on foot, km/h else), a sub-row with all the reps a person ran, Fade "0.7 s slower" with a tooltip, ? for
  suspect reps. Breaks: "Faster recovery" column (was 🫀). "Compare with my other <workout> sessions"
  (`/compare?suggest=<viewer's id>`) in the header, "compare ↗" per person (their own Compare). Replay button toggles
  (Replay ✕ when open). Overlapping map tracks: each line under the next is drawn 2.5 px wider (outline).
  Side-by-side gap chart: 250 m rolling median and at least a 15 s scale. Unverified: the GPS map of whoever has one,
  plain-language reason, proper-cased caveats. Tables scroll sideways on mobile; Best / Fade / Effort hidden < 500 px.
- Replay (`components/Replay.tsx`, Community "Replay upgrade"): chips **Whole session · R1 … Rn** (km 1 … km n for
  continuous sessions; dashed = only one person ran it, ⇄ = has passes). A rep chip, the ▶ at the end of each "Rep by
  rep" row (`?replay=1&rrep=<label>`) or the close-up's Replay ▶ starts **rep mode**: every dot at its own start of
  the rep (Own start) or on the real clock (Clock, the same `align` toggle as the close-up), all on one clock, the map
  zoomed to the rep (faint lines = each person's rep path, 20 s trails, initials in the dots, each dot waits at its
  finish). Live table: metres behind the leader → after the finish the time behind the first finisher, rep time,
  **total so far** (previous shared reps + the running rep; the session total at the finish). **Passes & moves** feed
  (newest first, only what has happened): "R4 · 210 m: Patrik passes Adrian", "Patrik moves up to 1st overall". At the
  last finish a **finish card** on the map (ranking, gaps, totals, rank changes) for 2.6 s, then **auto-advance** to the
  next rep with ≥ 2 people (checkbox). Whole session = the group clock (reps only skips breaks), live standings as
  before (continuous: metres along the shared route), the session passes in the feed and a short finish card after
  each rep. Speeds 1–16× (whole continuous sessions up to 64×; rep mode starts at the speed that runs a median rep in
  ≤ 30 s), scrub bar with rep bands / km ticks, finish ticks (person colours) and pass markers (▼ in the passer's
  colour). Keys: space play / pause, ← → previous / next rep (or km), shift ← → 5 s, 1–9 / 0 = rep 1–10, W = whole
  session. Person colours from `lib/people.ts` (map: light variant), both themes.

## Limits / next
- Thresholds were tuned on 18 adrian × patrik FIT pairs; re-check with a third person (pace 0.7 rule, 50 m radius).
- Set distances are inferred from signatures / rep distances; there is no manual override yet (T4).
- Along-track gaps use each person's own distance stream scaled to the set distance; snapping to the Bislett oval
  geometry would make them exact (T4).
- Continuous gap over distance projects onto the first participant's path; a participant who leaves it (detour) shows
  as a jump.
- Replay passes inherit the rep times: a rep whose detected end is late (2026-10-06 adrian R2 588 s, he's 20 m from
  the line at 7:52) shows as being passed near the line. Km / route passes use the projection's running maximum, so a
  track snapped to a later part of the path would be a (lasting) fake pass; the 10 m / 10 s rule only filters jitter.
  Interval passes need a distance stream (Bislett indoor laps without GPS have no dots, so no passes either).

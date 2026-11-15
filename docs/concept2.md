# Concept2 Logbook: RowErg and SkiErg workouts

Concept2 erg workouts (RowErg, SkiErg; BikeErg is mapped too) come from the
[Concept2 Logbook API](https://log.concept2.com/developers/documentation/) and become ordinary activities: they show up
in Activities, Calendar & day, Home, the activity page, Compare and the similarity index. They have their own fitness
trend ([erg.md](erg.md)). The watch never records these sessions, so there's nothing to deduplicate against Garmin.

## Day to day (how-to)
**After new erg sessions** (on the erg: log with ErgData as usual, so the Logbook gets the stroke data):
```sh
uv run scripts/refresh_concept2.py adrian
```
One command, ~15 s: fetches new / edited workouts (2 requests when nothing is new), cleans shared-machine sessions,
rebuilds session types, reps, workout names, the Row / SkiErg trend and the chart cache. The running app picks it up
by itself (reload the page; no restart). Then look at http://127.0.0.1:8765/adrian/fitness#erg.

**Shared a machine with friends** (taking turns): nothing to do, it's detected (see
[Cleaning](#cleaning-shared-machine-sessions)). Check that the session shows "(shared)" and your turns look right.
If it got it wrong, add a line to `data/adrian/session_overrides.json` (the id is the number in the activity URL):
```json
{ "113100837": {"erg_shared": false, "note": "solo, not shared"} }
```
(`true` = force shared, `{"slot_s": 120}` = 2 min turns), then `uv run scripts/refresh_concept2.py adrian --no-fetch`.

**Edited or deleted a workout on the Logbook**: the refresh picks edits up. Deleted ones are reported; remove them with
`uv run scripts/fetch_concept2.py adrian --prune`, then the refresh.

**Something looks off / start over**: `uv run scripts/refresh_concept2.py adrian --full` refetches everything
(~300 requests, a minute or two). Everything under `data/adrian/concept2/` can be deleted and refetched; your
corrections live in `data/adrian/session_overrides.json`, which is never touched.

**Token stopped working** (`401 from Concept2`): Logbook → Edit Profile → Applications → *Concept2 Logbook API* →
Regenerate, and put the new token in `.env` as `CONCEPT2_ADRIAN_TOKEN=…`.

**Another person**: they create their own token the same way (logged in as themselves), you add
`CONCEPT2_<NAME>_TOKEN=…` to `.env` and run `uv run scripts/refresh_concept2.py <name>` (the person needs a folder
`data/<name>/` from the Garmin fetch first).

**A friend's half of a shared session** (no own Logbook / token, e.g. Kristoffer 2026-11-07): copy the result into
their profile and mark them as the other turns:
```sh
mkdir -p data/kristoffer/concept2/results/<id> && cp data/adrian/concept2/results/<id>/* data/kristoffer/concept2/results/<id>/
```
then add the result's row from `data/adrian/concept2/results.json` to `data/kristoffer/concept2/results.json`, put
`{"<id>": {"erg_shared": {"who": "others"}}}` in `data/kristoffer/session_overrides.json`, and run
`uv run scripts/refresh_concept2.py kristoffer --no-fetch` (always `--no-fetch`: there's no token). A new profile needs
`data/<name>/activities_index.json` (`[]` without Garmin data) and a line in `data/people.json`.
**Three people on one machine** (e.g. 2026-11-15: Adrian, Kristoffer, Maren, 1:00 on / 2:00 off): the same, but pick
the friend's speed rank instead of `"others"`: `{"who": 2}` = the 2nd fastest turns (Kristoffer), `{"who": 3}` = the
slowest (Maren). Your own profile needs nothing (you = the fastest).
That day there were **two machines** (1:00 on each = 2:00 on / 1:00 off) and only one recorded; the other monitor
showed 10,842 m. `"second_machine_m": 10842` in each person's override adds the other machine as an estimate (their
turns here × 10842 / the recorded total): the session totals (distance, 30:00 work, pace, name "15×2:00, 2 machines")
include it, the reps and charts stay the measured turns on the recorded machine.

**A session that wasn't recorded** (no ErgData, only monitor photos; e.g. 2026-11-13 17:30: Adrian, Maren, Besjan, 2 machines,
2:00 on / 1:00 off × 10): add a Logbook-shaped row to `data/<person>/concept2/manual.json` for each person who was there
(the fetch never touches this file), then `uv run scripts/refresh_concept2.py <person> --no-fetch`. Ids start at
`concept2.MANUAL_ID_BASE` (2,900,000,000 and up; the same id for everyone in one session). `time` / `rest_time` = the
person's own work / rest (1/10 s), `distance: null` when the metres can't be split per person, `name` = the workout
name, `comments` = the note, and `manual` = what the monitors showed:
`{"people": [...], "on_s": 120, "off_s": 60, "rounds": 10, "session_s": 1800, "machines": [{"dist_m", "time_s",
"pace_500_s"}], "total_m": 14216, "source": "monitor photos"}`. It shows up as type "logged by hand" with a
*Logged by hand* row on the activity page, has no reps or charts, and stays out of the Row / SkiErg trend (erg_fitness
reads `results.json` only). A profile without a Logbook also needs `concept2/results.json` = `[]`.

**Full refresh of everything** (Garmin + Concept2): the order in the [README](../README.md#refreshing-the-data);
`fetch_concept2.py` sits next to the Garmin fetches, before `session_groups.py`.

Good to know:
- Erg metres are shown but never count in km totals; erg time does.
- No heart rate unless you pair a chest strap with the PM5 / ErgData.
- A real 2k (or 1k) test now and then on each machine anchors the trend best ([erg.md](erg.md)).

## Access
A personal, read-only token per person: Logbook → Edit Profile → Applications → *Concept2 Logbook API* (scopes
`user:read,results:read`; revoke or regenerate it on the same page). Put it in `.env` as `CONCEPT2_<PERSON>_TOKEN`
(template `.env.example`). The API has no rate limit today; `fetch_concept2.py` still pauses 0.2 s between calls.
A full fetch is about 3 requests per workout (result, strokes, FIT export). A re-run is 2 requests plus the new or
edited workouts.

## Fetch
```sh
uv run scripts/fetch_concept2.py adrian            # incremental; --full refetches everything, --prune drops deleted ones
```
Run it next to the Garmin fetch scripts, before `session_groups.py` ([README](../README.md#refreshing-the-data)).
It writes `data/<person>/concept2/`:

| File | What |
|---|---|
| `user.json` | Logbook profile |
| `results.json` | the list endpoint, newest first (the incremental check compares rows) |
| `results/<id>/result.json` | one result with `workout.intervals` / `workout.splits` (targets, stroke rate, rest) |
| `results/<id>/strokes.json` | per-stroke `t` (1/10 s), `d` (dm), `p` (1/10 s per 500 m), `spm`, `hr` |
| `results/<id>/<id>.fit` | Concept2's FIT export: per-stroke records (distance, speed, power, cadence = strokes/min), work / rest laps |
| `activities_index.json` | Garmin-style index rows (`concept2.sync`), merged with Garmin's by `concept2.load_index` |

and links (hard link) each FIT into `data/<person>/activities/<date>_<id>_<indoor_rowing|skierg|bikeerg>/` with a
`list_item.json`, so every FIT glob finds it. Erg folders whose result is gone from the Logbook are removed.

## How they map (`scripts/concept2.py`)
- **Id**: the Concept2 result id (~1e8; Garmin ids are ≥ 3e9, so no collisions).
- **Type / sport**: `rower` → typeKey `indoor_rowing` → sport `row` ("Row", label "RowErg"); `skierg` → `skierg`
  ("SkiErg"); `bike` → `bikeerg` → `bike`.
- **Time**: Concept2's `date` is the **start** (local), `date_utc` the start in UTC. `duration` = work + rest,
  `movingDuration` = work time; `distance` = work metres (Concept2's headline number; rest metres are separate).
- **Power** = 2.80 / (s per m)³ of the average work pace; `averageStrokeRate`, `calories`, HR when there is any.
- **`c2`** on the row: workout type, drag factor, stroke count, rest time / metres, intervals / splits, comments, user id.
- **Index readers**: `session_groups.py`, `similarity.py`, the app's `Store.activities` and `build_report.py` read
  `concept2.load_index` (Garmin + Concept2). Together / Community keep Garmin's index: they match people by GPS, and
  erg sessions have no position.

## Session types and reps (`session_groups.py`)
- `erg_intervals`: a Concept2 interval workout (`FixedTime/Distance/CalorieInterval`, `VariableInterval…`) with
  ≥ 2 work laps. Reps = the FIT's work laps (intensity active) with Concept2's own time / distance per interval
  (`concept2.erg_ana`, mode `erg`); breaks = the rests. They go into the rep cache like ski reps, so similarity,
  the app and Compare use the same reps.
- Shared-machine sessions are `erg_intervals` with your turns as reps (see Cleaning).
- `erg_piece`: continuous, ≤ 12 min work. `erg_steady`: continuous, longer.
- **Signature** (`session_groups.signature`, mode `erg`): consecutive reps with the same target, by time
  ("12×2:00", "8×4:00") or by distance ("4×500"); mixed sets "3×4:00 + 2×2:00". Workout families in the
  similarity index: `row/intervals`, `skierg/steady`, … ("Row intervals").
- The distance counts as trusted (`dist_ok`: the machine measures it), but **erg metres never count in km totals**
  (`store.volume_km`, frontend `trustedKm`, search km): their time does (user decision 2026-11).

## Cleaning: shared-machine sessions
Sometimes friends take turns on one SkiErg (or rower): 1 min on / 1 min off with two people, 1 on / 2 off with three
people on one machine, 2 on / 1 off with three people on two. The Logbook then has **everyone's strokes as one
continuous piece** (e.g. "40:00, 11,644 m"), which is far faster than any one person could go for 40 min (it made the
SkiErg trend look world-class). Cleaning finds these sessions and keeps **only your own turns**.

**How it's detected** (`concept2.shared_turns`, automatic, for continuous sessions ≥ 10 min with stroke data):
1. Cut the session into 60 s slots from the start (turns change on whole minutes).
2. Speed per slot from the strokes, leaving out each slot's first 15 s (the flywheel still carries the previous
   person's speed).
3. Group the slots by speed (2 groups, or 3 when the fastest is clearly apart from the next); **you = the fastest**
   (you're the fastest of the group; if someone faster joins, use an override).
4. It counts as shared when your slots are ≥ 4 % faster, form ≥ 3 separate turns that alternate with the others,
   and nobody else rows more than 3 min in a row. A solo piece that fades or an even piece is left alone.
5. The last slot counts when ≥ 90 % of it is there (the last stroke lands ~1 s before the end; before 2026-11-07 the
   last turn was lost in 11 of 17 sessions). In a regular on / off session, a slot in the last round that breaks the
   rhythm is yours only at ≥ 98 % of your turns' median speed: a friend sprinting the last minute isn't you.

**What changes** (`concept2.clean`): the session becomes your own interval workout: reps = your turns
(consecutive own minutes merge into one rep), rest = the others' turns. Distance, time, pace, watts and stroke rate
are yours; calories and the Logbook splits (everyone's) are dropped. The name gets "(shared)", e.g.
"SkiErg 20×1:00 (shared)", type detail "shared machine: 1:00 on / 1:00 off, own turns only". The Logbook's totals,
the slot pattern (`X` = you, `.` = others) and the others' average pace stay in the row's `c2.shared` and show on the
activity page. The charts still show every stroke (the others' turns are the unshaded parts). The erg fitness trend
scores these sessions as intervals ([erg.md](erg.md)).

**When it runs**: on every `fetch_concept2.py` and at the start of `session_groups.py` (cached per result in
`concept2/results/<id>/clean.json`, so it's instant), then the rest of the pipeline as usual.

**Correcting it** in `data/<person>/session_overrides.json` (the same file as other session corrections), keyed by
the Concept2 result id (the activity id in the app URL):
```json
{
  "109429504": {"erg_shared": true,  "note": "shared with Patrik, detection missed it"},
  "111431876": {"erg_shared": false, "note": "solo 40 min, not shared"},
  "120232865": {"erg_shared": {"slot_s": 120}, "note": "2 min turns"}
}
```
`true` forces cleaning (your slots = the fastest group even if the checks fail), `false` turns it off,
`{"slot_s": 120}` sets another turn length, `{"who": "others"}` keeps the other turns (a friend's profile), `{"who": 2}` / `{"who": 3}` the 2nd / 3rd fastest of three. Then run `uv run scripts/session_groups.py <person>` and the rest of the
chain (similarity, app cache); the erg trend rebuilds by itself.

Found for adrian (2026-11): 16 SkiErg sessions (Nov 2025 – Oct 2026), mostly 1 on / 1 off, one 1 on / 2 off
(2026-09-04). Example: 2026-02-19 "40:00, 11,644 m" → 20×1:00, 6,021 m own work at 354 W; the others averaged 1:48 /500 m.

## In the app
- `/api/activities` rows have `erg`: machine, workout type, pace /500 m (`pace_500_s`), watts, stroke rate, drag factor,
  work / rest, intervals, `logbook_url`.
- Pace is per 500 m everywhere (`format.ts` `isErg`, `pace500`, `speedText`): lists, the activity page, reps, Compare.
- Activity page: no map; summary = machine (+ Logbook link), work / rest, metres, pace, watts, stroke rate, drag
  factor; charts = pace /500 m, power, stroke rate (and HR when recorded); Reps tab = the erg intervals with watts,
  stroke rate and the monitor's target pace; laps = the monitor's work / rest laps.
- Shared-machine sessions have an **Only me / Full workout** switch at the top of the activity page (`?view=full`).
  Only me: the charts show your own turns only (the others' turns are gaps, and each turn's first 8 s and last 1 s are left out:
  measured over 60 swaps, the speed carries the previous person's pace for 4-8 s, the stroke rate spins up and settles
  by 8-9 s and sags in the last 1-2 s; the first turn too, its spin-up from standstill would stretch the pace axis); Segments = "My turns" with
  Break = the others' turns. Full workout: every stroke, your turns shaded; Segments = "All turns", yours and the
  others' in order with metres, pace, watts and stroke rate (median without the handover seconds).
- **Handover cut (adaptive):** each own turn's first seconds are left out until the monitor pace has settled within
  2 % of the turn's steady pace (5 strokes in a row), at least 8 s and never more than 10 s (slower after that is the
  rower's own start; user decision 2026-11-15). `concept2.turn_profiles`, served by
  `GET /api/activities/{id}/shared-turns` (every person who has the session, their turns with `cut_s`,
  `steady_pace_s` and the trimmed stroke pace). Taking over from a much slower person needs the longest (2026-11-15:
  Adrian after Maren 8-24 s, Kristoffer after Adrian ~4 s).
- **No breaks** (Series, Only me, `?breaks=off`): the turns joined end to end, x = work time, so the curve shows the
  pace and its progression only.
- **Turns** section (shared sessions, `components/SharedTurns.tsx`): the steady pace per person, "Pace per turn"
  (x = turn, a line per person) and "Pace curve" (each person's stroke pace inside their turns, handover cut, turns
  joined), for **Me** or **Everyone**, in each person's colour (`data/people.json`).
- FIT cadence of fitness equipment (FIT sport 4) is strokes/min as stored (running files store strides, doubled to
  steps/min in `fitcache.read_fit`).

## Limits
- No HR in the data unless a strap was paired with the PM5 / ErgData; HR-based features (zones, HR flags, form delta)
  are empty for ergs.
- A workout without stroke data (entered by hand) has no FIT: summary only, no charts or reps.
- The Logbook API also offers a webhook (result added / updated / deleted); not used, since the app only runs locally.

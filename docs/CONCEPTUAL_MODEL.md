# Conceptual model: start here

What the pieces are and how they fit. Details live in the linked docs; this page gives you the terms and the map.
All data is local (`data/`, gitignored). The app and the report only read files the pipeline wrote.

## 1. Data flow

```
 Garmin Personal API                                     Concept2 Logbook API (RowErg, SkiErg; docs/concept2.md)
        │  fetch_index.py / fetch_activities.py / fetch_daily.py     │  fetch_concept2.py → concept2/ + concept2.sync
        ▼  (scripts/garmin_client.py: tokens, throttle, 429s)        ▼  (Garmin-style rows, FIT links in activities/)
 data/<person>/  activities_index.json + concept2/activities_index.json (concept2.load_index merges them)
                 activities/<date>_<id>_<type>/{activity,details,…}.json + <id>.fit
                 daily/<date>/…   ranges/…   profile/{user_settings,heart_rate_zones}.json
        │
        ├─ session_groups.py ──► groups.json            places, areas, routes, legs, pauses, session types, reps,
        │        │                                     tracks (uses run_analysis.py + ski_intervals.py rep detectors;
        │        │                                     + session_overrides.json, hr_settings.json flags)
        │        ├─ similarity.py ──► similarity_index.json + tracks.json   workout hierarchy, main place, nearest
        │        ├─ hr_settings.py ──► profile/hr_settings.json  max / resting HR, per-device trust, HR flags
        │        └─ fitness_model.py ──► fitness.json   shape per discipline, distance curve, race prediction, races
        │                 │   (+ data/races.json, hr_settings.json)
        │                 └─ form_delta.py ──► cache/form_delta.json        session score − form (on demand)
        └─ garmin-app-cache (app/server/fitcache.py) ──► cache/series/<id>.npz + cache/tracks.json
                                                                                   │
 all people ─► together.py build ──► data/together.json   (+ data/_together/*.pkl)  │
                     └─ community.py (on request) + data/community.json, data/_community/photos/
                                                                                   ▼
               FastAPI app/server (routers/*) ─► Preact app/web/src      build_report.py ─► report.html
```

| Artefact | Producer | Main consumers |
|---|---|---|
| `data/<p>/activities_index.json`, `activities/…/<id>.fit` | `scripts/fetch_index.py`, `scripts/fetch_activities.py` | everything |
| `data/<p>/groups.json` (`places`, `areas`, `routes`, `activities`) | `scripts/session_groups.py` | similarity, fitness, compare, together, app, report |
| `data/<p>/similarity_index.json`, `tracks.json` | `scripts/similarity.py build` | app (place model, route names, search, nearest) |
| `data/<p>/fitness.json` | `scripts/fitness_model.py` (also the app's `POST /api/fitness/rebuild`) | app Fitness / Race predictor / Races / Shape, `form_delta` |
| `data/<p>/profile/hr_settings.json` | `scripts/hr_settings.py` (after session_groups; `"source": "manual"` is never overwritten, `manual` values survive) | fitness model (HR variant: max / rest HR, device trust), session types (steady vs easy), Together effort |
| `data/<p>/session_overrides.json` (optional, hand-edited) | you | session_groups (type), fitness model (`exclude` / `bound`) |
| `data/races.json` (optional, hand-edited) | you | fitness model (official races, `not_races`, `tests`), `calibration_set.py`, app Races |
| `data/calibration.json` (optional, hand-edited) | you | `calibration_set.py` (who calibrates the shared fitness parameters) |
| `data/<p>/cache/series/<id>.npz`, `cache/tracks.json` | `garmin-app-cache` (`app/server/fitcache.py:main`), also at server start | app activity pages, maps |
| `data/together.json` | `scripts/together.py build` (runs once, over every person) | app Together / Community, workout label |
| `data/community.json`, `data/_community/photos/` | the app (`/api/community/…`, `scripts/community.py`) | Community pages |
| `data/people.json` (optional, hand-edited) | you | display names / colours |
| `data/<p>/report.html` | `scripts/build_report.py` | standalone report |

Run order and commands: [README.md](../README.md#refreshing-the-data); folder layout: [data-layout.md](data-layout.md).
Every derived step is incremental and caches per FIT file in `data/<p>/cache/*.pkl`, keyed by FIT mtime + size
(+ detector / reader / parameter versions). The server reloads the JSON artefacts when their mtime changes.

## 2. Core entities

**Person** — a folder `data/<person>/` with an `activities_index.json` (`app/server/deps.py:people`). Helper folders (`_together/`, `_community/`)
have no index, so they are not people. Every API route is `/api/<person>/…` and every page
URL `/<person>/…`; Together and Community are cross-person (`data/together.json`). One server serves everyone.

**Activity** — one Garmin activity: index row + FIT file, keyed by Garmin activity id. FIT is read on a 1 Hz grid by
`session_groups.read_series` / `to_grid` (watch clock jumps removed via `gps_quality.clock_jumps`); the app reads
the same way (`fitcache.read_fit`). `sport` (run, ski, hike, …) and the analysed `type` live in
`groups.json` `activities[id]`. Background: [fit-files.md](fit-files.md).

**Session type** — rule-based label per activity in `session_groups.py`: runs → `intervals_track` /
`intervals_road` / `intervals_treadmill`, `tempo`, `long`, `steady`, `easy`, `race?`, `run_nogps`, `run_other`;
ski → `ski_intervals` / `ski_easy` / `ski_hard` / `ski_long`; Concept2 ergs (`row`, `skierg`) → `erg_intervals` /
`erg_piece` / `erg_steady` (reps = the erg's own intervals, [concept2.md](concept2.md)); other sports keep their Garmin type. Helpers:
`recovery_share` (intervals need real recoveries), `steady_or_easy`, `multisport_family`, `pool_swim`. Track vs
road is where the reps happen (`run_track`, `TRACKS`). User corrections (`session_overrides.json`) win.
[session-groups.md](session-groups.md).

**Workout structure** — what happened inside a session:
- *Reps / breaks*: run reps from `run_analysis.analyse` (change points on the distance-derived speed;
  [run-analysis.md](run-analysis.md)); ski reps from `ski_intervals.analyse` (lap workouts or hard efforts);
  Bislett indoor reps from laps (`session_groups.indoor_grid`). Stored as `reps`, `breaks_s`, `rep_starts`, `sig`
  in `groups.json`.
- *Signature* (`sig`, "6×1000", "3×1800 + 2×900", ski "4×4:00") from `session_groups.rep_groups`; *main part* =
  the rep group with the most rep time (`main_sig`).
- *Warm-up / cool-down*: what lies before the first / after the last rep. Compare's `start="rep1"` cuts it off;
  Together marks reps only one person ran outside the main set as WU / CD (`_mark_warmup`) and never ranks them.
- *Steady run*: a continuous run faster / harder than the person's easy pace (`steady_or_easy`); a lower bound in
  the shape model, like an easy run.

**Place** — a start cluster (greedy density clustering of first GPS fixes) in `groups.json` `places`, named by
`session_groups.name_places` (user `places.json` > `KNOWN` > `LANDMARKS` > cached reverse geocode >
`<Garmin location>-<rank>`). Id stays stable, only the display name changes. An activity's **place** in the app is
its *main place* from the similarity index (named place with ≥ 40 % of its time, else its start cluster).
[app.md](app.md#place-model), [similarity.md](similarity.md#places-and-regions).

**Route** — activities of one sport family whose tracks overlap on a 75 m grid (min coverage ≥ 0.6), ids
`R1, R2…` by size, in `groups.json` `routes` (`medoid`, `ref` = medoid thinned to 20 m, `shape` laps /
out_and_back / point_to_point / loop_cw / loop_ccw). Display names come from the similarity index
("R3 Holmenkollen climb: Marienlyst → Frognerseteren …").

**Leg** — two meanings: (1) per activity, a pause-free stretch (`groups.json` `activities[id].legs`, pauses =
speed < 0.5 m/s for ≥ 15 s or timer stop, `session_groups.find_pauses`); (2) per route, a section between
common stops (waypoints) or ~1 km pieces, with each member's time / rank (`routes[R].legs_table`, via
`session_groups.project` onto the medoid).

**Similar / nearest** — `similarity.Index.nearest(id, k, weights)` scores every same-sport activity on components
(route, place, workout, pace, hr, distance, duration, climb, season, recency; presets `balanced` / `workout` /
`route` / `place`). The workout hierarchy is `family` (`run/intervals`, `ski/easy`, `hike` …) > `signature` >
`exact_key` ("6×1000 · rest ~2:30", or "ski/easy · R3"). App: "Closest activities", `/api/nearest/{aid}`,
`/api/search`, `/api/groups`. (`groups.json` also has an older top-5 `similar` list.) [similarity.md](similarity.md).

**Shape** — fitness on the Daniels VDOT scale, `scripts/fitness_model.py` → `fitness.json`:
- *Disciplines*: `run` ("Run fitness"), `ski`, plus `overall` (% of personal best, precision-weighted); `interval` /
  `long` are legacy read-outs of the run curve, not shown.
- *Evidence*: per session 0..n scored items (`collect_evidence`): races, interval sessions (W′-balance model),
  tempo / long windows, ski (mechanical power → VO2); kinds `race`, `hard`, `bound` (lower bound only). Official
  results in `data/races.json` make a session a race at its chip time (confirmed `tests` / time trials too, as race
  evidence at their own distance, but not official races); `session_overrides.json` can exclude a session
  or make it a bound.
- *Personal distance curve* (`smooth_curve`, `CURVE`): running is one model, VDOT(km) = θ + s·log2(km/5);
  `run` = curve at 10 km (the one running number the app shows, "Run fitness"); legacy read-outs `interval` = 5 km,
  `long` = 14.5 km. Ski uses its own `smooth_series`.
- *Race prediction*: `race_prediction` / `race_equivalents_s` (via `race_time(vdot, dist)`), app page
  `/predictor`, `/api/fitness/predict`. Past races: `races` with the pre-race prediction (`pre_race_prediction`,
  data strictly before race day).
- *Calibration set* (`calibration_set.py`): shared parameters (offsets, τ, σ, priors) are chosen on persons with ≥ 30
  trusted interval sessions and ≥ 2 races (adrian, rise); others are validation only.
- *Stale*: no race / hard evidence for > `STALE_DAYS` (90) → greyed in the app.
- *Form delta* (`scripts/form_delta.py:form_delta`): session score − shape the day before from a leave-one-out
  refit, at the session's own distance; `None` for bounds (easy / long runs, hikes).
[fitness-model.md](fitness-model.md).

**Compare** — `scripts/compare.py:compare(ids, mode, …)`, `/api/compare`, page `/compare`. First id (or
`reference="median"`) is the reference. Modes: `elapsed` / `moving` (time axis), `distance` (route position:
ghost race on the medoid's reference, legs), `intervals` (reps aligned, breaks collapsed), `breaks` (break-aligned).
Optional common **start line** (`start="ref" | <id> | (lat, lon)`, sessions cut by `_window`; late joiners kept) or
`start="rep1"` (`_align_rep1`). [compare.md](compare.md).

**Together group** — `scripts/together.py`: for every pair of people with overlapping activities, positions /
speeds / rep onsets on the shared 1 Hz UTC clock give a pair label (`same_workout`, `together` ["Side by side"],
`partly_together`, `same_race`, `unverified`, hidden `same_place_diff_workout`). Groups = connected components;
id `YYYY-MM-DD_HHMM_<place>`; participants have a `role` (`full` / `partial` / `other`) and a clock `offset_s`.
`together.session(gid)` gives per-rep standings / splits, `together.replay(gid)` the replay. API
`/api/<p>/together…`; page `/community/<gid>` (`TogetherSession.tsx`). [together.md](together.md).

**Community / club** — `scripts/community.py` on top of Together: each visible Together group is a club
**session** with a category (`intervals`, `tempo`, `long`, `easy`, `race`, `other`; `auto_category`, manual wins),
user metadata from `data/community.json` (title, note, guests, photos, kudos, hidden), collective numbers
(km, moving time, reps, climb) and each member's `form_delta`. **Members** = people with a watch file in a session;
**club dashboard** (`club()`, `/api/community/club`, page `/community/club`), member profile (`member()`), goals,
"faster together than alone" (`company_effect`). No rivalry or records by design. [community.md](community.md).

## 3. Design rules and invariants

- **Local only.** Health data stays in `data/` (gitignored); the server binds 127.0.0.1 by default. The only outside calls
  are the Garmin fetch scripts and the one-off cached OSM reverse geocode (`geocode_places.py`, centre coordinates only).
- **Multi-person layout.** Everything per person lives in `data/<person>/` and every per-person script takes the
  person as its argument. Cross-person data (`together.json`, `community.json`, `people.json`, `_together/`,
  `_community/`) sits at the top of `data/`.
- **One time base.** All FIT readers (session_groups, run_analysis, compare, together, the app cache) share
  `read_series`' clock-jump fix, so rep times, legs and replays line up.
- **Distance trust.** `dist_trusted` (session_groups `F["dist_ok"]`: GPS-backed, or a pool swim by lengths) is the
  gate. Untrusted distances (no GPS, treadmill, Bislett indoor) are shown as "—" or as an estimate (`dist_est_km`),
  left out of km totals, paces, records, shape scores and similarity's distance component.
- **Distance field, not `enhanced_speed`.** Speed is Δdistance per second on the 1 Hz grid; `enhanced_speed`
  is 3–5 s late, cadence / power 4–6 s late, HR lags by tens of seconds.
- **Reps come from speed, not the lap button.** The detected speed jump is the truth; a lap press only confirms
  or flags it ("pressed early by X s"). Intervals need real recoveries (`recovery_share`). Track reps count as
  their set distance (`track_set_dists` / `snap_track_reps`, `gps_dist` keeps the measurement). Ski uses its own
  detector because speed thresholds fail on terrain.
- **One rep list everywhere.** groups.json reps feed compare, together, similarity, the app's Reps tab and the
  workout label (ski / indoor reps via `fitcache.reps_from_groups`).
- **HR is not a shape input.** Tested three ways (effort adjustment, HR-only aerobic estimate, plausibility gate);
  all made forecasts worse because wrist HR under-reads hard intervals erratically and shifts with the watch. Scores
  never use HR (`hr_used_in_score: false` for the default pace variant); the `hr` variant only uses it to judge
  whether a hard session was maximal, weighted by the device's trust. HR *is* used for classification (steady vs
  easy, ski hard / reps) and for effort (% HR reserve) in Together, with the person's own max / rest HR and
  implausible-HR flags from `hr_settings.json`.
- **Shape is performance-only and honest about gaps.** Easy runs are lower bounds, uncertainty grows without
  evidence, stale values are flagged, no gain is extrapolated past the longest evidenced distance.
- **Stable ids, changeable names.** Place ids, route ids and Together gids stay stable across rebuilds (Together
  keeps old ids as `aliases`); overrides change display names only.
- **One workout label.** `store.workout_name`: own rep signature > the Together group's shared structure > own
  signature > workout family / Garmin type ([app.md](app.md#workout-label)).

## 4. Glossary

| Term | Meaning (where) |
|---|---|
| activity id | Garmin's id; folder `activities/<date>_<id>_<type>/` |
| type / family | session_groups session type (`intervals_track` …) / similarity family (`run/intervals` …) |
| sig, signature | rep structure "6×1000", "3×1800 + 2×900", ski "4×4:00" |
| main part, `main_sig` | the rep group with the most rep time |
| exact_key | signature + clustered rest ("6×1000 · rest ~2:30"), or family + route |
| rep, break | detected work segment / the recovery between two reps |
| strides | short fast bits, left out of the main reps (`type_detail` tag) |
| WU / CD | warm-up / cool-down rep (Together: run by one person outside the main set) |
| avg rep pace | total rep distance / total rep time of the main reps (`store.rep_pace`) |
| moving time | elapsed − pauses ≥ 15 s (`moving_s`) |
| GAP | grade-adjusted pace (Minetti cost) |
| leg | pause-free stretch of an activity, or a route section between waypoints |
| waypoint | a common stop on a route (≥ 30 % of sessions stop there) |
| medoid, `ref` | the route's most typical session, thinned to 20 m, the axis for route projection |
| place / main place / start place | start cluster / where ≥ 40 % of the time was spent / start cluster (detail) |
| area, region | start clusters chained at 2 km / named regions from the similarity index |
| dist_trusted | distance is GPS-backed (or pool lengths); else excluded from totals and paces |
| Bislett indoor | winter laps of the 546.5 m indoor track, reps from laps, distance untrusted |
| shape | VDOT-scale fitness per discipline (run, ski, overall) |
| Run fitness | the personal distance curve at 10 km (10k-equivalent VDOT), `disciplines.run` |
| official race, not_races | user-supplied result in `data/races.json` / an auto-detected `race?` the user says wasn't one |
| test (time trial) | user-confirmed all-out effort in `data/races.json` `tests`: race evidence (σ 1.0, own distance), not an official race |
| session override | `session_overrides.json`: corrected type, `fitness: exclude \| bound` |
| track, loop | known running track by rep location (`TRACKS`: Bislett, Øya stadion: flat); Frogner stadion is a loop (road with grade, reps at whole laps) |
| device trust | how much a watch + HR source's HR is believed (0..1, `hr_settings.json`) |
| VDOT | Daniels "running VO2max from performance"; maps to race times |
| evidence (race / hard / bound) | per-session score item; bound = lower bound only |
| distance curve, θ, slope | VDOT at 5 km and change per doubling of distance |
| stale | no race / hard evidence for > 90 days |
| form delta, "vs form" | session score − leave-one-out shape the day before |
| reference (compare) | first id or the median; deltas are session − reference |
| ghost race | `distance` mode: everyone on the route's axis, gap to the reference |
| start line, late joiner | common start for compare; a session joining the anchor's path after the line |
| Together group, gid | sessions of ≥ 2 people done together, `YYYY-MM-DD_HHMM_<place>` |
| Side by side | UI name of the `together` label |
| near-miss | `same_place_diff_workout`, hidden group (`near=1`) |
| role | `full` / `partial` / `other` participant in a group |
| club session, category | a visible Together group in Community; intervals / tempo / long / easy / race / other |
| member, guest | person with a watch file in a session / a name without one |
| company effect | form delta with company vs alone (`community.company_effect`) |

## Further reading
[app.md](app.md) · [data-layout.md](data-layout.md) · [session-groups.md](session-groups.md) ·
[run-analysis.md](run-analysis.md) · [similarity.md](similarity.md) · [fitness-model.md](fitness-model.md) ·
[compare.md](compare.md) · [together.md](together.md) · [community.md](community.md) ·
[distance-calibration.md](distance-calibration.md) · [report.md](report.md) · [README.md](README.md) (everything else)

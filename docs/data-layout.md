# Scripts and data folder

Credentials: `.env` with `GARMIN_<PERSON>_API_KEY` (e.g. `GARMIN_ADRIAN_API_KEY`; template `.env.example`). Gitignored.
Python via uv (`uv sync`). Don't use the macOS system Python: its LibreSSL can't do the TLS version Garmin requires.
Run order: [README.md](../README.md#refreshing-the-data).

## Scripts (`scripts/`)
Fetching (Garmin Personal API, [garmin-api-guide.md](garmin-api-guide.md)):
- `garmin_client.py`: library: API key → bearer token, request log (`_requests.jsonl`), 429 backoff, `min_interval` throttle.
- `fetch_index.py <person> [--since YYYY-MM-DD]`: full activity list plus daily summaries, one request per year.
- `fetch_activities.py <person> --days N [--types running,track_running] [--min-interval S]`, or `--ids 123,456` /
  `--ids-file f.txt` (looked up in `activities_index.json`); `--parts activity,details,fit` (default; `fit` alone =
  1 call per activity, `workouts` opt-in). Skips activities that already have a `.fit`.
- `fetch_daily.py <person> --days N [--only heart_rate,sleep,...] [--min-interval S]`: per-day, range and profile endpoints.
- `select_activities.py <person> [--track-lat --track-lon --radius]`: offline; writes `candidates/*.txt` id lists for `--ids-file`.

Fetching (Concept2 Logbook API, [concept2.md](concept2.md); token `CONCEPT2_<PERSON>_TOKEN` in `.env`):
- `fetch_concept2.py <person> [--full] [--prune]`: erg results, strokes and FIT exports → `concept2/`, then
  `concept2.sync`: Garmin-style index rows + FIT links in `activities/`.
- `refresh_concept2.py <person> [--full] [--no-fetch]`: the whole erg chain in order (fetch, session_groups,
  similarity, erg_fitness, app cache); `--no-fetch` after editing `session_overrides.json` ([concept2.md](concept2.md#day-to-day-how-to)).
- `concept2.py`: library: `load_index` (Garmin + Concept2 activity list, used by the pipeline and the app), `sync`,
  `erg_ana` / `erg_type` (erg reps and session types for session_groups).

Analysis pipeline:
- `session_groups.py <person>`: places, routes, session types, legs and pauses, reps → `groups.json` ([session-groups.md](session-groups.md)).
- `ski_intervals.py <person> <id> ...`: library + CLI: ski interval detector used by session_groups and compare; prints reps and work blocks.
- `geocode_places.py <person> [--dry-run|--repick|--limit N]`: one-off OSM Nominatim reverse geocode of generic start-cluster names → `cache/geocode.json`.
- `similarity.py <person> build|search|nearest|groups|validate`: similarity index → `similarity_index.json` + `tracks.json` ([similarity.md](similarity.md)).
- `fitness_model.py <person> [--eval] [--workers N]`: shape model → `fitness.json` ([fitness-model.md](fitness-model.md)).
- `hr_settings.py <person> [--dry-run] [--workers N]`: HR settings per person and per device (watch + wrist / chest strap):
  max HR, resting HR, device trust for the fitness model's HR variant, implausible-HR flags per activity →
  `profile/hr_settings.json` (format below). Needs `groups.json` (session types); run it after `session_groups.py` and
  before `fitness_model.py` (session_groups reads the flags of the previous build).
- `together.py build|list|show <gid>|validate`: workouts run with others, across all people → `data/together.json` ([together.md](together.md)).
- `compare.py <person> <mode> [ids] [--route|--sig|--place|--last|...]`: compare engine (library + CLI) used by the app ([compare.md](compare.md)).
- `form_delta.py [person] [activity_id] [--series|--together]`: session score vs form going into it (library + CLI; [fitness-model.md](fitness-model.md#form-delta-scriptsform_deltapy)).
- `community.py`: library for the app's Community pages: session metadata, photos, categories, club numbers ([community.md](community.md)).
- `run_analysis.py`: library: per-second rep/break detection on a FIT file ([run-analysis.md](run-analysis.md)).
- `gps_quality.py`: library: GPS quality indicators, clock-jump fix and fast FIT reader; distance uncertainty from the model.
- `build_report.py <person>`: self-contained `report.html` (template `report_template.html`; [report.md](report.md)).

Distance calibration ([distance-calibration.md](distance-calibration.md)); order: `calibrate_distance` (geometry) →
`gps_lap_analysis` → `evaluate_detector` → `route_accuracy` → `calibrate_distance` again:
- `gps_lap_analysis.py <person>`: GPS-mode manual laps at Bislet vs track geometry (hidden lag, lap booking) → `gps_lap_analysis.json`.
- `evaluate_detector.py <person>`: rep detector v1.1 vs v2 against lap presses and geometry → `detector_eval.json`.
- `route_accuracy.py <person> [--no-osm] [--osm-fetch] [--osm-clusters N]`: races, repeated routes, OSM → distance uncertainty model → `route_accuracy.json`.
- `calibrate_distance.py <person>`: Bislet track model, distance sources vs geometry and certified races; merges the three files above → `calibration.json`.

Experiments (print tables, change nothing):
- `fitness_eval.py [person]`: fitness model back-tests → `fitness_eval.json` (also `fitness_model.py --eval`).
- `shape_split_eval.py [person ...]`: separate vs merged vs coupled running shapes (distance curve choice).
- `slope_prior_eval.py [person ...]`: distance-curve slope shrinkage grid.
- `rest_model_eval.py [person] [--no-cache]`: how well the interval W′-balance model captures rest length.
- `calibration_eval.py [--persons a,b] [--validate c]`: shared running calibration (tau_scale, interval_offset, tempo
  residual, forecast MAE) pooled over the calibration persons, every other person as validation rows.

Calibration set:
- `calibration_set.py`: library + CLI: which persons drive the shared model parameters (≥ 30 trusted interval sessions
  and ≥ 2 running races → weight 1, else 0 = validation only; `data/calibration.json` overrides). The eval scripts
  above default to these persons ([fitness-model.md](fitness-model.md#calibration-set)).

## Layout (`data/` is gitignored: personal health data)
```
data/people.json                         optional: {person: {name, initials, colour, colour_dark}} (hand-edited)
data/races.json                          optional: official race results + `not_races` + `tests` (hand-edited; fitness_model.py)
data/calibration.json                    optional: {"weights": {person: w}} manual calibration-set weights (calibration_set.py)
data/together.json                       workouts run with others (together.py)
data/_together/                          together.py caches (cache.pkl, sessions.pkl); not a person folder
data/community.json                      Community session metadata (title, category, note, photos, kudos; community.py)
data/_community/photos/<gid>/            Community photos (<id>.jpg + <id>_t.jpg thumbnail)
data/<person>/
  _requests.jsonl                        one line per HTTP request
  activities_index.json                  all Garmin activities (list endpoint)
  activities/<YYYY-MM-DD>_<id>_<type>/   list_item.json  activity.json  details.json  [workouts.json]  <id>.fit
                                         (Concept2: <date>_<id>_indoor_rowing|skierg/ with list_item.json + a FIT link)
  concept2/                              user.json  results.json  activities_index.json  results/<id>/{result,strokes}.json + <id>.fit
  erg_fitness.json                       erg trend per machine (erg_fitness.py, docs/erg.md)
  daily/<YYYY-MM-DD>/                    heart_rate.json  sleep.json  hrv.json  spo2.json  food_logs.json  fitness_age.json
  ranges/<endpoint>_<start>_<end>.json   daily_summaries, steps, stress, body_battery, sleep_summaries, weight, ...
  profile/                               user_settings.json  heart_rate_zones.json  hr_settings.json (hr_settings.py or hand-edited)
  candidates/*.txt                       activity id lists (select_activities.py)
  groups.json                            places / routes / session types / legs (session_groups.py)
  similarity_index.json  tracks.json     similarity index + encoded track polylines (similarity.py)
  fitness.json  fitness_eval.json        shape model and its back-tests (fitness_model.py, fitness_eval.py)
  gps_lap_analysis.json  detector_eval.json  route_accuracy.json  calibration.json   distance calibration
  osm_cache/                             Overpass replies (route_accuracy.py)
  places.json                            optional: start-cluster name overrides (session_groups.py)
  regions.json                           optional: extra / replaced named places and regions (similarity.py)
  indoor_bislett.json                    optional: `time_only` / `rep_start` overrides for Bislett indoor sessions (session_groups.py)
  session_overrides.json                 optional: user corrections of session type / fitness use (session_groups.py, below)
  report.html                            build_report.py
  screenshots/<YYYY-MM-DD>/              report screenshots for visual checks
  cache/
    session_groups.pkl                   rep detector output per FIT (mtime+size)
    similarity_tracks.pkl                thinned tracks per FIT
    fitness_model.pkl                    per-session fitness features
    hr_scan.pkl                          per-FIT HR scan: device, HR source, peaks, cadence lock, steady chunks (hr_settings.py)
    compare.pkl                          compare engine analyses / route projections
    form_delta.json                      form-delta references (keyed by fitness.json)
    geocode.json                         reverse-geocoded start-cluster names (geocode_places.py)
    series/<id>.npz  series_manifest.json  tracks.json   app FIT cache (garmin-app-cache, app/server/fitcache.py)
```

`data/<person>/calibration.json` (distance calibration, `calibrate_distance.py`) and `data/calibration.json` (who
calibrates the shared fitness parameters, `calibration_set.py`) are different files. A `data/<person>/settings.json`
(`{"distance_curve": false}`, the removed per-person distance-curve setting) is no longer read by anything.

## `data/races.json` (hand-edited, optional)
Official race results, read by `fitness_model.py` on every build (`load_official_races`, `load_not_races`, `load_tests`), by
`fitness_eval.py --races` and `calibration_set.py`:
```
{"_note": "...",
 "races": [{name, date, sport (run | ski), km, class?,
            results: {<person>: {official ("39:50"), official_s, activity_id (matched Garmin activity; null = not in
                                 the Garmin data, skipped by the model), source? (free text; "watch ..." = a watch
                                 time, not a chip time), watch_s?}}}],
 "not_races": {<person>: [{activity_id, note}]},
 "tests": {<person>: [{activity_id, date, km, time_s, time ("10:27"), note}]}}
```
- A listed activity becomes a race at the official distance and time (run) or a `hard` item with σ `ski_race` (ski),
  whatever session_groups typed it ([fitness-model.md](fitness-model.md#per-session-scores)).
- `not_races`: auto-detected `race?` sessions that weren't races; the model treats them as `steady` (lower bound).
- `tests`: user-confirmed all-out tests / time trials. The fitness model scores them as race evidence at their own
  distance (`km`, `time_s`); they are not official races (not in fitness.json `races`, the Races pages,
  `calibration_set.py` or the official back-test). [fitness-model.md](fitness-model.md#per-session-scores).
- Add new races here and rebuild `fitness_model.py <person>`; fitness.json `races` then carries the pre-race prediction.

## `session_overrides.json` (hand-edited, read by session_groups.py)
User corrections for sessions the rules get wrong, keyed by activity id:
```json
{"24444367861": {"type": "easy", "fitness": "exclude", "note": "2026-09-10: easy run, then bike / vehicle with the watch on"},
 "21445429328": {"fitness": "bound", "note": "2025-05-15: two steady blocks, not a VO2 session"}}
```
- `type` (optional): replaces the rule-based session type in `groups.json`; the rule's type is kept in
  `override.type_from`.
- `fitness` (optional): `exclude` (no fitness evidence from the session) or `bound` (its hard items become lower
  bounds; races stay races), read by `fitness_model.score_session` from `groups.json` `override.fitness`.
- `note`: why (shown in `override.note`).
Re-run `session_groups.py <person>` and `fitness_model.py <person>` after editing. patrik has 5 (2026-11-04 audit).

## `data/calibration.json` (hand-edited, optional)
`{"weights": {"patrik": 0.5}}`: a manual calibration-set weight per person, overriding the rule of
`calibration_set.py` (weight 1 with ≥ 30 trusted interval sessions and ≥ 2 running races, else 0). Without the file
the rule decides (2026-11-04: adrian, rise 1; patrik 0).

## `profile/hr_settings.json` (scripts/hr_settings.py, version 2)
Per person; read by fitness_model (`hr_of`), session_groups (`hr_norm`, `hr_flagged`) and together (`hr_norm`).
```
{version: 2, source: "auto" | "manual", built,
 max_hr,            person's max HR (one number for every device; %HRR everywhere)
 rest_hr,           median daily resting HR, last 365 days, full-wear days (fitness_model uses its own 30-day
                    rolling median per day and falls back to this)
 max_from: {value, from (device / zones), zones_max (watch HR zones), check (sanity note or null)},
 rest_from: {days, from, to},
 manual: {},        hand-set single values, kept on rebuild and applied last:
                    {max_hr, rest_hr, devices: {<key>: {trust: {interval, steady}, max_hr}}, activities: {<id>: {flags: []}}}
 devices: {"<watch>/<wrist|strap>": {      e.g. "fr935/wrist", "4315/wrist", "fenix3_hr/strap", "3077/wrist"
     product, source (wrist | strap), serials [watch serials], straps [external HR sensors seen, JSON strings],
     from, to (first / last activity), n (runs + ski), n_runs_hr,
     trust: {interval, steady} (0..1, HR-intent variant), trust_from ("interval_track_corr" | "default (...)" |
       "owner <p>'s <key> (borrowed watch, ...)"),
     borrowed_from?: "<person>" (the watch serial is that person's main watch: adrian's 3077 runs of 2021 on patrik's;
       never the by-date device, `device_on`),
     metrics: {interval_track_corr, n_intervals, steady_resid_sd_bpm, n_steady_runs, hr_coverage_median,
               flagged: {cadence_lock, hr_dropout, hr_high_for_pace}},
     peak_hr (confirmed held-12 s peak), peak_top [{hr, date, id}] (5 highest)}},
 activities: {"<id>": {device, flags?: [cadence_lock | hr_dropout | hr_high_for_pace]}}}
```
- Device key: the recording watch's product (file_id; `fr935` = 2691, `fenix3_hr` = 2413, else the product number) +
  the HR source: `strap` when the FIT's device_info lists an external sensor of ANT+ / BLE device type 120
  (heart rate), else `wrist`.
- `"source": "manual"` (or no `source`) freezes the whole file: the builder doesn't overwrite it. A version-1 file
  (only max_hr / rest_hr) still works: max / rest from it, device unknown, trust 0.5 / 0.5 (`TRUST_DEFAULT`).
- How the values are derived: [fitness-model.md](fitness-model.md#heart-rate-settings-per-person-and-device).

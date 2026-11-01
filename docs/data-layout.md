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

Analysis pipeline:
- `session_groups.py <person>`: places, routes, session types, legs and pauses, reps → `groups.json` ([session-groups.md](session-groups.md)).
- `ski_intervals.py <person> <id> ...`: library + CLI: ski interval detector used by session_groups and compare; prints reps and work blocks.
- `geocode_places.py <person> [--dry-run|--repick|--limit N]`: one-off OSM Nominatim reverse geocode of generic start-cluster names → `cache/geocode.json`.
- `similarity.py <person> build|search|nearest|groups|validate`: similarity index → `similarity_index.json` + `tracks.json` ([similarity.md](similarity.md)).
- `fitness_model.py <person> [--eval] [--workers N]`: shape model → `fitness.json` ([fitness-model.md](fitness-model.md)).
- `hr_settings.py <person> [--dry-run]`: max HR (highest real peak in a run) + resting HR → `profile/hr_settings.json`.
- `together.py build|list|show <gid>|validate`: workouts run with others, across all people → `data/together.json` ([together.md](together.md)).
- `compare.py <person> <mode> [ids] [--route|--sig|--place|--last|...]`: compare engine (library + CLI) used by the app ([compare.md](compare.md)).
- `form_delta.py [person] [activity_id] [--series|--together]`: session score vs form going into it (library + CLI; [fitness-model.md](fitness-model.md#form-delta-scriptsform_deltapy)).
- `community.py`: library for the app's Community pages: session metadata, photos, categories, club numbers ([community.md](community.md)).
- `person_settings.py`: library: per-person app settings in `settings.json` (`distance_curve`).
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

## Layout (`data/` is gitignored: personal health data)
```
data/people.json                         optional: {person: {name, initials, colour, colour_dark}} (hand-edited)
data/together.json                       workouts run with others (together.py)
data/_together/                          together.py caches (cache.pkl, sessions.pkl); not a person folder
data/community.json                      Community session metadata (title, category, note, photos, kudos; community.py)
data/_community/photos/<gid>/            Community photos (<id>.jpg + <id>_t.jpg thumbnail)
data/<person>/
  _requests.jsonl                        one line per HTTP request
  activities_index.json                  all activities (list endpoint)
  activities/<YYYY-MM-DD>_<id>_<type>/   list_item.json  activity.json  details.json  [workouts.json]  <id>.fit
  daily/<YYYY-MM-DD>/                    heart_rate.json  sleep.json  hrv.json  spo2.json  food_logs.json  fitness_age.json
  ranges/<endpoint>_<start>_<end>.json   daily_summaries, steps, stress, body_battery, sleep_summaries, weight, ...
  profile/                               user_settings.json  heart_rate_zones.json  hr_settings.json (hr_settings.py or hand-edited)
  candidates/*.txt                       activity id lists (select_activities.py)
  groups.json                            places / routes / session types / legs (session_groups.py)
  similarity_index.json  tracks.json     similarity index + encoded track polylines (similarity.py)
  fitness.json  fitness_eval.json        shape model and its back-tests (fitness_model.py, fitness_eval.py)
  gps_lap_analysis.json  detector_eval.json  route_accuracy.json  calibration.json   distance calibration
  osm_cache/                             Overpass replies (route_accuracy.py)
  settings.json                          optional: app settings, written by the app (person_settings.py)
  places.json                            optional: start-cluster name overrides (session_groups.py)
  regions.json                           optional: extra / replaced named places and regions (similarity.py)
  indoor_bislett.json                    optional: `time_only` overrides for Bislett indoor sessions (session_groups.py)
  report.html                            build_report.py
  screenshots/<YYYY-MM-DD>/              report screenshots for visual checks
  cache/
    session_groups.pkl                   rep detector output per FIT (mtime+size)
    similarity_tracks.pkl                thinned tracks per FIT
    fitness_model.pkl                    per-session fitness features
    compare.pkl                          compare engine analyses / route projections
    form_delta.json                      form-delta references (keyed by fitness.json)
    geocode.json                         reverse-geocoded start-cluster names (geocode_places.py)
    series/<id>.npz  series_manifest.json  tracks.json   app FIT cache (garmin-app-cache, app/server/fitcache.py)
```

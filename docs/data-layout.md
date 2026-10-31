# Scripts and data folder

Credentials: `.env` with `GARMIN_<NAME>_API_KEY` (ADRIAN, PATRIK, BESJAN). Gitignored.
Python via uv (`uv sync`). Don't use the macOS system Python: its LibreSSL can't do the
TLS version Garmin requires.

## Scripts
- `scripts/garmin_client.py`: token handling, request log, 429 backoff, `--min-interval` throttle.
- `scripts/fetch_activities.py <person> --days N [--types running,track_running] [--min-interval S]`, or `--ids 123,456` / `--ids-file f.txt` (looked up in `activities_index.json`); `--parts activity,details,fit` (default; `fit` alone = 1 call per activity, `workouts` opt-in); skips activities that already have a `.fit`
- `scripts/select_activities.py <person>`: offline, writes `data/<person>/candidates/{track,near_track,races,rest,indoor,priority}.txt` id lists for `--ids-file`
- `scripts/fetch_daily.py <person> --days N [--only heart_rate,sleep,...] [--min-interval S]`
- `scripts/fetch_index.py <person>`: full activity list plus daily summaries per year (~19 requests)
- `scripts/run_analysis.py`: per-second analysis of a FIT file: reps found from speed (independent of the lap button), breaks with HR recovery, km splits, Garmin laps matched to reps
- `scripts/calibrate_distance.py <person>`: compares distance sources with Bislet track geometry and certified races, writes `calibration.json`
- `scripts/gps_lap_analysis.py <person>`: GPS-mode manual laps at Bislet vs track geometry (hidden lag, lap booking, lap error), writes `gps_lap_analysis.json`
- `scripts/session_groups.py <person>`: places, routes, session types, legs, writes `groups.json` ([session-groups.md](session-groups.md))
- `scripts/ski_intervals.py <person> <id> ...`: ski interval detector (lap workouts / hard efforts), used by session_groups and compare; the CLI prints reps and work blocks
- `scripts/similarity.py <person> build|search|nearest|groups|validate`: similarity engine on top of `groups.json`, writes `similarity_index.json` + `tracks.json` ([similarity.md](similarity.md))
- `scripts/build_report.py <person>`: writes `data/<person>/report.html`, a self-contained overview (template: `scripts/report_template.html`)

Guides: [garmin-api-guide.md](garmin-api-guide.md), [run-analysis.md](run-analysis.md), [report.md](report.md).

## Layout (`data/` is gitignored, since it's personal health data)
```
data/<person>/
  _requests.jsonl                        one line per HTTP request
  activities/<YYYY-MM-DD>_<id>_<type>/
    list_item.json   activity.json   details.json   workouts.json   <id>.fit
  daily/<YYYY-MM-DD>/
    heart_rate.json  sleep.json  hrv.json  spo2.json  food_logs.json  fitness_age.json
  ranges/<endpoint>_<start>_<end>.json  daily_summaries, steps, stress, body_battery, sleep_summaries, weight, ...
  activities_index.json                  all activities (list endpoint)
  groups.json                            places / routes / session types (session_groups.py)
  similarity_index.json  tracks.json     similarity index + encoded track polylines (similarity.py)
  regions.json  places.json              optional: named places / regions, start-cluster names
  cache/                                 session_groups.pkl, similarity_tracks.pkl
  calibration.json                       distance calibration (calibrate_distance.py)
  gps_lap_analysis.json                  GPS-mode lap analysis (gps_lap_analysis.py)
  candidates/*.txt                       activity id lists (select_activities.py)
  report.html                            overview built by build_report.py
  cache/series/<id>.npz, cache/tracks.json  web app FIT cache (app/server/fitcache.py, docs/app.md)
  screenshots/<YYYY-MM-DD>/              report screenshots for visual checks
  profile/user_settings.json  heart_rate_zones.json
```

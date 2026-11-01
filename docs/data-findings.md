# What data we can get (tested on Adrian's account, Oct 2026)

All findings below come from real calls with `GARMIN_ADRIAN_API_KEY`. Raw responses are in
`data/adrian/` (gitignored). Per-endpoint behaviour: [endpoints-tested.md](endpoints-tested.md); how to fetch:
[garmin-api-guide.md](garmin-api-guide.md).

**Downloaded for Adrian:** the activity list (717 activities, from 2018-12-22), daily summaries from 2018-12-20,
the watch's FIT file for **all 717 activities** (492 runs; non-runs: hiking 64, stop_watch 8, cycling 6,
lap_swimming 5, rowing_v2 3, multi_sport, kayaking_v2, other, open_water_swimming 1 each), and 14 days of intraday
HR, sleep, SpO2 etc. All 8 stop_watch FITs are ~0.5 KB (summary only, no per-second records), and the 2021
open-water swim is 1 KB. **No all-day location data:** the API (OpenAPI spec) has no location or
position-history endpoint; GPS exists only inside activities (FIT, `/details`, start/end lat-lon).

## Summary

| Data | How far back | Resolution |
|---|---|---|
| Activities (list + summary) | All of it: 717 activities, first on 2018-12-22 | 1 record/activity, ~94 fields |
| Activity time series (`/details`) | Back to 2018 | ~every 10 s (≈ 250–275 rows/activity, downsampled) |
| Original FIT file (the watch's own file per activity) | Back to 2018 | **1 s** (current watch); 2–5 s "smart recording" (Forerunner 935, 2018 → 2026-02-17). GPS in ~100 % of records (point every 1 s / ~3 s), HR and cadence in 100 % |
| Daily summaries (steps, calories, HR min/max/rest, stress, body battery, respiration, SpO2, intensity minutes, floors) | Back to at least 2019 | 1 record/day |
| Intraday heart rate | **Only from 2026-06-01** (0 samples on 2026-05-31 and every older date tested) | 1 sample / 120 s |
| Body Battery | Back to 2019 | ~6 points/day (key moments, not continuous) |
| Stress | Back to 2019 | daily durations per stress level |
| Sleep | Whenever the watch was worn at night (e.g. 22/30 nights in Apr 2026, none in Oct 2026) | 1 summary/night, **no stage timeline** |
| SpO2 | Recent days | 24 hourly averages + daily avg/min |
| HRV | None: always HTTP 204 (watch probably doesn't record HRV status, or not worn at night) | - |
| Fitness age, HR zones, profile settings | Current | snapshot |
| Weight | Last entry 2025-11-28 | per weigh-in |
| Food logs, hydration, wellness activity | Endpoint works, no data logged | - |

## Activities

- **List** (`activitylist-service`): 94 fields per activity: distance, duration, moving time,
  avg/max HR, `hrTimeInZone_1..5`, `powerTimeInZone_1..5`, training effect & load, cadence,
  stride, ground contact, vertical oscillation, elevation, fastest 1 km / mile / 5 km splits,
  calories, start/end lat-lon, device, lap count.
- **Activity** (`activity-service/activity/{id}`): `summaryDTO` (~50 fields incl. stamina,
  recovery HR, normalized power, min HR) + `splitSummaries` (e.g. `RWD_RUN`, `RWD_WALK`,
  `INTERVAL_ACTIVE`). The 2018 run had no split summaries.
- **Details** (`/details`): `metricDescriptors` + `activityDetailMetrics` rows, ~10 s apart.
  2026 run: 25 metrics incl. heart rate, speed, grade-adjusted speed, power, cadence, stride,
  GCT, vertical ratio/oscillation, elevation, lat/lon, Body Battery, stamina, performance
  condition. 2018 run: 16 metrics (adds air temperature, no power/stamina). Also has a
  `geoPolylineDTO` (~230 GPS points).
- **Workouts** (`/workouts`): `[]` for normal runs.
- **FIT file** (`download-service/files/activity/{id}`): ZIP with one `<id>_ACTIVITY.fit`.
  Must send `Accept: application/zip, application/x-zip-compressed, application/octet-stream`;
  `Accept: application/octet-stream` alone gives **406**. ~100 KB per 45 min run (current
  watch), ~20–25 KB on the FR935. See [fit-files.md](fit-files.md).

## Daily health

- **Intraday heart rate** (`dailyHeartRate?date=`): `heartRateValues` = `[[epoch_ms, bpm], ...]`
  every 120 s while worn, plus resting/min/max and 7-day avg resting HR. 200–490 samples/day.
  Resting HR is returned for old dates too, but the sample array is empty before 2026-06-01.
  It's unclear whether that's a rolling ~5-month retention window or a fixed cutoff. Re-check
  in a few weeks: if the first day moves forward, it's rolling, and we should sync it daily
  so we don't lose it.
- **Daily summaries** (`usersummary-service/v2/dailysummaries`): the best "one row per day"
  source. Sections: `movement` (steps, floors), `calories`, `heartRate`, `intensityMinutes`,
  `stress` (proportions + durations), `bodyBattery`, `respiration`, `pulseOx`, `hydration`,
  `jetLag`. **Accepts a full year per call** (366 days OK, 3000 days -> 400).
- **Sleep** (`dailySleep?date=`): flat object. Empty nights have ~4 non-null fields. Real
  nights: total/deep/light/REM/awake seconds, start/end timestamps, avg HR, avg/min/max SpO2,
  respiration, avg sleep stress, awake count, `sleepScores` (overall score + per-component
  qualifiers) and `sleepNeed`. No per-minute stage data in this endpoint.
- **SpO2** (`spo2acclimation/{date}`): daily avg/min/latest, `spO2HourlyAverages` (24 rows),
  `monitoringEnvironmentValues` (240 rows: altitude/environment), `spO2SingleValues` was empty.
- **Body Battery**: per day `charged`, `drained`, ~6 `[ts, level]` points, feedback events.
- **Stress / steps** daily stats: one object per day.
- **Fitness age**: current fitness age, achievable age, components (RHR, BMI, vigorous days/min).

## Accuracy of activity distance and timing
See [distance-calibration.md](distance-calibration.md) and [run-analysis.md](run-analysis.md). In short:
Garmin's track-mode lap distances match track geometry (~0.1 %); GPS runs read +0.2..+1.2 % long against
measured courses; the watch's speed field lags ~4 s.

## Timestamps

Mixed formats: epoch ms (HR, body battery, sleep `*Timestamp*`), ISO strings without timezone
(`"2026-10-29T23:00:00.0"`, both `GMT` and `Local` variants), FIT `datetime` in UTC.
Days run local midnight to midnight (Oslo, so CET/CEST).

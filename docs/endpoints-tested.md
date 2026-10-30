# Endpoints tested (live, Adrian's key, 2026-10-30)

Base: `https://personal.connectapi.garmin.com`. Auth: `Authorization: Bearer <token>`
(see `rate-limits-observed.md` for the token flow). Official full list: `api-reference.md`.

Latency: median 150–200 ms per request, the token exchange ~1.3 s, FIT download ~350 ms.

| Endpoint | Params | Status seen | Max range | Notes |
|---|---|---|---|---|
| `POST /personalapitoken-service/personal-api/personal-api-key/token` | header `Authorization: ApiKey <key>` | 200 | - | Body is the raw JWT (text, not JSON). 24 h. Scopes: CONNECT_READ, PERSONAL_API_{FITNESS,NUTRITION,USER,WELLNESS}_READ |
| `GET /activitylist-service/activities/search/activities` | `start`, `limit` (100 works), `startDate`, `endDate`, `activityType` | 200 | paged | newest first |
| `GET /activity-service/activity/{id}` | - | 200 | - | summaryDTO + splitSummaries |
| `GET /activity-service/activity/{id}/details` | - | 200 | - | ~10 s time series. **Not in the official spec** (undocumented, could disappear) |
| `GET /activity-service/activity/{id}/workouts` | - | 200 `[]` | - | |
| `GET /activity-service/activity/{id}/{splits,laps,typedsplits,hrTimeInZones,weather}` | - | **404** | - | not exposed in the Personal API |
| `GET /download-service/files/activity/{id}` | Accept: zip types | 200 / **406** with only `application/octet-stream` | - | ZIP with FIT |
| `GET /wellness-service/wellness/dailyHeartRate` | `date` | 200 | 1 day | samples only from 2026-06-01 |
| `GET /sleep-service/sleep/dailySleep` | `date` | 200 (nulls if no sleep) | 1 day | |
| `GET /sleep-service/sleep/dailySleepSummariesByDate` | `startDate`, `endDate` | 200 / 400 | 32 OK, 60 -> 400 | rows of nulls for nights without data |
| `GET /usersummary-service/v2/dailysummaries` | `startDate`, `endDate` | 200 | **366 OK**, 3000 -> 400 | best daily source |
| `GET /usersummary-service/stats/steps/daily/{s}/{e}` | path | 200 / 400 | 28 OK, 31 -> 400 | |
| `GET /usersummary-service/stats/stress/daily/{s}/{e}` | path | 200 / 400 | 28 OK, 31 -> 400 | |
| `GET /usersummary-service/stats/hydration/daily/{s}/{e}` | path | 200 `[]` | | nothing logged |
| `GET /wellness-service/wellness/bodyBattery/reports/daily` | `startDate`, `endDate` | 200 / 400 | 31 OK, 32 -> 400 | |
| `GET /wellness-service/wellness/daily/spo2acclimation/{date}` | path | 200 | 1 day | |
| `GET /hrv-service/hrv/{date}` and `/hrv-service/hrv/daily/{s}/{e}` | path | **204** always | | no HRV data on this account |
| `GET /weight-service/weight/range/{s}/{e}` | path | 200 | 3000 days OK | |
| `GET /goal-service/goal/user/effective/weightgoal/{s}/{e}` | path | 200 empty | | |
| `GET /wellnessactivity-service/activity/summary/range/{s}/{e}` | path | 200 `[]` | | |
| `GET /nutrition-service/food/logs/{date}` | path | 200 empty | | |
| `GET /fitnessage-service/fitnessage/v2/{date}` | path | 200 | | |
| `GET /biometric-service/heartRateZones/v2` | - | 200 | | |
| `GET /userprofile-service/userprofile/settings` | - | 200 | | |
| `GET /usersummary-service/usersummary/daily` | `calendarDate` | 200 | 1 day | 87 flat fields (in spec, not in Python client) |
| `GET /weight-service/weight/all/{s}/{e}` | path | 400 for 2018..2026 | ? | in spec; range too long or other format, not explored further |

Errors return JSON like
`{"clientMessage": "...", "errorId": "<uuid>", "error": "NotAcceptableException"}` (406), and an
empty body for 400s on range overruns.

## Session 2026-10-30 (evening): watch coverage + search filters (35 requests, ~50 s, no 429)

| Endpoint | Params | Status seen | Notes |
|---|---|---|---|
| `GET .../search/activities` | `limit=200` | 200 | 200 items, 684 KB, 1.9 s. Whole index (717) in 4 calls |
| `GET .../search/activities` | `limit=500`, `limit=1000` | **500** (empty body) | max page is somewhere in 200..499; not narrowed down |
| `GET .../search/activities` | `requiredField=avgPower` (+`activityType=running`) | 200 | 53 runs, 2026-02-24..; = all new-watch runs. `activityType=running` also includes track/indoor |
| `GET .../search/activities` | `minAvgPower=1` | 200 | same 53 |
| `GET .../search/activities` | `sortOrder=asc` | 200 | oldest first (2018-12-22) |
| `GET /weight-service/weight/all/{s}/{e}` | 1 year | 200 | 1 entry; works with short ranges (the 2018..2026 400 was the range) |
| `GET /wellnessactivity-service/activity/summary/range/{s}/{e}` | 30 days | 200 `[]` | still empty |

- **No device endpoint** exists in the spec or api-reference (no devices/gear). List items and
  `activity.json` have no `deviceId`; only `metadataDTO.manufacturer` (`GARMIN`) and
  `metadataDTO.sensors` (`[]`/null). The watch is only visible in the FIT `file_id.garmin_product`.
- Watch switch: FR935 (serial ...7371) up to 2026-02-17, product 4315 (serial ...0964) from 2026-02-24.
  Proxy from the list without downloading: `avgPower`/`avgGroundContactTime` non-null = new watch.
- Per-activity download (4 calls at `--min-interval 1`): **~5 s**; server time ~1 s (details
  0.2–1.2 s, FIT 0.2–0.6 s). `workouts` was `[]` (2 bytes) every time: skipping it saves 25 % of calls.
- Per watch (FIT `record`, 7 FR935 runs 2018–2025 + 20 new-watch runs):

| | FR935 | product 4315 |
|---|---|---|
| Interval | smart recording: mostly 1–3 s gaps, up to ~7 s (≈ 1 record per 2–3 s) | 1 s |
| heart_rate / cadence / distance | 100 % (wrist HR, local sensor type 10) | 100 % |
| position | 86–100 % | 99 % (0 % on a no-GPS run 2026-03-03) |
| power, GCT, VO, step_length | none | yes |
| `details.json` metrics | HR, RunCadence, DoubleCadence, FractionalCadence, Speed, Elevation, VerticalSpeed, Lat/Lon, AirTemperature, PerformanceCondition, sum* (~250 rows) | + Power, AccumulatedPower, StrideLength, VerticalOscillation/Ratio, GroundContactTime, GradeAdjustedSpeed, BodyBattery, Potential/AvailableStamina; no AirTemperature |

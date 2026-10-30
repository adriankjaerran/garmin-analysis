# Garmin Connect Personal API — endpoint reference

Compiled 2026-10-30 from public sources only (no authenticated calls made).

**Sources**
- **[S] Swagger**: the official OpenAPI 3.0.1 spec ("Garmin Connect Personal API", version 1.0.0) shown at
  <https://developer.garmin.com/gc-developer-program/personal/swagger-docs/>. Garmin doesn't publish it as a
  separate file. It is inlined in the page's JS bundle
  (`/component---src-pages-gc-developer-program-personal-swagger-docs-js-715115a6210d45afc282.js`, webpack module `wOaG`).
  It was extracted unchanged to [`openapi/garmin-personal-api-openapi.json`](openapi/garmin-personal-api-openapi.json)
  (26 paths, 109 schemas).
- **[P] Python client**: <https://github.com/garmin/connect-personal-api-python> v1.0.0 (`src/garmin_personal_api/client.py`, README).
  It wraps 23 of the 26 Swagger paths and calls nothing outside the spec.

Every endpoint in the client is also in the Swagger spec. Three Swagger endpoints have no client wrapper. They are marked **[S only]**.

## General

| Item | Value |
|---|---|
| Base URL | `https://personal.connectapi.garmin.com` (spec also lists `https://personal.connectapi.garmin.cn` for China) [S] |
| Auth | `Authorization: Bearer <access token>` on every data call [P]. The spec defines **no** `securitySchemes` and no `security` blocks |
| OAuth scopes | **Not documented.** The spec has no scopes. The FAQ says only "Garmin Connect APIs use OAuth2 access tokens" |
| Methods | All data endpoints are `GET` (read-only). The Swagger UI is rendered with `supportedSubmitMethods: []` (no "Try it out") |
| Date format | `YYYY-MM-DD` (`format: date`). Ranges are inclusive. Dates use the user's local calendar where stated |
| Accept | `application/json` (client default). Use `application/zip, application/x-zip-compressed, application/octet-stream` for downloads [P] |
| Empty data | HRV endpoints document `204 No data`. The client returns `None` for an empty body [P] |
| Common errors | Where documented: 400 invalid/missing parameter, 401 auth missing or invalid, 403 not authorized, 404 not found (activities), 500 server error. **429 is not documented on any endpoint** |
| Client-side validation | None, except that the download activity ID must be a positive integer. "Validation of dates, ranges, and other arguments is left to the API" [P README] |

### Token endpoint

`POST /personalapitoken-service/personal-api/personal-api-key/token`
- Header `Authorization: ApiKey <personal-api-key>`, `Accept: */*`.
- The response body is a raw JWT in plain text. Read the expiry from the JWT `exp` claim [P `auth.py`].
- The endpoint is documented on the [Personal API page](https://developer.garmin.com/gc-developer-program/personal/personal-api) and the [FAQ](https://developer.garmin.com/gc-developer-program/personal/personal-faq/). It is **not** in the Swagger spec.
- For lifetime and caching, see [rate-limits-and-terms.md](rate-limits-and-terms.md).

---

## Activity (Swagger tag `Activity`)

### `GET /activitylist-service/activities/search/activities` [S][P]
Searches activities and returns paginated activity summaries.
Returns `SummarizedActivityDTO[]` (230 fields: distance, duration, HR, cadence, power, training effect, start lat/lon, etc.). Returns an empty array when nothing matches.

| Param (query) | Type | Default | Notes |
|---|---|---|---|
| `start` | int32 | 0 | Zero-based offset |
| `limit` | int32 | **99** (spec). The client defaults to 20 | Max page size **not documented** |
| `startDate`, `endDate` | date | – | Inclusive. Based on the activity's local start time. No max range documented |
| `activityType` | string | – | Parent type key only (`running`, `cycling`, …) |
| `activitySubType` | string | – | Child type key (`trail_running`, `road_cycling`). Must belong to `activityType` if both are given |
| `excludedActivitySubType` | string[] | – | Repeat the parameter to exclude several subtypes |
| `eventType` | string | – | `race`, `training`, `recreation`, `fitness`, `specialEvent`, `uncategorized` |
| `search` | string | – | Text in name/description |
| `favorite` | int32 | -1 | 1 = favorites, 0 = non-favorites, -1 = no filter |
| `sortBy` | string | `startLocal` | e.g. `name`, `distance`, `duration` |
| `sortOrder` | string | `desc` | `asc` / `desc` |
| `excludeChildren` | boolean | true | Excludes child activities such as multisport legs |
| `minDistance` / `maxDistance` | int64 | – | Meters |
| `minDuration` / `maxDuration` | int64 | – | Timer duration in seconds |
| `minElapsedDuration` / `maxElapsedDuration` | int64 | – | Seconds, including paused time |
| `minElevation` / `maxElevation` | int64 | – | Total elevation gain in meters |
| `minAvgPower` | int64 | – | Watts |
| `upperLatitude`, `lowerLatitude`, `leftLongitude`, `rightLongitude` | double | – | Bounding box for the start location. All four are required |
| `lowerMaxDepth` / `upperMaxDepth` | int64 | – | Dive max depth in cm |
| `minBottomTime` / `maxBottomTime` | int64 | – | Dive bottom time in seconds |
| `decoDive` | boolean | – | Decompression-dive filter |
| `requiredField` | string[] | – | Fields that must be non-null. Repeatable |

Errors: 400 (bad type/subtype), 401, 403, 500.

### `GET /activity-service/activity/{activityId}` [S][P]
`activityId` is an int64 path parameter. Returns `ActivitySummaryDTO`: activity metadata, `summaryDTO` measurements, `splitSummaries`, `activityTypeDTO`, `eventTypeDTO`, `timeZoneUnitDTO`, `metadataDTO`, `diveInfo`, `locationName`, etc. Errors: 400, 401, 403, 404, 500.

### `GET /activity-service/activity/{activityId}/workouts` [S][P]
Returns the workouts performed during the activity as `ActivityWorkoutDTO[]` (`workoutName`, `sport`, `steps`, `multisportSet`, …). Returns an empty array if there are none. Errors: 400, 401, 403, 404, 500.

### `GET /download-service/files/activity/{activityId}` [S][P]
Downloads the activity file in its original format (typically FIT), wrapped in a ZIP. The response type is `application/x-zip-compressed`. The client buffers the whole ZIP in memory.

> **Note:** The spec has no activity time-series endpoint, such as the internal Connect `/activity-service/activity/{id}/details`. For per-second samples, parse the downloaded FIT file.

### `GET /wellnessactivity-service/activity/summary/range/{fromDate}/{untilDate}` [S][P]
Returns wellness activity summaries for an inclusive date range as `WellnessActivitySummaryDTO[]` (`activityName`, `wellnessActivityType`, start/end timestamps, `summaryTypeDataList`, `timeInZoneList`, `deviceMetaData`). Max range is not documented. Errors: 400, 401, 403, 500.

---

## Daily summaries (tag `Health`)

### `GET /usersummary-service/v2/dailysummaries` [S][P]
Query parameters: `startDate`, `endDate` (date). **Max range 1 year.** Returns `ApiDocumentListUserDailySummaryV2DTO` (`{ "data": [...] }`, even for one day).

### `GET /usersummary-service/usersummary/daily` [S only]
Query parameter: `calendarDate` (date). Returns `UserDailySummaryDto` for a single day (95 fields: steps, calories, distance, intensity minutes, floors, min/max/resting HR, stress, Body Battery, etc.).

### `GET /usersummary-service/stats/steps/daily/{startDate}/{endDate}` [S][P]
**Max 28 days.** Returns `StepsDailyStatDTO[]` (`calendarDate`, `totalSteps`, `totalDistance`, `stepGoal`).

### `GET /usersummary-service/stats/stress/daily/{startDate}/{endDate}` [S][P]
**Max 28 days.** Returns `MultiValueWellnessChartItemDTO[]` (`calendarDate`, `values`).

### `GET /usersummary-service/stats/hydration/daily/{startDate}/{endDate}` [S][P]
**Max 31 days.** Returns `UserDailyHydrationDTO[]` (`valueInML`, `goalInML`, `sweatLossInML`, `activityIntakeInML`, …).

## Heart rate

### `GET /wellness-service/wellness/dailyHeartRate` [S][P]
Query parameter: `date` (date). Returns `DailyHeartRateDTO` (min/max/resting HR, 7-day average resting HR, `heartRateValues` time series with descriptors, abnormal-HR values). Errors: 400, 401, 403, 500.

### `GET /biometric-service/heartRateZones/v2` [S][P]
Query parameter: `generateDefaults` (boolean, default `true`). When true, default zones are generated if the user has none configured. Returns `HeartRateZonesDTO[]`, one per sport (`trainingMethod`, zone floors, max/resting/LTHR used).

## HRV

### `GET /hrv-service/hrv/{calendarDate}` [S][P]
Returns `HrvChartDTO` (`hrvSummary`, `hrvReadings`, sleep window timestamps). Returns 204 if there is no data.

### `GET /hrv-service/hrv/daily/{fromDate}/{toDate}` [S][P]
Returns `HrvDailySummaryDTO` (`hrvSummaries[]`). Returns 204 if there is no data. Max range is not documented.

## Sleep

### `GET /sleep-service/sleep/dailySleep` [S][P]
Query parameter: `date` (date). Returns `DailySleepDTO` (47 fields: stage seconds, SpO2, respiration, sleep stress, avg HR, `sleepScores`, feedback/insight, nap time, …).

### `GET /sleep-service/sleep/dailySleepSummariesByDate` [S][P]
Query parameters: `startDate`, `endDate`. **Max 31 days.** Returns `DailySleepDTO[]`.

## SpO2

### `GET /wellness-service/wellness/daily/spo2acclimation/{calendarDate}` [S][P]
Returns `DailySpO2AndAcclimationDTO` (average/lowest/latest SpO2, 7-day average, sleep SpO2, single values, hourly averages, monitoring-environment/altitude acclimation values). Errors: 400, 401, 403, 500.

## Body Battery

### `GET /wellness-service/wellness/bodyBattery/reports/daily` [S][P]
Query parameters: `startDate`, `endDate` (date), and optionally `chunks` (int32). `chunks` is the number of intervals per day; the response includes the min and max reading for each interval. **Max 31 days.** Returns `BodyBatteryDailyReportDTO[]` (`charged`, `drained`, `bodyBatteryValuesArray`, activity events, dynamic feedback). Errors: 400, 401, 403, 500.

## Fitness age

### `GET /fitnessage-service/fitnessage/v2/{calendarDate}` [S][P]
Returns the latest Fitness Age as of the given date.
Query parameter: `fullHistoryScan` (boolean, default `true`). If false, only the 28 days before the date are considered. The client doesn't expose this parameter.
Returns `FitnessAgeDTO[]` (`chronologicalAge`, `fitnessAge`, `achievableFitnessAge`, `components`, …).

## Weight / body composition

### `GET /weight-service/weight/range/{startDate}/{endDate}` [S][P]
Query parameter: `includeAll` (boolean, spec default `false`). The client always sends `true`, which returns all entries per day instead of only the latest. Returns `WeightRangeDTO` (`dailyWeightSummaries`, `totalAverage`, `previousDateWeight`, `nextDateWeight`). Dates are on the user's local calendar. Max range is not documented.

### `GET /weight-service/weight/all/{startDate}/{endDate}` [S only]
Returns every weight entry in the range as a flat list of `DateWeightDTO[]` (`weight`, `bmi`, `bodyFat`, `bodyWater`, `boneMass`, `muscleMass`, `physiqueRating`, `visceralFat`, `metabolicAge`, `timestampGMT`, …).

### `GET /goal-service/goal/user/effective/weightgoal/{startDate}/{endDate}` [S][P]
Returns the effective weight goals and goal ranges for the interval as `UserGoalsRangeDTO` (`userGoals`, `userGoalRanges`). Errors: 400, 401, 403, 500.

## Nutrition

### `GET /nutrition-service/food/logs/{date}` [S][P]
Returns the daily nutrition summary (goals, nutrients consumed, logged foods, meal details) as `DailyNutritionSummaryDTO`. The date is interpreted in the user's local timezone.
Query parameters:
- `includeGoals` (boolean, default true)
- `includeNutrition` (boolean, default true)
- `includeMedia` (boolean, default true)
- `mealId` (int64): breakfast, lunch, snacks, or dinner

The client sends none of these parameters.

## Women's health (tag `Women's Health`)

### `GET /periodichealth-service/menstrualcycle/summary/{calendarDate}` [S][P]
Returns `MenstrualCycleSummaryDTO` (phase, day in cycle, predictions, fertile window, pregnancy info). Errors: 400, 401, 403, 500.

### `GET /periodichealth-service/menstrualcycle/dayview/{calendarDate}` [S][P]
Query parameters:
- `todayDate` (date): reference date for predictions
- `reducedResponse` (boolean, default false)
- `skipbm` (boolean, default false): excludes baby-movement data
- `includeAnalysis` (boolean, default false)

Returns `DetailedMenstrualCycleDayDTO`. Errors: 400, 401, 403, 500.

## User profile (tag `User Profile`)

### `GET /userprofile-service/userprofile/settings` [S][P]
No parameters. Returns `UserSettingsDTO` (`displayName`, locale, `measurementSystem`, `timeZone`, formats, hydration units, training days, …).

---

## Max date ranges (summary)

| Endpoint | Max range (per Swagger) |
|---|---|
| `usersummary-service/v2/dailysummaries` | 1 year |
| `sleep/dailySleepSummariesByDate` | 31 days |
| `stats/hydration/daily` | 31 days |
| `bodyBattery/reports/daily` | 31 days |
| `stats/steps/daily` | 28 days |
| `stats/stress/daily` | 28 days |
| `hrv/daily`, `weight/range`, `weight/all`, `weightgoal`, `wellnessactivity …/range`, activity search `startDate`/`endDate` | not documented |

## Not in the Personal API (as of spec v1.0.0)

The spec has none of these: activity time-series (`/details`), splits/laps endpoints (only `splitSummaries` inside the activity summary), GPS polylines, training status/readiness/load, VO2 max history, race predictions, respiration or intensity-minute time-series endpoints, devices, gear, badges, personal records, workouts/courses/calendar, or any write endpoints.

# How to work with the Garmin Personal API (practical guide)

What works, learned from ~280 logged requests on Adrian's account (Oct 2026). Details:
[endpoints-tested.md](endpoints-tested.md), [api-reference.md](api-reference.md) (official spec),
[rate-limits-observed.md](rate-limits-observed.md), [rate-limits-and-terms.md](rate-limits-and-terms.md).

## 1. Keys and tokens
- Each person creates a key at <https://connect.garmin.com/app/settings/personalApiToken>.
  Keys expire after 30/90/180/365 days (chosen at creation), so ask for **1 year** and note the date.
- Store keys as `GARMIN_<NAME>_API_KEY` in `.env` (gitignored). Never print them.
- Swap the key for a token:
  `POST https://personal.connectapi.garmin.com/personalapitoken-service/personal-api/personal-api-key/token`
  with `Authorization: ApiKey <key>`. The body is the **raw JWT as plain text**. It's valid 24 h,
  with no refresh token: just ask again. `scripts/garmin_client.py` does this and refreshes after 23 h.
- Data calls: `Authorization: Bearer <jwt>`, `Accept: application/json`.

## 2. Environment
- Use uv with Python ≥ 3.12 (`uv sync`; run with `uv run ...`). **The macOS system Python 3.9
  fails** with `TLSV1_ALERT_PROTOCOL_VERSION` (LibreSSL 2.8.3).
- Use `scripts/garmin_client.py` rather than raw requests: token handling, backoff,
  throttling, and a log line per request in `data/<person>/_requests.jsonl`.

## 3. Which endpoint for what
| Need | Best endpoint | Calls |
|---|---|---|
| All activities | `activitylist-service/activities/search/activities?start=&limit=100` | 1 per 100 activities |
| Per-second run data | `download-service/files/activity/{id}` (ZIP with FIT) | 1 per activity |
| Run summary + Garmin's interval split totals | `activity-service/activity/{id}` | 1 per activity |
| ~10 s time series (undocumented, might disappear) | `activity-service/activity/{id}/details` | 1 per activity |
| One row per day (steps, HR, stress, Body Battery, SpO2, respiration...) | `usersummary-service/v2/dailysummaries` | **1 per year** |
| All-day heart rate (2 min) | `wellness-service/wellness/dailyHeartRate?date=` | 1 per day, only from 2026-06-01 |
| Sleep | `sleep-service/sleep/dailySleepSummariesByDate` (≤ 32 days) | 1 per month |
| Body Battery curve points | `wellness-service/wellness/bodyBattery/reports/daily` (≤ 31 days) | 1 per month |
| Steps / stress stats | `usersummary-service/stats/{steps,stress}/daily/{s}/{e}` (≤ 28 days) | 1 per 4 weeks |

Gotchas:
- **FIT download needs** `Accept: application/zip, application/x-zip-compressed, application/octet-stream`.
  `application/octet-stream` alone gives 406.
- Over-long ranges return **400 with an empty body**; stay within the limits above.
- HRV endpoints return **204** when there's no data (Adrian's account: always).
- Days without data come back as objects full of `null`, not as missing rows.
- `activity/{id}/{splits,laps,typedsplits,hrTimeInZones,weather}` are **404** in the Personal API.
  Laps come from the FIT file; interval totals from `splitSummaries` in `activity/{id}`.
- Timestamps mix epoch ms, ISO strings without a timezone (`...GMT` / `...Local` pairs) and FIT UTC.

## 4. Rate limits and pacing
- Not documented, and no rate-limit headers. We hit 429 after ~150–200 back-to-back requests
  in ~15 min; it cleared within ~30–40 min.
- Since then, 282 logged requests at ≤ 1 req/s gave **no 429s** (200 ×260, 204 ×15, 400 ×7 from
  deliberate range tests).
- Do this:
  - interactive work and small batches: `--min-interval 1`;
  - big backfills: `--min-interval 3–5`, in chunks of a few hundred; it resumes (activities
    that already have a `.fit` are skipped);
  - prefer range endpoints (a year of daily summaries = 1 call);
  - on 429 the client waits 15 s, doubling to 5 min, and gives up after 30 min.

## 5. Recommended sync strategy
1. **First time per person:** `fetch_index.py` (activity list + yearly daily summaries, ~20 calls).
2. **Backfill watch files**: `select_activities.py` (offline id lists, calibration-relevant first),
   then `fetch_activities.py --ids-file ... --parts fit` (1 call per activity; add `activity` for
   report splits; `details` duplicates the FIT at 10 s, `workouts` is always `[]`).
   See [onboarding-and-calibration.md](onboarding-and-calibration.md).
3. **Daily job:** new token, then the last 2–3 days of activities (summary + FIT), daily
   summaries for the last week, intraday HR and sleep for the last 3 days. That's about
   10–20 calls per person.
4. Intraday HR only exists from 2026-06-01 and may be a rolling window: sync it daily so it
   isn't lost.

## 6. Scripts
See [data-layout.md](data-layout.md) for the commands and the `data/` layout,
[run-analysis.md](run-analysis.md) for how runs are analysed, and [report.md](report.md) for the HTML report.

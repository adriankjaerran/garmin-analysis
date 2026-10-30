# Auth & rate limits: what we observed

Official documentation notes are in `rate-limits-and-terms.md`; this file is only what we saw.

## Token flow
1. `POST /personalapitoken-service/personal-api/personal-api-key/token` with
   `Authorization: ApiKey <key>`, which returns a JWT as **plain text**.
2. Use it as `Authorization: Bearer <jwt>`. `exp` is 24 h after `iat`; there's no refresh
   token, so just request a new one. `scripts/garmin_client.py` refreshes after 23 h.

**API keys expire** (30/90/180/365 days, chosen when created; see `rate-limits-and-terms.md`). Ask friends for long-lived keys and track expiry dates.

## Rate limiting
- No rate-limit headers on any response (no `X-RateLimit-*`, no `Retry-After`, even on 429).
  The API is behind Cloudflare.
- First 429 came after roughly **150–200 requests in ~15 minutes**, sent back-to-back with no
  delay (~0.2 s each). After that, *all* endpoints returned 429 (token endpoint not tested).
- It had cleared by the next session, at most ~30–40 minutes later. We didn't measure the
  exact window.
- Clean runs since then: 14 requests (2 weeks of runs), 97 (2 weeks of daily data),
  ~16 (sleep probe), 37 (range-limit probe), 19 (activity index + yearly daily summaries),
  97 (24 calibration activities at 1 req/s), all without a 429. The request log holds 282
  requests: 200 ×260, 204 ×15 (HRV), 400 ×7 (deliberate range tests).

## Practical guidance
- Back off on 429. The client sleeps 15 s, doubling up to 5 min, and gives up after 30 min.
- 1 req/s (`--min-interval 1`) has been safe for batches of ~100. For backfills, use `--min-interval 3–5` (≤ ~720 requests/hour) and resume across runs
  (the activity downloader skips folders that already have a `.fit`).
- Use range endpoints to cut down calls: `dailysummaries` covers a year in **1** request,
  versus 365 for per-day calls.
- A full backfill for one person is ~717 activities × 4 calls ≈ 2,900 requests, plus intraday
  HR per day since 2026-06-01 (~150), plus sleep in 31-day chunks.
- Every request is logged to `data/<person>/_requests.jsonl` (timestamp, path, status, bytes,
  ms) so we can work out the real window later.

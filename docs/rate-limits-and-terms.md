# Garmin Personal API: rate limits, tokens, and terms

Compiled 2026-10-30 from public sources only. Sources reviewed:

- [PA] Personal API page: <https://developer.garmin.com/gc-developer-program/personal/personal-api>
- [FAQ] Personal API FAQ: <https://developer.garmin.com/gc-developer-program/personal/personal-faq/>
- [SW] Swagger docs and OpenAPI spec: <https://developer.garmin.com/gc-developer-program/personal/swagger-docs/>. A local copy is in [`openapi/garmin-personal-api-openapi.json`](openapi/garmin-personal-api-openapi.json)
- [GH] Official Python client v1.0.0: <https://github.com/garmin/connect-personal-api-python> (README, `auth.py`, `client.py`). At the time of review the repo had 0 issues and was created 2026-10-20

These pages contain everything Garmin publishes about the Personal API. The `/gc-developer-program/personal/` index page only redirects to [PA].

## Summary

| Topic | Status |
|---|---|
| Rate limits / quotas | **Not documented** |
| 429 response / `Retry-After` | **Not documented** |
| Retry / backoff guidance | **Not documented.** The official client never retries |
| Access token lifetime | 24 h or until the key expires, whichever comes first (documented) |
| Refresh tokens | Not supported (documented) |
| API key lifetime | 30, 90, or 180 days, or 1 year, chosen at creation (documented) |
| Revocation | Deleting the key invalidates the key and all its tokens (documented) |
| Data history limits | **Not documented.** Only per-request date-range caps exist |
| OAuth scopes | **Not documented** |
| Allowed use | Personal use of **your own** data, not commercial multi-user apps (documented, FAQ) |
| Sharing other people's data | **Not documented explicitly.** The FAQ limits the API to a user's own data |

## Rate limits and quotas

**Not documented.** The FAQ, the Personal API page, the OpenAPI spec, and the Python client say nothing about rate limits, quotas, request budgets, or throttling windows. The spec documents no `429` response on any endpoint. Its error codes are only 400, 401, 403, 404, 500, and 204 "No data" on the HRV endpoints.

Our own observation (not official): we got HTTP 429 after about 190 rapid sequential requests. We don't know the window, the reset time, or whether the limit is per key, per token, or per user.

### Retry-After and backoff
**Not documented.** The official client does no retries and no backoff. Any non-2xx response raises `HttpError(status_code, method, url, response_text)` [GH `client.py`]. The README doesn't mention 429. We have no official information on whether 429 responses include `Retry-After`. Inspect the headers of a real 429 to find out.

Indirect guidance that reduces call volume:
- Use the range endpoints instead of per-day calls. Max ranges per [SW] are: `v2/dailysummaries` 1 year; sleep summaries, hydration, and Body Battery 31 days; steps and stress 28 days. See [api-reference.md](api-reference.md).
- Cache the bearer token. The official client reuses a cached token until less than 1 hour of validity remains, then requests a new one [GH README, `auth.py`: `_BEARER_TOKEN_REFRESH_WINDOW_SECONDS = 60 * 60`].

## Tokens and keys

**API key creation** [PA, FAQ]: create the key at <https://connect.garmin.com/app/settings/personalApiToken>. "Read and Accept the Terms and Conditions", choose an expiration period, then create. "The key will only be displayed once so copy, save, and protect the key like a password."

**API key expiry** [FAQ]:
> "When creating a personal API key, you can configure the key to expire after 30 days, 90 days, 180 days, or one year. We'll send you an email notification about a week before your key expires to remind you to generate a new key so your application maintains access to your data."

**Lost key** [FAQ]: "If you lose the key, you must delete it and generate a new one."

**Access token** [FAQ]:
> "Garmin Connect APIs use OAuth2 access tokens for authentication so your Personal API key cannot be used directly. You first need to obtain a token generated from your key."
>
> "Access tokens are valid for 24 hours or until the Personal API key expires, whichever occurs first."
>
> "No, refresh tokens are not supported. You can simply obtain a new access token using the endpoint described above."

Token endpoint [PA, FAQ]:
```
curl -X POST 'https://personal.connectapi.garmin.com/personalapitoken-service/personal-api/personal-api-key/token' -H 'Authorization: ApiKey <your-api-key>'
```
The response body is the raw JWT. Read the expiry from its `exp` claim [GH `auth.py`]. The following are **not documented**:
- any limit on how often new tokens can be requested
- whether issuing a new token invalidates earlier ones

**Revocation** [FAQ]:
> "You may delete your Personal API key at any time on Garmin Connect Web. Once deleted, the key and any access tokens associated with that key, will no longer be usable."

[GH README]: "Rarely, a token stops working before it expires, for example because it was revoked. Requests then fail with `401`, and the client doesn't retry them." The fix is to request a new token.

**Multiple keys per account:** not documented.

## Data history limits

**Not documented.** Nothing says how far back data is available. The only related limits are:
- the per-request max date ranges listed above [SW]
- a Fitness Age option: with `fullHistoryScan=false`, "only the last 28 days relative to the target calendar date will be considered". The default `true` scans the full history [SW].

## Terms of use / allowed usage

[FAQ]:
> "The Garmin Connect Personal API allows Garmin customers to securely access their own Garmin Connect data using an API. It is intended for developers, hobbyists, data enthusiasts, and individuals who want to build personal applications using their own Garmin data."
>
> "The Personal API is intended for individual Garmin users who want programmatic access to their own data. It is not intended for commercial applications serving multiple end users."
>
> "The Personal API provides access to an individual user's own data using a self-generated API key. The Garmin Connect Developer Program is designed for organizations building integrations for multiple users and includes commercial business APIs and enterprise options."

- **Sharing or using friends' or other people's data:** not explicitly addressed. Every statement above limits the API to a user's **own** data. For other users, the FAQ points to the (business) Garmin Connect Developer Program. Collecting other people's keys or data falls outside the stated intent. That is our reading, not a quoted rule.
- **Terms and Conditions text:** you must accept it when creating a key, but it is shown only inside Garmin Connect Web after login. It is **not publicly available**, so it was not reviewed here. Check it at <https://connect.garmin.com/app/settings/personalApiToken> for binding rules on rate limits, redistribution, and retention.
- Garmin's general site Terms of Use are at <https://www.garmin.com/en-US/legal/terms-of-use/> and are linked from the developer site footer. They were not reviewed for API-specific clauses because the page body is rendered client-side.
- **Client code license:** Apache 2.0 [GH `LICENSE`]. This covers the client code only, not the data or API usage.

## Support channels
- Developer forum: <https://forums.garmin.com/developer/>
- Contact form: <https://www.garmin.com/en-US/forms/developercontactus/>

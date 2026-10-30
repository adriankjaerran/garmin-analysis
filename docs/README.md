# Docs: Garmin data analysis

**Start here:** [garmin-api-guide.md](garmin-api-guide.md), how to work with the Garmin Personal API.

## Using the API
| File | What |
|---|---|
| [garmin-api-guide.md](garmin-api-guide.md) | Keys, tokens, which endpoint for what, pacing, sync strategy |
| [endpoints-tested.md](endpoints-tested.md) | Every endpoint we called: params, status codes, max date ranges |
| [rate-limits-observed.md](rate-limits-observed.md) | Token flow and the rate limiting we actually hit |
| [rate-limits-and-terms.md](rate-limits-and-terms.md) | What Garmin officially documents (limits, key expiry, terms) |
| [api-reference.md](api-reference.md) | All endpoints from the official OpenAPI spec |
| [openapi/](openapi/) | Official spec, extracted from Garmin's Swagger page |

## The data
| File | What |
|---|---|
| [data-findings.md](data-findings.md) | What data exists, how far back, at what resolution |
| [fit-files.md](fit-files.md) | Original watch files: per-second records, fields, signal quirks |
| [data-layout.md](data-layout.md) | Scripts and the `data/` folder layout |

## Analysis and report
| File | What |
|---|---|
| [run-analysis.md](run-analysis.md) | How reps, breaks and splits are found today, and how accurate they are |
| [session-groups.md](session-groups.md) | Places, routes, session types, legs and pauses, route comparison |
| [similarity.md](similarity.md) | Similarity engine: named places / regions (Marka), workout hierarchy (6×1000 · rest), search, top-k nearest, map polylines |
| [distance-calibration.md](distance-calibration.md) | Which distance to trust: Bislet track geometry and certified races |
| [compare.md](compare.md) | Workout comparison: elapsed / moving / route-position ghost race, rep- and break-aligned intervals |
| [report.md](report.md) | The HTML report: pages, data sources, how to rebuild |
| [app.md](app.md) | The web app (FastAPI + Vite): maps for every activity and day, how to run, endpoints, extension points |
| [onboarding-and-calibration.md](onboarding-and-calibration.md) | Best way to get data, onboarding a new runner, calibrating a new watch |

## Plans
| File | What |
|---|---|
| [plans/timing-and-distance-accuracy.md](plans/timing-and-distance-accuracy.md) | Lags at rep start/end, pace dependence, carrying Bislet calibration to other runs |

# Docs

**Start here:** [CONCEPTUAL_MODEL.md](CONCEPTUAL_MODEL.md) explains the concepts and how they fit together.

## Garmin API
| File | What |
|---|---|
| [garmin-api-guide.md](garmin-api-guide.md) | Keys, tokens, which endpoint for what, pacing, sync strategy |
| [endpoints-tested.md](endpoints-tested.md) | Every endpoint we called: params, status codes, max date ranges |
| [rate-limits-and-terms.md](rate-limits-and-terms.md) | Rate limits, tokens and keys, data history limits, terms of use |
| [api-reference.md](api-reference.md) | All endpoints from the official OpenAPI spec |
| [openapi/](openapi/) | Official spec, extracted from Garmin's Swagger page |

## Data
| File | What |
|---|---|
| [data-findings.md](data-findings.md) | What data exists, how far back, at what resolution |
| [fit-files.md](fit-files.md) | Original watch files: per-second records, fields per watch, signal quirks |
| [data-layout.md](data-layout.md) | Scripts and the `data/` folder layout |
| [onboarding-and-calibration.md](onboarding-and-calibration.md) | Getting data, onboarding a new runner, calibrating a new watch |

## Analysis
| File | What |
|---|---|
| [run-analysis.md](run-analysis.md) | How reps, breaks and splits are found, and how accurate they are |
| [distance-calibration.md](distance-calibration.md) | Which distance to trust: Bislett track geometry and certified races |
| [session-groups.md](session-groups.md) | Places, routes, session types, ski intervals, legs and pauses (`groups.json`) |
| [similarity.md](similarity.md) | Named places and regions, workout hierarchy, search, top-k nearest |
| [compare.md](compare.md) | Comparing sessions: ghost race, common start line, rep- and break-aligned intervals |
| [together.md](together.md) | Workouts run with others: detection, labels, groups, the session engine and API |
| [community.md](community.md) | The club layer: categories, photos, kudos, club and member stats, goals |
| [fitness-model.md](fitness-model.md) | Shape over time (VDOT scale), race predictor, back-tests, why HR isn't used |

## Front ends
| File | What |
|---|---|
| [app.md](app.md) | The web app: pages and URLs, endpoints, architecture, tests, visual QA |
| [report.md](report.md) | The HTML report: pages, data sources, how to rebuild |

## Plans
| File | What |
|---|---|
| [plans/open-items.md](plans/open-items.md) | What isn't built yet, data to collect, open decisions |

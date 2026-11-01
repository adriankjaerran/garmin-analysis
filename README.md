# garmin-analysis

Personal Garmin data, downloaded with the Garmin Personal API and analysed locally: every run, ski and hike with its
track, interval reps and breaks, places and repeated routes, similar sessions, side-by-side comparisons and a fitness
("shape") model on the Daniels VDOT scale. Two front ends read the same data:

- **Web app** (`app/`): FastAPI backend + Vite / TypeScript / Preact frontend (MapLibre maps, uPlot charts).
  Pages: Home (latest day, 5-week training log, volume vs typical, recent workouts, shape), Activities (one list with
  simple + advanced filters, map, preview and groups; activity page with Closest activities and Shape), Calendar & day,
  Map, Places & routes, Compare (route ghost race, moving / elapsed time, rep- and break-aligned intervals), Fitness.
- **HTML report** (`scripts/build_report.py` → `data/<person>/report.html`): one self-contained file.

Everything runs on 127.0.0.1; health data stays in `data/` (gitignored).

## Quick start
```sh
uv sync                                         # Python deps (+ dev: pytest, httpx)
(cd app/web && npm install && npm run build)    # frontend -> app/web/dist (again after frontend changes)
uv run garmin-app-cache adrian                  # pre-parse FIT files into data/adrian/cache (incremental)
uv run garmin-app                               # http://127.0.0.1:8765   (--person, --port, --reload)
uv run pytest -q                                # API smoke tests (skipped without data/adrian)
```
Frontend development with hot reload: `uv run garmin-app` + `cd app/web && npm run dev` (http://localhost:5173).

## Refreshing the data
Run in this order (each step reads the previous step's output):

```sh
uv run scripts/fetch_index.py adrian                              # 1. activity list + daily summaries
uv run scripts/fetch_activities.py adrian --days 14 --min-interval 1   #    FIT files / activity details
uv run scripts/fetch_daily.py adrian --days 14 --min-interval 1   #    sleep, intraday HR (optional)
uv run scripts/session_groups.py adrian                           # 2. places, routes, legs, reps -> groups.json
uv run scripts/similarity.py adrian build                         # 3. similarity index (names, search, nearest)
uv run scripts/fitness_model.py adrian                            # 4. fitness.json (shape per discipline)
uv run scripts/together.py build                                 #    together.json: workouts run with others (all people)
uv run garmin-app-cache adrian && uv run garmin-app               # 5. app cache (series, tracks, rep times) + app
```
Steps 1–4 run per person; `together.py build` runs once after them (it reads every person's data; incremental).
The app also refreshes its FIT cache in the background at startup, reloads `groups.json`, the similarity index,
`fitness.json` and `together.json` when they change, and the Fitness page has a **Rebuild** button (re-runs step 4).
API keys, tokens and pacing: [docs/garmin-api-guide.md](docs/garmin-api-guide.md).

## Docs
| | |
|---|---|
| [docs/app.md](docs/app.md) | The web app: pages and URLs, endpoints, architecture, place model, tests, known gaps |
| [docs/compare.md](docs/compare.md) | Compare engine (`scripts/compare.py`) |
| [docs/together.md](docs/together.md) | Together: workouts run with others (`scripts/together.py`, `/api/together`) |
| [docs/similarity.md](docs/similarity.md) | Similarity index: search, nearest, groups, names |
| [docs/session-groups.md](docs/session-groups.md) | Places, routes, legs and reps (`groups.json`) |
| [docs/fitness-model.md](docs/fitness-model.md) | The fitness / shape model |
| [docs/run-analysis.md](docs/run-analysis.md) | Interval rep and break detection |
| [docs/report.md](docs/report.md) | The HTML report |
| [docs/data-layout.md](docs/data-layout.md) | Scripts and the `data/` folder layout |
| [docs/README.md](docs/README.md) | Index of everything else (Garmin API, data findings, FIT files, calibration) |
| [docs/plans/polish-audit.md](docs/plans/polish-audit.md) | UI audit, fixes and the desktop browser recipe for screenshots |
| [docs/plans/compare-plan.md](docs/plans/compare-plan.md) | Plan: common start line, interval alignment, compare fixes and next features |
| [docs/plans/together-plan.md](docs/plans/together-plan.md) | Plan: Together — workouts run with others (detection, list, session page with standings and replay) |

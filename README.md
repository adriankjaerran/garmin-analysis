# garmin-analysis

Personal Garmin data, downloaded with the Garmin Personal API and analysed locally, plus Concept2 RowErg / SkiErg
workouts from the Concept2 Logbook ([docs/concept2.md](docs/concept2.md)). It covers every run, ski, hike and erg session:
the track, interval reps and breaks, places and repeated routes, similar sessions, side-by-side comparisons, workouts
run with others (a small running club) and a fitness ("shape") model on the Daniels VDOT scale.

- **Web app** (`app/`): a FastAPI backend and a Vite / TypeScript / Preact frontend (MapLibre maps, uPlot charts).
- **HTML report** (`scripts/build_report.py` → `data/<person>/report.html`): one self-contained file.

Everything runs on 127.0.0.1. Health data stays in `data/`, which is gitignored.

## Quick start
```sh
uv sync                                         # Python deps (+ dev: pytest, httpx)
(cd app/web && npm install && npm run build)    # frontend -> app/web/dist (again after frontend changes)
uv run garmin-app-cache adrian                  # pre-parse FIT files into data/adrian/cache (incremental)
uv run garmin-app                               # http://127.0.0.1:8765   (--person, --port, --reload)
uv run pytest -q                                # API tests (skipped without data/adrian)
```
For frontend development with hot reload, run `uv run garmin-app` and `cd app/web && npm run dev`
(http://localhost:5173).

## Refreshing the data
Run in this order (each step reads the previous step's output):

```sh
uv run scripts/fetch_index.py adrian                                   # 1. activity list + daily summaries
uv run scripts/fetch_activities.py adrian --days 14 --min-interval 1   #    FIT files / activity details
uv run scripts/fetch_daily.py adrian --days 14 --min-interval 1        #    sleep, intraday HR (optional)
uv run scripts/fetch_concept2.py adrian                                #    Concept2 RowErg / SkiErg workouts (optional, CONCEPT2_<P>_TOKEN)
                                                                       #    (only erg sessions new? uv run scripts/refresh_concept2.py adrian)
uv run scripts/session_groups.py adrian                                # 2. places, routes, legs, reps -> groups.json
uv run scripts/similarity.py adrian build                              # 3. similarity index (names, search, nearest)
uv run scripts/hr_settings.py adrian                                   # 4. HR max / rest + per-device trust -> profile/hr_settings.json
uv run scripts/fitness_model.py adrian                                 #    fitness.json (shape per discipline, official races)
uv run scripts/together.py build                                       # 5. together.json: workouts run with others
uv run garmin-app-cache adrian && uv run garmin-app                    # 6. app cache (series, tracks, rep times) + app
```
Steps 1–4 run per person. `together.py build` runs once after them, because it reads every person's data
(incremental). On a person's first build, run `session_groups.py` once more after `hr_settings.py`: the easy/steady
split reads the per-activity HR flags from `hr_settings.json`. Official race results (Races pages, race markers on Fitness) are hand-kept in `data/races.json`
(shared by everyone); rebuild fitness after editing it. The app reloads `groups.json`, the similarity index,
`fitness.json` and `together.json` when they change, and it refreshes the FIT cache in the background at startup.
The Fitness page has a **Rebuild** button that re-runs `fitness_model.py`. API keys, tokens and pacing: [docs/garmin-api-guide.md](docs/garmin-api-guide.md).

## Docs
**Start here:** [docs/CONCEPTUAL_MODEL.md](docs/CONCEPTUAL_MODEL.md), which explains the concepts and how they fit
together. Every doc is listed in [docs/README.md](docs/README.md).

| | |
|---|---|
| [docs/app.md](docs/app.md) | The web app: pages and URLs, endpoints, architecture, tests, visual QA |
| [docs/compare.md](docs/compare.md) | Compare engine (`scripts/compare.py`) |
| [docs/together.md](docs/together.md) | Together: detecting workouts run with others (`scripts/together.py`) |
| [docs/community.md](docs/community.md) | Community: the club layer on top of Together (sessions, club, members, goals) |
| [docs/session-groups.md](docs/session-groups.md) | Places, routes, legs and reps (`groups.json`) |
| [docs/similarity.md](docs/similarity.md) | Similarity index: search, nearest, groups, names |
| [docs/fitness-model.md](docs/fitness-model.md) | The fitness / shape model and race predictor |
| [docs/run-analysis.md](docs/run-analysis.md) | Interval rep and break detection |
| [docs/data-layout.md](docs/data-layout.md) | Scripts and the `data/` folder layout |
| [docs/concept2.md](docs/concept2.md) | Concept2 Logbook: **how-to** (refresh after erg sessions, fix shared sessions, token), mapping, cleaning |
| [docs/erg.md](docs/erg.md) | Erg fitness trend per machine (2k-equivalent pace, watts) |
| [docs/plans/open-items.md](docs/plans/open-items.md) | What isn't built yet, and open decisions |

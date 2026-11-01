# The web app

A local app for browsing the data: maps of every track, a day view, search, comparison, fitness and the running club.
FastAPI backend (`app/server`) + Preact/Vite frontend (`app/web`), following the Fabstack console UI rules of the HTML
report ([report.md](report.md)). Concepts (places, workouts, shape, together) are in
[CONCEPTUAL_MODEL.md](CONCEPTUAL_MODEL.md); this file is the map of the app.

## Run
```
uv sync
(cd app/web && npm install && npm run build)   # once, and after frontend changes -> app/web/dist
uv run garmin-app-cache adrian                 # pre-parse FIT files (incremental)
uv run garmin-app                              # http://127.0.0.1:8765  (or: uv run python -m app.server)
```
- Options: `garmin-app --person adrian --port 8765 --reload`. One server serves everyone in `data/`; `--person` is the
  default person.
- The server refreshes every person's cache incrementally at startup and on `POST /api/cache/refresh`. Re-run
  `scripts/session_groups.py` first to get places, routes, legs and reps for new activities.
- Frontend dev: run `uv run garmin-app`, then `cd app/web && npm run dev` (http://localhost:5173, hot reload, `/api`
  proxied to :8765; other ports: `API_PORT=8767 VITE_PORT=5175 npm run dev`). `npm run typecheck` checks types only.

## Pages
Every view is a URL, so links and reloads keep the state. URLs are prefixed with the person (`/<person>/activities/123`;
the table leaves the prefix out); unprefixed URLs redirect to the default person. A person is anyone with an
`activities_index.json` in `data/`. Switching person keeps the section (and the whole `/day/…` and `/community/…`
path) and drops ids and filters, except that an activity done together opens the other person's activity in the same
group. The frontend writes person-less paths; `lib/router.ts` and `lib/api.ts` add the prefix.

Sidebar: **Personal** (Home; Activities, Calendar & day, Map, Places & routes; Compare, Fitness, Race predictor) and
**Community** (Sessions, Club, Members). Every page has a `PageHeader` (title, context, `?` help). Below 760 px the
sidebar becomes a top bar.

| URL | Shows |
|---|---|
| `/?vm=km\|time` | **Home**: current shape, latest active day, 5-week training log, volume vs typical, recent workouts. `vm` = km or hours |
| `/map?sport=&year=&mode=heat&v=lon,lat,zoom` | All GPS tracks by sport or as a heat map; click a track to open it |
| `/activities?q=&sport=&from=&to=&adv=1&…&pick=a,b&sel=<id>` | **Activities**: filterable, sortable list with optional map. Search filters as in `/api/search` (`place`, `place_mode=main`, `route`, `signature`, `rep_count`, `rep_len`, `tol`, `rest_s`, `part`, …). `sel` = selected row, `pick` = ticked for Compare. `view=groups&gk=places\|regions\|routes\|workouts&g=<key>` = groups browser. `year=2025` is rewritten to `from`/`to` |
| `/activities/<id>?color=speed\|hr\|alt&seg=reps\|lengths\|legs\|laps&nk=&np=&go=closest` | **Activity**: summary, map, charts, segments, route rank, records and workout progress, [shape](fitness-model.md) section, closest activities (`nk` = how many, `np` = preset) |
| `/day` | Redirects to the last active day |
| `/day/<date>` | **Day**: the day's activities, year calendar, map of every track, daily summary, sleep, intraday HR. ←/→ = previous/next day |
| `/places?place=<id>&route=<id>&tab=places\|activities&sport=` | **Places & routes**: places map and list; a place's routes and activities; a route's reference line, members and ranking |
| `/compare?ids=a,b,c&mode=&ref=&start=&rep=&view=reps&…` | **Compare** sessions on one axis (engine and modes: [compare.md](compare.md)). `mode` = distance\|moving\|elapsed\|intervals\|breaks; `ref` = first\|median; `start` = ref\|<id>\|lat,lon\|rep1; `rep`/`view` pick reps. `?suggest=<id>` picks a set; bare `/compare` lists sessions to start from |
| `/fitness?d=interval,long,ski&ov=0&hr=0&b=1&from=&to=&sd=&sk=&q=` | **Fitness**: shape per discipline ([fitness-model.md](fitness-model.md)), shape over time, sessions, personal records (`#records`). `d` = disciplines, `ov=0` hides overall, `hr=0` hides the with-HR variant, `b=1` shows lower bounds, `sd`/`sk`/`q` filter the session table |
| `/predictor?date=YYYY-MM-DD&km=7.5` | **Race predictor**: predicted times on a day, VDOT chart, distance-curve setting ([fitness-model.md](fitness-model.md#coupled-distance-curve-race-predictor)) |
| `/community?cat=&people=&period=90d\|12m\|<year>\|<yyyy-mm>&q=&near=1` | **Community · Sessions**: club sessions, newest first, grouped by month ([community.md](community.md)) |
| `/community/<gid>?more=1&rep=&align=clock&replay=1&rrep=` | **Session**: one Together group (results, rep by rep, splits; maps, replay, photos under "More detail"). Same under any person prefix. See [together.md](together.md#pages), [community.md](community.md) |
| `/community/club?period=` | **Club**: totals, goals, members, faster together than alone, month by month, highlights |
| `/community/members` | **Members** table |
| `/community/members/<person>` | **Member profile**: stats, all sessions, form in club sessions, partners |

Basemaps (dropdown on every map, remembered app-wide): Kartverket topo / grey (Norway only; OSM is used outside
Norway), OpenStreetMap, OpenTopoMap.

## Architecture
```
app/server/
  __main__.py   garmin-app entry (uvicorn, factory=create_app)
  main.py       FastAPI app: routers under /api, PersonPrefix middleware (/api/<person>/x -> /api/x),
                serves app/web/dist with SPA fallback, background cache refresh at startup
  deps.py       get_store(): person from the prefix, else GARMIN_PERSON / --person; people()
  store.py      Store(person): lazy, mtime-memoised loaders (index, groups.json, similarity index, tracks,
                daily, sleep, HR), per-activity series LRU, resampling, workout label, distance trust
  fitcache.py   FIT -> data/<p>/cache/series/<id>.npz + cache/tracks.json (garmin-app-cache)
  routers/      meta, activities, days, home, maps, places, progress, search, compare, fitness,
                together, community, settings
app/web/src/
  main.tsx      shell (sidebar, person menu) + route table
  pages/        one file per page (+ page CSS)
  components/   MapView (MapLibre), TracksMap, SeriesCharts / CompareCharts / ShapeChart (uPlot), Table, …
  lib/          router.ts, api.ts (types, memoised fetch, useApi), format.ts, search.ts, compare.ts,
                fitness.ts, together.ts, community.ts, people.ts, theme.ts
```
- **Reuse**: `app/server/__init__.py` puts `scripts/` on `sys.path`. FIT reading via `gps_quality._read_fast`, reps via
  `run_analysis.analyse` (ski and indoor Bislett reps from groups.json, `fitcache.reps_from_groups`), daily / sleep
  via `build_report`. Search, nearest and groups wrap `similarity.py`; Compare wraps `compare.py` (lock + response
  memo); Fitness serves `fitness.json` from `fitness_model.py`.
- **Cache**: `series/<id>.npz` = per-record arrays (`t lat lon hr speed alt dist cad power`) + `meta` JSON (laps,
  reps, swim lengths). `tracks.json` = encoded polylines (`poly` 4 m, `poly_lo` 20 m) + bbox. Incremental via
  `cache/series_manifest.json`; a missing or stale npz is rebuilt on request.
- **Series** in `/api/activities/{id}` are resampled to `ceil(duration / 3600)` s steps, speed / cadence / power
  smoothed over 5 s, long gaps null.
- **Frontend**: Preact + a small history router, no state library; uPlot for charts, MapLibre GL for maps; colours
  from the report's CSS tokens (`src/styles.css`). GET responses are memoised per page load.
- **New router**: a module in `app/server/routers/` with `router = APIRouter()`, listed in `main.py`; frontend types
  in `lib/api.ts`, pages and nav in `main.tsx`.

## Workout label
One name per activity, used everywhere (`store.workout_name` → `workout_label` on every row):
1. its own rep signature when it names the structure ("15×400", "3×1800 + 2×900");
2. else, for intervals done with others, the Together group's shared structure;
3. else its own signature ("8 reps (mixed …)");
4. else the workout family ("Easy run") or the Garmin type.

`workout_detail` adds rest / route for tooltips.

## Distance trust
`dist_trusted` (session_groups `dist_ok`: GPS-backed, or a pool swim by lengths) is on every row. When false,
`dist_km` stays the watch's number, but indoor Bislett sessions get an estimate `dist_est_km` (rep laps + easy-pace
warm-up / cool-down; `dist_note` says how), others show "—"; km totals, paces and records leave them out.
Frontend helpers: `components/ActivityBits.tsx` (`DistCell`, `trustedKm`, `kmTotal`).

## Vocabulary
- **Avg rep pace** = total rep distance / total rep time of the main reps (`rep_pace_ms`), shown as "6×1000 @ 3:27 /km".
- <a id="rep-pace"></a>**Track reps = set distance**: in a track session a rep from 5 % under to 12.5 % over a round
  track distance (or the group's distance label) counts as exactly that distance (`session_groups.track_set_dists`);
  the measurement stays in `gps_dist`. Applies to avg rep pace, the activity's reps, Compare and Together.
- **Pace / speed** (`format.ts` `speedText`): /km on foot, /100 m for swims, km/h otherwise. Seconds always with a
  unit; gaps in words ("12 s behind"); reps "Rep 3" / "R3"; green / red only for better / worse.

## Place model
From the similarity index ([similarity.md](similarity.md)):
- **Place id** (`Bislet`) is the key in URLs and filters, **name** (`Bislett`) is what's shown; `place=` accepts
  either. Names: user override > curated list > cached geocode > `<Garmin location>-<rank>` (`session_groups.name_places`).
- An activity's **place** is its main place (named place with ≥ 40 % of its time, else its start cluster).
- Counts: `n_main` (sort key), `n_touched` (starts, ends or ≥ 5 % / 3 min there; default for `/api/search?place=`),
  `n_start`. Route names come from the index.
- **HR** on rows = whole-activity average; Compare's `hr_avg` = moving seconds only. **Rest**: `rest_s` = this
  session's median rest, `rest_group_s` = its same-workout group's.

## Endpoints
All GET unless noted; interactive docs at `/api/docs`. Each also answers under `/api/<person>/…` (the frontend's form).

| Endpoint | Purpose |
|---|---|
| `/api/people` | everyone with data + display info (name, initials, colour) |
| `/api/meta` | person, counts, years, sports, types, build time |
| `POST /api/cache/refresh` | start an incremental cache build |
| `/api/activities?sport=&type=&from=&to=&q=&place=&route=` | activity rows, newest first |
| `/api/activities/{id}?max_points=` | one activity: summary, group, legs, laps, reps, resampled series, route rank, neighbours |
| `/api/activities/{id}/track?format=geojson\|columns` | GPS track with t / speed / hr / alt |
| `/api/days/{date}` | a day's activities, tracks, summary, sleep, intraday HR |
| `/api/calendar?from=&to=&sport=` | per active day: count, km, time, sports |
| `/api/home?date=` | Home payload (latest day, volume, log, recent, shape) |
| `/api/map/tracks?bbox=&sport=&type=&from=&to=&q=&detail=lo\|hi` | simplified polylines for the map |
| `/api/places?touched=0\|1` | places with counts ([Place model](#place-model)) |
| `/api/routes?place=&sport=` | routes with ≥ 2 sessions + PB |
| `/api/routes/{id}` | route detail: reference line, legs, members, ranking, trend |
| `/api/search?<filters>&sort=&limit=&poly=1` | filtered activity search ([similarity.md](similarity.md)) |
| `/api/search/options` | filter values with counts |
| `/api/nearest/{id}?k=&preset=&weights=&same_sport=` | closest activities with scores and reasons ([similarity.md](similarity.md)) |
| `/api/groups?kind=&touched=&min_n=&<filters>` | groups (places, regions, routes, workout tree) |
| `/api/compare?ids=&mode=&…` | comparison ([compare.md](compare.md)) + app rows and map tracks |
| `/api/compare/suggest?id=&n=` | suggested comparison sets for one activity |
| `/api/records` | personal records from Garmin's fastest splits (trusted runs only) |
| `/api/progress/{id}` | progression of this interval workout (same signature) |
| `/api/fitness?discipline=&from=&to=&hr=` | fitness.json: shapes, sessions, variants ([fitness-model.md](fitness-model.md)) |
| `/api/fitness/session/{id}` | one activity's scores and explanation, or why it isn't scored |
| `/api/fitness/predictor` | race-predictor chart data ([fitness-model.md](fitness-model.md#coupled-distance-curve-race-predictor)) |
| `/api/fitness/predict?date=&km=` | predicted times on a day |
| `POST /api/fitness/rebuild`, `GET /api/fitness/rebuild` | rebuild fitness.json in the background / status |
| `/api/settings` (GET, PUT) | per-person settings (`distance_curve`; [fitness-model.md](fitness-model.md#distance-curve-on--off-per-person-setting)) |
| `/api/together?person=&sport=&kind=&label=&near=1&place=&from=&to=&q=` | workouts done with others ([together.md](together.md#api)) |
| `/api/together/{gid}` | one Together session's results |
| `/api/together/{gid}/replay?step_s=&max_points=` | positions per participant for maps and replay |
| `/api/community/sessions` | club sessions list ([community.md](community.md#api), as all below) |
| `/api/community/sessions/{gid}` (GET, PATCH) | session with club layer / edit title, type, guests, note |
| `POST /api/community/sessions/{gid}/photos`, `DELETE …/photos/{photo_id}` | add / delete photos |
| `POST`, `DELETE /api/community/sessions/{gid}/kudos` | give / take back kudos |
| `/api/community/photos/{gid}/{file}` | photo file |
| `/api/community/club?from=&to=` | club dashboard |
| `/api/community/members`, `/api/community/members/{person}` | members table / member profile |
| `/api/community/goals` (GET, POST), `/api/community/goals/{goal_id}` (PATCH, DELETE) | club goals |

## Tests
`uv run pytest -q`. `tests/test_api.py` runs smoke tests with FastAPI `TestClient` against the real data in
`data/adrian` (skipped when missing): every GET answers 200, plus invariants (Bislett 6×1000 group = 17, route R3 = 19
sessions and its ranking, Home, Compare rep alignment, distance trust, records, progress, suggestions). It reads
data and caches only. Other files cover community, together, the predictor, form delta, the distance-curve setting
and classification.

## Visual QA (screenshots)
Check UI changes in a real browser for both people, dark and light: 1600 or 1440 px wide, plus 390 × 844 for mobile.
Screenshots go in `data/<person>/screenshots/<date>/` (gitignored). With cmux, a browser split is only ~310 px wide
and one in a background workspace renders blank, so give it its own window:
```sh
uv run garmin-app &                                                 # http://127.0.0.1:8765
cmux workspace create --name garmin-qa --focus false                # -> workspace:N
cmux browser open http://127.0.0.1:8765/ --workspace workspace:N    # -> surface:S (opens as a split)
cmux new-window                                                     # -> window W (with an empty workspace:M)
cmux move-workspace-to-window --workspace workspace:N --window W
cmux workspace select workspace:N                                   # now visible, so it renders
cmux workspace close workspace:M
cmux tree --workspace workspace:N                                   # find the terminal surface T
cmux close-surface --surface surface:T --workspace workspace:N      # the browser fills the window
cmux browser surface:S viewport 1600 1000                           # CSS viewport; taller (1600 2400) = whole page
cmux browser surface:S goto 'http://127.0.0.1:8765/adrian/fitness'
cmux browser surface:S screenshot --out data/adrian/screenshots/<date>/fitness.png
cmux browser surface:S errors list; cmux browser surface:S console list
cmux browser surface:S eval 'JSON.stringify([innerWidth, innerHeight, document.visibilityState])'
# cleanup: pkill -f garmin-app; cmux workspace close workspace:N   (closes the extra window too)
```
Images over 2000 px are downsized when read back; crop them (PIL) to inspect details.

## Known gaps
- Compare: hovering the map doesn't move the chart cursor; route-position markers wait during long stops; reversed
  sessions keep their own direction; selections capped at 40 (suggestion sets at 8, 40 for a route).
- Activities: place / region / route filters are single-select in the UI (the API takes `a|b`); Garmin
  municipalities have no shape on the regions map; the workout tree's open state isn't in the URL; results aren't paged.
- Closest activities is limited to the same sport; custom weights aren't exposed in the UI.
- No auth; binds to 127.0.0.1 only. One Fitness rebuild at a time across people.
- Frontend GET responses are memoised for the session; reload after new data arrives.
- The overview map draws all tracks as one GeoJSON layer (fine for ~1000 tracks).
- Reps exist only for runs classified as intervals and for `ski_intervals.py` sessions; other skiing has pauses/legs.
- Sleep and intraday HR exist only for days fetched with `fetch_daily.py`.

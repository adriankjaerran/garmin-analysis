# The web app

A local app for browsing the data on maps: every run / ski / hike shows its track, a day shows
everywhere you were that day. FastAPI backend (`app/server`) + Vite/TypeScript frontend (`app/web`),
following the same Fabstack console UI rules as the HTML report ([report.md](report.md)).

## Run
```
uv sync                                   # fastapi, uvicorn
(cd app/web && npm install && npm run build)   # once, and after frontend changes -> app/web/dist
uv run garmin-app-cache adrian            # pre-parse FIT files (incremental, ~8 s first time, 0.1 s after)
uv run garmin-app                         # http://127.0.0.1:8765  (or: uv run python -m app.server)
```
Options: `garmin-app --person adrian --port 8765 --reload`. The server also refreshes the cache
incrementally in the background at startup (new or changed FIT files only) and on
`POST /api/cache/refresh`. Re-run `scripts/session_groups.py` first if you want places, routes, legs and
reps for new activities; then `garmin-app-cache` picks up the rep times.

Frontend development: run `uv run garmin-app` and, in another terminal, `cd app/web && npm run dev`
(http://localhost:5173, hot reload, `/api` proxied to :8765). `npm run typecheck` checks types only.

## Pages
Every view is a URL (path + query), so links and reloads keep the state.

| URL | Shows |
|---|---|
| `/map?sport=run&year=2025&mode=heat&v=lon,lat,zoom` | All GPS tracks (20 m simplified), coloured by sport or as a heat overlay; hover = activity, click = open it |
| `/activities?q=6×1000&sport=run&year=2026&sel=<id>` | Sortable list; selecting a row shows a mini map + summary in the side panel, clicking again opens the activity |
| `/activities/<id>?color=speed\|hr\|alt` | Summary; map with the track coloured by pace/speed, HR or elevation; stacked charts (pace or speed, HR, elevation, cadence, power) with reps (or pauses) shaded. Hovering the chart moves a marker on the map, hovering the track moves the chart cursor. Reps, laps, legs & pauses (hovering a row highlights that stretch on the map), similar sessions |
| `/day/<date>` | Year heat calendar (colour = main sport, darker = longer), map of every track that day, daily summary, sleep, 2-minute HR. ← / → keys = previous / next day |
| `/places?place=<id>&route=R3&tab=places&sport=ski` | Places as circles (size = sessions), route list; a route shows its reference line (dashed), common stops, all member tracks and the session table |
| `/search`, `/compare`, `/fitness` | Stubs (see extension points) |

Basemaps (toggle on every map, remembered in localStorage): Kartverket topo and grey
(`cache.kartverket.no`, Norway only; blank elsewhere), OpenStreetMap, OpenTopoMap.

## Architecture
```
app/server/
  __main__.py      garmin-app entry (uvicorn, factory=create_app)
  main.py          FastAPI app: routers under /api, serves app/web/dist with SPA fallback,
                   background cache refresh at startup
  store.py         Store(person): lazy loaders with mtime memo (activities_index.json + groups.json +
                   tracks.json, daily summaries, sleep, intraday HR), per-activity series LRU (64),
                   resample() / track_points() for the API
  fitcache.py      FIT -> data/<p>/cache/series/<id>.npz + cache/tracks.json (garmin-app-cache)
  deps.py          get_store() dependency (person from GARMIN_PERSON, default adrian)
  routers/         meta, activities, days, maps, places, search*, compare*, fitness*   (* = stubs)
app/web/
  src/main.tsx     shell (sidebar) + route table
  src/lib/         router.ts (history router, setQuery), api.ts (types, memoised fetch, useApi),
                   format.ts, geo.ts (polyline decode, colour ramp)
  src/components/  MapView (MapLibre + basemap toggle), TracksMap, SeriesCharts (uPlot),
                   Table (sortable), Calendar, ActivityBits
  src/pages/       MapPage, ActivitiesPage, ActivityPage, DayPage, PlacesPage, StubPage
```
- Reuse of the analysis code: `app/server/__init__.py` puts `scripts/` on `sys.path`; the FIT reader is
  `gps_quality._read_fast` (extended with HR/altitude/cadence/power and lap fields, the same way
  `session_groups.py` extends it), rep/break times come from `run_analysis.analyse`, daily summaries and
  sleep from `build_report.daily/sleep`. The scripts' CLIs are unchanged.
- Cache: `series/<id>.npz` holds per-record arrays `t lat lon hr speed alt dist cad power` plus a `meta`
  JSON (start time, laps, reps/breaks for interval runs). `tracks.json` holds encoded polylines per
  activity: `poly` (4 m Douglas–Peucker, day and mini maps) and `poly_lo` (20 m, overview map), plus bbox.
  Incremental via `cache/series_manifest.json` (FIT mtime + size). A missing or stale npz is also rebuilt
  on the fly when an activity is requested.
- Series in `/api/activities/{id}` are resampled to a regular grid of `ceil(duration / 3600)` s
  (1 Hz up to an hour); speed, cadence and power are smoothed over 5 s; gaps longer than
  max(10 s, 3 steps) are null.
- Frontend: Preact (3 kB, hooks, JSX) + a 60-line history router, no state library. Charts use uPlot
  (canvas, ~50 kB): thousands of points per series, cursor sync and hooks for chart↔map linking come
  built in, which would be a lot of hand-written hit-testing with SVG. Maps use MapLibre GL JS 6 (its
  worker is emitted as a separate asset, see `MapView.tsx`). Colours and type come from the report's
  CSS tokens (`src/styles.css`, plus `--sport-*` colours).

## Endpoints
All GET unless noted; interactive docs at `/api/docs`.

| Endpoint | Returns |
|---|---|
| `/api/meta` | person, counts, years, sports, types, groups.json build time |
| `/api/activities?sport=run,ski&type=&from=&to=&q=&place=&route=` | `{n, activities: [...]}` newest first; row = index fields + groups.json place/area/route/type/sig + `fit`, `track` flags. `q` matches all words in name, type, place, route, signature, date, id |
| `/api/activities/{id}?max_points=3600` | `activity`, `group` (groups.json entry without arrays), `legs`, `pauses` (objects), `similar`, `route`, `laps` (FIT), `reps`/`breaks` (with start/end s), `series` (columnar: t, lat, lon, hr, speed, alt, dist, cad, power), `newer`/`older` ids |
| `/api/activities/{id}/track?format=geojson\|columns&max_points=4000` | GPS track with per-point `t`, `speed` (m/s, 5 s smoothed), `hr`, `alt`; geojson = Feature(LineString) with the arrays in `properties` |
| `/api/days/{date}` | that day's activities, `tracks` (4 m polylines), daily `summary`, `sleep`, intraday `hr`, `prev_active` / `next_active` |
| `/api/calendar?from=&to=&sport=` | per active day: `n`, `km`, `s`, `sports` counts |
| `/api/map/tracks?bbox=w,s,e,n&sport=&type=&from=&to=&q=&detail=lo\|hi` | simplified polylines (`poly`) with id, date, sport, type, name, km, bbox |
| `/api/places` | places (id, lat/lon, n, types, area, routes, last) and areas |
| `/api/routes?place=&sport=` | routes with ≥ 2 sessions: id, name, place, sport, shape, km, n, first/last |
| `/api/routes/{id}` | route row + reference polyline (`ref` lat/lon/km/alt), waypoints, `legs_table`, `members` (activity rows + route_dir, pause_s, `poly` 20 m) |
| `POST /api/cache/refresh` | starts an incremental cache build in the background |
| `/api/search`, `/api/nearest/{id}`, `/api/compare?ids=`, `/api/fitness` | 501 stubs |

## Extension points
- **Search / nearest**: `app/server/routers/search.py` (wrap `scripts/similarity.py`:
  `load_index(person)`, `ix.search(filters)`, `ix.nearest(id, k, weights)`), page `app/web/src/pages/` →
  replace the `/search` entry in `ROUTES` in `src/main.tsx`.
- **Compare**: `routers/compare.py` (`ids=a,b,c`), route `/compare?ids=…`. Series per activity are in
  `Store.series(id)`; `SeriesCharts` can be reused per activity.
- **Fitness**: `routers/fitness.py` reads `data/<p>/fitness.json` (`scripts/fitness_model.py`), route `/fitness`.
- New routers: add a module in `app/server/routers/` with `router = APIRouter()` and list it in
  `main.py`. Types for the frontend go in `src/lib/api.ts`. Remove "soon" from the nav in `main.tsx`.

## Known gaps
- No auth, single person per server (`--person`), binds to 127.0.0.1 only.
- Frontend GET responses are memoised for the session; reload the page after new data arrives.
- The overview map draws all tracks as one GeoJSON layer (fine for ~1000 tracks; a vector-tile or
  pre-rendered heat layer would be needed for many more).
- Kartverket tiles are blank outside Norway; switch basemap for trips abroad.
- Reps exist only for runs that `session_groups.py` classified as intervals; skiing has pauses/legs only.
- Sleep and intraday HR exist only for days fetched with `fetch_daily.py`.

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
(http://localhost:5173, hot reload, `/api` proxied to :8765; other ports: `API_PORT=8767 VITE_PORT=5175 npm run dev`). `npm run typecheck` checks types only.

## Pages
Every view is a URL (path + query), so links and reloads keep the state.

| URL | Shows |
|---|---|
| `/map?sport=run&year=2025&mode=heat&v=lon,lat,zoom` | All GPS tracks (20 m simplified), coloured by sport or as a heat overlay; hover = activity, click = open it |
| `/activities?q=6×1000&sport=run&year=2026&place=<id>&sel=<id>` | Sortable list (28 px rows; GPS column = dot: green track, amber FIT without GPS, grey no file); selecting a row shows a mini map + summary in the side panel (Open, Day, Compare, Closest), clicking again opens the activity |
| `/activities/<id>?color=speed\|hr\|alt` | Summary; map with the track coloured by pace/speed, HR or elevation; stacked charts (pace or speed, HR, elevation, cadence, power) with reps (or pauses) shaded. Hovering the chart moves a marker on the map, hovering the track moves the chart cursor. Pool swims: pace /100 m, swimming vs rest time, pool / lengths / strokes, pace (min/100 m) + stroke-rate charts with rests shaded. No GPS: charts full width, no map. **Segments** tabs (`seg=reps|lengths|legs|laps`, default reps, else lengths, else legs): reps, pool lengths, legs (< 100 m merged into the next) & pauses, watch laps (hovering a row highlights that stretch on the map). **Closest activities** (`&nk=5\|10\|20&np=balanced\|workout\|route\|place`): top-k by `/api/nearest`, score, one bar per weighted component, the top reasons; mini map with this track dashed and theirs on top (hover row ↔ track); Moving / rep pace / rest columns; “Compare these” → `/compare?ids=this,top 5`; `?go=closest` scrolls to it |
| `/day/<date>` | Year heat calendar (colour = main sport, darker = longer; cells scale with the width), map of every track that day (empty day: the previous active day's tracks, muted), daily summary, sleep, 2-minute HR (gaps not bridged). ← / → keys = previous / next day |
| `/places?place=<id>&route=R3&tab=places\|activities&sport=ski` | Places as circles (size = main-place activities, top 14 labelled), route list; a place shows "94 · 108 touched", its routes and (`tab=activities`) its activities, "List in Find & group"; a route shows its reference line (dashed), common stops, all member tracks, the activity table, "Compare all n" and "List in Find & group" |
| `/search?place=Bislet&place_mode=main&rep_count=5-7&rep_len=1000m&tol=0.1&rest_s=2:00-3:00&pick=a,b` | **Find & group.** Filter bar (all in the URL): words (`q`), sport, place / region (touched or `main`), route, workout family, category, signature (`6×1000` or `~×1000`), same-workout key (`exact_key`), reps (`5-7`), rep length (`1000m`, `4:00`, `900-1100`, ± `tol`), rest (`2:00-3:00`, `90-`), `part=main`, km (`8-12`), dates (`from`, `to`). Results: sortable table — tick box, Date (both sticky when the table scrolls sideways), Workout (same-workout label; sport + name in the tooltip), and only when intervals are in the result **Main reps** (“6 × 1000 m”: reps × measured median rep length of the main part), **Rep pace** (runs /km, swims /100 m, else km/h; mean rep time in the tooltip) and **Rest** (this activity's median rest; its same-workout group's median in the tooltip), then km, Time (moving), Pace, HR (whole activity), Climb, Place (“Bislett”, touched places in the tooltip), Route, Region — + map of every result track on the Grey basemap, fitted to the densest cluster (“n elsewhere · show all”) (hover row ↔ track, click = activity). Tick rows (`pick`) → “Compare selected” (`/compare?ids=…`); with ≤ 12 results “Compare all”. `view=groups&gk=places\|regions\|routes\|workouts&touched=1&g=<key>`: groups left by the current filters — places (points, blue = named, grey = start clusters, plus named shapes), regions (polygons), routes (lines), workout tree family › signature › same workout. Click a row = show it on the map (members' tracks, zoom), click again or “list →” = apply it as a filter |
| `/compare?ids=a,b,c&mode=distance\|moving\|elapsed\|intervals\|breaks&ref=first\|median&focus=<id>` | **Compare** one session with one or many ([compare.md](compare.md) is the engine). Session chips (click = highlight, `ref` = make it the reference, ↗ = open, × = remove; hover for the actions), paste ids / activity URLs, **Suggestions** for the first session (same workout, same signature at the place, any signature, whole route, most similar → replace the set, or add single sessions). Reference: first session or median of the selection. Mode tabs: **Route position** (ghost race: gap to the reference along the route, + behind, plotted with ahead up; `delta=elapsed` counts stops), **Moving time**, **Elapsed** (distance ahead at the same time), **Intervals** (reps start at the same x, breaks squeezed to `gap` s (15–120, default 60), `rep_axis=normalized` = % of each rep, pace range from the reps only), **Breaks** (breaks start at the same x in real seconds, reps squeezed to `rgap` s, HR first). `match=index` pairs reps by number instead of by length. `metric=pace\|speed`. Left: map with every track and one marker per session at the chart cursor — at the same time in the time and aligned modes; in Route position where each session was when the reference (or median) reached the cursor, so the markers race each other — plus a hover legend with every session's values at the cursor (sorted by gap / pace). Right: synced uPlot charts (gap, pace/speed, HR, elevation; reps shaded), reference dark on top, the highlighted session thick; with > 6 sessions the others are grey with the interquartile band, with the median reference a dashed median line; drag = zoom all, double-click = reset. **Intervals extras**: a **Gap within each rep** panel (seconds behind / ahead of the reference at the same distance, reset at every rep start, from the rep points only; shown with ≥ 2 sessions), **Who was faster** under the map (mean rep pace / speed per session vs the reference, reps both have, with green / red bars; the hover legend replaces it while on the charts), a **rep ladder** above the reps table (pace / speed per rep, one line per session, faster up) and the **reps × sessions table tinted** green / red vs the reference with the difference per cell + Mean / Δ ref columns; the reps section comes before the sessions table. Ski sessions get a note that reps are the hard efforts between stops; mixed sports get a ⚠ note. A reference without reps (e.g. a pool swim in Intervals) falls back to the engine's anchor session for the lines. Map: Grey basemap by default. Tables (sortable, best per column green, hover ↔ highlight, click = keep highlighted): sessions with deltas vs the reference (+ gap at the end in Route position, + rep summary in the interval modes); legs × sessions (`lm` = moving time / Δ leg / gap at leg end / speed / HR / stopped / rank; partly covered legs faded); reps × sessions (`rm`) and breaks × sessions (`bm`: duration, kind, HR drop 30/60 s, HR at next start, …), `tab=reps\|breaks`. `/compare?suggest=<id>` picks a set and redirects (intervals → same workout, route → all route sessions, else same workout); `/compare` alone lists recent runs / ski sessions to start from. The activity page has a **Compare** button |
| `/fitness?d=interval,long,ski&ov=0&b=0&from=&to=&sd=&sk=&q=` | Shape per discipline ([fitness-model.md](fitness-model.md)): current-shape table (mean ± sd, confidence dot, % of best, 5k/10k/half equivalents, 90-day trend, last evidence); weekly shape chart with ±1 sd bands and every session as a marker (◆ race, ● hard, ○ lower bound, × implausible; hover = details, click = activity; drag = zoom, double-click = reset), overall % of best in a synced panel below; per-year table; sortable/filterable session table; collapsible "How this is computed". Two model variants: performance only (solid) and with HR (dashed, `hr=0` hides it); a "Performance vs with HR" eval table and the sessions HR demoted. `d` = shown disciplines, `ov=0` hides overall, `b=0` hides bounds, `sd`/`sk` filter the session table by discipline/kind. **Rebuild** re-runs the model |
| `/activities/<id>` → Shape | For scored sessions: one-line meaning ("6×1000 @ 3:29/km with 2:40 rest ≈ VDOT 52.0 ≈ 5k 19:17"), score, evidence kind and weight, shape that day and now, race equivalents, components explained, HR diagnostics (effort %HRR, efficiency, decoupling, HR-only estimate) marked as not used in the score |

Basemaps (toggle on every map, remembered in localStorage): Kartverket topo and grey
(`cache.kartverket.no`, Norway only), OpenStreetMap, OpenTopoMap. Until you pick one, track-heavy maps (Map, Places &
routes, Find & group, Compare) start on Grey and the activity / day maps on Topo (`MapView defaultBasemap`). A map whose centre is outside a rough Norway
outline shows OSM "(auto)" instead of a blank Kartverket map (the stored choice is kept; clicking Topo / Grey on that
map forces Kartverket).

## Architecture
```
app/server/
  __main__.py      garmin-app entry (uvicorn, factory=create_app)
  main.py          FastAPI app: routers under /api, serves app/web/dist with SPA fallback,
                   background cache refresh at startup
  store.py         Store(person): lazy loaders with mtime memo (activities_index.json + groups.json +
                   similarity index (places / route names, store.index()) + tracks.json, daily summaries,
                   sleep, intraday HR), per-activity series LRU (64), resample() / track_points() for the API
  fitcache.py      FIT -> data/<p>/cache/series/<id>.npz + cache/tracks.json (garmin-app-cache)
  deps.py          get_store() dependency (person from GARMIN_PERSON, default adrian)
  routers/         meta, activities, days, maps, places, search, compare, fitness
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
  Incremental via `cache/series_manifest.json` (FIT mtime + size; a new cache format re-parses every FIT but keeps
  the rep analyses of unchanged ones). A missing, stale or old-format npz is also rebuilt on the fly when an activity
  is requested. **Pool swims** (FIT sport 5 with length messages): time origin = session start (the records come in
  bursts and start late, which gave negative lap starts), the series is built from the swum lengths (speed = pool
  length / length time, NaN in rests; distance; strokes/min as `cad`), `meta.swim` = summary + lengths, laps get
  `kind` swim / rest and `pace_100m_s` from the swimming time of their lengths.
- Series in `/api/activities/{id}` are resampled to a regular grid of `ceil(duration / 3600)` s
  (1 Hz up to an hour); speed, cadence and power are smoothed over 5 s; gaps longer than
  max(10 s, 3 steps) are null.
- Frontend: Preact (3 kB, hooks, JSX) + a 60-line history router, no state library. Charts use uPlot
  (canvas, ~50 kB): thousands of points per series, cursor sync and hooks for chart↔map linking come
  built in, which would be a lot of hand-written hit-testing with SVG. Maps use MapLibre GL JS 6 (its
  worker is emitted as a separate asset, see `MapView.tsx`). Colours and type come from the report's
  CSS tokens (`src/styles.css`, plus `--sport-*` colours).

## Place model
One definition for every page and endpoint (source: the similarity index, [similarity.md](similarity.md)):
- **Place id** = stable key in URLs and filters (`Bislet`, `Marienlyst`, `Oslo-3`); **name** = what to show
  (`Bislett`, user overrides). Filters (`place=`) accept either. Names come from one function,
  `session_groups.name_places`: user override (`data/<p>/places.json`) > curated list (KNOWN spots, LANDMARKS) >
  cached reverse geocode (`data/<p>/cache/geocode.json`, optional) > `<Garmin location>-<rank>`.
- An activity's **place** is its **main place**: the named place with ≥ 40 % of its time, else its start cluster.
  `start_place` is the start cluster (detail only).
- Counts: `n_main` (main place; the Places page and `/api/groups?kind=place` sort by it), `n_touched` (starts,
  ends or spends ≥ 5 % / 3 min there = what `/api/search?place=` returns by default), `n_start` (start cluster).
  Bislett: 94 main, 108 touched, 107 start cluster. 216 places have a main-place activity (218 incl. touched-only).
- **Route names** come from the index (start place → end place via named places, climbs): `R3 Holmenkollen climb:
  Marienlyst → Frognerseteren via Holmenkollen 9.1 km`. groups.json route names are only a fallback.
- **HR**: `hr` / `avg_hr` on rows = whole-activity average (Garmin); Compare's `hr_avg` = moving seconds only
  (`hr_avg_moving`, plus `hr_avg_activity` for the header value). **Rest**: `rest_s` = this session's median rest,
  `rest_group_s` = median of its same-workout group (the "rest ~2:30" in `exact_key`).

## Endpoints
All GET unless noted; interactive docs at `/api/docs`.

| Endpoint | Returns |
|---|---|
| `/api/meta` | person, counts, years, sports, types, groups.json build time |
| `/api/activities?sport=run,ski&type=&from=&to=&q=&place=&route=` | `{n, activities: [...]}` newest first; row = Garmin summary (`hr` = whole-activity average HR) + `type_label`, `place` (main place id) / `place_name`, `start_place(_name)` (start cluster), `region`, `route` / `route_name` (similarity index), groups.json area/type/sig, `workout_label`, `swim` (pool swims: pool_m, lengths, active_s, rest_s, pace_100m_s, n_rests, …) + `fit`, `track` flags. `place` = main place, id or name. `q` matches all words in name, type, place, route (name), signature, date, id |
| `/api/activities/{id}?max_points=3600` | `activity`, `group` (groups.json entry without arrays), `legs`, `pauses` (objects), `similar`, `route` (`name` from the index), `laps` (FIT; pool swims add `kind` swim/rest, `swim_s`, `pace_100m_s`), `reps`/`breaks` (with start/end s), `series` (columnar: t, lat, lon, hr, speed, alt, dist, cad, power), `swim` (pool swims: summary + `lengths` [{start, time, strokes, stroke}]), `newer`/`older` ids |
| `/api/activities/{id}/track?format=geojson\|columns&max_points=4000` | GPS track with per-point `t`, `speed` (m/s, 5 s smoothed), `hr`, `alt`; geojson = Feature(LineString) with the arrays in `properties` |
| `/api/days/{date}` | that day's activities, `tracks` (4 m polylines), daily `summary`, `sleep`, intraday `hr`, `prev_active` / `next_active` |
| `/api/calendar?from=&to=&sport=` | per active day: `n`, `km`, `s`, `sports` counts |
| `/api/map/tracks?bbox=w,s,e,n&sport=&type=&from=&to=&q=&detail=lo\|hi` | simplified polylines (`poly`) with id, date, sport, type, name, km, bbox |
| `/api/places?touched=0\|1` | places with ≥ 1 main-place activity (touched=1: also places only started at / passed through): id, `name`, lat/lon, `n` (= `n_main`), `n_touched`, `n_start`, kind, src (named / start), name_src, types, area, routes, last; plus areas. Same set and counts as `/api/groups?kind=place` (see [Place model](#place-model)) |
| `/api/routes?place=&sport=` | routes with ≥ 2 sessions: id, name, place / start_place / end_place / via (ids) + `*_name`, region, sport, shape, km, climb, n, first/last, has_legs (names from the similarity index) |
| `/api/routes/{id}` | route row + reference polyline (`ref` lat/lon/km/alt), waypoints, `legs_table`, `members` (activity rows + route_dir, pause_s, `poly` 20 m) |
| `POST /api/cache/refresh` | starts an incremental cache build in the background |
| `/api/search?<filters>&sort=-date&limit=&poly=1` | `{n, km, truncated, filters, families, rows}`; filters = every `similarity.Index.search` filter under its own name (lists `a\|b`; ranges `lo-hi`, `lo-`, `-hi`, m:ss allowed; one number = exact for counts, ± `tol` for km/time/HR/rep length, ± 5 s for paces) plus `sport` (app families, comma list), `q` (all words in name, date, place(s), region(s), route, workout), `from`/`to`. Row = id, date, start, name, sport, distances/times/HR/climb/speed, place (+ touched `places`), region (+ `regions`), route, workout (family, label, category, signature, main_sig, exact_key, rep_count, rep_dist_m, rep_time_s, rest_s, …), bbox, `poly` (≤ 200-pt encoded polyline) |
| `/api/nearest/{id}?k=10&preset=balanced\|workout\|route\|place&weights=route:0.5,pace:0.2&same_sport=1&<filters>` | `{query, preset, weights (used), components, presets, results}`; result = search row + `score`, `components` {c: 0..1}, `reasons` [{c, s, text}]. `weights` overrides the preset's weights (without `preset`: only those components). Search filters restrict the candidates |
| `/api/groups?kind=place\|start_place\|region\|route\|family\|signature\|exact\|workout&touched=0\|1&min_n=1&<filters>` | `{kind, groups}`: `{key, label, n, first, last, ids, sports}` + place lat/lon/kind/src/n_main/n_touched/`shape` (circle or polygon), region kind/`shape`, route sport/shape/km/climb/`poly`; `workout` = tree `{key, label, n, first, last, places, children: [signature {…, children: [exact {…, ids}]}]}` |
| `/api/search/options` | values for the filter controls with counts: sports (+ `label`), places (`{id, name, n = n_touched, n_main, n_start}`), regions, routes, families, categories, interval signatures and exact keys, types (+ `label`), rest modes, presets |
| `/api/compare?ids=a,b,c&mode=distance\|route_position\|moving\|elapsed\|intervals\|breaks&reference=first\|median&axis=auto\|route\|own&rep_axis=absolute\|normalized&gap_s=60&rep_gap_s=30&match=auto\|index&step_m=&smooth_s=&max_points=1500&tracks=1` | `scripts/compare.py` `compare()` output as documented in [compare.md](compare.md) (sessions + deltas, axis, series per session, legs / slots / reps / breaks / rep_summary / median), plus `sessions[].hr_avg_moving` (= the engine's `hr_avg`, moving seconds only) and `hr_avg_activity` (whole activity, as in the header), `activities` (app rows by id) and `tracks` (`{id: {t, lat, lon}}`, ≤ 1500 points, for the map markers). ≤ 40 ids; 422 with the engine's message for unusable input (e.g. no reps in any session). Engine calls are serialised by a lock (its in-process cache isn't thread-safe); cold rep detection runs in its process pool (4 workers) and is cached in `cache/compare.pkl`; the last 12 responses are memoised (key includes groups.json mtime). Warm: 0.02–0.1 s |
| `/api/compare/suggest?id=&n=8` | `{id, date, mode, sets, activities}`: comparison sets for one activity from the similarity index, each `{key, label, n_total, ids, mode, why}` with this activity first, then the most recent others on or before its date (topped up with later ones): `exact` (same exact workout key), `sig_place`, `sig` (same rep signature; intervals only), `route` (all members up to 40 for non-laps routes), `nearest` (similarity, `workout` weights for intervals else `balanced`). `mode` = suggested view (intervals / distance for a non-laps route / moving; sessions without distance: intervals / elapsed), `modes` = modes that work for this session, `no_distance`, `comparable` + `why_not` (no FIT, or neither distance nor HR → no sets). Set members are limited to sessions the engine can read |
| `/api/fitness?discipline=interval,long,ski,overall&from=&to=&hr=1` | fitness.json (reloaded on mtime) filtered by discipline / date: `disciplines` (current + `trend_90d`, `last_evidence`; yearly; weekly), `sessions` (+ `explain`, activity `name`, `dist_km`, `sport`), `rebuild` status; `hr=1` adds `hr_diagnostics`; `variants` (labels + `hr.disciplines`, same filters) and `eval_variants` (from fitness_eval.json, null if missing). 404 if fitness.json is missing |
| `/api/fitness/eval` | the `variants` block of fitness_eval.json (performance vs with-HR metrics, demoted sessions, sensitivity) or null |
| `/api/fitness/session/{id}` | `{scored, sessions: [...], hr_diagnostics, hr_used_in_score}`; a session = score, kind, weight, `hr` (with-HR variant: kind, sigma, weight, p_max, note, shape, current), `explain`, `components_explained` (label, text, meaning), `shape` that week (mean, sd), race equivalents of the score and of the shape, `current`. A 10k race appears twice (interval + long). `scored: false` for unscored activities, with `not_scored: {key, text}` saying why (sport, indoor / treadmill pace not trusted, no GPS distance, race distance, no ≥ 20 min stretch, …) |
| `POST /api/fitness/rebuild`, `GET /api/fitness/rebuild` | re-run `fitness_model.build` in a background thread (≈0.1 s with cached features, ≈10 s cold) and atomically rewrite fitness.json; GET = status `{running, started, finished, seconds, error}` |

## Extension points
- **Search / nearest / groups** (done): `routers/search.py` wraps `scripts/similarity.py` (`load_index`
  caches the index in-process and reloads it when `similarity_index.json` changes; it rebuilds it when
  older than groups.json). Index ids are ints, the API uses strings; `sport` is mapped to the app's
  families (`store.family(atype)`). Page `pages/SearchPage.tsx` (filter bar, results, groups browser),
  `components/GroupsMap.tsx` (points / shapes / lines / member tracks), `components/NearestPanel.tsx`
  (activity page), types + URL helpers in `lib/search.ts`.
- **Compare** (done): `routers/compare.py` wraps `scripts/compare.py` (lock + response memo) and
  `similarity.Index` for the suggestions. Page `pages/ComparePage.tsx` (picker, mode bar, legend, tables;
  styles in `pages/compare.css`), `components/CompareCharts.tsx` (stacked uPlot on one shared x grid:
  band + focus drawn in hooks, nearest-line pick, zoom sync), `components/CompareMap.tsx` (tracks, route
  reference, cursor markers), `lib/compare.ts` (types, grid interpolation, quartiles, colours, axis labels).
  All sessions are interpolated client-side onto one grid (the distance bins as they are, else ≤ 1500
  evenly spaced x); display smoothing: ±2 grid points in the aligned views, ~1/200 of the range in the time
  views (the engine's own smoothing is unchanged).
- **Fitness** (done): `routers/fitness.py` serves `data/<p>/fitness.json` (`scripts/fitness_model.py`); page `pages/FitnessPage.tsx` + `components/ShapeChart.tsx` (uPlot, markers drawn in a draw hook, own hit-testing), activity section `components/ShapeSection.tsx`, types in `lib/fitness.ts`, styles in `src/fitness.css`. Session one-liners and component meanings are generated server-side (`explain`, `COMPONENT_LABELS`).
- New routers: add a module in `app/server/routers/` with `router = APIRouter()` and list it in
  `main.py`. Types for the frontend go in `src/lib/api.ts`. Remove "soon" from the nav in `main.tsx`.

## Tests
`uv run pytest -q` runs API smoke tests (`tests/test_api.py`, FastAPI `TestClient` against the real data in
`data/adrian`; skipped when that folder is missing): every GET endpoint answers 200, plus invariants — Bislett 6×1000
same-workout group = 17, route R3 = 19 sessions, Compare › Intervals reps start at the same x (`axis.slot_x0`),
fitness.json loads. They read the data and caches only (no rebuilds).

## Known gaps
- Compare: hovering the map doesn't move the chart cursor (only highlights a session); the route-position
  markers use the first time each session reached the reference's time, so long stops show as a marker
  waiting; reversed sessions on a route keep their own direction (engine limitation); no per-session
  rep / leg detail beyond the matrices (open the activity for that); suggestion sets are capped at 8 (40
  for a route) and the picker caps the selection at 40.
- Find & group: place / region / route filters are single-select in the UI (the API takes `a|b`); garmin
  municipalities have no shape on the regions map (only their members' tracks when selected); the
  workout tree's open/closed state isn't in the URL (only the selected node `g`); results list every
  match (≤ 717 rows, ~0.8 MB uncompressed with polylines, gzip on).
- Closest activities replaced the old groups.json "Similar sessions" lists on the activity page; it is
  limited to the same sport (the API has `same_sport=0`, the UI doesn't expose it, nor custom weights).
- No auth, single person per server (`--person`), binds to 127.0.0.1 only.
- Frontend GET responses are memoised for the session; reload the page after new data arrives.
- The overview map draws all tracks as one GeoJSON layer (fine for ~1000 tracks; a vector-tile or
  pre-rendered heat layer would be needed for many more).
- Reps exist only for runs that `session_groups.py` classified as intervals and for ski_intervals sessions
  (`ski_intervals.py`: lap workouts and hard efforts such as hard Holmenkollen climbs); other skiing has pauses/legs only.
- Sleep and intraday HR exist only for days fetched with `fetch_daily.py`.

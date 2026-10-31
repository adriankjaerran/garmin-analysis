# Polish audit — 2026-10-31

Audit of every page of the app (`uv run garmin-app`, built `app/web/dist`) at a 1600 px wide desktop viewport.
Screenshots: `data/adrian/screenshots/2026-10-31/audit-*.png` (named `SS-xx` below = `audit-xx-*.png`).
No app code was changed.

## Browser recipe (cmux, desktop-sized viewport)

The narrow (~310 px) pane problem comes from opening the browser as a split next to a terminal. A browser in a
background workspace is also `document.visibilityState = "hidden"` and renders nothing (blank screenshot).
What works:

```sh
uv run garmin-app &                                   # http://127.0.0.1:8765
cmux workspace create --name garmin-audit --focus false      # -> workspace:N
cmux browser open http://127.0.0.1:8765/ --workspace workspace:N   # -> surface:S (placement=split)
cmux new-window                                       # -> window UUID W (it contains one fresh workspace:M)
cmux move-workspace-to-window --workspace workspace:N --window W
cmux workspace select workspace:N                     # page becomes "visible"
cmux workspace close workspace:M                      # drop the empty workspace in the new window
cmux tree --workspace workspace:N                     # find the terminal surface T next to the browser
cmux close-surface --surface surface:T --workspace workspace:N   # browser now fills the window
cmux browser surface:S viewport 1600 1000             # exact CSS viewport (emulated); taller for full pages, e.g. 1600 2400
cmux browser surface:S goto 'http://127.0.0.1:8765/fitness'
cmux browser surface:S screenshot --out data/adrian/screenshots/<date>/x.png   # 1600 px wide PNG
cmux browser surface:S errors list ; cmux browser surface:S console list        # JS errors / console
cmux browser surface:S eval 'JSON.stringify([innerWidth, innerHeight, document.visibilityState])'
# cleanup: pkill -f garmin-app ; cmux workspace close workspace:N   (closes the extra window too)
```
`viewport W H` works without the new window too, but only a visible (selected) workspace renders. Screenshots
at a tall viewport give the whole page in one image (Read downsizes > 2000 px; crop with PIL to inspect).

No JS errors or console warnings were logged on any page. API timings (warm, curl): every endpoint 1–45 ms
(`/api/search?sport=run` 21 ms / 520 kB, `/api/fitness` 44 ms / 305 kB, `/api/activities` 13 ms / 330 kB),
`/api/compare` with the 19 R3 sessions 0.13–0.9 s (1.4 MB). Performance is not an issue.

## P1 — broken or wrong

| # | Page | Issue | Evidence | Fix | Files |
|---|---|---|---|---|---|
| 1 | All | **Places and routes are named by start clusters** (`Oslo-1`, `Oslo-12`, `Vang-2`, `Malung-Sälen-1`): the Holmenkollen climb (R3, 19 sessions) shows as "Oslo-1 → Frognerseteren"; "Holmenkollen" as a place has 1 session; Vasaloppet start = "Malung-Sälen-1". The user's own vocabulary (Holmenkollen, Frognerparken, Marka, Bislett) is mostly missing in tables | SS-09, SS-17, SS-18, SS-23 | Name start clusters after the nearest named place / OSM locality (reverse geocode once, cache) e.g. "Majorstuen", "Vang (Bitihorn)"; keep `Oslo-1` only as id. Spell "Bislett" (map label says Bislett stadion) | `scripts/session_groups.py`, `scripts/similarity.py` (→ groups.json / index), WP-A |
| 2 | Places, Activity, Activities vs Find & group | **Two route-name sources disagree**: `/api/routes` + activity header say "R3 Oslo-1 → Frognerseteren 9.1 km", "R13 Oslo-10 out & back"; Find & group / compare suggestions say "R3 Holmenkollen climb: Oslo-1 → Frognerseteren via Holmenkollen", "R13 Oslo-19 out & back" (same id, different place!) | SS-18 vs SS-37; `curl /api/routes` vs `/api/groups?kind=route` | Use the similarity index name (better) everywhere: store.py joins it into activity rows / routes; R13 start place must come from one source | `app/server/store.py`, `app/server/routers/places.py`, `routers/activities.py` (WP-A) |
| 3 | Places, Find & group, Activities | **Session counts disagree for the same place**: Bislet = 107 (Places page, `/activities?q=Bislet`), 94 main / 94 touched (Find & group); "217 places" vs "218 places" | SS-19, SS-23 | One definition of place membership (groups.json `place` vs index `main_place`); show touched/main consistently | `app/server/store.py`, `routers/places.py`, `routers/search.py` (WP-A) |
| 4 ✅ | Fitness | **X axis has no years** — the year line under "Jun/Dec" is clipped, so a 2018–2026 chart reads "Jun Dec Jun Dec…" | SS-31 (below chart), SS-32 | Give the x axis enough `size` for two lines or label "Jun 24"/"2025" at year starts | `components/ShapeChart.tsx` (WP-B) <br>**WP-B:** `lib/chartAxes.ts` `dateAxis()` — calendar ticks (weeks / 1–6 months / years), January ticks read "2025", first tick carries the year when no January is visible; used by both fitness charts |
| 5 ✅ | Compare › Intervals with many reps | **Rep labels on the x axis overlap** into "Rep 1Rep 1Rep 2…" (R3, 6–14 reps × 19 sessions) | SS-25 | Thin out labels by available px per slot (skip, or show only "R1…"), hide `+k+j` slot labels when narrow | `components/CompareCharts.tsx` (WP-B) <br>**WP-B:** `categoryAxis()` thins rep/break labels by available px (primary "Rep k" / "Bk" first, "+k+j" and "1:00" only in free space), shortens to "R12" when cramped |
| 6 ✅ | Activity (swim) | Lap start "**-1:-6:-42**" (negative seconds through `dur()`); pool swim shows "8.0 km/h" from 1.68 km in a 12:40 "moving" time while laps add up to ~51 min; speed chart empty; swim pace should be /100 m | SS-16 | `dur()` handle negatives (`−1:06:42` or clamp); for swims use lap times for moving time and show pace /100 m; hide empty speed chart | `lib/format.ts` (WP-B); swim moving time `app/server/store.py`/`fitcache.py` (WP-A); swim pace display `pages/ActivityPage.tsx` (WP-C) <br>**WP-B:** part: `dur()` / `hm()` handle negatives ("−5:42"); new `sdur()` (signed) and `pace100()` (/100 m) in `lib/format.ts`; SeriesCharts hides near-empty panels (< 10 samples or < 5 %), `drawablePanels()` exported. Open: swim moving time (A), swim pace display + no-chart layout (C) |
| 7 | Places › route | Stale text "Leg-by-leg comparison: **Compare (soon)** and the report's route view" and **no Compare button** on a route — the main entry to "compare all 19 Holmenkollen climbs" is missing | SS-18 | Replace with a "Compare all n" button → `/compare?ids=<members>&mode=distance` (≤ 40), plus "List in Find & group" → `/search?route=R3` | `pages/PlacesPage.tsx` (WP-C) |
| 8 | Day | Intraday HR draws a **straight line across missing data** (00:00–07:40) | SS-06 | Insert `null` where consecutive samples are > 2× the step apart before passing to SeriesCharts | `pages/DayPage.tsx` (WP-C) |
| 9 ✅ | Find & group, Compare tables | **Tables clipped on the right** without visible horizontal scroll: "Cli(mb)", "Last" column "2026-1", Compare sessions table "HR dro…", "Break me…" | SS-21, SS-36, SS-25, SS-28 | Let tables scroll horizontally with a visible affordance, or drop/condense columns (Compare: move Δ columns into tooltips; Find: narrower Workout col) | `pages/SearchPage.tsx`, `pages/ComparePage.tsx`, `pages/compare.css` (WP-D); `components/Table.tsx` if done generically (WP-B) <br>**WP-B:** generic: `Table` detects horizontal overflow → always-visible thin scrollbars, right/left edge shadows, sticky leading columns (`stickyCols`, default 1; Search should pass `stickyCols={2}` for checkbox + date) — `components/table.css`. Column condensing left to D |

## P2 — confusing or inconsistent

| # | Page | Issue | Evidence | Fix | Files |
|---|---|---|---|---|---|
| 10 ✅ | Activity, Compare | **Pace axis dominated by breaks**: activity 6×1000 axis 3:20–10:00 squashes reps flat; compare 5-vs-6 goes to 13:20 even though docs say "pace range from the reps only" | SS-12, SS-15, SS-28 | Pace range from rep samples (or moving samples > some speed) when reps exist; clamp to e.g. [p2, p98] of rep pace ± 30 s | `components/SeriesCharts.tsx`, `components/CompareCharts.tsx` (WP-B) <br>**WP-B:** `paceRange()` / `speedRange()` = robust range of moving samples (speed ≥ 0.6 × median, p2–p92 for pace). Activity with reps: fast end from reps (p2 − 20 s), slow end ≤ rep p98 + 2:00; Compare uses `rangeMask` + moving filter; CPanel `axis` option |
| 11 ✅ | Activity, Compare, Fitness | **Odd tick steps** for time axes: 3:20 / 6:40 / 10:00, gap +3:20 / +6:40, pace 3:20 / 4:10 / 5:00 | SS-12, SS-24, SS-26 | Custom `splits` for m:ss axes at 10/15/30 s, 1/2/5 min steps | `components/SeriesCharts.tsx`, `CompareCharts.tsx` (WP-B) <br>**WP-B:** `PACE_INCRS` (0:05…5:00) on pace axes, `TIME_INCRS` on m:ss y axes (Compare gap) and SeriesCharts x (also whole hours on the Day HR chart); activity x labels "10 min" below 1 h, "h:mm" for all ticks beyond |
| 12 | Activity vs Compare vs Shape | **Same session, different numbers**: avg HR 143 (header) vs 156 (Compare sessions table, moving-only?); rest "2:40" (reps, Shape) vs "rest ~2:30" (Closest, Find & group workout label); rep pace 3:29 (Shape, mean) vs 3:27 (Compare); climb 438 vs 428 | SS-12, SS-26, SS-13 vs SS-24 | Label what differs ("HR (moving)", "rest bin ~2:30") or use the same value; prefer showing the measured median rest in labels | `pages/ComparePage.tsx` (WP-D), `components/NearestPanel.tsx` (WP-C) |
| 13 | Activity | **Three overlapping segmentations** (Reps, Laps, Legs & pauses) with conflicting breaks (Break after 2:40 vs pause 1:33…) and noisy legs (0.01–0.04 km legs, 28 legs on a hike); developer refs "(run_analysis.py)", "(session_groups.py)", "docs/fitness-model.md" in UI | SS-12, SS-14 | Show Reps (intervals) or Legs (route) by default, Laps collapsed; merge tiny legs (< 100 m) into pauses; drop script names from captions | `pages/ActivityPage.tsx`, `components/ShapeSection.tsx` (WP-C) |
| 14 | Activity (no GPS) | Big grey "No GPS for this activity" box, "Colour by Pace/HR/Elevation" toggle and "hover a row to see it on the map" still shown; Closest map shows "No GPS tracks" over Oslo | SS-15, SS-16 | When `!track`: no map box, charts full width, hide colour toggle; Closest: hide its map when none of the rows has a track | `pages/ActivityPage.tsx`, `components/NearestPanel.tsx` (WP-C) |
| 15 | Activity (treadmill) | "Not scored by the fitness model (only interval, race, run and ski sessions …)" on a 6×3:45 **interval** session — reads as a contradiction | SS-15 | Say why: "treadmill / indoor: pace from the foot pod is not trusted" | `components/ShapeSection.tsx` (WP-C) or explain text in `app/server/routers/fitness.py` (WP-A) |
| 16 | Closest | No **time / rep pace column** — the thing you'd compare (same route → moving time; same workout → rep pace); "Why" wraps to 2 lines; hike "Why" in min/km while hikes are km/h elsewhere; raw labels "Lap_swimming", "Multi_sport 10–30 km" | SS-12, SS-13, SS-14, SS-16, SS-20 | Add "Time" (route/place) or "Rep pace" (intervals) column; one-line Why with tooltip; humanise family labels server-side | `components/NearestPanel.tsx` (WP-C); labels `scripts/similarity.py` (WP-A) |
| 17 | Find & group | Default map **fits the whole world** (Brisbane, LA…) → tiny blob on a white Kartverket background; Places groups map same | SS-20, SS-23 | Fit to the densest cluster (e.g. bbox of the 80 % most central tracks) or home region; list "n abroad" | `components/GroupsMap.tsx`, `pages/SearchPage.tsx` (WP-D) |
| 18 | Find & group | **Interval results lack rep pace** (Pace column = whole-session avg 4:52/km for a 3:27/km 6×1000) | SS-21 | Add "Rep pace" column when intervals are in the result (API has rep_time_s / rep_dist_m) | `pages/SearchPage.tsx` (WP-D) |
| 19 | All maps | Kartverket topo/grey are **blank outside Norway** (Vasaloppet day, abroad hikes) — the default basemap shows an empty map | SS-04, SS-14 (closest map) | MapView: if the fitted bbox is outside Norway, fall back to OSM for that map (without overwriting the stored preference) | `components/MapView.tsx` (WP-C) |
| 20 | Navigation | Sidebar groups with a single item ("Activities › List", "Places › Places & routes"); a transient "Activity" entry; **Activities list and Find & group overlap** (two lists with different columns, filters, counts) | SS-09, SS-20 | Group as Explore (Map, Day, Places) / Activities (List, Find & group, Compare) / Fitness; or make Activities list = Find & group without filters expanded | `main.tsx`, `styles.css` (WP-C) |
| 21 | Terminology | "Activity" (Day, Activities, Find) vs "Session" (Places, Compare, Fitness, Closest), "Type" column shows classification ("intervals track · 6×1000", "nogps") vs "Workout" column elsewhere; sport chip order differs (Activities: Run Ski Hike Bike Swim Other; Find: Run Ski Hike Other Swim Bike) | SS-07, SS-09, SS-20 | Pick "activity" for rows and "session" only inside Compare; rename "Type" → "Workout"; map `nogps` → "run (no GPS)"; use `SPORTS` order everywhere | `pages/DayPage.tsx`, `ActivitiesPage.tsx` (WP-C), `pages/SearchPage.tsx` (WP-D) |
| 22 | Places | Place panel "Activities" links to `/activities?q=Bislet` (free text) instead of the place filter; place page has no activity list; "Area A1" / "Bislet area A1" is meaningless to the user; place circles have no labels; "Last" column truncated | SS-17, SS-19, SS-12 header | Link to `/search?place=Bislet`; list the place's sessions under its routes; hide area ids (or show the region name, e.g. "Marka"); label top places on the map | `pages/PlacesPage.tsx`, `pages/ActivityPage.tsx` (WP-C) |
| 23 | Fitness | Discipline colours reuse sport colours with another meaning (Long distance = green = Hike, Interval = blue = Run) | SS-31 | Own palette for disciplines (e.g. purple / teal / red) defined as CSS tokens | `src/fitness.css`, `lib/fitness.ts` (WP-D) |
| 24 | Day (empty) | "No activities this day" printed over the map's place labels (unreadable); empty day map always shows Oslo even when the user was travelling | SS-06, SS-08 | Put the note in a chip like other map overlays; centre on the previous active day's tracks | `pages/DayPage.tsx` (WP-C) |
| 25 | Compare | Ski "Rep pace 4:29" in min/km while the metric toggle is Speed; breaks mode silently switches runs to km/h; rep slot headers "+0+1, +2+3" are cryptic | SS-25, SS-27 | Format rep columns by the active metric; tooltip/legend for "+k+j" = "extra rep after rep k" | `pages/ComparePage.tsx` (WP-D) |

## P3 — nice to have

| # | Page | Issue / idea | Evidence | Files |
|---|---|---|---|---|
| 26 | Home | `/` → `/map`. The map is a good overview, but the user's daily question is "what did I do / where was I": consider `/day` (latest active day) as home, or add a recent-activities strip to the Map page. **Decision for the user** | SS-01, SS-03 | `main.tsx` (WP-C) |
| 27 | Day | Calendar is small (≈ 750 px of 1350) — make cells scale to the width; HR chart clips peaks at the 99th percentile (looks cut off); x ticks at 02:46 / 05:33 instead of whole hours | SS-03, SS-06 | `components/Calendar.tsx`, `pages/DayPage.tsx` (WP-C) <br>**WP-B:** x ticks now whole hours, HR y range up to the max instead of p99 — calendar width still C |
| 28 | Compare | Map column is short with empty space below; make it sticky while scrolling the tables; empty `/compare` list has no search box | SS-24, SS-29 | `pages/ComparePage.tsx`, `compare.css` (WP-D) |
| 29 | Compare | Cross-sport sets allowed (pool swim vs 6×1000) — warn when sports differ | SS-30 | `pages/ComparePage.tsx` (WP-D) |
| 30 | Activities list | Rows 32 px (less dense than "dense"); no route / rep pace columns; side panel lacks Compare / Closest links | SS-09, SS-10 | `pages/ActivitiesPage.tsx` (WP-C) |
| 31 | Maps | Colourful Kartverket topo competes with coloured tracks on track-heavy maps (Places, Compare, Find); Map page already defaults to Grey | SS-17, SS-21 | per-page `defaultBasemap="grey"` (owner of each page) |
| 32 ✅ | Fitness | Overall % chart starts with an artificial dip (first week); main chart y range pulled down to 30 by lower bounds — consider clamping to shape band ± margin with bounds clipped | SS-31 | `components/ShapeChart.tsx` (WP-B) <br>**WP-B:** overall % starts once every discipline has data (no warm-up dip); hard/race evidence widens the VDOT range at most 3 beyond the bands (was 6 below) |
| 33 | Find & group | Date inputs show today's date as grey placeholder (looks like a set filter); "Bislet (94)" vs touched counts unexplained | SS-20, SS-21 | `pages/SearchPage.tsx` (WP-D) |
| 34 | Activity | Ski climbs (R3) have no reps; the Holmenkollen "intervals" in Compare come from pauses — a short note "reps = segments between stops" on ski compares would help | SS-25 | `pages/ComparePage.tsx` (WP-D) |

## Parallel work packages (disjoint files)

| WP | Scope | Issues | Files (only these) |
|---|---|---|---|
| **A — data & naming (backend/pipeline)** | Human place names, one route-name source, consistent place membership counts, swim moving time, humanised family labels, treadmill not-scored reason | 1, 2, 3, 6 (swim moving time), 15 (server text), 16 (labels) | `scripts/session_groups.py`, `scripts/similarity.py`, `app/server/store.py`, `app/server/fitcache.py`, `app/server/routers/*.py` (then rerun `session_groups.py`, cache) |
| **B — charts & formatting** | Fitness year axis, compare rep-label overlap, pace ranges from reps, nice m:ss ticks, negative durations, generic table h-scroll, fitness y-range | 4, 5, 6 (`dur`), 9 (generic Table), 10, 11, 32 | `components/ShapeChart.tsx`, `components/CompareCharts.tsx`, `components/SeriesCharts.tsx`, `components/Table.tsx`, `lib/format.ts` |
| **C — Activity / Day / Places / Activities / shell** | Route Compare button & stale text, day HR gaps, no-GPS layout, segmentation tables, closest panel columns, basemap fallback abroad, nav + terminology, place panel links, empty-day note, calendar width, home decision | 6 (swim pace display), 7, 8, 12 (closest rest), 13, 14, 15, 16, 19, 20, 21 (Day/Activities), 22, 24, 26, 27, 30 | `pages/ActivityPage.tsx`, `pages/DayPage.tsx`, `pages/PlacesPage.tsx`, `pages/ActivitiesPage.tsx`, `components/NearestPanel.tsx`, `components/ShapeSection.tsx`, `components/MapView.tsx`, `components/Calendar.tsx`, `components/TracksMap.tsx`, `main.tsx`, `styles.css` |
| **D — Find & group / Compare / Fitness page** | Table clipping, world-fit map, rep pace column, consistent HR/rest labels, metric-aware rep columns, chip order, fitness discipline colours, sticky map, cross-sport warning | 9, 12 (compare), 17, 18, 21 (Search), 23, 25, 28, 29, 33, 34 | `pages/SearchPage.tsx`, `components/GroupsMap.tsx`, `lib/search.ts`, `pages/ComparePage.tsx`, `pages/compare.css`, `lib/compare.ts`, `pages/FitnessPage.tsx`, `src/fitness.css`, `lib/fitness.ts` |

Notes: B owns `lib/format.ts` — C/D should only call new helpers after B lands (or add local helpers).
A changes data that C/D display; C/D need no API changes for their items except #16 (Closest time/rep pace
columns: `/api/nearest` rows already carry `moving_s`, `rep_time_s`, `rep_dist_m`). Rebuild `app/web/dist`
once at the end (`npm run build`) to avoid conflicting builds.

## Open questions for the user
- Home page: keep `/map`, or start on the latest day (`/day`) — or a combined "today" page?
- Merge the Activities list into Find & group (one list page), or keep both?
- Place naming: OK to reverse-geocode cluster names with OSM/Kartverket place names (one-off network call), or hand-maintain a `named_places` list (Majorstuen, Frognerparken, Holmenkollen…)?

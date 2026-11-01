# Compare: other improvements for runs and intervals (not GPS-start alignment, not rep-start alignment)

Written 2026-10-31. Based on `scripts/compare.py`, `scripts/session_groups.py` (`project`, `to_grid`, `find_pauses`),
`app/server/routers/compare.py`, `app/web/src/pages/ComparePage.tsx` + `components/Compare*.tsx`, the data in
`data/adrian` and `data/patrik`, and live calls to `/api/<person>/compare`. No repo files were changed.
The probe scripts are in `/tmp/compare-plan/*.py` (scan2.py, decim.py, decim_reps.py, corun.py).

## What I measured (evidence behind the ranking)

1. **The ghost race is wrong on about 15 % of route sessions.** `scan2.py` runs every non-laps route with at least 3
   members in distance mode and flags sessions with a ≥ 300 m stretch where the implied speed is more than
   max(2 × the session's moving speed, 25 km/h):
   - **adrian: 24 of 161 sessions. patrik: 4 of 19.**
   - R30 2026-05-22: 2160 km/h stretch, finish gap −2985 s on a 13.9 km loop.
   - R32 2026-06-16: −1414 s.
   - R4 2020-04-07 (a reversed loop, `dir rev`): progress jumps from 6 m to 5866 m after a 5-min off-route spell,
     then the session "gains" 1855 s.
   - patrik R2 2023-10-29: only 0–1100 m covered, then a jump.

   **Cause.** `session_groups.project` widens its search window to `40 + 15 m/s × seconds since the last on-route fix`.
   After a long off-route spell (detour, shortcut, a wrong start, the turn of an out & back) it can snap kilometres
   ahead. Progress is a running max, so the jump sticks. The bins it skipped add ~0 s, which shows up as time gained.
   There is no coverage or plausibility guard, and the finish gap / leg rank use these values.
2. **Sampling bias (patrik 5 s vs adrian 1 s).** `decim.py` decimates adrian's FIT records to one every 5 s and
   recomputes:
   - **Interval sessions with standing breaks:** 6×1000 at Bislet: moving time 2443 → 2520 s, moving pace
     292 → 301 s/km (+3 %), pauses 16 → 15. The 1 Hz linear interpolation spreads every stop/start over the 5 s gap,
     so standing seconds count as moving.
   - **Continuous runs:** < 1 s/km difference. Best-400 m differs by 0.1–0.2 s.
   - **Rep detector (`decim_reps.py`):** 1000 m reps at ±1.4 s, 400 m reps at ±1 s. 45 s hill reps are off by up
     to 5 s (≈ 10 %). The first and last reps of a session can change completely (26 → 22 s, 227 → 93 s).
   - **GAP:** on the flat Bislet track, patrik's GAP differs from his pace by −7 … +3 s/km (GPS-only altitude
     noise through Minetti). Adrian's differs by 0–0.3 s/km.

   The Compare UI shows none of this (no `sample_s` per session).
3. **The two people often train together.** 24 activities overlap in time by more than 5 min:
   - 6 Bislet Tuesday interval sessions (2025-11-11, 2026-10-13/15/20/27, …), easy runs (2021-01-23 R24, 2024-04
     R27), a race (2023-05-07, 10.09 vs 10.05 km, starts 1 s apart, 8 m apart), and a hiking trip.
   - They don't always run the same workout. On 2026-10-27 adrian ran 6×1000 while patrik ran continuous at
     ~4:35/km, and his detector output is "5 reps mixed 3:00–21:42".
   - On 2026-10-20: adrian 15×400 vs patrik 10×400.

   Today `/api/adrian/compare?ids=<adrian>,<patrik>` returns `skipped: no FIT file for <patrik id>`.
4. **Interval compare across different workouts gives misleading deltas.** 6×1000 vs 15×400 vs 6×800+2×400 falls
   back to index pairing: 15 slots, and `d_rep_pace_matched_s_km −14.7` compares 400s with 1000s. (The pairing
   itself belongs to the rep-alignment work. A warning or a normaliser is still needed.)
5. **Fields that exist:**
   - **Adrian's current watch (product 4315):** power, cadence, step_length, stance_time, vertical_oscillation /
     ratio. No temperature.
   - **Adrian's fr935 era (2019–~2023):** wrist `temperature` (body-biased), session avg/max temperature.
   - **patrik (product 3077):** HR, cadence, GPS-only `enhanced_altitude`. No power, dynamics or temperature.
   - **Both:** `profile/heart_rate_zones.json` (max HR 206 / 207). Patrik's looks like an age default.
   - **Both:** `fitness.json` with per-session `score` (VDOT-like), `components.hrr`, `hr_diagnostics`
     (decoupling).
   - No weather anywhere.
6. **The UI ignores some engine output.** The distance series has `gap_kmh`, but the UI never plots it. Map hover
   doesn't move the chart cursor. The chart zoom isn't in the URL. There's no export.

---

## Prioritised ideas

### P1-1 Ghost-race integrity: coverage and jump guard (fix)
- **What.** A route stretch is only "covered" if the session actually ran it.
  - In `_distance_mode`, mark bins as uncovered where progress jumps: implied speed over the bin run is greater
    than max(2.5 × own moving speed, 25 km/h run / 60 km/h ski), or progress advanced while `on` was false for
    more than N s.
  - Don't accumulate `delta_*` across uncovered bins. Hold the gap and restart accumulation, the same way
    late-joining sessions already work.
  - Emit `covered` (bool per x, or a list of runs) and `coverage_pct` per session.
  - Replace "gap at the finish" with "gap at the last common point" plus coverage.
  - Mark legs `partial` when they are not covered.
  - In `project`, cap the `ahead` window, for example at 400 m + 6 m/s × gap. If the session re-acquires further
    ahead, accept it as a jump that the caller can detect, instead of silently taking the running max.
  - Reversed sessions on loops / out & back: either project them onto the reversed reference as their own
    direction group, or (simplest) skip them with `why: "reversed direction"`, or show them on the own-distance
    axis.
- **Why.** Today a detour or shortcut makes a session look 5–50 min faster. The finish-gap column and leg ranks
  can't be trusted on about 1 in 7 sessions. This undermines every route comparison.
- **Feasibility.** All data is there. The `scan2.py` scan doubles as a validation check for
  `compare.py validate`.
- **Where.** `scripts/session_groups.py: project` (this also affects `legs_table`, so re-run session_groups), and
  `scripts/compare.py: _distance_mode` (delta accumulation, legs) and `progress`. UI: fade uncovered stretches,
  show a coverage % column.
- **Coordinate with** the GPS-start alignment agent. A forgotten watch start shows the same symptom: a late join.
- **Effort.** M (S for the guard in compare.py alone).

### P1-2 Stretch compare from the chart selection, plus "ghost of my best"
- **What.**
  - (a) Drag-zoom on Route position (already zooms every chart) also opens a **"This stretch"** table. For x0..x1
    per session: moving and elapsed time, pace, GAP, HR avg/max, climb, rank, and Δ vs the reference. Add an
    option to **rebase the gap to 0 at x0**, so the gap panel shows who gained on this stretch only.
  - Put `x0,x1` in the URL so the view can be shared.
  - (b) `reference=best`: the reference is the fastest session (moving time) among those with ≥ 95 % coverage. It
    becomes the first-class "race my PR" button. Add `reference=previous` too (the last session before the newest).
- **Why.** This answers the most common question: "Was I faster up the hill / on the last km than last time?"
  Today you have to read the gap curve by eye.
- **Feasibility.** (a) can be done entirely in the frontend: the distance series already has `moving_s`,
  `elapsed_s`, `hr` and `gap_kmh` per x bin. (b) is a few lines in `compare()` reference resolution. With
  P1-1 coverage it can pick only full sessions.
- **Where.** `ComparePage.tsx` (zoom callback from `CompareCharts.tsx`, new table), `lib/compare.ts`, and
  `compare.py: compare()` (`reference` values) plus the router's `reference` pattern.
- **Effort.** S–M.

### P1-3 Cross-person compare (same session, same route, same workout)
- **What.** Compare sessions from several people in one view.
  - **Ids.** Garmin activity ids are globally unique. Build a global `id → person` map from each
    `data/*/activities_index.json`, or accept `person:id` tokens.
  - **Engine.** `compare.compare` gets the store per id (`store(person_of[id])`). Route context, waypoints and legs
    come from the reference's person. Other people's sessions are projected onto that polyline: `sg.project` works
    on any grid. Reps and projections stay cached in each person's own `compare.pkl` (keyed by FIT). The
    projection cache key must include the reference person (route ids like `R1` are person-local: adrian R1 =
    Bislett laps, patrik R1 = Oslo out & back).
  - **Router.** Resolve persons, put the persons in the memo key, and build `activities` / `tracks` from each
    person's Store.
  - **UI.** Person badge or initials on the chips, links to `/<person>/activities/<id>`, and paste accepts other
    people's URLs.
  - **HR.** Raw HR isn't comparable between people. Add %HRmax or %HRR per session (per-person max/rest: profile
    zones or observed max).
  - **"Ran together" suggestion set.** Same time window (> 50 % overlap, start within 200 m). It was found 24 times
    with a 10-line scan.
  - **Optional "wall clock" x axis** for co-runs: x = clock time, plus the distance apart. This differs from
    GPS-start alignment because both started their watches at different moments by design.
- **Why.** The data shows they run the Tuesday Bislet sessions and races together. "Who held pace better in the
  same session" is the natural question now that both people are in one app.
- **Feasibility.** Engine changes are moderate. Similarity and suggestions are per person: cross-person "same route"
  needs geographic route matching (route reference polylines, e.g. patrik R8 "St. Hanshaugen laps" = Bislett). Do
  co-runs first, routes later.
- **Where.** `scripts/compare.py` (`compare`, `_Store` usage, `_common_route`), `app/server/routers/compare.py`
  (person resolution, memo, tracks, activities), `app/server/deps.py` (store per person),
  `routers/compare.py: suggest` (co-run set), and `ComparePage.tsx` / `lib/api.ts` (person-less URLs need a
  cross-person form).
- **Effort.** M for co-runs and explicit ids. L including cross-person route matching and suggestions.
- **Needs a decision** on the URL scheme (see questions).

### P1-4 Fair treatment of coarse sampling (patrik's 5 s)
- **What.**
  - (a) **Gap-aware pauses.** In `find_pauses` / `to_grid`, when two raw samples are more than 2 s apart and the
    distance hardly changed, mark the in-between grid seconds as standing instead of interpolating a slow jog.
    Alternatively, detect pauses on raw samples and map them to the grid.
  - (b) Expose `sample_s` (median record interval) per session in `sessions[]`. Show a ⚠ when the sampling is
    mixed, and widen the rep-time uncertainty (± half the interval) in the reps table.
  - (c) **GAP robustness.** On a route axis, take the grade from the route reference altitude (`route_ref.alt`,
    which is the same for everyone). At laps / track places, use grade = 0. Otherwise compute the grade over
    ≥ 100 m of distance, not ±30 s.
- **Why.** Patrik's moving pace in an interval session is ~3 % slower purely from sampling. His GAP on the flat
  track is off by up to 7 s/km. Short-rep comparisons carry ±5 s errors. Without these fixes, cross-person
  comparisons (P1-3) are unfair by default.
- **Where.** `scripts/session_groups.py: to_grid / find_pauses` (affects groups.json pause_s, so re-run it),
  `scripts/compare.py: _grade_per_s, _summary`, and the `run_analysis.detect` uncertainty. Validate with the
  `decim*.py` approach on adrian's files.
- **Effort.** S–M.

### P1-5 Normalisers that work across different workouts and people: score, %HRR, decoupling
- **What.** Add columns to the sessions table:
  - the fitness-model **score** (VDOT-like, "≈ 5k 19:17") from `fitness.json`
  - **effort %HRR** (`components.hrr`)
  - **aerobic decoupling %** and EF first vs second half (`fitness_model.hr_aerobic` already computes this;
    `hr_diagnostics` stores it)
  - **HR drift** per session: HR at constant GAP, last third vs first third of the steady part
- **Use.** When `rep_summary.match == "index"` and the rep lengths differ by more than 20 %, hide or grey the
  per-rep Δ and show the score Δ as "equivalent effort".
- **Why.** These make 6×1000 vs 15×400 (or adrian vs patrik) comparable on one number. Decoupling and drift answer
  "was this easy run easier than last month at the same pace".
- **Feasibility.** The data exists for both people. Patrik's hrr values look high (0.95 on an easy run) because
  his rest/max HR defaults are probably wrong, so per-person HR settings matter (see questions).
- **Where.** `app/server/routers/compare.py` (join fitness.json by id, as the Fitness router does) or `compare.py:
  _summary` (drift / decoupling from the grid). UI: sessions table in `ComparePage.tsx`.
- **Effort.** S.

### P2-1 Route history: trend, PR progression, small multiples
- **What.** On Route position with ≥ 5 sessions, a "History" panel:
  - moving time, elapsed time, GAP, HR, m/beat and score vs date, with PR steps marked
  - leg times vs date (one tiny line per leg: "which part got faster")
  - optional "best leg composite" (sum of best legs = the theoretical best)
- **Why.** "Am I getting faster on Holmenkollen / Brekke loop over the year" is a trend question. A 19-line
  overlay can't answer it.
- **Feasibility.** `groups.json routes[R].legs_table.sessions` already has per-member elapsed/moving/pause and leg
  times. The engine's legs block has the rest. Needs P1-1 so partial sessions don't fake PRs.
- **Where.** Frontend panel in `ComparePage.tsx` (or the Places route view). There's no new engine work beyond
  coverage.
- **Effort.** M.

### P2-2 Auto-detected common segments and finish alignment for different routes
- **What.** For any two (or N) tracks:
  - Project B onto A's own polyline (`sg.ref_polyline(G_A)` works on any session) in both directions, and find
    monotone on-route runs ≥ 500 m. Return `segments [{a_from,a_to,b_from,b_to,len_m}]`.
  - Then run the ghost race on the shared section only (axis = metres along the shared section), or with
    **finish alignment**: x = metres to the end of the shared section, so "who was closer to the line".
  - In the UI: "Shared stretches" list, and clicking one = a stretch compare (P1-2) on it.
- **Why.** Most runs aren't on an identical route (adrian has ~60 run routes, many with 2–4 members). Bislett
  loops R11 / R30 / R59 share long parts. This unlocks comparisons beyond "same route".
- **Feasibility.** The projection code exists. The work is the segment finder and an axis variant. Keep the
  GPS-start alignment agent in sync: a common start is the special case "segment starts at 0".
- **Where.** New `compare.py` helper (`common_segments(G_a, G_b)`), new axis kind `segment` in `_distance_mode`,
  router param `segment=a_from-a_to`, UI list.
- **Effort.** L.

### P2-3 Best efforts and a stretch leaderboard
- **What.**
  - Per session: fastest 400 m / 1 km / 1 mi / 5 km / 10 km by own distance (rolling on the 1 Hz grid, sampling
    aware), plus best 20-min GAP.
  - Per route / segment: leaderboard of all members' times over a stretch (P1-2's stretch, but over every member,
    not just the selected set).
  - "My best 5 km ever" vs this run.
- **Why.** PRs and "best on this stretch" are what people look for. They're also a good way to pick comparison
  sets automatically.
- **Feasibility.** Cheap per session (≈ 10 ms per grid). Leaderboards need cached projections, which already
  exist for route members.
- **Where.** `compare.py` (or a small `best_efforts.py`) plus the cache in `compare.pkl`, a new endpoint
  `/api/<p>/compare/stretch?route=&from=&to=`, and an activity-page widget.
- **Effort.** M.

### P2-4 Charts for pace at HR (efficiency curves) and GAP
- **What.**
  - Toggle the pace panel between actual and **GAP** (`gap_kmh` is already in the distance series, unused).
  - Add an **HR vs GAP-speed scatter** per session (steady 1-min chunks), with a fitted line. A line shifting right
    means a fitter session.
  - Add **HR drift** (HR minus first-third mean at the same speed) as a chart option in moving mode.
- **Why.** On hilly routes, and for easy-run comparisons, pace alone misleads. Efficiency at HR is the cleanest
  fitness comparison between two easy runs, and across people when paired with %HRR.
- **Where.** `ComparePage.tsx` panels, a new uPlot scatter component, and `compare.py: _time_mode` (emit `gap_kmh`
  there too).
- **Effort.** S–M.

### P2-5 Power, cadence and running dynamics in Compare
- **What.**
  - Per point: power, cadence, step length.
  - Per rep: avg power, cadence, step length, GCT, plus "power at pace" and step length × cadence breakdown.
  - Chart panels are shown only when ≥ 1 session has the field.
- **Why.** For intervals, "same pace at lower power / longer stride" shows progress or fatigue form. Adrian has
  these on all recent sessions.
- **Feasibility.** The fields exist on product 4315 files only. They're missing for the fr935 era and for patrik
  (show null). `gps_quality._read_fast` / the app's Store.series already read cadence and power. The compare grid
  (`sg.read_series`) does not.
- **Where.** `session_groups.read_series/to_grid` (optional fields), `compare.py` series and rep rows, and the UI
  panels and reps-table metric `rm`.
- **Effort.** M.

### P2-6 Map ↔ chart sync and ghost playback
- **What.**
  - Hovering or clicking the map track moves the chart cursor: nearest x of that session, then the reference x.
    This is listed in the app.md known gaps.
  - A ▶ button animates the cursor over x (×10 / ×30 / ×60) so the markers race. In Route position, show
    distance-apart labels at the markers.
- **Why.** It makes the ghost race obvious, especially for co-runs and for showing someone else. The marker
  positions per x already exist (`posAt`, `tracks`).
- **Where.** `CompareMap.tsx` (mousemove → onCursor), and `CompareCharts.tsx` (setCursor API, rAF loop).
- **Effort.** S–M.

### P3-1 Export
- **What.** CSV download for the sessions, legs, reps and breaks tables, and the per-x series. PNG of the chart
  stack (uPlot canvases). The URL already shares the state. With P1-2 also add `x0,x1`.
- **Where.** `Table.tsx` (generic CSV button) and `ComparePage.tsx`.
- **Effort.** S.

### P3-2 Conditions: temperature and weather
- **What.** Air temperature, wind and rain at the start time and place, as a sessions column, and as a
  "same conditions" filter for suggestions.
- **Feasibility.** Weak from the files: wrist temperature only on fr935-era files (body-heat biased), and none on
  the current watch or patrik's. It would need an external historical weather source (e.g. MET Norway Frost needs
  a client id; Open-Meteo archive), a network call per activity and a local cache. That is a privacy/network
  decision for the user.
- **Effort.** M.

### P3-3 Bislet track geometry for lap splits
- **What.** In distance mode at Bislet (laps route), use watch laps (track mode: auto 400 m laps with
  `sub_sport track`) or snap to the track geometry, instead of own-distance 400 m steps (a listed limitation).
- **Why.** Splits are GPS-precision only. Patrik doesn't use track mode, so this matters for cross-person track
  sessions.
- **Effort.** M.

## Dropped or left to others
- Rep-start alignment, N vs N+1 skipped-rep ambiguity, and median-anchor rep matching: the rep-alignment agent.
- Common GPS start / forgotten watch start: the GPS-start agent. P1-1 and P2-2 touch the same projection code, so
  coordinate.
- Classic vs skate and ski GAP: not about runs.

## Open questions for the user
1. **Cross-person URL scheme.** `/compare?ids=adrian:123,patrik:456` (explicit, robust), or plain ids resolved
   through a global id → person map (shorter, needs every person's index loaded)? Also: under which person's URL
   prefix does a mixed comparison live?
2. **Patrik's HR settings.** His zones look like age defaults and his easy-run %HRR is 0.95. Is there a known max
   / resting HR for him? Without one, cross-person HR normalisation will be off.
3. **Reversed loop / out & back sessions on a route.** Exclude them from the ghost race, give them their own
   reversed-direction group, or show them on own distance?
4. **Weather.** OK to call an external weather API (with a local cache) for conditions, or keep the app offline?
5. **Pause fix.** It changes groups.json `pause_s` / `moving_s` for coarse-sampled sessions. Is it fine to shift
   moving times (they'll differ from Garmin's moving time)?

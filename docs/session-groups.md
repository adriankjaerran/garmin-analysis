# Session groups, legs and route comparison

`uv run scripts/session_groups.py adrian [--show [ROUTE_ID ...]]` → `data/adrian/groups.json`
(~5 s; first run ~15 s while the rep detector of every run is cached in
`data/adrian/cache/session_groups.pkl`, keyed by FIT mtime+size). `--show` prints the leg table of a
route (default: the largest ski route, i.e. the Holmenkollen rollerski climb).

## Grouping
- **Places**: first GPS fix of each session, greedy density clustering (densest point + everything
  within 400 m, repeat). Name = known spot within 600 m (Bislet, Sognsvann, Frognerseteren,
  Holmenkollen, Granåsen, NTNU Gløshaugen; only the cluster nearest to the spot gets the name),
  else `<Garmin locationName>-<rank by size>`; the name is also the place id. Where an activity
  mostly happens (named places / regions by time share) is in [similarity.md](similarity.md). Overrides: `data/adrian/places.json` `{"Oslo-1": "Majorstuen"}` or
  `{"59.933,10.719": "name"}` (any place whose centre is within 400 m).
- **Areas**: single-link chaining of start points at 2 km.
- **Routes**: track on a 75 m grid; coverage(A|B) = share of A's cells within one cell of B's;
  similarity = min of both coverages; same sport family, distance ratio ≤ 1.3; average-linkage merge
  while ≥ 0.6. Routes with ≥ 2 sessions get ids `R1..` (by size). Shape = laps / out_and_back /
  point_to_point / loop_cw / loop_ccw (retrace share + start–end distance); `route_dir` = fwd/rev
  vs the reference (cw/ccw for loops).
- **Session types** (rules): runs → intervals_track/road/treadmill (rep detector, manual laps),
  tempo, long, easy, race? (fast + standard distance), run_nogps, run_other; tags hilly/strides in
  `type_detail`. Ski → ski_easy / ski_hard (≥ 25 % time in Z4–5) / ski_long (≥ 150 min or ≥ 35 km);
  `surface` guess roller? (May–Oct) / snow — classic vs skate isn't in the files (all sport 12).
  No-GPS distances are `dist_trusted: false`. Intervals get `sig` ("6×1000", "10×4:00",
  "3×1800 + 2×900"), `reps` [time_s, dist_m, speed_ms, hr] and `breaks_s`.
- **Similar**: top 5 of the same sport family for two presets, `route` (route overlap 0.45) and
  `workout` (type 0.30, pace/intensity/structure); missing components score 0.4; `reasons` = top 3.

## Legs and pauses (every FIT with distance)
1 Hz grid; speed from the distance field (5 s centred). Pause = speed < 0.5 m/s for ≥ 15 s or a
timer stop (FIT timer events); record gaps without distance progress fall out as zero speed.
Moving blips < 5 s inside a stop are stop; pauses split by < 20 s and < 30 m are merged.
`legs` rows = `fields.legs` [t, dur_s, dist_m, kmh, hr, hr_max, climb_m, desc_m, grade_pct]
(climb with 2 m hysteresis on 9 s smoothed altitude), `pauses` = [t, dur_s, lat, lon, km, timer].
`moving_s` = elapsed − pauses ≥ 15 s (slightly different from Garmin's moving time).

## Route comparison (routes with ≥ 3 sessions)
- Reference = medoid session, thinned to 20 m (`ref` ≤ 300 points: lat, lon, km, alt).
- Each member is projected onto it per second: nearest reference point within 80 m, searching
  200 m back and at most 40 m + 15 m/s × time since the last good fix ahead (no jumps across
  hairpins); progress = running max; direction = the one covering more of the reference.
  Laps routes use the session's own distance scaled to the reference length.
- **Waypoints** (ski routes): pause positions clustered along the route (≤ 150 m gaps); kept when
  ≥ 30 % of sessions (and ≥ 2) stop there, ≥ 250 m from start/end and 300 m from each other;
  stretches > 3 km are split into ~2 km legs. No common stops, or runs: equal ~1 km legs (≤ 12).
- Per session × leg: moving_s, pause_s (pauses in the leg not at a waypoint), speed_kmh over the
  covered reference length, hr, climb_m, rank (1 = fastest); `stops` = pause seconds per waypoint
  (standing before the start / after the finish counts to start / end). A leg is null if the session
  covers < 80 % of it (`partial` if < 98 %) or its own distance disagrees with the reference length
  by > 25 % (`legs_off_route`).
- Quality: excellent on the Holmenkollen climb (99 % of cells filled); out & back / loop routes with
  varying turnarounds fill 30–70 %.

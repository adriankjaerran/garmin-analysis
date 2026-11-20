# Session groups, legs and route comparison

`uv run scripts/session_groups.py adrian [--workers N] [--show [ROUTE_ID ...]] [--out PATH]` → `data/adrian/groups.json` (`--out`: trial run elsewhere)
(~5 s; first run ~15 s while the rep detector of every run is cached in
`data/adrian/cache/session_groups.pkl`, keyed by FIT mtime+size; ski reps from `ski_intervals.py` go into
the same cache, keyed by FIT mtime+size+detector version). `--show` prints the leg table of a
route (default: the largest ski route, i.e. the Holmenkollen rollerski climb).

## FIT time base (`read_series`)
t = seconds since the first record. **Watch clock jumps are taken out**: some files start recording before
the clock is set (no GPS yet, an old clock), and when the first GPS fix sets it the timestamps jump forward
by days to months between two records that are really a few seconds apart (patrik: 10 files, up to 9.4 M s;
adrian: 2). The session's `total_elapsed_time` isn't affected, so `gps_quality.clock_jumps` takes the excess
(record span − session elapsed, if > 60 s) out of the longest record gaps ≥ 600 s, leaving each at the
median record interval; real long pauses with the watch running (elapsed = span: a 14 h hike) stay. Without
a session elapsed time, gaps > 6 h are taken out. The same rule shifts lap and timer-event times, and is used by
`run_analysis._read` (rep detector) and the app's FIT cache (`app/server/fitcache.read_fit`), so all of them
share one time base. Timestamps after the jump are shifted back: the date stays Garmin's (from the pre-jump
clock, which may itself be off). `read_series(...)["clock_jumps"]` = `[(t, s taken out)]`. Backwards jumps
aren't seen in the data (records are sorted by time). `READER_VERSION` (2) is part of the caches built on
the reader (similarity tracks, fitness features, compare) and the rep cache version is 3 (3: rep starts for every run).

## Grouping
- **Places**: first GPS fix of each session, greedy density clustering (densest point + everything
  within 400 m, repeat). Name = known spot within 600 m (`KNOWN`: Bislet, Sognsvann, Frognerseteren,
  Holmenkollen, Granåsen, NTNU Gløshaugen; only the cluster nearest to the spot gets the name),
  else a curated landmark within 400 m (`LANDMARKS` in the code, offline approximate centres: Marienlyst,
  Majorstuen, Frogner stadion, Frognerparken, Blindern, Ullevål stadion, St. Hanshaugen, Nationaltheatret,
  Aker Brygge, Rådhusplassen, Tryvann; Lerkendal, Øya, Nidarosdomen, Torvet, Moholt, Dragvoll, Tyholt;
  Fauske sentrum; again only the nearest cluster), else the cached OSM reverse geocode (below), else
  `<Garmin locationName>-<rank by size>`. Known and landmark names are also the place id; everything else
  keeps the generic id. The rank counts every cluster without a KNOWN name, so generic ids stay
  stable (`Oslo-3` stays `Oslo-3` although `Oslo-1/2/4/5` got landmark names); a landmark-named place keeps
  its generic id in `generic_id` (e.g. Marienlyst = Oslo-1, the start of the Holmenkollen climbs). Where an activity mostly
  happens (named places / regions by time share) is in [similarity.md](similarity.md). User overrides:
  `data/adrian/places.json` `{"Oslo-3": "Bogstad"}` (place id or generic id) or
  `{"59.933,10.719": "name"}` (any place whose centre is within 400 m).
  All naming is one function, `name_places(cens, sizes, locs, over, geocode)`, priority **user override >
  KNOWN > LANDMARKS > cached reverse geocode > `<loc>-<rank>`**. An override or a geocode name changes only
  the **display name** (`name`; `name_src: override` / `src: geocode`), never the id, so ids in URLs, saved
  filters and other scripts stay stable. `DISPLAY_NAMES` fixes spellings without changing ids
  (`Bislet` → "Bislett"). Place entries: `name, lat, lon, n, types, src` (known / landmark / geocode / generic),
  `name_src`, `generic_id`, `geocode_kind` (OSM kind of the geocode name: neighbourhood / suburb / park / ...).
- **Reverse geocode** (one-off, `uv run scripts/geocode_places.py adrian`): every start-cluster centre whose
  name is still generic is sent to OSM Nominatim `/reverse?format=jsonv2&zoom=18&addressdetails=1` (only the
  centre coordinates; User-Agent `garmin-analysis/0.1 (<email>)`, ≤ 1 request/s, ~200 calls / ~4 min for
  adrian). Replies go into `data/<p>/cache/geocode.json` `{"source", "points": [{"lat", "lon", "name", "kind",
  "for", "raw"}]}`, saved after every call; a centre with a cached point within 150 m is never re-fetched, so
  re-running only fetches new clusters. Name choice (`pick()`): the reply object itself if it is a park /
  stadium / track / lake / nature / place-type feature (not pitches, playgrounds, hotels, buildings, shops,
  parking), else the most specific area of the address chain (leisure > natural > locality > neighbourhood >
  quarter > hamlet > village > suburb > borough / city district), else a named trail (path / track / footway)
  under the point, else town > municipality > city, a street only as last resort. Oslo's bydel "Marka" (all of
  Nordmarka / Østmarka, and a region name in similarity.py) is too big: those clusters get the road instead
  ("Maridalsveien", "Holmenkollveien", "Gamle Heggelivei"). `--repick` re-runs the choice offline on the stored raw replies, `--dry-run` lists what would
  be fetched. In session_groups the nearest point within 400 m names a cluster that no KNOWN / LANDMARK
  claims. Duplicates (a name already used by a known / landmark / bigger geocoded place, case-insensitive)
  get the compass direction from the place that has it: "Holmenkollen (SW)", then "Holmenkollen (SW 2)".
  Result for adrian (2026-10-31, 202 geocoded): Fauske-1 → Brekke (Fauske-3 → Brekke (N)), Trondheim-1 →
  Eventyrbyen, Brisbane-1 → South Brisbane, Oslo-3 → Jarbakken, Dubai-1 → Al Satwa, Oslo-5 → Bjølsen. Area names
  and route names use the display names, so they change with it; ids don't.
- **Areas**: single-link chaining of start points at 2 km.
- **Routes**: track on a 75 m grid; coverage(A|B) = share of A's cells within one cell of B's;
  similarity = min of both coverages; same sport family, distance ratio ≤ 1.3; average-linkage merge
  while ≥ 0.6. Routes with ≥ 2 sessions get ids `R1..` (by size). Route `place` = most common start place of the
  members (ties → the medoid's start place, then the bigger place; deterministic, the similarity index reuses it),
  `end_place` = id of the place at the end (point-to-point). The app shows the index's route names
  ([similarity.md](similarity.md)); `name` here is the fallback. Shape = laps / out_and_back /
  point_to_point / loop_cw / loop_ccw (retrace share + start–end distance); `route_dir` = fwd/rev
  vs the reference (cw/ccw for loops).
- **Session types** (rules): runs → intervals_track/road/treadmill (rep detector, manual laps),
  tempo, long, steady, easy, race? (fast + standard distance), run_nogps, run_other; tags hilly/strides in
  `type_detail`.
  - **Intervals need real recoveries** (`recovery_share`): a break counts as a recovery when it is ≥ 30 s or ≥ 25 % of
    the rep before it (45/15 s sets: yes); at least half of the breaks must be recoveries (watch workouts with ≥ 3
    `active` laps always count). This stops the detector splitting one continuous effort at traffic
    lights / lap presses / GPS slowdowns into "intervals" (e.g. patrik 2026-10-27: 1625 m, 9.5 s, 792 m, 11.8 s,
    4902 m, 12.1 s, 581 m, a steady run with ~400 m lap presses, is tempo). patrik 2026-09-10 (reps at
    2:47–3:27 /km with HR 115–158, not on foot) has 6 of 9 recovery breaks; the rules keep it intervals_road, a user
    correction makes it easy / excluded (below).
  - **Track or road: where the reps happen** (`run_track`, `track_of`), not where the run starts (Rise's Bislett
    sessions start at Marienlyst, Patrik's at St. Hanshaugen, and some sessions start at Bislett and run their reps
    elsewhere). Known tracks `TRACKS` {name: (centre, radius)}: **Bislet** (the 400 m oval, centre = median of 674 reps,
    radius 130 m), **Øya stadion** (Trondheim, 400 m oval, centre = median of adrian's 13 rep sessions 2020–2022
    (63.42653, 10.38021), 95th / 99th-percentile rep distance 81 / 84 m → radius 120 m; user decision 2026-11-04) and
    **Frogner stadion** (the ~890 m loop the reps there run, 330 × 220 m, radius 210 m). Frogner is a **loop**
    (`LOOPS` {name: lap m} = 890; user decision 2026-11-04): not a 400 m oval and ~12 m up and down per lap, so its
    sessions are `intervals_road` (grade-adjusted, road σ; `flat_track()` is false), but its GPS reps still snap to whole
    laps (below). The Dubai track (25.2149, 55.2743) is not a track (soft surface, heat; user decision): road. The worker stores
    `track_spans` (time spans within each track's radius and with a GPS fix) per run; a run is on a track when ≥ 60 %
    of the GPS time of its reps (≥ 35 s, ≥ 2 reps; runs without reps: ≥ 80 % of its moving time) is on it. Reps with
    < 50 % GPS (indoors) fall back to the old rule (a start within 400 m of Bislet); Bislett indoor sessions are on
    Bislet. The watch's track mode / `track_running` still counts as track anywhere. Output `track` {name, share,
    basis: reps / moving / indoor / start} (null: not on a track); the fitness model reads it (`flat_track`: any track
    but a loop) for flat (no grade) and the track σ. Rep starts for every run (rep cache version 3).
    Changes 2026-11-04 (type intervals_road ↔ intervals_track): adrian 8 road → track (Frogner loop, Bislett reps from
    other starts) and 1 track → road (2025-05-20: start at Bislet, reps elsewhere), rise 42 road → track (Bislett from
    Marienlyst etc., Frogner), patrik 1 road → track (Frogner 2026-10-06). Runs whose flat / track flag in the fitness
    model changed vs the old start-place rule: adrian 42, rise 46, patrik 7. Later 2026-11-04 (Øya added, Frogner a
    loop): adrian 13 Øya sessions road → track (incl. 3 easy / steady runs on the oval now `track`), adrian 9 / rise 8 /
    patrik 1 Frogner sessions track → road.
  - **User corrections** (`data/<person>/session_overrides.json`, [data-layout.md](data-layout.md)): `type` replaces
    the rule's type after all rules (the old one in `override.type_from`), `fitness` (exclude / bound) and `note` go to
    `override` in groups.json for the fitness model. patrik (2026-11-04): 2026-09-10 → easy + exclude, 2025-06-28 →
    easy (16 min run + 60 min walk), 2024-04-14 → intervals_road (3×1000 with adrian on a ~1000 m road loop ~2.5 km
    north of Bislett; `track` null), 2025-05-15 and 2026-02-05 → bound (sub-maximal).
  - **Steady vs easy** (`steady_or_easy`, after the other rules): a continuous run classified easy is `steady` when its
    mean HR is ≥ 68 % of the HR reserve (`profile/hr_settings.json` max / rest) at ≥ 1.05 × the person's easy pace, or
    it is ≥ 1.15 × the easy pace (HR ≥ 60 % HRR or no HR); ≥ 15 min moving. Easy pace = median speed of the person's
    own continuous runs (≥ 3 km, trusted distance) with mean HR < 65 % HRR within ±180 days (≥ 5 runs, else no
    steady). The median of *all* runs (the reference for tempo / race? / structured) is pulled up by fast runs, so it
    would leave runs like 2026-10-03 (4:34 /km, HR 167) "easy". Result (2026-11-04): adrian
    196 easy + 77 steady, rise 48 + 31, patrik 40 + 13. HR-flagged runs (`hr_settings.json` `activities[].flags`) use
    the pace rule only. Steady runs are lower bounds in the fitness model, like easy ones. Label "Steady run".
  - **Multisport files** (`multisport_family`): a `multi_sport` activity whose FIT sessions are ≥ 80 % of the time one
    family (FIT sport 1 = run) gets that sport and is classified like any run (adrian 2026-10-15, two running legs at
    Bislett: `run` / intervals_track, scored by the fitness model). Without a summary HR the mean HR comes from the
    records (`avg_hr` in groups.json, `avg_hr_src: records`). Ski → **ski_intervals** (≥ 3 reps from the ski detector below; detail
  `lap workout` / `uphill reps` / `reps`, `short|medium|long reps`) / ski_easy / ski_hard (≥ 25 % time in
  Z4–5) / ski_long (≥ 150 min or ≥ 35 km); `surface` guess roller? (May–Oct) / snow — classic vs skate
  isn't in the files (all sport 12). Concept2 ergs (sport `row` / `skierg`) → **erg_intervals** (a Concept2 interval
  workout; reps = its work laps with the erg's targets, signature "12×2:00" / "4×500") / erg_piece (continuous ≤ 12 min) /
  erg_steady; distance trusted, no legs / pauses from the records ([concept2.md](concept2.md)). Other sports keep their sport / activity type as type (hike,
  cycling, lap_swimming, rowing_v2, …) with `type_detail` tags: `no GPS`, `over 24 h (multi-day or timer left running)`, and for hikes `hilly` (≥ 25 m/km) / `steep` (≥ 60 m/km), `long` (≥ 20 km or ≥ 5 h),
  `fast: part by vehicle / bike?` (moving ≥ 7.5 km/h for ≥ 30 min: 5 hikes, e.g. a 112 km "hike").
  No-GPS distances are `dist_trusted: false`. **Bislett indoor** (GPS track < 30 % of the watch distance at Bislet, or
  no GPS at all in Oct–Mar with ≥ 3 whole-lap reps; the latter are placed at Bislet): `dist_trusted: false`, type
  `intervals_track` with detail `indoor laps (546.5 m)` (`, no GPS`), reps from the laps (≥ 160 steps/min: the lap's FIT total_cycles, or the records' mean cadence when the two differ
  by > 15 % (`lap_spm_records`; the last 1-lap rep of 2026-11-10 / 11-17 said 143 vs 198), ending on
  a manual press) as whole laps n × 546.5 m − 7.5 m (n = 1–3 from the rep time; fastest pace per lap count 2:25 / 2:35 / 2:45 /km, `INDOOR_N_FAST`); `indoor_bislett` {source
  (bislet_gps_lost / no_gps), gps_cover, n_reps, rep_laps [[start_s, dur_s, n]], rep_m}. Overrides:
  `data/<person>/indoor_bislett.json` `time_only` {id: reason}, `rep_start` {id: {reps: {"<rep no., 1-based>": shift s}, note}} (a lap pressed late / early: moves the rep's start, its end stays; adrian 2026-11-17 rep 1 −10.5 s). `indoor_grid()` gives consumers the matching 1 Hz speed
  (rep distance / rep time in reps, 0 between, no altitude). See [distance-calibration.md](distance-calibration.md). Intervals (runs and ski) get `sig` ("6×1000", "10×4:00",
  "3×1800 + 2×900"; ski: durations only, "4×4:00"), `reps` [time_s, dist_m, speed_ms, hr], `breaks_s` and
  `rep_starts` (s on the read_series time base; ski intervals, Bislett indoor and run reps analysed after 2026-11-01;
  the app's series cache takes the reps of ski / indoor sessions from here).
  Track sessions: each rep counts as its set distance, `track_set_dists(times, dists)` (the signature group's distance
  label when the rep is 5 % under .. 12.5 % over it, else `TRACK_SET_M` via `track_set_m`); `snap_track_reps` applies
  it to rep dicts (compare, the activity page, avg rep pace).
  **`track_reps`** [start_s, dur_s, measured_m, set_m] (`track_reps()`, for the fitness model's speed): GPS-mode
  intervals_track sessions on a known track and intervals_road sessions on a loop (Frogner) (not the watch's track mode, whose laps are already the set distance, and
  not Bislett indoor). Rep windows = the manual laps when the lap presses are the reps (≥ 3 manual / non-timed active
  laps each overlapping detected reps for ≥ half their time, ≥ 75 % of the median such lap's speed, together ≥ 70 % of
  the detected rep time; Patrik's 2026-11-03 detector split two 1000s, the laps don't), else the detector's reps. Per
  group of similar reps (`rep_groups`, ≥ 2 reps, distances within 15 % and times within 25 % of the median) the set
  distance (`group_set_m`) is its 100 m multiple when steady (≤ 6 %) and within 4 % (the signature's label: 3×1800 at
  Frogner), else on a 400 m oval (`OVALS` = Bislet, Øya stadion) the nearest `TRACK_SET_M` within 10 % if the next is ≥ twice as far;
  reps of the group from −15 % to +20 % of it count as it. On a loop (`LOOPS`) only whole laps: n × 890 m within 5 %
  of the group median, reps from −7 % to +5 % of it (`LOOP_TOL`, `LOOP_LO`, `LOOP_HI`; GPS laps there 847–897 m, two
  laps 1685–1803 m → 890 / 1780): ~1000 m reps there (adrian 2026-06-09 988–1017 m, rise 942–986 m the same day) and
  rise's 1521 m rep in a 2-lap set keep their GPS distance. Not snapped: time-triggered laps (time reps), groups with
  unclear distance (Rise's winter Bislett sessions: 173–595 m for ~105 s reps). Examples: patrik 2026-11-03 953 / 955 /
  921 / 944 / 938 / 969 m → 6 × 1000; rise 2026-10-20 411–467 m → 10 × 400 (a 145 m recovery lap the detector merged
  is skipped); adrian 2026-09-08 914 m (GPS cold start) → 1000. 2026-11-04 (with Øya and the Frogner loop): adrian 52
  sessions, rise 40, patrik 5 have `track_reps`.
- **Pool swims** (`pool_swim()`, FIT sport 5 with length messages and a pool length): the records of a pool swim
  come in bursts and their distance steps per length, so pauses / legs from records are garbage (12:40 "moving",
  8 km/h). Instead: moving = swimming time of the active lengths, distance = valid lengths × pool length (lengths
  faster than 2.5 m/s are lap-button glitches and dropped: 8 + 4 lengths in two 2019 swims), rests = gaps ≥ 10 s
  between lengths, elapsed from the session; no legs / pauses. Output `swim` {pool_m, lengths, dist_m, active_s,
  timer_s, elapsed_s, rest_s, pace_100m_s, strokes_per_length, bad_lengths, n_rests, longest_rest_s}; pool swims
  count as trusted distance.
- **Similar**: top 5 of the same sport family for two presets, `route` (route overlap 0.45) and
  `workout` (type 0.30, pace/intensity/structure); missing components score 0.4; `reasons` = top 3.

## Ski intervals (`scripts/ski_intervals.py`, detector `ski-1.0`)
The run detector needs ≥ 2.5 m/s steps, so on skis it finds either nothing or random fast stretches
(downhills). The ski detector runs on the same 1 Hz
grid (fast reader, ~10 ms per file, all 135 ski files in ~1 s), same code in session_groups and compare:
- **Lap workout** (`mode ski_laps`): ≥ 5 consecutive time-triggered laps (or laps marked rest/recovery)
  alternating between two durations; work = the longer phase (equal: the phase where HR rises).
  Other blocks of the same workout split by a manual lap are added (laps of the work duration ±10 %).
  Needs ≥ 3 reps of similar duration. HR is not required (the workout is programmed).
- **Effort** (`mode ski_effort`): work rate P = v·(0.03 + grade) + 4e-4·v³ (vertical-equivalent m/s,
  grade over ±10 s, clipped at 0, 0 while stopped), 15 s smoothed. Recovery = standing or P < 45 % of
  the session's median working P for ≥ 30 s (so the downhill back after an uphill rep is a recovery);
  work blocks = the rest, ≥ 60 s. A block is hard when its last-third HR ≥ max(Z4 floor (165),
  top-3 block median − 12). Reps = the contiguous series of hard blocks with the most work time (non-hard
  blocks < 90 s don't break it); hard blocks < 60 s apart or without a 12 bpm HR drop between them are one
  rep; first / last reps shorter than half the median are dropped (warm-up ramp, finish). The session
  counts only if it is hard overall (≥ 25 % of HR time ≥ Z4, the ski_hard rule), has ≥ 3 reps ≤ 20 min,
  ≥ 3 of them within ±25 % of the median duration, ≥ 8 min rep time, median recovery ≤ 8 min and median
  HR drop in the recoveries ≥ 20 bpm. Otherwise there are no reps.
- **Holmenkollen climb (R3)**: every session stops at the same points, so stops alone never make
  intervals. The 11 easy climbs (HR < Z4 in most blocks, < 25 % Z4+ time) get no reps. 7 of the 8 hard
  climbs are intervals: 3–4 climbs between stops ending at 170–188 bpm, standing rests of 1–4 min
  (median HR drop after 60 s of rest 25–36 bpm per session; e.g. 2026-08-10 `4×4:30`, 187 → 114 bpm in
  rests of 3:00–4:15). The 8th (2024-09-30) fails the
  similar-duration rule (7:38 / 3:57 / 2:39) and stays ski_hard. This is a judgment call: a hard R3
  climb is "climb hard between the usual stops"; the rep durations follow the terrain, so their
  similarity `exact_key` is the route (`ski/intervals · R3`), see [similarity.md](similarity.md).
- **Result** (135 ski files): 18 ski_intervals = 11 lap workouts 2021–2024 (10 without GPS) (`4×4:00` / 2:00 ×6,
  `3×4:00` ×2, `7×4:00`, `8×4:00` / 1:00, `5×1:00` / 0:30) + 7 hard R3 climbs. No GPS ski session outside R3
  qualified (hilly Sognsvann tours with 8–10 min climbs at 150–160 bpm stay ski_easy).
- **Signature** (`rep_groups(times, dists, ski=True)`): one group if every rep is within ±35 % of the
  median (`4×6:30` for 5:19 / 5:02 / 7:34 / 7:25), else consecutive groups at ±30 %; labels are durations
  rounded to 15 s below 2:00, 30 s below 10:00, else 1:00.
- Cache entry (session_groups.pkl, per FIT): `rep_times, rep_dists, rep_speeds, rep_hr, rep_starts,
  rep_climbs, breaks [(time, dist, standing)], laps [(intensity, trigger)], mode, threshold, version`.
  `uv run scripts/ski_intervals.py adrian <id> ...` prints reps and all work blocks (late HR, P).

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
  200 m back and ahead at most 40 m + min(15 m/s × time since the last on-route fix, 200 m + 2 × own
  distance moved since then) (`PROJ_JUMP_M`, `PROJ_JUMP_K`). The own-distance cap keeps hairpins, a long
  stop next to a later part of a loop (R30 2026-05-22: stood 19 min at km 2 and snapped to km 12.4) and
  detours from jumping kilometres ahead; after a real shortcut the window catches up and the session
  re-acquires with a jump that compare marks as not covered ([compare.md](compare.md)). Progress = running
  max; direction = the one covering more of the reference.
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

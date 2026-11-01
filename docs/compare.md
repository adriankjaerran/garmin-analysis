# Workout comparison (`scripts/compare.py`)

Compares one session with one or many others: "this run vs similar runs / intervals". Library + CLI.
Served by `/api/compare` and the Compare page (see [app.md](app.md)).

```
uv run scripts/compare.py adrian distance --route R3                 # all Holmenkollen climbs, ghost race vs the newest
uv run scripts/compare.py adrian intervals --sig 6×1000 --place Bislet --last 6
uv run scripts/compare.py adrian breaks 8884710494 8835446895        # 6×1000 vs 5×1000
uv run scripts/compare.py adrian moving 24874084265 24460851059 --reference median --json out.json
uv run scripts/compare.py adrian moving 11266836943 12910143619 --start ref   # common start line (lead-in cut off)
uv run scripts/compare.py adrian elapsed --sig 6×1000 --place Bislet --last 8 --start rep1   # t = 0 at rep 1
uv run scripts/compare.py adrian validate
```
Ids: the explicit ids come first, then `--route` members or `--sig` (+ `--place`) matches, newest first.
`--last N` keeps the first N. **The first id is the reference**, unless you pass `--reference median`.

```python
import compare as cmp                      # scripts/ on sys.path
r = cmp.compare(ids, mode="distance", person="adrian", reference="first" | "median",
                max_points=1500, smooth_s=None, axis="auto" | "route" | "own",
                rep_axis="absolute" | "normalized", gap_s=60, rep_gap_s=30, step_m=None,
                match="auto" | "index", workers=8,
                start=None | "ref" | <id> | (lat, lon) | "lat,lon" | "rep1", start_radius_m=40,
                rep_series=False, rep_lead_s=15, rep_post_s=30)   # -> JSON-serialisable dict
cmp.select_ids(person, ids=(), route=None, sig=None, place=None, last=None)   # -> [id, ...]
cmp.validate(person)                                                          # prints the checks below
```

## Data and speed
- `groups.json` (session_groups.py) supplies the session meta, routes, waypoints and the medoid. FIT files
  are re-read with the fast reader on the 1 Hz grid (`session_groups.read_series/to_grid`, ~10 ms each).
  Watch clock jumps (days to months between two records, patrik's 3077 / adrian's fr935) are taken out by the
  reader, the rep detector and the app's FIT cache alike ([session-groups.md](session-groups.md#fit-time-base-read_series)).
  Pauses use `session_groups.find_pauses` (speed < 0.5 m/s for ≥ 15 s, or timer stopped).
- Reps come from `run_analysis.analyse` for runs (detector v2, sub-second boundaries, ~0.6 s per file, in a
  process pool) and from `ski_intervals.analyse` for ski sessions (lap workouts / hard efforts, 1 s boundaries,
  ~10 ms per file; the same detector session_groups uses, so ski reps equal groups.json reps). Route projections
  come from `session_groups.project` onto the medoid's 20 m reference. All are cached in
  `data/<p>/cache/compare.pkl`, keyed by FIT mtime+size (+ detector version for ski).
- Sessions **without distance** (no-GPS lap workouts, HR only) work in `elapsed`, `intervals` and `breaks`
  (speed / distance / pace fields are null, pauses = timer stops only, reps are matched by duration). In
  `distance` and `moving` they are skipped with `why: "no distance ..."`. With a warm cache a call takes 0.01–0.1 s
  (all 19 R3 sessions: 0.1 s). Cold: about 0.5 s plus the rep detector.
- An in-process `_Store` per person caches the grids of up to 64 sessions. It isn't thread-safe.

## Common output (all modes)
```
mode, person, ids, skipped [{id, why}], compute_s
reference   {kind: "session", id} | {kind: "median", n, summary{...median of the session rows}}
sessions    [{id, date, sport, type, sig, route, place, elapsed_s, moving_s, pause_s, n_pauses, dist_m,
              moving_kmh, moving_pace_s_km, gap_pace_s_km (runs), hr_avg (moving), hr_max, climb_m,
              eff_m_per_beat, delta {moving_s, elapsed_s, pause_s, dist_m, moving_pace_s_km,
              gap_pace_s_km, moving_kmh, hr_avg, climb_m, eff_pct}}]
axis        {x, unit, note, ...mode-specific}
series      {"<id>": {x: [...], ...}}      # <= max_points per session, numbers rounded, NaN -> null
```
- Moving pace = total distance / moving time (like Garmin). **GAP** (runs only): every second's
  distance × the Minetti (2002) cost factor C(g)/C(0). The grade g comes from smoothed altitude over ±30 s
  (≥ 50 m), clipped to ±30 %. GAP = Σ(distance × factor) / moving time. On the flat Bislet track,
  GAP = pace.
- `eff_m_per_beat` = (GAP speed for runs, moving speed for ski) in m/min ÷ moving HR. Higher means more
  distance per heartbeat. `delta.eff_pct` is relative to the reference.
- Deltas are session − reference. With `reference="median"` the reference is the median of each value
  across the selected sessions.

## Common start line (`start`)
"I forgot to start my watch" / "I jogged to the start first": with `start` set, every session in the elapsed, moving
and distance modes starts at the same place. Intervals and breaks are rep-aligned already and ignore it
(`align: {applied: false, note}`).
- **Anchor and line.** `start="ref"` = the first id's start (also with the median reference), `<id>` = that session's
  start (it must be one of the ids), `(lat, lon)` = where the first id first passes within 30 m of the point (else its
  closest approach). The start moment `tA` is the anchor's first moving second with GPS (a watch started while standing
  → where it moved off); P = its position, heading = P → the anchor's positions 10–50 m further along its GPS path
  (GPS path length, not the watch distance: that runs ahead of stale fixes at the start). The **line** is the segment
  across that heading, ±`start_radius_m` (default 40, API 10–150). The anchor's path after `tA` is sampled every 10 m.
- **Crossing (aligned).** Forward crossings of the line (cross-track offset ≤ R, motion within 60° of the heading over
  ±5 s, interpolated to sub-seconds). Accepted when the session then follows the anchor's first 200 m: for each of
  the 21 template points (shifted by the crossing's offset along the line) the nearest point of the session's next
  400 m, mean ≤ 25 m, and in order in ≥ 70 % of the steps (rejects the reverse direction). The first accepted crossing
  wins (laps / loops: the first pass); within the same pass (still within 2R of P, < 4R of path) the last one (jitter
  while waiting). **Start at a stop:** if a pause overlaps the stretch from the crossing until the session is 2R from P,
  the clock starts when the pause ends (it *leaves* the line). Sessions starting ≤ 20 m past the line count as aligned
  (`kind: at_line`).
- **Late joiners (kept).** No accepted crossing, but where its recording begins (its first 100 m of GPS path, moving)
  the session is within R of the anchor's path anywhere after the line, moving within 60° of the path's heading there
  (±30 m), and follows the anchor's path from that point (same 200 m template check): `status: late`, `join_m` = the
  anchor's watch distance from the line to that point. One candidate per pass (the nearest path point of each run of
  points within R), so laps and out & back legs are tried separately. Also a session coming in from a side street onto
  the anchor's first 150 m (`side_street`). A late joiner that dawdles at its join point for > 60 s starts at its last
  second within R/2 of it. If its recording begins on the path *and* it also crosses the line later (a loop started
  elsewhere on it), whichever leaves the longer stretch to compare wins. **Direction:** when the anchor and the
  session belong to the same (non-laps) route, a late joiner whose route projection runs the other way (`dir`) is
  excluded: it only shares a stretch with the anchor (R13 out & back: 2 reversed sessions, "starts on the anchor's path
  2343 m after the line, but runs route R13 the other way").
- **Excluded**, with a reason (`skipped` gets `start: <reason>`): `no GPS`; `crosses the start line but then takes a
  different way (path differs by X m)`; `crosses the start line only in the opposite direction`; `starts on the
  anchor's path, but running the other way`; `never within R m of the start line (closest X m) and doesn't start on
  the anchor's path` (or `doesn't cross the start line within R m of its point` when it passes closer but crosses
  further out). If the reference itself is excluded, the next session is the reference.
- **Windowing.** Each kept session is copied and cut at `floor(t0)` (`_window`): every per-second array sliced,
  distance / climb / moving time from 0, pauses re-cut; the store's sessions are untouched. Everything downstream runs
  on the cut copies, so the **sessions table totals are the aligned window**; `sessions[].whole` holds the whole-session
  totals (elapsed, moving, pause, distance, paces, HR, climb).
  - *Time modes:* x = seconds since the line. A late joiner enters at the **pacer's** time at its join point (the
    reference when it starts at the line, else the anchor) with distance = `join_m`, so its `ahead_m` starts at ≈ 0
    there (R4: −1…−3 m). Series `t` stays the file's elapsed time (the map places markers by it).
  - *Distance, own axis:* d from the line (+ `join_m` for late joiners).
  - *Distance, route axis:* x = route metres − the line's route position (`axis.origin_m` = the anchor's projection at
    its first on-route fix from `tA`), so the line is x = 0. Sessions at the line start at 0 (a projection up to 40 m
    past the line still counts as at the line); late joiners at max(join + 20 m, the anchor's route position where they
    joined). Legs start at the line ("start line → …", waypoints < 50 m past it dropped), `route_ref.m`, `uncovered`,
    `gap_end.x_m` are shifted the same way; `coverage_pct` is relative to the route after the line. Series also get
    `t` (file elapsed time at x) for the map.
- **`align` block:** `{applied, start {kind: ref|session|point, anchor_id, lat, lon, heading_deg, radius_m, t_anchor_s,
  route_m}, finish: null, pacer_id, sessions {"<id>": {status: anchor|aligned|late|excluded, kind:
  anchor|crossing|at_line|join, reason, offset_s, moving_offset_s, offset_m (cut: file seconds / watch metres),
  dist_to_line_m (|offset| at the crossing, or distance to the path at the join), zone_pause_s, join_m, x_offset_s,
  x_offset_moving_s (late: where it enters the time modes), shape_err_m, closest_m (excluded)}}, excluded [{id,
  reason, closest_m}], late [ids]}`. `finish` is reserved for the finish line (Phase 1b).
- Cost: 1–10 ms per session (no cache; the GPS path length is cached on the store's session).

## t = 0 at rep 1 (`start="rep1"`)
"Start all the intervals at the same time" in the real-time views: in `elapsed`, `moving` and `distance` every session
is cut at the detected start of its **rep 1** (`_align_rep1`), so rep 1 starts at x = 0 for everyone; the warm-up is
hidden and the real breaks are kept (so later reps drift apart by the break differences: Bislet 6×1000, rep 6 spread
~220 s). Intervals / breaks ignore it (`align: {applied: false, note}`).
- **Which rep is rep 1:** the same reps and matching as the intervals mode (`_reps_slots`: strides left out, slots
  matched against the first session with reps). A session's rep 1 is its rep in the anchor's rep-1 slot; extra reps
  before it (e.g. an 800 m in slot `0+1`) are cut off with the warm-up. A session whose first matched rep is in a later
  anchor slot k (1×800 + 5×1000 vs 6×1000: its 1000s match reps 2–6) is kept as **late**: it enters at the anchor's
  elapsed / moving time and distance of rep k since its rep 1 (`x_offset_s`, `x_offset_moving_s`, `join_m`), like a late
  joiner of the start line.
- **Excluded** (reason in `align.excluded` / `skipped`): sessions groups.json doesn't count as interval sessions (no
  `reps`; the run detector finds a "rep" in easy runs: the whole run as one effort), no reps detected, or no rep matched
  to an anchor rep. If nothing is left: `ValueError("no interval session ...")`.
- **Windowing:** `_window` at the rep start, with the fraction of the cut second taken out of the x offset (time modes)
  and the distance, so x = 0 is the sub-second rep start (elapsed x[0] ∈ (−1, 0]). Totals are from rep 1 on
  (`sessions[].whole` = whole session); the map draws the part before rep 1 dashed (`offset_s`).
- **Distance mode:** always the session's own distance from rep 1 (`axis="own"`): reps start anywhere along a road
  route, so a route position isn't the same point in the workout. Legs = 1 km / 400 m (Bislet) from rep 1.
- `axis.t0 = "rep1"`; `align = {applied, start {kind: "rep1", anchor_id, t_anchor_s}, sessions {"<id>": {status:
  anchor|aligned|late|excluded, kind: "rep1", reason, offset_s, moving_offset_s, offset_m (where rep 1 starts in the
  file), rep_n (its own rep number), slot (label of its first matched slot), join_m / x_offset_s / x_offset_moving_s
  (late)}}, excluded, late}`.

## Modes
### `elapsed` / `moving`
x = elapsed seconds, or moving seconds (paused seconds are dropped, so x = moving time). Per point:
`t` (elapsed s), `speed_kmh` (smoothed 5 s for runs, 15 s for ski, `smooth_s`), `hr`, `alt`, `dist_m`,
`ahead_m` (own distance − reference distance at the same x, positive = ahead) and `pauses
[{x, t, dur_s}]`. With the median reference you also get `median {x, speed_kmh, hr, dist_m}`, the
per-second median over the sessions present (needs at least half of them).

### `distance` (= `route_position`)
- **x axis.** With `axis="auto"`: if every session belongs to the same compared non-laps route, x = metres
  along the route reference. That's the medoid's polyline, and each session is projected onto it per
  second exactly as in session_groups (progress = running max, `dir` per session, the jump window capped by
  the own distance moved). Otherwise x = the session's own distance (Bislet laps, mixed routes, treadmill).
  `step_m` = max(10, length / 1500).
- **Start.** On the route axis a session's time counts from its first on-route fix, so a lead-in before it
  gets on the route (even when it joins at the route start) isn't dumped into the first bins (R11
  2023-11-26, 890 m / 324 s lead-in: was 0 → +161 → +323 s within 20 m, first leg 647 s; now 0 → +12 → +24 s,
  348 s). Then the clock starts when the session **leaves** its start: leading bins (within 300 m) slower
  than max(20 s, 4 × its median bin, elapsed time) are cut off (standing / milling about / GPS creeping at
  the start).
- **Coverage.** On the route axis some stretches aren't *covered*: the session didn't run them on the route,
  so their time isn't comparable. Between two consecutive on-route fixes: a progress **jump** (> 100 m and
  > 100 m + 1.5 × own distance moved, or > 40 m and faster than max(2.5 × own moving speed, 25 km/h run /
  60 km/h ski)), or **off route** > 120 s while progress went on > 100 m and the own distance differs from it
  by > 10 % + 50 m (another way; a parallel path of the same length stays covered). Per bin: a **stall** (one
  bin passed while the own distance grew > 100 m + 1.5 × step: a detour that comes back, running the other
  way, milling about) and **too fast** (100 m passed quicker than the plausible top speed in elapsed or moving
  time: small jumps, GPS drift during a pause). Uncovered bins have no speed / HR / gap value (a break in the
  chart lines), and they add nothing to the ghost race: the gap is held across them. Per session
  (`sessions[]`): `coverage_pct` (covered share of the route), `uncovered [[from_m, to_m]]`, `uncovered_m`,
  `reversed`, and `gap_end {x_m, delta_moving_s, delta_elapsed_s}`: the gap at the last position both this
  session and the reference covered (the table's "Gap at end"). The series has `uncovered` too.
- **Per x:** the first time the session reaches x, as `elapsed_s` and `moving_s`. Speed =
  2·50 m / moving time over x ± 50 m (`speed_kmh`, `pace_s_km`). `gap_kmh` is grade-adjusted (runs).
  `hr` is the HR at the time x is reached. `alt` comes from the route reference (route axis) or from the
  session's own altitude.
- **Ghost race.** `delta_moving_s` / `delta_elapsed_s` = own time to reach x − reference time
  (**positive = behind**). It's accumulated per bin from the first bin both sessions cover, so a session
  that joins the route late starts at 0 there. Sessions that join mid-route (first on-route fix > 0) start
  20 m after the join, because the projection waits there until the session catches up. Increments only
  count on bins both sessions covered (see Coverage). Before this guard ~15 % of route sessions had impossible
  gaps (R30 2026-05-22: −2985 s on 15 km after the projection jumped 10 km; now +166 s over the 36 % both
  covered). Example, R3, newest
  session (2026-08-10) vs 2026-06-07: −42 s at km 2, −35 s at km 4, +33 s at km 6, +52 s at km 8, +26 s
  at the finish (moving time). In elapsed time the 06-07 session is ahead by 272 s because it stopped less.
  Median reference: increments are the per-bin median, so `median {x, moving_s, speed_kmh, hr}`.
- **`legs`**: `[{from_m, to_m, name, climb_m, sessions{"<id>": {moving_s, pause_s, speed_kmh, hr,
  delta_moving_s (at leg end), d_moving_s (vs reference leg), rank, partial?}}}]`. A session that didn't
  cover more than 10 % of a leg gets no row there; `partial` = covered share when < 98 %. The legs come from the
  route waypoints (common stops) on a route axis (`legs_kind: route_waypoints`), 400 m laps when every
  session is at Bislet (`laps_400m`), else 1 km (`km`). A session covers a leg if it joins / stops within
  100 m of the leg ends. `route_ref {id, m, alt, lat, lon, waypoints}` is the profile and map line.

### `intervals` (rep-aligned, breaks collapsed)
- **Reps**: detector v2 reps, with strides left out (groups of ≤ 2 consecutive reps < 35 s, the
  same rule as the signature). Ski: the ski_intervals reps as detected (only ski_intervals sessions have any).
- **Matching** against the anchor session (the first id, also with the median reference). It's an
  order-preserving DP. Rep cost c = min(|log distance ratio|, |log time ratio|) when that's ≤ 5 % (same
  prescribed rep, e.g. 1000 m at different speeds or 4:00 at different distances). Otherwise c = the
  max of both. Pairs with c > 0.2 can't match. Cost max(0, c − 0.05), so GPS noise is free. An unmatched
  rep costs 0.25, plus a tiny index-shift penalty so ties leave the *trailing* reps unmatched. If fewer
  than half the reps match (different workouts, e.g. 6×1000 vs 8×800), it falls back to index pairing
  (rep 1 vs rep 1). `rep_summary[id].match` = anchor / distance / index. `match="index"` forces index
  pairing. When every session is a ski session (`det_mode` ski_*), c = the closer of distance / time,
  free within 10 %, matchable up to 0.45 (terrain-defined reps vary more).
- **Slots**: the anchor's reps are slots `1..n`. Unmatched reps of other sessions get extra slots right after
  the anchor rep they follow (`"0+1"` = before anchor rep 1, `"6+2"` = 2nd extra after rep 6).
  `slots [{slot, label, anchor, n_sessions}]`.
- **Timeline** (`series`): slot k starts at `axis.slot_x0[k]` for every session. Its width is the longest
  rep in the slot, or 100 with `rep_axis="normalized"` (x = % of the rep). Breaks are squeezed linearly
  into a fixed `gap_s` (60) between slots, and the 60 s before the first rep are shown at x < 0. Rep points
  are at 1 s and never thinned (`max_points` only applies to the pre / break points, built at ~1 per x unit, so
  a long break or a clock jump can't flood the series); `t` has 0.1 s. A session without a rep in a slot
  draws its break only through the collapsed gaps, split evenly, with a null point where it skips the slot's
  rep area (no line through it). Per point:
  `x, t` (elapsed), `speed_kmh` (3 s difference of the distance field, so no watch-speed lag), `hr`,
  `phase` (0 pre, 1 rep, 2 break), `slot`.
- **`reps["<id>"]`** = one row per slot (null if the session has no rep there): `n, start_s, time_s, dist_m,
  speed_kmh, pace_s_km, hr, hr_max, hr_start, hr_end, d_time_s` (time at the reference rep's distance −
  reference time), `d_pace_s_km, d_hr, d_hr_max, d_hr_end`. With the median reference, the per-slot reference
  is in `reps_ref`.
- **`rep_summary["<id>"]`**: `n_reps, rep_time_s, rep_dist_m, rep_pace_s_km, rep_hr, rep_hr_max,
  break_total_s, break_median_s, drop_60_median, matched_slots, d_rep_pace_matched_s_km,
  d_rep_hr_matched` (over the slots both have, so 5×1000 vs 6×1000 compares like with like),
  `det_mode` (gps / track / ski_effort / ski_laps), `n_detected, n_used, match`. `no_reps` lists sessions without
  reps. An easy session alone (run or ski) raises `ValueError("no reps found in any session")`.

- **`rep_series`** (`rep_series=True`, intervals only; the Rep N and Per rep views): every rep on its own real-time
  axis, `{lead_s, post_s, slots: [{slot, width, x0 = -lead_s, n, series {"<id>": {speed_kmh, hr, start_s, time_s}}}]}`.
  x = x0 + array index = seconds since the session's (sub-second, detected) rep start, a shared 1 s grid from
  −`rep_lead_s` (15) to the slot's longest rep + `rep_post_s` (30). Speed = the 3 s distance difference (as in the
  timeline), HR interpolated. Null before the file starts, after `post_s` past the own rep end, and from the session's
  next rep start on (the lead-in can reach back into the previous rep when the break is shorter than `lead_s`).
  Size: 8 Bislet 6×1000 +125 kB (684 → 809 kB with tracks).

### `breaks` (break-aligned)
Same matching and slots. Break k = from the end of the rep in slot k to the session's next rep.
`breaks["<id>"]` per slot: `start_s, dur_s, dist_m, speed_kmh, standing_s` (speed < 0.5 m/s), `kind`
(stand ≥ 70 % standing / jog ≥ 1.8 m/s / walk ≥ 0.8 m/s / stand+walk), `hr_peak` (max from rep end −5 to
+15 s), `hr_30/60/90` and `drop_30/60/90` (null if the break is shorter), `hr_min`, `hr_next_start`,
`after_rep`, `next_slot`, plus `d_dur_s, d_drop_30, d_drop_60, d_hr_next_start` vs the reference break
(`breaks_ref` for median). Timeline: break k starts at `axis.break_x0[k]` for every session, and x runs in real
seconds since the break start (the width is the longest break in the slot). Reps are squeezed to
`rep_gap_s` (30). After the last rep, up to 120 s of recovery is shown (phase 3 `post`). The rep table
is included too.

## Validation (`compare.py adrian validate`, 2026-10-31)
Correctness checks first, for any person (`validate_fixes`; `route_scan` is the ghost-race scan):
- **Clock jumps:** patrik 10 files fixed (0.4–155 days taken out), adrian 2; in every file with long record
  gaps the record span = session elapsed ± 60 s. Patrik's 23044028473 / 21445487763 (before: 0 rep points,
  elapsed 86400 s) draw 1455 / 1373 rep points, elapsed 2986 / 3574 s.
- **Ghost race** (every non-laps route with ≥ 3 sessions; impossible = a covered ≥ 250 m stretch faster than
  max(2 × own moving speed, 25 km/h run / 50 km/h ski)): adrian 24 of 161 sessions → 0, patrik 3 of 19 → 0
  of 29 (R1 and R7, which hold clock-jump sessions, are on the route axis now). Coverage median 91 % (adrian),
  66 % (patrik: some sessions stop where the projection stalls on out & back / point-to-point routes).
- **Route start:** gap change within the first 20 m of each session's start on the axis ≤ 60 s for every
  route session (adrian worst 24 s, patrik 16 s).
- **Intervals timeline:** reps at 1 s and empty slots left empty (R3 interval climbs with slot `1+1`, Bislet
  6×1000, 6×1000 vs 5×1000, patrik's clock-jump sessions).

- **Common start** (`validate_start`; every route with ≥ 3 sessions, `start=ref`, the newest session is the anchor):
  adrian 33 routes: 101 aligned, 42 late, 46 excluded; patrik 10 routes: 15 / 8 / 13. Every kept session starts at
  distance 0 (late: at its `join_m`, ± 30 m for thinning), its distance ahead of the reference at the first point is
  ≤ 40 m, and on the route axis the gap changes ≤ 60 s within the first 20 m (0 misses each). Line crossings vs the
  route projection (raw progress within 30 m of the line's route position, loops and point-to-point routes): median
  |Δ| 8 s (adrian, n = 16), 0 s (patrik, n = 2); 2 crossings are a later pass than the projection's: R12 2024-06-16
  (the first pass misses the 25 m template tolerance by 0.5 m, so a lap later is taken) and R27 2024-04-14 (projection
  artefact). Out & back routes are left out of that comparison: their projection matches the other leg's shared
  streets (checked by hand: R5, patrik R1 / R6 sessions start 1.5–2.3 km before the line and really reach it at the
  crossing found). Slowest call 0.25 s (40 sessions).
- **Common start, adrian cases:**
  - R11 lead-in, 12910143619 vs 11266836943: cut at 323.9 s / 890 m; ahead at 5 / 10 / 20 / 30 min −108 / −232 /
    −111 / −144 m (own start −210 / −373 / −504 / −368); table 8186 m / 42:10 moving (whole 9073 m / 47:33). The
    parallel street 12069730602 is excluded (closest 75 m; with R = 80 still excluded: it crosses the line 87 m out),
    9885837163 / 12935044109 (loop the other way) too.
  - R3, anchor 19475311436 (watch started at the km 1.86 stop, line at route 1840 m): 18 of 18 aligned, cut at
    466–781 s, 15 left the line after a 46–250 s stop. Their gap moves up to ~30 s in the first 20 m (milling about at
    > 0.5 m/s after the stop counts as moving).
  - R4, the medoid 14872637121 (started ~150 m into the usual route): 12 aligned (cut 44–104 s, 4749963603 `rev` in
    the grouping crosses forwards), 5 late (join 240–1554 m; ahead of the reference at the join −1…−3 m), 0 excluded.
    A point at route km 3: 15 aligned, 5073438359 excluded ("crosses the start line but then takes a different way,
    path differs by 35 m").
  - R1 Bislett laps: 34 of 34 kept, the first pass; 3 cut after more than a lap (warm-up outside the track, e.g.
    18440364486 at 625 s / 1515 m).
  - R13 out & back reversed (20212154644, 14326254571): excluded at the route start and with a point at km 2 (the
    prototype took them as joins).
  - patrik R2, anchor 23057172015 (by hand): 3 crossings, 24002107294 late via a side street (+27 m), 19081639913
    excluded (crosses only in the opposite direction).

- **t = 0 at rep 1 / per-rep series** (`validate_reps`; the two most common signatures, 8 newest each, else the 6 newest
  interval sessions, + the newest easy run, which must be excluded): rep 1 at x = 0 (|x| ≤ 1 s, distance 0 m; late
  sessions at their offset) in elapsed / moving / distance, sessions without reps excluded, the elapsed view keeps the
  real breaks (a speed jump at x = rep k start − rep 1 start; reps out of a jog faster than 60 % of the rep speed
  aren't checked: rolling starts have no step), `rep_series` on a 1 s grid from −15 s with a speed jump at x = 0.
  adrian 6×1000 (8 + easy): 0 misses, 40 later reps checked, 49 rep windows; 6×1000 vs 1×800 + 5×1000: 1 late (from rep
  2: elapsed +371 s, moving +247 s, 1054 m), 0 misses. patrik 6 newest interval sessions: 0 of 26 misses (9 rolling
  starts not checked), rep windows 0 misses (17 rolling); clock-jump pair 23044028473 / 21445487763: 21445487763 late
  from rep 2 (match by length), 0 misses.

Then (adrian only):
- **R3 Holmenkollen rollerski climb, all 19 sessions, route axis, 10 m bins:** moving time vs position is
  monotone in all sessions. Away from the common stops (> 150 m), the gap changes per 10 m bin by p99 3.0 s
  and at most 11.5 s. Larger steps (up to ~70 s) only happen at stops, where Adrian mills around at
  > 0.5 m/s, and at a late route join (fixed with the 20 m join margin). Leg moving times equal
  session_groups `legs_table` exactly in all 114 middle legs (median and p90 |Δ| 0 s). The first and last
  legs differ by design: session_groups starts after 20 m of progress and ends 30 m early. Ghost race at the
  finish vs the newest session: +26 s (best) to +874 s.
- **Bislet 6×1000, 8 newest sessions:** at every slot start, in every session, speed 5–15 s after the start
  is > 3 km/h above speed 3–12 s before it (0 misses). Rep times equal groups.json reps (0.00 s). In
  the breaks view every break starts at its common x (0 misses). Breaks are 143–175 s, mostly stand+walk,
  HR drop at 60 s 26–69 bpm.
- **Ski (135 sessions):** 18 ski_intervals; compare's ski reps equal groups.json reps in all 18 (0 differ), no
  other ski session has reps (before: the run detector gave 13–16 random "reps" to 3 R3 climbs). The 11 easy R3
  climbs raise "no reps found". The 7 R3 interval climbs align into slots `1, 1+1, 2, 3, 4` (rest medians 66–225 s,
  HR drop at 60 s 25–36 bpm per session); breaks view 5 break slots. Four no-GPS lap workouts (`7×4:00`,
  `8×4:00`, `4×4:00` ×2) align into 8 slots by duration.
- **Different rep counts:**
  - 6×1000 vs 5×1000 (2022, R55): reps 1–5 matched, slot 6 empty for the 5-rep session. Matched pace
    −6.1 s/km.
  - 6×1000 vs 1×800 + 5×1000: the 800 gets its own slot `0+1`. The anchor's rep 1 is unmatched and the
    1000s pair up 2–6.
  - 6×1000 vs 8×800: nothing matches by distance, so it falls back to index pairing (reps 1–6, plus
    slots 6+1 and 6+2).

## Limitations
- t = 0 at rep 1 keeps real breaks, so only rep 1 lines up exactly; Intervals (and Rep N) line up every rep. With
  different workouts (`match: index`) "rep 1" is formal. Late sessions (no rep 1) enter at the anchor's time of their
  first matched rep, so their gap vs the reference starts at that point, not at 0 at rep 1.
- Common start: a session whose recording begins *off* the anchor's path and joins it mid-route (a lead-in to the middle
  of a loop) isn't a late joiner; if it crosses the line later it is aligned there (adrian R19 2024-12-08: at 3842 of
  4075 s), else excluded. The line is fixed at 40 m (no radius / flag / finish UI yet, `srad=` in the URL works). The
  first 10 s of a late joiner are a standing start against a pacer passing at speed.
- Which rep was skipped in an N vs N+1 session isn't knowable. Ties leave the trailing reps unmatched, so a
  fast last rep of a 5×1000 is compared with rep 5 of the 6×1000, not with its last rep.
- Ski reps exist only for ski_intervals sessions (programmed lap workouts, or hard efforts with clear HR
  recovery; see [session-groups.md](session-groups.md)). Their boundaries are 1 s grid points from the effort
  blocks (no sub-second refinement or lag compensation), and for climbs they follow the route's stops. GAP is
  only computed for runs. Classic and skate are indistinguishable.
- Route axis only works when *all* sessions are in the same compared route (≥ 3 sessions, not laps).
  Otherwise it uses its own distance, and GPS distance error (±1–2 %) then shifts the ghost gap a little.
  Reversed sessions on a route keep their own travel direction (`dir: "rev"`, `reversed: true`, ↺ in the
  table), so their x is the distance from their own start, not the same physical point, and their waypoint
  legs aren't physical either. The coverage guard keeps them from absurd gaps (the R4 2020-04-07 loop jumped
  6 m → 5866 m: now no jump); whether to exclude them, group them or compare them on their own distance is
  still open (plan decision 8).
- Coverage depends on the projection: where it stalls (some out & back / point-to-point sessions turn or
  leave the medoid's line, e.g. adrian R8 2024-08-10 at 9.0 of 17.8 km) the rest counts as not covered.
- With `delta_moving_s`, slow walking (> 0.5 m/s) at stops counts as moving. `delta_elapsed_s` includes
  all stopping.
- The median reference uses the first id as the matching anchor for reps. Per-second medians need at least
  half the sessions present.
- Bislet distance-mode laps use the session's own distance in 400 m steps, not the track geometry, so lap
  splits are GPS-precision only.

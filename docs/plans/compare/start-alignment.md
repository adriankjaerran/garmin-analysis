# Compare: common start ("align starts") — design

The user asked for this: *"make them start on the same GPS location … in case I forgot to start my watch … select which run's
start to use, and other runs wait until I get to their GPS before they start."*

**In short:** you pick an **anchor** session, and its start point becomes a **start line**. That's a short segment
across the anchor's initial heading, with a half-width of `start_radius_m` (default 40 m). For every other session the
engine finds the moment it crosses that line going forward, on the same path. That session's time, moving time and
distance are set to 0 there, and everything before it is cut off (the lead-in). Sessions that never cross the line are
excluded, with a reason. As a generalisation, the line can sit on **any point of the anchor's track** (map flag), and an
optional **finish line** trims the tail.

Prototype: `docs/plans/compare/prototype/start_align.py` (detection), `val_route.py` (checks it against the route
projection), `finish.py` (finish line). Run them with `uv run python docs/plans/compare/prototype/<x>.py …` from the repo
root. Nothing in the repo was changed.

---------------------------------------------------------------------------------------------------------------------

## 1. What the engine does today, and where a late or early watch start breaks it

### elapsed / moving (`scripts/compare.py` `_time_mode`, l. 436–486)
- x = `np.arange(T+1)` or `S["mt"]` (l. 444): seconds since **the file's first record**. Nothing is aligned in space.
  `ahead_m` = own `G["d"]` − reference `G["d"]` at the same x (l. 452–480).
- A session with a lead-in (a warm-up jog to the usual start, or the watch started at home) is behind by the whole
  lead-in. A session whose watch started late is ahead by the part it missed. The offset stays for the whole session.
- Real example, R11 "Bislett loop": the reference 11266836943 vs 12910143619. The second session ran 890 m (324 s) to the
  usual start first. At moving time 300 / 600 / 1200 / 1800 s, `ahead_m` today is −210 / −373 / −504 / −368 m. With
  the starts aligned it is −111 / −235 / −112 / −146 m, which is the real difference.
- The summary rows (`_summary`, l. 321, called on the whole session at l. 410) include the lead-in. The table showed
  9073 m / 2853 s moving, where the aligned values are 8183 m / 2529 s.

### distance, axis "own" (`_distance_mode`, l. 517)
- `d = G["d"] - G["d"][0]`: x = own distance from the file start. It's the same problem: x is shifted by the
  lead-in / the missed part, so `delta_*` and the km legs compare different physical places.

### distance, route axis (l. 506–515, plus `session_groups.project`, l. 928–973)
- The projection gives `tp` = running max of progress along the medoid's 20 m reference. `j0` = progress at the first
  on-route fix. Before that fix, `prog[i] = best = rm[cur]` (sg l. 951/963): the progress of the reference point
  nearest the first fix, usually 0.
- **Late join, j0 > 0** (watch started mid-route): the session starts at `x0 = j0 + 20` (l. 514). The ghost race
  starts at 0 from the first bin both sessions cover (l. 572). That part works, but `elapsed_s` / `moving_s` keep their
  absolute values, and two things go wrong:
  - **Map ghost markers** (`ComparePage.tsx` l. 368–375) compare the reference's absolute `moving_s` with the other
    session's absolute `moving_s`. A session that joined late (12069730602 starts at x = 40 m with `moving_s` = 344)
    stays frozen at its start on the map until the reference's clock passes 344 s.
  - **Starting at a stop**: R3 19475311436 (the watch was started at the km 1.86 stop) has `x0` = 1860. It stood there
    for 526 s, so the first bins show +28 / +56 s of `delta_moving_s` within 20 m (API, today). That's milling-around
    time counted as moving.
- **Lead-in that joins the route at its start, j0 ≈ 0** (`x0 = 0`, no 20 m margin). This is a **current bug**. R11
  12910143619: every second before the join has p = 0, so `nz` (l. 529) maps x = 0 → t = 0, and the 324 s lead-in
  falls into the first 20 m. API today: `elapsed_s` [0, 164, 328, …], `delta_moving_s` [0, +161, +323, …]. The first
  leg "Bislett → km 1.0" shows 647 s vs 276 s (Δ +371 s). Fix, independent of this feature: use only samples from the
  first on-route fix on (`ti >= onix[0]`), or always apply the 20 m margin.
- Route axis needs every session on the same compared route, so it never covers mixed sets or non-route sessions. Those
  fall back to "own", which has no alignment at all.

### intervals / breaks
These are aligned by rep (the detector reps), so the start doesn't matter. Not affected.

---------------------------------------------------------------------------------------------------------------------

## 2. Algorithm (prototyped)

Everything runs on the 1 Hz grid from `session_groups.to_grid`. lat/lon there is already linearly interpolated between
fixes, so 4–6 s smart recording (patrik) needs no special handling. Crossings are interpolated to sub-second times.
Coordinates are local metres around the start point P (`sg.local_xy`).

**Use the GPS path length, not the watch distance field.** At the start of a file the watch distance often grows while
the fixes are still stale. With the R13 / R4 medoids, the heading came out NaN. `geo_d` = cumulative distance between
the 1 Hz positions over `G["gps"]` seconds.

### 2.1 Anchor line
- **Start moment `tA`**: the first second that is not in a pause (`S["paused"]`, i.e. `find_pauses`) and has GPS. If the
  watch was started while standing, the line is where the anchor moved off, not where it stood (R3 19475311436:
  tA = 526 s).
- **Arbitrary point** (`start=lat,lon`): `tA` = the anchor's first second within 30 m of the point, else its closest
  approach. P = that point.
- **Heading h**: unit vector from P to the mean of the anchor's positions at +10…50 m of path.
- **Template**: the anchor's positions at +0…200 m of path in 10 m steps, relative to P.

### 2.2 Per session
1. **Candidates**: forward crossings of the line, with u = (pos−P)·h going from < 0 to ≥ 0 between two GPS seconds. Each
   is interpolated to a sub-second t. The cross-track offset |w| at the crossing must be ≤ R (`start_radius_m`). The
   direction of motion over ±5 s must be within 60° of h.
2. **Path check**. This handles loops, out & back, junctions, parallel streets and reversed passes. Take the session's
   path from the crossing over the next 400 m (2 × template). For each template point, find the nearest point on that
   path (order-free, so milling about at a stop and GPS distance noise don't matter). Accept if the mean distance is
   ≤ 25 m and the nearest-point indices increase in ≥ 70 % of the steps. The order test rejects passing the same streets
   in reverse. The template is shifted by the crossing's offset along the line, so a session crossing 30 m to the side
   isn't penalised.
3. **Pick** the earliest accepted candidate, so for laps and loops it's the first pass. Then, within the same pass,
   take the last forward crossing before the session gets more than 2R from P (or more than 4R of path). That covers
   back-and-forth GPS jitter while waiting at the line.
4. **Departure rule**: if a pause overlaps the stretch from the crossing until the session leaves the 2R circle, the
   start is the end of that pause (`zone_pause_s`). The anchor gets the same treatment (step 2.1), so "started at the
   stop" and "arrived at the stop, stood, left" line up.
5. **No crossing → join fallback.** The first non-paused GPS second within R of the anchor's path, within the first
   `late_max_m` (150 m) of it, where the path check passes from that template index → `kind: "join"` with
   `late_m` = how far past the line it joined. If `late_m` ≤ 20 m, call it `at_line`.
   This covers sessions that started a few metres past the line, or came in from a side street.
6. **Otherwise excluded**, with one of these reasons:
   - `no GPS` (no-GPS lap workouts, treadmill, indoor Bislett)
   - `never within R m of the start line (closest X m)`
   - `crosses the start line only in the opposite direction`
   - `crosses the line but then takes a different way (path differs by X m)`
   - `starts N m after the start line, on the anchor's path` — the watch was started late. The UI should offer "use
     this session's start instead".

### 2.3 Finish line (optional)
P_E = the anchor's last moving GPS second, heading = direction of arrival. For each session: the first forward crossing
after its start, once it has covered ≥ 80 % of the anchor's start→finish GPS distance. Point-to-point runs usually stop
*before* the line, so the fallback is "arrival": the closest approach during the first visit within R. If neither is
found, the session is kept untrimmed with `finish: null` and a reason.

### 2.4 Cost
Warm grids: 19 R3 sessions take 16 ms, 34 R1 laps 5 ms. It needs no disk cache.

---------------------------------------------------------------------------------------------------------------------

## 3. Prototype results (real data)

"proj" = the time each session first reaches the anchor's route position via `st.progress`, the independent route
projection (20 m reference step, 80 m on-route tolerance). Δmov = moving-time difference, found − proj.

**A. Anchor started late — R3 Holmenkollen, anchor 19475311436 (watch started at the km 1.86 stop, line at route 1840 m).**
- All 18 other sessions are found by `crossing`. The line offset is 11–26 m from P.
- 4 sessions passed straight through. Their elapsed and moving Δ vs proj is −7…−16 s, a systematic projection lead.
- 14 sessions stopped there (the common stop). For those the departure rule moves t0 by `zone_pause_s` = 46–250 s.
  Elapsed Δ vs proj is +115…+225 s, because proj takes the *arrival*. Δmov is +23…+55 s: mill-around seconds above
  0.5 m/s at the stop, which both the anchor and the others skip.
- Offsets found (t0, elapsed): 466–781 s. So every other session "waits" until about 8–13 min into its file. **Looks right.**

**B. Anchor started late — R4 Brekke loop (Fauske), anchor = the route medoid 14872637121.**
- It turned out that the medoid itself started its watch about 150 m into the usual route.
- 12 sessions cross its start line at t0 = 44–59 s (one at 82 s after a 38 s stop at the line). Elapsed Δ is ±3 s
  around 49 s, and each crossing is 13–30 m from P. **Consistent: everyone else started ~150 m earlier, from home.**
- 5 sessions are excluded with "starts 60 / 240 / 240 / 430 / 770 / 1370 m after the start line, on the anchor's path".
  Those are their own late watch starts.
- 4749963603 is `dir: rev` in the route grouping. Its first 200 m match, so it is found (t0 = 104 s), which is correct
  for a start line.
- Finish line on the same set: 11 of 14 found (8 by line crossing, 2 by "arrival 1 m short"). Start→finish GPS
  distance 6794–6921 m vs the anchor's 6868. Tails trimmed: 0–50 s.

**C. Lead-in before the usual start — R11 Bislett loop, anchor medoid 11266836943.**
- 12910143619 → `crossing` at t0 = 323.9 s, own distance 890 m, line offset 7.9 m, path error 11 m. Matches the
  projection's join (299 s / 812 m, on-route tolerance 80 m).
- 12069730602 is excluded: `never within 40 m (closest 75 m)`. Checked metre by metre, it runs a **parallel street
  61–104 m away** for the first 400 m, then joins. That's correct for R = 40 m. With `start_radius_m=80` it would be
  accepted, because the line test plus the offset-shifted template would pass. This is the user's knob.
- 9885837163 and 12935044109 run the loop the other way: excluded (closest 107 m / 75 m in the forward direction).

**D. Point anywhere on the anchor's track (map flag).**
- R3 at route km 5: 15 sessions by crossing, 4 by join. Δmov vs proj is +12…+26 s for all 19, a systematic offset of
  about 40 m between the flag and the ref position; the spread is ±7 s.
- R4 at km 3: 16 crossings. Δ vs proj is +5.9…+8.9 s for 15 of them (spread ±1.5 s). 5073438359 is rejected: it
  crosses 2 m from the point and then **turns into a different street 30 m later** (path error 35 m), which is right
  but needs a clear message. 5339905625: closest 42 m.
- R13 out & back, flag at km 2 (out leg): 3 crossings, Δ +6.9 / +8.1 s. The two `rev` sessions are found only via the
  join fallback with a large Δ: **bug to fix**, the join fallback needs the order and direction check too.

**E. Laps — R1 Bislett laps (14 sessions; 18–25 line crossings each).**
- Before I made "same pass" mean "stays within the 2R circle", it chained all the laps together and picked the last
  lap. Fixed in the prototype.
- Now `pass = 1` for all 14. t0 = 5–163 s, except 18440364486 at 625 s / 1517 m: it ran a warm-up outside the track
  first.

**F. Patrik, 5–6 s smart recording (median fix interval 5–6 s).**
- R2 St. Hanshaugen → Oslo-3, anchor 23057172015 (watch started 680 m into the route): 3 crossings + 1 join. Δ vs proj
  −2.3 / −2.6 / −5.0 / +1.0 s. **Interpolation is fine.**
- R2 with a flag at km 4: Δmov +11–12 s for all 4 (one with a 171 s stop at the point).

**Prototype bugs / todo before porting:**
- The join fallback lacks the direction check (D, R13 rev).
- The "starts N m after" message ignores direction.
- The prototype writes `G["_geo"]` into the shared `_Store` grid. In the real code, compute it in the store and cache it
  per session.

---------------------------------------------------------------------------------------------------------------------

## 4. Engine and API integration

### 4.1 Engine
Add `compare(..., start=None | "ref" | <id> | (lat, lon), start_radius_m=40, finish=None | same, late_max_m=150)`.

1. Load the sessions as today, then call `_align(st, sess, start, finish, …)`. It returns, per session:
   `{kind, t0, t1, mt0, d0, late_m, line_offset_m, zone_pause_s, head_s, head_m, tail_s}`, plus the excluded sessions
   (they go to `skipped` with `why`).
   - The anchor defaults to the reference (the first id). With the median reference, use the first id.
   - If the anchor is excluded (no GPS), raise `ValueError("start anchor has no GPS")`.
2. **Window the sessions**: `S' = _window(S, t0, t1)` slices every per-second array of `G` (`d`, `v`, `hr`, `alt`,
   `lat/lon/gps`, `timer`, `stopped`, `up/dn`) to `[floor(t0) … t1]`.
   - `d -= d[0]`, then `+ late_m` (a late joiner sits at its true distance from the line).
   - Re-cut `P` / `paused` and recompute `mt`.
   - Keep `S'["t_off"] = floor(t0)`.
   - Integer-second slicing loses ≤ 0.5 s, which is fine. Don't put windowed sessions into `_Store._sess`; it's shared.
3. After that, **everything downstream runs unchanged** on `S'`: `_summary` (the table shows the aligned window),
   `_time_mode`, and `_distance_mode` own. Two places need the offset:
   - Series `t`: add `t_off`, so it stays file-absolute. The map's `posAt(tracks[id], t)` uses the file's elapsed time.
     Alternatively, return `t0` per session and let the client add it. Absolute is simpler.
   - Route axis: `st.progress` is cached per full session. Slice `tp/on` with the window, and make the anchor's route
     position `pA` = `tp_anchor[tA]` the new x origin. Either the route axis starts at `pA` (`X = arange(pA, L)`), or
     x shifts so the line is 0. **I recommend x = route metres − pA**, so the line is 0 in every mode, and the legs and
     waypoints are shifted the same way, dropping the legs before pA.
   - The existing late-join rule (`x0 = j0 + 20`) stays for sessions joining after the line.
4. **intervals / breaks**: ignore `start` (`align.applied = false`, note "reps are aligned by rep"). Later, optionally
   drop reps outside `[t0, t1]` when a finish is set.

### 4.2 API (`app/server/routers/compare.py`)
New query parameters:

| Parameter | Format | Values / range |
|---|---|---|
| `start` | `^(ref\|\d+\|-?\d+\.\d+,-?\d+\.\d+)$` | the reference, a session id, or a point |
| `start_radius_m` | number | 10–150, default 40 |
| `finish` | same as `start` | |
| `late_max_m` | number | 0–500, default 150 |

All of them go into the `_RESULTS` memo key (l. 67). The response gets an `align` block:
```
align: {start: {lat, lon, heading_deg, anchor_id, t_anchor_s, radius_m, route_m?},
        finish: {...} | null,
        sessions: {"<id>": {kind: anchor|crossing|at_line|join, t0_s, moving0_s, dist0_m, late_m, line_offset_m,
                            zone_pause_s, head_s, head_m, finish_kind?, t1_s?, tail_s?, tail_m?}},
        excluded: [{id, why, closest_m?, late_m?}], applied: bool}
```
The tracks stay the full tracks (the UI greys the trimmed parts using `t0_s` / `t1_s`).

### 4.3 Caching
- `compare.pkl`: unchanged. Reps and projections are per FIT file and the windowing happens afterwards, so
  `CACHE_VERSION` stays the same.
- The `_Store` grids are unchanged. Cache `geo_d` per session in the store.
- Alignment is milliseconds, so no persistent cache. The router memo key covers the new parameters.

---------------------------------------------------------------------------------------------------------------------

## 5. UI

- **Toggle** in the toolbar, next to Reference: `Start: Own | Common`. It writes `start=ref` to the URL. Hidden in the
  Intervals / Breaks modes. Show "Common" as a suggestion when the starts differ by > 100 m, i.e. the first GPS fixes
  are more than 100 m apart; the client can compute that from `tracks`.
- **Choose the anchor**:
  - Each session chip gets a small "start" button, like the existing "ref" button (ComparePage l. 120), which sets
    `start=<id>`. The anchor chip shows a flag ⚑.
  - For an excluded session that "starts N m after the start line", the tooltip offers "Use this start", because it is
    probably the one where the watch was forgotten.
- **Map** (`CompareMap.tsx`):
  - A start-line marker: a flag at P plus a short line across the heading with ±R ticks, so the radius is visible.
  - The flag is **draggable** and snaps to the anchor's track on drop → `start=lat,lon`. Alternatively, "click on the
    anchor track → Start here".
  - Optional finish flag (chequered).
  - Lead-ins (before t0) and tails (after t1) are drawn grey and dashed. Split each track at the `t0_s` / `t1_s` of its
    session.
- **Radius**: a small select, 25 / 40 / 80 m, next to the toggle. The tooltip on an excluded chip suggests it ("closest
  61 m — try 80 m").
- **Charts**: x starts at 0 on the line. Phase 2: a "show lead-in" option puts the pre-start part at x < 0, greyed
  (time modes: x = t − t0; distance own: d − d0), like the intervals view already shows the 60 s before rep 1.
- **Excluded sessions**: faded chips with the reason in the tooltip. They are listed in a note under the ModeNote:
  "Common start at the anchor's start (2025-06-18) · 40 m · 2 excluded: 17359792249 never within 40 m (closest 377 m), …".
- **Tables**:
  - The Sessions table shows totals over the aligned window. Add a "Start" column: "−5:24 · 890 m lead-in", "late
    30 m", "anchor", "at line +0:44". With a finish, also "tail −2:10".
  - The tooltip shows the whole-session totals.
  - The Legs table on the route axis starts at the line, with the first leg partial if the line falls inside a leg.

---------------------------------------------------------------------------------------------------------------------

## 6. Risks, edge cases, effort, plan

### Risks and edge cases
- **Parallel streets** (R11 12069730602 at 61–104 m) and wide areas (squares, tracks): R is a trade-off. A bigger
  radius catches more sessions but can pick the wrong pass on dense street grids. The path check limits the damage.
  Show the radius on the map and let the user change it.
- **Start at a stop or junction**: the departure rule leaves the moving seconds of milling about (> 0.5 m/s) in the
  count, +25–55 s at the R3 km 1.86 stop. A junction right after the line rejects sessions that branch off
  (R4 5073438359), which is correct but needs the "takes a different way" message. Option: a shorter template
  (100 m) for flags placed by the user.
- **Loops and laps**: first pass. If the user wants a later lap, they can drag the flag, but the first pass will still
  win. A possible `pass=k` parameter is out of scope.
- **Out & back**: the return leg passes the line backwards and is rejected by the direction and order checks. The join
  fallback still needs those checks (prototype bug D).
- **GPS lock delay at the start**: stale or jumping first fixes. Use `G["gps"]`, the GPS path length, and the "first
  moving second" anchor rule.
- **Late joiners** (`late_m` > 0): distance is right (it gets `+late_m`), but time misses the unknown first metres. Up
  to 150 m that's ≤ ~40 s, flagged in the table. Excluding them instead is a user choice (open question 1).
- **No-GPS sessions** (lap workouts, treadmill, indoor Bislett) are excluded in the Common mode. They still work in the
  Own mode and in intervals.
- **Median reference**: the anchor = the first id. The median is computed on the windowed sessions, which is fine.
- **Mutating shared state**: windowed copies only. `_Store` grids are shared across requests (the router lock).
- **Different start and route anchor**: on the route axis, the line's route position `pA` comes from the anchor's
  projection (±20 m ref step). Legs and waypoints shift with it.

### Effort

| Part | Size |
|---|---|
| P0 bug fixes: route-axis lead-in at j0 ≈ 0 (`_distance_mode` l. 511–529); map ghost markers using absolute `moving_s` (ComparePage l. 368–375, should use time since the session's first bin) | S |
| Detection (`_align`): port the prototype + direction fix + messages + `validate()` cases A–F | S–M |
| Windowing + time modes + distance own + summary | M |
| Route axis: x origin at the line, legs and waypoints shifted, late joiners | M |
| API parameters, memo key, `align` block, types in `lib/compare.ts` | S |
| UI v1: toggle, chip "start" button, excluded chips, table "Start" column, ModeNote | S–M |
| Map: start / finish flags, draggable snap → `start=lat,lon`, radius ticks, greyed lead-in and tail | M |
| Finish line | S (engine) + S (UI) |
| Lead-in at x < 0 in the charts | M |
| docs/compare.md section | S |

### Recommended phases
1. **Phase 0 (S)**: fix the two current bugs: the R11 12910143619 +323 s jump and +371 s first leg, and the frozen map
   markers for late joiners. These are worth doing even without the feature.
2. **Phase 1 (M)**:
   - Engine `start=ref|<id>` and `start_radius_m` for elapsed, moving and distance (own + route).
   - The `align` block and the UI toggle, chip button, excluded list and Start column.
   - Validation cases A (R3 / 19475311436), B (R4 medoid), C (R11), E (R1 laps), F (patrik R2) in
     `compare.py validate`.
3. **Phase 2 (M)**: map flag with drag → `start=lat,lon`, radius select, greyed lead-ins on the map, finish line.
4. **Phase 3 (S–M)**: lead-in shown at x < 0 in the charts. Optional: trim reps by the window in intervals.

### Open questions for the user
1. Sessions whose watch started **after** the line (e.g. R3 19475311436 starts 1840 m in): exclude them, or keep them
   joined late? Proposal: keep them up to 150 m late with a badge, exclude beyond that, and offer "use as start".
2. Start at a stop: should the clock start at the **departure** (end of the stop, the proposal) or at the arrival on
   the line?
3. Should the start anchor always be the reference (simpler: one "ref" choice), or separate (`start=<id>` independent
   of the reference)?
4. Tables: aligned-window totals only, or both (aligned plus whole session in the tooltip)?
5. Is a finish line wanted in v1, or later?
6. Lead-in: hide it (v1), or show it greyed at x < 0 in the charts too?
7. Default radius 40 m? (Bislett / Oslo parallel streets: 61 m in R11.)

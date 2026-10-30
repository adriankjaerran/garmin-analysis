# Workout comparison (`scripts/compare.py`)

Compares one session with one or many others: "this run vs similar runs / intervals". Library + CLI.
The API will use it via `/api/compare`.

```
uv run scripts/compare.py adrian distance --route R3                 # all Holmenkollen climbs, ghost race vs the newest
uv run scripts/compare.py adrian intervals --sig 6×1000 --place Bislet --last 6
uv run scripts/compare.py adrian breaks 8884710494 8835446895        # 6×1000 vs 5×1000
uv run scripts/compare.py adrian moving 24874084265 24460851059 --reference median --json out.json
uv run scripts/compare.py adrian validate
```
Ids: the explicit ids come first, then `--route` members or `--sig` (+ `--place`) matches, newest first.
`--last N` keeps the first N. **The first id is the reference**, unless you pass `--reference median`.

```python
import compare as cmp                      # scripts/ on sys.path
r = cmp.compare(ids, mode="distance", person="adrian", reference="first" | "median",
                max_points=1500, smooth_s=None, axis="auto" | "route" | "own",
                rep_axis="absolute" | "normalized", gap_s=60, rep_gap_s=30, step_m=None,
                match="auto" | "index", workers=8)          # -> JSON-serialisable dict
cmp.select_ids(person, ids=(), route=None, sig=None, place=None, last=None)   # -> [id, ...]
cmp.validate(person)                                                          # prints the checks below
```

## Data and speed
- `groups.json` (session_groups.py) supplies the session meta, routes, waypoints and the medoid. FIT files
  are re-read with the fast reader on the 1 Hz grid (`session_groups.read_series/to_grid`, ~10 ms each).
  Pauses use `session_groups.find_pauses` (speed < 0.5 m/s for ≥ 15 s, or timer stopped).
- Reps come from `run_analysis.analyse` (detector v2, sub-second boundaries, ~0.6 s per file, in a process
  pool). Route projections come from `session_groups.project` onto the medoid's 20 m reference. Both are cached in
  `data/<p>/cache/compare.pkl`, keyed by FIT mtime+size. With a warm cache a call takes 0.01–0.1 s
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
  second exactly as in session_groups (progress = running max, `dir` per session). Otherwise x = the
  session's own distance (Bislet laps, mixed routes, treadmill). `step_m` = max(10, length / 1500).
- **Per x:** the first time the session reaches x, as `elapsed_s` and `moving_s`. Speed =
  2·50 m / moving time over x ± 50 m (`speed_kmh`, `pace_s_km`). `gap_kmh` is grade-adjusted (runs).
  `hr` is the HR at the time x is reached. `alt` comes from the route reference (route axis) or from the
  session's own altitude.
- **Ghost race.** `delta_moving_s` / `delta_elapsed_s` = own time to reach x − reference time
  (**positive = behind**). It's accumulated per bin from the first bin both sessions cover, so a session
  that joins the route late starts at 0 there. Sessions that join mid-route (first on-route fix > 0) start
  20 m after the join, because the projection waits there until the session catches up. Example, R3, newest
  session (2026-08-10) vs 2026-06-07: −42 s at km 2, −35 s at km 4, +33 s at km 6, +52 s at km 8, +26 s
  at the finish (moving time). In elapsed time the 06-07 session is ahead by 272 s because it stopped less.
  Median reference: increments are the per-bin median, so `median {x, moving_s, speed_kmh, hr}`.
- **`legs`**: `[{from_m, to_m, name, climb_m, sessions{"<id>": {moving_s, pause_s, speed_kmh, hr,
  delta_moving_s (at leg end), d_moving_s (vs reference leg), rank, partial?}}}]`. The legs come from the
  route waypoints (common stops) on a route axis (`legs_kind: route_waypoints`), 400 m laps when every
  session is at Bislet (`laps_400m`), else 1 km (`km`). A session covers a leg if it joins / stops within
  100 m of the leg ends. `route_ref {id, m, alt, lat, lon, waypoints}` is the profile and map line.

### `intervals` (rep-aligned, breaks collapsed)
- **Reps**: detector v2 reps, with strides left out (groups of ≤ 2 consecutive reps < 35 s, the
  same rule as the signature).
- **Matching** against the anchor session (the first id, also with the median reference). It's an
  order-preserving DP. Rep cost c = min(|log distance ratio|, |log time ratio|) when that's ≤ 5 % (same
  prescribed rep, e.g. 1000 m at different speeds or 4:00 at different distances). Otherwise c = the
  max of both. Pairs with c > 0.2 can't match. Cost max(0, c − 0.05), so GPS noise is free. An unmatched
  rep costs 0.25, plus a tiny index-shift penalty so ties leave the *trailing* reps unmatched. If fewer
  than half the reps match (different workouts, e.g. 6×1000 vs 8×800), it falls back to index pairing
  (rep 1 vs rep 1). `rep_summary[id].match` = anchor / distance / index. `match="index"` forces index
  pairing.
- **Slots**: the anchor's reps are slots `1..n`. Unmatched reps of other sessions get extra slots right after
  the anchor rep they follow (`"0+1"` = before anchor rep 1, `"6+2"` = 2nd extra after rep 6).
  `slots [{slot, label, anchor, n_sessions}]`.
- **Timeline** (`series`): slot k starts at `axis.slot_x0[k]` for every session. Its width is the longest
  rep in the slot, or 100 with `rep_axis="normalized"` (x = % of the rep). Breaks are squeezed linearly
  into a fixed `gap_s` (60) between slots, and the 60 s before the first rep are shown at x < 0. Per point:
  `x, t` (elapsed), `speed_kmh` (3 s difference of the distance field, so no watch-speed lag), `hr`,
  `phase` (0 pre, 1 rep, 2 break), `slot`.
- **`reps["<id>"]`** = one row per slot (null if the session has no rep there): `n, start_s, time_s, dist_m,
  speed_kmh, pace_s_km, hr, hr_max, hr_start, hr_end, d_time_s` (time at the reference rep's distance −
  reference time), `d_pace_s_km, d_hr, d_hr_max, d_hr_end`. With the median reference, the per-slot reference
  is in `reps_ref`.
- **`rep_summary["<id>"]`**: `n_reps, rep_time_s, rep_dist_m, rep_pace_s_km, rep_hr, rep_hr_max,
  break_total_s, break_median_s, drop_60_median, matched_slots, d_rep_pace_matched_s_km,
  d_rep_hr_matched` (over the slots both have, so 5×1000 vs 6×1000 compares like with like),
  `det_mode, n_detected, n_used, match`. `no_reps` lists sessions without reps. A ski or easy session alone
  raises `ValueError`.

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
- **Different rep counts:**
  - 6×1000 vs 5×1000 (2022, R55): reps 1–5 matched, slot 6 empty for the 5-rep session. Matched pace
    −6.1 s/km.
  - 6×1000 vs 1×800 + 5×1000: the 800 gets its own slot `0+1`. The anchor's rep 1 is unmatched and the
    1000s pair up 2–6.
  - 6×1000 vs 8×800: nothing matches by distance, so it falls back to index pairing (reps 1–6, plus
    slots 6+1 and 6+2).

## Limitations
- Which rep was skipped in an N vs N+1 session isn't knowable. Ties leave the trailing reps unmatched, so a
  fast last rep of a 5×1000 is compared with rep 5 of the 6×1000, not with its last rep.
- Ski sessions don't get reps: the run detector needs ≥ 2.5 m/s steps. GAP is only computed for runs.
  Classic and skate are indistinguishable.
- Route axis only works when *all* sessions are in the same compared route (≥ 3 sessions, not laps).
  Otherwise it uses its own distance, and GPS distance error (±1–2 %) then shifts the ghost gap a little.
  Reversed sessions on a route keep their own travel direction (`dir: "rev"`), so their x isn't the same
  physical point.
- With `delta_moving_s`, slow walking (> 0.5 m/s) at stops counts as moving. `delta_elapsed_s` includes
  all stopping.
- The median reference uses the first id as the matching anchor for reps. Per-second medians need at least
  half the sessions present.
- Bislet distance-mode laps use the session's own distance in 400 m steps, not the track geometry, so lap
  splits are GPS-precision only.

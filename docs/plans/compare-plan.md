# Compare: alignment and next features — plan

Status: **in progress**: Phase 0, Phase 1 and Phase 2 items 2.2–2.4 done (2026-11-01); Phase 1b, 2.1, 2.5, 2.6 open. Built from three investigations, each run against the real data of
adrian and patrik. The details, with numbers, file and line references and edge cases, are in:

- [compare/start-alignment.md](compare/start-alignment.md): common GPS start / finish line ("I forgot to start my watch")
- [compare/interval-alignment.md](compare/interval-alignment.md): starting every rep at the same time
- [compare/other-ideas.md](compare/other-ideas.md): everything else, ranked
- [compare/prototype/](compare/prototype/): working start-line detector (`start_align.py`) plus its validation scripts

Effort: S ≈ ½ day, M ≈ 1–2 days, L ≈ 3+ days.

## What we found

- **Intervals already does most of what was asked.** In the Intervals tab every rep starts at the same x for every
  session, regardless of warm-up, break length or lap presses. Measured on 8 Bislet 6×1000s, the speed jump lands within
  ±0.5 s of the common start (max 1.3 s spread). Patrik's 4–9 s sampling gives about ±1.5 s, and rolling starts 3–6 s.
  What's missing is around it: the time-based tabs aren't rep-aligned, the run-up to reps 2..n is squeezed 2–4×, and
  there's no per-rep view.
- **A late or early watch start breaks the time and distance tabs.** x counts from the first record in the file. Example
  R11: a session that jogged 890 m / 324 s to the usual start shows −373 m "behind" at 10 min, where it's really −235 m.
  The table totals include the lead-in.
- **The start-line prototype works.** On R3, R4, R1 and Patrik's routes it finds the crossing within 2–16 s of the route
  projection. It correctly excludes a parallel street 61 m away and a session that turns off after 30 m. It takes 5–16 ms
  per comparison, so no cache is needed.
- **Bugs found along the way** (Phase 0):
  1. Route ghost race: on **~15 % of route sessions** (24 / 161 adrian, 4 / 19 patrik) the projection jumps km
     ahead, giving impossible gaps (R30 2026-05-22: −2985 s on 13.9 km). Cause: `session_groups.project` widens its
     search window while a session is off-route and keeps progress as a running max.
  2. Route axis: when a session joins at the route start after a lead-in, all the lead-in time lands in the first 20 m
     (`delta_moving_s` 0 → +323 s).
  3. Map ghost markers compare absolute moving time, so a session that joins late sits frozen at its start.
  4. **10 of patrik's FIT files have clock jumps** (up to 9.4 M s). In 2 interval sessions this hides every rep,
     because all 1500 points are spent on a fake break.
  5. Intervals timeline: the 1500-point thinning puts most points on breaks, so reps arrive at 2 s resolution.
     A session missing a rep draws its break through that rep's shaded area.
- **Sampling isn't treated fairly.** At 5 s sampling, interpolated standing seconds count as moving: adrian's 6×1000
  thinned to 5 s gets moving pace +3 %. Patrik's GAP on the flat track is off by −7 to +3 s/km because of GPS
  altitude noise.
- **Adrian and Patrik ran together 24 times** (6 Tuesday Bislet interval sessions, a race, easy runs). Comparing across
  people isn't possible today.

## Plan

### Phase 0 — correctness fixes (≈ 2–3 days, do first) — **done 2026-10-31**
Each is useful on its own, and everything below builds on them. What was built and the numbers are in
[compare.md](../compare.md) (Coverage, Start, Intervals timeline, Validation) and
[session-groups.md](../session-groups.md#fit-time-base-read_series).

| # | Fix | Where | Effort |
|---|---|---|---|
| ✅ 0.1 | Clock jumps: split / clamp time gaps in the shared FIT reader, guard in compare | `session_groups` reader, `compare.py` | M |
|  | *Done:* `gps_quality.clock_jumps` (excess of record span over the session's elapsed time, taken out of the longest ≥ 600 s gaps) used by `read_series`, `run_analysis._read`, `fitcache.read_fit`; compare builds break points per x unit. Patrik 10 files, adrian 2 | | |
| ✅ 0.2 | Ghost race jumps: cap the projection's jump window, mark jumped stretches as uncovered (no time gained), coverage % + "gap at last common point" | `session_groups.project`, `compare._distance_mode` | M |
| ✅ 0.3 | Route-axis lead-in dumped into the first 20 m; ghost markers on the map use time since the session joined | `compare._distance_mode`, `ComparePage.tsx` | S |
| ✅ 0.4 | Intervals timeline: thin breaks, not reps; leave empty slots empty | `compare.py` intervals series | S |
|  | *Done:* projection jump window ≤ 200 m + 2 × own distance; jumps / off-route / stalls / too-fast bins not covered (gap held); `coverage_pct`, `uncovered`, `gap_end`, `reversed` per session; time from the first on-route fix and from when the session leaves its start; map markers use time since each session's start; reps never thinned, empty slots left empty. Ghost-race scan: adrian 24/161 → 0, patrik 3/19 → 0/29. Reversed sessions kept in their own direction (decision 8 still open) | | |
| ✅ 0.5 | Add each case above to `compare.py validate` | `compare.py` | S |

### Phase 1 — common start line (your main ask) (≈ 3–4 days) — **done 2026-10-31** (Phase 1b open)
*Done:* `compare(start=ref|<id>|(lat, lon), start_radius_m=40)` (`_start_line`, `_find_start`, `_join`, `_window`,
`_align_starts`), API `start` / `start_radius_m` (in the memo key) and the `align` block, URL `start=` (+ `srad=`),
Start Own / Common toggle, ⚑ "use this start" on each chip, late / excluded chip badges, Start column + whole-session
tooltips, ModeNote, lead-ins dashed on the map. Late starters kept at any distance (decision below), joins need the
direction check, and on a shared route a late joiner must run the route the same way as the anchor (R13 reversed:
excluded). `start=lat,lon` works in the engine / API (no map flag yet). Details and numbers:
[compare.md](../compare.md#common-start-line-start). Not done (1b): map flag, radius select, finish line, lead-in at x < 0.
- **Engine.** Pick a start anchor (default: the reference session). Its first moving GPS fix becomes a **start line**
  across its initial heading (half-width 40 m). For each other session, find the first forward crossing that then
  follows the anchor's next 200 m. That moment becomes t = 0 and d = 0, and the lead-in is cut off. If the session is
  standing at the line, the clock starts when it leaves. Sessions that join just past the line (≤ 150 m) are kept as
  "late"; others are excluded with a reason. Each session is cut to its window, then the existing modes run unchanged.
  The route axis shifts by the line's position. Intervals and Breaks ignore it, since they are rep-aligned already.
- **API.** `start=ref|<id>|lat,lon`, `start_radius_m`, `late_max_m`, all included in the response cache key. The
  response gets an `align` block: per session the offset (s, m), distance to the line, late / excluded and why.
- **UI.**
  - A **Start: Own / Common** toggle.
  - A **"Use this start"** action on each session chip, so you can pick whose start counts (e.g. the run where you
    forgot the watch).
  - Excluded sessions are greyed with their reason.
  - A **Start** column in the sessions table ("−5:24 · 890 m lead-in"); totals are the aligned ones.
- **Phase 1b (M):** a draggable **start flag on the map** (any point on the anchor's track) with the radius drawn,
  plus an optional **finish line** that trims the tail the same way. The lead-in is shown greyed at x < 0 in the charts.

### Phase 2 — intervals: "every rep starts together" done fully (≈ 3–5 days) — **2.2–2.4 done 2026-11-01**
*Done:* `compare(start="rep1")` (`_align_rep1`: intervals-mode matching via `_reps_slots`, rep 1 at x = 0 to the
sub-second, real breaks kept, distance mode on the own distance; no rep 1 but a later matched rep = late, entering at
the anchor's time of that rep; no reps / not an interval session = excluded), `compare(rep_series=True, rep_lead_s=15,
rep_post_s=30)` (`_rep_windows`: every rep on a 1 s real-time grid), API `start=rep1`, `rep_series`, `rep_lead_s`,
`rep_post_s` (memo key). UI: Start **Own / Common / Rep 1**, Intervals **Show: All / Per rep / R1 … Rn** (`view=reps`,
`rep=<slot>`), Breaks **Break: All / B1 …**; map zoomed to the selected rep / break, ladder and table column
highlighted. `validate_reps` (0 misses for adrian and patrik). Details: [compare.md](../compare.md#t--0-at-rep-1-startrep1).
| # | Item | Effort |
|---|---|---|
| 2.1 | **Real-time lead-in before every rep** (~15 s at true speed instead of squeezed), so each rep's start is readable | S–M |
| ✅ 2.2 | **Rep N selector**: show just rep 3 of every session, starting at 0 | S |
| ✅ 2.3 | **"t = 0 at rep 1"** option in Elapsed / Moving / Route position | S |
| ✅ 2.4 | **One small chart per rep**, all sessions overlaid | M |
| 2.5 | **Fatigue view**: every rep of one session overlaid (rep 1..6 on top of each other) | M |
| 2.6 | Start anchor option **"up to speed"** (50 % of rep speed) for rolling starts / Patrik's sampling; lap presses as tick marks | M / S |

The lap button isn't proposed as the main anchor: it's +0.5 s median for adrian, but Patrik pressed it on only 26 of 57
reps and was up to 8 s off. Neither is aligning on a GPS spot (2–4 s error).

### Phase 3 — compare across people (≈ 3–5 days)
- Ids with a person (`adrian:123,patrik:456`). Project everyone onto the reference person's route. Make caches and
  route ids per person (adrian R1 ≠ patrik R1). Show HR as % of HR reserve.
- A **"Ran together"** suggestion set (same time and place, 24 cases today).
- Depends on 0.1 / 0.2. Patrik's HR zones look like age defaults, so his max / resting HR needs checking (see decision 6).

### Phase 4 — other improvements, ranked (pick as wanted)
| P | Idea | Effort |
|---|---|---|
| P1 | **Compare a selected stretch**: drag-zoom the chart, get a per-session table for that stretch, optionally reset the gap to 0 there. Plus `reference=best` ("race my PR") and `reference=previous` | S–M |
| P1 | **Fair sampling**: pause detection that knows about sampling gaps, ⚠ when sampling is mixed, GAP from the route's reference altitude (flat on the track) | S–M |
| P1 | **Common yardsticks in the table**: fitness score, % HR reserve, aerobic decoupling, HR drift (already in `fitness.json`). Grey out rep deltas when reps were paired by number with very different lengths (e.g. 6×1000 vs 15×400) | S |
| P2 | **Route history**: time / GAP / efficiency against date, PR steps, per-leg trends (needs 0.2) | M |
| P2 | GAP toggle in charts (the engine already sends it), HR-vs-speed scatter, HR drift chart | S–M |
| P2 | Best efforts per session (400 m – 10 km) and a stretch leaderboard | M |
| P2 | Map hover moves the chart cursor; ▶ animated ghost race | S–M |
| P2 | Power, cadence and stride in charts / reps table (adrian's newer watch only) | M |
| P2 | Auto-detected shared stretches between *different* routes | L |
| P3 | CSV / PNG export (S); weather (M, external API); Bislet track geometry for lap splits (M) | — |

## Decided (2026-10-31)
- **Order:** Phase 0 fixes → Phase 1 (toggle + "use this start" chip; map flag / finish line later) → Phase 2 items
  2.2, 2.3 and 2.4.
- **Late starters are kept.** A session whose watch started after the start line joins the race where its own recording
  begins on the anchor's path. Its gap starts at 0 there, and it's compared "from there onwards" (no distance limit,
  marked *late* with the join distance). Sessions that never get on the anchor's path are still excluded, with a reason.
- **Start at a stop:** the clock starts when the session **leaves** the line.
- **Intervals:** both. "t = 0 at rep 1" in Elapsed / Moving / Route position (2.3), plus a Rep N selector and one chart
  per rep (2.2, 2.4).

## Follow-ups found while building
- **Route projection gets stuck on some out & back / point-to-point routes.** Phase 0's coverage % made this visible:
  median coverage is 91 % for adrian and 66 % for patrik (e.g. patrik R2 stuck at 7.0 of 10.4 km, adrian R8 2024-08-10
  at 9.0 of 17.8 km). Fix in `session_groups.project` (M).
- `gps_quality.load` doesn't use the clock-jump fix yet (S).

## Decisions still open (later phases)
1. **Start line, late starters:** if a session's watch started after the line, keep it as a "late" joiner (≤ 150 m,
   with a badge) or exclude it? *Proposed: keep ≤ 150 m, exclude beyond.*
2. **Start at a stop:** start the clock when a session arrives at the line or when it leaves? *Proposed: when it leaves.*
3. **Start anchor:** always the reference session, or chosen separately? *Proposed: separate, defaulting to the reference.*
4. **Phase 1 scope:** include the finish line and the map flag in v1, or ship toggle + chip first? *Proposed: toggle +
   chip first.*
5. **Intervals:** in which tab did the starts not line up for you? If Elapsed / Moving, do you want real breaks with
   t = 0 at rep 1 (2.3), or every rep at the same x (the Intervals tab)? And which per-rep view first: 2.2, 2.4 or 2.5?
6. **Across people:** URL form (`ids=adrian:123,patrik:456`, or plain ids looked up across everyone)? Does Patrik know his
   real max / resting HR?
7. **Fair sampling:** the pause fix changes stored moving times for 5 s sessions, so they no longer match Garmin's
   numbers. OK?
8. **Reversed sessions on a route:** exclude them from the ghost race, give them their own group, or compare them on
   their own distance?
9. **Weather:** OK to call an external weather service (with a local cache), or keep the app fully offline?

## Suggested order
Phase 0 → Phase 1 (toggle + chip) → 2.1–2.3 → Phase 4 P1 items → Phase 3 → the rest.

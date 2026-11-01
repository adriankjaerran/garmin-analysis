# Interval alignment: "start all the intervals at the same time"

User request: *"and for intervals one option to start all the intervals on the same time even if some difference on each
in when we start it"*.

All numbers below come from the running API (`/api/<p>/compare?mode=intervals`) and from `run_analysis.analyse` on the
FIT files, measured 2026-10-31. Scripts: `/tmp/cmpwork/{a_press,b_api,c_trace,d_ramp}.py`.

## TL;DR
- **The Intervals view already starts every rep at the same x.** The engine sets x = `slot_x0[k] + (t − rep.start)`
  for every session, so the rep starts line up exactly, whatever the warm-up or break lengths. Compare → Intervals is
  also the default view for interval sessions (`suggest` returns `mode: intervals` for every set).
- On Adrian's Bislet 6×1000 the visible speed jump (the 50 % crossing) is within **±0.5 s** of the common start
  (p10..p90 −0.35..+0.50 s, max 1.2 s; spread across 8 sessions per rep: median 0.9 s, max 1.3 s). On Patrik's
  4–6 s data it's within ±1.5 s for most reps, but rolling starts are 3–6 s off.
- What falls short is everything *around* the rep starts:
  1. Elapsed, Moving and Route-position views aren't aligned at all. Rep 1 starts between 2 s and 850 s into the
     session (8 Bislet sessions).
  2. The run-up to each rep is squeezed. The last seconds before a start are compressed 2–4× (breaks are 130–220 s,
     drawn as 60 s), so the "getting ready" part doesn't line up in real time. The 60 s before rep 1 is in real time.
  3. A session that has no rep in a slot draws its break stretched right through that slot's shaded rep area.
  4. **Bug (Patrik):** FIT clock jumps (up to 9.4 million s) leave 2 interval sessions with **0 rep points** in the
     Intervals chart.
  5. The server thins every series to 1500 points, and breaks take up most of them, so reps arrive at 2 s
     resolution. That's coarse for zooming in on a start.
  6. There's no view per rep, no "rep N only", and no "all reps of one session on top of each other".

## 1. Current behaviour, verified

### How the engine builds the timeline (`scripts/compare.py::_rep_timeline`)
- Slot k starts at `X0[k]`. Its width is the longest rep in the slot (absolute) or 100 (normalized).
- Rep point: `x = X0[k] + (t − start)` (or `· 100/time`). Starts and points are sub-second because they come from
  `r["start"]`, which is detector v2's refined, lag-compensated boundary.
- Break: linearly squeezed from `[rep end, next start]` into `[X0[k]+W[k], X0[next]]`.
- Pre: the 60 s before the first rep at `x < 0`, in real seconds. In normalized mode it's scaled by
  `gap_s/60`, which is 1:1 at the default.
- Speed is `_rep_speed`, a centred 3 s difference of the distance field, so it has no lag. The frontend adds a centred
  ±2 grid-point smoothing (`smoothN`, sk=2), which doesn't shift anything. The in-rep gap panel (`repGap`) starts each
  rep at 0 by construction.
- Map markers in Intervals use `posAt(track, t at cursor)`, so they show every session at the same time since its own
  rep start. That's already rep-aligned (to within ±0.5 s, because the series `t` is rounded to whole seconds in `_pack`).

### Test A: 8 × Bislet 6×1000 (track + GPS, FR935 / 4315), long vs short warm-up
ids `24874084265,24460851059,23940996175,23853808371,23107809976,22777491503,20689580586,19252924176`

- All 6 slots are matched by distance.
- Rep 1 starts at 2 s, 35 s, 426 s, 425 s, 461 s, 580 s, 669 s and 850 s of elapsed time. In Intervals they all sit at
  x = 0.
- Visual onset (50 % crossing of the speed step) relative to `slot_x0`, per rep:

| slot | spread across 8 sessions |
|---|---|
| 1 | 1.1 s |
| 2 | 0.7 s |
| 3 | 0.4 s |
| 4 | 0.8 s |
| 5 | 1.0 s |
| 6 | 1.3 s |

  Over all reps: median +0.07 s, p10 −0.35 s, p90 +0.50 s. The API series and the raw 1 Hz signal agree within about
  0.4 s.
- **Start spot on the track, same slot:** 0–18 m from the anchor's spot (GPS noise; the 4315 sessions in track mode
  are 0–4 m off). Odd and even reps start 112 m apart, because 1000 m is 2.5 laps. That's correct.
- **Pre segment:** 60 s for 6 sessions, 35 s for 20689580586, and **2 s for 19252924176**: the watch was started at
  the line, so there's nothing to show before rep 1.
- **Elapsed view for the same 8 sessions:** rep 1 is spread over 848 s. Even with t = 0 at rep 1, the rep starts drift
  apart because breaks differ:

| rep | spread of start times (t = 0 at rep 1) |
|---|---|
| 2 | 50 s |
| 3 | 99 s |
| 4 | 114 s |
| 5 | 179 s |
| 6 | 224 s |

  So real-time breaks can't keep the reps aligned. Squeezing (or a fixed lead-in, see §3) is needed.

### Test B: 6×1000 vs 5×1000 (2022, FR935, smart recording) + a 2026 track session
ids `8884710494,8835446895,24874084265`

- Reps 1–5 are matched and slot 6 is empty for the 5-rep session.
- Onset median +0.62 s, max 1.1 s, per-slot spread 0.7–1.2 s. The 2022 FR935 sessions are about +0.8 s late relative
  to the 2026 track session.

### Test C: Patrik (4–6 s recording; median record gap 3–4 s, p90 8–9 s)
ids `24795299858` (10×400), `21445490294` (10 mixed), `24444367861` (9 mixed). Different workouts, so `match: index`.

- **Onset:** median +0.9 s, p10 −3.9 s, p90 +2.2 s. The per-slot spread is median 3.1 s, worst 11–20 s.
- **What's behind the outliers** (`c_trace.py`):
  - Slot 1: a run-up at 16 km/h, a stop, then the start. The detector start is correct; my crossing estimator was
    fooled.
  - Slot 8 of 24444367861: a *rolling* start, 13 → 18 km/h over about 10 s. "The start" is genuinely ambiguous by
    5 s: the detector marks the onset, the 50 % point comes 5.7 s later.
- **Clock-jump bug:** 23044028473 and 21445487763 have FIT timestamp jumps of 9.4 million s and 1.9 million s in the
  middle of the session. Patrik has 10 sessions with gaps over 6 h.
  - The break before the jumped rep becomes a 9.4-million-second "break". `np.arange` builds it at 1 s steps, the
    stride keeps 1500 points, and **the series is 1499 break points and 1 pre point: no rep is drawn at all**.
    `compute_s` is 2.3 s and `elapsed_s` is 86400.

### Test D: ski (R3 interval climbs, `ski_effort`, 1 s boundaries, no lag compensation)
ids `23925803195,20295806648,19230867986,19136121015`

- Onset median +0.7 s, p90 +2.3 s, max 3.3 s; per-slot spread median 1.8 s, max 5.9 s.
- The reps start at different places on the climb (up to 765 m apart), so "same start line" means nothing for these
  ski sessions.
- **Slot without a rep:** with all 7 R3 interval climbs, slot `1+1` exists in only one session. The other 6 draw their
  break *through* the shaded 701 s rep area, stretched about 4×.

### Where the user would see "not starting at the same time"

| Where | What they see | Size |
|---|---|---|
| Elapsed / Moving / Route-position views of interval sessions | rep 1 at a different x per session; rep 6 drifts further | 2–850 s (rep 1); +224 s more by rep 6 |
| Intervals, the seconds before each rep | run-up squeezed (break 130–220 s → 60 s), so the stand-still → go isn't comparable in real seconds | 2.2–3.7× compression |
| Intervals, rolling or flying starts (Patrik: 10 of 57 reps; jog → rep) | ramp of 5–10 s; the start is ambiguous | 3–6 s |
| Intervals, sparse sampling (Patrik, FR935 smart recording) | the ramp is linearly interpolated across 4–9 s record gaps | ±1–2 s |
| Intervals, a session missing a slot | break line through a shaded rep area | — |
| Intervals, Patrik sessions with clock jumps | reps don't render | 2 of 14 interval sessions |
| Zooming into a start | server stride is 2 s, client grid ≈ 1.1–1.3 s | 1–2 s |
| HR panel | HR rises 20–40 s after the start (physiology, not alignment) | — |

## 2. Rep start precision

- **Detector v2:** change point → a sub-second fit to the raw distance records within ±10 s → lag compensation
  (GPS/FR935 1.18 s, GPS/4315 0.49 s, track 0 s).
- Stated uncertainty: median 0.7 s (Adrian), 2.6 s (Patrik). On the 58-session Bislet evaluation, median |start error|
  vs the lap press is about 0.45–0.5 s.

### Lap press minus detected start (`press_start_dt`, main reps only)

| person / mode | reps | press within 10 s | median | p10 | p90 | max \|.\| |
|---|---|---|---|---|---|---|
| Adrian, track (4315) | 36 | 36 | **+0.88 s** (press later) | −0.09 | +1.96 | 3.66 |
| Adrian, GPS (FR935 / 4315) | 35 | 35 | +0.18 s | −0.48 | +1.16 | — |
| Adrian, all | 71 | 71 | +0.53 s | — | — | 3.66 (\|median\| 0.63, p90 \|.\| 1.74) |
| Patrik, GPS (3077) | 57 | **26 (46 %)** | −0.60 s | −4.5 | +0.8 | 8.25 |

- Adrian is within 1 s on 52 of 71 reps. For Patrik, half of the reps have no manual press: he uses auto 1 km laps or
  programmed workouts. 24444367861 and 23044028473 have no manual laps at all, and the nearest auto lap is 10–125 s
  away.

### Shape of the speed step (raw distance, 3 s centred difference)

| person | t10 | t50 | t90 | rise 10→90 % |
|---|---|---|---|---|
| Adrian | −1.4 s | **+0.2 s** | +1.7 s | median 3.6 s, p90 5.0 s (no flying starts) |
| Patrik (48 of 57 have a clear step) | −1.8 s | **+1.0 s** | +3.0 s (p90 +7.6 s) | median 5.4 s, p90 10.7 s (10 flying starts) |

### Alignment anchors compared

| Anchor | Precision | Availability | Verdict |
|---|---|---|---|
| **Detected start** (today) | ±0.5 s (Adrian); ±1–2 s, 3–6 s on rolling starts (Patrik) | every rep | keep as the default |
| **Lap press** | Adrian's press is a human reaction, +0.5 s median, up to 3.7 s; Patrik −0.6 s median, up to 8 s | Adrian ~100 %, Patrik 46 % (otherwise falls back to detected) | option at most; better as small tick marks ("you pressed 1.9 s late") |
| **Same GPS spot as the reference's start** | GPS 0–18 m ≈ 2–4 s at accelerating speed; track mode snaps distance but not lat/lon | only when reps start at the same place (Bislet yes, road / ski no: 0.1–4 km apart) | not recommended |
| **Speed reaches 50 % of the rep speed** (or a fixed pace X) | robust to rolling starts and ramp shape; "who got up to speed first" | every rep with a step (Patrik: 48/57) | worth having as a 2nd option ("Align: start / up to speed") |

## 3. UX options

| # | Option | What it fixes | Effort |
|---|---|---|---|
| A | **Fix clock jumps.** Collapse FIT timestamp gaps over about 1 h (while the timer is running) into a pause in `session_groups.read_series` / `to_grid`, or at least guard the compare timeline. | Patrik's 2 invisible interval sessions; elapsed 86400 | S (guard) / M (in `read_series`: touches groups / detectors, needs regenerating caches) |
| B | **Thin the breaks, not the reps.** Sample break points per x unit (≈ `gap_s` points), keep reps at 1 Hz; or raise `max_points` for intervals. Keep `t` to 0.1 s. | 2 s → 1 s rep resolution; sharper starts | S |
| C | **Real-time lead-in / lead-out** (`lead_s`, e.g. 15 s). The last `lead_s` before every rep start and the first `lead_s` after every rep end are drawn in real seconds; only the middle of the break is squeezed. Ticks −15 s / 0. | every rep's run-up and start line up in real time, as rep 1 already does | S–M (engine + tick labels; gap ≥ 2·lead) |
| D | **Empty slots: no stretching.** A session's break goes only into the gap regions and is null across a slot it doesn't have. | ski `1+1`, 5×1000 vs 6×1000 mid-gaps | S |
| E | **"Rep N" selector / zoom.** Chips R1..Rn plus "All" that set the x range to `[slot_x0 − lead, slot_x0 + width + lead]`. With B and C it's at full resolution. | "show me rep 4 only" | S (client only) |
| F | **Per-rep small multiples.** One small chart per slot, x = s since the rep start (−lead..width), all sessions overlaid; pace + in-rep gap. | clearest "every rep starts at 0" picture | M (client; needs C, or a per-point `rt` = seconds since the rep start) |
| G | **One session, all reps overlaid** (fatigue view): rep 1..n of one session, coloured by rep number, x from the start. Also a "this session vs its own rep 1" gap panel. | pacing / fatigue within one workout | M (client transform of the same series; a toggle when 1 session is pinned) |
| H | **Align toggle:** "Rep start (detected) / Up to speed (50 %) / Lap button". Applied as a per-rep start shift in the engine (`align=`); the rep's end and time stay as detected. | rolling starts (Patrik), lap-press preference | M (50 %: S–M in the engine; lap: M, needs `press_start_dt` / lap times kept in the `_rep_job` cache with a version bump, plus a fallback) |
| I | **Elapsed / Moving / Route: "t = 0 at rep 1"** (shift each session by its first rep start, real breaks kept). | warm-up differences in the real-time views (848 s spread) | S (engine `t0=rep1` for interval sessions, a toggle) |
| J | **Lap-press ticks** on the Intervals chart, plus a "pressed late / early" note per rep in the table. | shows the watch vs detection difference | S |

The in-rep gap chart exists, is correct (0 at every rep start, ≈ the rep time difference at the end) and is hidden in
normalized mode. It depends on the start anchor: if a session's start is placed 1 s late, its whole gap curve (and its rep
time) moves by about 1 s. With "up to speed" alignment the different acceleration ramps drop out, which is arguably
fairer for rolling starts.

## 4. Edge cases

- **Different rep counts / missing reps:** slots handle them, and the rep starts stay aligned. Option D cleans up the
  drawing. With an index fallback, "rep 1 vs rep 1" may be different workouts (all of Patrik's are), so alignment is
  formal there.
- **Strides:** excluded (≤ 2 consecutive reps < 35 s). A stride just before rep 1 lands in the pre segment and can look
  like a false start. Fine.
- **Ski:** 1 s boundaries, no lag compensation, onset spread up to 5.9 s; reps start at different places (GPS-spot
  alignment is useless). The "up to speed" anchor is less meaningful on climbs, so use the detected start.
- **4–6 s sampling (Patrik, FR935 smart recording):** linear interpolation over record gaps blurs the ramp. The
  detector's sub-second fit is limited by the records (uncertainty 2.6 s). Option H (50 %) is the most stable
  visually. Show the uncertainty in the tooltip?
- **Reps by time vs distance:** start alignment is the same. Time reps also end together (absolute axis);
  distance reps end together in "% of rep". The gap chart covers distance reps.
- **Watch started at the line** (19252924176: 2 s pre): pre is empty. Fine.
- **No-GPS / indoor lap workouts:** reps = laps (indoor_bislett / ski_laps), so the start = the press by definition.
  No speed, so "up to speed" isn't available; it falls back.
- **Clock jumps:** see A. Any rep after a jump has a start in the millions of seconds.

## 5. Recommended plan

| Phase | Items | Effort | Notes |
|---|---|---|---|
| 1. Fixes | A (guard in `_rep_timeline`: build break points in x, never `np.arange` over real break seconds), B, D | S each, ~½ day | Plus A properly in `read_series` once agreed |
| 2. What the user asked for | C (real-time lead-in at every rep, default ~15 s), E (rep selector), I ("t = 0 at rep 1" in the real-time views) | S–M, ~1 day | |
| 3. Per-rep views | F (small multiples), G (one session's reps overlaid) | M each | |
| 4. Anchors | H "Up to speed (50 %)" (+ fixed pace X?), J lap ticks; "Lap button" alignment only if the user wants it | M | |

## Open questions for the user

1. Which view were you in when the starts didn't line up: Intervals (already aligned), or Elapsed / Moving / Route
   (not aligned)? Do you want real-time breaks with t = 0 at rep 1 (I), or every rep on the same x (today's Intervals,
   plus C)?
2. "Start": the speed jump (today, ±0.5 s), your lap press (Adrian: on average 0.5–0.9 s later, up to 3.7 s), or
   "up to speed"? Patrik often has no presses.
3. Is a 15 s real-time lead-in before every rep the right size, or do you want the whole break in real time,
   left-aligned at the rep end?
4. Per-rep small multiples vs one selected rep: which comes first? Is the fatigue view (one session's reps overlaid)
   wanted?
5. Clock jumps (Patrik, 10 sessions): fix in the shared reader (affects groups, the detectors and caches), or just
   guard compare?

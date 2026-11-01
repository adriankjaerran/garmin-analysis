# Together: workouts run with others — plan

Status: **T1, T2 and T3 done** (2026-11-01; [../together.md](../together.md), pages in [../app.md](../app.md)), T4 open. Built from two investigations on the real adrian × patrik data:

- [together/detection.md](together/detection.md): how "ran together" is detected (rules, thresholds, every case checked)
- [together/page-design.md](together/page-design.md): list page and session page, HR choices, engine and API, layouts
- [together/prototype/together_proto.py](together/prototype/together_proto.py): working detector
  (`uv run python -I docs/plans/together/prototype/together_proto.py --json /tmp/pairs.json --groups /tmp/together.json`)

The goal, in the user's words: after e.g. a 6×1000 at Bislett, compare how each of us did on every rep, see us run around
the track and the distance between us, the race standings and the cumulative total over the reps, a little heart rate
(not clutter), and a list of every workout with at least two participants. More people will be added later, so
everything works for N people.

## What we found

- **27 candidate pairs → 17 joint workouts**, all checked by eye: 5 same workout (Bislett 2026-10-20 15×400 vs
  10×400, 10-15, 10-06, 2025-11-11, 2024-04-14), 4 together (easy runs, hikes), 1 partly together (met after 15 min),
  1 same race (Lier 2023-05-07), 6 unverified (patrik has no FIT for a July 2023 trip).
- **"Together" ≠ "same workout".** On 2026-10-27 patrik ran a steady run while adrian did the 6×1000, so only the
  cool-down was together. They're the same place and time but a different workout, and aren't counted as together.
- **Distance alone fails on a track.** Two people doing different workouts at Bislett are within 100 m 77 % of the
  time; two people doing the same 400s are within 25 m only 38–47 % of the time. Detection is based on **reps followed**:
  the other person speeds up within ±12 s and ≤ 60 m of the rep start.
- **Pair reps by clock time across people**, not by length: starts agree within ±5 s, even when the rep detectors
  disagree on the labels.
- **Score the set distance, not GPS distance**: patrik's 400 m reps measure 392–416 m, adrian's track mode gives exactly 400.
- **"Metres apart" on a track** must be the along-track gap (distance since the rep start, scaled to the set distance).
  Straight-line distance breaks past about 60 m on a 400 m oval.
- **Watch clocks differ** by about −1 s (patrik vs adrian's FR935) and +2.3 s (vs adrian's newer watch), ≈ 10 m of fake
  gap at running pace. They're estimated per pair from runs side by side.
- **The index has wrong start dates** for 12 activities (clock jumps), so detection takes the start time from the FIT.
- **Patrik's HR settings are defaults** (max 207 from zones, no resting HR).
- **Places and labels differ per person** for the same session (adrian's "Bislet" vs patrik's "St. Hanshaugen"), so a
  group gets its own place and label.

## Decided (defaults chosen so the build can go on; easy to change)

| # | Question | Default |
|---|---|---|
| 1 | What counts as together | Only same workout, together, partly together and same race. "Same place, different workout" (a shared warm-up or cool-down) is **hidden by default**, with a "Show near-misses" filter. |
| 2 | Unverified (no GPS / no FIT) | Listed, greyed, with a "no track" tag. They have no session page beyond the summary. |
| 3 | Partly together | Listed, with the span ("together 15–42 min"). |
| 4 | Sports | All sports are detected; the list has sport chips and defaults to **all**. |
| 5 | Ranking with different rep counts | Over the reps **everyone ran**. Extra reps are shown but not ranked. |
| 6 | Rep close-up default alignment | Each person's **own rep start** (fair, about ±2 s noise); a toggle switches to **clock time** (a late start counts). |
| 7 | HR settings | Optional hand-edited `data/<person>/profile/hr_settings.json` `{max_hr, rest_hr}`. Without it, % of max HR from the zones with a ⚠. |
| 8 | People config | `data/people.json` `{id: {name, colour, initials}}`. Optional, so new people get a colour automatically. No avatars, no network. |
| 9 | Head-to-head record | Shown per pair at the top of the list (e.g. "Adrian 4–1 Patrik on shared reps"). It can be hidden later if it feels too competitive. |
| 10 | URLs | `/<person>/together` and `/<person>/together/<gid>`. The group is global; the prefix only means "viewing as". The person switcher keeps the gid. |
| 11 | Group id | `YYYY-MM-DD_HHMM_<place>`, saved in `data/together.json` and kept on rebuild. |

## Plan

### T1 — detection and data (≈ 1–2 days) — ✅ done ([together.md](../together.md#detection))
- `scripts/together.py <build|list|show gid>`: the prototype made robust.
  - FIT start times; candidates by time overlap + start / track proximity; rules and labels per detection.md.
  - Groups as connected components (N people); per-pair clock offsets (fallback: median for the watch pair).
  - Stable gids; incremental rebuild.
- `data/together.json` (cross-person, not a person folder) + `data/people.json` (optional).
- Pipeline: after `session_groups` / `similarity` for everyone. The app reloads the file when it changes, like
  groups.json.
- Activity rows get `together: {gid, label, with: [person]}` so pages can link.

### T2 — engine and API (≈ 2 days) — ✅ done ([together.md](../together.md#session-engine), [API](../together.md#api))
- `scripts/together.py` session builder (a thin layer on the per-person compare engine, which isn't forked):
  - Intervals: clock-time rep matching across people (with the pair offset), set distance per rep, results =
    time for the set distance.
  - Standings per rep (rank, gap to winner, ties within the sampling error), cumulative totals and gap to the leader
    over the reps, rep wins, fade.
  - Rep close-up series: per person, along-track metres behind the leader vs seconds into the rep (own-start and clock
    alignment).
  - Breaks: duration, HR drop in the first 30 s, HR at the next rep start.
  - HR: effort per rep as % of HR reserve (fallback % max HR ⚠).
  - Continuous runs (together / same race / easy): gap over distance and distance apart over time (only while
    together), who led where, km splits.
  - Replay tracks: per person, position over clock time, thinned, loaded only when the replay is opened.
- `/api/together` (list, filters, head-to-head), `/api/together/<gid>` (session), `/api/together/<gid>/replay`.
  They're outside the person store, built from each participant's own store. Response caching is the same as compare.

### T3 — pages (≈ 3 days) — ✅ done ([app.md](../app.md): `/together`, `/together/<gid>`)
- **Together** tab in the sidebar (under Analysis), and on an activity page a "👥 Together with Patrik ›" link.
- **List** `/together`: head-to-head strip; rows with date/time, place, workout, coloured participant initials, label,
  completion ("A 15/15 · P 10/15") and a headline ("Adrian 10–0 · +7.8 s/rep · closest rep 10, 2.5 s"). Filters:
  people, sport, label (+ near-misses), place, period, search.
- **Session page** `/together/<gid>`, intervals first:
  - Header: workout, place, date, participants with fitness score and average effort ("54.9 @ 89 %"), caveat chips
    (sampling, clock offset, HR defaults).
  - **Leaderboard**: total over the shared reps, average, gap, rep wins, fade.
  - **Per-rep standings**: reps × people, time, rank medal, gap to the winner, cells tinted by gap.
  - **Cumulative gap to the leader** line chart across reps (the "race standing" over the session).
  - **Rep close-up**: pick a rep → "metres behind the leader" line chart + the track map with a dot per person at the
    cursor; Own start / Clock toggle.
  - **Replay ▶**: dots running round the track on the map in clock time, with play / speed / scrub, "reps only"
    (skips breaks) and live standings next to it.
  - **Breaks**: who recovered faster (HR drop 30 s, HR at the next start).
  - HR beyond these three elements goes behind a "Heart rate" toggle (HR traces per rep).
- Continuous runs: gap over distance, distance apart, who led where, km splits, and the same replay.
- Consistent person colours everywhere (from people.json or automatic).
- Built: `pages/TogetherPage.tsx`, `pages/TogetherSession.tsx`, `components/{TLine,TogetherMap,Replay}.tsx`,
  `lib/{people,together}.ts`, `pages/together.css`; sidebar tab with a 30-day badge, 👥 link on the activity page,
  👥 marker in activity tables (`/api/search` rows got `together`). Not done: the transposed rep matrix for > 4
  people (columns just get narrower; T4 "Layout for 8 people"), a people show/hide selector on the session page.

### T4 — later (pick)
- Snapping to the Bislett track geometry for exact dots and 200 m splits.
- Manual overrides (exclude a participant, set the label).
- Season view: head-to-head over time ("Patrik is closing in"), PR badges, streaks.
- "Open in Compare" across people (Compare Phase 3).
- Layout for 8 people.

## Questions for the user (not blocking)
1. ~~Real max and resting HR~~: from the data (scripts/hr_settings.py): adrian 214 / 48, patrik 210 / 54.
2. Are the defaults above OK, especially hiding near-misses and showing the head-to-head record?

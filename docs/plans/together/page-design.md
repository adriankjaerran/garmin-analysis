# "Together": a cross-person page for sessions run together (design, no implementation)

Status: design draft, 2026-11-01. Nothing in the repo was changed. Throwaway probes: `/tmp/together/scan.py`
(finds joint sessions in both indexes), `pair.py` (pairs reps across people by clock time), `gap.py` (distance
apart within a rep), and the `cmp_*.json` / `act_*.json` payloads fetched from the running app.

The ask: after a group session (e.g. 6×1000 at Bislett) show per rep how everyone did, a race replay round the
track, how far apart people were, the cumulative total over the reps, and some heart rate without clutter. Plus a
list of every workout with at least 2 participants, built to grow to about 8 people.

---------------------------------------------------------------------------------------------------------------
## 0. What the real data says (this drives most of the design)

The scan matched activities with > 50 % time overlap. It found **24 adrian–patrik pairs**: 13 runs (most on Tuesday evenings at
Bislett), 1 race among them, 7 hikes, 1 ride, 1 ski, plus 2 false positives (starts 844 km apart; a stopwatch file with a
bad clock), so detection needs a start-distance check. The Bislett Tuesdays I checked with today's
per-person `/api/<p>/compare?mode=intervals`:

| date | adrian (detected) | patrik (detected) | paired by clock time (rep start Δ) |
|---|---|---|---|
| 2026-10-20 | 15×400 @ 75–80 s (track mode, all 400 m) | 10×400 @ 79–90 s (GPS 392–416 m) | reps 1–10 pair, Δ start **+1.0 … +5.0 s** (median +2.3); patrik stopped after 10 |
| 2025-11-11 | 6×800 + 2×400 | "10 reps (mixed 1:02–3:09)" | 7 of 8 pair, Δ −1.0 … +2.2 s; adrian rep 2 pairs with a **partial** patrik rep (443 of 826 m: his detector split it); patrik has 2 extra short reps |
| 2026-10-15 | 3000 + 2000 + … (multisport file) | 1×1300 + 2×3000 | 2 pair (Δ −0.5, −1.7 s); the 2nd is 2009 vs 3027 m, so **adrian cut his short** |
| 2026-10-27 | 6×1000 @ 3:17–3:36 | continuous ~4:25/km laps (labelled "5 reps mixed") | **different workouts**: patrik covered 713–826 m in each of adrian's 1000 m windows |
| 2026-10-13 | 8×800 | "Run without GPS" / 3 long reps | no usable pairing |
| 2023-05-07 | Race 10 km (place **Sørum**, R69) | Race 8–12 km (place **Lier-1**) | continuous: same start ±1 s |

Consequences:
1. **"Together" ≠ "same workout".** Participants can do the full set, part of it (10 of 15), cut a rep short, or do
   something else alongside (10-27). The page has to show completion and partial reps honestly. "Missing" is not
   the same as "slow".
2. **Match reps across people by clock time, not by the compare engine's distance/time DP.** Rep starts agree to
   within ±5 s (sampling and detection), and that holds even when one person's detector gives a messy signature.
   The DP would pair adrian's 6×800 with patrik's "mixed 10 reps" badly, or fall back to index pairing.
3. **Labels and places differ per person for the same session** (Bislett vs St. Hanshaugen, Sørum vs Lier-1,
   15×400 vs 10×400). The group needs its *own* label and place, not one person's.
4. **GPS distance on the track isn't fair.** Adrian's track mode gives exactly 400 m. Patrik's GPS gives 392–416 m
   for the same rep. **The official result is the time for the prescribed distance**, never pace from own distance.
5. **Distance apart:** in `gap.py` (10-20, reps 1–3) the straight-line distance matched the along-track gap to within
   ~10 m for gaps up to ~60 m. A 400 m track is only ~73 m across, though, so straight-line distance can never show a
   gap bigger than that and shrinks again past half a lap. **Use the along-track gap** (difference in distance run
   since the rep started, each scaled to the prescribed distance), not straight-line metres.
6. **Sampling:** patrik's median record interval is 3 s, p90 8 s; adrian's is 1 s. Patrik's rep starts land ~2 s
   after adrian's every time, which is mostly detection lag from coarse sampling, not a slow start. Rep times are
   ±1–2 s for him.
7. **HR normalisation inputs are weak for patrik.** `heart_rate_zones.json` says max 207 with zones in HR_MAX mode
   (adrian: max 206, LT 181). Neither file has a resting HR. Patrik has no `daily/` folder, so there's no resting HR
   from the watch either. `fitness.json` uses hr_max 206 / rest 50 for **both** people. Patrik's rep %HRR (0.95)
   looks inflated. Any cross-person HR element needs per-person max/rest settings, plus a caveat until those exist.
8. **The fitness score already gives a fair one-line comparison:** 10-20 adrian 54.9, patrik 48.6 (VDOT-like, not
   HR-based). It's a good "equivalent performance" line for the header.
9. **Payload:** a per-person intervals compare is 60–105 kB (series 50–70 kB, tracks ~41 kB), 13–540 ms. Eight
   people is ~0.5–0.8 MB raw, ~150–200 kB gzipped. That's acceptable, but split the replay tracks into their own
   endpoint (see §4).

---------------------------------------------------------------------------------------------------------------
(Numbers in the sketches come from 2026-10-20 where computed. The h2h tallies and the 2026-10-15 / race rows are
illustrative.)

## 1. Information architecture

### Recommendation: inside the person prefix, global content
- URLs: **`/<viewer>/together`** (list) and **`/<viewer>/together/<gid>`** (deep dive, tabs via `?tab=reps|race|hr`).
  The group id is **global** (person-independent), so the same `<gid>` shows the same session under any prefix.
  The prefix only means *viewing as*: the list defaults to "sessions with me", "you" is shown in bold, and the
  sidebar stays that person's.
- Why not a top-level `/together` outside the prefix: `router.ts` treats a first segment that isn't a person as
  unprefixed and redirects it to the default person (main.tsx `App`). The sidebar, `withPerson()`, the `<a href>`
  vnode hook and `api.ts` all assume a prefix. Keeping the prefix means **no router changes**, and the page is still
  shareable (`/patrik/together/2026-10-20-bislet-1907` works for anyone).
- **Person switch** (PersonMenu): today it keeps the section and drops ids. For `/together/<gid>` it should keep the
  gid, because the gid is global (a 1-line exception like the one for `/day/<date>`).
- **API:** `/api/together` and `/api/together/<gid>`. The frontend calls them through `api.ts`, so they arrive as
  `/api/<viewer>/together…`. `PersonPrefix` rewrites that and sets `scope.person` = viewer, and the router uses the
  viewer only for defaults and "you". The data comes from every person's store (`deps.store_for(p)` for each
  participant), not from `get_store`.
- **Group ids:** stable when people are added later, so **not** derived from the participant set. Proposed form:
  `<date>-<place-slug>-<HHMM of first start>`, e.g. `2026-10-20-bislet-2007`. That's the detection agent's call;
  this page only needs it to be stable and URL-safe.

### Nav and entry points
- Sidebar, under **Analysis**: Compare, **Together**, Fitness. It's a new tab with a small count of the viewer's
  sessions in the last 30 days ("Together 3"). It's hidden when there's only 1 person.
- **Activity page:** a chip in the summary header, `👥 Together with Patrik · 10×400 · see results ›`, linking to
  `/together/<gid>`. It's shown when the activity belongs to a group. It needs `group_id` on the activity row (the
  detection output, joined in `activities.py`).
- **Activities list / Home recent workouts:** a small 👥 marker in the row (title = participants). It's optional and
  cheap once `group_id` is on the row.
- **Compare:** "Compare in Compare ›" on the deep dive for ad-hoc multi-session views. That needs Phase 3
  `person:id` ids, which is **not** required by Together (Together has its own engine entry, see §4).
- **Day page:** joint activities get the 👥 marker and a link.

---------------------------------------------------------------------------------------------------------------
## 2. List page `/together`

### Row contents (one row per joint session, newest first)
```
Tue 20 Oct 2026  20:07   Bislett     Intervals 15×400 · r ~1:00        (A)(P)          Adrian 10–0 · +7.8 s/rep     ›
                                     A 15/15 · P 10/15                 2 people        "closest: rep 10, 2.5 s"
```
- **Date + weekday + start time** (local; earliest participant start).
- **Place**: the group's place (§0.3). Use one shared name function, e.g. the KNOWN/LANDMARKS list, else the first
  participant's place name.
- **Workout**: the group label + kind chip (Intervals / Easy / Long / Race / Hike / Ski / Bike) and the per-person
  completion "A 15/15 · P 10/15" when it differs. A participant doing something else gets "P: easy alongside".
- **Participants**: avatar circles (initial on the person colour, §5). With > 5 people: 5 avatars + "+3". Each avatar
  has a tiny "✓ full / ◐ partial / ○ other" mark.
- **Headline** (the motivation hook): intervals = "Adrian won 10 of 10 shared reps · avg +9.4 s/rep"; with 3+ people
  "1. Adrian · 2. Patrik (+9 s) · 3. …". Race/continuous = finish order + gap. Hikes/rides = no winner, just
  "together 94 %". A secondary line, "closest rep: rep 10, 2.5 s apart", gives a reason to click even when the
  result is predictable.
- No per-row map or sparkline in v1 (cost, clutter). Hovering a row could show a mini map of the tracks later.

### Above the list: head-to-head strip (cheap, very motivating)
`Adrian vs Patrik · 24 sessions together · intervals: shared reps won N–M · closest session: 2025-11-11 (+25 s/800)`.
It shows the viewer against each other person (one line per pair, collapsed beyond 3 pairs).

### Filters (URL query, like Activities)
- **People** chips: any-of (default = the viewer) or `all=1` "all of these"; **min participants** (2 default, up to 8).
- **Kind**: Intervals / Continuous (easy, long, tempo) / Race / Other sports (hike, bike, ski). Runs are on by
  default, other sports off but countable ("+9 hikes & rides").
- **Place**: dropdown of group places, with counts.
- **Period**: Any, last 90 days, each year (same chips as Activities).
- **Words** (`q`): label, place, date.
- Sort: date (default), participants, closeness (smallest mean gap), the viewer's margin.
- Empty state: "No sessions with 2+ people yet. Detection runs when both people's data is refreshed."

---------------------------------------------------------------------------------------------------------------
## 3. Deep dive `/together/<gid>`

### 3.1 Header (all kinds)
```
Tue 20 Oct 2026 · 20:07 · Bislett · Intervals 15×400 · rest ~1:00            [Reps] [Race] [Heart rate]
(A) Adrian  15/15 reps · 8.8 km · shape 54.9 ≈ 5k 18:5x  ↗ activity
(P) Patrik  10/15 reps · 9.1 km · shape 48.6 ≈ 5k 21:xx  ↗ activity
⚠ Patrik records every 3–8 s: rep times ±2 s.  ⚠ Patrik's HR max/rest are watch defaults.        (caveats, collapsible)
```
- The shape score (fitness.json) is the fair "equivalent effort" line. It isn't HR-based and copes with different
  rep counts.
- Each name links to that person's activity: `withPerson('/activities/<id>', person)`, the one place where a link
  goes to another person's prefix.
- A participant selector to show or hide people: chips in their colours, focus on hover, and "pin vs me" on click.
  It matters at 8 people.

### 3.2 Intervals (MVP)

**A. Leaderboard (cumulative)**: one compact table, sorted by rank:
| # | Person | Reps | Total (shared reps) | Avg rep | Gap to leader | Rep wins | Best rep | Fade (last 3 vs first 3) |
|---|---|---|---|---|---|---|---|---|
| 1 | Adrian | 15/15 | 12:52.3 (10 reps) | 77.2 s | — | 10 | R1 75.6 | +0.7 s |
| 2 | Patrik | 10/15 | 14:10.7 (10 reps) | 85.1 s | +1:18.4 | 0 | R10 79.3 | −2.0 s |
- **Ranking rule:** totals compare only the **shared reps** (the reps every shown participant ran). The column header
  says "(10 reps)". People who did more reps also get "all 15: 19:11.4" in grey. With many people "shared" can shrink
  to almost nothing. Fallback: rank by **mean gap to the rep winner over the reps you ran**, and show the reps count.
  (Open question: which rule.)
- **Fade** is the "who held pace" metric: last 3 reps vs first 3, as a plain number.

**B. Rep standings ("race results per rep")**: a matrix with one row per rep, one column per person:
```
Rep   Adrian         Patrik            gap
 1   🥇 75.6        86.5  +10.9        ▇▇▇▇▇▇▇▇▇▇▇
 2   🥇 77.0        87.2  +10.2
 ...
10   🥇 76.8        79.3   +2.5  ← closest
11      78.3          —   (stopped)
```
- Cell = rep time for the prescribed distance (0.1 s at 1 Hz, whole seconds plus "±2" at coarse sampling), small
  rank number/medal, gap to the rep winner. A 1-px bar shows the gap in "gap to winner" mode.
  **Within 1 s (or within the sampling uncertainty) = "=" dead heat**, so nobody gets a fake win.
- Partial rep (distance off > 15 % from the prescribed) = cell in italics + "◐ 443 m". It's left out of the ranking
  but the time is shown.
- Click a row = select that rep for C and D (URL `rep=10`).
- Heat colouring per cell like Compare's Matrix (fast → dark), optional.

**C. Cumulative gap chart** (uPlot, x = rep 1..n, y = seconds behind the leader *after* that rep, cumulative). The
leader's line sits at 0 and the others go up, so lines that flatten show someone holding on. The line ends where a
person stopped (marker "stopped after R10"). Hover = per-person cumulative + that rep's split. It answers "how did
the total build up". With 2 people this is one interesting line, which is fine. Alternative y: "seconds behind the
fastest cumulative", which is the same thing.

**D. Rep N close-up**: picked from B or with ◀ ▶ (keyboard ← →):
- **Distance between us** (uPlot, x = seconds since the rep start, 0 .. longest rep time): y = **metres behind the
  rep's leader**, along the track (§0.5), one line per person, the leader flat at 0. The 10-20 R1 shape is a jump to
  ~21 m in the first 10 s (patrik's ~2 s later start), then a steady drift to ~56 m by 70 s. After the leader
  finishes, the line freezes at the finish-line gap (no more data for the leader), ending at the rep result
  "+10.9 s ≈ 51 m". Speed lines are a toggle (off by default).
- **Start alignment toggle:** **Common start** (default: each person's rep from their own detected start, so the
  gap = running speed only, fair given ±2 s detection) | **Clock time** (real clock, so a late start counts:
  the real race). It shows "start Δ +2.3 s (within sampling uncertainty)" when |Δ| < 3 s.
- **Map, track view** (MapLibre, Kartverket grey, zoomed to the track): each person's rep path in their colour plus
  a dot each at the chart cursor. A dot carries a small label "−51 m" relative to the viewer.
- **Splits inside the rep:** in 1000 m reps, per 200 m (from distance since the rep start, scaled). Behind a
  disclosure, because they're GPS-precision only.

**E. Breaks / recovery** (a small table, MVP as columns, chart later): per break, duration (same for everyone in a
group session, mostly), **HR drop** (§3.4), kind (stand/walk/jog), and "ready" = HR at the next rep start. "Who
recovered faster" = the person with the bigger drop, as a small 🫀 marker per break row.

**F. Race replay** (Phase 2, its own tab `Race`): a full-session map on clock time, everyone at once.
- Controls: ▶/❚❚, speed ×10 / ×30 / ×60, scrubber with reps shaded (click a rep = jump to its start), "follow rep
  only" (skips breaks: when it plays through a break it jumps to the next rep start, about 8× shorter).
- Dots in person colours with initials. A live standings panel next to the map: "Rep 4 · 0:42 · 1 Adrian · 2 Patrik
  −38 m". Optional trailing tail of the last 10 s.
- Positions: clock time = activity `start_utc` + the series `t` + the detection's clock offset per participant.
  Snapping to the 400 m oval comes later (P3-3 geometry): raw GPS on a track wobbles ±5 m, fine for a dot.

### 3.3 Continuous runs (easy / long / race) — Phase 2
- **Gap over distance** (x = km along the leader's track, or the first participant's when they're all on the same
  route; people are projected onto it with `session_groups.project`): y = seconds behind the leader. This is the
  ghost race of `distance` mode, run across people.
- **Who led where:** a ribbon under the chart and a map line coloured by the leader of each 100 m. For a race, the
  finish order is the headline.
- **Distance apart over clock time** (here straight-line distance *is* meaningful): metres between the two (with 3+
  people: from the viewer, or the spread of the group as max−min), plus a single number "ran within 20 m of each
  other for 82 % of the time". For easy runs this "together %" is the main result (no winner).
- **Splits per km:** a table, km × person, rank per km (from distance mode's `legs`, `km` kind).
- The replay works the same as for intervals.

### 3.4 Heart rate: at most 3 elements, the rest behind a toggle
Raw bpm across people is misleading (different max/rest, wrist vs strap, the fr935 era read low: adrian's
2025-11-11 800s show 134–151 bpm at 3:07/km). So:
1. **Effort per rep (%HRR)** as one small extra line in each rep-standings cell ("89 %" in grey). The leaderboard
   gets an "avg effort" column. The insight: "Patrik ran 9 s slower at 95 % of his reserve, Adrian at 89 %",
   i.e. who was closer to their limit. Use the rep's **peak** HR (more robust than the mean on short reps with HR
   lag). With unknown rest HR, fall back to **%HRmax** and label the column so.
2. **Recovery in the breaks:** HR drop over the first 30 s (`drop_30`, available for ~1:00 breaks; `drop_60` is null
   on 50 s breaks) **as % of HRR** (raw bpm drops depend on HR max), plus HR at the next rep start. One column pair
   in the breaks table. The 🫀 marks the faster recovery.
3. **Session effort line in the header:** shape score + average rep %HRR ("54.9 @ 89 %"). It puts performance and
   cost side by side, a cross-person "efficiency" without inventing a new metric.

Behind the **Heart rate** tab or toggle (not on the main view): HR traces in the rep close-up (overlay, %HRR axis),
HR drift across reps (peak %HRR vs rep #), HR in the replay dots, raw bpm tables.
**Not doing:** pace-per-beat efficiency across people (it mixes stride, weight and HR range: individually
meaningful, not between people). Zones-time bars per person (clutter, and zones are defaults for patrik).
**Precondition:** per-person HR settings, i.e. max (observed or tested) and resting. That means a small override file
(e.g. `data/<p>/profile/hr_settings.json`) or the max/rest from `fitness.json` params once they're per person. Until
then the HR elements show "%HRmax (watch default)" with a ⚠.

### 3.5 Fairness and caveats (shown, not hidden)
- **Sampling:** per participant `sample_s` (median record interval). When it's mixed, the caveat chip reads "Patrik
  records every 3–8 s: rep times ±2 s, start Δ under 3 s isn't meaningful". Rep times at 1 s precision for coarse
  sessions. Dead-heat rule within max(1 s, the larger uncertainty).
- **Clock offsets:** the detection agent gives one offset per participant. Show it in the caveats ("watch clocks
  within 1 s" / "Patrik's clock corrected −3 s"). It only matters for *Clock time* mode and the replay. The ranking
  uses rep times, which are offset-free.
- **GPS distance:** results are times for the prescribed distance. Distances (412 m) are in a tooltip only.
  Along-track gaps use distance scaled to the prescribed distance (a 412 m GPS rep is scaled ×400/412).
  Straight-line metres are never used on track sessions.
- **Detection disagreements:** partial reps (◐) are excluded from ranking; "did other workout" participants (○) are
  shown in the header, map and replay but not in rep standings. A manual **override** later (exclude, mark rep
  partial, set the group label).
- **Watch HR quality:** an fr935-era file gets an "optical HR, may read low" marker on the HR elements (the fitness
  model already has a per-device `trust`).

---------------------------------------------------------------------------------------------------------------
## 4. Engine and API

### What can be reused from `scripts/compare.py` (per person, unchanged)
- `compare(ids=[one id], "intervals", person=p, rep_series=True)` per participant already gives everything per
  session: `reps[id]` (start_s, time_s, dist_m, hr, hr_max, hr_start/end), `breaks[id]` (dur, kind, hr_peak,
  drop_30/60/90, hr_next_start), `rep_summary`, the rep windows (`_rep_windows`: per-rep 1 s grid of speed/HR). The
  running server didn't return `rep_series` when I asked for it, probably an older build. It's in the code. Each
  person's `compare.pkl` stays per person, keyed by FIT, which is fine.
- The `_Store` grids (`G["dist"]`, `G["hr"]`, `G["T"]`) per person, for along-track gaps and splits.
- `distance` mode internals (`session_groups.project`, legs, coverage) for continuous runs (Phase 2).
- `fitness.json` sessions (score, components.work_hrr) per person, joined by id as the Fitness router does.
- `store.series()` / `track_points()` for replay tracks; `start_utc` is in the series meta.

### What's new: `scripts/together.py` (a thin layer, it doesn't fork compare)
1. **Clock-time slot matching** (new `match="clock"` in the spirit of `_slots`): the anchor is the participant with
   the most reps in the shared window, preferring track-mode/1 Hz. Another person's rep pairs with an anchor rep when
   |Δ start (UTC + clock offset)| ≤ max(8 s, 2 × sample_s) and the time overlap is > 50 %. Unpaired reps get extra
   slots exactly like `"6+1"`. **Partial** = distance ratio off by > 15 % (or time ratio when there's no GPS). Proven
   on 10-20 (10/10), 2025-11-11 (7/8 + 1 partial), 10-15 (2 + 1 partial).
2. **Prescribed distance per slot:** the median of the full (non-partial) reps, rounded to a round number (400 /
   800 / 1000 m), or the track-mode person's distance. Rep time for the prescribed distance = interpolated time at
   that scaled distance.
3. **Standings:** per slot rank, gap to winner, dead heat; cumulative over shared slots; leaderboard rows.
4. **Within-rep gap series:** per slot, x = 0..W s on the 1 s grid, for each person `d_norm(x)` = (own distance since
   own rep start) × prescribed / own rep distance. Gap = leader's d_norm − own. Mode `clock` uses a common x0 = the
   earliest start. Size: about 2 values × ~W s per person per rep, ≈ 2–3 k numbers per person for a 6×1000.
5. **HR normalisation per person:** `hr_norm {person: {max, rest, source: profile|observed|override|default}}`.
   %HRR per rep/break computed server side.
6. **Continuous (Phase 2):** distance mode with the projection reference = the first participant, sessions from
   different stores. This is the Phase 3 "compare across people" change. Route ids are person-local, so the
   projection cache key gets the reference person (other-ideas P1-3).
7. **Group meta:** label/place consensus (§0.3) is the detection agent's job, but the page needs `label`, `kind`,
   `place {id, name}`, `participants[{person, activity_id, role: full|partial|other, clock_offset_s, sample_s}]`.

### Endpoints
- `GET /api/together?people=adrian,patrik&all=0&min_n=2&kind=intervals,continuous,race&place=&from=&to=&q=` →
  `{n, people: [{person, name, initials, color}], h2h: [{a, b, n, rep_wins: [x, y], ...}], groups: [{id, date,
  start_local, place, label, kind, participants: [{person, activity_id, workout_label, dist_km, moving_s, reps_done,
  role}], headline: {winner, text, closest: {slot, gap_s}}}]}`. The headline needs an engine run per group, so
  cache it in **`data/_together/cache.json`** (not a person folder: `people()` only counts folders with an
  `activities_index.json`). Key it by the participants' FIT mtimes; compute lazily. That's ~25 groups × ~50 ms cold,
  free when warm. Size ≈ 300 B/row, 25 rows ≈ 8 kB.
- `GET /api/together/<gid>?align=common|clock&people=` → `{group, participants: [... + color, hr_norm, sample_s,
  shape], kind, slots [{slot, label, prescribed_m, n_people}], reps {person: [row|null per slot]}, standings [{slot,
  order: [{person, time_s, rank, gap_s, partial, tie}]}], leaderboard [...], cumulative {person: [gap_s per slot]},
  breaks {person: [...]}, rep_gaps {slot: {x0, n, people: {person: {gap_m, speed_kmh?, hr_pct?}}}}, caveats [{kind,
  text}]}`. ≈ 15–25 kB per person (rep_gaps dominates) → 8 people ≈ 150 kB raw, ≈ 40 kB gzipped.
- `GET /api/together/<gid>/replay?max_points=3000` → `{t0_utc, people: {person: {t, lat, lon, hr?}}}`, clock
  time from t0. ~40 kB per person. Loaded only when the Race tab opens.
- Memo/lock: reuse the compare router's `_LOCK` (the engine isn't thread-safe) and a small result memo keyed by gid +
  FIT mtimes.

### Frontend pieces
New `pages/TogetherPage.tsx` (list) and `pages/TogetherSession.tsx` (deep dive), plus `lib/people.ts` (person
colours). Reuse: `Table` (sortable), `CompareCharts`/uPlot (cumulative and gap charts take `colors` keyed by
person), `CompareMap` (tracks + `positions` at the cursor), `MapView`, `Matrix`-style heat cells from ComparePage
(it would have to move out into a component: coordinate with the agent editing ComparePage).

---------------------------------------------------------------------------------------------------------------
## 5. Visual design

### Person colours (consistent everywhere)
- A **fixed colour per person**, not per position: from a small config (`data/people.json` → `{adrian: {name, color,
  initials}}`), else assigned in `people()` order from the existing `PALETTE` (adrian #2f5fd0 blue, patrik #c43c3c
  red, then green, amber, teal, purple, orange, slate). That's 8 distinct colours, the 8-person target. Dark theme
  via `PALETTE_DARK` at the same index.
- Unlike Compare there's no black "reference" colour. The **viewer** is emphasised by line width (2.5 vs 1.5 px)
  and bold labels instead.
- Avatars: a circle with the initial(s) on the person colour. Used on list rows, the leaderboard, map dots and
  chips.

### List (desktop)
```
┌ sidebar ┐ Together                                                        [People: (A)✓ (P)  ] [min 2 ▾]
│ Home    │ ┌───────────────────────────────────────────────────────────────────────────────────────────┐
│ Explore │ │ Adrian vs Patrik · 24 together · shared reps 61–4 · closest: 11 Nov 2025 (+25 s / 800)      │
│ ...     │ └───────────────────────────────────────────────────────────────────────────────────────────┘
│ Analysis│ [Intervals✓][Continuous✓][Race✓][Other +9]  Place [Any ▾]  Period [Any|90 d|2026|2025…]  [search ]
│ Compare │ ┌────────────┬───────┬──────────┬─────────────────────────┬──────────┬──────────────────────────────┐
│>Together│ │ Date       │ Start │ Place    │ Workout                 │ Who      │ Result                       │
│ Fitness │ ├────────────┼───────┼──────────┼─────────────────────────┼──────────┼──────────────────────────────┤
│         │ │ Tue 27 Oct │ 20:04 │ Bislett  │ 6×1000 · A only; P easy │ (A)(P)○  │ — different workouts          │
│         │ │ Tue 20 Oct │ 20:07 │ Bislett  │ 15×400 · A 15 · P 10    │ (A)(P)◐  │ 🥇 Adrian 10–0 · +7.8 s/rep   │
│         │ │ Thu 15 Oct │ 20:12 │ Bislett  │ 2×3000 (+1300)          │ (A)◐(P)  │ 🥇 Adrian 1–0 · closest 45 s  │
│         │ │ Tue 11 Nov │ 19:37 │ Bislett  │ 6×800 + 2×400           │ (A)(P)   │ 🥇 Adrian 6–0 ◐1 · +25 s/800     │
│         │ │ Sun 7 May  │ 12:00 │ Lier     │ Race 10 km              │ (A)(P)   │ 🏁 Adrian · Patrik +x:xx       │
│         │ └────────────┴───────┴──────────┴─────────────────────────┴──────────┴──────────────────────────────┘
```

### Deep dive, intervals (desktop ≥ 1280 px)
```
Tue 20 Oct 2026 · 20:07 · Bislett · 15×400 · rest ~1:00                         [Reps] [Race ▶] [Heart rate]
(A) Adrian 15/15 · shape 54.9 @ 89 % HRR  ↗     (P) Patrik 10/15 · shape 48.6 @ 95 % HRmax⚠  ↗     ⚠ 2 caveats ▾
┌ Leaderboard (10 shared reps) ───────────────────────┐ ┌ Cumulative gap to leader (s) ─────────────────────┐
│ 1 (A) Adrian 12:52.3  77.2 s   —      10 wins  +0.7 │ │ 90┤                               ●P stopped      │
│ 2 (P) Patrik 14:10.7  85.1 s  +1:18.4  0 wins −2.0 │ │   ┤                  ___.---'                     │
│        (all 15: Adrian 19:11.4)                     │ │ 45┤        ___.---'''                             │
└─────────────────────────────────────────────────────┘ │  0┼A═════════════════════════════════════════      │
┌ Rep standings ───────────────────────────────────────┐│    1  2  3  4  5  6  7  8  9 10 11 … 15  rep     │
│ Rep │ (A) Adrian    │ (P) Patrik         │ gap        │└──────────────────────────────────────────────────┘
│  1  │ 🥇75.6  89%   │ 86.5 +10.9  95%    │ ▇▇▇▇▇▇▇▇▇▇ │┌ Rep 10 ◀ ▶  [Common start|Clock time] ─────────┐
│  …  │               │                    │            ││ map: track, 2 paths, dots at cursor  (A) (P)-12 m│
│ 10  │ 🥇76.8  94%   │ 79.3 +2.5   97%  ← │ ▇▇         ││ ─────────────────────────────────────────────── │
│ 11  │   78.3  90%   │  —  stopped        │            ││ m behind leader  0 ═══A═════════════════════     │
│  …  │               │                    │            ││                 -12   ‾‾‾‾‾‾‾‾‾P‾‾‾‾‾‾‾‾          │
└──────────────────────────────────────────────────────┘│   0 s        20         40         60   79 s      │
┌ Breaks · recovery ─────────────────────────────────┐  └──────────────────────────────────────────────────┘
│ After │ dur  │ (A) drop 30 s · next │ (P) …   │ 🫀 │
└────────────────────────────────────────────────────┘
```
Narrow screens: one column in the order header → leaderboard → rep standings → rep close-up → cumulative → breaks.

### Scaling 2 → 8 people
- Rep standings: columns per person up to 4, then **switch to rows = people, columns = reps** (a compact heat matrix
  of rank numbers, 1 = darkest, hover = time). With 8 people × 15 reps that's 120 small cells, still readable.
- Charts: the viewer and the hovered/pinned person bold, others 1.5 px, and > 5 people dims non-focused lines (like
  `FOCUS_MANY`). Gap charts are always relative to the leader, so the lines fan out downwards with no crossings to
  untangle.
- Leaderboard: always all people (8 rows is fine). Rep winners show the medal only, gap numbers on hover.
- Map/replay: 8 dots with initials; labels only for the viewer and the pinned person, the standings panel lists all.
- Participant chips show/hide people, URL `people=`.

---------------------------------------------------------------------------------------------------------------
## 6. Phased plan (S ≈ ½ day, M ≈ 1–2 days, L ≈ 3+ days)

**Phase 0: contracts (S).** Agree on the detection output (gid, place, label, kind, participants, roles, clock
offsets), `data/_together/` cache, the people config (name, colour, initials), and `group_id` on activity rows.
Per-person HR settings (max, rest), even as a hand-written file.

**Phase 1: MVP intervals deep dive + list (L, ~4–5 days)**
- `scripts/together.py`: clock slot matching, prescribed distance, standings, cumulative, rep gap series,
  %HRR/%HRmax (M).
- `/api/together` (list + headline cache + h2h line) and `/api/together/<gid>` (M).
- List page with filters/sort and the nav tab; the activity page chip (S–M).
- Deep dive: header + caveats, leaderboard, rep standings matrix, cumulative gap chart, rep close-up (distance
  behind the leader + track map with cursor dots, Common/Clock toggle), breaks table with HR drop (M–L).
- HR elements 1–3 (effort %, recovery, header line). Other HR stays out.

**Phase 2: replay + continuous (M–L)**
- Race tab: replay endpoint, ▶ / speed / scrub / "reps only", live standings panel (M).
- Continuous runs: cross-person distance mode (projection onto the first participant, per-person caches), gap over
  distance, who-led ribbon, distance apart + "together %", km splits (L; overlaps Compare Phase 3).
- Heart rate tab: HR overlays, drift across reps (S–M).

**Phase 3: polish and motivation (pick)**
- Bislett track geometry snapping for dots and 200 m splits (M, = P3-3).
- Overrides: exclude a participant, mark partial, set the label (S–M).
- Season view: head-to-head over time (avg gap per shared rep by month: "Patrik is closing in"), PR badges ("Patrik's
  fastest 400 ever, while chasing Adrian"), streaks (M).
- 8-person layout switch (rows = people), focus/pin, show/hide (S–M).
- "Open in Compare" with `person:id` ids (depends on Compare Phase 3).

MVP = Phase 0 + 1. It covers the user's core: per-rep "how did each do", the cumulative total, distance between
people in each rep on a line chart with the map, and a little HR. Replay and continuous runs follow.

---------------------------------------------------------------------------------------------------------------
## 7. Open questions for the user
1. **Ranking with different rep counts** (Patrik 10/15): rank on the shared reps only (proposed), on mean gap per rep
   ran, or "did all reps" first?
2. **Same session, different workout** (10-27: Adrian 6×1000, Patrik a steady run alongside): list it ("○ other
   workout") or hide it from Together?
3. **Common start vs clock time** as the default within a rep: own detected start (fair, ±2 s noise) or the real
   race (a late start counts)?
4. **HR settings:** do you know Patrik's (and your) real max and resting HR? Until then HR shows %HRmax with a
   warning. A hand-edited `hr_settings.json` per person is OK?
5. **People config:** a `data/people.json` with display name, colour and initials (and later avatar photos? The
   index has Garmin profile image URLs, but loading them is a network call)?
6. **Other sports** (hikes, bike, ski together, 9 of the 24): show them in the list with a "together %" only, or runs
   only for now?
7. **Motivation features:** is a head-to-head season record ("61–4") welcome, or too competitive for sharing with
   Patrik and future members? Show it only to the viewer?

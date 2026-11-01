# Polish audit 3: runner + product view (2026-11-01)

I used the app as a motivated runner would, on the built dist at http://127.0.0.1:8765. Coverage:
- adrian: every Personal page (Home, Activities with filters and groups, a run, an interval session, a ski, a hike,
  multisport, indoor Bislett, Day, Map, Places/route, Compare, Fitness).
- patrik: Home and Fitness.
- 1600 px in dark and light, plus 390×844 for Home, Activities and an activity.

Screenshots: `data/adrian/screenshots/2026-11-01/audit3-*.png` (`d-` dark, `l-` light, `m-` mobile, `p-` patrik).
No code was changed. I left Together/Community pages out (other workers are building them now).

**Health:** no JS errors or console entries on any page. Every API endpoint answers in 1–33 ms warm, for example:
- `/api/activities`: 21 ms / 548 kB
- `/api/fitness`: 23 ms / 470 kB
- `/api/nearest`: 33 ms

Speed is not a problem. One small thing: `/api/search?sport=run` returns the same 669 kB with and without `poly=1`,
so polylines seem to be always included.

**Verdict:** the earlier audits fixed the presentation. What's left are **numbers a runner would distrust**: the same
session is described two ways, indoor distances are inflated, shape values are stale, and the "easy" label is wrong.
There is also **missing "am I getting better?" framing**: PRs, route bests, workout progression and load. The data
for most of that is already in `data/`.

**Fix status (fix3, 2026-11-01)** — ✅ fixed, ◐ partly. Screenshots `fix3-*.png`; details in [docs/app.md](../app.md)
(Distance trust, Home / Activity / Activities / Places / Fitness rows, `/api/records`, `/api/progress`).
- #2 ✅ `dist_trusted` / `dist_est_km` / `dist_note` on every row: indoor Bislett = "≈ 10.5 km" (rep laps + warm-up /
  cool-down at the easy pace, breaks standing), other untrusted = "—"; km totals (Home volume / log / sums, calendar,
  search) count trusted km only (`n_untrusted`), pace "—", the activity page drops the pace panel.
- #7 ✅ lower bound below the shape = "Easy effort: doesn't change your shape" (activity) / "easy: no effect" (Fitness);
  the Fitness session list defaults to races + hard sessions (`sk=all` = every kind).
- #8 ✅ below 500 px the log shows "6×1.2k" / "1×1.3k + 2×3k", wrapping only after "×" / before "+"; km without unit.
- #9 ◐ Activities on a phone = two-line list ("Sat 31 Oct · 6×1000 @ 3:27 /km" / "8.4 km · 40:43 · 4:52 /km · HR 143 ·
  Bislett"); activity header values wrap on a phone. **Not done:** the theme toggle in the top bar (needs `main.tsx`).
- #11 ✅ route rank (#, 🏆 PB, "On route" = time over the route's legs, Δ best, per direction on point-to-point routes),
  "🏆 Best 47:14" + time trend in the route pane, Best column in the route list, "3rd fastest of 19" on the activity.
  The clipped column is gone (Dir only when directions differ).
- #12 ✅ suggest sets "6×1000: your 3 fastest" (any date), "same time last year", and for routes "your 3 fastest" /
  "same time last year". (No PB marker on the reference picker.)
- Feature 1 ✅ progression per workout (same sport + rep signature): activity-page line + Home **Progress** card.
- Feature 2 ✅ personal records from `fastestSplit_*` (1k, mile, 5k, 10k, half; trusted GPS runs, VDOT ≤ best + 3):
  Home card, Fitness › Personal records with PR history, record chips on the activity. Route bests = #11.
- Also fixed: indoor Bislett avg rep pace was computed with the track snap (1086 m → 1000 m), so 17 Feb 6×1086 said
  "3:44 /km" while its reps ran 3:20–3:39; now 3:26 (`store.rep_pace` skips the snap for `indoor_bislett`).
- Open: #1, #3–#6 (scheduled), #10, #13–#17.

---

## 1. Findings

### P1: wrong or contradicting numbers

| # | Page | Issue | Evidence | Fix | Files |
|---|---|---|---|---|---|
| 1 | Activity (ski intervals, indoor laps) | **The workout label and the Reps tab disagree.** 10 Aug ski is labelled "4×4:30 · avg rep pace 10.9 km/h", but Segments › Reps lists **16 reps** (25 s, 43 s, 77 m …) and the chart shades 16 stretches. `groups.json` has the correct 4 reps (305 / 252 / 251 / 346 s, breaks 253 / 225 / 183 s). The cache's reps come from `run_analysis.analyse` (a running detector). The same mismatch is in **all 18** ski-interval sessions (e.g. "3×5:00" → 16 reps, "7×4:00" → 0) and in 21 of 125 run interval sessions (mostly indoor Bislett "6×1086" / "10×539"; the cache has 2–4 reps). | `audit3-d-act-ski.png`; check: groups.json `reps` count vs `cache/series/<id>.npz` meta `reps` | In `fitcache.build` / `_reps_job`: for `type == "ski_intervals"` and for `indoor_bislett` sessions, take reps and breaks from `groups.json` (`reps` = [dur, dist, speed, hr] + starts from the ski_intervals / indoor rep_laps), not from `run_analysis`. Better: one source of truth, so the label, Reps tab, Compare and Shape all read the same rep list. Add a test that `len(meta.reps) == len(groups.reps)` for every interval session. | `app/server/fitcache.py`, `scripts/ski_intervals.py`, `scripts/session_groups.py` (export rep starts), `tests/test_api.py` |
| 2 ✅ | Activity, Activities, Home volume, Calendar | **Indoor Bislett distances are inflated and shown as fact.** Examples: 17 Feb "18.21 km (no GPS, not trusted) · **3:12 /km**"; 3 Feb 23.06 km at **2:48 /km**. 99 activities have `dist_trusted: false` (578 km in total). They still flow into km totals, weekly volume, the 52-week sparkline and list pace columns. The pace chart is nonsense (2:00–2:30 /km) and is offset from the cadence-aligned rep shading. | `audit3-d-act-indoor-6x1086.png` | For `dist_trusted == false`, show km as "≈ 11.4 km (laps)" when the rep laps are known: reps × lap length + easy jog estimated at the person's easy pace from moving time. Otherwise show "—". Show pace "—" in lists. Count time, not km, in volume (or count the estimate, marked). Hide the pace panel (keep HR / cadence). | `app/server/store.py` (row `dist_km` / `dist_est`), `routers/home.py` (volume), `components/ActivityBits.tsx`, `pages/ActivityPage.tsx`, `components/SeriesCharts.tsx` |
| 3 | Home shape strip, Fitness, Activity › Shape | **Stale shape shown as current.** Long distance is 58.4 "100 % of best · →0.0", but the last evidence is a race on 2025-11-02, **363 days ago**. The weekly series is flat for 26 weeks and its 10k equivalent is **36:11**. Interval (fresh, high confidence) says 10k **39:20**, so the user gets two predictions 3 minutes apart. Ski is 55.3 "100 %" with nothing since August. patrik: Long 44.2, last evidence **923 d ago**. | `audit3-d-home.png`, `audit3-l-fitness.png`, `audit3-p-fitness.png`; `/api/home` `fitness.long.last_evidence` | Model: let sd grow with time since evidence and decay the mean toward the interval shape after ~8 weeks without evidence (`fitness_model.py`, `current`). UI: when `last_evidence` is older than 90 days, grey the value and add "· last race 12 mo ago". On Home, show only fresh disciplines, or one **"Today's 10k ≈ 39:xx"** from a precision-weighted blend. | `scripts/fitness_model.py`, `pages/HomePage.tsx`, `pages/FitnessPage.tsx` |
| 4 | Home volume, Shape | **A running interval session recorded as Multisport is lost.** adrian 15 Oct (1×1300 + 2×3000 at Bislett with Patrik, 11.2 km) has `sport: other`. Effects: it counts as **"Other 11.2 km · new"** instead of Run; run "−5 %" should be about −18 %. It shows **12.1 km/h** instead of pace, HR "—" (the summary has no avg HR but the chart has HR), no Reps tab and no Shape score. Closest says "No other activities of this sport" while its map shows tracks. | `audit3-d-act-multisport.png`, `audit3-d-home.png`; `/api/home` volume `other.month.km = 11.2` | When a `multi_sport` parent's legs (or > 80 % of its time, per FIT session sport) are running, treat it as `run` in `store.family()` and in session_groups' classification. Compute avg HR from the series when the summary has none. | `app/server/store.py` (`family`), `scripts/session_groups.py` (`classify`), `scripts/fitness_model.py` |
| 5 | patrik Home shape, Fitness | **A misdetected session drags patrik's shape down.** 27 Oct is "5 reps (mixed 3:00–21:42) @ 4:20 /km with **12 s rest**": an 800 m warm-up at HR 98, then 1600, 800, a **5000 m "rep"** and 600, with breaks of 61 / 10 / 12 / 12 s. It is scored as a *hard session* (VDOT 41.5, 5.3 below shape), which drives Home's red **Interval ↓0.6**. The with-HR variant says 50.5 vs 46.8 performance-only, a 3.7 gap that nothing flags. | `audit3-p-home.png`, `audit3-p-fitness.png`; `/api/patrik/activities/24874001821` reps | Treat it as an interval session only if median break ≥ 30 s and the rep CV is plausible. Otherwise classify it as tempo/continuous, which makes it a lower bound. In `fitness_model`, a "hard session" with breaks < 20 s should become a continuous effort. When the variants disagree by > 2 VDOT, show "⚠ models disagree" in the Fitness table. | `scripts/session_groups.py` (`classify` `structured`), `scripts/fitness_model.py`, `pages/FitnessPage.tsx` |
| 6 | Home, Activities, Fitness | **"Easy run" covers tempo runs.** Examples: 3 Oct 7.5 km at **4:34 /km, HR 167** and 10 Oct 13 km at 4:45, HR 162 are both "Easy run". Interval shape 53 implies an easy pace of about 5:10–5:40. 272 of 493 runs are "easy" and only 10 are "tempo". The cause: `rel = speed / median own speed`, so a fast median hides steady runs. | `audit3-d-home.png` (Recent workouts) | Classify continuous runs by **HR % of max/reserve** (`profile/hr_settings.json` already has max 214 and rest 48) and/or pace vs the VDOT easy band: easy < 75 % HRR, steady 75–83 %, tempo > 83 %. Labels: "Easy run" / "Steady run" / "Tempo run" / "Long run". | `scripts/session_groups.py` (`classify`), `scripts/similarity.py` (FAMILY labels) |

### P2: confusing UX

| # | Page | Issue | Evidence | Fix | Files |
|---|---|---|---|---|---|
| 7 ✅ | Activity › Shape, Fitness sessions | **"21.5 below your shape that day"** appears on every easy run, in the same tone as hard sessions. In the Fitness table, lower bounds are about half the rows ("27.5 below", "28.6 below"), which is noise and reads as failure. | `audit3-d-act-easy.png`, `audit3-l-fitness.png` | For `kind == lower_bound` below the shape, show "Easy effort: doesn't change your shape" (no number). Default the Fitness session filter to *race + hard session*, keeping lower bounds one click away. | `components/ShapeSection.tsx`, `pages/FitnessPage.tsx` |
| 8 ✅ | Mobile Home | **Training-log labels truncate into wrong workouts.** "6×1200" shows as "6×120", "3×1800" as "3×180", "1×1300 + 2×3000" as "1×130 2×300". On a 390 px screen this reads as a different session. | `audit3-m-home.png` | Below 500 px, show the compact form "6×1.2k" (k for ≥ 1000 m) or only the sport dot + km, never clip digits. Use `text-overflow: clip` on whole tokens (wrap at "×" and "+"). | `pages/HomePage.tsx`, `pages/home.css` |
| 9 ◐ | Mobile (all) | The top bar is 3 rows (~100 px) because the **Auto / Light / Dark** toggle sits in it. Activities on mobile shows only Date + Workout; km, pace and HR are off-screen. The date takes 130 px ("2026-10-31 09:07"). | `audit3-m-activities.png`, `audit3-m-act-6x1000.png` | Move the theme toggle into the person menu. On mobile, the Activities row becomes 2 lines: "Sat 31 Oct · Easy run" and "8.0 km · 5:25 /km · HR 132". Activity header values: let them wrap instead of ellipsis ("203 max whole activ…"). | `main.tsx`, `styles.css`, `pages/ActivitiesPage.tsx` |
| 10 | Activity (hike) | A steep hike gets **27 pauses / 17 legs** of 0.1–0.4 km because slow uphill walking falls under the moving threshold. The pace chart (10:00–30:00 /km) is unreadable. Min/km is the wrong metric for a 602 m climb. | `audit3-d-act-hike.png` | For hikes: pause threshold ~0.3 m/s *and* no vertical progress. Header "Ascent 602 m · 480 m/h up". Replace the pace chart with vertical speed (m/h) or speed in km/h. | `scripts/session_groups.py` (pauses), `pages/ActivityPage.tsx`, `components/SeriesCharts.tsx` |
| 11 ✅ | Places › route, Activities | **A route has no "best" answer.** R3 Holmenkollen climb lists 19 activities by date with Moving / Paused / Speed, but no PB, no rank, no trend and no "your time on the route" (moving includes the lead-in). The table is also clipped (a "D…" column at the right edge). | `audit3-d-places-route.png` | Add Rank, "Time on route" (start-line to end-line, the same as Compare's common start), Δ to PB, a 🏆 on the best, and a small time-trend sparkline in the route header. | `routers/places.py` (route members `route_time_s` via compare's start line), `pages/PlacesPage.tsx` |
| 12 ✅ | Compare (suggest from an interval) | `?suggest=` takes the **8 latest** same-workout sessions. For the 6×1000, that leaves out the fastest ones (Sep–Oct 2025 at **3:13 /km** vs 3:27 now). "Am I as fit as last autumn?" is the most interesting comparison and it is not offered. | `audit3-l-compare.png`, `audit3-d-act-intervals.png` | Suggestion sets "Same workout: best ever" (top 3 by rep pace + this one) and "Same time last year". Also a "PB" marker on the reference picker. | `app/server/routers/compare.py` (`suggest`) |

### P3: polish

| # | Page | Issue | Fix | Files |
|---|---|---|---|---|
| 13 | Activities › Advanced | The placeholders "5-7", "1000m / 4:00", "2:00-3:00", "8-12" look like set values next to a real value ("~×1000"). | Prefix with "e.g.", or use a lighter placeholder colour. | `pages/ActivitiesPage.tsx`, `styles.css` |
| 14 | Groups › Workouts | "6×1086", "9×539", "2×1632 + 3×1086" are technically right (indoor laps of 546.5 m − 7.5 m) but read like noise. | Label "6×2 laps (1086 m)" for indoor Bislett. | `scripts/similarity.py` / `session_groups.py` signature label |
| 15 | Activity vs Compare | Rep 5 is 3:36 on the activity (216.45 s) and **3:37** in the Compare reps table: two rounding rules. | Use one `secs()` / m:ss rounding (floor or round consistently). | `lib/format.ts`, `pages/ComparePage.tsx` |
| 16 | Activity | Climb shows "0 m" in the header but "—" in lists for the same track session. | Use one rule ("—" when flat/indoor). | `pages/ActivityPage.tsx` |
| 17 | patrik Fitness | The Ski toggle is shown for a person with no ski data. Volume "+347 %" vs a 12-month average when he has < 6 months of history. | Hide disciplines without data. Show "new" when the history is shorter than the comparison window. | `pages/FitnessPage.tsx`, `routers/home.py` |

---

## 2. Feature ideas for a motivated runner (ranked by value ÷ effort)

| Rank | Feature | What it shows | Data already in `data/` | Effort |
|---|---|---|---|---|
| 1 ✅ | **Workout progression card** (activity page + Home) | "6×1000 at Bislett: 3:27 today · best 3:13 (14 Oct 2025) · last 5: 3:29 → 3:27 ↗". A mini chart of avg rep pace and avg rep HR per occurrence of the same `exact_key`. This is the runner's #1 question. | `rep_pace_ms`, `exact_key`, rep HR (search rows / cache) for 19 × 6×1000, 125 interval sessions | S |
| 2 ✅ | **PRs and records** | Fastest 1k / mile / 5k / 10k / half (whole-activity splits). Best per route (R3: fastest climb). Best avg rep pace per workout. "🏆 New PR" chips on Home / activity. A Records page. | `activities_index.json` `fastestSplit_1000/1609/5000/10000/21098` (151 activities have a 5k split); route members; rep pace | S–M |
| 3 | **Training load and form (CTL / ATL / TSB)** | Weekly load bars, a fitness/fatigue/form line, an "acute:chronic 1.4 ⚠" ramp warning, "Ready for a hard session?". Pairs with the shape model (performance) for "load vs shape". | Garmin `load` (`activityTrainingLoad`) on every activity, `te`, `hrTimeInZone_1..5`; daily resting HR / stress / body battery in `ranges/daily_summaries_*.json` since 2018 | M |
| 4 | **Weekly trends + consistency streaks** | Run km per week (12 / 52 weeks) with a goal line. Streaks: "9 weeks in a row with ≥ 1 interval session", "3 runs/week for 6 weeks". Easy / hard ratio per week (80/20) once #6 is fixed. | `/api/home` weekly buckets, the activity list, type classification | S |
| 5 | **One race predictor** | "Today: 5k 18:5x · 10k 39:xx · half 1:27" with a confidence band, from the blended (decayed) shape, plus a "since your 35:41 10k (Nov 2025)" delta. Replaces the 3–4 conflicting tables. | `fitness.json` `race_equivalents_s`, races in the index | S (after P1-3) |
| 6 | **Insight cards on Home** (3 at most, auto-generated) | "Rep pace 6 s/km faster than your 8-session median", "HR at 5:00 /km down 4 bpm vs August (aerobic gain)", "No long run for 5 weeks", "Resting HR +5 above baseline: easy day?". | Rep pace / exact_key; HR vs pace per easy run (series cache); daily resting HR | M |
| 7 | **Aerobic efficiency trend** | Easy runs: pace at a fixed HR (e.g. 140 bpm) or m/beat over time, plus HR drift (2nd half vs 1st). This is the easiest "fitness without racing" signal. | Series cache (hr, speed, alt), `avgGradeAdjustedSpeed` | M |
| 8 | **Goals** | "Sub-39 10k by May": the target VDOT line on the Fitness chart, a weekly km goal, a "x of y weeks hit" counter. Stored in `data/<p>/goals.json`. | Fitness model, weekly volume | M |
| 9 | **Pace zones / time in zone per week** | Stacked weekly minutes in zones 1–5 (HR) with an "80/20" marker. | `hrTimeInZone_*` per activity, `heart_rate_zones.json` | S |

**Do not build yet:** sleep-based readiness. Sleep is downloaded for only ~19 days. Resting HR / stress / body battery
(daily summaries since 2018) are enough for a simple readiness hint.

---

## 3. Community ideas (running club)

These use only data we already have: `together.json` (rep-by-rep standings, gaps, completion, replay), per-person
`fitness.json` (shape on the day), `groups.json` places / routes / exact workouts, and daily summaries.

1. **"Beat your own form" scoreboard per session.** Rank people by the form delta (session score − shape the day
   before: "+2.1"), not by raw speed. A 46-VDOT runner who has their best day tops a 53-VDOT runner who jogged. Planned
   in `form_delta.py`; this should be the *headline* of every session card. The most motivating number for a mixed club.
2. **Club records per standard session + place.** Examples: "15×400 at Bislett: club best average 75.1 s (Adrian, 20 Oct)",
   "R3 Holmenkollen climb: fastest member time", "most reps completed". Also **personal bests set in a club session**
   ("Patrik: fastest 400 ever, rep 7"). Source: Together slots (set distance × time), route members, `exact_key`.
3. **Attendance and streaks.** "Tuesday Bislett: 12 weeks in a row", attendance leaderboard per season, a "missed you"
   nudge after 3 weeks away (last seen per member). Weekday/hour heatmap of joint sessions to pick the next club time.
4. **Pair chemistry.** For each pair: sessions together, h2h reps won, closest finishes ("decided by 0.4 s in rep 10"),
   who pulls whom (the person whose pace rises in joint sessions vs solo, from their own same-workout history: "Patrik
   runs 6 s/km faster with Adrian than alone"). That last one is unique and very shareable.
5. **Collective numbers with a goal.** Club km / person-hours together this season against a target ("Run to Bergen:
   463 of 500 km"), monthly bars, and a "together time" counter.
6. **Session highlights generated from standings.** "Biggest comeback" (cumulative gap closed), "most even pacing"
   (lowest rep CV), "strongest finish" (last rep vs first), "best recovery" (HR drop 60 s). One badge per person per
   session, so everyone gets something.
7. **Replay as the share moment.** The "who passed whom" list per rep and a 20-second auto-play of the closest rep on
   the session card.
8. **Workout ladder over the season.** For the club's recurring workouts (6×1000, 15×400), plot every member's avg
   rep pace per occurrence on one chart. Everyone can see their own line rise. This is the community version of
   feature #1.
9. **Invite-free guests.** "+2 guests" (manual) keeps the count honest when members run without watches. Already
   planned; make sure the club totals count only timed members.

**Data caveats to design around:**
- Patrik's data starts in 2021 and is sparse (118 activities), so normalise per member by "% of own best" / form
  delta, not by totals.
- Watch-less participants get no splits.
- Near-misses (e.g. 27 Oct, same place, different workout) are valuable "attended" signals. Count them as attendance
  even when the workouts differ.

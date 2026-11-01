# Polish audit 2 — 2026-11-01

**Status: fixed** (2026-11-01) — all P1, P2 and P3 items and app-level proposals 1–6 were addressed by three fix passes (Compare, Together, app shell). Since fixed: track reps count as the set distance ([app.md](../app.md#rep-pace)). Left open: a "More columns" toggle for the route Sessions table; button groups on mobile scroll instead of becoming dropdowns.

First-time-user walk of the whole app at http://127.0.0.1:8765 (built dist, no code changed). 1440 px wide in
dark and light; mobile 390×844. adrian: every page; patrik: Home, Activities, Together, a session, Fitness.
Screenshots are in `data/adrian/screenshots/2026-11-01/audit2-*.png` (`d-` = dark, `l-` = light, `m-` = mobile,
`p-` = patrik). API timings are 3–31 ms on every endpoint, including Compare with rep series and the replay,
so speed is not a problem. The only JS error is the one in P1-1.

## Verdict

The pages work well one at a time. Activity, Together session (intervals), Compare › Intervals and Replay are
strong. Data is real, interactions are linked (hover ↔ map ↔ table) and loads are instant. The app does not yet
feel like one product, for three reasons:
1. **Joint training is an island.** Home, Day, Compare and the person's own lists barely know about Together
   (only the Activities 👥 icon and the activity-page chip do). Together → Compare doesn't exist.
2. **The same workout is described differently on each page.** "Multisport" vs "1×1300 + 2×3000", "6×1000 @ 3:29"
   vs "Rep pace 3:27", hike pace "3.5 km/h" vs "17:10", Together "Run" for a hike, "R3" vs "Rep 3" vs "3".
3. **Density over explanation.** Compare has 5–7 toggle groups that appear and disappear by mode. Fitness and
   Shape show model-evaluation jargon (MAE, log-lik, W′, τ, Skiba). Several wide tables are clipped at 1440.
   Mobile is half-done: Home overflows and there is no Home link.

There are two real bugs (Compare landing crash, wrong "all 15 reps" total). The rest is polish.

---

## P1 — broken, wrong or confusing

| # | Page | What's wrong | Evidence | Fix | Files |
|---|---|---|---|---|---|
| 1 | Compare (sidebar → Compare, `/compare`) | **Blank page.** `TypeError: null is not an object (evaluating 'D.data.mode')`. With no ids, `sug.data?.id === ids[0]` is `undefined === undefined` → true, then `sug.data.mode` throws. The whole app body is empty when loaded directly; the main area is empty when reached from the sidebar. The sidebar entry to a main feature is broken. | `audit2-d-cmp-empty.png`, `audit2-d-cmp-empty-nav.png` | `const hasReps = !!(actMap.get(ids[0])?.sig \|\| (ids[0] && sug.data?.id === ids[0] && sug.data?.mode === "intervals"))`, and the same `ids[0] &&` guard on the `allowed` line 306. Add a smoke test that renders `/compare` with no ids. | `app/web/src/pages/ComparePage.tsx` (l. 306–308) |
| 2 | Together session › Leaderboard | **"all 15 reps 12:52" is wrong.** It equals the 10-rep shared total (12:52.3). The 15 reps would be ≈ 19:30. `all_total_s` sums only *ranked* reps (`rk`), not all reps done. | `audit2-d-ts-intervals.png` (Leaderboard, Adrian row); `/api/together/2026-10-20_2007_Bislet` → `all_total_s = total_s = 772.3` | Sum every rep the person ran (`mine` with a result), or relabel it "10 ranked reps". Also show "avg of all 15" rather than a total. | `scripts/together.py` l. 1398; `pages/TogetherSession.tsx` l. 148 |
| 3 | Mobile (all pages) | **No Home link.** `nav.side .step { display:none }` also hides `a.step.home`. The top bar starts at "Activities" and the only way home is the URL. | `audit2-m-home.png`, `/tmp` check: Home link `offsetParent=null` | Exclude `.home` from the rule (`nav.side .step:not(.home)`), or show the person name as a home link. | `app/web/src/styles.css` (@media 760px), `main.tsx` l. 100 |
| 4 | Mobile Home | **Content cut off on the right.** The map is 456 px wide in a 390 px viewport. The training log loses Sat/Sun/Week, and the latest-day, Volume and Recent tables are clipped with no scroll. | `audit2-m-home.png` | Below 760 px: `.home-day` / `.home-two` already go to 1 column, but `.map` needs `width:100%`. Make the training log 7 narrow columns with the week total under the row, and wrap tables in `.tablewrap` (overflow-x:auto). | `pages/home.css`, `pages/HomePage.tsx` |
| 5 | Home (1440) | **Volume table cut at the right.** The "52 week(s)" header and the sparkline are clipped. Recent workouts' Place is cut ("Frognerpa"). For patrik the Place column is cut to one letter. | `audit2-d-home.png`, `audit2-l-home.png`, `audit2-p-home.png` | Give `.home-two` `minmax(0,…)` children an `overflow-x:auto` wrapper, or drop the "n" columns and shrink bars (60 px). Put the Recent map under the table below 1500 px, or make the table 3fr / map 2fr with a narrower Place column (ellipsis + tooltip). | `pages/home.css` (`.home-two`, `table.vol`), `pages/HomePage.tsx` |
| 6 | Together list / session (hike) | **A hike is labelled Run.** List row "RUN Run" for 29 Jun; the session header reads "Hike · 青柯坪 … · Run" while Adrian's card says Hike. Patrik recorded "Run". Group kind and sport come from one participant. The place name is in Chinese (geocoder). | `audit2-d-together.png`, `audit2-d-ts-hike.png` | Group sport = the viewer's sport (or "Hike / Run" when they differ). Header kind = the label shown in the title. Prefer a latin/English geocode name (`accept-language=en`) or `places.json` override. | `scripts/together.py` (group sport/kind), `pages/TogetherPage.tsx`, `pages/TogetherSession.tsx`, geocode in `scripts/session_groups.py` |
| 7 | Same session across pages | **One workout, three names.** Adrian 15 Oct is "Multisport 10–30 km" (Activities), "Multisport · 12.1 km/h" (Home), and "INTERVALS 1×1300 + 2×3000" (Together). patrik 6 Oct is "8 reps (mixed 1:17–7:40)" on his Home and "3×1800 + 2×900" in Together. A first-time user can't tell these are the same thing. | `audit2-d-activities.png`, `audit2-d-home.png`, `audit2-d-together.png`, `audit2-p-home.png` | Use one workout label per activity. When Together has a better (shared) structure, write it back as the activity's `workout_label`, or show "1×1300 + 2×3000 (with Patrik)" on the activity rows. At minimum, put a 👥 + Together label in the Home/Day rows. | `app/server/store.py` / `routers/home.py` (workout_label), `scripts/together.py`, `pages/HomePage.tsx`, `pages/ActivitiesPage.tsx` |

## P2 — polish that clearly helps understanding

| # | Page | What's wrong | Evidence | Fix | Files |
|---|---|---|---|---|---|
| 8 | Home | **No joint training on Home.** Recent workouts and the training log have no 👥 marker or link (Activities has one). The latest Together session (Tue 20 Oct, "Adrian 10–0") is invisible from the landing page. | `audit2-d-home.png` | Add 👥 to log entries and Recent rows (link → session). Add a small "Latest together" card under the shape strip: "15×400 Bislett with Patrik · Adrian 10–0 · +7.8 s/rep →". | `pages/HomePage.tsx`, `routers/home.py` (add `together` to `recent`), `pages/home.css` |
| 9 | Together session | **No way to Compare.** The session has "activity ↗" links but no "Compare these" (both persons' activities can't be compared anyway: Compare is per person). Together → Compare is missing. | `audit2-d-ts-intervals.png` | Add "Compare with my other 15×400s" (`/compare?suggest=<my id>`). Longer term, cross-person ids in Compare (`patrik:<id>`). | `pages/TogetherSession.tsx`; cross-person: `routers/compare.py`, `lib/api.ts` |
| 10 | Compare | **Control overload and controls that jump.** Route position shows Reference ×2, Start ×3, 5 modes, Pace/Speed, Gap in ×2. Intervals swaps Start for Show (All/Per rep/R1–R6), Rep axis, Break width and Match reps, on another row. Per rep / R3 hide Rep axis and Break width again, so the rest shifts each click. "4 sessions · 0.04 s" exposes timing. | `audit2-d-cmp-route.png`, `audit2-d-cmp-int.png`, `audit2-d-cmp-perrep.png`, `audit2-d-cmp-breaks.png` | Two fixed rows. Row 1: sessions + Suggestions. Row 2: mode tabs left, then **one** contextual group: Intervals → Show (All / Per rep / Rep ▾), Breaks → Break ▾. Move Reference, Start, Pace/Speed, Gap in, Rep axis, Break width and Match reps into a "⚙ Options" popover with the active non-defaults as chips. Disable rather than hide. Drop the timing (or put it in a tooltip). | `pages/ComparePage.tsx`, `pages/compare.css` |
| 11 | Compare | **Session colours clash with meaning.** Sessions are white/blue/**red**/**green**, while green/red also mean faster/slower (Who was faster bars, tinted reps table). The 2026-08-04 session is green but slower; 08-11 is red. In light theme the ref is black, in dark white. | `audit2-d-cmp-int.png`, `audit2-l-cmp-perrep.png` | Session palette without pure red/green (blue, orange, purple, teal, pink, olive …). Keep green/red only for better/worse. | `lib/compare.ts` (colours) |
| 12 | Compare › Per rep | **1 s pace lines are noise.** Six panels of overlapping zig-zags. Fast spikes are clipped at the top (3:00). The ref isn't distinguishable at small size. | `audit2-d-cmp-perrep.png`, `audit2-l-cmp-perrep.png` | Smooth 5–10 s (rolling median) in Per rep and Rep N (raw on hover / toggle "raw"). Thicker ref line. Gap-within-rep as the primary small multiple (it is the readable one). y range from the p5–p95 of the rep. | `components/CompareCharts.tsx`, `pages/ComparePage.tsx` |
| 13 | Compare | **The gap sign reads backwards.** Axis "+ behind; up = ahead" puts "−2:00" at the top and "+10 s" at the bottom. The table shows "−1:53" (green). Users read "+" as better. | `audit2-d-cmp-route-rep1.png`, `audit2-d-cmp-r3.png` | Label ticks "2:00 ahead" / "2:00 behind" (no signs) on the axis and "1:53 ahead" in tables. | `lib/chartAxes.ts`, `components/CompareCharts.tsx`, `pages/ComparePage.tsx` |
| 14 | Compare › Rep N | "Who was faster" stays the all-rep mean when R3 is selected. `rep=R3` in a URL (as the UI labels it) silently selects nothing and hides the All controls; the real value is `rep=3`. | `audit2-d-cmp-r3.png` | Follow the selected rep ("Rep 3: who was faster"). Accept `R3` / `r3` as aliases. | `pages/ComparePage.tsx` |
| 15 | Compare (ids, no mode) | Opening an interval set without `mode` lands on **Route position** with 400 m "legs" on a track: the least useful view for 6×1000 at Bislett. | `audit2-d-cmp-route.png` | Without `mode`, use `suggest.mode` (intervals for interval sets, as `?suggest=` does). | `pages/ComparePage.tsx` |
| 16 | Rep pace numbers | "6×1000 @ 3:29" (Home, Activities, Shape: median rep time) vs "Rep pace 3:27" (Compare: mean of reps) vs Together "77.2 avg" (s). patrik 20 Oct "10×400 @ 3:29" vs Together avg 85.1 s/400 m = 3:33. | `audit2-d-home.png`, `audit2-d-cmp-int.png`, `audit2-p-home.png` vs `audit2-p-ts-intervals.png` | Choose one definition (mean of the main reps, distance-weighted) for every label. Name it "avg rep pace" everywhere, with the median in the tooltip. | `scripts/similarity.py` / workout label, `app/server/routers/home.py`, `pages/ComparePage.tsx` |
| 17 | Units | Hike pace is "17:10" (min/km) in Activities but "3.5 km/h" on Home. Multisport/ski are km/h. Leaderboard "Total/Avg/Best" are seconds without a unit ("77.2"). | `audit2-d-activities.png`, `audit2-d-home.png`, `audit2-d-ts-intervals.png` | One `speedText(sport)` used by every list. In Together, rep times as "1:17.2". | `lib/format.ts`, `pages/ActivitiesPage.tsx`, `pages/TogetherSession.tsx` |
| 18 | Together session (race) | **"Together" stats on a race** ("10 % of the time together", "1 % within 20 m", "46 m median apart") are irrelevant and read as a fault. "Distance apart … grey: not together" is all grey. Splits stop at km 8 of a 10 km race ("on the shared route" 8.9 km). | `audit2-d-ts-race.png` | Race: show result, gap over distance, splits (all 10 km, own distance), who led. Hide the 4 together tiles or show "Patrik lost contact at 1.2 km". | `pages/TogetherSession.tsx`, `scripts/together.py` (splits on own distance for races) |
| 19 | Together session (easy / hike) | "Gap over distance" for a 97 % together run is 0–4 s spikes (noise). For the hike it spikes to 2000 s ("together 88 %"), which contradicts the headline. On the map only the top (orange) track is visible, so Adrian's line is hidden under Patrik's. | `audit2-d-ts-easy.png`, `audit2-d-ts-hike.png`, `audit2-d-replay-play.png` | For "together" sessions, lead with "Distance apart" and a who-led ribbon. Clip the gap chart to moving time (ignore stops). Draw the tracks with offset/thinner top line or alternating dashes. | `pages/TogetherSession.tsx`, `components/TogetherMap.tsx` |
| 20 | Together session | Rep 15 Adrian **64.5 s** for 400 m (2:41/km, faster than every raced rep) is likely a short/mis-detected rep but is shown plainly. Leaderboard "Fade +0.7" in red / "−2.0" in green has no explanation. "🫀" column header in Breaks. | `audit2-d-ts-intervals.png`, `c1` crop | Flag implausible reps (≈ / faded with tooltip). Tooltip "Fade = last 3 − first 3 reps (s); + = slowed". Header "Faster recovery". | `pages/TogetherSession.tsx`, `scripts/together.py` |
| 21 | Together list | "Together" is both the page and a label ("Together 4"). Unverified rows say "Unverified · no track" twice (badge + headline). "17 sessions" vs "17 together" in the h2h strip. The sidebar badge "3" is unexplained. | `audit2-d-together.png` | Rename the label "Side by side". Unverified headline → "Patrik has no watch file". Tooltip on the badge "3 sessions together in the last 30 days". | `pages/TogetherPage.tsx`, `main.tsx` |
| 22 | Fitness | **Developer content on a user page.** "Performance vs with HR — held-out evaluation (scripts/fitness_eval.py)", MAE / Log-lik / R3 corr, "Demoted by HR". The Demoted table is clipped ("lower bo…"). The Sessions table is clipped at the right inside a nested scroll. | `audit2-d-fitness.png`, `audit2-l-fitness.png` | Move the eval + demoted tables into the collapsed "How this is computed". Sessions table: drop "What it shows" to a tooltip or let it wrap, and use the page scroll, not a nested one. | `pages/FitnessPage.tsx`, `src/fitness.css` |
| 23 | Activity › Shape | Jargon: "τ scale 0.35 — W′ recovery speed vs Skiba", "Score − shape", "p max", "HR weight". Also "Load / TE", "fwd" in the header. | `audit2-d-activity.png` | Keep the one-liner + score + race equivalents visible. Put components / HR diagnostics behind "Details". Spell "Training load / effect", drop "fwd". | `components/ShapeSection.tsx`, `pages/ActivityPage.tsx` |
| 24 | Activity › Closest, Compare R3 route | Tables clipped at the right with a nested scroll ("Why" column cut; Compare sessions `m/beat` cut; 19-session table scrolls inside itself). | `audit2-d-activity.png`, `audit2-d-cmp-r3route.png`, `audit2-d-cmp-route-rep1.png` | Let page height grow (no max-height on `.tablewrap` in Compare). Make "Why" a tooltip icon. Move rare columns (m/beat, Climb, Max) into a "More columns" toggle. | `components/NearestPanel.tsx`, `pages/ComparePage.tsx`, `components/table.css` |
| 25 | Mobile Together session / Compare | The leaderboard and rep matrix are clipped (Wins…, Patrik %, Gap). The Compare rep buttons overflow (R6 cut). | `audit2-m-ts-intervals.png`, `audit2-m-compare.png` | Wrap in `.tablewrap` overflow-x. Hide Best/Fade/Effort below 500 px. Rep buttons → a select on narrow screens. | `pages/together.css`, `pages/compare.css` |
| 26 | Person switching | The switcher is a 10 px "▾" next to the name, with no colour/initial. Switching from a joint activity goes to patrik's Activities list rather than *his* activity of the same session. Nothing on screen says "you are viewing patrik" apart from the name. | `audit2-d-person-menu.png`, `audit2-d-person-switched.png` | Show the person's avatar circle (person colour) + name as the button. From an activity with `together`, switch to the partner's activity id. A subtle person-coloured top border. | `main.tsx`, `styles.css`, `lib/people.ts` |

## P3 — nice to have

| # | Page | Idea | Evidence | Files |
|---|---|---|---|---|
| 27 | Home / Activity / Day | Topo basemap (bright beige) is the default on the small maps in dark theme, which is glaring next to the dark UI. Default to Grey in dark theme (or dim raster 10 %). | `audit2-d-home.png`, `audit2-d-calendar.png` | `components/MapView.tsx` |
| 28 | Day | Title "Sat 2026-10-31" (ISO) next to the date input "10/31/2026" (US). "No daily summary for this day" while Home shows Fri's summary ("from Fri 30 Oct"). No Together marker in the day's activities. | `audit2-d-calendar.png` | `pages/DayPage.tsx` |
| 29 | Places | Map dot colours (sport? named vs start?) have no legend. The Bislett label is overlapped by its own circle. Route names are truncated in the list with no tooltip. | `audit2-d-places.png` | `pages/PlacesPage.tsx`, `components/GroupsMap.tsx` |
| 30 | Empty / error states | "No activities match these filters." sits flush against the edge with no padding and no "Clear filters" button in place. "no together group nope" / "no such activity" are raw API strings in red. | `audit2-d-empty-search.png`, `audit2-d-bad-*.png` | `pages/ActivitiesPage.tsx`, `pages/TogetherSession.tsx`, `pages/ActivityPage.tsx` |
| 31 | Unverified session | Adrian has GPS but no map is shown. The text "index start points 22 m / −202 s apart" is dev wording, and "patrik:" is lowercase in the caveat chip. | `audit2-d-ts-unverified.png` | `pages/TogetherSession.tsx` |
| 32 | Replay | The standings panel mixes units in one column (Adrian "0:23" time, Patrik "−17 m"). A large empty area sits under it. The header "Replay ▶" stays when replay is open. | `audit2-d-replay-play.png` | `components/Replay.tsx` |
| 33 | Compare › Breaks | The B1…B6 buttons vs the "After R1…After R5" table columns use two vocabularies (and B6 = post-session recovery). | `audit2-d-cmp-breaks.png` | `pages/ComparePage.tsx` |
| 34 | Activity | The single pace spike at ~0:47 / ~0:52 (one sample) and shaded reps on a flat track Elevation panel add noise. Hide elevation when climb = 0. | `audit2-d-activity.png`, `audit2-d-act-joint.png` | `components/SeriesCharts.tsx` |

## App-level proposals (one coherent product)

1. **One page header pattern with a one-line "what is this".** Use title + one sentence + primary action on every
   page. Examples:
   - Compare: "Line up sessions of the same workout or route and see where you gained or lost time"
   - Fitness: "Your estimated running/ski shape over time (VDOT)"
   - Places: "Where you train and the routes you repeat"

   Today some pages have a subtitle (Together, Fitness) and others none (Map, Places, Compare). **S**
   (`main.tsx` + a `<PageHeader>` component in `components/`, used by all `pages/*`).
2. **Make joint training a first-class thread:**
   - Home "Latest together" card.
   - 👥 markers in the Home log / Recent / Day.
   - Activity-row workout labels from the Together structure.
   - Together session → "Compare with my other 15×400s".
   - The person switch keeps the joint session/activity.

   **M** (`HomePage`, `DayPage`, `TogetherSession`, `main.tsx`, `routers/home.py`, `scripts/together.py`).
3. **One vocabulary + one formatter layer.** Use a small glossary in code and the UI:
   - activity (row) vs session (inside Compare/Together)
   - Rep 3 (long) / R3 (compact), never "3" alone in UI
   - avg rep pace (one definition)
   - pace for run/hike-on-foot? (decide)
   - km/h for ski/bike/hike
   - "ahead/behind" instead of signs

   Enforce it through `lib/format.ts` helpers (`speedText(sport)`, `gapText`, `repLabel`). **M**
4. **Progressive disclosure for expert controls.** Compare: mode tabs + one contextual control group, the rest
   in "⚙ Options" with non-default chips. Fitness/Shape: model internals in "How this is computed". Activity:
   Segments collapsed to the relevant tab. Wide tables get a "More columns" toggle instead of clipping. **M**
5. **First-run help.** Use a dismissible "Start here" strip on Home (localStorage) with 4 links:
   - "Your last workout"
   - "Compare it with similar ones"
   - "Training together with Patrik"
   - "How fit am I?"

   Add a `?` icon in each page header that opens the one-liner + 3 tips (hover map ↔ chart, drag to zoom, URLs
   are shareable). **S**
6. (Bonus) **Mobile baseline:** Home link, every table in `.tablewrap`, maps `width:100%`, button groups → selects
   under 500 px. **S–M** (`styles.css`, `home.css`, `together.css`, `compare.css`).

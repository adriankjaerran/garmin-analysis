# Community — plan (2026-11-01)

The user's brief: the app gets two halves. **Personal** (everything today: my runs and activities) and **Community**
(the running club): common trainings, who was there, a few categories, a nice UI; clicking a session shows who was
there, their splits, the collective time running and other numbers that **spark motivation in a running club**.
Optional photo upload per session. The list says how many members were there (that we have the time of). How each
person did **compared to their current form** ("+2"). A **replay** of the joint workout: all circles start and run
around when you press an interval, who passes whom in that interval, and their total time so far. "Rather too much
than too little; I will cut down."

Builds on Together (`scripts/together.py`, `/api/together…`, `pages/Together*.tsx`, docs/together.md): detection,
standings, replay already exist. Community = Together, upgraded and made club-shaped. Works for N people.

## Decisions (made while the user is away; easy to change)
| # | Question | Default |
|---|---|---|
| 1 | Name / URLs | Sidebar gets two groups, **Personal** and **Community**. Community pages live at `/community…`; `/together…` redirects. Code keeps the `together` names internally. |
| 2 | Categories | Six: `intervals`, `tempo`, `long`, `easy`, `race`, `other` (hike, ski, ride, swim, …). Automatic from the group's kind / workout / distance / duration; a manual override per session. |
| 3 | User data | `data/community.json` (hand-editable, written by the API): per gid `{title, category, note, photos, guests, hidden, kudos}`. Photos in `data/_community/photos/<gid>/` (jpg/png/webp/heic ≤ 15 MB; a ≤ 1600 px jpg + a 480 px thumbnail are stored). Everything stays local. |
| 4 | "Members there" | Members with a watch file in the group (`n_timed`); manually added guests (no watch file) are listed separately ("+2 guests"). |
| 5 | Form score | Per participant: the session's performance on the fitness (VDOT) scale minus their shape on the day before → "+2.1" (better than current form) / "−1.3". `null` when the session has no score (easy, hike). `scripts/form_delta.py`. |
| 6 | Collective numbers | Sum over members: km, moving time, reps, metres climbed; "together time" (person-minutes side by side); a club total for the season. |

## API (new; `app/server/routers/community.py` on top of `scripts/community.py`)
All work unprefixed and under `/api/<person>/…`.
```
GET  /api/community/sessions?category=&person=&from=&to=&q=&near=1
     -> {n, people, categories: [{id, label, emoji, n}], groups: [<together list row> + community fields]}
        community fields per row: {category, category_auto, title, note, n_timed, guests: [...], n_guests,
        photo: {thumb, url} | null, n_photos, kudos, collective: {km, moving_s, reps, climb_m, person_min_together},
        form: {person: delta | null}}
GET  /api/community/sessions/{gid}  -> {gid, meta: {title, category, category_auto, note, guests, photos: [{id, url, thumb, by, at}], kudos},
                                        collective: {...}, form: {person key: {score, shape, delta, discipline} | null},
                                        records: [{kind, text, person}]  /* PBs / club bests set in this session */}
PATCH /api/community/sessions/{gid}  body {title?, category?, note?, guests?}  -> meta
POST  /api/community/sessions/{gid}/photos  multipart file=  (?by=person)       -> photo
DELETE /api/community/sessions/{gid}/photos/{photo_id}
POST  /api/community/sessions/{gid}/kudos  body {person}  -> {kudos}
GET   /api/community/photos/{gid}/{file}   (served image)
GET   /api/community/club?from=&to=   -> club dashboard: totals (sessions, km, hours, person-hours together),
        per month series, attendance per member (sessions, streak in weeks, last seen), pairs matrix (sessions together),
        category mix, records (club bests per standard rep / route), "most loyal", weekday / hour heatmap,
        recent highlights (closest finish, biggest form jump, longest session).
GET   /api/community/members/{person} -> member profile: sessions attended, form deltas over time, partners, best sessions, shape trend.
```
The session page keeps using `/api/together/{gid}` (+ `/replay`) for standings / splits / replay.

## Replay upgrade (`components/Replay.tsx`, `together.replay`)
Press a rep → every circle starts at the rep's start line on the same clock and runs round; live order with
"passes" (who overtook whom, when, where) listed under the map; total time so far (cumulative over the reps up to this
one) next to each person. Also play the whole session. Speed control, scrub, keyboard (space, ←/→).

## Pages
- **Community · Sessions** `/community`: cards (photo or mini map, category chip, title / workout, place, date, member
  avatars, "3 members · +1 guest", headline, collective km / time), filters (category chips, people, period, search),
  group by month. Head-to-head strip stays.
- **Session** `/community/<gid>`: hero (photo / map, title editable, category select, note), members strip with form
  deltas ("+2.1"), collective panel, records set, then the existing standings / splits / replay. Photo gallery +
  upload. Kudos.
- **Club** `/community/club`: the dashboard (totals, monthly chart, attendance leaderboard, streaks, pair matrix,
  category mix, records, highlights, heatmap).
- **Member** `/community/members/<person>`: profile.

## Status (2026-11-01, end of day)
- [x] Phase 1: backend API (`scripts/community.py`, `routers/community.py`), form deltas (`scripts/form_delta.py`),
  replay upgrade, app audit.
- [x] Phase 2: Community pages — Sessions feed (cards / list, month groups, season strip, head-to-head, filters, goal
  link), session page (hero, note, story, members + form chips, collective, records, last time here, highlights,
  replay, splits, breaks, photos, kudos, share card), Club (totals, goal, badges, faster together?, rivalry, months,
  highlights, attendance, category mix, pairs, records, heatmap, places, recent), Members + member profile, Wrapped.
- [x] Phase 3: audits + QA in the browser (adrian / patrik, light / dark, 1600 / 390 px; screenshots
  `data/adrian/screenshots/2026-11-01/qa-*.png`), docs (docs/community.md, docs/app.md). `data/community.json` is
  empty (no test titles / photos / kudos) and `data/_community/photos/` has no files.

### Fixed in the last QA pass
- Mobile top bar 4 → 3 rows: the theme toggle moved into the person menu (the menu was also clipped by the bar: fixed).
- Intervals emoji ⏱️ (pale grey on Apple) → ⚡; fastest rep / km highlight and best-rep chip → 🏎️; badge "Day of
  days" → 🌟 (so ⚡ only means intervals).
- One avatar component (`Av` in CommunityBits) everywhere; `Avatar` / `.tg-av` removed.
- Club "This year" card said "Most loyal: Patrik · 17 sessions" (the all-time count, and a tie): now this year's
  count, hidden on a tie; Wrapped's "Always there" handles ties ("Everyone … every single one, together").
- Rivalry 27–0 vs head-to-head 26–0: the rivalry counts the race too; the card now says "shared reps 26–0 · race
  1–0 · 6 sessions · 1 tied rep"; the timeline labels show the day when a month has several sessions.
- "Faster together than alone?" showed averages that didn't add up to the verdict (−5.9 vs +2.4 → "1.1 better
  alone"): the averages are now over the compared sessions only (+1.3 vs +2.4 → −1.1).
- Member "Best sessions" listed a score the fitness model rejects (+13.9 "score unreliable"); form chips with
  confidence none now show "?" (dashed) like the session page.
- Session member card showed km/h rep pace for a run logged as Multisport: uses the group's sport.
- `fitness_eval.json` NaN → null (writer + router guard; tests); `data/patrik/fitness_eval.json` regenerated.
- Unknown member page: a proper message with a link back.

### Open questions for the user
1. **Rivalry score**: count races in the rivalry score (27–0, now explained) or show reps only (26–0, same as the
   head-to-head card) with races beside it?
2. **Members without a watch file** (Patrik in 7 hikes of July 2023) count as attending: 17 sessions / 100 % for
   both. Keep, or count only timed members for attendance, streaks and badges?
3. **Person-hours**: Sessions shows "43.0 person-hours side by side", Club "64 person-hours out there together" (two
   different measures). Keep both or pick one name everywhere?
4. **Emoji**: happy with ⚡ intervals / 🏎️ fastest rep, or prefer another set?
5. **Splits "Avg" row** averages all reps a person ran (suspect reps left out), the leaderboard's avg only the shared
   reps (1:17.6 vs 1:17.2 for the same person): label it, or align them?
6. "Last time here" uses scores the fitness model marks unreliable (Patrik 44.8 on 15 Oct): hide those?

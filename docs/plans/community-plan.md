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

## Status
- [ ] Phase 1: backend API, form deltas, replay upgrade, app audit (parallel workers)
- [ ] Phase 2: Community pages (nav + list + club + member; session page)
- [ ] Phase 3: audit fixes, QA with screenshots, docs

# Community: the running club (`scripts/community.py`, `/api/community…`)

The club layer on top of [Together](together.md): every joint workout Together detects becomes a club **session** with
a category (shown as "type" in the app), an optional title / note / guests / photos / kudos, collective numbers, each
member's result against their current form. Plus a club dashboard and member profiles. The pages are deliberately plain:
dense tables and text, no emoji, badges, avatars, medals or animations ([app.md](app.md)). Standings, splits and the
replay of a session stay on `/api/together/{gid}` (+ `/replay`, [together.md](together.md#api)).

```
uv run scripts/community.py list            # sessions: category, members + guests, km, hours, form
uv run scripts/community.py categories      # the automatic category of every group, with the reason
uv run scripts/community.py club | member <person> | show <gid>    # JSON (trimmed)
```

```python
import community as cm                     # scripts/ on sys.path
cm.base()                                  # memoised per input signature: together sessions, categories, collective, form
cm.list_sessions(category=, person=, date_from=, date_to=, q=, near=, hidden=, viewer=)
cm.get_session(gid); cm.club(date_from=, date_to=); cm.members(); cm.member(person)
cm.patch(gid, {...}); cm.kudos(gid, person, on=True); cm.add_photo(gid, bytes, filename, by); cm.delete_photo(gid, id)
cm.auto_category(group) -> (category, reason)
cm.company_effect(B, store, tdoc, person)
cm.goals(); cm.patch_goal(id, {...}); cm.add_goal({...}); cm.delete_goal(id)
```

## Files
| File | What |
|---|---|
| `data/community.json` | user metadata, hand-editable, written by the API (atomic: temp file + rename, under a write lock) |
| `data/_community/photos/<gid>/<id>.jpg`, `<id>_t.jpg` | photos: ≤ 1600 px jpg (q 85) and a 480 px thumbnail (q 80); id = first 16 hex of the upload's SHA-1 (the same file twice = one photo) |
| `scripts/form_delta.py` | optional (another module): `form_delta(person, activity_id) -> {score, shape, delta, discipline, …} \| None`. Missing or raising → `form: null` entries; reloaded when the file changes |

`data/community.json`:
```
{"version": 1, "updated": "2026-11-01T12:00:00Z",
 "goals": [{"id": "3f9a12bc", "title": "500 km together in 2026", "metric": "km", "target": 500,
            "from": "2026-01-01", "to": "2026-12-31", "created": "…Z"}],
 "sessions": {"2026-10-20_2007_Bislet": {"title": "Track night", "category": "intervals", "note": "windy",
     "guests": ["Kari", "Ola"], "hidden": true, "cover": "<photo id>", "kudos": ["patrik", "adrian"],
     "photos": [{"id": "3f2a…", "by": "adrian", "at": "…Z", "taken": "2026-10-20T20:31:05", "name": "IMG_1.HEIC",
                 "w": 1600, "h": 1200, "orig_w": 4032, "orig_h": 3024, "bytes": 312345}]}}}
```
Empty fields are left out. Tolerant reading: a missing file = no metadata; a hand-written `{gid: {...}}` without the
wrapper works; wrong types are coerced or dropped (`"guests": "Kari, Ola"` → a list, `"kudos": {"patrik": 1}` → a list,
an unknown category → automatic); unknown keys are kept. A file that doesn't parse is reported as `store_error` and
moved to `data/community.bad-<unix time>.json` before the next write (never silently lost). Entries stored under an
old gid (together's `aliases`) are merged into the current gid and rewritten under it on the next edit.

## Categories
Six: `intervals`, `tempo`, `long` (Long run), `easy`, `race`, `other` (the API sends `{id, label, description}`). Automatic (`category_auto`, with
`category_reason`); a manual `category` in community.json wins (`category_manual: true`). Rules, first match:

1. **race**: group kind `race` / label `same_race`.
2. **intervals**: group kind `intervals` (detection found ≥ 2 reps started together; ski intervals too).
3. **other**: the group sport isn't run (hike, bike, ski, swim, other): hikes, rides, ski tours. Together's group sport
   is the majority over typed members, so a hike one person logged as "Run" is a hike.
4. **intervals**: every member logged an interval type (`intervals_*`, `ski_intervals`) with ≥ 2 reps (the near-miss
   groups where two people ran different rep sets at the same track, `near=1`).
5. **tempo**: the group workout is "Tempo run", or the most common specific type among the members is `tempo`.
6. **long**: the workout is "Long run" or a member typed `long`; or the time together ≥ 90 min (the together span of a
   `partly_together` group, else the shorter member's duration); or the shorter member's run ≥ 16 km (not for partly together).
7. **easy**: any other run (side by side easy runs; a run where one continued alone with reps counts by the joint part:
   2026-09-10 is easy).

On today's data: 6 intervals (incl. the hidden 2026-10-27 near-miss), 3 easy, 1 race, 1 tempo (hidden 2026-01-20),
8 other (hikes, a ride).

## Numbers
- **Members / `n_timed`**: distinct people with a watch file in the group (role `other` = near-miss partner left out);
  `timed` = their participant keys (`patrik#2` for a 2nd file). Guests are names without a watch file (`n_guests`).
- **Collective** (sum over the members): `km` (detected distance), `moving_s` (groups.json moving time, else the
  index's movingDuration), `reps` (every full rep each ran: leaderboard `reps_done`, else the own detector's count),
  `climb_m` (index elevationGain), `kcal`. Per member the same numbers (+ `avg_hr`, `dur_s`) are in the session's
  `members[].numbers`. There is no "together time" (person-minutes side by side).
- **Form**: `form_delta(person, activity_id)` per participant (role `other` → null). Lists carry `form: {key: delta}`
  and `form_info: {key: {delta, text, confidence, discipline, score, shape}}`; sessions / members the full dict.

## Motivation
Computed per request from the base (cheap: ~3 ms warm for club / member). Member sessions = visible
community sessions (not near-misses, not user-hidden), oldest first.

### Faster together than alone? (`company_effect`)
- **Form**: the member's form deltas (`form_delta`, confidence ≠ none) in club sessions vs their **solo** sessions
  (activities in no together group at all, hidden / near-miss ones included) of the **same discipline** within
  ±60 days of a club session (the "same period"). `diff` = the mean over club sessions of (club delta − mean of its
  window's solo deltas) — a paired comparison; `joint_avg` = the mean of the compared club sessions
  (`n_joint_cmp`), `solo_avg` = the mean of their solo baselines (over `n_solo_cmp` solo sessions), so
  `joint_avg − solo_avg = diff` (the page shows "together −1.1 (6) · alone +1.2 (27)"). `verdict`: `company`
  (diff ≥ +0.3), `alone` (≤ −0.3), `even`; `text` "Patrik runs +1.8 better with company" / "Adrian runs 2.0 better
  alone". `thin`: fewer than 3 compared club sessions or 3 solo sessions (shown as "thin data").
- **Rep pace** per family: interval sessions' reps from groups.json (`reps` [dur, dist, …]; reps within 0.6–1.6× the
  median distance, so warm-up blocks and strides drop out); family by the median rep: short < 600 m ≤ medium < 1.4 km
  ≤ long. Club interval sessions vs solo interval sessions of the same family within ±90 days: pace s/km each
  (`joint_s_km`, `solo_s_km`) and a `text` ("3 s/km faster per rep with company").
- On today's data: Patrik −1.3 (1 solo session to compare: thin), Adrian −2.0 (5 vs 27); Adrian's medium reps
  3:19 /km together vs 3:22 alone.

### Deliberately not built: rivalry, records, badges
The club decided (2026-11-01) against rivalry and records, and the pages were then made minimal:
- **No rivalry:** no head-to-head scores, win counts, rivalry timelines or "rep winner" headlines. Results stay: each
  rep is ranked among whoever ran it (fastest bold), the session leaderboard orders by average rep pace, races by
  finish time.
- **No club records:** no club bests, club PBs or record badges. Personal records (`/api/records`, Garmin's fastest
  splits, on the Fitness page) are a separate, personal feature and stay.
- **Also gone:** badges, the yearly Wrapped page, the club's pairs / pair matrix / weekday × hour heatmap / recent
  sessions / most loyal, the member's best sessions and shape trend, and the category emoji.

`tests/test_community.py` checks that the removed endpoints and fields stay gone.

### Goals
`goals` in community.json (list; id, title, metric `sessions | km | hours | reps | climb_m`, target
> 0, from ≤ to; unknown keys kept, invalid entries ignored). Progress over the visible sessions in the period: value,
pct, status `done | ahead (≥ 110 % of expected = target × elapsed share of the period) | on_track (≥ 90 %) | behind |
ended | upcoming`, days left, `by_member` (person-level: sessions attended / own km / …). The
primary `goal` = the first running one, else the one ending last; none stored → a suggested default
`{id: "default", title: "12 sessions this <season>", default: true}` (meteorological season of today).
`PATCH /goals/default` stores it (with a fresh id) on the first edit. Session edits keep `goals` (and any other
top-level key).

## API
All work unprefixed and under `/api/<person>/…` (the prefix sets `viewer`: `kudoed`, `mine`, default kudos / uploader).
404 for an unknown gid (aliases resolve) or a missing together.json. The base is built under compare's lock and memoised
by together's `inputs_sig()` + activities_index / groups.json / fitness.json / form_delta.py / community.py mtimes;
community.json is overlaid per request. First request after a change ≈ 0.4 s (base build; sessions come from
together's disk cache), then 2–6 ms per request.

### `GET /api/community/sessions?category=&person=&from=&to=&q=&near=1&hidden=1&sport=&kind=&label=&place=`
Together's list rows (newest first, all their fields) + community fields. `category` / `person` comma lists; `q` = all
words in gid, date, place, workout, people, title, note, guests, category; `near=1` together's hidden near-misses;
`hidden=1` also the sessions the user hid. `categories[].n` counts the rows after all filters but `category`.
```
{n, viewer, built, people: {id: {name, initials, colour, colour_dark, auto}},
 categories: [{id: "intervals", label: "Intervals", description, n: 5}, ...],
 form_available: true, store_error: null, compute_ms,
 groups: [{gid, date, start_local, start_utc, sport, sports, kind, label, label_text, score, hidden, place, workout,
           people, n_people, no_track, participants: [...], headline: {...},          # together's row
           category: "intervals", category_auto: "intervals", category_manual: false,
           title: null, display_title: "15×400" /* title or workout */, note: null,
           n_timed: 2, timed: ["adrian", "patrik"], n_members: 2, guests: [], n_guests: 0,
           photo: {thumb, url, w, h} | null /* cover, else the first */, n_photos: 0,
           kudos: ["patrik"], n_kudos: 1, kudoed: true | false | null /* no viewer */, mine: true | null,
           user_hidden: false,
           collective: {km: 17.9, moving_s: 5805, hours: 1.61, reps: 25, climb_m: 27, kcal: 1538, n_members: 2},
           form: {adrian: 2.4, patrik: 2.9},
           form_info: {adrian: {delta: 2.4, text: "+2.4 vs form", confidence: "high", discipline: "interval", score: 54.9, shape: 52.5}}}]}
```

### `GET /api/community/sessions/{gid}`
```
{gid, requested_gid, viewer, date, start_local, place, workout, kind, label, label_text, people: {id: display},
 display_title, category_info: {id, label, description}, categories: [{id, label, description, n: 0}],
 meta: {title, category, category_auto, category_reason: "same reps (15×400)", category_manual, note, guests: ["Kari"],
        photos: [{id, url: "/api/community/photos/<gid>/<id>.jpg", thumb: ".../<id>_t.jpg", by, at, taken, name, w, h}],
        cover, kudos: ["patrik"], hidden},
 n_timed, n_guests, kudoed,
 collective: {...as in the list},
 form: {key: {score, shape, delta, discipline, kind, confidence, text, …} | null}, form_available,
 members: [{key, person, display, role, activity_id, workout, type, has_fit, timed, form, numbers /* per_person */,
            rank, reps_done, n_counted, of, avg_s, pace_s_km, by_target, best /* intervals leaderboard */}],
 headline: {...together's}, nav: {newer: gid | null, older: gid | null} /* visible groups, list order */,
 same_place: [{gid, date, workout, category, people}] /* other sessions at the same place, newest first, ≤ 12 */,
 store_error}
```

### `PATCH /api/community/sessions/{gid}` body `{title?, category?, note?, guests?, hidden?, cover?}` → `meta`
`category`: one of the six, or `null` / `""` / `"auto"` = automatic. `guests`: list of names (or a comma string),
trimmed, de-duplicated case-insensitively, ≤ 60 chars each. `title` ≤ 120, `note` ≤ 4000 chars; `null` or empty clears.
`cover`: a photo id of this session (or null). Unknown fields / bad values → 422.

### Photos
- `POST /api/community/sessions/{gid}/photos?by=` multipart `file` (jpg / png / webp / heic via pillow-heif; ≤ 15 MB,
  413 above, 422 when it isn't an image) → `{id, url, thumb, by, at, taken, name, w, h}`. EXIF orientation applied,
  transparency flattened on white, `taken` from EXIF DateTimeOriginal. `by` defaults to the URL's person.
- `DELETE /api/community/sessions/{gid}/photos/{id}` → `meta` (404 if unknown; files removed; cover cleared).
- `GET /api/community/photos/{gid}/{id}.jpg | {id}_t.jpg` → the image (`Cache-Control: immutable`: ids are content hashes).

### Kudos
`POST /api/community/sessions/{gid}/kudos` body `{person}` (default the URL's person; idempotent) and
`DELETE /api/community/sessions/{gid}/kudos?person=` → `{gid, kudos: [people, in order given], n_kudos, kudoed}`.
Any name ≤ 40 chars is accepted (guests can give kudos too).

### `GET /api/community/club?from=&to=`
Over the visible sessions (not together's near-misses, not user-hidden) in the period.
```
{from, to, today, viewer, people, form_available, store_error, compute_ms,
 totals: {sessions, km, hours, reps, climb_m, kcal, members, guests, photos, kudos, places, first, last,
          this_year: {year, sessions, km, hours}},
 months: [{month: "2026-10", sessions, km, hours, reps, climb_m, members: [..], n_members,
           by_category: {intervals: 3}, gids: [..]}]          /* every month first..last, zeros included */,
 attendance: [{person, display, sessions, share /* of all sessions */, last_gid, days_since,
               streak_weeks /* consecutive weeks with a session, alive if the last is this or last week */,
               best_streak_weeks, weeks_active, km, hours, reps, by_category, avg_form_delta, n_form}],
 categories: [{id, label, description, n, km, hours}],
 highlights: [{kind: closest_finish | form_jump | longest_session | most_km | most_reps | most_climb |
               biggest_turnout (≥ 3), gid, date, person, other?, value, unit, text}]
             /* closest_finish = the rep (any rep ≥ 2 ran) / race with the smallest gap between the first two, worded
                neutrally: "Rep 4: Adrian and Patrik 1.4 s apart" */,
 places: [{id, name, sessions, last}], guests: [{name, visits}]}
```

### `GET /api/community/members` → `{people, members: [attendance rows as in club]}`

### `GET /api/community/members/{person}` (404 if not a member and not a known person)
```
{person, display, viewer, form_available, compute_ms,
 totals: {sessions, km, hours, reps, climb_m, first,
          last, streak_weeks, best_streak_weeks, weeks_active, avg_form_delta},
 by_category: {intervals: 5, …},
 sessions: [{gid, date, start_local, place, workout /* own */, group_workout, title, category, with: [people], role,
             form, numbers, rank, of_people, reps_done, avg_s, pace_s_km, n_photos, kudos}]   /* newest first */,
 form_series: [{date, gid, score, shape, delta, discipline, …}]     /* oldest first, sessions with a delta */,
 partners: [{person, display, sessions, last, gids}]}
```

### Motivation endpoints
- `/api/community/club` adds `company: {person: {diff, verdict, text, n_joint, n_solo, n_compared, n_joint_cmp, n_solo_cmp, joint_avg, solo_avg,
  thin, pace: [{family, label, text, n_joint, n_solo, joint_s_km, solo_s_km}]}}` (over the period when
  `from` / `to` are given), `goal`, `goals`; each `attendance` row (and `/members` rows) gets `company`.
- `/api/community/members/{person}` adds `company` (as above + `window_days`, `joint: [{gid, date, delta, discipline}]`,
  `solo: [{activity_id, date, delta, discipline, type, name}]`, pace `joint` / `solo` lists).
- `GET /api/community/goals` → `{goal, goals: [{id, title, metric, target, from, to, default?, unit,
  value, pct, days, days_left, status, text, n_sessions, last_gid,
  by_member: [{person, value}]}], metrics: [{id, unit, label}]}`; `POST /api/community/goals` `{title?, metric,
  target, from, to}`; `PATCH /api/community/goals/{id}` (any of those; `default` materialises the suggestion);
  `DELETE /api/community/goals/{id}` — all return the goals payload; bad values / unknown fields → 422, unknown id → 404.

## Tests
`tests/test_community.py` (skips without data): list / filters / categories, PATCH + kudos + guests + hidden, photo
upload (resize, thumbnail, dedupe, PNG with alpha, cover, delete, path traversal), a broken / hand-edited
community.json, club invariants (totals = months = categories), members, the removed endpoints / fields.
community.json and the photo folder are monkeypatched to a temp dir; the test fails if the real file's mtime changes.
Motivation: company effect (no joint activity counted as solo), rep families, no rivalry / together time
(`test_no_rivalry_or_together_time`), goals (default, PATCH default, POST / DELETE, 422 / 404, km goal = club km, goals survive session edits, statuses).

## Limits
- Form deltas come from `scripts/form_delta.py`; an implausible delta there (e.g. a session where one member ran reps
  alone after the joint easy part) shows up as the club's "biggest form jump".

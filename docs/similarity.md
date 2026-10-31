# Similarity engine: places, regions, routes, workout hierarchy, search, nearest

`scripts/similarity.py` is a library for the future API, with a CLI on top. It builds on
`groups.json` from [session-groups.md](session-groups.md): start places, areas, routes, session
types, interval signatures, reps and breaks. It also re-reads every FIT track (the fast reader, ~3 s
for all files, cached) to find where an activity actually happens.

```
uv run scripts/session_groups.py adrian                 # groups.json (run first / after new data)
uv run scripts/similarity.py adrian build               # similarity_index.json + tracks.json (~3 s)
uv run scripts/similarity.py adrian validate            # the checks below
uv run scripts/similarity.py adrian search --rep-count 5-7 --rep-dist-m 900-1100 --rest 120-200
uv run scripts/similarity.py adrian search --rep-len 4:00 --tol 0.15 --place Bislet
uv run scripts/similarity.py adrian search --signature '~×1:15' --region Dubai
uv run scripts/similarity.py adrian nearest 24874084265 -k 10 --preset workout -w pace=0.3
uv run scripts/similarity.py adrian groups route|place|start_place|region|family|signature|exact|workout [--touched]
```

```python
import similarity as sim
ix = sim.load_index("adrian")          # Index; rebuilt automatically if missing or older than groups.json
ix.acts[24874084265]                   # feature record (schema below)
ix.search({"family": "run/intervals", "rep_count": [5, 7], "rep_len": "1000m", "rest_s": [120, 200]})
ix.nearest(24874084265, k=10, weights="workout")             # or a dict, see below
ix.groups("workout")                   # tree family > signature > exact
sim.search(...), sim.nearest(...), sim.groups(...)            # same, on the cached index of person="adrian"
```
Loading the index takes 0.04 s. One `nearest` call over all 717 activities takes about 5 ms.

## Signatures
| Function | Returns |
|---|---|
| `build(person="adrian", workers=8, quiet=False) -> dict` | writes both artifacts, returns the index doc |
| `load_index(person="adrian", rebuild=False) -> Index` | cached in-process (by file mtime) |
| `Index.search(filters=None, sort="-date", limit=None) -> list[record]` | AND of all filters |
| `Index.nearest(activity_id, k=10, weights=None, filters=None, same_sport=True, explain=4) -> list[dict]` | `{id, date, sport, name, dist_km, moving_s, place, region, route, route_name, family, signature, exact_key, score, components{c: 0..1}, reasons[str]}` |
| `Index.groups(kind, min_n=1, touched=False, filters=None) -> list[dict]` | `{key, label, n, first, last, ids, sports[, family, places]}`; `workout` gives a tree `{key, label, n, children:[{key, n, children:[{key, n, ids}]}]}` |
| `Index.components(q, c) -> (scores, texts)` | per-component scores for one pair |
| `Index.overlap(a, b) -> float or None` | route overlap of two activities |
| `encode_polyline / decode_polyline`, `encode_ints / decode_ints`, `decode_ints_pairs` | artifact encodings |

### Search filters (`Index.search`)
- **Equal to a value or in a list:** `sport` (run, ski, hike, cycling, …), `atype`, `family`, `category`,
  `signature` (prefix `~` = substring, e.g. `"~×1000"`), `main_sig`, `exact_key`, `route`, `rest_mode`.
- **`place`** matches the main place, the start cluster, the end place or any touched place.
  `place_mode="main"` matches the main place only. **`region`** works the same way
  (`region_mode="main"`). It also matches the Garmin municipality.
- **Dates:** `date_from`, `date_to` (`YYYY-MM-DD`), `year` (int or list), `months` (list).
- **Ranges** are `[lo, hi]` (None = open), `"lo-hi"`, or a single number (± `tol`, default 0.1 = 10 %).
  Range filters: `distance_km`, `duration_s` (moving), `climb_m`, `avg_hr`, `pace` (s/km, `"4:00-4:30"`), `reps_total`.
- **Reps:** `rep_count`, `rep_dist_m`, `rep_time_s`, `rest_s`, `rep_pace` and `rep_len`. `rep_len` is a
  distance or a duration depending on how it's written: `"1000m"`, `1000`, `"4:00"`, `"240s"`,
  `"900-1100"` or `"3:30-4:30"`, ± `tol`. All rep filters must hold for the **same part** of the
  workout: with `part="any"` (default) any part can match, with `part="main"` only the main part.
  Example: `"1×800 + 5×1000"` matches `rep_count 5, rep_len 1000m`.
- `dist_trusted`, `gps` (bool), `text` (substring of the activity name).

### Weights (`Index.nearest`)
Components, each scored 0..1:

| Component | Score |
|---|---|
| `route` | 75 m cell overlap (mean of both coverages) |
| `place` | 1 if same main place; 0.4–0.8 if touched places overlap (Jaccard); 0.3 same region; 0.15 same area |
| `workout` | 1 for the same `exact_key`; 0.80–0.95 for the same signature (scaled by rest similarity); ≤ 0.75 × structure similarity for other intervals (rep count, rep duration, rest; twice as tolerant between two ski sessions); 0.75 same family + same distance band; 0.6 same family; 0.35 related family (ski/intervals ~ ski/hard) |
| `pace` | rep pace for interval pairs, else moving pace; exp(−\|ln ratio\|/0.1) |
| `hr` | avg HR, exp(−Δ/8) |
| `distance` | exp(−\|ln ratio\|/0.2), trusted distance only |
| `duration` | moving time, exp(−\|ln ratio\|/0.25) |
| `climb` | m/km, exp(−Δ/10) |
| `season` | day of year, cosine |
| `recency` | exp(−days/730) |

`weights` can be a preset name: `balanced` (default), `workout`, `route` or `place` (see
`PRESETS`). It can also be a dict: `{"workout": 0.6, "pace": 0.4}` uses only those components, and
`{"preset": "route", "season": 0.2}` overrides one weight of a preset. A component a pair lacks
(no GPS, no HR, untrusted distance) scores 0.4 (`MISSING`). Score = Σ w·s / Σ w. `reasons` lists
the top components by contribution, each with a readable text: "same workout 6×1000 · rest ~2:30",
"same route R1 (100% overlap)", "reps 3:29/km vs 3:27/km". By default candidates are limited to the
same sport (`same_sport=False` lifts that). `filters` limits them further using any search filters.

## Places and regions
- **Named places** (`NAMED_PLACES`): Bislet (track, 130 m), Frognerparken (polygon), St. Hanshaugen,
  Sognsvann (700 m), Holmenkollen, Frognerseteren, NTNU Gløshaugen, Granåsen. Each is a circle or a polygon.
- **Regions** (`REGIONS`, rough polygons, ±300 m): **Marka** (Nordmarka + Lillomarka + northern
  Bærumsmarka, with the city edge as its south border), Østmarka, Bymarka (Trondheim).
- For each GPS track: the **time share** inside every named shape (point weight = time to the next
  point, capped at 30 s). A shape counts as **touched** at ≥ 5 % of the time or ≥ 3 min. One activity
  can touch several places and regions (`places`, `regions` with `share` and seconds).
- **Main place** (`place`): the named place with ≥ 40 % of the time (`place_src: "named"`). Otherwise
  it's the start cluster from session_groups (`place_src: "start"`, e.g. `Marienlyst`, `Fauske-1`; start
  clusters are named by KNOWN spots, then curated landmarks, else `<loc>-<rank>`, see
  [session-groups.md](session-groups.md)).
  **Main region** (`region`): the polygon with ≥ 40 % of the time, else the Garmin `locationName`
  (municipality), else the area name. Activities without GPS get a place and region from the
  index start coordinates, if there are any.
- **Overrides:** `data/<p>/regions.json`
  `{"places": {"Name": {"circle": [lat, lon, r_m], "kind": "park"}}, "regions": {"Name": {"poly": [[lat, lon], ...]}}}`.
  This adds new shapes or replaces existing ones; a value of `null` removes one. Start-cluster names
  still come from `places.json` (session_groups).
- **Route names** come from where the members go: start place → end place (named place within
  150 m of the end, otherwise the start cluster), plus `via` = named places touched by ≥ 50 % of
  the members. A point-to-point route with ≥ 25 m/km climb and descent < 40 % of the climb is
  marked `is_climb` and named `"<first via> climb: …"`, for example
  `Holmenkollen climb: Marienlyst → Frognerseteren via Holmenkollen 9.1 km`.

## Workout hierarchy
`workout = {family, family_label, category, detail, signature, main_sig, exact_key, ...}`
- **family**: `run/intervals`, `run/tempo`, `run/race`, `run/long`, `run/easy`, `run/nogps`, `run/other`,
  `ski/intervals|easy|hard|long`, or the sport for everything else (`hike`, `cycling`, `lap_swimming`, …;
  labels Hike, Cycling, Pool swim, Open water swim, Rowing, Kayaking, Stopwatch (no GPS), Multisport).
  `category` is the session_groups type (`intervals_track`, `intervals_road`, `ski_intervals`, …),
  `detail` is its `type_detail`.
- **signature**: for intervals, the session_groups `sig` (`6×1000`, `10×4:00`, `3×1800 + 2×900`; ski:
  durations, `4×4:00`). `main_sig` is the main part alone (the part with the most rep time):
  `1×800 + 5×1000` → `5×1000`. Parts come from `session_groups.rep_groups` (ski: by duration only).
  Other sessions get family label + distance band (`Easy run 8–12 km`). Bands for runs:
  0–5, 5–8, 8–12, 12–16, 16–21, 21–30 and 30+ km; for ski: 0–10, 10–20, 20–30, 30–45 and 45+ km.
- **exact_key**: for intervals, `"<signature> · rest ~<m:ss>"`. Rest = median break between
  consecutive reps of the main part, so breaks next to strides or a warm-up rep don't count. Within
  a signature, the rest medians are clustered 1-D: a new cluster starts where neighbouring values
  differ by > max(20 s, 20 %), and a cluster wider than 1.5× is split at its largest gap. The label
  is the cluster median, bucketed (15 s steps below 1:00, 30 s up to 3:00, 1:00 up to 6:00). Clusters are
  per family + signature (a run `4×4:00` and a ski `4×4:00` don't share rest clusters).
  This avoids fixed bucket edges: Bislet 6×1000 rests run continuously from 135 to 194 s, and fixed
  30 s buckets split them 8 / 8 at 2:45. For other sessions, `exact_key = "<family> · <route>"`, for
  example `ski/easy · R3` (easy Holmenkollen climbs). **Ski intervals found by effort on a route**
  (terrain-defined reps: the climbs between the route's stops) also get the route key,
  `ski/intervals · R3`, and `exact_by: "route"`: their rep durations follow the terrain and vary from
  session to session (`4×4:30`, `4×6:00`, `3×5:00`), so sig + rest would make every session its own
  group. Lap workouts keep `"<signature> · rest ~<m:ss>"`.
- Per-interval stats: `parts` [{n, dist_m, time_s, speed_ms, hr, rest_s, rest_min_s, rest_max_s,
  rest_stand_share, rest_dist_m}], `main_part`, `rep_count`, `reps_total`, `rep_dist_m`, `rep_time_s`,
  `rep_speed_ms`, `rep_hr`, `rest_s`, `rest_min_s`, `rest_max_s`, `rest_label` (own fixed bucket),
  `rest_cluster` [lo, hi], `rest_mode` (stand ≥ 60 % standing / jog ≤ 20 % / walk/mixed), `work_s`.
  Standing seconds and rest distance come from the rep-detector cache (`cache/session_groups.pkl`,
  read-only).

## Artifacts
**`data/<p>/similarity_index.json`** (~1.1 MB):
```
{version, built, person, groups_built,
 params: {named_places, regions, place_min_share, main_share, presets, missing_score, cell_m, overview_pts, detail_tol_m},
 places:  {name: {n (touched or main), n_main, kind, src: named|start, lat, lon}},
 regions: {name: {n, n_main, kind, src: polygon|garmin}},
 routes:  {R1: {name, sport, shape, dist_km, n, start_place, end_place, via[], place, region, climb_m,
                is_climb, medoid, members[], poly}},
 activities: {id: {id, date, start ("YYYY-MM-DD HH:MM" local), name, sport, atype, gps, dist_km, dist_trusted,
                   dur_s, moving_s, climb_m, desc_m, avg_hr, max_hr, speed_ms, loc, area,
                   start_place, start_place_name, end_place, place, place_src, places[{name, share, s}],
                   region, regions[{name, share, s}], route, route_name, route_dir, shape,
                   workout{...}, bbox [s, w, n, e], start_ll, end_ll,
                   poly (≤ 200 pts), cells}}}
```
Keys whose value is null are left out. `poly` is a Google encoded polyline (precision 5 ≈ 1 m),
Douglas-Peucker simplified to ≤ 200 points (tolerance 3 → 80 m, as needed). Leaflet / Mapbox
decode it directly, and drawing hundreds of tracks is cheap. `cells` are the 75 m route-grid cells
(same grid as session_groups), as (x, y) pairs coded per coordinate with the same varint
(`decode_ints_pairs`). They give the route overlap for any pair without the tracks.

**`data/<p>/tracks.json`** (~0.55 MB, 583 tracks): `{format, tol_m: 3, tracks: {id: {p: encoded polyline
(DP 3 m), alt: delta-coded int metres on the same points, -999 = none}}}`. Use it for detail
maps and elevation profiles. Cache: `data/<p>/cache/similarity_tracks.pkl` (thinned tracks per
FIT, keyed by mtime + size).

## Validation (adrian, 717 activities, 583 with GPS; `validate`)
- **Bislet**: 94 activities have Bislet as main place (56 intervals, 28 easy, 3 race?, …), 108 touch
  it. The session_groups start cluster "Bislet" has 107; 14 of those actually happen elsewhere:
  St. Hanshaugen (8, a 4.8 km out & back, route R9) and Frognerparken (6).
- **Fixed in session_groups.py**: every start cluster within 600 m of a known spot got that spot's id.
  Their `places` entries overwrote each other, so `places["Bislet"].n` was 1 while 113 activities
  pointed to it. Now the known name goes to the nearest cluster only. Other clusters get `<loc>-N`
  ids (Bislet start cluster 107). The output schema is unchanged.
- **Holmenkollen**: R3 = `Holmenkollen climb: Marienlyst → Frognerseteren via Holmenkollen 9.1 km`,
  19 rollerski sessions, +489 m, 100 % mutual overlap. 25 activities touch Holmenkollen (23 ski,
  1 run, 1 hike). By intensity the climbs split into `ski/easy · R3` (11), `ski/intervals · R3` (7, hard
  climbs between the stops, see [session-groups.md](session-groups.md#ski-intervals-scriptsski_intervalspy-detector-ski-10))
  and `ski/hard · R3` (1). `nearest` (workout preset) of a hard interval climb (2026-08-10) returns the 6
  other interval climbs first (0.75–0.88); of the remaining hard climb (2024-09-30), three interval climbs rank first (0.60–0.62).
- **Ski intervals**: `ski/intervals` 18: the 7 R3 climbs and 11 lap workouts 2021–2024
  (`4×4:00 · rest ~2:00` 6, `3×4:00 · rest ~2:00` 2, `8×4:00 · rest ~1:00`, `7×4:00 · rest ~1:00`,
  `5×1:00 · rest ~0:30`). `nearest` of the `8×4:00` returns the `7×4:00` first (0.56).
- **Marka**: 85 activities touch it, 61 are mostly inside it (40 ski, 20 run, starting mainly at
  Sognsvann and Oslo-3); Bymarka 12. Frognerparken is the main place of 20 activities and touched by 25
  (one more main than before: a start cluster in the park is now named Frognerparken). St. Hanshaugen
  touched 16 / main 10 (start cluster Oslo-16 → St. Hanshaugen).
- **6×1000**: 19 sessions. 17 at Bislet (2024-11 → 2026-10, rest medians 135–194 s) form
  `6×1000 · rest ~2:30`, and two Trondheim road sessions in 2022 (rest 61 / 78 s) form
  `6×1000 · rest ~1:00` (start place Øya, was Trondheim-2). A search for 5–7 reps of 900–1100 m also finds 5×1000 (Trondheim 2020–23,
  rest 1:00 / 1:30 / 2:00) and Bislet `1×300 + 5×1000`, `1×800 + 5×1000` (main part 5×1000). Other
  exact groups: Dubai `8×1:15 · rest ~0:30` (4) and `12×1:15 · rest ~0:45` (4); `4×1000 · rest ~2:00` (3).
- **Other sports** (all 64 hikes, 6 rides, 1 open-water swim, 3 rowing, 1 kayak, 1 multisport, 1 "other"
  have GPS tracks and polylines; 5 pool swims and 8 stopwatch sessions have none and get no place /
  region, which is fine for search and `nearest`). One hike route repeats: R67, Fauske-1, 2 hikes.
- **nearest spot checks**:
  - Bislet 6×1000 (2026-10-27, `workout` preset): the top 6 are all `6×1000 · rest ~2:30` on R1 (0.89–0.92).
  - Trondheim 6×1000 / 1:00 rest: the other 1:00-rest 6×1000 (0.87) comes first, then 5×1000 on
    the same route (0.63), then Bislet 6×1000 with long rest (~0.58).
  - Frognerparken easy run (`place` preset): the same route R22 first, then Frognerparken runs off any route.
  - Fauske easy run: R2 members.
  - A hike in Vang with no repeats: other hikes at ~0.46, explained by season / HR / duration only.

## Limitations and open points
- Region polygons and named-place shapes are hand-drawn approximations (no network, no OSM).
  Refine them in `regions.json`. Only Oslo / Trondheim spots are named. Start clusters near a curated
  landmark are named (Oslo-1 → Marienlyst, Oslo-2 → Aker Brygge, Trondheim-2 → Øya, …); the busiest
  remaining generic ones (`Fauske-1`, `Trondheim-1`, `Oslo-3`) need names in `data/<p>/places.json`.
- Route ids `R1..` come from session_groups and are renumbered by size on every run. Store
  activity ids, not route ids, in the UI. Routes cover only one sport family. All 64 hikes have a
  FIT file now; only one hike route repeats (R67, 2 hikes at Fauske).
- Rest clusters are data-dependent: a new session that fills a gap can merge two clusters and
  change an `exact_key`. `rest_label` (fixed bucket) and `rest_s` stay stable.
- The `rest_mode` standing share is noisy (most Bislet rests are 50–65 % standing → walk/mixed).
- Ski reps come from `ski_intervals.py` (lap workouts, hard efforts); other ski sessions stop at
  family + distance band / route. Ski interval signatures on terrain vary per session, hence the route
  `exact_key` for them.
- In the `workout` tree, a non-interval route's members can fall into different distance bands,
  so one `exact_key` can appear under two signatures.

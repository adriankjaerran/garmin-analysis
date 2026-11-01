# Distance calibration (Adrian, 2026-10-30)

Script: `scripts/calibrate_distance.py adrian` -> `data/adrian/calibration.json`.
Report: Runs -> Distance check. Next steps: [plans/timing-and-distance-accuracy.md](plans/timing-and-distance-accuracy.md).

## Truth we used
- **Bislet track geometry.** A stadium model of a standard 400 m track (straights 84.39 m,
  lane-1 measuring radius 36.80 m), placed on the map from 13,941 track-mode GPS points
  (orientation from the principal axis). Every GPS point is projected onto lane 1, giving an
  arc position (0–400 m) and an offset outward (lane). Lane-1 distance = sum of |Δ arc|, which
  doesn't depend on running direction. Running at offset `o` m adds `2π·o` m per lap
  (lane 2 ≈ +7.7 m per lap).
- **Certified races:** Copenhagen Half 2025 (21,097.5 m), Rome Marathon 2019 (42,195 m).

## Results

### Track mode at Bislet (11 sessions, 80 reps)
| Source | Median vs track | p10 | p90 |
|---|---|---|---|
| Garmin lap distance | −0.08 % | −0.53 % | +0.48 % |
| Watch distance field (FIT `record.distance`) | +0.00 % | −0.21 % | +0.25 % |
| Raw GPS positions | −0.27 % | −0.29 % | −0.24 % |
| Our detected reps | −0.82 % | −2.51 % | +0.60 % |

- In track mode the watch **already corrects positions and distance onto the track**: all
  track-mode points sit at a constant −1.1 m offset (synthetic, not raw GPS). So lane changes
  **can't** be detected from track-mode data; distance is always lane 1 (or the lane set on the watch).
- Garmin snaps lap distances to round numbers (400, 1000 m). For a 6 × 1000 m session,
  6,000 m is what Garmin reports, and the geometry agrees to about 0.1 %.
- The watch's **speed field lags 3–4 s**, and in track mode the distance field stalls for
  1–3 s around each lap mark. Rep detection now uses speed from distance differences: the
  error against Garmin laps went from −20 m to −6 m per rep (median). The rest comes from
  ~1 s of smoothing at each end.

### Normal running mode at Bislet (GPS only, 6 runs)
- The watch reads **+1.7 % to +5.9 % more than lane 1**, but the GPS puts you 0.9–3.4 m
  outside lane 1 (outer lanes on easy days). Correcting for that lane, the watch is
  **−2.0 % to +0.6 % (median +0.4 %)** of the expected distance. The GPS distance holds up.
- "Almost half marathon" 2025-09-07: 51.5 laps on track, lane-1 distance 20,604 m,
  expected in your lane ≈ 21,212 m; the watch measured 21,332 m on track (+0.6 %). Garmin's
  total was 21,865 m, including the parts off the track.

### Certified races
| Race | Certified | Garmin | Difference |
|---|---|---|---|
| Copenhagen Half 2025 | 21,098 m | 21,350 m | +253 m (+1.20 %) |
| Rome Marathon 2019 | 42,195 m | 42,397 m | +202 m (+0.48 %) |

Courses are measured along the shortest possible line. Runners weave and take corners
wide, so +0.5–1.5 % is normal and isn't watch error alone.

### Signal timing at rep starts (89 reps, vs lap button)
Speed field +4 s, cadence +5 s (noisy), distance-derived speed 0 s. In normal (GPS) mode there's no
steady lag either (below).

### Normal (GPS) mode, manual laps at the line (41 sessions, 402 presses, both watches)
Script: `scripts/gps_lap_analysis.py adrian` -> `data/adrian/gps_lap_analysis.json`. GPS positions are
projected onto the track model; the line is at arc 284.39 m (standing at the line before a start, GPS
puts Adrian +0.3 m from it, so the line placement is right).

| Metric | Median | p10..p90 | n |
|---|---|---|---|
| Running through: GPS crosses line minus press | **−0.26 s** | −0.79..+1.03 | 128 |
| Same, new watch (1 s recording) only | −0.33 s | −0.43..−0.24 | 14 |
| Distance-field speed vs GPS-position speed (per session) | −0.07 s | −0.18..+0.09 | 41 |
| Garmin lap boundary vs press (running) | **1.5 s ≈ 6.7 m early** | 1.2..1.8 | 128 |
| Rep start: time to move 5 m, GPS mode / track mode | 1.25 s / 0.40 s | | 56 / 36 |
| Rep end: speed halves, GPS mode / track mode | 1.7 s / 0.0 s | | 84 / 61 |
| Lap error, line-to-line running laps (lane corrected) | +0.18 % | −1.2..+1.8 % | 93 |
| Lap error, rep laps (start→end) | **−0.87 % (−8.5 m)** | −2.2..+0.1 % | 47 |

- **No steady hidden GPS lag.** At constant pace, position and distance field reach the line within
  ~0.3 s of the press (≲ 2 m), with no detectable pace dependence within a session.
- **Garmin books the lap distance ~1.5 s before the press.** Cancels on continuous laps, but makes
  rep laps ~5 m short (more at fast ends than at rolling starts).
- **GPS mode reacts 1–1.7 s late to speed *changes*** (acceleration/deceleration), unlike track mode.
  ~2–3 m per rep. With the booking offset this explains most of the −8.5 m on reps.
- Caveats: lap timestamps are whole seconds (truncation assumed; rounding would shift everything
  +0.5 s), FR935 smart recording is interpolated, press timing can't be separated from lag in absolute
  terms (consistent within a session; session medians vary ±1 s, attributed to ±3 m GPS bias).

### Signal lags vs lap press, step model (Phase 2; `gps_lap_analysis.py` → `lag_table`)
A step (1 s ramp) is fitted to each signal around every rep start/end press, lag by least squares,
90 % CI of the median bootstrapped by session. Median start / end lag in seconds:

| Mode / watch | GPS position | Distance field | Speed field | Cadence | Power |
|---|---|---|---|---|---|
| GPS / FR935 (26–27 sessions) | 1.29 / 1.69 | **0.90 / 1.62** | 3.8 / 4.8 | 4.3 / 5.8 | – |
| GPS / 4315 (5 sessions, small n) | 0.20 / 0.69 | **0.39 / 0.79** | 3.0 / 5.0 | 4.5 / 4.8 | 4.8 / 4.5 |
| Track / 4315 (13 sessions) | −0.5 / −0.2 | −0.4 / −0.7 | 3.1 / 5.1 | 4.2 / 4.6 | 4.4 / 4.7 |

- **The new watch reacts about twice as fast in GPS mode** as the FR935, so lags are per watch.
- **Lag is constant in time, not in distance** (filter delay). The slope vs pace is ~0 (−0.14..−0.03 ± 0.2 s per m/s),
  and metres lost grow weakly with pace. On the FR935 that's ~4–9 m per speed change.
- Track-mode negative lags are an artefact of snapping and stalls, so compensation is clamped to 0.
- Lags include Adrian's reaction at the press and the acceleration itself, so they're relative to the press, not physics.

### Rep detector v2 (Phase 3; `evaluate_detector.py`, details in [run-analysis.md](run-analysis.md))
Change points (PELT) on distance-derived speed, sub-second refit, minus the lag fitted on this
detector's own offsets (`calibration.json` `detector_lags`). Leave-one-session-out on 58 Bislet
sessions / 365 rep laps: median |start err| 0.44–0.50 s, |duration err| 0.46–0.82 s, rep distance vs
geometry −0.40..+0.21 % (median) by watch/mode. v1.1 was 1–3.6 s and −0.8..−1.2 %.

### Outside Bislet (Phase 4; `route_accuracy.py`, `gps_quality.py` → `route_accuracy.json`)
All 492 runs downloaded (backfill 2026-10-30, 393 calls at 3 s, no 429).

| Race | Truth | Garmin | GPS positions |
|---|---|---|---|
| Copenhagen Half 2025 | certified | +1.20 % | +1.16 % |
| Rome Marathon 2019 | certified | +0.48 % | +0.91 % |
| Lier 10k 2025 | nominal | +0.32 % | +0.02 % |
| Lier 10k 2023 | nominal | +0.51 % | +0.20 % |
| Oslo 10k 2023 | nominal | +0.17 % | +0.14 % |
| Trondheim 5k 2021 | nominal | +0.56 % | (GPS glitch) |

- **Repeated routes (precision):** 12 groups of ≥ 2 runs, 3 clusters of ≥ 3 runs (all Bodø, FR935).
  Run-to-run robust SD **0.70 %** (p90 |dev| 1.38 %, 39 runs). The new watch has no repeated routes
  yet (27 outdoor runs), so its precision outside Bislet isn't measured.
- **OSM map matching: not used.** Nearest-way snapping swung −5..+9 % with small rule changes (parallel
  sidewalks, junctions), because it needs a real map-matching algorithm. 6 Overpass calls, cached in `data/adrian/osm_cache/`.
- **No correction factor.** Races read +0.2..+1.2 % long, which is normal for shortest-line courses. Bislet
  has no stable sign (FR935 laps +0.33 %, 4315 −0.32 %).
- **Per-activity uncertainty (1σ):** `sqrt(base[watch][env]² + (5 m / distance)²)`. base: track
  0.41 % (Bislet), open 0.81 % (FR935, fitted: √(0.70² + 0.41²)), 4315 open 0.80 % (assumed), urban/trees
  1.2–1.5 % (assumed), unknown 1.6 %, indoor 5 %. GPS jitter turned out not to predict error (slope ≤ 0),
  so it's not in the model. 72 % of cluster runs fall within 1σ.
- Caveats: the environment guess is a jitter threshold and doesn't really find cities (Copenhagen and Rome count as
  "open"). Satellites and accuracy can't be decoded from `gps_metadata`. A third device (product 3077, 6 runs in 2021) exists.

### Bislett indoor lap, winter (27 sessions; `session_groups.bislett_indoor`, 2026-10-31)
In winter the reps are run on the indoor lap under the Bislett stands: **546.5 m per lap** (Adrian), and each rep
stops 7–8 m before the line, so **rep = n laps × 546.5 m − 7.5 m** (1 lap 539.0 m, 2 laps 1,085.5 m, 3 laps 1,632.0 m).
- **Detection, two ways:** (a) main place Bislet and GPS track length < 30 % of the watch distance (7 sessions at
  0.3–6 %; every other Bislet run is > 80 %; the few fixes sit 40–60 m outside lane 1, under the stands). (b) No GPS
  position at all, Oct–Mar, running or "Indoor Running" mode (not treadmill), with ≥ 3 reps of a clear lap count
  (20 sessions, 2023-11 to 2026-03, mostly Tuesdays). These get their start set to Bislet (place / area Bislet).
- **The watch distance is made up there.** Rest laps at 10–70 steps/min (standing / walking: 7–75 strides) still
  log 200–1,100 m; on 2026-01-20 the 1-lap reps read 464–556 m. Session distance: `dist_trusted: false`.
- **Reps = laps at ≥ 160 steps/min** (FIT lap `total_cycles`) that end on a manual press (at the line); warm-up /
  cool-down laps of a watch workout are skipped; an auto lap (1 km) plus the press a few s later is one rep. Reps on
  timer laps (e.g. 2019 Trondheim, 2023-03: 4:00 / 1:30 timer workouts) are not whole laps and don't count.
- **n laps from time:** per block of similar-time reps (±15 %), the only n in 1, 2, 3 whose pace is 4:15–2:45 /km
  (100 s → 1, 220 s → 2, 345 s → 3); no unique n → time-only. Per rep, 2:40–4:30 /km or no distance.
- **Override** `data/adrian/indoor_bislett.json` `time_only`: 2025-01-21 (5 × 5:00, jogged rests: 3 laps = 3:03 /km,
  2 laps = 4:36 /km, neither fits) has no rep distance.
- No-GPS sessions in the fitness model: 19 new interval items (47.5–56.2), 2026 interval evidence 28 → 37, 2025
  40 → 47. The first 7 (GPS lost at Bislet):

| Date | Reps | n laps | Pace /km | Old sig (speed detector) | Fitness interval item, old → new |
|---|---|---|---|---|---|
| 2024-12-10 | 3 × 6:00 | 3 | 3:31–3:40 | 1×1500 + 3×7:00 | 55.6 → 50.6 |
| 2024-12-17 | 9 × 1:28–1:43 | 1 | 2:51–3:10 | 8 reps (mixed) | 57.3 → 55.0 |
| 2025-01-07 | 6 × ~4:00 | 2 | 3:29–3:50 | 4×4:00 + 1×3000 | 50.8 → 50.1 |
| 2025-01-28 | 6 × 4:00 | 2 | 3:26–3:42 | 7 reps (mixed) | 52.1 → 48.2 |
| 2025-02-04 | 6 × 3:45 | 2 | 3:17–3:34 | 3×4:00 + 1×3600 | 55.0 → 51.1 |
| 2026-01-13 | 3 × 5:45, 3:45, 1:30 | 3, 2, 1 | 3:27–3:37 (1 lap 2:53) | 5 reps (mixed) | 50.0 → 50.9 |
| 2026-01-20 | 10 × ~1:42 | 1 | 3:05–3:15 (last 2:53) | 1×2400 + 1×7700 + 2×8:45 | 66.2 → 54.4 |

The old "long" bound items of all 7 (and the implausible 68.6 on 2026-01-20) are gone; the 2026 interval max
went 56.2 → 54.9, no other session changed. Between reps the speed is 0 (standing rests).

## Which number to believe
1. **Track mode, lap button pressed:** Garmin lap distance (exactly the nominal lane-1
   distance). If you're forced out to lane 2 for a bend, add ≈ 3.8 m per bend (7.7 m per
   lap). The watch can't see it.
2. **Rep timing:** detector v2 (speed jump, lag-compensated), ±0.5 s typical. The lap press is a cross-check.
   The report flags presses more than 1 s off.
3. **GPS-mode rep distance:** the detector's distance (median −0.4..+0.2 % vs geometry), *not* Garmin's lap
   distance (−0.7..−1.25 %, booked ~1.5 s before the press).
4. **Normal GPS runs:** the watch distance, with the report's ± (1σ, typically 0.8 %). Don't add a buffer.
   Against a measured course it reads ~0.2–1.2 % long, mostly course measurement (shortest line).

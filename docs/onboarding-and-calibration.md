# Getting data, onboarding a runner, calibrating a watch

Learned on Adrian's account (717 activities, 2 watches), 2026-10-30. API details:
[garmin-api-guide.md](garmin-api-guide.md), [endpoints-tested.md](endpoints-tested.md).
Accuracy background: [distance-calibration.md](distance-calibration.md), plan:
[plans/timing-and-distance-accuracy.md](plans/timing-and-distance-accuracy.md).

## 1. Best way to get the data

| Step | Command | Calls | Time |
|---|---|---|---|
| Activity list + yearly daily summaries | `fetch_index.py <person>` | ~20 | < 1 min |
| Priority watch files (track, own track runs, races) | `fetch_activities.py <person> --ids-file ids.txt --parts activity,fit --min-interval 1.2` | 2 / activity | ~2.5 s / activity |
| Full backfill, all runs | same with `--parts fit --min-interval 3`, in chunks of a few hundred | 1 / activity | ~500 runs ≈ 25 min |
| Daily job | see guide §5 | 10–20 | seconds |

**Rule: for analysis, download only the FIT** (the watch's own file for the activity, see
[fit-files.md](fit-files.md)). Add `activity` only for runs shown on the report's Session page.

What each call is worth:
- **FIT file** (`--parts fit`): everything for analysis. Per-record HR, cadence, distance,
  position (GPS every 1 s on the new watch, every ~3 s on the FR935), laps with `lap_trigger`/`intensity`, device and firmware. 20–100 KB.
- **`activity`**: `splitSummaries` (Garmin interval totals), used by the report. Keep it for runs
  you show in the report.
- **`details`**: ~10 s downsampled copy of what's in the FIT. Only useful if the FIT is missing. Skip it.
- **`workouts`**: always `[]` so far. Off by default.
- **Activity list** already has per-activity averages for *all* activities without any
  download: avg/max HR (100 % of runs), cadence (485/492 runs), stride, and since the new watch
  power/GCT/VO. HR time-in-zone only from 2024-12, fastest splits from 2024-09.

Pacing: ≤ 1 req/s has never given a 429 (~450 requests so far). Bursts of 150–200 back-to-back
did. Search pages: `limit=200` works, `limit≥500` returns 500.

Which watch recorded what, without downloading: in the list, `avgPower` non-null ⇒ new watch
(Adrian: holds for all 492 runs). `requiredField=avgPower` on the search filters to those.
Otherwise the watch is only visible in the FIT (`file_id.garmin_product`, `serial_number`).
There's no device endpoint, and `eventType` is always `uncategorized`: races must be told to us
or guessed (standard distance, fast pace, few laps).

## 2. Onboarding a new runner
1. They create a Personal API key (1 year), we store `GARMIN_<NAME>_API_KEY` in `.env`.
2. `fetch_index.py <name>`.
3. Ask them (the same four questions we asked Adrian):
   - races with official chip times/splits, and any route with a known length;
   - watch model(s) and **when they switched**, plus GPS mode (GPS only / all systems / multi-band).
     It isn't in the FIT;
   - on track sessions: track mode on? lap button pressed at the line? lane setting?
   - OK to download (show the call count and time).
4. Find watch eras from the list (`avgPower` proxy, or the FIT of one run per year), then
   download one FIT per era to see the record interval and fields.
5. `select_activities.py <name> --track-lat .. --track-lon ..` builds the id lists (track, near track,
   guessed races), then `fetch_activities.py <name> --ids-file data/<name>/candidates/priority.txt --parts activity,fit --min-interval 1.2`.
6. Calibration, in this order (each < 1 min on ~500 runs). Needs a track geometry for *their* track. Bislet is
   hard-coded today, so a new track means fitting a new stadium model from its track-mode points:
   `calibrate_distance.py` (geometry) → `gps_lap_analysis.py` (lag table) → `evaluate_detector.py`
   (detector lags, accuracy) → `route_accuracy.py` (races, repeated routes, uncertainty model) →
   `calibrate_distance.py` again (merges all into `calibration.json`).
7. `build_report.py <name>`.

## 3. Calibrating a new watch
Signal behaviour is a property of the watch (and firmware), not of the place, so recalibrate
on each **new watch** and check after big firmware jumps. Minimum set:

| What | How | Data needed |
|---|---|---|
| Record interval | gaps between `record` timestamps | any run |
| Signal lags (speed field, cadence, power, distance field, GPS position) vs lap press | `gps_lap_analysis.py` step model → `lag_table`; `evaluate_detector.py` → `detector_lags` | ≥ 5 track sessions with lap presses at rep starts/ends (≈ 50 transitions) |
| Distance scale, best case | track mode vs geometry; GPS mode vs lane-corrected geometry | ≥ 3 track-mode sessions + ≥ 3 GPS-mode runs on the track with manual laps |
| Hidden GPS lag, lap booking offset (GPS mode) | position crossing the line vs lap press (`gps_lap_analysis.py`) | GPS-mode track runs with manual laps at the line |
| Real-world distance, uncertainty model | `route_accuracy.py`: certified races, repeated routes | ≥ 3 repeated routes with ≥ 3 runs each, any races |

Per-watch caveats seen so far:
- **Forerunner 935** (2018 → 2026-02-17): smart recording (median gap 2–5 s), so timing
  boundaries can't be sharper than the gaps. No power/running dynamics. All Adrian's
  GPS-mode Bislet laps are from this watch.
- **Product 4315** (2026-02-24 →): 1 s recording, wrist power and running dynamics,
  `gps_metadata` at 1 Hz. All Adrian's track-mode sessions are from this watch.

So today **track mode (timing lags) and GPS mode (distance/lag) are measured on different watches**.
Lags measured on one shouldn't be applied to the other without checking. To close the gap: a few
GPS-mode runs at Bislet with the new watch, pressing lap at the line.

## 4. Status for Adrian (2026-10-30, evening)
- Downloaded: all 492 runs (FIT), 100 earlier + 392 in the backfill (393 calls at 3 s, ~20 min, no 429).
- Lag table per watch (step model), detector v2 (±0.5 s timing), per-activity distance uncertainty: see
  [distance-calibration.md](distance-calibration.md) and [run-analysis.md](run-analysis.md).
- Still thin: **new watch in GPS mode at Bislet** (5 sessions, ~40 presses) and **no repeated routes on the
  new watch**. A few GPS-mode runs at Bislet with lap at the line, and repeating one or two regular loops,
  would close both gaps.

# Original activity files (FIT)

**What it is:** FIT (Flexible and Interoperable Data Transfer) is Garmin's binary file format. The
watch writes one `.fit` file per activity, from start to stop, and syncs it to Garmin Connect
unchanged. Everything Garmin shows about a run is derived from this file. It's a list of
messages: `record` (one row per second, or per 1–5 s on the FR935: time, GPS, distance, speed,
HR, cadence, power...), `lap` (each lap press / auto-lap), `event` (start/stop/pause),
`session` (totals), `device_info`, `file_id` (watch model, serial). It covers **only the
activity**, not the all-day wear. For analysis it's the only download we need per activity.

Downloaded via `GET /download-service/files/activity/{activityId}` with
`Accept: application/zip, application/x-zip-compressed, application/octet-stream`
(narrower Accept headers return 406). Response is a ZIP with one `<activityId>_ACTIVITY.fit`.
Our downloader unpacks it to `data/<person>/activities/<date>_<id>_<type>/<id>.fit`.

Parse with `fitparse` (in `pyproject.toml`):

```python
import fitparse
ff = fitparse.FitFile("24768988970.fit")
records = [{f.name: f.value for f in m if f.value is not None} for m in ff.get_messages("record")]
```

## What's inside (tested)

Two watches so far (from FIT `file_id`, checked on 32 files). The switch was between 2026-02-17 and
2026-02-24, most likely 2026-02-18 (two identical ski sessions recorded 1 s apart that night).

| | Forerunner 935 (`fr935`), 2018-12 → 2026-02-17 | Product id 4315 (probably Forerunner 965), 2026-02-24 → |
|---|---|---|
| Serial / firmware | 3985557371 / 12.1 → 21.0 | 3625270964 / 27.09 → 29.05 |
| Record interval | **Smart recording**: median gap 2–5 s, only 15–32 % of gaps 1 s, max 6–58 s | **1 s** |
| Size | ~20–80 KB | ~100 KB per 45 min |
| `record` fields | timestamp, position, heart_rate, speed + enhanced_speed, distance, altitude, cadence, fractional_cadence, temperature | timestamp, position, heart_rate, enhanced_speed, distance, enhanced_altitude, cadence, fractional_cadence, power, accumulated_power, step_length, stance_time (GCT), vertical_oscillation, vertical_ratio, activity_type |
| Sensors | wrist HR, accelerometer, barometer; no chest strap / foot pod | same + sensor hub; power and running dynamics are **wrist-based** |
| Other messages | lap, event, device_info, device_settings, user_profile, workout_step | lap, session, event, device_info, `gps_metadata` (1 Hz), many `unknown_*` (233/325/326) |

- HR and cadence are in **every** run file from both watches (and in every run's summary in the list).
- GPS mode (GPS only / all systems / multi-band) is **not decodable** from either watch's FIT.
- Without downloading: in the activity list, `avgPower` present ⇔ the new watch (holds for all 492 runs).
- Smart recording on the FR935 means per-second timing analysis needs interpolation; boundary
  precision is limited to the gap size (often 2–5 s).

## Laps
`lap` messages carry `start_time`, `total_elapsed_time`, `total_timer_time`, `total_distance`,
`avg/max_heart_rate`, `enhanced_avg_speed`, **`intensity`** (warmup / active / rest / cooldown,
from structured workouts) and **`lap_trigger`** (manual / distance / session_end).
- Bislet interval sessions: manual laps tagged active/rest; in track mode distances are snapped
  (400, 1000 m...).
- Normal-mode Bislet runs: manual laps pressed at the line (≈ 400–430 m each in GPS distance).
- Races: auto laps every 1000 m (`distance`) or a single `session_end` lap.

## Signal quirks (measured on 89 rep starts; see [run-analysis.md](run-analysis.md))
| Field | Behaviour |
|---|---|
| `enhanced_speed` | Smoothed by the watch, **~4 s late** (p10 3 s, p90 5 s) |
| `distance` | Reacts at once (median 0 s). In **track mode** it's model-corrected and stalls 1–3 s around lap marks |
| `position_lat/long` | In track mode snapped to the track model (constant offset), so lane changes aren't visible |
| `cadence` | Late and noisy at transitions (median +5 s, −5..+8 s) |
| `heart_rate` | Physiological lag of tens of seconds |

Use Δ`distance` per second as speed for timing, never `enhanced_speed`.

## GPS coverage (100 downloaded files, 2026-10-30)
| Watch, type | Files | Records with GPS (median) | GPS point every (median) | Points/hour |
|---|---|---|---|---|
| FR935, running | 74 | 100 % (min 2 %) | 3.1 s | ~1,150 |
| 4315, running | 10 | 99 % (1 file without GPS) | 1.1 s | ~3,340 |
| 4315, track_running | 16 | 100 % (min 76 %), snapped to the track | 1.0 s | ~3,600 |

HR and cadence are in 100 % of records in all files. Without the FIT: the activity list has only
start/end coordinates; `details.json` has a ~245-point polyline (≈ every 10 s).

## Notes
- `position_lat/long` are in semicircles: degrees = value * 180 / 2**31.
- Timestamps are UTC.
- For per-second analysis (pace, HR drift, cadence) use the FIT file; `/details` is downsampled
  to ~10 s.

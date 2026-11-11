# Check lap presses before detecting reps (lesson from 10 Nov 2026)

**People press lap at every rep start and end even without a structured workout.** Those manual laps (FIT `lap`
messages, `lap_trigger = manual`) are the true rep times. Before relying on the speed-change detector
([run-analysis.md](run-analysis.md)) for anyone's reps, especially other people's (`patrik`, `rise`, ...), look at
their laps: `run_analysis.load(fit)` returns them with `t_start`, `time` and `trigger`. `analyse()` also gives
`press_start_dt` and `press_end_dt` for each rep.

## What happened

On the Bislett Bunker of 10 Nov 2026 (6 reps: 3-3-2-2-1-1 laps), Patrik (Forerunner 245 Music) and Ole Jørgen (`rise`,
Fenix 3 HR) both pressed lap at every rep. Their start presses were within ~1 s of Adrian's. garmin-integration
(`scripts/import_partner_runs.py`) first took the detector's reps instead, and they were wrong:

| | lap presses | detector | why |
|---|---|---|---|
| patrik | 390 / 389 / 249 / 250 / 103 / 97 s | 6–12 s short | smart recording: a 5–11 s gap in the records after each press, the detected start falls after it |
| rise | 390 / 389 / 253 / 257 / 115 / 109 s | 10–36 s long, rep 6 missed | distance grows while standing (2.9 m/s at cadence 0); speed frozen at 1.568 m/s through rep 6 |

Notes:

- **Patrik's extra presses:** he also presses at every track lap, so one rep is several manual laps. The rest between reps is its own manual lap (150–166 s, 20–50 m).
- **Ole Jørgen's warm-up:** recorded as one long lap.
- **Nothing else marks the reps:** neither file has workout / workout_step / split messages or timer pauses.
- **Garmin Connect's splits endpoints:** they would only repeat the FIT laps, or give Garmin's own run/walk/stand estimate. `fetch_activities.py` doesn't fetch them and doesn't need to.

## Rule for next time

1. **When fetching someone's session to compare reps,** check their manual laps first. If presses line up with the reps (start within ~15 s of a known rep start), use them. garmin-integration's `from_presses` does this:
   - The rep starts at that press.
   - It ends at the press that starts their rest.
   - Presses in between (per track lap) are ignored.
2. **Use the detector only where there is no press.** Treat it as an estimate: ±3 s at best, worse with a footpod or GPS indoors (frozen speed, distance while standing). Cadence is a good check of when someone actually stood still.
3. **Watch for the same failures in Together ([together.md](together.md)):** it uses the detector too, so they can show up there as well.

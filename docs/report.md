# The HTML report

Build: `uv run scripts/build_report.py <person>` → `data/<person>/report.html` (one self-contained
file, gitignored because it holds health data). Template: `scripts/report_template.html`
(plain HTML, CSS custom properties, inline SVG, no dependencies; follows the Fabstack console
UI rules: sidebar, dense tables, four type sizes, status dots, no metric cards).

Open it with `open -a "Google Chrome" "file://$PWD/data/adrian/report.html#step=runs&tab=session"`.
The step, tab and selected item are in the URL hash, so every view can be linked
(e.g. `#step=runs&tab=groups&item=R3` = Holmenkollen rollerski climb; the "Sessions" step keeps the id `runs`).
`groups.json` (scripts/session_groups.py, see docs/session-groups.md) is embedded as `data.groups`
without pause/waypoint/reference lat-lon. Ski FIT series are thinned to 10 s, without cadence/power
and without the run rep detector.

## Pages
| Step / tab | Shows | Data source |
|---|---|---|
| Coverage | Each data type: from, to, count, resolution, state | everything |
| Activities / List | All activities, sortable, search + type filter; a row opens a side panel (per-second charts and laps if the FIT is downloaded) | `activities_index.json`, FIT |
| Activities / By year | Totals per year; running and skiing km per month | `activities_index.json` |
| Sessions / Session | One run or ski session. Runs: watch file vs Garmin activity, pace and HR with reps shaded, reps vs Garmin laps with boundary source, ±1σ boundary uncertainty and lap-press check, distance ± 1σ %, breaks with HR recovery, km splits. Ski (no rep detection): speed/HR/elevation with pauses shaded. Both: place, route (link), type, workout signature, **Legs & pauses** tables, **Similar sessions** (top 5 same route / same workout, each with a Compare link) | FIT + `activity.json` + `calibration.json` + `groups.json` |
| Sessions / Compare | `item=id1,id2,…` (default: latest 3): metric table with Δ vs the first column; if all share a route, its leg/stop comparison for just these; side-by-side pace (or ski speed) and HR on the same scales (first 4 with a watch file) | FIT + `groups.json` |
| Sessions / Places & routes | Browser: sport select + type chips → places (`item=place:<name>`) → routes → sessions. Route view `item=R3`: reference elevation profile with common stops, speed per leg (median, fastest, latest, picked session), per-leg summary, leg speed per session (best per leg green, hover = moving time), pause per stop, moving vs paused over time | `groups.json` |
| Sessions / Distance check | Distance sources vs Bislet geometry, signal lag vs lap press (table + lag-vs-pace charts), rep detector v1.1 vs v2, races, repeated routes, distance uncertainty model | `calibration.json` |
| Health / Days, Trends, Heart rate, Sleep | Daily table per year, 30-day trends since 2019, 2-minute HR (downloaded days), sleep nights | daily summaries, `daily/*` |

## Refresh
```
uv run scripts/fetch_index.py adrian                       # activity list + daily summaries
uv run scripts/fetch_activities.py adrian --days 14 --min-interval 1
uv run scripts/fetch_daily.py adrian --days 14 --min-interval 1
uv run scripts/calibrate_distance.py adrian
uv run scripts/session_groups.py adrian
uv run scripts/build_report.py adrian
```
Visual check: `cmux browser open file://.../report.html`, then screenshots go to
`data/<person>/screenshots/<date>/`.

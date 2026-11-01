# Open items

Things that aren't built yet, grouped by area. Everything else from the old plans is either done (and described in the
reference docs) or dropped. The old plans are in git history (`docs/plans/`, removed 2026-11-01).

## Compare ([compare.md](../compare.md))
- Common start line, the rest of it:
  - a draggable start flag on the map (`start=lat,lon` already works in the API);
  - a radius control (`srad=` already works in the URL);
  - a finish line (`align.finish` is reserved and always `null`);
  - the lead-in drawn at x < 0.
- Intervals **All** view: a real-time ~15 s lead-in before every rep. Per rep and Rep N already have one.
- Fatigue view: every rep of one session overlaid.
- An "up to speed" rep anchor (50 % of rep speed), for rolling starts and coarse sampling. Lap presses as tick marks.
- Cross-person compare (`adrian:123,patrik:456`), with HR as % of reserve. This would also let a Together session
  open in Compare. Today the Compare router works per person.
- Stretch compare from a chart drag-zoom; `reference=best|previous`.
- Fair sampling for 5 s files:
  - pause detection that knows about the sampling;
  - a ⚠ when sessions are sampled differently;
  - GAP from the route altitude ([compare.md Limitations](../compare.md#limitations)).
- Yardsticks in the sessions table: fitness score, % HR reserve, decoupling / drift.
- Charts: a GAP toggle, HR vs speed, HR drift.
- Map hover → chart cursor; animated ghost race.
- Best efforts per session and a stretch leaderboard.
- Power / cadence (only the newer watch has them).
- Shared stretches across different routes.
- CSV / PNG export.
- Bislett track geometry for lap splits.
- Pool swims use raw records (moving 12:40 vs 29:36 swimming time on the activity page). compare.py has no swim
  handling.
- Engine:
  - the route projection stalls on some out & back / point-to-point routes (patrik's coverage median is 66 %);
  - `gps_quality.load` doesn't apply the clock-jump fix.

## Together / Community ([together.md](../together.md), [community.md](../community.md))
- Snap track sessions to the Bislett oval, for exact dots and 200 m splits.
- Manual overrides: exclude a participant, set the label or the set distance. PATCH only covers title, category,
  note, guests, hidden and cover.
- A layout for many people: a transposed rep matrix and a people show / hide control.
- A season workout ladder: each member's average rep pace at every occurrence of a recurring club workout.
- Re-check the detection thresholds once a third person has data.

## Personal pages ([app.md](../app.md))
- Hikes:
  - a pause rule for slow uphill walking (one hike splits into 27 pauses / 17 legs);
  - vertical speed (m/h) instead of min/km.
- Fitness:
  - a "models disagree" flag when the performance and with-HR variants differ by more than 2 VDOT;
  - hide disciplines with no data (Patrik's Ski column).
- Home: the volume % should say "new" when the history is shorter than the comparison window (Patrik's "+347 %").
- Small polish:
  - Advanced filter placeholders ("5-7", "1000m / 4:00", "2:00-3:00") look like set values;
  - indoor Bislett signatures "6×1086" should read "6×2 laps (1086 m)";
  - one m:ss rounding rule (the activity page says 3:36, Compare says 3:37);
  - climb "0 m" vs "—" for flat / indoor sessions;
  - a "More columns" toggle for wide tables (the route sessions table);
  - button groups → selects on phones.
- Feature ideas:
  - training load / form (CTL / ATL / TSB, with a ramp warning);
  - weekly trends and personal streaks;
  - insight cards on Home;
  - an aerobic efficiency trend (pace at a fixed HR, drift);
  - personal goals;
  - weekly time in HR zones.

## Data to collect (Adrian)
- A few GPS-mode sessions at Bislett on the new watch, with a lap press at the line. Its GPS lag comes from only 5
  sessions.
- Repeated routes on the new watch. Its precision outside Bislett hasn't been measured.

## Open decisions
- Reversed sessions on a route: exclude them from the ghost race, give them their own group, or compare them on
  their own distance?
- The fair-sampling pause fix would change the stored moving times of 5 s sessions, so they would no longer match
  Garmin's. Is that OK?
- The URL form for cross-person Compare, and which person prefix it lives under.
- Weather: an external API with a local cache, or stay offline?

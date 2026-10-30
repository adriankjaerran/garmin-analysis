"""Pick which activities to download, offline, from activities_index.json.

Usage: uv run scripts/select_activities.py adrian [--track-lat 59.9256 --track-lon 10.7326 --radius 400]
Writes data/<person>/candidates/<category>.txt (comma-separated ids not yet downloaded), ready for
`fetch_activities.py <person> --ids-file ...`. Categories, most useful for calibration first:
track (track_running), near_track (run starts/ends within radius of the track), races (guessed:
standard distance, fast, few laps, away from the track; eventType is always 'uncategorized'),
rest (other outdoor runs), indoor. Also `priority.txt` = track + near_track + races.
Watch era from the list without downloading: avgPower present => watch with power (Adrian: 4315).
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path

RUN = {"running", "track_running", "treadmill_running", "indoor_running"}
STD = {"5k": 5000, "10k": 10000, "half": 21097.5, "marathon": 42195}


def dist_m(a: tuple, b: tuple) -> float:
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b[1] - a[1]) / 2) ** 2
    return 2 * 6371008.8 * math.asin(math.sqrt(h))


def near(a: dict, track: tuple) -> float:
    pts = [(a[f"{k}Latitude"], a[f"{k}Longitude"]) for k in ("start", "end") if a.get(f"{k}Latitude") is not None]
    return min((dist_m(p, track) for p in pts), default=math.inf)


def is_race(a: dict, track: tuple, radius: float) -> bool:
    d, t = a.get("distance") or 0, a.get("movingDuration") or a.get("duration") or 0
    if a["activityType"]["typeKey"] != "running" or not d or a.get("startLatitude") is None:
        return False
    if (a.get("averageRunningCadenceInStepsPerMinute") or 0) < 120 or near(a, track) <= radius:
        return False
    std = any(0.98 * v <= d <= 1.05 * v for v in STD.values())
    return std and (a.get("lapCount") or 0) <= 13 and t / d * 1000 <= 245  # pace <= 4:05/km


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("person")
    ap.add_argument("--track-lat", type=float, default=59.9256)  # Bislet
    ap.add_argument("--track-lon", type=float, default=10.7326)
    ap.add_argument("--radius", type=float, default=400)
    a = ap.parse_args()
    data = Path("data") / a.person
    track = (a.track_lat, a.track_lon)
    index = json.loads((data / "activities_index.json").read_text())
    have = {int(p.stem) for p in data.glob("activities/*/*.fit")}
    runs = [x for x in index if x["activityType"]["typeKey"] in RUN]

    cats: dict[str, list[dict]] = {k: [] for k in ("track", "near_track", "races", "rest", "indoor")}
    for x in runs:
        typ = x["activityType"]["typeKey"]
        if typ == "track_running":
            k = "track"
        elif typ != "running":
            k = "indoor"
        elif near(x, track) <= a.radius:
            k = "near_track"
        elif is_race(x, track, a.radius):
            k = "races"
        else:
            k = "rest"
        cats[k].append(x)

    out = data / "candidates"
    out.mkdir(exist_ok=True)
    print(f"{'category':<11}{'n':>5}{'have':>6}{'to get':>8}  watch era (power/no power)")
    for k, xs in cats.items():
        new = [str(x["activityId"]) for x in xs if x["activityId"] not in have]
        (out / f"{k}.txt").write_text(",".join(new))
        era = Counter("power" if x.get("avgPower") else "no power" for x in xs)
        print(f"{k:<11}{len(xs):>5}{len(xs) - len(new):>6}{len(new):>8}  {dict(era)}")
    pri = [str(x["activityId"]) for k in ("track", "near_track", "races") for x in cats[k] if x["activityId"] not in have]
    (out / "priority.txt").write_text(",".join(pri))
    print(f"priority: {len(pri)} to get = {len(pri)} calls with --parts fit, {2 * len(pri)} with activity,fit")
    for x in cats["races"]:
        print(f"  race? {x['startTimeLocal'][:10]} {x['activityId']} {x.get('activityName')} {x['distance']:.0f} m")


if __name__ == "__main__":
    main()

"""Download daily health data for the last N days.

Usage: uv run scripts/fetch_daily.py adrian --days 14
Layout:
  data/<person>/daily/<YYYY-MM-DD>/<endpoint>.json   per-day endpoints
  data/<person>/ranges/<endpoint>_<start>_<end>.json  range endpoints
  data/<person>/profile/*.json                       static-ish endpoints
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta

from garmin_client import GarminClient, save_json

PER_DAY = {
    "heart_rate": lambda d: ("/wellness-service/wellness/dailyHeartRate", {"date": d}),
    "sleep": lambda d: ("/sleep-service/sleep/dailySleep", {"date": d}),
    "hrv": lambda d: (f"/hrv-service/hrv/{d}", {}),
    "spo2": lambda d: (f"/wellness-service/wellness/daily/spo2acclimation/{d}", {}),
    "food_logs": lambda d: (f"/nutrition-service/food/logs/{d}", {}),
    "fitness_age": lambda d: (f"/fitnessage-service/fitnessage/v2/{d}", {}),
}

RANGE = {
    "daily_summaries": lambda s, e: ("/usersummary-service/v2/dailysummaries", {"startDate": s, "endDate": e}),
    "steps": lambda s, e: (f"/usersummary-service/stats/steps/daily/{s}/{e}", {}),
    "stress": lambda s, e: (f"/usersummary-service/stats/stress/daily/{s}/{e}", {}),
    "hydration": lambda s, e: (f"/usersummary-service/stats/hydration/daily/{s}/{e}", {}),
    "body_battery": lambda s, e: ("/wellness-service/wellness/bodyBattery/reports/daily", {"startDate": s, "endDate": e}),
    "sleep_summaries": lambda s, e: ("/sleep-service/sleep/dailySleepSummariesByDate", {"startDate": s, "endDate": e}),
    "hrv_summaries": lambda s, e: (f"/hrv-service/hrv/daily/{s}/{e}", {}),
    "weight": lambda s, e: (f"/weight-service/weight/range/{s}/{e}", {}),
    "weight_goals": lambda s, e: (f"/goal-service/goal/user/effective/weightgoal/{s}/{e}", {}),
    "wellness_activity": lambda s, e: (f"/wellnessactivity-service/activity/summary/range/{s}/{e}", {}),
}

STATIC = {
    "user_settings": "/userprofile-service/userprofile/settings",
    "heart_rate_zones": "/biometric-service/heartRateZones/v2",
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("person")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--only", default="", help="comma-separated endpoint names")
    ap.add_argument("--min-interval", type=float, default=0.0)
    a = ap.parse_args()
    only = {x for x in a.only.split(",") if x}
    want = lambda n: not only or n in only

    c = GarminClient(a.person, min_interval=a.min_interval)
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=a.days - 1)
    s, e = str(start), str(end)
    summary = []

    for name, path in STATIC.items():
        if want(name):
            st, body = c.get_json(path)
            if body is not None:
                save_json(c.data_dir / "profile" / f"{name}.json", body)
            summary.append((name, "-", st))

    for name, f in RANGE.items():
        if not want(name):
            continue
        path, params = f(s, e)
        st, body = c.get_json(path, **params)
        if body is not None:
            save_json(c.data_dir / "ranges" / f"{name}_{s}_{e}.json", body)
        summary.append((name, f"{s}..{e}", st))

    for i in range(a.days):
        d = str(start + timedelta(days=i))
        for name, f in PER_DAY.items():
            if not want(name):
                continue
            path, params = f(d)
            st, body = c.get_json(path, **params)
            if body is not None:
                save_json(c.data_dir / "daily" / d / f"{name}.json", body)
            summary.append((name, d, st))

    by = {}
    for name, _, st in summary:
        by.setdefault(name, []).append(st)
    for name, sts in by.items():
        print(f"  {name:18} " + " ".join(map(str, sts)))
    print(f"done, {c.count} requests")


if __name__ == "__main__":
    main()

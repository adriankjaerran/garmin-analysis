"""Fetch the full activity list and all daily summaries (1 year per request).

Usage: uv run scripts/fetch_index.py adrian [--since 2018-01-01]
Writes data/<person>/activities_index.json and data/<person>/ranges/daily_summaries_<year>.json
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta

from garmin_client import GarminClient, save_json


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("person")
    ap.add_argument("--since", default="2018-01-01")
    a = ap.parse_args()
    c = GarminClient(a.person)

    acts, offset = [], 0
    while True:
        _, page = c.get_json("/activitylist-service/activities/search/activities", start=offset, limit=100)
        if not page:
            break
        acts += page
        offset += len(page)
    save_json(c.data_dir / "activities_index.json", acts)
    print(f"{len(acts)} activities")

    yesterday = date.today() - timedelta(days=1)
    for y in range(date.fromisoformat(a.since).year, yesterday.year + 1):
        s, e = date(y, 1, 1), min(date(y, 12, 31), yesterday)
        st, body = c.get_json("/usersummary-service/v2/dailysummaries", startDate=str(s), endDate=str(e))
        if body:
            save_json(c.data_dir / "ranges" / f"daily_summaries_{y}.json", body)
        print(f"  daily summaries {y}: {st} {len((body or {}).get('data', []))} days")
    print(f"done, {c.count} requests")


if __name__ == "__main__":
    main()

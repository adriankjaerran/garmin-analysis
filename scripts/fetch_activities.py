"""Download activities (list entry, summary, ~10s details, original FIT) for a date range.

Usage: uv run scripts/fetch_activities.py adrian --days 14 [--types running,track_running]
       uv run scripts/fetch_activities.py adrian --ids 123,456   (ids looked up in activities_index.json)
       uv run scripts/fetch_activities.py adrian --ids-file ids.txt --parts fit --min-interval 3   (backfill, 1 call/activity)
--parts: which calls to make per activity (default activity,details,fit; `workouts` is opt-in, it was
always [] on Adrian's runs). The report uses activity.json (splitSummaries); analysis needs only the FIT.
Layout: data/<person>/activities/<YYYY-MM-DD>_<id>_<type>/{list_item,activity,details}.json, <id>.fit
"""

from __future__ import annotations

import argparse
import io
import zipfile
from datetime import date, timedelta

from garmin_client import GarminClient, save_json


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("person")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--types", default="", help="comma-separated activityType keys; empty = all")
    ap.add_argument("--ids", default="", help="comma-separated activity ids (uses activities_index.json)")
    ap.add_argument("--ids-file", default="", help="file with comma/newline-separated activity ids")
    ap.add_argument("--parts", default="activity,details,fit",
                    help="comma-separated subset of activity,details,workouts,fit")
    ap.add_argument("--min-interval", type=float, default=0.0)
    a = ap.parse_args()

    c = GarminClient(a.person, min_interval=a.min_interval)
    start, end = date.today() - timedelta(days=a.days), date.today()
    types = {t for t in a.types.split(",") if t}

    acts, offset = [], 0
    raw = a.ids + "," + (open(a.ids_file).read() if a.ids_file else "")
    ids = {int(x) for x in raw.replace("\n", ",").split(",") if x.strip()}
    parts = {p for p in a.parts.split(",") if p}
    if ids:
        import json
        index = json.loads((c.data_dir / "activities_index.json").read_text())
        acts = [x for x in index if x["activityId"] in ids]
    while not ids:
        _, page = c.get_json("/activitylist-service/activities/search/activities",
                             startDate=str(start), endDate=str(end), start=offset, limit=100)
        if not page:
            break
        acts += page
        offset += len(page)
        if len(page) < 100:
            break
    acts = [x for x in acts if not types or x["activityType"]["typeKey"] in types]
    print(f"{len(acts)} activities" + ("" if ids else f" {start}..{end}"))

    for x in acts:
        aid, typ = x["activityId"], x["activityType"]["typeKey"]
        d = c.data_dir / "activities" / f"{x['startTimeLocal'][:10]}_{aid}_{typ}"
        if (d / f"{aid}.fit").exists() or ("fit" not in parts and (d / "activity.json").exists()):
            print(f"  skip {d.name} (already downloaded)")
            continue
        save_json(d / "list_item.json", x)
        for name, path in (("activity", f"/activity-service/activity/{aid}"),
                           ("details", f"/activity-service/activity/{aid}/details"),
                           ("workouts", f"/activity-service/activity/{aid}/workouts")):
            if name not in parts:
                continue
            status, body = c.get_json(path)
            if status == 200 and body is not None:
                save_json(d / f"{name}.json", body)
        if "fit" not in parts:
            print(f"  {d.name}: no FIT requested")
            continue
        r = c.get(f"/download-service/files/activity/{aid}",
                  accept="application/zip, application/x-zip-compressed, application/octet-stream")
        if r.ok:
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                for n in z.namelist():
                    out = d / (f"{aid}.fit" if n.lower().endswith(".fit") else n)
                    out.write_bytes(z.read(n))
        print(f"  {d.name}: FIT {r.status_code} {len(r.content)//1024} KB")
    print(f"done, {c.count} requests")


if __name__ == "__main__":
    main()

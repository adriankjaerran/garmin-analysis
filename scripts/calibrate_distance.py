"""Calibrate distance sources against known truth.

Truth sources:
  * Bislet track: laps counted from GPS geometry (unwrapped angle around the track centre);
    1 lap in lane 1 = 400 m, lane n adds 2*pi*1.22*(n-1) m.
  * Races with certified distance (half 21,097.5 m, marathon 42,195 m).

Compared distance sources per segment:
  garmin_lap   Garmin's lap distance (FIT lap total_distance; snapped in track mode)
  record       watch's per-second distance field (FIT record.distance) over the same window
  gps          raw haversine sum of the 1 Hz GPS positions over the same window
  geometry     laps on track * 400 m (only on the track)

Usage: uv run scripts/calibrate_distance.py adrian  -> writes data/<person>/calibration.json
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path

import fitparse

ROOT = Path(__file__).resolve().parent.parent
SEMI = 180 / 2**31
BISLET_GUESS = (59.92527, 10.73306)
LANE_W = 1.22
RACES = {  # activityId: (label, certified metres)
    20380305975: ("Copenhagen Half Marathon 2025", 21097.5),
    3533402624: ("Rome Marathon 2019", 42195.0),
}


def hav(a, b):
    r = 6371008.8
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def local_xy(p, c):
    """metres east/north of centre c."""
    return ((p[1] - c[1]) * 111320 * math.cos(math.radians(c[0])), (p[0] - c[0]) * 110574)


def read(fit: Path):
    ff = fitparse.FitFile(str(fit))
    recs = [{f.name: f.value for f in m if f.value is not None} for m in ff.get_messages("record")]
    t0 = recs[0]["timestamp"]
    rows = []
    for x in recs:
        pos = (x["position_lat"] * SEMI, x["position_long"] * SEMI) if "position_lat" in x and "position_long" in x else None
        rows.append({"t": (x["timestamp"] - t0).total_seconds(), "pos": pos, "dist": x.get("distance"),
                     "speed": x.get("enhanced_speed", x.get("speed"))})
    laps = []
    for m in ff.get_messages("lap"):
        v = {f.name: f.value for f in m if f.value is not None}
        laps.append({"start": (v["start_time"] - t0).total_seconds(), "time": v.get("total_elapsed_time") or 0,
                     "dist": v.get("total_distance") or 0, "intensity": str(v.get("intensity") or "")})
    sess = [{f.name: f.value for f in m if f.value is not None} for m in ff.get_messages("session")][0]
    return rows, laps, sess


def window(rows, a, b):
    return [r for r in rows if a <= r["t"] < b]


def gps_dist(seg):
    pts = [r["pos"] for r in seg if r["pos"]]
    return sum(hav(p, q) for p, q in zip(pts, pts[1:]))


def record_dist(seg):
    d = [r["dist"] for r in seg if r["dist"] is not None]
    return d[-1] - d[0] if len(d) > 1 else None


def track_geometry(seg, centre, max_r=80):
    """Turns around the centre (only points within max_r m) and median radius."""
    turns, radii, prev = 0.0, [], None
    for r in seg:
        if not r["pos"]:
            continue
        x, y = local_xy(r["pos"], centre)
        rad = math.hypot(x, y)
        if rad > max_r:
            prev = None
            continue
        radii.append(rad)
        ang = math.atan2(y, x)
        if prev is not None:
            d = ang - prev
            d = (d + math.pi) % (2 * math.pi) - math.pi
            turns += d
        prev = ang
    return abs(turns) / (2 * math.pi), (statistics.median(radii) if radii else None)


# IAAF 400 m track, lane 1 measuring line (0.30 m outside the kerb)
HALF_STRAIGHT = 84.39 / 2
R1 = 36.50 + 0.30
LAP = 4 * HALF_STRAIGHT + 2 * math.pi * R1  # = 400.0


class Track:
    """Stadium model placed on the map: projects GPS points to (arc position s along lane 1, lane offset)."""

    def __init__(self, pts):
        c = (statistics.fmean(p[0] for p in pts), statistics.fmean(p[1] for p in pts))
        xy = [local_xy(p, c) for p in pts]
        # orientation: principal axis
        sxx = statistics.fmean(x * x for x, _ in xy); syy = statistics.fmean(y * y for _, y in xy)
        sxy = statistics.fmean(x * y for x, y in xy)
        self.theta = 0.5 * math.atan2(2 * sxy, sxx - syy)
        # recentre on the midpoint of the extent along/perpendicular to the axis
        u = (math.cos(self.theta), math.sin(self.theta))
        a = sorted(x * u[0] + y * u[1] for x, y in xy); b = sorted(-x * u[1] + y * u[0] for x, y in xy)
        q = lambda s_, f: s_[int(len(s_) * f)]
        da, db = (q(a, 0.005) + q(a, 0.995)) / 2, (q(b, 0.005) + q(b, 0.995)) / 2
        ex, ey = da * u[0] - db * u[1], da * u[1] + db * u[0]
        self.c = (c[0] + ey / 110574, c[1] + ex / (111320 * math.cos(math.radians(c[0]))))
        self.u = u

    def project(self, p):
        x, y = local_xy(p, self.c)
        a, b = x * self.u[0] + y * self.u[1], -x * self.u[1] + y * self.u[0]
        L, R = HALF_STRAIGHT, R1
        if -L <= a <= L:
            if b < 0:
                return a + L, -b - R
            return 2 * L + math.pi * R + (L - a), b - R
        if a > L:
            phi = math.atan2(b, a - L)  # -pi/2..pi/2
            return 2 * L + R * (phi + math.pi / 2), math.hypot(a - L, b) - R
        phi = math.atan2(b, a + L)
        phi = phi if phi > 0 else phi + 2 * math.pi  # pi/2..3pi/2
        return 4 * L + math.pi * R + R * (phi - math.pi / 2), math.hypot(a + L, b) - R


def on_track(seg, track, lo=-4.0, hi=12.0):
    """Lane-1 distance covered on the track (direction-free), lane offsets, and GPS/record distance on track."""
    lane1, offs, gps, rec, prev = 0.0, [], 0.0, 0.0, None
    for r in seg:
        if not r["pos"]:
            prev = None
            continue
        s_, o = track.project(r["pos"])
        if not (lo <= o <= hi):
            prev = None
            continue
        offs.append(o)
        if prev is not None:
            ds = (s_ - prev[0] + LAP / 2) % LAP - LAP / 2
            lane1 += abs(ds)
            gps += hav(prev[1]["pos"], r["pos"])
            if r["dist"] is not None and prev[1]["dist"] is not None:
                rec += r["dist"] - prev[1]["dist"]
        prev = (s_, r)
    return lane1, offs, gps, rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("person")
    a = ap.parse_args()
    d = ROOT / "data" / a.person.lower()
    fits = sorted((d / "activities").glob("*/*.fit"))
    loaded = {}
    for f in fits:
        rows, laps, sess = read(f)
        first = next((r["pos"] for r in rows if r["pos"]), None)
        loaded[f] = (rows, laps, sess, first)

    # 1) estimate the Bislet centre from track-mode active laps near Bislet
    pts = []
    for f, (rows, laps, sess, first) in loaded.items():
        if str(sess.get("sub_sport")) == "track" and first and hav(first, BISLET_GUESS) < 400:
            for lap in laps:
                if lap["intensity"] == "active":
                    pts += [r["pos"] for r in window(rows, lap["start"], lap["start"] + lap["time"]) if r["pos"]]
    pts = [p for p in pts if hav(p, BISLET_GUESS) < 120]
    # centre = midpoint of the bounding box of the track points (robust to uneven sampling)
    lats, lons = sorted(p[0] for p in pts), sorted(p[1] for p in pts)
    q = lambda s, x: s[int(len(s) * x)]
    centre = ((q(lats, 0.01) + q(lats, 0.99)) / 2, (q(lons, 0.01) + q(lons, 0.99)) / 2)

    track = Track(pts)
    centre = track.c
    offs_all = [track.project(p)[1] for p in pts]
    out = {"centre": centre, "theta_deg": round(math.degrees(track.theta), 1), "track_points": len(pts),
           "offset_median_all": round(statistics.median(offs_all), 2), "track_laps": [], "bislet_runs": [], "races": []}

    for f, (rows, laps, sess, first) in loaded.items():
        aid = int(f.stem)
        date = f.parent.name[:10]
        at_bislet = first and hav(first, centre) < 400
        is_track = str(sess.get("sub_sport")) == "track"

        if is_track and at_bislet:
            for i, lap in enumerate(laps):
                if lap["intensity"] != "active":
                    continue
                seg = window(rows, lap["start"], lap["start"] + lap["time"])
                turns, rad = track_geometry(seg, centre)
                lane1, offs, _, _ = on_track(seg, track)
                off = statistics.median(offs) if offs else None
                out["track_laps"].append({
                    "lane1": round(lane1, 1), "offset": round(off, 2) if off is not None else None,
                    "in_lane": round(lane1 * (1 + 2 * math.pi * max(0, off or 0) / LAP), 1),
                    "id": aid, "date": date, "lap": i + 1, "time": round(lap["time"], 1),
                    "garmin_lap": round(lap["dist"], 1), "record": round(record_dist(seg) or 0, 1),
                    "gps": round(gps_dist(seg), 1), "turns": round(turns, 3),
                    "geometry": round(turns * 400, 1), "radius": round(rad, 1) if rad else None,
                })

        if not is_track and at_bislet:
            turns, rad = track_geometry(rows, centre)
            on = [r for r in rows if r["pos"] and math.hypot(*local_xy(r["pos"], centre)) <= 80]
            # distance while on the track only
            seg_gps, seg_rec, prev = 0.0, 0.0, None
            for r in rows:
                inside = r["pos"] and math.hypot(*local_xy(r["pos"], centre)) <= 80
                if inside and prev is not None:
                    seg_gps += hav(prev["pos"], r["pos"])
                    if r["dist"] is not None and prev["dist"] is not None:
                        seg_rec += r["dist"] - prev["dist"]
                prev = r if inside else None
            lane1, offs, gps_on, rec_on = on_track(rows, track)
            off = statistics.median(offs) if offs else None
            out["bislet_runs"].append({
                "lane1": round(lane1, 1), "offset": round(off, 2) if off is not None else None,
                "gps_on": round(gps_on, 1), "rec_on": round(rec_on, 1),
                "id": aid, "date": date, "garmin_total": round(sess.get("total_distance") or 0, 1),
                "turns": round(turns, 2), "geometry": round(turns * 400, 1),
                "record_on_track": round(seg_rec, 1), "gps_on_track": round(seg_gps, 1),
                "radius": round(rad, 1) if rad else None, "share_on_track": round(len(on) / max(1, len(rows)), 2),
            })

        if aid in RACES:
            label, truth = RACES[aid]
            out["races"].append({
                "id": aid, "date": date, "race": label, "truth": truth,
                "garmin_total": round(sess.get("total_distance") or 0, 1),
                "record": round((rows[-1]["dist"] or 0), 1), "gps": round(gps_dist(rows), 1),
            })

    merge_derived(d, out)
    (d / "calibration.json").write_text(json.dumps(out, indent=1))
    tl = out["track_laps"]
    print("theta", out["theta_deg"], "median offset all track pts", out["offset_median_all"])
    for k in ("garmin_lap", "record", "gps", "geometry"):
        rel = [x[k] / x["lane1"] - 1 for x in tl if x["lane1"] > 150]
        print(f"  {k:10} vs lane1 model: median {statistics.median(rel)*100:+.2f}%  p10 {sorted(rel)[len(rel)//10]*100:+.2f}%  p90 {sorted(rel)[len(rel)*9//10]*100:+.2f}%")
    print("  rep offsets (m):", sorted(x["offset"] for x in tl if x["offset"] is not None)[::8])
    print(f"centre {centre}  ({len(pts)} pts)")
    print(f"track-mode active laps: {len(tl)} from {len({x['id'] for x in tl})} sessions")
    for k in ("garmin_lap", "record", "gps"):
        rel = [x[k] / x["geometry"] - 1 for x in tl if x["geometry"] > 150]
        print(f"  {k:10} vs geometry: median {statistics.median(rel)*100:+.2f}%  mean {statistics.fmean(rel)*100:+.2f}%  p10 {sorted(rel)[len(rel)//10]*100:+.2f}%  p90 {sorted(rel)[len(rel)*9//10]*100:+.2f}%")
    print("  radius median", statistics.median([x["radius"] for x in tl if x["radius"]]))
    print("  garmin_lap values:", sorted({x["garmin_lap"] for x in tl}))
    for x in out["bislet_runs"]:
        print("  bislet run", x["date"], "garmin", x["garmin_total"], "| on track: lane1", x["lane1"], "rec", x["rec_on"], "gps", x["gps_on"], f"rec/lane1 {x['rec_on']/max(1,x['lane1'])-1:+.2%}", "offset", x["offset"], "share", x["share_on_track"])
    for x in out["races"]:
        print("  race", x)


def merge_derived(d: Path, out: dict) -> None:
    """calibration.json is the single source of parameters: copy in what the other calibration
    scripts measured (gps_lap_analysis.json: lag table, lag compensation for the rep detector;
    route_accuracy.json: distance uncertainty model, races, repeated-route spread)."""
    for name, keys in (("gps_lap_analysis.json", ("lag_compensation", "lag_table", "lag_vs_pace")),
                       ("detector_eval.json", ("fitted_lags_v2", "summary")),
                       ("route_accuracy.json", ("uncertainty_model", "bias", "races", "spread_by_era", "clusters", "bislet", "osm"))):
        f = d / name
        if not f.exists():
            continue
        src = json.loads(f.read_text())
        for k in keys:
            if k in src:
                if name.startswith("detector"):
                    out[{"fitted_lags_v2": "detector_lags", "summary": "detector_eval"}[k]] = src[k]
                else:
                    out[f"route_{k}" if (name.startswith("route") and k in ("races", "bislet")) else k] = src[k]
    # Track mode snaps positions and stalls the distance field at lap marks, so its step lags come
    # out slightly negative (-0.4/-0.7 s): an artefact, not a lead. Compensate by >= 0 only.
    for key, c in [*(out.get("lag_compensation") or {}).items(), *(out.get("detector_lags") or {}).items()]:
        for k in ("start_s", "end_s"):
            if key.startswith("track") and c.get(k) is not None and c[k] < 0:
                c[k + "_raw"], c[k] = c[k], 0.0


if __name__ == "__main__":
    main()

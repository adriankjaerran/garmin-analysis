"""Build a self-contained HTML overview of a person's downloaded Garmin data.

Usage: uv run scripts/build_report.py adrian
Writes data/<person>/report.html (gitignored: it embeds personal health data).
"""

from __future__ import annotations

import argparse
import glob
import json
from datetime import datetime, timezone
from pathlib import Path

import fitparse

from run_analysis import analyse

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = Path(__file__).with_name("report_template.html")


def load(p):
    return json.loads(Path(p).read_text())


def r(v, n=2):
    return None if v is None else round(v, n)


def activities(d: Path):
    downloaded = {p.name.split("_")[1]: p for p in (d / "activities").glob("*_*_*") if list(p.glob("*.fit"))}
    out = []
    for a in load(d / "activities_index.json"):
        out.append({
            "id": a["activityId"],
            "date": a["startTimeLocal"],
            "name": a.get("activityName"),
            "type": a["activityType"]["typeKey"],
            "dist": r((a.get("distance") or 0) / 1000, 3) or None,
            "dur": r(a.get("duration"), 0),
            "moving": r(a.get("movingDuration"), 0),
            "hr": a.get("averageHR"),
            "maxhr": a.get("maxHR"),
            "elev": r(a.get("elevationGain"), 0),
            "cad": r(a.get("averageRunningCadenceInStepsPerMinute"), 0),
            "load": r(a.get("activityTrainingLoad"), 0),
            "te": r(a.get("aerobicTrainingEffect"), 1),
            "kcal": r(a.get("calories"), 0),
            "power": r(a.get("avgPower"), 0),
            "fit": str(a["activityId"]) in downloaded,
        })
    return out, downloaded


def fit_series(folder: Path):
    fit = next(folder.glob("*.fit"))
    ff = fitparse.FitFile(str(fit))
    recs = [{f.name: f.value for f in m if f.value is not None} for m in ff.get_messages("record")]
    t0 = recs[0]["timestamp"]
    rows = []
    for x in recs:
        cad = x.get("cadence")
        if cad is not None:
            cad = cad * 2 + (x.get("fractional_cadence") or 0) * 2
        rows.append([
            int((x["timestamp"] - t0).total_seconds()),
            x.get("heart_rate"),
            r(x.get("enhanced_speed", x.get("speed")), 3),
            r(cad, 0),
            r(x.get("enhanced_altitude", x.get("altitude")), 1),
            x.get("power"),
            r(x.get("distance"), 1),
        ])
    laps = []
    for m in ff.get_messages("lap"):
        v = {f.name: f.value for f in m if f.value is not None}
        laps.append({
            "dist": r((v.get("total_distance") or 0) / 1000, 3),
            "time": r(v.get("total_timer_time"), 0),
            "hr": v.get("avg_heart_rate"),
            "maxhr": v.get("max_heart_rate"),
            "speed": r(v.get("enhanced_avg_speed", v.get("avg_speed")), 3),
        })
    devices = [{f.name: f.value for f in m} for m in ff.get_messages("file_id")]
    intervals = {}
    for a, b in zip(rows, rows[1:]):
        intervals[b[0] - a[0]] = intervals.get(b[0] - a[0], 0) + 1
    return {
        "cols": ["t", "hr", "speed", "cad", "alt", "power", "dist"],
        "rows": rows,
        "laps": laps,
        "file": fit.name,
        "kb": round(fit.stat().st_size / 1024),
        "product": str(devices[0].get("garmin_product") or devices[0].get("product")) if devices else None,
        "interval": max(intervals, key=intervals.get) if intervals else None,
    }


SKI_MIN_STEP_S = 10  # ski series are thinned to one row per 10 s (long sessions, no reps to resolve)
SKI_COLS = ["t", "hr", "speed", "alt", "dist"]


def ski_series(folder: Path):
    """fit_series for a ski session: thinned rows, no cadence/power, no run analysis (the rep
    detector would treat every downhill as a rep; legs and pauses come from groups.json)."""
    run = fit_series(folder)
    keep = [run["cols"].index(c) for c in SKI_COLS]
    rows, last = [], None
    for x in run["rows"]:
        if last is None or x[0] - last >= SKI_MIN_STEP_S:
            rows.append([x[i] for i in keep])
            last = x[0]
    run["cols"], run["rows"], run["sport"] = SKI_COLS, rows, "ski"
    return run


def groups_payload(d: Path):
    """groups.json from session_groups.py, trimmed: pause lat/lon are only needed by the script."""
    p = d / "groups.json"
    if not p.exists():
        return None
    g = load(p)
    pf = g["fields"]["pauses"]
    keep = [pf.index(c) for c in pf if c not in ("lat", "lon")]
    g["fields"]["pauses"] = [pf[i] for i in keep]
    for a in g["activities"].values():
        if a.get("pauses"):
            a["pauses"] = [[x[i] for i in keep] for x in a["pauses"]]
    for rt in g["routes"].values():
        for w in rt.get("waypoints") or []:
            w.pop("lat", None), w.pop("lon", None)
        if rt.get("ref"):
            rt["ref"] = {k: v for k, v in rt["ref"].items() if k not in ("lat", "lon")}
    g.pop("params", None)
    return g


def daily(d: Path):
    days = {}
    for p in sorted(glob.glob(str(d / "ranges" / "daily_summaries_[0-9][0-9][0-9][0-9].json"))):
        for x in load(p).get("data", []):
            mv, hr, st = x.get("movement") or {}, x.get("heartRate") or {}, x.get("stress") or {}
            bb, rs, ox = x.get("bodyBattery") or {}, x.get("respiration") or {}, x.get("pulseOx") or {}
            im, cal = x.get("intensityMinutes") or {}, x.get("calories") or {}
            days[x["calendarDate"]] = {
                "date": x["calendarDate"],
                "steps": (mv.get("steps") or {}).get("value"),
                "rhr": hr.get("restingValue"),
                "minhr": hr.get("minValue"),
                "maxhr": hr.get("maxValue"),
                "stress": st.get("avgLevel") if (st.get("avgLevel") or -1) >= 0 else None,
                "bbmax": bb.get("maxValue"),
                "bbmin": bb.get("minValue"),
                "resp": rs.get("avgValue"),
                "spo2": ox.get("avgValue"),
                "mod": im.get("moderate"),
                "vig": im.get("vigorous"),
                "kcal": cal.get("burnedTotal"),
                "floors": (mv.get("floorsAscended") or {}).get("value"),
            }
    return [days[k] for k in sorted(days)]


def intraday(d: Path):
    out = []
    for p in sorted(glob.glob(str(d / "daily" / "*" / "heart_rate.json"))):
        h = load(p)
        v = [[x[0], x[1]] for x in (h.get("heartRateValues") or []) if x[1] is not None]
        if v:
            out.append({"date": Path(p).parent.name, "rhr": h.get("restingHeartRate"), "values": v})
    return out


def sleep(d: Path):
    out = []
    for p in sorted(glob.glob(str(d / "daily" / "*" / "sleep.json"))):
        s = load(p)
        if not s.get("sleepTimeSeconds"):
            continue
        out.append({
            "date": s.get("calendarDate") or Path(p).parent.name,
            "total": s["sleepTimeSeconds"],
            "deep": s.get("deepSleepSeconds"),
            "light": s.get("lightSleepSeconds"),
            "rem": s.get("remSleepSeconds"),
            "awake": s.get("awakeSleepSeconds"),
            "score": ((s.get("sleepScores") or {}).get("overall") or {}).get("value"),
            "hr": s.get("avgHeartRate"),
            "spo2": s.get("averageSpO2Value"),
            "resp": s.get("averageRespirationValue"),
            "stress": s.get("avgSleepStress"),
        })
    for p in sorted(glob.glob(str(d / "ranges" / "sleep_summaries_*.json"))):
        have = {x["date"] for x in out}
        for s in load(p):
            if s.get("sleepTimeSeconds") and s.get("calendarDate") not in have:
                out.append({
                    "date": s["calendarDate"], "total": s["sleepTimeSeconds"], "deep": s.get("deepSleepSeconds"),
                    "light": s.get("lightSleepSeconds"), "rem": s.get("remSleepSeconds"),
                    "awake": s.get("awakeSleepSeconds"),
                    "score": ((s.get("sleepScores") or {}).get("overall") or {}).get("value"),
                    "hr": s.get("avgHeartRate"), "spo2": s.get("averageSpO2Value"),
                    "resp": s.get("averageRespirationValue"), "stress": s.get("avgSleepStress"),
                })
    return sorted(out, key=lambda x: x["date"])


def calibration_extras(cal: dict) -> dict:
    """Phase 5 keys from calibration.json, trimmed for the report payload."""
    import statistics
    bins = {}
    for p in cal.get("lag_vs_pace") or []:
        if p.get("v") is None or p.get("lag") is None:
            continue
        k = (p["mode"], p["device"], p["signal"], p["kind"], int(p["v"] // 0.5))
        bins.setdefault(k, []).append(p["lag"])
    lag_vs_pace = [{"mode": k[0], "device": k[1], "signal": k[2], "kind": k[3], "v": k[4] * 0.5 + 0.25,
                    "median": round(statistics.median(v), 2), "n": len(v)} for k, v in sorted(bins.items())]
    races = []
    for r in cal.get("route_races") or []:
        r = dict(r)
        al = r.pop("auto_laps", None)
        if al:
            r["auto_laps"] = {k: v for k, v in al.items() if k != "laps"}
        races.append(r)
    clusters = []
    for c in cal.get("clusters") or []:
        mem = c.get("members") or []
        clusters.append({"cluster": c["cluster"], "n": c["n"], "medoid": c.get("medoid"), "eras": c.get("eras"),
                         "dates": sorted(m["date"] for m in mem),
                         "envs": sorted({m.get("env") for m in mem if m.get("env")})})
    out = {"lag_vs_pace": lag_vs_pace, "route_races": races, "clusters": clusters}
    for k in ("lag_table", "detector_lags", "lag_compensation", "detector_eval", "spread_by_era",
              "uncertainty_model", "bias", "route_bislet"):
        out[k] = cal.get(k)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("person")
    a = ap.parse_args()
    d = ROOT / "data" / a.person.lower()
    acts, downloaded = activities(d)
    runs = {}
    for aid, folder in downloaded.items():
        if folder.name.endswith("_cross_country_skiing_ws"):
            run = ski_series(folder)
            run["analysis"] = {"totals": {}, "reps": [], "breaks": [], "laps": [], "km_splits": []}
        else:
            run = fit_series(folder)
            run["sport"] = "run"
            run["analysis"] = analyse(next(folder.glob("*.fit")))
        act = load(folder / "activity.json") if (folder / "activity.json").exists() else {}
        sm = act.get("summaryDTO") or {}
        splits = {x["splitType"]: x for x in act.get("splitSummaries") or []}
        run["garmin"] = {
            "dist": sm.get("distance"), "elapsed": sm.get("elapsedDuration"), "moving": sm.get("movingDuration"),
            "hr": sm.get("averageHR"), "maxhr": sm.get("maxHR"),
            "reps": (splits.get("INTERVAL_ACTIVE") or {}).get("noOfSplits") if "INTERVAL_REST" in splits else None,
            "rep_time": (splits.get("INTERVAL_ACTIVE") or {}).get("duration") if "INTERVAL_REST" in splits else None,
            "rep_dist": (splits.get("INTERVAL_ACTIVE") or {}).get("distance") if "INTERVAL_REST" in splits else None,
            "break_time": (splits.get("INTERVAL_REST") or {}).get("duration"),
            "standing": (splits.get("RWD_STAND") or {}).get("duration"),
            "walking": (splits.get("RWD_WALK") or {}).get("duration"),
        }
        runs[aid] = run
    calibration = None
    cal_path = d / "calibration.json"
    if cal_path.exists():
        import statistics
        cal = load(cal_path)
        tl = [x for x in cal["track_laps"] if x["lane1"] > 150]
        def summ(label, key, truth_key="lane1"):
            rel = sorted(x[key] / x[truth_key] - 1 for x in tl)
            return {"source": label, "n": len(rel), "median": statistics.median(rel) * 100,
                    "p10": rel[len(rel) // 10] * 100, "p90": rel[len(rel) * 9 // 10] * 100}
        reps = []
        for x in tl:
            run = runs.get(str(x["id"]))
            if not run:
                continue
            laps = run["analysis"]["laps"]
            # x["lap"] is the 1-based lap index in the FIT file; fall back to matching distance and time
            lap = laps[x["lap"] - 1] if 0 < x.get("lap", 0) <= len(laps) else None
            if not (lap and lap.get("rep") and abs(lap["time"] - x["time"]) < 0.5):
                lap = next((l for l in laps if l.get("rep") and abs(l["dist"] - x["garmin_lap"]) < 0.05
                            and abs(l["time"] - x["time"]) < 0.5), None)
            if lap:
                rp = run["analysis"]["reps"][lap["rep"] - 1]
                reps.append(rp.get("dist_detected", rp["dist"]) / x["lane1"] - 1)
        rows = [summ("Garmin lap distance", "garmin_lap"), summ("Watch distance field", "record"), summ("Raw GPS positions", "gps")]
        if reps:
            reps.sort()
            rows.append({"source": "Detected reps (watch distance between detected boundaries)", "n": len(reps), "median": statistics.median(reps) * 100,
                         "p10": reps[len(reps) // 10] * 100, "p90": reps[len(reps) * 9 // 10] * 100})
        lap_m = 400.0
        bislet = []
        for x in cal["bislet_runs"]:
            if not x["lane1"]:
                continue
            off = max(0.0, x["offset"] or 0)
            expected = x["lane1"] * (1 + 2 * 3.141592653589793 * off / lap_m)
            bislet.append({**x, "laps": x["lane1"] / lap_m, "expected": expected,
                           "vs_lane1": (x["rec_on"] / x["lane1"] - 1) * 100,
                           "vs_expected": (x["rec_on"] / expected - 1) * 100})
        races = [{**x, "diff": x["garmin_total"] - x["truth"], "pct": (x["garmin_total"] / x["truth"] - 1) * 100} for x in cal["races"]]
        calibration = {"sources": rows, "sessions": len({x["id"] for x in tl}), "bislet": bislet, "races": races,
                       **calibration_extras(cal)}
    data = {
        "person": a.person.capitalize(),
        "built": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M"),
        "activities": acts,
        "runs": runs,
        "daily": daily(d),
        "intraday": intraday(d),
        "sleep": sleep(d),
        "calibration": calibration,
        "groups": groups_payload(d),
    }
    html = TEMPLATE.read_text().replace("/*__DATA__*/null", json.dumps(data, separators=(",", ":"), default=str))
    out = d / "report.html"
    out.write_text(html)
    print(f"wrote {out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()

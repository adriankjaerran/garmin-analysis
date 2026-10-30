"""Evaluate rep detectors v1.1 and v2 against lap presses and track geometry at Bislet.

Sessions: running activities whose first GPS fix is within 400 m of the Bislet centre
(calibration.json `centre`) and that have >= 3 manual laps (as gps_lap_analysis.py).
Truth reps:
  * sessions with `active` laps: the active laps;
  * otherwise: manual laps (both ends pressed) of >= 20 s whose Garmin speed is >= 1.25x
    both neighbouring laps (isolated fast laps; continuous fast laps are skipped).
Press times are sub-second (run_analysis._press_times). Errors are detected - press.
Distance truth: track mode `geometry` from calibration.json track_laps (id, lap); GPS mode the
lane-corrected `expected` from gps_lap_analysis.json laps (id, lap), when present.

v2 is scored three ways: raw change points (no lag), lags from calibration.json/defaults (what
analyse() reports), and leave-one-session-out lags (median detected - press per mode/watch,
fitted on the other sessions). The PELT penalty and windows are fixed, not fitted.

Usage: uv run scripts/evaluate_detector.py adrian  -> data/<person>/detector_eval.json
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from pathlib import Path

import fitparse

from run_analysis import _read, analyse, analyse_v1, lag_for

ROOT = Path(__file__).resolve().parent.parent
SEMI = 180 / 2 ** 31


def hav(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def first_fix(fit: Path):
    for m in fitparse.FitFile(str(fit)).get_messages("record"):
        v = {f.name: f.value for f in m if f.value is not None}
        if "position_lat" in v and "position_long" in v:
            return v["position_lat"] * SEMI, v["position_long"] * SEMI
    return None


def truth_reps(laps):
    spans = [(l["t_start"], laps[i + 1]["t_start"] if i + 1 < len(laps) else l["t_start"] + l["time"]) for i, l in enumerate(laps)]
    if any(l["intensity"] == "active" for l in laps):
        idx = [i for i, l in enumerate(laps) if l["intensity"] == "active" and i > 0 and i + 1 < len(laps)]
    else:
        sp = [l["dist"] / l["time"] if l["time"] else 0 for l in laps]
        idx = [i for i in range(1, len(laps) - 1)
               if laps[i - 1]["trigger"] == "manual" and laps[i]["trigger"] == "manual" and laps[i]["time"] >= 20
               and sp[i] >= 1.25 * max(sp[i - 1], sp[i + 1])]
    return [{"lap": i + 1, "a": spans[i][0], "b": spans[i][1], "garmin": laps[i]["dist"]} for i in idx]


def match(reps, a, b, k_start="start", k_end="end"):
    best, ov = None, 0.0
    for r in reps:
        o = min(b, r[k_end]) - max(a, r[k_start])
        if o > ov:
            best, ov = r, o
    return best if ov >= 0.5 * (b - a) else None


def med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def q(xs, p):
    xs = sorted(x for x in xs if x is not None)
    return xs[min(len(xs) - 1, int(len(xs) * p))] if xs else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("person")
    d = ROOT / "data" / ap.parse_args().person.lower()
    cal = json.loads((d / "calibration.json").read_text())
    geo = {(x["id"], x["lap"]): x["geometry"] for x in cal["track_laps"]}
    gla = d / "gps_lap_analysis.json"
    if gla.exists():
        for x in json.loads(gla.read_text()).get("laps", []):
            if x.get("mode") == "gps":
                geo.setdefault((x["id"], x["lap"]), x["expected"])

    t_start = time.time()
    rows, sessions, t_v1, t_v2 = [], [], 0.0, 0.0
    for f in sorted((d / "activities").glob("*/*.fit")):
        if "running" not in f.parent.name:
            continue
        fix = first_fix(f)
        if not fix or hav(fix, cal["centre"]) > 400:
            continue
        _, _, laps, mode, device = _read(f)
        if sum(l["trigger"] == "manual" for l in laps) < 3:
            continue
        truth = truth_reps(laps)
        if not truth:
            continue
        sid = int(f.stem)
        t = time.time(); v1 = analyse_v1(f); t_v1 += time.time() - t
        t = time.time(); v2 = analyse(f, cal); t_v2 += time.time() - t
        sessions.append({"id": sid, "date": f.parent.name[:10], "mode": mode, "device": device, "truth": len(truth)})
        for x in truth:
            r1 = match(v1["reps"], x["a"], x["b"])
            r2 = match(v2["reps"], x["a"], x["b"])
            g = geo.get((sid, x["lap"]))
            row = {"id": sid, "date": f.parent.name[:10], "mode": mode, "device": device, "lap": x["lap"],
                   "press_a": round(x["a"], 2), "press_b": round(x["b"], 2), "garmin": x["garmin"], "geometry": g}
            if r1:  # v1: start/end are whole seconds, end inclusive
                row.update(v1_start=r1["start"] - x["a"], v1_dur=r1["time"] - (x["b"] - x["a"]), v1_dist=r1["dist"])
            if r2:
                row.update(v2_raw_s=r2["start_detected"] - x["a"], v2_raw_e=r2["end_detected"] - x["b"],
                           v2_start=r2["start"] - x["a"], v2_end=r2["end"] - x["b"],
                           v2_dist=r2["dist"], v2_dist_det=r2["dist_detected"], v2_check=r2["start_check"],
                           v2_gap_s=r2["record_gap_start_s"], v2_gap_e=r2["record_gap_end_s"])
                row["v2_dur"] = row["v2_end"] - row["v2_start"]
            rows.append(row)

    # leave-one-session-out lags for v2
    for r in rows:
        if "v2_raw_s" not in r:
            continue
        other = [o for o in rows if o["id"] != r["id"] and o["mode"] == r["mode"] and o["device"] == r["device"] and "v2_raw_s" in o]
        ls, le = med([o["v2_raw_s"] for o in other]), med([o["v2_raw_e"] for o in other])
        if ls is None:
            fb = lag_for(r["mode"], r["device"], None)
            ls, le = fb["start_s"], fb["end_s"]
        r["v2_loso_start"] = r["v2_raw_s"] - ls
        r["v2_loso_dur"] = (r["v2_raw_e"] - le) - r["v2_loso_start"]

    groups = sorted({(r["mode"], r["device"]) for r in rows}) + [("all", "all")]
    variants = [("v1.1", "v1_start", "v1_dur"), ("v2 raw", "v2_raw_s", None), ("v2 calib", "v2_start", "v2_dur"),
                ("v2 LOSO", "v2_loso_start", "v2_loso_dur")]
    summary = {}
    print(f"sessions {len(sessions)}, truth reps {len(rows)}; runtime v1.1 {t_v1:.1f} s, v2 {t_v2:.1f} s, total {time.time() - t_start:.1f} s\n")
    hdr = f"{'group':12} {'variant':9} {'found':>7} {'|start| med':>11} {'start med':>9} {'start p90|':>10} {'|dur| med':>9} {'dur med':>8} {'dur p90|':>8}"
    print(hdr)
    for mode, dev in groups:
        G = [r for r in rows if mode == "all" or (r["mode"], r["device"]) == (mode, dev)]
        name = f"{mode}/{dev}"
        for label, ks, kd in variants:
            S = [r[ks] for r in G if r.get(ks) is not None]
            if label == "v2 raw":
                Dd = [r["v2_raw_e"] - r["v2_raw_s"] for r in G if r.get("v2_raw_s") is not None]
            else:
                Dd = [r[kd] for r in G if r.get(kd) is not None]
            s = {"n": len(G), "found": len(S), "abs_start_med": med([abs(x) for x in S]), "start_med": med(S),
                 "abs_start_p90": q([abs(x) for x in S], 0.9), "abs_dur_med": med([abs(x) for x in Dd]),
                 "dur_med": med(Dd), "abs_dur_p90": q([abs(x) for x in Dd], 0.9)}
            summary[f"{name}/{label}"] = s
            f2 = lambda v: "   —" if v is None else f"{v:+.2f}"
            print(f"{name:12} {label:9} {s['found']:3}/{s['n']:<3} {f2(s['abs_start_med']):>11} {f2(s['start_med']):>9} "
                  f"{f2(s['abs_start_p90']):>10} {f2(s['abs_dur_med']):>9} {f2(s['dur_med']):>8} {f2(s['abs_dur_p90']):>8}")
        print()

    print(f"rep distance vs geometry (%): {'group':12} {'source':16} {'n':>3} {'median':>7} {'|med|':>6} {'p10':>6} {'p90':>6} {'within 0.5%':>11}")
    for mode, dev in groups:
        G = [r for r in rows if r["geometry"] and (mode == "all" or (r["mode"], r["device"]) == (mode, dev))]
        for label, k in (("Garmin lap", "garmin"), ("v1.1", "v1_dist"), ("v2 (reported)", "v2_dist"), ("v2 detected", "v2_dist_det")):
            E = [100 * (r[k] / r["geometry"] - 1) for r in G if r.get(k)]
            if not E:
                continue
            s = {"n": len(E), "median": med(E), "abs_median": med([abs(e) for e in E]), "p10": q(E, 0.1), "p90": q(E, 0.9),
                 "within_0.5": sum(abs(e) <= 0.5 for e in E) / len(E)}
            summary[f"{mode}/{dev}/dist/{label}"] = s
            print(f"{'':30} {mode + '/' + dev:12} {label:16} {s['n']:3} {s['median']:+7.2f} {s['abs_median']:6.2f} {s['p10']:+6.2f} {s['p90']:+6.2f} {100 * s['within_0.5']:10.0f}%")
        print()

    fitted = {}
    for mode, dev in groups[:-1]:
        G = [r for r in rows if (r["mode"], r["device"]) == (mode, dev) and "v2_raw_s" in r]
        fitted[f"{mode}/{dev}"] = {"start_s": round(med([r["v2_raw_s"] for r in G]), 2), "end_s": round(med([r["v2_raw_e"] for r in G]), 2),
                                   "n": len(G), "sessions": len({r["id"] for r in G})}
    print("v2 lags fitted on all sessions (detected change point - press):", json.dumps(fitted))
    out = {"sessions": sessions, "summary": summary, "fitted_lags_v2": fitted, "rows": rows}
    (d / "detector_eval.json").write_text(json.dumps(out, indent=1, default=lambda x: round(x, 3)))


if __name__ == "__main__":
    main()

"""Back-tests for the fitness model (numbers in docs/fitness-model.md).

Usage: uv run scripts/fitness_eval.py adrian      (or fitness_model.py adrian --eval)
Writes data/<person>/fitness_eval.json and prints the tables.
"""

from __future__ import annotations

import collections
import datetime as dt
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fitness_model as fm  # noqa: E402

ROOT = fm.ROOT


def mad_sd(x):
    x = np.asarray(x, float)
    return float(1.4826 * np.median(np.abs(x - np.median(x)))) if len(x) else float("nan")


def pairs_sd(items, max_days=10):
    """Test-retest: SD of one session from differences of consecutive sessions <= max_days apart
    (robust: MAD of differences / sqrt 2)."""
    items = sorted(items)
    d = [b[1] - a[1] for a, b in zip(items, items[1:]) if b[0] - a[0] <= max_days]
    return (mad_sd(d) / math.sqrt(2) if len(d) >= 4 else None), len(d)


def watch(date):
    return "4315" if date >= fm.NEW_WATCH_FROM else "fr935"


def run(person, out=True):
    acts, feats, rest = fm.load_features(person, progress=False)
    R = {}
    day = lambda a: dt.date.fromisoformat(a["date"]).toordinal()

    # ---------------------------------------------------------------- races
    races = []
    for i, f in feats.items():
        a = acts[i]
        if a["type"] == "race?" and "err" not in f:
            nom, nm = fm.race_nominal(f["dist_m"])
            if nom:
                races.append({"id": i, "date": a["date"], "day": day(a), "race": nm, "place": a.get("place"),
                              "vdot": fm.vdot_from_race(nom, a.get("moving_s") or f["moving_s"]),
                              "time_s": a.get("moving_s")})
    races.sort(key=lambda r: r["day"])
    R["races"] = [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items() if k != "day"} for r in races]

    # ---------------------------------------------------------------- interval sessions table
    ints = []
    for i, f in feats.items():
        a = acts[i]
        if a["type"] in ("intervals_track", "intervals_road") and isinstance(f.get("wbal"), dict) and a.get("dist_trusted"):
            reps, br = a.get("reps") or [], a.get("breaks_s") or []
            ints.append({"id": i, "day": day(a), "date": a["date"], "track": a.get("atype") == "track_running" or a.get("place") == "Bislet",
                         "wbal": f["wbal"], "flat": f.get("wbal_flat"), "hrr": f.get("work_hrr"), "sig": a.get("sig"),
                         "rep_s": float(np.median([r[0] for r in reps])) if reps else None,
                         "rest_s": float(np.median(br)) if br else None, "watch": watch(a["date"])})
    ints.sort(key=lambda r: r["day"])

    # (1) tau calibration: agreement with races and independence of rep length
    tau_rows = {}
    for tau in fm.TAU_VARIANTS:
        k = str(tau)
        near = []
        for r in races:
            if r["race"] not in ("5k", "10k"):
                continue
            for s in ints:
                if 0 < r["day"] - s["day"] <= 42 or 0 < s["day"] - r["day"] <= 14:
                    near.append(s["wbal"][k] - r["vdot"])
        # rep-length bias: residual vs the 60-day median of all interval sessions, regressed on log2(rep_s)
        res, lr = [], []
        for s in ints:
            nb = [x["wbal"][k] for x in ints if abs(x["day"] - s["day"]) <= 30 and x["id"] != s["id"]]
            if len(nb) >= 3 and s["rep_s"]:
                res.append(s["wbal"][k] - np.median(nb)); lr.append(math.log2(s["rep_s"]))
        slope = float(np.polyfit(lr, res, 1)[0]) if len(lr) > 5 else None
        e, _ = fm.forecast_errors([(s["day"], s["wbal"][k], 1.5 if s["track"] else 2.0, "hard") for s in ints], 0.012)
        tr = pairs_sd([(s["day"], s["wbal"][k]) for s in ints if s["track"]], 14)
        tau_rows[k] = {"vs_race_median": round(float(np.median(near)), 2), "vs_race_n": len(near),
                       "rep_len_slope_per_doubling": round(slope, 2) if slope is not None else None,
                       "forecast_mae": round(float(np.mean(np.abs(e))), 2), "forecast_rmse": round(float(np.sqrt(np.mean(e**2))), 2),
                       "track_test_retest_sd": round(tr[0], 2) if tr[0] else None, "pairs": tr[1]}
    R["tau_calibration"] = tau_rows

    tau = fm.P["tau_scale"]; k = str(tau)

    # (2) HR as final adjustment of interval scores: y + b (0.9 - work %HRR)
    hr_rows = {}
    for b in (0.0, 5.0, 10.0, 20.0):
        row = {}
        for wsel in ("all", "fr935", "4315"):
            sub = [s for s in ints if s["hrr"] is not None and (wsel == "all" or s["watch"] == wsel)]
            obs = [(s["day"], s["wbal"][k] + b * (0.9 - s["hrr"]), 1.5 if s["track"] else 2.0, "hard") for s in sub]
            e, _ = fm.forecast_errors(obs, 0.012)
            tr = pairs_sd([(o[0], o[1]) for o, s in zip(obs, sub) if s["track"]], 14)
            row[wsel] = {"forecast_mae": round(float(np.mean(np.abs(e))), 2), "n": len(e),
                         "track_test_retest_sd": round(tr[0], 2) if tr[0] else None}
        hr_rows[str(b)] = row
    R["hr_adjustment_interval"] = hr_rows
    # work HR level by watch on the same kind of session (Bislet 6x1000 / 5x1000)
    w1000 = collections.defaultdict(list)
    for s in ints:
        if s["sig"] and ("×1000" in s["sig"]) and s["track"] and s["hrr"] is not None:
            w1000[s["watch"]].append(s["hrr"])
    R["work_hrr_1000s_by_watch"] = {w: {"n": len(v), "median": round(float(np.median(v)), 3), "p10": round(float(np.percentile(v, 10)), 3),
                                        "p90": round(float(np.percentile(v, 90)), 3)} for w, v in w1000.items()}
    # correlation of HR effort with the residual vs neighbours (does low HR mean an easy session?)
    xs, ys = [], []
    for s in ints:
        nb = [x["wbal"][k] for x in ints if abs(x["day"] - s["day"]) <= 30 and x["id"] != s["id"]]
        if len(nb) >= 3 and s["hrr"] is not None:
            xs.append(s["hrr"]); ys.append(s["wbal"][k] - np.median(nb))
    R["hrr_vs_residual_corr"] = {"r": round(float(np.corrcoef(xs, ys)[0, 1]), 3), "n": len(xs)}

    # (3) HR-only aerobic estimate (Swain %HRR) on steady runs
    aer = [(day(acts[i]), f["hr_aer"]["vdot"], f["hr_aer"]["hrr"], acts[i]["date"], acts[i].get("route"))
           for i, f in feats.items() if acts[i]["sport"] == "run" and acts[i]["type"] in ("easy", "long", "tempo")
           and (f.get("hr_aer") or {}).get("vdot") and f["hr_aer"]["n_chunks"] >= 3]
    aer.sort()
    tr = pairs_sd([(a[0], a[1]) for a in aer], 10)
    hrr_bins = {}
    for lo in (0.5, 0.6, 0.7, 0.8):
        v = [a[1] for a in aer if lo <= a[2] < lo + 0.1]
        if v:
            hrr_bins[f"{lo:.1f}-{lo + 0.1:.1f}"] = {"n": len(v), "median": round(float(np.median(v)), 1)}
    # pace-based interval shape (smoothed) vs HR estimate, by quarter
    obs_i = [(s["day"], s["wbal"][k], 1.5 if s["track"] else 2.0, "hard") for s in ints]
    d0, d1 = min(o[0] for o in obs_i) - 1, max(o[0] for o in obs_i)
    days, m, sd, _, _ = fm.smooth_series(obs_i, d0, d1, 0.012)
    qa = collections.defaultdict(list); qi = {}
    for a in aer:
        if d0 <= a[0] <= d1:
            q = a[3][:4] + "Q" + str((int(a[3][5:7]) - 1) // 3 + 1)
            qa[q].append(a[1]); qi.setdefault(q, []).append(m[a[0] - d0])
    qq = [(np.median(qa[q]), np.median(qi[q])) for q in qa if len(qa[q]) >= 3]
    r_q = float(np.corrcoef(*zip(*qq))[0, 1]) if len(qq) > 3 else None
    by_year = collections.defaultdict(list)
    for a in aer:
        by_year[a[3][:4]].append(a[1])
    race_cmp = []
    for r in races:
        v = [a[1] for a in aer if 0 < r["day"] - a[0] <= 42]
        if len(v) >= 2:
            race_cmp.append(float(np.median(v)) - r["vdot"])
    R["hr_aerobic"] = {"n": len(aer), "test_retest_sd": round(tr[0], 2) if tr[0] else None, "pairs": tr[1],
                       "median_by_hrr": hrr_bins,
                       "median_by_year": {y: round(float(np.median(v)), 1) for y, v in sorted(by_year.items())},
                       "quarterly_corr_with_interval_shape": round(r_q, 3) if r_q is not None else None,
                       "n_quarters": len(qq),
                       "minus_race_vdot_6wk_before": [round(x, 1) for x in race_cmp]}
    # efficiency factor (GAP m/s per beat) at Bislet easy-ish runs by watch: HR offset at the watch change
    ef = collections.defaultdict(list)
    for i, f in feats.items():
        a = acts[i]
        ha = f.get("hr_aer") or {}
        if a["sport"] == "run" and a["type"] in ("easy", "long") and ha.get("ef") and a["date"] >= "2025-03-01":
            ef[watch(a["date"])].append(ha["ef"] * 60)  # m/min per beat
    R["ef_easy_by_watch_since_2025_03"] = {w: {"n": len(v), "median_m_per_beat": round(float(np.median(v)), 3)} for w, v in ef.items()}

    # (4) test-retest per evidence kind (production settings)
    ev = fm.collect_evidence(acts, feats)
    grp = collections.defaultdict(list)
    for e in ev:
        sub = e["kind"]
        if e["discipline"] == "interval" and e["kind"] == "hard":
            sub += "/track" if e["sigma"] == fm.SIG["int_track"] else "/road"
        grp[(e["discipline"], sub)].append((e["day"], e["score"]))
    R["test_retest"] = {f"{d}:{s}": dict(zip(("sd", "pairs"), [round(x, 2) if isinstance(x, float) else x for x in pairs_sd(v, 10)]))
                        for (d, s), v in grp.items()}
    # offsets: median residual vs the smoothed estimate, per kind
    doc = fm.build(person, progress=False)
    resid = collections.defaultdict(list)
    for s in doc["sessions"]:
        if s["shape_at"] is not None:
            resid[(s["discipline"], s["kind"])].append(s["score"] - s["shape_at"])
    R["residual_vs_shape"] = {f"{d}:{kd}": {"n": len(v), "median": round(float(np.median(v)), 2), "mad_sd": round(mad_sd(v), 2)}
                              for (d, kd), v in resid.items()}

    # (5) race prediction: forward-filtered estimate before the race (race itself and later data excluded)
    pred = []
    for r in races:
        row = {"date": r["date"], "race": r["race"], "race_vdot": round(r["vdot"], 2)}
        for disc in ("interval", "long"):
            for variant, kw in (("pace", {}), ("pace+hr_aer", {"use_hr_aer": True}), ("pace+hr_adj10", {"hr_b": 10.0})):
                evd = [e for e in fm.collect_evidence(acts, feats, **kw) if e["discipline"] == disc and e["day"] < r["day"]]
                if len(evd) < 3:
                    continue
                obs = fm._obs(evd, disc)
                dd, mm, ss, _, _ = fm.smooth_series(obs, min(o[0] for o in obs) - 7, r["day"] - 1, fm.P["q_per_day"][disc])
                row[f"{disc}/{variant}"] = [round(float(mm[-1]), 2), round(float(ss[-1]), 2)]
        pred.append(row)
    R["race_prediction"] = pred
    summ = {}
    fits_disc = {"interval": ("5k", "10k"), "long": ("10k", "half", "marathon")}
    for key in {k for row in pred for k in row if "/" in k}:
        e = [row[key][0] - row["race_vdot"] for row in pred if key in row and row["race"] in fits_disc[key.split("/")[0]]]
        summ[key] = {"n": len(e), "mean_err": round(float(np.mean(e)), 2), "mae": round(float(np.mean(np.abs(e))), 2)}
    R["race_prediction_summary"] = dict(sorted(summ.items()))

    # (6) repeated routes: Holmenkollen rollerski climb (R3) and Bislet 1000s
    g = json.loads((ROOT / "data" / person / "groups.json").read_text())
    r3 = g["routes"].get("R3", {})
    rows = []
    for s in (r3.get("legs_table") or {}).get("sessions", []):
        f = feats.get(str(s["id"])) or {}
        if f.get("ski") and all(s["legs"]):
            rows.append((s["date"], f["ski"]["vdot"], sum(l["moving_s"] for l in s["legs"]), s["type"]))
    if rows:
        sc = np.array([r[1] for r in rows]); mv = np.array([r[2] for r in rows])
        R["R3_holmenkollen"] = {"n": len(rows), "corr_score_vs_log_speed": round(float(np.corrcoef(sc, -np.log(mv))[0, 1]), 3),
                                "score_sd": round(float(sc.std()), 2),
                                "sessions": [{"date": r[0], "score": round(r[1], 1), "moving_s": r[2], "type": r[3]} for r in rows]}
    b1000 = [(s["day"], s["wbal"][k], s["date"], s["sig"]) for s in ints if s["track"] and s["sig"] in ("6×1000", "5×1000")]
    if b1000:
        tr = pairs_sd([(x[0], x[1]) for x in b1000], 21)
        R["bislet_1000s"] = {"n": len(b1000), "test_retest_sd_21d": round(tr[0], 2) if tr[0] else None, "pairs": tr[1],
                             "by_year": {y: round(float(np.median([x[1] for x in b1000 if x[2][:4] == y])), 1)
                                         for y in sorted({x[2][:4] for x in b1000})}}
    # (7) GAP on/off for road intervals: test-retest
    road = [s for s in ints if not s["track"] and s["flat"] is not None]
    R["gap_road_intervals"] = {"with_gap": pairs_sd([(s["day"], s["wbal"][k]) for s in road], 21),
                               "flat": pairs_sd([(s["day"], s["flat"]) for s in road], 21)}
    # (8) q (random-walk variance) choice by one-step-ahead predictive log-likelihood
    qrow = {}
    for disc in fm.DISCIPLINES:
        obs = fm._obs(ev, disc)
        best = None
        for q in (0.002, 0.005, 0.01, 0.02, 0.05, 0.1):
            e, s = fm.forecast_errors(obs, q)
            if not len(e):
                continue
            z = e / s
            z = np.clip(z, -3, 3)  # robust: cap outliers
            ll = float(np.mean(-0.5 * z**2 - np.log(s)))
            qrow.setdefault(disc, {})[str(q)] = round(ll, 3)
    R["q_loglik"] = qrow
    if out:
        p = ROOT / "data" / person / "fitness_eval.json"
        p.write_text(json.dumps(R, ensure_ascii=False, indent=1, default=float))
        print(json.dumps(R, ensure_ascii=False, indent=1, default=float)[:20000])
    return R


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "adrian")

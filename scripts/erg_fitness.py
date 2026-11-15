"""Erg fitness trend (Concept2 RowErg + SkiErg): a smoothed 2k-equivalent pace / watts per machine.

Usage: uv run scripts/erg_fitness.py adrian [--today YYYY-MM-DD] [--quiet]
Reads only data/<person>/concept2/ (scripts/fetch_concept2.py), writes data/<person>/erg_fitness.json.
Model and file format: docs/erg.md. Own scale (pace /500 m and watts), not VDOT, not part of Overall.

Per session (machine = row | skierg):
  work      continuous piece: its average pace; interval session: the time-weighted average watts of the work
            intervals (rests and zero-distance intervals left out), turned back into a pace.
  duration  continuous: its distance. Intervals: an effective continuous duration between one rep and the total
            work time, d_eff = rep_s · n^f with f = work / (work + rest between reps) (no rest -> continuous,
            long rests -> one rep), at the work pace -> an effective distance.
  2k-eq     Paul's law: pace(2000 m) = pace(D) − 5 s · log2(D / 2000) (+5 s /500 m per doubling of distance).
  kind      test       a single continuous piece of 1:30–10:00 or a standard test distance, that scores at or above
                       the trend (σ 1.5 s, full evidence both ways)
            piece      another continuous piece (σ 2.5 s, a lower bound: below the trend counts weakly)
            intervals  σ 3.0 s, robust like a piece
            warmup     a same-day extra piece clearly slower than the day's best (bound: ignored unless far above)
            ignored    < 60 s or < 250 m of work, or broken (most interval work without distance)
Smoother: fitness_model.smooth_series (daily random walk, Kalman + RTS, asymmetric robust weights) on y = −pace
(higher = better), q = 0.1 s²/day, prior sd 8 s. No HR in the data, so intent is unknown: hard/bound weights keep
easy sessions from pulling the line down.

Library: `build(person, today=None) -> doc`, `score_result(r)`, `paul_2k(pace, dist_m)`, `watts`, `pace_from_watts`,
`fmt_pace`, `concept2_paths(person)`, `write(person)`.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fitness_model as fm  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MODEL_VERSION = 2   # 2: shared-machine sessions scored on own turns (concept2.clean)
MACHINES = {"rower": "row", "skierg": "skierg"}         # Concept2 type -> app sport family
LABELS = {"row": "Row", "skierg": "SkiErg"}
P = {
    "paul_s_per_doubling": 5.0,     # Paul's law: +5 s /500 m per doubling of distance
    "ref_m": 2000,
    "q_per_day": 0.1,               # s² / day random walk: ~1.7 s /500 m sd per month
    "prior_sd": 8.0,
    "sigma": {"test": 1.5, "piece": 2.5, "intervals": 3.0, "warmup": 3.0},
    "test_s": [90, 600],            # a single continuous piece of this work time (s) can be a test
    "standard_m": [500, 1000, 2000, 5000, 6000, 10000],
    "min_s": 60, "min_m": 250,      # shorter pieces are ignored
    "warmup_gap_s": 3.0,            # same-day extra piece this much slower (2k-eq) than the day's best -> warmup
    "stale_days": 90,
}
KIND_SMOOTH = {"test": "race", "piece": "hard", "intervals": "hard", "warmup": "bound"}
PB_DURATIONS_S = [60, 120, 240, 600, 1200, 1800, 2400]


# ------------------------------------------------------------------ pace / watts
def watts(pace_s_500: float) -> float:
    """Concept2: P = 2.80 / (s per m)³"""
    return 2.80 / (pace_s_500 / 500) ** 3


def pace_from_watts(w: float) -> float:
    return 500 * (2.80 / w) ** (1 / 3)


def paul_2k(pace_s_500: float, dist_m: float) -> float:
    """2k-equivalent pace by Paul's law: pace(2000) = pace(D) − 5 s · log2(D / 2000)"""
    return pace_s_500 - P["paul_s_per_doubling"] * math.log2(dist_m / P["ref_m"])


def fmt_pace(s: float | None, unit: bool = True) -> str:
    """'1:48.3 /500 m'"""
    if s is None or not math.isfinite(s):
        return "—"
    t = round(s, 1)
    m, x = int(t // 60), t - 60 * int(t // 60)
    return f"{m}:{x:04.1f}" + (" /500 m" if unit else "")


def _mmss(s: float) -> str:
    s = int(round(s))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def _label(r: dict) -> str:
    try:
        import concept2  # scripts/concept2.py: the same label as the activity name
        return concept2.workout_name(r)
    except Exception:  # noqa: BLE001
        return f"{r.get('distance')}m" if r.get("workout_type") == "FixedDistanceSplits" else _mmss((r.get("time") or 0) / 10)


# ------------------------------------------------------------------ files
def concept2_dir(person: str) -> Path:
    return ROOT / "data" / person / "concept2"


def concept2_paths(person: str) -> list[Path]:
    """inputs whose mtime decides whether erg_fitness.json is stale"""
    d = concept2_dir(person)
    # activities_index.json is rewritten by concept2.sync (also after cleaning); overrides can force / undo cleaning
    return [d / "results.json", d / "results", d / "activities_index.json", ROOT / "data" / person / "session_overrides.json"]


def load_results(person: str) -> list[dict]:
    """every result with its intervals / splits, as this person did it: concept2.load_result (results/<id>/result.json,
    else the list row; shared-machine sessions cleaned to the person's own turns, docs/concept2.md#cleaning)"""
    import concept2 as c2
    d = concept2_dir(person)
    p = d / "results.json"
    if not p.exists():
        return []
    D = ROOT / "data" / person
    out = []
    for row in json.loads(p.read_text()):
        try:
            r = c2.load_result(D, row["id"], row) or row
        except (ValueError, OSError):
            r = row
        out.append(r)
    return out


# ------------------------------------------------------------------ per-session score
def _pieces(r: dict) -> list[dict]:
    """sub-pieces for personal bests: valid work intervals or splits, {s, m, what}"""
    w = r.get("workout") or {}
    ivs, sps = w.get("intervals") or [], w.get("splits") or []
    out = []
    for i, x in enumerate(ivs or sps):
        s, m = (x.get("time") or 0) / 10, x.get("distance") or 0
        if s >= 10 and m >= 50:
            out.append({"s": s, "m": m, "what": f"{'rep' if ivs else 'split'} {i + 1}"})
    return out


def score_result(r: dict) -> dict | None:
    """One session's score (before the trend): {id, date, machine, label, kind (candidate), work_s, work_m, rest_s,
    pace (work), watts (work), d_eff_s, d_eff_m, pace_2k, watts_2k, n_reps, test_candidate, reason}.
    None for machines other than RowErg / SkiErg."""
    machine = MACHINES.get(r.get("type"))
    if not machine:
        return None
    base = {"id": str(r["id"]), "date": (r.get("date") or "")[:10], "start": (r.get("date") or "")[:19],
            "machine": machine, "label": _label(r), "workout_type": r.get("workout_type"),
            "drag_factor": r.get("drag_factor"), "stroke_rate": r.get("stroke_rate")}
    ivs = (r.get("workout") or {}).get("intervals") or []
    total_s, total_m = (r.get("time") or 0) / 10, r.get("distance") or 0
    if len(ivs) > 1:
        valid = [x for x in ivs if (x.get("time") or 0) > 0 and (x.get("distance") or 0) > 0]
        ws = sum(x["time"] for x in valid) / 10
        all_s = sum((x.get("time") or 0) for x in ivs) / 10
        if len(valid) < 2 or ws < 0.5 * all_s or ws < P["min_s"]:
            return {**base, "kind": "ignored", "reason": "interval work without distance", "work_s": all_s,
                    "work_m": total_m}
        rest = sum((x.get("rest_time") or 0) for x in ivs[:-1]) / 10   # the rest after the last rep isn't between reps
        wm = sum(x["distance"] for x in valid)
        w_avg = sum(x["time"] / 10 * watts(x["time"] / 10 / x["distance"] * 500) for x in valid) / ws
        pace = pace_from_watts(w_avg)
        n = len(valid)
        f = ws / (ws + rest) if ws + rest > 0 else 1.0
        d_eff = ws / n * n ** f
        d_m = d_eff * 500 / pace
        p2k = paul_2k(pace, d_m)
        return {**base, "kind": "intervals", "work_s": round(ws, 1), "work_m": wm, "rest_s": round(rest, 1),
                "n_reps": n, "work_share": round(f, 3), "pace": round(pace, 2), "watts": round(w_avg, 1),
                "d_eff_s": round(d_eff, 1), "d_eff_m": round(d_m), "pace_2k": round(p2k, 2),
                "watts_2k": round(watts(p2k), 1), "test_candidate": False, "pieces": _pieces(r)}
    # continuous: the whole piece (a single interval counts as one piece)
    if len(ivs) == 1:
        total_s, total_m = (ivs[0].get("time") or 0) / 10, ivs[0].get("distance") or 0
    if total_s < P["min_s"] or total_m < P["min_m"]:
        return {**base, "kind": "ignored", "reason": "too short", "work_s": total_s, "work_m": total_m}
    pace = total_s / total_m * 500
    p2k = paul_2k(pace, total_m)
    std = any(abs(total_m - d) <= 0.005 * d for d in P["standard_m"])
    cand = P["test_s"][0] <= total_s <= P["test_s"][1] or (std and total_s >= P["test_s"][0])
    return {**base, "kind": "piece", "work_s": round(total_s, 1), "work_m": total_m, "rest_s": 0.0, "n_reps": 1,
            "pace": round(pace, 2), "watts": round(watts(pace), 1), "d_eff_s": round(total_s, 1),
            "d_eff_m": total_m, "pace_2k": round(p2k, 2), "watts_2k": round(watts(p2k), 1),
            "test_candidate": bool(cand), "pieces": _pieces(r) if len(ivs) != 1 else []}   # + "whole" in _pbs


def _mark_warmups(rows: list[dict]) -> None:
    """same machine, same day: extra pieces clearly slower (2k-eq) than the day's best become warm-ups"""
    by = collections.defaultdict(list)
    for s in rows:
        if s["kind"] != "ignored":
            by[(s["machine"], s["date"])].append(s)
    for grp in by.values():
        if len(grp) < 2:
            continue
        best = min(s["pace_2k"] for s in grp)
        for s in grp:
            if s["pace_2k"] > best + P["warmup_gap_s"]:
                s["kind"], s["test_candidate"] = "warmup", False


# ------------------------------------------------------------------ trend
def _smooth(rows: list[dict], kinds: dict, d0: int, d1: int):
    obs = [(dt.date.fromisoformat(s["date"]).toordinal(), -s["pace_2k"], P["sigma"][kinds[s["id"]]],
            KIND_SMOOTH[kinds[s["id"]]]) for s in rows]
    order = sorted(range(len(rows)), key=lambda j: obs[j][0])   # smooth_series sorts by day (stable)
    days, m, sd, w, _ = fm.smooth_series(obs, d0, d1, P["q_per_day"], prior_sd=P["prior_sd"])
    weights = [0.0] * len(rows)
    for k, j in enumerate(order):
        weights[j] = float(w[k])
    return days, m, sd, weights


def _machine(rows: list[dict], today: dt.date) -> dict | None:
    rows = sorted(rows, key=lambda s: s["start"])
    scored = [s for s in rows if s["kind"] != "ignored"]
    if not scored:
        return None
    d0 = dt.date.fromisoformat(scored[0]["date"]).toordinal()
    d1 = max(today.toordinal(), dt.date.fromisoformat(scored[-1]["date"]).toordinal())
    # pass 1: test candidates as pieces; pass 2: a candidate at or above the trend (within 1 σ) is a test
    kinds = {s["id"]: s["kind"] for s in scored}
    days, m, sd, _ = _smooth(scored, kinds, d0, d1)
    for s in scored:
        if s.get("test_candidate"):
            k = dt.date.fromisoformat(s["date"]).toordinal() - d0
            if -s["pace_2k"] >= m[k] - P["sigma"]["test"]:
                kinds[s["id"]] = "test"
    days, m, sd, weights = _smooth(scored, kinds, d0, d1)
    for s, wt in zip(scored, weights):
        k = dt.date.fromisoformat(s["date"]).toordinal() - d0
        s["kind"] = kinds[s["id"]]
        s["sigma"], s["weight"] = P["sigma"][s["kind"]], round(wt, 3)
        s["trend_pace"] = round(float(-m[k]), 2)
        s["delta_s"] = round(s["pace_2k"] - s["trend_pace"], 2)   # > 0: slower than the trend that day

    def pt(k):
        p, e = float(-m[k]), float(sd[k])
        return {"date": dt.date.fromordinal(int(days[k])).isoformat(), "pace": round(p, 2), "sd": round(e, 2),
                "pace_fast": round(p - e, 2), "pace_slow": round(p + e, 2), "watts": round(watts(p), 1)}
    daily = [pt(k) for k in range(len(days))]
    ev = [s for s in scored if s["kind"] in ("test", "piece", "intervals") and s["weight"] > 0.5]
    last = max((s["date"] for s in ev), default=None)
    kt = today.toordinal() - d0
    cur = pt(min(kt, len(days) - 1))
    cur.update(pace_text=fmt_pace(cur["pace"]), watts_fast=round(watts(cur["pace_fast"]), 1),
               watts_slow=round(watts(cur["pace_slow"]), 1),
               time_2k_s=round(cur["pace"] * 4, 1), last_evidence_date=last,
               days_since_evidence=(today - dt.date.fromisoformat(last)).days if last else None)
    cur["stale"] = cur["days_since_evidence"] is None or cur["days_since_evidence"] > P["stale_days"]
    k90 = kt - 90
    cur["trend_90d_s"] = round(cur["pace"] - daily[k90]["pace"], 2) if 0 <= k90 < len(daily) else None
    weekly = [daily[k] for k in range(len(daily) - 1, -1, -7)][::-1]
    return {"label": LABELS[rows[0]["machine"]], "unit": "s /500 m (2k-equivalent)", "current": cur,
            "daily": daily, "weekly": weekly, "personal_bests": _pbs(rows),
            "n_sessions": len(rows), "n_scored": len(scored), "first_date": scored[0]["date"],
            "last_date": scored[-1]["date"]}


def _pbs(rows: list[dict]) -> dict:
    """best 2k-equivalent session; best pace over ≥ D m and best average watts over ≥ T s from whole pieces, reps and
    splits (a piece of ≥ D at pace p means the best D was at least that fast). `exact`: the piece was D (±1 %)."""
    ok = [s for s in rows if s["kind"] != "ignored"]
    best = min((s for s in ok if s["kind"] != "warmup"), key=lambda s: s["pace_2k"], default=None)
    pieces = []
    for s in ok:
        ps = list(s.get("pieces") or [])
        if s["kind"] in ("piece", "test", "warmup"):
            ps.append({"s": s["work_s"], "m": s["work_m"], "what": "whole"})
        for p in ps:
            pieces.append({**p, "id": s["id"], "date": s["date"], "label": s["label"], "pace": p["s"] / p["m"] * 500})
    dists = []
    for d in P["standard_m"]:
        c = [p for p in pieces if p["m"] >= 0.995 * d]
        if c:
            b = min(c, key=lambda p: (p["pace"], p["date"]))
            dists.append({"m": d, "pace": round(b["pace"], 2), "time_s": round(b["pace"] * d / 500, 1),
                          "watts": round(watts(b["pace"]), 1), "id": b["id"], "date": b["date"], "label": b["label"],
                          "what": b["what"], "piece_m": b["m"], "piece_s": b["s"], "exact": abs(b["m"] - d) <= 0.01 * d})
    durs = []
    for t in PB_DURATIONS_S:
        c = [p for p in pieces if p["s"] >= 0.99 * t]
        if c:
            b = min(c, key=lambda p: (p["pace"], p["date"]))
            durs.append({"s": t, "watts": round(watts(b["pace"]), 1), "pace": round(b["pace"], 2), "id": b["id"],
                         "date": b["date"], "label": b["label"], "what": b["what"], "piece_s": b["s"],
                         "exact": abs(b["s"] - t) <= 0.01 * t})
    return {"best_2k_equivalent": {"pace": best["pace_2k"], "watts": best["watts_2k"], "id": best["id"],
                                   "date": best["date"], "label": best["label"], "kind": best["kind"]} if best else None,
            "distances": dists, "durations": durs}


def build(person: str, today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    sessions = [s for s in (score_result(r) for r in load_results(person)) if s]
    _mark_warmups(sessions)
    machines = {}
    for mk in ("row", "skierg"):
        doc = _machine([s for s in sessions if s["machine"] == mk], today)
        if doc:
            machines[mk] = doc
    out_sessions = []
    for s in sorted(sessions, key=lambda s: s["start"]):
        s = {k: v for k, v in s.items() if k not in ("pieces", "test_candidate")}
        if s.get("pace") is not None:
            s["pace_text"] = fmt_pace(s["pace"])
            s["pace_2k_text"] = fmt_pace(s.get("pace_2k"))
        out_sessions.append(s)
    return {"built": dt.datetime.now().isoformat(timespec="seconds"), "today": today.isoformat(),
            "model_version": MODEL_VERSION, "params": P, "scale": "pace s /500 m, 2k-equivalent (Paul's law); "
            "watts = 2.80 / (s per m)³", "machines": machines, "sessions": out_sessions}


def write(person: str, today: dt.date | None = None) -> dict:
    doc = build(person, today)
    out = ROOT / "data" / person / "erg_fitness.json"
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
    tmp.replace(out)
    return doc


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("person")
    ap.add_argument("--today", help="YYYY-MM-DD (default: today)")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    if not (concept2_dir(a.person) / "results.json").exists():
        sys.exit(f"no {concept2_dir(a.person) / 'results.json'}: run scripts/fetch_concept2.py {a.person}")
    t0 = time.time()
    doc = write(a.person, dt.date.fromisoformat(a.today) if a.today else None)
    if a.quiet:
        return
    print(f"wrote data/{a.person}/erg_fitness.json in {time.time() - t0:.2f} s")
    for mk, d in doc["machines"].items():
        c = d["current"]
        print(f"\n{d['label']}: now {fmt_pace(c['pace'])} ± {c['sd']:.1f} s ({c['watts']:.0f} W, 2k ≈ "
              f"{_mmss(c['time_2k_s'])}), last evidence {c['last_evidence_date']}, {d['n_scored']}/{d['n_sessions']} scored")
        print("  month     trend      ±sd   watts")
        seen = set()
        for p in d["daily"]:
            mo = p["date"][:7]
            if mo not in seen:
                seen.add(mo)
                print(f"  {mo}  {fmt_pace(p['pace'], False):>8}  {p['sd']:5.1f}  {p['watts']:6.0f}")
        b = d["personal_bests"]["best_2k_equivalent"]
        if b:
            print(f"  best 2k-eq: {fmt_pace(b['pace'])} ({b['watts']:.0f} W) {b['date']} {b['label']} [{b['kind']}]")
        for x in d["personal_bests"]["distances"]:
            print(f"  best ≥{x['m']} m: {fmt_pace(x['pace'])} {x['date']} {x['label']} {x['what']}"
                  f"{'' if x['exact'] else ' (in a longer piece)'}")
        for x in d["personal_bests"]["durations"]:
            print(f"  best ≥{_mmss(x['s'])}: {x['watts']:.0f} W {x['date']} {x['label']} {x['what']}")
        kinds = collections.Counter(s["kind"] for s in doc["sessions"] if s["machine"] == mk)
        print("  kinds:", dict(kinds))


if __name__ == "__main__":
    main()

"""Interval detector for cross-country / roller skiing (see docs/session-groups.md, "Ski intervals").

The run detector (run_analysis) finds reps as fast stretches (>= 2.5 m/s). On skis that fails: speed
depends on terrain (uphill reps are slow, the downhill back is the fastest part) and the Holmenkollen
climb is a sequence of climbs between standing stops whatever the intensity. This detector works on
the session_groups 1 Hz grid (fast reader, ~10 ms per file) in two modes:

* **laps** (`mode "ski_laps"`): a programmed workout, i.e. >= 5 consecutive time-triggered laps
  (or laps marked rest / recovery) alternating between a work and a rest duration. Work = the longer
  phase (equal durations: the phase where HR rises). Typical: indoor / no-GPS sessions `4×4:00 / 2:00`.
* **effort** (`mode "ski_effort"`): work rate per kg as vertical-equivalent speed
  P = v·(CRR + grade) + CD·v³ (m/s; × 9.81 = W/kg), 15 s smoothed, clipped at 0 (descents and gliding
  are ~0, standing is 0). Recovery = standing, or P < 45 % of the session's median working P, for
  >= 30 s. Work blocks = the rest (>= 60 s). Only in hard sessions (>= 25 % of HR time >= Z4 floor,
  heart_rate_zones.json, default 165). A block is hard if its last-third HR >= max(Z4 floor, median of
  the 3 hardest blocks - 12). Reps = the contiguous series of hard blocks with the most work time;
  hard blocks < 60 s apart or without a 12 bpm HR drop between them are one rep; first / last reps
  shorter than half the median are dropped. The session counts only with >= 3 reps of <= 20 min,
  >= 3 of them within +-25 % of the median, >= 8 min rep time, median recovery <= 8 min and median HR
  drop in the recoveries >= 20 bpm; otherwise no reps are returned. So an easy Holmenkollen climb
  (same stops, HR < Z4) has no reps; a hard one where the climbs between stops end near max HR with
  real recoveries gets e.g. `4×4:30`.

`analyse(fit)` -> {version, mode, threshold (HR), reps [{start, end, time, dist, speed, hr, maxhr,
hr_start, hr_end, climb_m, dist_source}], breaks [{start, end, time, dist, standing, hr_drop}],
laps [(intensity, trigger)], blocks (effort mode: all work blocks, for debugging)}.
`compact(x)` -> the session_groups cache format (rep_times, rep_dists, rep_speeds, rep_hr, breaks, laps, mode).
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import session_groups as sg  # noqa: E402

VERSION = "ski-1.0"
CRR, CD = 0.03, 4e-4          # sliding resistance (roller ski ~0.02-0.03, snow ~0.03-0.05); 0.5·rho·CdA/(m·g) s²/m²
GRADE_W = 10                  # s: grade from altitude over +-10 s (>= 15 m travelled)
P_SMOOTH = 15                 # s
REC_REL, REC_MIN_S = 0.45, 30   # recovery = standing or P < 45 % of the working median, for >= 30 s
MIN_BLOCK_S, MAX_REP_S = 60, 1200
HR_DROP = 12                  # bpm: HR must drop at least this much in a recovery
HR_TOP_MARGIN = 12            # bpm: a rep's late HR must also be within 12 bpm of the session's hardest blocks
SHORT_BLOCK_S = 90            # non-hard blocks shorter than this don't interrupt a series of reps
MIN_REPS, MIN_WORK_S, MAX_REC_MED_S = 3, 480, 480
HARD_SHARE = 0.25             # effort mode only in hard sessions: >= 25 % of the HR time >= Z4 floor (the ski_hard rule)
SIM_TOL = 0.25                # >= 3 reps within +-25 % of their median duration
REC_DROP_MED = 20             # bpm: median HR drop over the recoveries
MIN_REC_S = 60                # s: hard blocks closer than this are one rep (a short stop inside a climb)
END_FRAC = 0.5                # first / last reps shorter than half the median rep are fragments (warm-up ramp, finish)
DEFAULT_Z4 = 165
LAP_TIME, LAP_MANUAL = 1, 0   # FIT lap_trigger enum
INT_ACTIVE, INT_REST, INT_RECOVERY = 0, 1, 4


def hr_hard_floor(fit: Path | None = None) -> float:
    """Z4 floor from data/<person>/profile/heart_rate_zones.json (person = folder of the FIT file)"""
    try:
        D = Path(fit).resolve().parents[2]
        z = json.loads((D / "profile" / "heart_rate_zones.json").read_text())
        z = [x for x in z if x.get("sport") in ("DEFAULT", None)][0] if isinstance(z, list) else z
        return float(z["zone4Floor"])
    except Exception:
        return float(DEFAULT_Z4)


def _sm(x, k):
    if len(x) < k:
        return x.copy()
    return np.convolve(np.pad(x, (k // 2, k - 1 - k // 2), mode="edge"), np.ones(k) / k, mode="valid")


def effort(G):
    """per-second work rate P (m/s vertical-equivalent, >= 0) and grade"""
    T = G["T"]
    v = G["v"]
    gr = np.zeros(T + 1)
    if "alt" in G and T > 2 * GRADE_W:
        k = GRADE_W
        a, d = G["alt"], G["d"]
        dd = d[2 * k:] - d[:-2 * k]; da = a[2 * k:] - a[:-2 * k]
        gr[k:-k] = np.clip(np.where(dd >= 15, da / np.maximum(dd, 1), 0), -0.3, 0.3)
    P = np.clip(v * (CRR + gr) + CD * v ** 3, 0, None)
    P[G["stopped"]] = 0
    return P, gr


def _hr_stats(hr, a, b):
    if hr is None:
        return {"hr": None, "maxhr": None, "hr_start": None, "hr_end": None}
    h = hr[a:b]
    ok = ~np.isnan(h)
    g = lambda i: None if i < 0 or i >= len(hr) or np.isnan(hr[i]) else int(round(hr[i]))
    return {"hr": int(round(float(h[ok].mean()))) if ok.sum() > 5 else None,
            "maxhr": int(np.nanmax(h)) if ok.any() else None, "hr_start": g(a), "hr_end": g(b)}


def _rep_row(G, a, b, src="detected"):
    d = float(G["d"][min(b, G["T"])] - G["d"][a])
    t = float(b - a)
    row = {"start": float(a), "end": float(b), "time": t, "dist": round(d, 1),
           "speed": round(d / t, 3) if t > 0 and d > 0 else None, **_hr_stats(G.get("hr"), a, b),
           "dist_source": src}
    if "up" in G:
        row["climb_m"] = round(float(G["up"][min(b, G["T"])] - G["up"][a]))
    return row


def _break_row(G, a, b):
    d = float(G["d"][min(b, G["T"])] - G["d"][a])
    v = G["v"][a:b + 1]
    row = {"start": float(a), "end": float(b), "time": float(b - a), "dist": round(d, 1),
           "standing": int(np.sum(v < sg.STOP_V)), "hr_drop": None}
    hr = G.get("hr")
    if hr is not None:
        w0 = hr[max(0, a - 10): a + 10]; w1 = hr[a:b + 1]
        if np.isfinite(w0).any() and np.isfinite(w1).any():
            row["hr_drop"] = int(round(float(np.nanmax(w0) - np.nanmin(w1))))
    return row


# ---------------------------------------------------------------- laps mode
def lap_workout(G, laps):
    """reps from an alternating block of time-triggered (or active/rest) laps; None if there is none"""
    if not laps:
        return None
    step = [(l["trigger"] == LAP_TIME or l.get("intensity") in (INT_REST, INT_RECOVERY)) and l["dur"] >= 10
            for l in laps]
    runs, cur = [], []
    for k, ok in enumerate(step + [False]):
        if ok:
            cur.append(k)
        else:
            if len(cur) >= 3:
                runs.append(cur)
            cur = []
    if not runs or max(map(len, runs)) < 5:
        return None
    best = max(runs, key=len)
    L = [laps[k] for k in best]
    hr = G.get("hr")
    # work phase = laps marked active vs rest, else the longer phase, else the one where HR rises
    def phase_score(p):
        ph = L[p::2]
        marked = sum(1 for l in ph if l.get("intensity") in (INT_REST, INT_RECOVERY))
        return -marked, float(np.median([l["dur"] for l in ph]))
    s0, s1 = phase_score(0), phase_score(1)
    if s0[0] != s1[0]:
        p = 0 if s0[0] > s1[0] else 1
    elif max(s0[1], s1[1]) >= 1.1 * min(s0[1], s1[1]):
        p = 0 if s0[1] > s1[1] else 1
    elif hr is not None:
        rise = []
        for q in (0, 1):
            r = [hr[min(G["T"], int(l["t"] + l["dur"]))] - hr[min(G["T"], int(l["t"]))] for l in L[q::2]]
            rise.append(np.nanmean(r) if np.isfinite(r).any() else 0)
        p = 0 if rise[0] >= rise[1] else 1
    else:
        p = 0
    work = L[p::2]
    wdur = float(np.median([l["dur"] for l in work]))
    for r in runs:   # other blocks of the same workout (split by a manual lap): laps of the work duration
        if r is not best:
            work += [laps[k] for k in r if abs(laps[k]["dur"] / wdur - 1) <= 0.1]
    work.sort(key=lambda l: l["t"])
    wd = [l["dur"] for l in work]
    if len(work) < MIN_REPS or np.std(wd) > 0.25 * np.mean(wd):
        # durations must repeat (pyramids etc. still count if within +-25 %)
        groups = [[work[0]]]
        for l in work[1:]:
            if abs(math.log(l["dur"] / groups[-1][-1]["dur"])) < 0.15:
                groups[-1].append(l)
            else:
                groups.append([l])
        if max(len(g) for g in groups) < MIN_REPS:
            return None
    reps = []
    for l in work:
        a = int(round(l["t"])); b = int(round(l["t"] + l["dur"]))
        b = min(b, G["T"])
        if b - a >= 10:
            r = _rep_row(G, a, b, "lap")
            r["time"] = round(l["dur"], 1)
            r["dist"] = round(l["dist"], 1) if l["dist"] else r["dist"]
            r["speed"] = round(r["dist"] / r["time"], 3) if r["dist"] and r["time"] else None
            reps.append(r)
    return reps if len(reps) >= MIN_REPS else None


# ---------------------------------------------------------------- effort mode
def effort_blocks(G):
    """work blocks [(a, b)] between recoveries (standing, or low work rate for >= 30 s)"""
    P, gr = effort(G)
    Ps = _sm(P, P_SMOOTH)
    work = Ps[(~G["stopped"]) & (Ps > 0.05)]
    if len(work) < 120:
        return [], P, gr, 0.0
    lvl = float(np.median(work))
    rec = G["stopped"] | (Ps < REC_REL * lvl)
    rec_runs = [(a, b) for a, b in sg.runs_of(rec) if b - a >= REC_MIN_S]
    blocks, cur = [], 0
    for a, b in rec_runs:
        if a - cur >= MIN_BLOCK_S:
            blocks.append((cur, a))
        cur = b
    if G["T"] + 1 - cur >= MIN_BLOCK_S:
        blocks.append((cur, G["T"]))
    return blocks, P, gr, lvl


def hard_share(G, hr_hard):
    hr = G.get("hr")
    if hr is None or not np.isfinite(hr).any():
        return None
    h = hr[np.isfinite(hr)]
    return float(np.mean(h >= hr_hard))


def effort_reps(G, hr_hard):
    blocks, P, gr, lvl = effort_blocks(G)
    hr = G.get("hr")
    info = []
    for a, b in blocks:
        n = b - a
        late = None
        if hr is not None:
            h = hr[a + 2 * n // 3: b]
            late = float(np.nanmean(h)) if np.isfinite(h).sum() >= 10 else None
        info.append({"a": a, "b": b, "late": late, "hard": late is not None and late >= hr_hard,
                     "P": round(float(P[a:b].mean()), 3)})
    hs = hard_share(G, hr_hard)
    if hr is None or hs is None or hs < HARD_SHARE:
        return [], info, lvl
    lates = sorted((x["late"] for x in info if x["late"] is not None), reverse=True)
    if not lates:
        return [], info, lvl
    thr = max(hr_hard, float(np.median(lates[:3])) - HR_TOP_MARGIN)
    for x in info:
        x["hard"] = x["late"] is not None and x["late"] >= thr
    # the reps are one contiguous series of hard blocks (short non-hard connecting blocks are skipped);
    # take the series with the most work time
    series, cur = [], []
    for x in info:
        if x["hard"]:
            cur.append(dict(x))
        elif x["b"] - x["a"] >= SHORT_BLOCK_S:
            if cur:
                series.append(cur)
            cur = []
    if cur:
        series.append(cur)
    if not series:
        return [], info, lvl
    hard = max(series, key=lambda c: sum(x["b"] - x["a"] for x in c))
    # merge hard blocks whose recovery has no real HR drop (one continuous effort with a short stop)
    merged = []
    for x in hard:
        if merged:
            y = merged[-1]
            w = hr[y["b"]: x["a"] + 1]
            peak = hr[max(0, y["b"] - 10): y["b"] + 10]
            drop = (np.nanmax(peak) - np.nanmin(w)) if np.isfinite(w).any() and np.isfinite(peak).any() else 0
            if (drop < HR_DROP and x["a"] - y["b"] < 180) or x["a"] - y["b"] < MIN_REC_S:
                y["b"] = x["b"]
                continue
        merged.append(x)
    med = float(np.median([x["b"] - x["a"] for x in merged]))
    while merged and merged[-1]["b"] - merged[-1]["a"] < END_FRAC * med:
        merged.pop()
    while merged and merged[0]["b"] - merged[0]["a"] < END_FRAC * med:
        merged.pop(0)
    reps = [x for x in merged if x["b"] - x["a"] <= MAX_REP_S]
    if len(reps) < len(merged) or len(reps) < MIN_REPS:
        return [], info, lvl
    dur = np.array([x["b"] - x["a"] for x in reps], float)
    if np.sum(np.abs(np.log(dur / np.median(dur))) <= SIM_TOL) < MIN_REPS:
        return [], info, lvl
    recs = [y["a"] - x["b"] for x, y in zip(reps, reps[1:])]
    drops = []
    for x, y in zip(reps, reps[1:]):
        pk, w = hr[max(0, x["b"] - 10): x["b"] + 10], hr[x["b"]: y["a"] + 1]
        if np.isfinite(pk).any() and np.isfinite(w).any():
            drops.append(float(np.nanmax(pk) - np.nanmin(w)))
    if (dur.sum() < MIN_WORK_S or (recs and float(np.median(recs)) > MAX_REC_MED_S)
            or (drops and float(np.median(drops)) < REC_DROP_MED)):
        return [], info, lvl
    return [_rep_row(G, x["a"], x["b"]) for x in reps], info, lvl


# ---------------------------------------------------------------- entry points
def analyse(fit: Path, hr_hard: float | None = None, G: dict | None = None, s: dict | None = None) -> dict:
    """fit: FIT path; G / s: the session_groups grid / series if already read (s carries the laps)"""
    fit = Path(fit)
    if hr_hard is None:
        hr_hard = hr_hard_floor(fit)
    if s is None:
        s = sg.read_series(fit)
    if G is None:
        G = sg.to_grid(s)
    laps = s.get("laps") or []
    out = {"version": VERSION, "threshold": hr_hard, "laps": [(l["intensity"], l["trigger"]) for l in laps]}
    reps = lap_workout(G, laps)
    if reps:
        out["mode"] = "ski_laps"
    else:
        reps, info, lvl = effort_reps(G, hr_hard)
        out.update(mode="ski_effort", work_level=round(lvl, 3),
                   blocks=[[x["a"], x["b"] - x["a"], x["late"] and round(x["late"]), x["P"]] for x in info])
    out["reps"] = reps or []
    out["breaks"] = [_break_row(G, int(x["end"]), int(y["start"])) for x, y in zip(out["reps"], out["reps"][1:])
                     if y["start"] > x["end"]]
    return out


def compact(x: dict) -> dict:
    """session_groups cache format (as session_groups.ana_compact)"""
    reps = x["reps"]
    return {"mode": x["mode"], "threshold": x["threshold"], "version": x["version"],
            "rep_times": [r["time"] for r in reps], "rep_dists": [r["dist"] for r in reps],
            "rep_speeds": [r["speed"] for r in reps], "rep_hr": [r["hr"] for r in reps],
            "rep_starts": [r["start"] for r in reps], "rep_climbs": [r.get("climb_m") for r in reps],
            "breaks": [(b["time"], b["dist"], b["standing"]) for b in x["breaks"]], "laps": x["laps"]}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="print ski reps of FIT files / activity ids")
    ap.add_argument("person")
    ap.add_argument("ids", nargs="+")
    a = ap.parse_args()
    D = sg.ROOT / "data" / a.person
    fits = {p.stem: p for p in (D / "activities").glob("*/*.fit")}
    for i in a.ids:
        x = analyse(fits[i])
        print(i, x["mode"], f"{len(x['reps'])} reps", [(sg.mmss(r["time"]), r["hr"]) for r in x["reps"]])
        for b in x.get("blocks", []):
            print("   block", sg.mmss(b[0]), sg.mmss(b[1]), "late HR", b[2], "P", b[3])

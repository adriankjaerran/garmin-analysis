"""/api/records (personal records from Garmin's fastest splits), /api/progress (recurring interval workouts and
their avg rep pace over time) and /api/progress/{id} (the progression of one activity's workout).

Records: Garmin stores the fastest stretch of every distance it covers in the activity summary
(`activities_index.json` `fastestSplit_<m>`: 1000, 1609, 5000, 10000, 21098, 42195 when present; seconds). Only
runs with a trusted (GPS) distance count, and a split worth more than the person's best shape + 3 VDOT
(fitness.json personal bests; Daniels' formula) is rejected as a GPS glitch (listed under `rejected`).

Progress: an interval workout's occurrences = the activities with the same rep signature ("6×1000") and sport
(any rest or place, so the rest is shown per occurrence). The value is the app's one avg rep pace
(store.rep_pace: total rep distance / total rep time of the main reps), with the mean rep HR next to it.
Trend at an occurrence = its rep speed vs the median of the (up to) 4 occurrences before it: ↗ more than 1 %
faster, ↘ more than 1 % slower, → in between."""

from __future__ import annotations

import datetime as dt
import json
import statistics

from fastapi import APIRouter, Depends, HTTPException, Query

from ..deps import get_store
from ..store import Store, _clean_sig

router = APIRouter(tags=["progress"])

SPLITS = [(1000, "1k", "1 km"), (1609, "mile", "1 mile"), (5000, "5k", "5 km"), (10000, "10k", "10 km"),
          (21098, "half", "Half marathon"), (42195, "marathon", "Marathon")]
NO_GPS_TYPES = {"treadmill_running", "indoor_running"}
VDOT_MARGIN = 3.0       # a split above best shape + this is a GPS glitch
TREND_N = 4             # occurrences before the current one that the trend compares with
TREND_PCT = 1.0         # ± % of rep speed that counts as "level"


# ------------------------------------------------------------------ records
def _raw_index(store: Store) -> list[dict]:
    p = store.dir / "activities_index.json"
    return store._memoize("raw_index", lambda: [p], lambda: json.loads(p.read_text()))


def _vdot_cap(store: Store) -> float | None:
    from . import fitness as fr
    try:
        doc = fr._doc(store)
    except HTTPException:
        return None
    pbs = [d.get("personal_best") for k, d in doc.get("disciplines", {}).items() if k in ("interval", "long")]
    pbs = [x for x in pbs if x]
    return max(pbs) + VDOT_MARGIN if pbs else None


def build_records(store: Store) -> dict:
    import fitness_model as fm
    A = store.activity_map()
    cap = _vdot_cap(store)
    out = []
    by_act: dict[str, list] = {}
    for dist, key, label in SPLITS:
        f = f"fastestSplit_{dist}"
        ok, rejected = [], []
        for r in _raw_index(store):
            t = r.get(f)
            if not t:
                continue
            aid = str(r["activityId"])
            a = A.get(aid)
            if not a or a["sport"] != "run":
                continue
            row = {"id": aid, "date": a["date"], "time_s": round(float(t), 1), "pace_s_km": round(float(t) / dist * 1000, 1),
                   "name": a.get("name"), "workout_label": a.get("workout_label"), "place_name": a.get("place_name"),
                   "dist_km": a.get("dist_km")}
            try:
                row["vdot"] = round(fm.vdot_from_race(dist, float(t)), 1)
            except Exception:  # noqa: BLE001
                row["vdot"] = None
            why = None
            if a.get("dist_trusted") is not True or a.get("type") in NO_GPS_TYPES:
                why = "no trusted distance (no GPS / indoor)"
            elif (a.get("dist_km") or 0) * 1000 < dist * 0.98:
                why = "longer than the activity"
            elif cap and row["vdot"] and row["vdot"] > cap:
                why = f"faster than your best shape allows (VDOT {row['vdot']:.0f} > {cap:.0f}): GPS glitch"
            if why:
                rejected.append({**row, "reason": why})
            else:
                ok.append(row)
        if not ok:
            continue
        ranked = sorted(ok, key=lambda x: x["time_s"])
        for i, x in enumerate(ranked):
            x["rank"] = i + 1
        history, best = [], None     # chronological: every split that beat all earlier ones
        for x in sorted(ok, key=lambda x: (x["date"], x["time_s"])):
            if best is None or x["time_s"] < best["time_s"]:
                history.append({**x, "improved_s": round(best["time_s"] - x["time_s"], 1) if best else None})
                best = x
        for x in ranked[:3]:
            by_act.setdefault(x["id"], []).append({"key": key, "label": label, "rank": x["rank"], "of": len(ranked),
                                                   "time_s": x["time_s"]})
        out.append({"key": key, "label": label, "dist_m": dist, "n": len(ok), "best": ranked[0], "top": ranked[:5],
                    "history": history[::-1], "rejected": sorted(rejected, key=lambda x: x["time_s"])[:10],
                    "n_rejected": len(rejected)})
    return {"records": out, "by_activity": by_act, "vdot_cap": round(cap, 1) if cap else None,
            "definition": "Garmin's fastest split of each distance within a run (activities_index fastestSplit_*); "
                          "runs with a trusted GPS distance only; splits above your best shape + "
                          f"{VDOT_MARGIN:.0f} VDOT are rejected as GPS glitches"}


@router.get("/records")
def records(store: Store = Depends(get_store)):
    """Personal records per distance (1k, mile, 5k, 10k, half, marathon where Garmin has splits): `best`, `top`
    (5 fastest with rank), `history` (each new PR, newest first, `improved_s` vs the previous PR), `rejected`
    (untrusted / implausible splits with the reason); `by_activity` = {id: [{key, rank, of}]} for top-3 splits."""
    acts = store.activities()
    fit = store.dir / "fitness.json"
    key = (id(acts), fit.stat().st_mtime_ns if fit.exists() else 0)
    with store._lock:
        hit = store._memo.get("records")
    if hit and hit[0] == key:
        return hit[1]
    out = build_records(store)
    with store._lock:
        store._memo["records"] = (key, out)
    return out


# ------------------------------------------------------------------ workout progression
def _groups(store: Store) -> dict[tuple, list[dict]]:
    """(sport, signature) -> occurrences oldest first: {id, date, speed, hr, rest_s, place_name, label}"""
    acts = store.activities()
    with store._lock:
        hit = store._memo.get("progress_groups")
    if hit and hit[0] is acts:
        return hit[1]
    ix = store.index()
    X = ix.acts if ix else {}
    G: dict[tuple, list[dict]] = {}
    for a in acts:
        if not a.get("rep_pace_ms"):
            continue
        w = (X.get(int(a["id"])) or {}).get("workout") or {}
        sig = w.get("signature")
        if not _clean_sig(sig):
            continue
        G.setdefault((a["sport"], sig), []).append({
            "id": a["id"], "date": a["date"], "speed": a["rep_pace_ms"], "hr": a.get("rep_hr"), "rest_s": w.get("rest_s"),
            "place_name": a.get("place_name"), "label": a.get("workout_label"), "together": bool(a.get("together"))})
    for v in G.values():
        v.sort(key=lambda x: x["date"])
    with store._lock:
        store._memo["progress_groups"] = (acts, G)
    return G


def _trend(pts: list[dict], i: int) -> dict | None:
    prev = [p["speed"] for p in pts[max(0, i - TREND_N):i]]
    if not prev:
        return None
    med = statistics.median(prev)
    pct = (pts[i]["speed"] / med - 1) * 100
    return {"vs_median_pct": round(pct, 1), "n_prev": len(prev), "median_speed": round(med, 3),
            "arrow": "↗" if pct > TREND_PCT else "↘" if pct < -TREND_PCT else "→",
            "word": "faster" if pct > TREND_PCT else "slower" if pct < -TREND_PCT else "level"}


def _summary(sport: str, sig: str, pts: list[dict], at: int | None = None) -> dict:
    i = len(pts) - 1 if at is None else at
    upto = pts[: i + 1]
    best = max(upto, key=lambda p: p["speed"])
    best_all = max(pts, key=lambda p: p["speed"])
    rank = 1 + sum(p["speed"] > pts[i]["speed"] for p in pts)
    return {"key": f"{sport}:{sig}", "sport": sport, "signature": sig, "n": len(pts), "first": pts[0]["date"], "last": pts[-1]["date"],
            "current": pts[i], "best_before": best if best["id"] != pts[i]["id"] else None, "best": best_all,
            "is_best": best_all["id"] == pts[i]["id"], "rank": rank, "trend": _trend(pts, i), "points": pts}


@router.get("/progress")
def progress(min_n: int = Query(3, ge=2), days: int = Query(120, ge=1, le=36500, description="last occurrence within this many days of the newest activity"),
             limit: int = Query(8, ge=1, le=100), store: Store = Depends(get_store)):
    """Recurring interval workouts (same sport + rep signature, >= min_n occurrences, the last one within `days`),
    newest first. Each: key, signature, n, first, last, current (= latest occurrence {id, date, speed (avg rep pace,
    m/s), hr, rest_s}), best (fastest ever), best_before (fastest before the latest; null if the latest is the best),
    is_best, rank (of n), trend ({arrow ↗/→/↘, vs_median_pct, n_prev}: latest vs the median of the 4 before), points."""
    acts = store.activities()
    newest = dt.date.fromisoformat(acts[0]["date"]) if acts else dt.date.today()
    lo = (newest - dt.timedelta(days=days)).isoformat()
    rows = [_summary(sp, sig, pts) for (sp, sig), pts in _groups(store).items() if len(pts) >= min_n and pts[-1]["date"] >= lo]
    rows.sort(key=lambda r: (r["last"], r["n"]), reverse=True)
    return {"n": len(rows), "workouts": rows[:limit], "definition": __doc__.split("Progress: ", 1)[1].strip()}


@router.get("/progress/{aid}")
def progress_for(aid: str, store: Store = Depends(get_store)):
    """The progression of this activity's workout (same sport + rep signature): {id, workout} with `workout` as in
    /api/progress but at this occurrence (current = this one, trend vs the 4 before it, best_before = fastest
    earlier one, rank among all); `workout` is null when the activity has no clean rep signature or is the only one."""
    a = store.activity_map().get(aid)
    if not a:
        raise HTTPException(404, "no such activity")
    for (sp, sig), pts in _groups(store).items():
        for i, p in enumerate(pts):
            if p["id"] == aid:
                return {"id": aid, "workout": _summary(sp, sig, pts, i) if len(pts) >= 2 else None}
    return {"id": aid, "workout": None}

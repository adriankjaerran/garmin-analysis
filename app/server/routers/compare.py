"""/api/compare (scripts/compare.py: ghost race, moving / elapsed, rep- and break-aligned intervals) and
/api/compare/suggest (comparison sets for one activity from the similarity index)."""

from __future__ import annotations

import threading
from collections import OrderedDict

from fastapi import APIRouter, Depends, HTTPException, Query

from ..deps import get_store
from ..store import Store, track_points

router = APIRouter(tags=["compare"])

# compare.py keeps an in-process cache (sessions, rep detections, projections) that isn't thread-safe:
# FastAPI runs sync endpoints in a threadpool, so every engine call goes through this lock. Rep detection
# itself runs in compare.py's process pool (cold sessions only; results are cached on disk).
_LOCK = threading.Lock()
_RESULTS: OrderedDict[tuple, dict] = OrderedDict()   # small response memo (params + groups.json mtime)
MAX_IDS = 40
MODES = ("distance", "route_position", "moving", "elapsed", "intervals", "breaks")


def _tracks(store: Store, ids: list[int], max_points: int = 1500) -> dict:
    """lat/lon per elapsed second (thinned) per session, so the map can place each session at the cursor"""
    out = {}
    for i in ids:
        s = store.series(str(i))
        if not s:
            continue
        tp = track_points(s[0], max_points)
        if tp:
            out[str(i)] = {"t": tp["t"], "lat": tp["lat"], "lon": tp["lon"]}
    return out


@router.get("/compare")
def compare(ids: str = Query(..., description="comma-separated activity ids; the first is the reference unless reference=median"),
            mode: str = Query("distance", description="distance (= route_position, ghost race) | moving | elapsed | intervals | breaks"),
            reference: str = Query("first", pattern="^(first|median)$"),
            axis: str = Query("auto", pattern="^(auto|route|own)$", description="distance mode x axis"),
            rep_axis: str = Query("absolute", pattern="^(absolute|normalized)$", description="intervals: s since rep start or % of rep"),
            gap_s: float = Query(60, ge=5, le=600, description="intervals: width of a collapsed break"),
            rep_gap_s: float = Query(30, ge=5, le=600, description="breaks: width of a collapsed rep"),
            match: str = Query("auto", pattern="^(auto|index)$"),
            step_m: float | None = Query(None, ge=5, le=1000),
            smooth_s: int | None = Query(None, ge=1, le=120),
            max_points: int = Query(1500, ge=100, le=5000),
            tracks: bool = Query(True, description="include lat/lon per session for the map"),
            store: Store = Depends(get_store)):
    import compare as cmp

    if mode not in MODES:
        raise HTTPException(422, f"mode must be one of {MODES}")
    try:
        id_list = list(dict.fromkeys(int(x) for x in ids.replace(" ", ",").split(",") if x.strip()))
    except ValueError:
        raise HTTPException(422, "ids must be integers")
    if not id_list:
        raise HTTPException(422, "no ids")
    if len(id_list) > MAX_IDS:
        raise HTTPException(422, f"at most {MAX_IDS} sessions")
    gp = store.dir / "groups.json"
    key = (tuple(id_list), mode, reference, axis, rep_axis, gap_s, rep_gap_s, match, step_m, smooth_s, max_points,
           gp.stat().st_mtime_ns if gp.exists() else 0)
    with _LOCK:
        r = _RESULTS.get(key)
        if r is None:
            try:
                r = cmp.compare(id_list, mode, person=store.person, reference=reference, max_points=max_points,
                                smooth_s=smooth_s, axis=axis, rep_axis=rep_axis, gap_s=gap_s, rep_gap_s=rep_gap_s,
                                step_m=step_m, match=match, workers=4)
            except (ValueError, KeyError) as e:
                raise HTTPException(422, str(e).strip("'\""))
            _RESULTS[key] = r
            while len(_RESULTS) > 12:
                _RESULTS.popitem(last=False)
        else:
            _RESULTS.move_to_end(key)
    A = store.activity_map()
    out = dict(r)
    # explicit HR fields: the engine's hr_avg is the mean over moving seconds only (stops excluded); the activity
    # header / lists show Garmin's whole-activity average (hr_avg_activity)
    out["sessions"] = [s | {"hr_avg_moving": s.get("hr_avg"), "hr_avg_activity": (A.get(str(s["id"])) or {}).get("hr")}
                       for s in r["sessions"]]
    out["hr_avg_definition"] = "hr_avg = hr_avg_moving: mean HR over moving seconds (pauses excluded); hr_avg_activity: whole activity (Garmin)"
    out["activities"] = {str(i): A.get(str(i)) for i in r["ids"]}
    if tracks:
        out["tracks"] = _tracks(store, r["ids"])
    return out


def _ids_before(rows: list[dict], aid: int, date: str, n: int) -> list[int]:
    """this activity first, then the n-1 most recent others on or before its date (topped up with later ones)"""
    rows = sorted((a for a in rows if a["id"] != aid), key=lambda a: a["date"], reverse=True)
    before = [a["id"] for a in rows if a["date"] <= date]
    after = [a["id"] for a in reversed(rows) if a["date"] > date]
    return [aid] + (before + after)[: n - 1]


@router.get("/compare/suggest")
def suggest(id: int, n: int = Query(8, ge=2, le=MAX_IDS), store: Store = Depends(get_store)):
    """Comparison sets for an activity, best first. Each set: key, label, n_total (sessions in the group),
    ids (this activity first, then the most recent others on or before its date), mode (suggested view).
    Sets: exact workout (same reps and rest, or same route and effort for ski / runs), signature (same reps,
    any rest or place), route (all members, up to MAX_IDS), nearest (similarity index, workout or balanced)."""
    import similarity

    with _LOCK:
        ix = similarity.load_index(store.person)
    q = ix.acts.get(id)
    if not q:
        raise HTTPException(404, "activity not in the similarity index (no FIT / not grouped)")
    w = q["workout"]
    reps = bool(w.get("rep_count")) and "intervals" in (w.get("family") or "")
    rt = ix.routes.get(q.get("route")) if q.get("route") else None
    route_ok = bool(rt) and q.get("shape") != "laps"
    # sessions without distance (stopwatch, no-GPS lap workouts) only work in the HR / time modes of the engine
    no_dist = (q.get("dist_km") or 0) < 0.1
    has_fit = str(id) in store.folders() and any(store.folders()[str(id)].glob("*.fit"))
    comparable, why_not = True, None
    if not has_fit:
        comparable, why_not = False, "no FIT file for this activity"
    elif no_dist and not q.get("avg_hr"):
        comparable, why_not = False, "no distance and no heart rate: nothing to compare"
    if no_dist:
        mode = "intervals" if reps else "elapsed"
    else:
        mode = "intervals" if reps else ("distance" if route_ok else "moving")
    import compare as cmp
    allowed = list(cmp.NO_DIST_MODES) if no_dist else [m for m in MODES if m != "route_position"]
    sets = []

    def usable(rows):   # candidates the engine can read (FIT present; HR if there's no distance)
        F = store.folders()
        return [a for a in rows if a["id"] == id or (str(a["id"]) in F and (not no_dist or a.get("avg_hr")))]

    def add(key, label, rows, nn=n, m=mode, why=""):
        rows = usable(rows)
        if len(rows) < 2 or not comparable:
            return
        ids = _ids_before(rows, id, q["date"], nn)
        if any(s["ids"] == ids for s in sets):
            return
        sets.append({"key": key, "label": label, "n_total": len(rows), "ids": [str(i) for i in ids], "mode": m, "why": why})

    if w.get("exact_key"):
        add("exact", f"Same workout: {w['exact_key']}", ix.search({"exact_key": w["exact_key"], "sport": q["sport"]}),
            why="same exact workout key (reps + rest, or route + effort)")
    if reps and w.get("signature"):
        if q.get("place"):
            add("sig_place", f"{w['signature']} at {q.get('place_name') or q['place']}", ix.search({"signature": w["signature"], "place": q["place"], "place_mode": "main"}),
                why="same rep signature, same place, any rest")
        add("sig", f"Any {w['signature']}", ix.search({"signature": w["signature"], "sport": q["sport"]}), why="same rep signature")
    if q.get("route"):
        rows = [a for a in ix.acts.values() if a.get("route") == q["route"]]
        add("route", f"Route {q['route']}" + (f" {rt.get('name')}" if rt and rt.get("name") else ""), rows,
            nn=MAX_IDS if route_ok else n, m="distance" if route_ok and not reps else mode,
            why="every session on the same route" if route_ok else "same laps route")
    near = ix.nearest(id, k=3 * n, weights="workout" if reps else "balanced") if comparable else []
    near = usable([ix.acts[r["id"]] for r in near])[: n - 1]
    if near:
        ids = [id] + [r["id"] for r in near]
        sets.append({"key": "nearest", "label": "Most similar", "n_total": len(near) + 1, "ids": [str(i) for i in ids],
                     "mode": mode, "why": "similarity index, " + ("workout" if reps else "balanced") + " weights"})
    A = store.activity_map()
    return {"id": str(id), "date": q["date"], "mode": mode, "modes": allowed, "no_distance": no_dist,
            "comparable": comparable, "why_not": why_not, "sets": sets,
            "activities": {str(i): A.get(str(i)) for s in sets for i in s["ids"]}}

"""/api/activities, /api/activities/{id}, /api/activities/{id}/track"""

from __future__ import annotations

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query

from ..deps import get_store
from ..store import Store, resample, track_points
from ..fitcache import sg

router = APIRouter(tags=["activities"])


def filter_activities(acts: list[dict], sport: str | None = None, type: str | None = None,
                      date_from: str | None = None, date_to: str | None = None, q: str | None = None,
                      place: str | None = None, route: str | None = None) -> list[dict]:
    sports = set(sport.split(",")) if sport else None
    types = set(type.split(",")) if type else None
    ql = q.lower().strip() if q else None
    out = []
    for a in acts:
        if sports and a["sport"] not in sports:
            continue
        if types and a["type"] not in types:
            continue
        if date_from and a["date"] < date_from:
            continue
        if date_to and a["date"] > date_to:
            continue
        if place and place not in (a["place"], a.get("place_name")):   # main place, id or display name
            continue
        if route and a["route"] != route:
            continue
        if ql:
            hay = " ".join(str(a.get(k) or "") for k in ("name", "type", "type_label", "stype", "type_detail", "sig",
                                                         "workout_label", "workout_detail", "place", "place_name", "start_place_name",
                                                         "region", "route", "route_name", "location", "date", "id")).lower()
            if not all(w in hay for w in ql.split()):
                continue
        out.append(a)
    return out


@router.get("/activities")
def list_activities(sport: str | None = Query(None, description="family: run,ski,hike,bike,swim,other (comma list)"),
                    type: str | None = Query(None, description="Garmin typeKey (comma list)"),
                    date_from: str | None = Query(None, alias="from"), date_to: str | None = Query(None, alias="to"),
                    q: str | None = None,
                    place: str | None = Query(None, description="main place (id or display name)"), route: str | None = None,
                    store: Store = Depends(get_store)):
    """Row: Garmin summary (dist_km, dur_s, hr = whole-activity average HR, ...) + place (main place id),
    place_name, start_place(_name), region, route, route_name, type_label, workout_label (the one workout name),
    workout_detail, rep_pace_ms (avg rep pace, m/s), together, swim (pool swims)."""
    if place:
        ix = store.index()
        place = ix.place_id(place) if ix else place
    rows = filter_activities(store.activities(), sport, type, date_from, date_to, q, place, route)
    return {"n": len(rows), "activities": rows}


def _rows(fields: list[str], rows: list | None) -> list[dict]:
    return [dict(zip(fields, r)) for r in rows or []]


def _indoor_reps(ind: dict, arr: dict) -> tuple[list[dict], list[dict]]:
    """Bislett indoor (groups.json `indoor_bislett`): reps = manual run laps, whole laps n x 546.5 m - 7.5 m;
    breaks between them (standing rests, no distance: the watch distance is made up indoors)."""
    t, hr = arr["t"].astype(float), arr.get("hr")
    def hr_in(a, b):
        if hr is None:
            return None, None
        h = hr[(t >= a) & (t < b)].astype(float)
        h = h[np.isfinite(h) & (h > 0)]
        return (int(round(h.mean())), int(h.max())) if len(h) > 5 else (None, None)
    def hr_at(x):
        if hr is None or not len(t):
            return None
        i = int(np.clip(np.searchsorted(t, x), 0, len(t) - 1))
        return int(hr[i]) if np.isfinite(hr[i]) and hr[i] > 0 else None
    reps, breaks = [], []
    rl = [(t0, dur, m) for (t0, dur, _), m in zip(ind.get("rep_laps") or [], ind.get("rep_m") or [])]
    for j, (t0, dur, m) in enumerate(rl):
        h, hm = hr_in(t0, t0 + dur)
        reps.append({"start": round(t0, 2), "end": round(t0 + dur, 2), "time": round(dur, 2), "dist": m,
                     "speed": round(m / dur, 3) if m else None, "hr": h, "maxhr": hm})
        if j + 1 < len(rl):
            a, b = t0 + dur, rl[j + 1][0]
            h, hm = hr_in(a, b)
            peak = max((x for x in (hr_at(a + k) for k in range(-5, 6)) if x), default=None)
            end_hr = hr_at(min(a + 60, b))
            breaks.append({"start": round(a, 2), "end": round(b, 2), "time": round(b - a, 2), "dist": None, "speed": None,
                           "hr": h, "maxhr": hm, "hr_peak": peak,
                           "hr_drop": peak - end_hr if peak and end_hr else None, "standing": 1})
    return reps, breaks


def _neighbours(store: Store, aid: str) -> dict:
    acts = store.activities()  # newest first
    ids = [a["id"] for a in acts]
    i = ids.index(aid)
    return {"newer": ids[i - 1] if i > 0 else None, "older": ids[i + 1] if i + 1 < len(ids) else None}


@router.get("/activities/{aid}")
def get_activity(aid: str, max_points: int = Query(3600, ge=100, le=20000), store: Store = Depends(get_store)):
    a = store.activity_map().get(aid)
    if not a:
        raise HTTPException(404, "no such activity")
    g = store.groups()
    ga = g["activities"].get(aid) or {}
    F = g.get("fields", {})
    out = {"activity": a, "group": {k: v for k, v in ga.items() if k not in ("legs", "pauses", "reps", "similar")},
           "legs": _rows(F.get("legs", []), ga.get("legs")),
           "pauses": _rows(F.get("pauses", []), ga.get("pauses")),
           "similar": ga.get("similar"),
           "route": None, "laps": [], "reps": [], "breaks": [], "series": None, "swim": None,
           **_neighbours(store, aid)}
    if a.get("route") and a["route"] in g["routes"]:
        rt = g["routes"][a["route"]]
        from .places import route_rankings
        out["route"] = {"id": a["route"], "name": a.get("route_name") or rt.get("name"), "n": rt.get("n"), "shape": rt.get("shape"),
                        # time on the route and its rank (places.route_rankings): "3rd fastest of 19", null for laps routes
                        "rank": route_rankings(store).get(a["route"], {}).get(aid)}
    s = store.series(aid)
    if s:
        arr, meta = s
        cols, data = resample(arr, max_points)
        out["series"] = {"cols": cols, **data, "start_utc": meta.get("start_utc")}
        out["laps"] = meta.get("laps") or []
        out["reps"] = meta.get("reps") or []
        if ga.get("type") == "intervals_track" or meta.get("mode") == "track":   # track: reps are the set distance
            out["reps"] = sg.snap_track_reps(out["reps"])
        out["breaks"] = meta.get("breaks") or []
        if ga.get("indoor_bislett"):   # watch distance is made up indoors: reps from the whole-lap manual laps
            out["reps"], out["breaks"] = _indoor_reps(ga["indoor_bislett"], arr)
        if meta.get("swim"):   # pool swim: lengths (start s, time s, strokes), pace per 100 m, swimming vs rest time
            out["swim"] = meta["swim"]
    elif ga.get("reps"):  # no FIT: durations only
        out["reps"] = [dict(zip(F.get("reps", []), r)) for r in ga["reps"]]
    return out


@router.get("/activities/{aid}/track")
def get_track(aid: str, format: str = Query("geojson", pattern="^(geojson|columns)$"),
              max_points: int = Query(4000, ge=50, le=50000), store: Store = Depends(get_store)):
    """GPS track with per-point t (s), speed (m/s, 5 s smoothed), hr, alt for colouring.
    geojson: Feature(LineString) with the per-point arrays in properties; columns: plain arrays."""
    s = store.series(aid)
    if not s:
        raise HTTPException(404, "no FIT for this activity")
    tp = track_points(s[0], max_points)
    if not tp:
        raise HTTPException(404, "activity has no GPS")
    if format == "columns":
        return {"id": aid, **tp}
    coords = [[lo, la] for la, lo in zip(tp["lat"], tp["lon"])]
    return {"type": "Feature", "id": aid, "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {"id": aid, "t": tp["t"], "speed": tp["speed"], "hr": tp["hr"], "alt": tp["alt"]}}

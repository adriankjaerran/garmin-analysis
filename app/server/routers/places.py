"""/api/places, /api/routes, /api/routes/{id} (from groups.json, scripts/session_groups.py)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_store
from ..store import Store

router = APIRouter(tags=["places"])


@router.get("/places")
def places(store: Store = Depends(get_store)):
    g = store.groups()
    acts = store.activities()
    last: dict[str, str] = {}
    for a in acts:  # newest first
        if a["place"] and a["place"] not in last:
            last[a["place"]] = a["date"]
    area_of = {p: aid for aid, ar in g.get("areas", {}).items() for p in ar.get("places", [])}
    n_routes: dict[str, int] = {}
    for r in g.get("routes", {}).values():
        n_routes[r.get("place")] = n_routes.get(r.get("place"), 0) + 1
    out = [{"id": pid, "name": p.get("name", pid), "lat": p["lat"], "lon": p["lon"], "n": p.get("n", 0),
            "types": p.get("types", {}), "area": area_of.get(pid), "routes": n_routes.get(pid, 0),
            "last": last.get(pid)} for pid, p in g.get("places", {}).items()]
    out.sort(key=lambda x: -x["n"])
    areas = [{"id": k, "name": v.get("name"), "lat": v.get("lat"), "lon": v.get("lon"), "n": v.get("n"),
              "places": v.get("places", [])} for k, v in g.get("areas", {}).items()]
    return {"places": out, "areas": areas}


def _route_row(rid: str, r: dict, A: dict) -> dict:
    dates = sorted(A[str(m)]["date"] for m in r.get("members", []) if str(m) in A)
    return {"id": rid, "name": r.get("name"), "place": r.get("place"), "sport": r.get("sport"),
            "shape": r.get("shape"), "dist_km": r.get("dist_km"), "n": r.get("n"),
            "first": dates[0] if dates else None, "last": dates[-1] if dates else None,
            "has_legs": bool(r.get("legs_table"))}


def _rkey(rid: str):
    return (0, int(rid[1:])) if rid[1:].isdigit() else (1, rid)


@router.get("/routes")
def routes(place: str | None = None, sport: str | None = None, store: Store = Depends(get_store)):
    A = store.activity_map()
    rs = store.groups().get("routes", {})
    out = [_route_row(k, r, A) for k, r in sorted(rs.items(), key=lambda kv: _rkey(kv[0]))
           if (not place or r.get("place") == place) and (not sport or r.get("sport") == sport)]
    return {"n": len(out), "routes": out}


@router.get("/routes/{rid}")
def route(rid: str, store: Store = Depends(get_store)):
    g = store.groups()
    r = g.get("routes", {}).get(rid)
    if not r:
        raise HTTPException(404, "no such route")
    A = store.activity_map()
    tracks = store.tracks()
    ga = g["activities"]
    members = []
    for m in r.get("members", []):
        a = A.get(str(m))
        if not a:
            continue
        x = ga.get(str(m), {})
        members.append({**a, "route_dir": x.get("route_dir"), "pause_s": x.get("pause_s"),
                        "poly": (tracks.get(str(m)) or {}).get("poly_lo")})
    members.sort(key=lambda a: a["start"], reverse=True)
    return {**_route_row(rid, r, A), "medoid": r.get("medoid"), "ref": r.get("ref"),
            "waypoints": r.get("waypoints"), "legs_table": r.get("legs_table"), "legs_mode": r.get("legs_mode"),
            "members": members}

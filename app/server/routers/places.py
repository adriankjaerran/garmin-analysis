"""/api/places, /api/routes, /api/routes/{id}.

One place model for the whole app (the similarity index, scripts/similarity.py, built on groups.json):
- place id = stable key (filters, URLs); `name` = display name ("Bislet" -> "Bislett", user overrides).
- an activity's place = its **main place**: the named place where it spends >= 40 % of the time, else its
  start cluster. `n` / `n_main` count main-place activities, `n_touched` every activity that starts, ends or
  spends >= 5 % / 3 min there (what `/api/search?place=` returns), `n_start` the start-cluster members.
- route names and start / end / main places come from the index too (groups.json only adds the leg tables).
"""

from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_store
from ..store import Store

router = APIRouter(tags=["places"])


def _ix(store: Store):
    ix = store.index()
    if ix is None:
        raise HTTPException(503, "similarity index missing: run scripts/session_groups.py and scripts/similarity.py")
    return ix


@router.get("/places")
def places(touched: int = 0, store: Store = Depends(get_store)):
    """Places with >= 1 main-place activity (touched=1: also places only passed through / started at).
    Row: id, name, lat, lon, n (= n_main), n_main, n_touched, n_start, kind, src (named | start), types
    (Garmin types of the main-place activities), area, routes (routes whose main place it is), last."""
    ix = _ix(store)
    g = store.groups()
    acts = store.activities()   # newest first; `place` = main place id
    last: dict[str, str] = {}
    types: dict[str, Counter] = {}
    areas: dict[str, Counter] = {}
    for a in acts:
        p = a["place"]
        if not p:
            continue
        last.setdefault(p, a["date"])
        types.setdefault(p, Counter())[a["type"]] += 1
        if a.get("area"):
            areas.setdefault(p, Counter())[a["area"]] += 1
    n_routes = Counter(r.get("place") for r in ix.routes.values())
    out = []
    for pid, p in ix.places.items():
        if p.get("lat") is None or not (p.get("n_main") or (touched and p.get("n"))):
            continue
        out.append({"id": pid, "name": p.get("name") or pid, "lat": p["lat"], "lon": p["lon"],
                    "n": p.get("n_main", 0), "n_main": p.get("n_main", 0), "n_touched": p.get("n", 0),
                    "n_start": p.get("n_start", 0), "kind": p.get("kind"), "src": p.get("src"),
                    "name_src": p.get("name_src"), "types": dict(types.get(pid, Counter()).most_common()),
                    "area": areas[pid].most_common(1)[0][0] if pid in areas else None,
                    "routes": n_routes.get(pid, 0), "last": last.get(pid)})
    out.sort(key=lambda x: (-x["n"], -x["n_touched"], x["id"]))
    area_rows = [{"id": k, "name": v.get("name"), "lat": v.get("lat"), "lon": v.get("lon"), "n": v.get("n"),
                  "places": v.get("places", [])} for k, v in g.get("areas", {}).items()]
    return {"n": len(out), "places": out, "areas": area_rows,
            "definition": "n = activities whose main place this is (>= 40 % of the time inside a named place, "
                          "else the start cluster); n_touched = starts, ends or spends >= 5 % / 3 min there"}


def _route_time(s: dict) -> tuple[float | None, str]:
    """time on the route of one legs_table session: the moving time over the route's legs (start line to finish,
    lead-in / cool-down outside the route excluded); the session's moving time when a leg is missing"""
    legs = s.get("legs") or []
    if legs and all(l and l.get("moving_s") is not None for l in legs):
        return float(sum(l["moving_s"] for l in legs)), "legs"
    return (float(s["moving"]) if s.get("moving") else None), "moving"


def route_rankings(store: Store) -> dict[str, dict[str, dict]]:
    """{route id: {activity id: {route_time_s, time_src, rank, of, best_s, best_id, delta_s, is_pb, dir, group}}}.
    Ranked by time on the route (fastest = rank 1). Point-to-point routes rank each direction on its own (up a hill
    is not down it); laps routes (track / indoor: the distance varies) aren't ranked. Routes without a legs table
    (2-session routes) rank by moving time."""
    gp = store.dir / "groups.json"

    def build():
        g = store.groups()
        A = g["activities"]
        out: dict[str, dict[str, dict]] = {}
        for rid, r in g.get("routes", {}).items():
            if r.get("shape") == "laps":
                continue
            sess = (r.get("legs_table") or {}).get("sessions") or []
            rows = []
            if sess:
                for s in sess:
                    t, src = _route_time(s)
                    if t:
                        rows.append((str(s["id"]), s.get("date"), t, src, s.get("dir")))
            else:
                for m in r.get("members", []):
                    a = A.get(str(m)) or {}
                    if a.get("moving_s"):
                        rows.append((str(m), a.get("date"), float(a["moving_s"]), "moving", a.get("route_dir")))
            by_grp: dict[str, list] = {}
            for x in rows:
                by_grp.setdefault((x[4] or "fwd") if r.get("shape") == "point_to_point" else "all", []).append(x)
            res = {}
            for grp, xs in by_grp.items():
                xs.sort(key=lambda x: x[2])
                best = xs[0]
                for i, (aid, date, t, src, d) in enumerate(xs):
                    res[aid] = {"route_time_s": round(t), "time_src": src, "rank": i + 1, "of": len(xs), "best_s": round(best[2]),
                                "best_id": best[0], "best_date": best[1], "delta_s": round(t - best[2]), "is_pb": i == 0,
                                "dir": d, "group": grp}
            out[rid] = res
        return out
    return store._memoize("route_rankings", lambda: [gp], build)


def _route_row(rid: str, r: dict, gr: dict, A: dict) -> dict:
    dates = sorted(A[str(m)]["date"] for m in r.get("members", []) if str(m) in A)
    return {"id": rid, "name": r.get("name") or gr.get("name"),
            "place": r.get("place"), "place_name": r.get("place_name"),
            "start_place": r.get("start_place"), "start_place_name": r.get("start_place_name"),
            "end_place": r.get("end_place"), "end_place_name": r.get("end_place_name"),
            "via": r.get("via") or [], "via_names": r.get("via_names") or [],
            "region": r.get("region"), "sport": r.get("sport"), "shape": r.get("shape"), "dist_km": r.get("dist_km"),
            "climb_m": r.get("climb_m"), "is_climb": r.get("is_climb"), "n": r.get("n"),
            "first": dates[0] if dates else None, "last": dates[-1] if dates else None,
            "has_legs": bool(gr.get("legs_table"))}


def _rkey(rid: str):
    return (0, int(rid[1:])) if rid[1:].isdigit() else (1, rid)


@router.get("/routes")
def routes(place: str | None = None, sport: str | None = None, store: Store = Depends(get_store)):
    """Routes with >= 2 sessions. `place` (id or name) matches the route's main place or its start place.
    `pb` = fastest time on the route {id, route_time_s, best_date, of, group} (null for laps routes)."""
    ix = _ix(store)
    A = store.activity_map()
    G = store.groups().get("routes", {})
    pid = ix.place_id(place) if place else None
    RK = route_rankings(store)
    out = [_route_row(k, r, G.get(k, {}), A) for k, r in sorted(ix.routes.items(), key=lambda kv: _rkey(kv[0]))
           if (not pid or pid in (r.get("place"), r.get("start_place"))) and (not sport or r.get("sport") == sport)]
    for row in out:   # fastest time on the route (main direction: the group with most sessions)
        rk = RK.get(row["id"]) or {}
        pb = [dict(v, id=k) for k, v in rk.items() if v["is_pb"]]
        pb = max(pb, key=lambda v: v["of"]) if pb else None
        row["pb"] = {k: pb[k] for k in ("id", "route_time_s", "best_date", "of", "group")} if pb else None
    return {"n": len(out), "routes": out}


@router.get("/routes/{rid}")
def route(rid: str, store: Store = Depends(get_store)):
    ix = _ix(store)
    g = store.groups()
    gr = g.get("routes", {}).get(rid)
    r = ix.routes.get(rid)
    if not r or not gr:
        raise HTTPException(404, "no such route")
    A = store.activity_map()
    tracks = store.tracks()
    ga = g["activities"]
    rk = route_rankings(store).get(rid, {})
    members = []
    for m in r.get("members", []):
        a = A.get(str(m))
        if not a:
            continue
        x = ga.get(str(m), {})
        members.append({**a, "route_dir": x.get("route_dir"), "pause_s": x.get("pause_s"),
                        "poly": (tracks.get(str(m)) or {}).get("poly_lo"), **(rk.get(str(m)) or {})})
    members.sort(key=lambda a: a["start"], reverse=True)
    # the PB per ranking group (both directions of a point-to-point route) and the time trend (oldest first)
    pbs = [{**v, "id": k} for k, v in rk.items() if v["is_pb"]]
    trend = sorted(({"id": k, "date": (A.get(k) or {}).get("date"), "route_time_s": v["route_time_s"], "group": v["group"]}
                    for k, v in rk.items()), key=lambda x: x["date"] or "")
    return {**_route_row(rid, r, gr, A), "medoid": gr.get("medoid"), "ref": gr.get("ref"),
            "waypoints": gr.get("waypoints"), "legs_table": gr.get("legs_table"), "legs_mode": gr.get("legs_mode"),
            "ranked": bool(rk), "pbs": pbs, "trend": trend,
            "rank_definition": "time on route = moving time over the route's legs (start line to finish; lead-in excluded); "
                               "point-to-point routes are ranked per direction, laps routes not at all",
            "members": members}

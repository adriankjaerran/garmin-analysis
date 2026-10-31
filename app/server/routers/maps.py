"""/api/map/tracks: many simplified tracks for the overview map."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..deps import get_store
from ..store import Store
from .activities import filter_activities

router = APIRouter(tags=["map"])


@router.get("/map/tracks")
def map_tracks(bbox: str | None = Query(None, description="west,south,east,north"),
               sport: str | None = None, type: str | None = None,
               date_from: str | None = Query(None, alias="from"), date_to: str | None = Query(None, alias="to"),
               q: str | None = None,
               detail: str = Query("lo", pattern="^(lo|hi)$", description="lo = 20 m tolerance, hi = 4 m"),
               store: Store = Depends(get_store)):
    box = None
    if bbox:
        try:
            box = [float(x) for x in bbox.split(",")]
            assert len(box) == 4
        except (ValueError, AssertionError):
            raise HTTPException(400, "bbox = west,south,east,north")
    tracks = store.tracks()
    key = "poly_lo" if detail == "lo" else "poly"
    out = []
    for a in filter_activities(store.activities(), sport, type, date_from, date_to, q):
        t = tracks.get(a["id"])
        if not t:
            continue
        if box:
            w, s, e, n = t["bbox"]
            if e < box[0] or w > box[2] or n < box[1] or s > box[3]:
                continue
        out.append({"id": a["id"], "date": a["date"], "sport": a["sport"], "type": a["type"], "type_label": a.get("type_label"), "name": a["name"],
                    "km": a["dist_km"], "bbox": t["bbox"], "poly": t[key]})
    return {"n": len(out), "tracks": out}

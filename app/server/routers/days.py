"""/api/days/{date}, /api/calendar"""

from __future__ import annotations

import collections
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query

from ..deps import get_store
from ..store import Store, volume_km

router = APIRouter(tags=["days"])


@router.get("/calendar")
def calendar(date_from: str | None = Query(None, alias="from"), date_to: str | None = Query(None, alias="to"),
             sport: str | None = None, store: Store = Depends(get_store)):
    """Per day with activities: n, km (trusted distances only), seconds, per-sport counts (for a heat calendar)."""
    sports = set(sport.split(",")) if sport else None
    days: dict[str, dict] = {}
    for a in store.activities():
        if (date_from and a["date"] < date_from) or (date_to and a["date"] > date_to):
            continue
        if sports and a["sport"] not in sports:
            continue
        d = days.setdefault(a["date"], {"date": a["date"], "n": 0, "km": 0.0, "s": 0, "sports": collections.Counter()})
        d["n"] += 1
        d["km"] += volume_km(a)   # trusted distances only
        d["s"] += a["dur_s"] or 0
        d["sports"][a["sport"]] += 1
    out = []
    for k in sorted(days):
        d = days[k]
        d["km"] = round(d["km"], 1)
        d["sports"] = dict(d["sports"])
        out.append(d)
    return {"days": out}


@router.get("/days/{date}")
def day(date: str, store: Store = Depends(get_store)):
    try:
        dt.date.fromisoformat(date)
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")
    acts = sorted((a for a in store.activities() if a["date"] == date), key=lambda a: a["start"])
    tracks = store.tracks()
    active = sorted({a["date"] for a in store.activities()})
    prev_active = next((d for d in reversed(active) if d < date), None)
    next_active = next((d for d in active if d > date), None)
    return {
        "date": date,
        "activities": acts,
        "tracks": [{"id": a["id"], "sport": a["sport"], "poly": tracks[a["id"]]["poly"],
                    "bbox": tracks[a["id"]]["bbox"]} for a in acts if a["id"] in tracks],
        "summary": store.daily().get(date),
        "sleep": store.sleep().get(date),
        "hr": store.intraday_hr(date),
        "prev_active": prev_active, "next_active": next_active,
    }

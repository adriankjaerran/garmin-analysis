"""/api/home: everything the Home page needs in one response — the latest active day, a 5-week training log,
volume per sport (last 7 / 30 days vs typical), recent workouts (search rows with workout fields), the last 30 days'
tracks and the current shape per discipline. Built from the same sources as the other endpoints (store activities,
similarity index rows, tracks.json, fitness.json); memoised per anchor date and data version."""

from __future__ import annotations

import datetime as dt
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query

from ..deps import get_store
from ..store import SPORTS, Store, volume_km
from . import days as days_router
from . import fitness as fitness_router
from .search import app_fields
from .search import row as search_row

router = APIRouter(tags=["home"])

WEEKS = 52          # rolling 7-day buckets for the sparklines
LOG_WEEKS = 5       # Monday-aligned weeks in the training log
N_RECENT = 15       # recent workouts at least (more when the last 30 days have more)


def _d(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def _agg(acts) -> dict:
    """n, km (trusted distances only: store.volume_km), s (moving), climb; n_untrusted = activities whose
    distance isn't trusted (indoor / no GPS: their time counts, their km don't)"""
    n = km = s = climb = nu = 0
    for a in acts:
        n += 1
        km += volume_km(a)
        nu += a.get("dist_trusted") is False
        s += a["moving_s"] or a["dur_s"] or 0
        climb += a["elev"] or 0
    return {"n": n, "km": round(km, 1), "s": int(s), "climb": int(climb), "n_untrusted": int(nu)}


def _scale(x: dict, f: float) -> dict:
    return {k: round(v * f, 1) for k, v in x.items()}


def _fitness(store: Store) -> dict | None:
    try:
        doc = fitness_router._doc(store)
    except HTTPException:
        return None
    out = {}
    for k in (*fitness_router.DISC, "overall"):
        d = doc["disciplines"].get(k)
        if not d:
            continue
        c = d["current"]
        out[k] = {"label": d.get("label"), "unit": d.get("unit"), "mean": c.get("mean"), "sd": c.get("sd"),
                  "confidence": c.get("confidence"), "pct_of_best": c.get("pct_of_best"), "trend_90d": c.get("trend_90d"),
                  "race_equivalents_s": c.get("race_equivalents_s"), "last_evidence": c.get("last_evidence"),
                  "date": c.get("date"),
                  # weekly means of the last ~26 weeks for a sparkline
                  "weekly": [w["mean"] for w in d.get("weekly", [])[-26:]]}
    return out


def build(store: Store, anchor: dt.date) -> dict:
    acts = store.activities()  # newest first
    tracks = store.tracks()
    ix = store.index()
    upto = [a for a in acts if _d(a["date"]) <= anchor]
    latest = upto[0]["date"] if upto else None

    # ---- volume per sport: last 7 / 30 days, typical = previous 4 weeks (per week) / previous 12 months (per 30 days)
    by_sport: dict[str, list] = defaultdict(list)
    for a in upto:
        if (anchor - _d(a["date"])).days < 30 + 365:
            by_sport[a["sport"]].append(a)

    def win(lst, lo, hi):   # days back: lo <= age < hi
        return [a for a in lst if lo <= (anchor - _d(a["date"])).days < hi]

    volume = []
    for sp in [*SPORTS, "all"]:
        lst = [a for x in by_sport.values() for a in x] if sp == "all" else by_sport.get(sp, [])
        if not lst:
            continue
        weekly = [_agg(win(lst, 7 * i, 7 * (i + 1))) for i in range(WEEKS)][::-1]   # oldest first, last = last 7 days
        row = {"sport": sp,
               "week": _agg(win(lst, 0, 7)), "prev4": _scale(_agg(win(lst, 7, 35)), 1 / 4),
               "month": _agg(win(lst, 0, 30)), "year30": _scale(_agg(win(lst, 30, 30 + 365)), 30 / 365),
               "weekly_km": [w["km"] for w in weekly], "weekly_s": [w["s"] for w in weekly]}
        if sp != "all" and not (row["month"]["n"] or row["year30"]["n"] >= 0.25):
            continue   # a sport not done for a year or more: leave it out
        volume.append(row)

    # ---- training log: Monday-aligned weeks ending with the anchor's week
    log_start = anchor - dt.timedelta(days=anchor.weekday() + 7 * (LOG_WEEKS - 1))
    in_log = [a for a in upto if _d(a["date"]) >= log_start]
    days: dict[str, list] = defaultdict(list)
    for a in sorted(in_log, key=lambda a: a["start"]):
        days[a["date"]].append(a["id"])

    # ---- recent workouts: everything in the log window (>= 30 days), at least N_RECENT
    month_start = anchor - dt.timedelta(days=29)
    recent_acts = [a for a in upto if _d(a["date"]) >= min(log_start, month_start)]
    if len(recent_acts) < N_RECENT:
        recent_acts = upto[:N_RECENT]
    X = ix.acts if ix else {}
    rows = []
    for a in recent_acts:
        x = X.get(int(a["id"]))
        r = search_row(x, poly=False) if x else {"id": a["id"], "date": a["date"], "start": a["start"][:16], "name": a["name"],
                                                    "sport": a["sport"], "dist_km": a["dist_km"], "moving_s": a["moving_s"],
                                                    "avg_hr": a["hr"], "climb_m": a["elev"], "place": a["place"],
                                                    "place_name": a["place_name"]}
        # app-row extras the search row lacks
        r.update({"dur_s": a["dur_s"], "type_label": a.get("type_label"), "fit": a["fit"], "track": a["track"],
                  "dist_trusted": a.get("dist_trusted"), "dist_est_km": a.get("dist_est_km"), "dist_note": a.get("dist_note"),
                  "load": a.get("load"), "te": a.get("te"), "swim": a.get("swim")})
        rows.append(r)
    app_fields(store, rows)

    # ---- last 30 days' tracks (20 m polylines)
    map_tracks = [{"id": a["id"], "date": a["date"], "sport": a["sport"], "bbox": tracks[a["id"]]["bbox"],
                   "poly": tracks[a["id"]]["poly_lo"]}
                  for a in upto if _d(a["date"]) >= month_start and a["id"] in tracks]

    latest_day = None
    if latest:
        latest_day = days_router.day(latest, store)
        latest_day.pop("hr", None)
        # the daily summary / sleep of the latest day are often not downloaded yet: fall back to the most recent
        # earlier day (<= 3 days back) and say which day it is
        daily, sleep = store.daily(), store.sleep()
        for key, src in (("summary", daily), ("sleep", sleep)):
            latest_day[key + "_date"] = latest if latest_day.get(key) else None
            if not latest_day.get(key):
                for k in range(1, 4):
                    d = (_d(latest) - dt.timedelta(days=k)).isoformat()
                    if src.get(d):
                        latest_day[key], latest_day[key + "_date"] = src[d], d
                        break

    return {"anchor": anchor.isoformat(), "latest": latest,
            "periods": {"week": [(anchor - dt.timedelta(days=6)).isoformat(), anchor.isoformat()],
                        "month": [month_start.isoformat(), anchor.isoformat()],
                        "log": [log_start.isoformat(), (log_start + dt.timedelta(days=7 * LOG_WEEKS - 1)).isoformat()]},
            "latest_day": latest_day, "volume": volume, "days": dict(days), "recent": rows, "tracks": map_tracks,
            "fitness": _fitness(store)}


@router.get("/home")
def home(date: str | None = Query(None, description="anchor date (default: today, or the last activity if later)"),
         store: Store = Depends(get_store)):
    """Home page payload. Week = the 7 days ending at the anchor, month = the 30 days ending at it; `prev4` = average
    week of the 4 weeks before, `year30` = average 30 days over the 12 months before the month."""
    if date:
        try:
            anchor = dt.date.fromisoformat(date)
        except ValueError:
            raise HTTPException(400, "date must be YYYY-MM-DD")
    else:
        acts = store.activities()
        anchor = max(dt.date.today(), _d(acts[0]["date"])) if acts else dt.date.today()
    acts = store.activities()
    fit_v = None
    try:
        fit_v = id(fitness_router._doc(store))
    except HTTPException:
        pass
    key = (anchor, id(acts), id(store.tracks()), id(store.index()), fit_v, id(store.daily()), id(store.sleep()))
    with store._lock:
        hit = store._memo.get("home")
    if hit and hit[0] == key:
        return hit[1]
    out = build(store, anchor)
    with store._lock:
        store._memo["home"] = (key, out)
    return out

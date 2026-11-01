"""/api/together (list + head-to-head), /api/together/{gid} (session), /api/together/{gid}/replay, /api/together/people.

Groups are cross-person (data/together.json from scripts/together.py build), so these endpoints don't use the person
store: they work unprefixed and under /api/<person>/... alike (the prefix only sets `viewer`). The engine
(scripts/together.py on top of scripts/compare.py) isn't thread-safe: calls go through the compare router's lock.
Payloads: docs/together.md#api."""

from __future__ import annotations

from collections import OrderedDict

from fastapi import APIRouter, HTTPException, Query, Request

from .compare import _LOCK

router = APIRouter(tags=["together"])
_RESULTS: OrderedDict[tuple, dict] = OrderedDict()   # response memo (params + together.json mtime); sessions are also cached on disk


def _tg():
    import together  # scripts/ (on sys.path via app/server/__init__.py)
    return together


def _doc():
    tg = _tg()
    try:
        return tg.load()
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))


def _memo(key, build):
    with _LOCK:
        r = _RESULTS.get(key)
        if r is None:
            r = build()
            _RESULTS[key] = r
            while len(_RESULTS) > 24:
                _RESULTS.popitem(last=False)
        else:
            _RESULTS.move_to_end(key)
    return r


def _mtime():
    """together.json + every input the sessions read (groups.json, HR settings, fitness.json, people.json)"""
    return _tg().inputs_sig()


@router.get("/together")
def list_together(request: Request,
                  person: str | None = Query(None, description="comma list: groups with any of these people (default: all)"),
                  sport: str | None = Query(None, description="comma list of sport families: run,hike,bike,ski,swim,other"),
                  kind: str | None = Query(None, description="comma list: intervals,continuous,race,hike,ride,ski,swim,other"),
                  label: str | None = Query(None, description="comma list of labels (same_workout,together,partly_together,"
                                                              "same_race,unverified,same_place_diff_workout)"),
                  near: bool = Query(False, description="also the hidden near-misses (same place, different workout)"),
                  place: str | None = Query(None, description="substring of the group's place name / id"),
                  date_from: str | None = Query(None, alias="from"), date_to: str | None = Query(None, alias="to"),
                  q: str | None = Query(None, description="words: place, workout, label, date, people")):
    """Rows (newest first) with headline + per-person completion, and head-to-head per pair over the listed
    sessions' shared reps."""
    doc = _doc()
    viewer = request.scope.get("person")
    flt = {"person": person, "sport": sport, "kind": kind, "label": label, "near": near, "place": place,
           "date_from": date_from, "date_to": date_to, "q": q}
    key = ("list", tuple(sorted(flt.items())), _mtime())
    r = _memo(key, lambda: _tg().list_payload(doc, viewer=None, **flt))
    return {**r, "viewer": viewer}


@router.get("/together/people")
def together_people():
    """Display info per person: {person: {name, initials, colour, colour_dark, auto}} (data/people.json + automatic)."""
    return {"people": _tg().people_info()}


@router.get("/together/{gid}")
def get_session(gid: str, request: Request):
    """One group's session: group record, participants (+ HR settings, sampling, shape), caveats, headline,
    intervals (slots, reps, standings, leaderboard, cumulative, breaks, rep close-ups) and/or continuous
    (gap over distance, distance apart, who led, km splits, race)."""
    doc = _doc()
    tg = _tg()
    g = tg.find(gid, doc)
    if g is None:
        raise HTTPException(404, f"no together group {gid}")

    def build():
        try:
            return tg.session(g["gid"], doc)
        except (ValueError, KeyError) as e:
            raise HTTPException(422, str(e).strip("'\""))
    r = _memo(("session", g["gid"], _mtime()), build)
    return {**r, "viewer": request.scope.get("person"), "requested_gid": gid}


@router.get("/together/{gid}/replay")
def get_replay(gid: str, step_s: int = Query(2, ge=1, le=10, description="seconds between points"),
               max_points: int = Query(4000, ge=200, le=20000, description="per person")):
    """Positions per participant over the group clock (s since t0_utc), thinned; rep windows for "reps only"."""
    doc = _doc()
    tg = _tg()
    g = tg.find(gid, doc)
    if g is None:
        raise HTTPException(404, f"no together group {gid}")
    return _memo(("replay", g["gid"], step_s, max_points, _mtime()),
                 lambda: tg.replay(g["gid"], step_s=step_s, max_points=max_points, doc=doc))

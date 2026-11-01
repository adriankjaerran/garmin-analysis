"""/api/community/…: the running club on top of Together (scripts/community.py; payloads in docs/community.md).

Like /api/together these are cross-person: they work unprefixed and under /api/<person>/… (the prefix only sets
`viewer`, e.g. for `kudoed` / `mine`). The expensive base (together sessions, form deltas, efforts) is built under
compare's lock (the together engine isn't thread-safe) and memoised by its inputs; community.json edits are cheap
overlays (their own write lock). Gids are validated via together.find (aliases resolve to the current gid)."""

from __future__ import annotations

from fastapi import APIRouter, Body, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse

from .compare import _LOCK

router = APIRouter(tags=["community"])


def _cm():
    import community  # scripts/ (on sys.path via app/server/__init__.py)
    return community


def _tg():
    import together
    return together


def _doc():
    try:
        return _tg().load()
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))


def _gid(gid: str) -> str:
    g = _tg().find(gid, _doc())
    if g is None:
        raise HTTPException(404, f"no together group {gid}")
    return g["gid"]


def _run(name, *a, **k):
    """community.<name>(...) under the engine lock (it may (re)build the base: together sessions, form deltas)"""
    cm = _cm()
    _doc()
    with _LOCK:
        return getattr(cm, name)(*a, **k)


@router.get("/community/sessions")
def list_sessions(request: Request,
                  category: str | None = Query(None, description="comma list: intervals,tempo,long,easy,race,other"),
                  person: str | None = Query(None, description="comma list: sessions with any of these people"),
                  date_from: str | None = Query(None, alias="from"), date_to: str | None = Query(None, alias="to"),
                  q: str | None = Query(None, description="words: place, workout, people, title, note, guests, category"),
                  near: bool = Query(False, description="also together's hidden near-misses"),
                  sport: str | None = None, kind: str | None = None, label: str | None = None,
                  place: str | None = None,
                  hidden: bool = Query(False, description="also the sessions hidden by the user")):
    """Together's list rows (newest first) + community fields (category, title, n_timed, guests, photo, kudos,
    collective numbers, form deltas, records)."""
    return _run("list_sessions", category=category, person=person, date_from=date_from, date_to=date_to, q=q, near=near,
                            sport=sport, kind=kind, label=label, place=place, hidden=hidden,
                            viewer=request.scope.get("person"))


@router.get("/community/sessions/{gid}")
def get_session(gid: str, request: Request):
    """Community layer of one session: meta (title, category, note, guests, photos, kudos), collective numbers, form
    per participant, records set, members strip, neighbours. Standings / splits / replay: /api/together/{gid}."""
    g = _gid(gid)
    r = _run("get_session", g, viewer=request.scope.get("person"))
    return {**r, "requested_gid": gid}


@router.patch("/community/sessions/{gid}")
def patch_session(gid: str, body: dict = Body(..., examples=[{"title": "Bislett 15×400", "category": "intervals",
                                                               "note": "windy", "guests": ["Kari"]}])):
    """Edit title / category (null = automatic) / note / guests / hidden / cover -> meta"""
    g = _gid(gid)
    try:
        _cm().patch(g, body)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return _run("get_session", g)["meta"]


@router.post("/community/sessions/{gid}/photos")
def upload_photo(gid: str, request: Request, file: UploadFile = File(...),
                 by: str | None = Query(None, description="who uploaded it (default: the URL's person)")):
    """multipart `file` (jpg / png / webp / heic ≤ 15 MB) -> stored as a ≤ 1600 px jpg + 480 px thumbnail -> photo"""
    g = _gid(gid)
    cm = _cm()
    data = file.file.read(cm.MAX_PHOTO_BYTES + 1)
    try:
        return cm.add_photo(g, data, filename=file.filename, by=by or request.scope.get("person"))
    except ValueError as e:
        raise HTTPException(413 if "too large" in str(e) else 422, str(e))


@router.delete("/community/sessions/{gid}/photos/{photo_id}")
def delete_photo(gid: str, photo_id: str):
    """remove a photo (files + metadata) -> meta"""
    g = _gid(gid)
    try:
        _cm().delete_photo(g, photo_id)
    except KeyError as e:
        raise HTTPException(404, str(e).strip("'\""))
    return _run("get_session", g)["meta"]


def _kudos(gid, request, body, on):
    g = _gid(gid)
    cm = _cm()
    person = (body or {}).get("person") or request.scope.get("person")
    try:
        ks = cm.kudos(g, person, on=on)
    except ValueError as e:
        raise HTTPException(422, str(e))
    viewer = person
    return {"gid": g, "kudos": ks, "n_kudos": len(ks), "kudoed": viewer in ks}


@router.post("/community/sessions/{gid}/kudos")
def add_kudos(gid: str, request: Request, body: dict | None = Body(None, examples=[{"person": "patrik"}])):
    """give kudos (idempotent; body {person}, default the URL's person) -> {gid, kudos, n_kudos, kudoed}"""
    return _kudos(gid, request, body, True)


@router.delete("/community/sessions/{gid}/kudos")
def remove_kudos(gid: str, request: Request, person: str | None = Query(None)):
    """take kudos back (?person=, default the URL's person) -> {gid, kudos, n_kudos, kudoed}"""
    return _kudos(gid, request, {"person": person}, False)


@router.get("/community/photos/{gid}/{file}", include_in_schema=False)
def photo(gid: str, file: str):
    p = _cm().photo_file(gid, file)
    if p is None:
        raise HTTPException(404, "no such photo")
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=31536000, immutable"})


@router.get("/community/club")
def club(request: Request, date_from: str | None = Query(None, alias="from"), date_to: str | None = Query(None, alias="to")):
    """Club dashboard: totals, months, attendance (streaks, last seen), pairs, categories, records, highlights,
    weekday × hour heatmap, places, guests, recent."""
    return _run("club", date_from=date_from, date_to=date_to, viewer=request.scope.get("person"))


@router.get("/community/members")
def members(request: Request):
    """attendance rows of every member (as in /community/club)"""
    return _run("members", viewer=request.scope.get("person"))


@router.get("/community/members/{person}")
def member(person: str, request: Request):
    """member profile: totals, sessions, form series, partners, best sessions, personal bests, shape trend"""
    try:
        return _run("member", person, viewer=request.scope.get("person"))
    except KeyError:
        raise HTTPException(404, f"no member {person}")


@router.get("/community/wrapped/{year}")
def wrapped(year: str, request: Request):
    """the yearly recap (Wrapped): totals vs the year before, top place, favourite category, biggest day, best form
    jump, most loyal, closest finish, records, badges earned, a slide per member. `latest` = the newest year
    with a club session"""
    if year == "latest":
        r = _run("wrapped", "latest", viewer=request.scope.get("person"))
    elif year.isdigit() and len(year) == 4:
        r = _run("wrapped", year, viewer=request.scope.get("person"))
    else:
        raise HTTPException(422, "year: YYYY or latest")
    if not r["totals"]["sessions"]:
        raise HTTPException(404, f"no club sessions in {year} (years: {', '.join(r['years']) or 'none'})")
    return r


@router.get("/community/badges")
def badges(request: Request):
    """every member's badges (earned with date + session, locked with progress) and the newest earned ones"""
    def f():
        cm = _cm()
        tdoc = cm.tg.load()
        B = cm.base(tdoc)
        M = cm.motivation(B, cm.load_store(), tdoc)
        return {"people": B["people"], "badges": M["badges"], "latest": cm.latest_badges(M["badges"], n=20),
                "definitions": list(cm.BADGE_INFO.values())}
    _doc()
    with _LOCK:
        return f()


@router.get("/community/goals")
def goals():
    """the club goals with progress; `goal` = the primary one (default "12 sessions this <season>" until one is saved)"""
    return _run("goals")


@router.post("/community/goals")
def add_goal(body: dict = Body(..., examples=[{"title": "500 km together in 2026", "metric": "km", "target": 500,
                                               "from": "2026-01-01", "to": "2026-12-31"}])):
    """new goal {title?, metric: sessions|km|hours|reps|climb_m, target, from, to} -> goals payload"""
    try:
        _cm().add_goal(body)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return _run("goals")


@router.patch("/community/goals/{goal_id}")
def patch_goal(goal_id: str, body: dict = Body(..., examples=[{"target": 15}])):
    """edit a goal (id `default` stores the built-in default on first edit) -> goals payload"""
    try:
        _cm().patch_goal(goal_id, body)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except KeyError:
        raise HTTPException(404, f"no goal {goal_id}")
    return _run("goals")


@router.delete("/community/goals/{goal_id}")
def delete_goal(goal_id: str):
    """remove a goal -> goals payload"""
    try:
        _cm().delete_goal(goal_id)
    except KeyError:
        raise HTTPException(404, f"no goal {goal_id}")
    return _run("goals")

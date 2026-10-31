"""/api/search, /api/nearest/{id}, /api/groups, /api/search/options on top of scripts/similarity.py.

The index (data/<p>/similarity_index.json) is loaded by `similarity.load_index`, which caches it in-process
and reloads when the file's mtime changes (and rebuilds it when it is older than groups.json).
Activity ids are strings in the API (like the rest of the app); the index uses ints.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, Request

from ..deps import get_store
from ..store import SPORT_LABEL, Store, family, type_label

router = APIRouter(tags=["search"])

# query parameter -> similarity.Index.search filter key (same name unless listed)
LIST_KEYS = ("atype", "family", "category", "signature", "main_sig", "exact_key", "route", "place", "region", "rest_mode")
RANGE_TOL = ("distance_km", "duration_s", "climb_m", "avg_hr")      # a single number means +- tol
RANGE_INT = ("rep_count", "reps_total")                              # a single number means exactly that
RANGE_STR = ("rep_dist_m", "rep_time_s", "rest_s", "pace", "rep_pace", "rep_len")
SORTS = {"date", "dist_km", "moving_s", "avg_hr", "climb_m", "speed_ms"}


def _ix(store: Store):
    import similarity as sim  # scripts/ is on sys.path (app/server/__init__.py)
    try:
        return sim.load_index(store.person)
    except FileNotFoundError as e:
        raise HTTPException(503, f"similarity index missing: run scripts/session_groups.py and scripts/similarity.py ({e})")


def _f(v):
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _num(v: str) -> float:
    import similarity as sim
    try:
        return sim._num(v)
    except (ValueError, TypeError):
        raise HTTPException(400, f"bad number {v!r}")


def _range(v: str):
    """'5-7' / '5..7' / '120-' / '-200' / '2:00-3:00' -> (lo, hi) with None open; a single value -> None"""
    v = v.strip()
    sep = ".." if ".." in v else ("-" if "-" in v else None)
    if not sep:
        return None
    a, b = v.split(sep, 1)
    return (_num(a) if a.strip() else None, _num(b) if b.strip() else None)


def _bbox(b):  # index [s, w, n, e] -> app [w, s, e, n]
    return [b[1], b[0], b[3], b[2]] if b else None


def row(a: dict, poly: bool = True) -> dict:
    """compact result row (index record -> API)"""
    w = a.get("workout") or {}
    r = {
        "id": str(a["id"]), "date": a["date"], "start": a.get("start"), "name": a.get("name"),
        "sport": family(a.get("atype")), "sport_label": a.get("sport_label"), "isport": a.get("sport"),
        "atype": a.get("atype"), "gps": bool(a.get("gps")),
        "dist_km": a.get("dist_km"), "dist_trusted": a.get("dist_trusted"), "moving_s": a.get("moving_s"),
        "avg_hr": a.get("avg_hr"),   # whole-activity average (Garmin summary), same as the activity header
        "climb_m": a.get("climb_m"), "speed_ms": a.get("speed_ms"),
        # place = main place id, place_name its display name; places = touched place ids, place_names their names
        "place": a.get("place"), "place_name": a.get("place_name"), "place_src": a.get("place_src"),
        "start_place": a.get("start_place"), "start_place_name": a.get("start_place_name"),
        "places": [p["name"] for p in a.get("places", [])],
        "place_names": [p.get("label") or p["name"] for p in a.get("places", [])],
        "region": a.get("region"), "regions": [p["name"] for p in a.get("regions", [])],
        "route": a.get("route"), "route_name": a.get("route_name"),
        "family": w.get("family"), "family_label": w.get("family_label"), "category": w.get("category"),
        "signature": w.get("signature"), "main_sig": w.get("main_sig"), "exact_key": w.get("exact_key"),
        "exact_label": w.get("exact_label"),
        "rep_count": w.get("rep_count"), "reps_total": w.get("reps_total"), "rep_dist_m": w.get("rep_dist_m"),
        "rep_time_s": w.get("rep_time_s"), "rep_speed_ms": w.get("rep_speed_ms"),
        # rest_s = this session's median rest (main part); rest_group_s = median of its exact_key group ("rest ~2:30")
        "rest_s": w.get("rest_s"), "rest_group_s": w.get("rest_group_s"),
        "rest_mode": w.get("rest_mode"), "bbox": _bbox(a.get("bbox")),
    }
    if poly:
        r["poly"] = a.get("poly")
    return r


def _filters(q) -> tuple[dict, dict]:
    """query params -> (similarity filters, app-side filters)"""
    f: dict = {}
    g = lambda k: (q.get(k) or "").strip() or None
    for k in LIST_KEYS:
        v = g(k)
        if v:
            f[k] = [x for x in v.split("|")] if "|" in v else v
    for k in ("place_mode", "region_mode", "part"):
        if g(k):
            f[k] = g(k)
    if g("from"):
        f["date_from"] = g("from")
    if g("to"):
        f["date_to"] = g("to")
    if g("year"):
        f["year"] = [int(y) for y in g("year").split(",")]
    if g("tol"):
        f["tol"] = float(g("tol"))
    for k in RANGE_TOL + RANGE_INT + RANGE_STR:
        v = g(k)
        if not v:
            continue
        if k == "rep_len":
            f[k] = v
            continue
        rg = _range(v)
        if rg is None:  # single value: exact for counts, +- tol otherwise
            x = _num(v)
            if k in RANGE_INT:
                f[k] = [x, x]
            elif k in ("pace", "rep_pace"):  # +- 5 s/km
                f[k] = [x - 5, x + 5]
            else:
                f[k] = x
        else:
            f[k] = list(rg)
    for k in ("gps", "dist_trusted"):
        if g(k) in ("0", "1", "true", "false"):
            f[k] = g(k) in ("1", "true")
    app = {"sport": set(g("sport").split(",")) if g("sport") else None,
           "q": [w for w in (g("q") or "").lower().split() if w]}
    return f, app


def _text(a: dict) -> str:
    w = a.get("workout") or {}
    return " ".join(str(x) for x in (
        a.get("name"), a.get("date"), a.get("id"), a.get("atype"), a.get("sport_label"), a.get("place"), a.get("place_name"),
        a.get("start_place_name"), a.get("region"), a.get("loc"),
        a.get("route"), a.get("route_name"), w.get("family_label"), w.get("signature"), w.get("exact_key"),
        w.get("detail"), *[p.get("label") or p["name"] for p in a.get("places", [])], *[p["name"] for p in a.get("places", [])],
        *[p["name"] for p in a.get("regions", [])]) if x).lower()


def _app_ok(a: dict, app: dict) -> bool:
    if app["sport"] and family(a.get("atype")) not in app["sport"]:
        return False
    if app["q"]:
        t = _text(a)
        return all(w in t for w in app["q"])
    return True


def _run_search(ix, q):
    f, app = _filters(q)
    try:
        res = ix.search(f or None, sort="-date")
    except (ValueError, TypeError) as e:
        raise HTTPException(400, f"bad filter: {e}")
    return [a for a in res if _app_ok(a, app)], f


@router.get("/search")
def search(request: Request, sort: str = "-date", limit: int | None = None, poly: int = 1, store: Store = Depends(get_store)):
    """All `similarity.Index.search` filters as query params (lists: `a|b`; ranges: `lo-hi`, `lo-`, `-hi`;
    rep_len `1000m` / `4:00` / `900-1100` / `3:30-4:30`, +- `tol`), plus `sport` (app families, comma list),
    `q` (words in name, place, region, route, workout), `from`/`to` (dates), `sort` (`-date`, `dist_km`, ...)."""
    ix = _ix(store)
    res, f = _run_search(ix, request.query_params)
    key = sort.lstrip("-")
    if key in SORTS and sort != "-date":
        rev = sort.startswith("-")
        res = sorted(res, key=lambda a: (a.get(key) is None, a.get(key) if a.get(key) is not None else 0), reverse=rev)
        if rev:
            res.sort(key=lambda a: a.get(key) is None)
    n = len(res)
    rows = [row(a, bool(poly)) for a in (res[:limit] if limit else res)]
    return {"n": n, "truncated": bool(limit and n > limit), "filters": f,
            "km": round(sum(a.get("dist_km") or 0 for a in res), 1),
            "families": dict(Counter((a.get("workout") or {}).get("family") for a in res).most_common()),
            "rows": rows}


def _weights(preset: str | None, weights: str | None):
    import similarity as sim
    if preset and preset not in sim.PRESETS:
        raise HTTPException(400, f"unknown preset {preset}; use {list(sim.PRESETS)}")
    if not weights:
        return preset or "balanced"
    W: dict = {"preset": preset} if preset else {}
    for part in weights.split(","):
        if not part.strip():
            continue
        k, _, v = part.partition(":") if ":" in part else part.partition("=")
        if k.strip() not in sim.COMPONENTS:
            raise HTTPException(400, f"unknown component {k}; use {sim.COMPONENTS}")
        W[k.strip()] = float(v)
    return W


RE_REASON = re.compile(r"^(.*) \((\w+) ([\d.]+)\)$")


@router.get("/nearest/{aid}")
def nearest(aid: str, request: Request, k: int = 10, preset: str | None = None, weights: str | None = None,
            same_sport: int = 1, poly: int = 1, store: Store = Depends(get_store)):
    """Top-k closest activities. `preset` balanced|workout|route|place; `weights` = `route:0.5,pace:0.2`
    (overrides of the preset, or only these components without a preset). Search params restrict candidates."""
    import similarity as sim
    ix = _ix(store)
    try:
        qid = int(aid)
    except ValueError:
        raise HTTPException(404, "no such activity")
    if qid not in ix.acts:
        raise HTTPException(404, "activity not in the similarity index (rebuild with scripts/similarity.py)")
    W = _weights(preset, weights)
    f, app = _filters(request.query_params)
    k = max(1, min(k, 200))
    try:
        res = ix.nearest(qid, k=k * 3 if (app["sport"] or app["q"]) else k, weights=W, filters=f or None,
                         same_sport=bool(same_sport), explain=4)
    except ValueError as e:
        raise HTTPException(400, str(e))
    out = []
    for r in res:
        a = ix.acts[r["id"]]
        if not _app_ok(a, app):
            continue
        reasons = []
        for s in r["reasons"]:
            m = RE_REASON.match(s)
            reasons.append({"c": m.group(2), "s": float(m.group(3)), "text": m.group(1)} if m else {"c": None, "s": None, "text": s})
        out.append(row(a, bool(poly)) | {"score": r["score"], "components": r["components"], "reasons": reasons})
        if len(out) >= k:
            break
    used = dict(sim.PRESETS[W]) if isinstance(W, str) else (
        (dict(sim.PRESETS[W["preset"]]) if W.get("preset") else {}) | {c: v for c, v in W.items() if c != "preset"})
    return {"query": row(ix.acts[qid], bool(poly)), "preset": preset or ("custom" if weights else "balanced"),
            "weights": used, "components": sim.COMPONENTS, "presets": list(sim.PRESETS), "results": out}


def _enrich_tree(ix, nodes):
    """workout tree: add first/last/places to every node (from the ids of its leaves)"""
    def ids_of(n):
        return n["ids"] if "ids" in n else [i for c in n.get("children", []) for i in ids_of(c)]
    for n in nodes:
        ids = ids_of(n)
        ds = sorted(ix.acts[i]["date"] for i in ids)
        n["first"], n["last"] = (ds[0], ds[-1]) if ds else (None, None)
        n["places"] = dict(Counter(ix.acts[i].get("place_name") or "no place" for i in ids).most_common(3))
        if "ids" in n:
            n["ids"] = [str(i) for i in n["ids"]]
        if n.get("children"):
            _enrich_tree(ix, n["children"])
    return nodes


@router.get("/groups")
def groups(request: Request, kind: str = "place", touched: int = 0, min_n: int = 1, store: Store = Depends(get_store)):
    """kind: place | start_place | region | route | family | signature | exact | workout (tree family >
    signature > exact). touched=1: place/region groups count every touched one, not just the main one.
    Search params restrict the activities."""
    ix = _ix(store)
    kinds = ("place", "start_place", "region", "route", "family", "signature", "exact", "workout")
    if kind not in kinds:
        raise HTTPException(400, f"kind must be one of {kinds}")
    f, app = _filters(request.query_params)
    if app["sport"] or app["q"]:  # app-side filters: restrict to the matching ids via a temporary index view
        keep = {a["id"] for a in ix.search(f or None) if _app_ok(a, app)}
        f = None
    else:
        keep = None
    try:
        gs = ix.groups(kind, min_n=min_n, touched=bool(touched), filters=f or None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if keep is not None:
        gs = _restrict(ix, kind, gs, keep, min_n)
    if kind == "workout":
        return {"kind": kind, "groups": _enrich_tree(ix, gs)}
    defs = ix.doc.get("params", {})
    for g in gs:
        g["ids"] = [str(i) for i in g["ids"]]
        if "places" in g:
            g["places"] = {k or "no place": v for k, v in g["places"].items()}
        g["sports"] = dict(Counter(family(ix.acts[int(i)].get("atype")) for i in g["ids"]).most_common())
        if kind in ("place", "start_place"):
            p = ix.places.get(g["key"], {})
            g |= {"name": p.get("name") or g["key"], "lat": p.get("lat"), "lon": p.get("lon"), "kind": p.get("kind"),
                  "src": p.get("src"), "n_main": p.get("n_main"), "n_touched": p.get("n"), "n_start": p.get("n_start")}
            shp = defs.get("named_places", {}).get(g["key"])
            if shp:
                g["shape"] = shp
        elif kind == "region":
            p = ix.regions.get(g["key"], {})
            g |= {"kind": p.get("kind"), "src": p.get("src"), "n_main": p.get("n_main"), "n_touched": p.get("n")}
            shp = defs.get("regions", {}).get(g["key"])
            if shp:
                g["shape"] = shp
        elif kind == "route":
            r = ix.routes.get(g["key"], {})
            g |= {k: r.get(k) for k in ("sport", "shape", "dist_km", "start_place", "end_place", "via", "place",
                                        "start_place_name", "end_place_name", "via_names", "place_name",
                                        "region", "climb_m", "is_climb", "poly")}
            g["sport"] = family(ix.acts[int(g["ids"][0])].get("atype")) if g["ids"] else g.get("sport")
    return {"kind": kind, "groups": gs}


def _restrict(ix, kind, gs, keep, min_n):
    if kind == "workout":
        def walk(nodes):
            out = []
            for n in nodes:
                if "ids" in n:
                    n["ids"] = [i for i in n["ids"] if i in keep]
                    n["n"] = len(n["ids"])
                else:
                    n["children"] = walk(n.get("children", []))
                    n["n"] = sum(c["n"] for c in n["children"])
                if n["n"] >= max(1, min_n):
                    out.append(n)
            return out
        return walk(gs)
    out = []
    for g in gs:
        g["ids"] = [i for i in g["ids"] if i in keep]
        g["n"] = len(g["ids"])
        if g["n"] >= max(1, min_n):
            ds = sorted(ix.acts[i]["date"] for i in g["ids"])
            g["first"], g["last"] = ds[0], ds[-1]
            out.append(g)
    return sorted(out, key=lambda g: (-g["n"], g["key"]))


@router.get("/search/options")
def options(store: Store = Depends(get_store)):
    """values for the filter controls: places, regions, routes, families, categories, signatures, exact keys, sports"""
    ix = _ix(store)
    A = ix.acts.values()
    cnt = lambda f: [{"id": k, "n": n} for k, n in Counter(x for a in A for x in f(a) if x).most_common()]
    W = lambda a: a.get("workout") or {}
    fam_label = {W(a)["family"]: W(a).get("family_label") for a in A}
    return {
        "sports": [d | {"label": SPORT_LABEL.get(d["id"], d["id"])} for d in cnt(lambda a: [family(a.get("atype"))])],
        # n = what the place filter returns in its default (touched) mode = n_touched; n_main = with place_mode=main
        "places": [{"id": k, "name": v.get("name") or k, "n": v.get("n"), "n_touched": v.get("n"), "n_main": v.get("n_main"),
                    "n_start": v.get("n_start"), "kind": v.get("kind"), "src": v.get("src")}
                   for k, v in sorted(ix.places.items(), key=lambda kv: (-kv[1].get("n", 0), kv[0])) if v.get("n")],
        "regions": [{"id": k, "n": v.get("n"), "n_main": v.get("n_main"), "kind": v.get("kind"), "src": v.get("src")}
                    for k, v in sorted(ix.regions.items(), key=lambda kv: (-kv[1].get("n", 0), kv[0]))],
        "routes": [{"id": k, "name": r.get("name"), "n": r.get("n"), "sport": r.get("sport")}
                   for k, r in sorted(ix.routes.items(), key=lambda kv: (-kv[1].get("n", 0), kv[0]))],
        "families": [d | {"label": fam_label.get(d["id"])} for d in cnt(lambda a: [W(a).get("family")])],
        "categories": cnt(lambda a: [W(a).get("category")]),
        "signatures": cnt(lambda a: [W(a).get("signature")] if W(a).get("rep_count") else []),
        "exact": cnt(lambda a: [W(a).get("exact_key")] if W(a).get("rep_count") else []),
        "types": [d | {"label": type_label(d["id"])} for d in cnt(lambda a: [a.get("atype")])],
        "rest_modes": cnt(lambda a: [W(a).get("rest_mode")]),
        "presets": list(__import__("similarity").PRESETS),
        "built": ix.doc.get("built"),
    }

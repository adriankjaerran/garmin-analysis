"""/api/fitness — per-discipline shape scores over time from scripts/fitness_model.py
(artifact data/<p>/fitness.json, reloaded when its mtime changes). Frontend: pages/FitnessPage.tsx and the
"Shape" section of pages/ActivityPage.tsx. Model: docs/fitness-model.md."""

from __future__ import annotations

import datetime as dt
import json
import logging
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import ROOT
from ..deps import get_store
from ..store import Store

router = APIRouter(tags=["fitness"])
log = logging.getLogger("garmin-app")

DISC = ("interval", "long", "ski")
RACE_M = {"5k": 5000, "10k": 10000, "half": 21097.5, "marathon": 42195}
_rebuild: dict = {"running": False, "started": None, "finished": None, "error": None, "seconds": None}
_rlock = threading.Lock()


def _fm():
    import fitness_model  # scripts/ (on sys.path via app/server/__init__.py)
    return fitness_model


def _doc(store: Store) -> dict:
    p = store.dir / "fitness.json"
    if not p.exists():
        raise HTTPException(404, "no fitness.json yet: run `uv run scripts/fitness_model.py "
                                 f"{store.person}` or POST /api/fitness/rebuild")
    return store._memoize("fitness", lambda: [p], lambda: _prepare(json.loads(p.read_text())))


# ------------------------------------------------------------------ formatting helpers
def _dur(s) -> str:
    if s is None:
        return "—"
    s = int(round(s))
    h, m, x = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{x:02d}" if h else f"{m}:{x:02d}"


def _pace(v) -> str:
    return _dur(1000 / v) + "/km" if v and v > 0.3 else "—"


def _race_eq(vdot: float) -> dict:
    fm = _fm()
    return {k: round(float(fm.race_time(vdot, d))) for k, d in RACE_M.items()}


# ------------------------------------------------------------------ derived fields
def _trend(d: dict) -> float | None:
    cur = d["current"]
    cut = (dt.date.fromisoformat(cur["date"]) - dt.timedelta(days=90)).isoformat()
    past = [w for w in d["weekly"] if w["date"] <= cut]
    return round(cur["mean"] - past[-1]["mean"], 2) if past else None


def _prep_discs(discs: dict, sessions: list, variant: str = "pace") -> None:
    """trend_90d / last_evidence per discipline; for the "hr" variant kind/weight come from each session's `hr` block."""
    for disc in (*DISC, "overall"):
        d = discs.get(disc)
        if not d:
            continue
        cur = d["current"]
        cur["trend_90d"] = _trend(d)
        if disc == "overall":
            continue
        ev = []
        for s in sessions:
            v = s if variant == "pace" else (s.get("hr") or s)
            if s["discipline"] == disc and v["kind"] in ("race", "hard") and (v.get("weight") or 0) > 0.5:
                ev.append((s, v))
        last, lv = max(ev, key=lambda e: e[0]["date"]) if ev else (None, None)
        cur["last_evidence"] = {"date": last["date"], "id": last["id"], "score": last["score"], "kind": lv["kind"]} if last else None


def _prepare(doc: dict) -> dict:
    """Add trend_90d / last_evidence per discipline (both model variants) and a sessions-by-id index
    (memoised with the file)."""
    _prep_discs(doc["disciplines"], doc["sessions"])
    for name, v in (doc.get("variants") or {}).items():
        if v.get("disciplines"):
            _prep_discs(v["disciplines"], doc["sessions"], name)
    by_id: dict[str, list] = {}
    for s in doc["sessions"]:
        s["explain"] = explain(s)
        by_id.setdefault(str(s["id"]), []).append(s)
    doc["_by_id"] = by_id
    return doc


def _eval_variants(store: Store) -> dict | None:
    """`variants` of data/<p>/fitness_eval.json (scripts/fitness_eval.py): pace vs hr model comparison,
    demoted sessions, HR sensitivity. None if the file (or the key) is missing."""
    p = store.dir / "fitness_eval.json"

    def build():
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text()).get("variants")
        except (OSError, ValueError):
            log.warning("unreadable %s", p)
            return None
    return store._memoize("fitness_eval", lambda: [p], build)


def explain(s: dict) -> str:
    """One line: what was done and what VDOT / race time it is worth."""
    c = s.get("components") or {}
    sc = s["score"]
    k5 = ""
    if s["discipline"] in ("interval", "long") and s["kind"] != "implausible" and 20 < sc < 90:
        k5 = f" ≈ 5k {_dur(_fm().race_time(sc, 5000))}"
    unit = "VDOT-eq." if s["discipline"] == "ski" else "VDOT"
    if "race" in c:
        what = f"{ {'half': 'half marathon', 'marathon': 'marathon'}.get(c['race'], c['race'])} race in {_dur(c.get('time_s'))}"
        if c["race"] == "5k":
            k5 = ""
        if s["discipline"] == "interval" and c["race"] != "5k":
            what += " (counts for interval shape as hard evidence)"
    elif s["discipline"] == "interval":
        rest = c.get("rest_s")
        reps = c.get("sig") or f"{c.get('n_reps', '?')} × {_dur(c.get('rep_s'))}"
        what = f"{reps} @ {_pace(c.get('rep_speed'))}" + (f" with {_dur(rest)} rest" if rest is not None else "")
    elif s["discipline"] == "long":
        what = f"best {round((c.get('window_s') or 0) / 60)} min at {_pace(c.get('gap_speed'))} (grade-adjusted)"
        if s["kind"] == "implausible":
            return f"{what}: ignored, {c.get('implausible', 'faster than the interval shape allows')}"
    else:
        sp = c.get("speed")
        what = (f"best {round((c.get('window_s') or 0) / 60)} min: {c.get('climb_m', 0)} m net climb at "
                f"{sp * 3.6:.1f} km/h, demand {c.get('demand')} ml/kg/min ({c.get('surface', '?').rstrip('?')})"
                if sp else "ski effort")
    tail = {"bound": " (lower bound: only counts if above the shape)"}.get(s["kind"], "")
    return f"{what} ≈ {unit} {sc:.1f}{k5}{tail}"


COMPONENT_LABELS = {
    "sig": ("Session", "rep structure detected in the session"),
    "n_reps": ("Reps", "work bouts used"),
    "rep_s": ("Rep time", "median work-bout duration"),
    "rest_s": ("Rest", "median recovery between reps; recovery restores W′ (shorter rest = harder)"),
    "work_rest": ("Work:rest", "rep time / rest time"),
    "rep_speed": ("Rep pace", "mean pace over the reps"),
    "tau_scale": ("τ scale", "W′ recovery speed vs Skiba (0.35 = faster recovery, fitted on races)"),
    "score_without_grade": ("Score w/o grade", "same session scored without grade adjustment"),
    "work_hrr": ("Work %HRR", "HR during the work, % of heart-rate reserve (diagnostic only)"),
    "race": ("Race", "nominal distance (within 3.5 %)"),
    "time_s": ("Time", "moving time"),
    "window_s": ("Window", "best pause-free window used"),
    "gap_speed": ("GAP pace", "grade-adjusted pace over the window"),
    "hrr": ("%HRR", "mean HR as % of heart-rate reserve (diagnostic only)"),
    "climb_m": ("Net climb", "altitude gained over the window"),
    "speed": ("Speed", "mean moving speed"),
    "demand": ("O₂ demand", "VO2 from mechanical power (η 0.16), ml/kg/min"),
    "surface": ("Surface", "roller (May–Oct) or snow; sets rolling resistance μ"),
    "implausible": ("Rejected", "cross-discipline check against the interval shape"),
}


def _fmt_component(k: str, v):
    if k in ("rep_s", "rest_s", "time_s", "window_s"):
        return _dur(v)
    if k in ("rep_speed", "gap_speed"):
        return _pace(v)
    if k == "speed":
        return f"{v * 3.6:.1f} km/h"
    if k in ("work_hrr", "hrr"):
        return f"{v * 100:.0f} %"
    if k == "climb_m":
        return f"{v} m"
    if k == "surface":
        return str(v).rstrip("?")
    return str(v)


def _shape_on(d: dict, date: str) -> dict | None:
    """Weekly mean ± sd at the week containing `date` (first weekly row on/after it)."""
    for w in d.get("weekly", []):
        if w["date"] >= date:
            return {"date": w["date"], "mean": w["mean"], "sd": w["sd"]}
    return None


def _strip(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if not k.startswith("_")}


# ------------------------------------------------------------------ endpoints
@router.get("/fitness")
def fitness(store: Store = Depends(get_store),
            discipline: str | None = Query(None, description="comma list: interval,long,ski,overall"),
            date_from: str | None = Query(None, alias="from"), date_to: str | None = Query(None, alias="to"),
            hr: bool = Query(False, description="include hr_diagnostics")):
    doc = _doc(store)
    want = set(discipline.split(",")) if discipline else None
    acts = store.activity_map()

    def inr(date: str) -> bool:
        return (not date_from or date >= date_from) and (not date_to or date <= date_to)
    def cut(src: dict) -> dict:
        discs = {}
        for k, d in src.items():
            if want and k not in want:
                continue
            d = dict(d)
            d["weekly"] = [w for w in d["weekly"] if inr(w["date"])]
            if date_from or date_to:
                d["yearly"] = {y: v for y, v in d["yearly"].items()
                               if (not date_from or y >= date_from[:4]) and (not date_to or y <= date_to[:4])}
            discs[k] = d
        return discs
    discs = cut(doc["disciplines"])
    variants = {name: {**{k: x for k, x in v.items() if k != "disciplines"},
                       **({"disciplines": cut(v["disciplines"])} if v.get("disciplines") else {})}
                for name, v in (doc.get("variants") or {}).items()}
    sessions = []
    for s in doc["sessions"]:
        if (want and s["discipline"] not in want) or not inr(s["date"]):
            continue
        a = acts.get(str(s["id"])) or {}
        sessions.append({**s, "id": str(s["id"]), "name": a.get("name"), "dist_km": a.get("dist_km"),
                         "sport": a.get("sport")})
    out = {k: v for k, v in _strip(doc).items() if k not in ("sessions", "hr_diagnostics", "disciplines", "variants")}
    out.update(disciplines=discs, variants=variants, sessions=sessions, eval_variants=_eval_variants(store),
               rebuild=dict(_rebuild))
    if hr:
        out["hr_diagnostics"] = doc["hr_diagnostics"]
    return out


@router.get("/fitness/eval")
def fitness_eval(store: Store = Depends(get_store)):
    """Pace vs with-HR model comparison from fitness_eval.json (`variants`); null if not evaluated yet."""
    return _eval_variants(store)


@router.get("/fitness/session/{aid}")
def fitness_session(aid: str, store: Store = Depends(get_store)):
    """Shape evidence for one activity: scores (a 10k race counts for interval and long), components
    explained, the shape that day, and HR diagnostics (not used in the score). `scored: false` if none."""
    doc = _doc(store)
    items = []
    for s in doc["_by_id"].get(aid, []):
        d = doc["disciplines"].get(s["discipline"], {})
        comps = [{"key": k, "label": COMPONENT_LABELS.get(k, (k, ""))[0], "value": v, "text": _fmt_component(k, v),
                  "meaning": COMPONENT_LABELS.get(k, (k, ""))[1]} for k, v in (s.get("components") or {}).items()]
        sh = _shape_on(d, s["date"])
        hv = ((doc.get("variants") or {}).get("hr") or {}).get("disciplines", {}).get(s["discipline"], {})
        hr_blk = {**s["hr"], "shape": _shape_on(hv, s["date"]), "current": hv.get("current"),
                  "label": ((doc.get("variants") or {}).get("hr") or {}).get("label")} if s.get("hr") else None
        items.append({**s, "id": str(s["id"]), "label": d.get("label", s["discipline"]),
                      "race_equivalents_s": _race_eq(s["score"]) if s["discipline"] != "ski"
                      and s["kind"] != "implausible" and 20 < s["score"] < 90 else None,
                      "components_explained": comps, "shape": sh,
                      "shape_race_equivalents_s": _race_eq(sh["mean"]) if sh and s["discipline"] != "ski" else None,
                      "hr": hr_blk, "personal_best": d.get("personal_best"),
                      "current": d.get("current")})
    hd = doc["hr_diagnostics"].get(aid)
    hr = None
    if hd:
        hr = {**hd, "explained": [
            {"key": "hrr", "label": "Effort (%HRR)", "text": f"{hd['hrr'] * 100:.0f} %" if hd.get("hrr") is not None else "—",
             "meaning": "median HR of steady 3-min chunks as % of heart-rate reserve"},
            {"key": "ef", "label": "Efficiency factor", "text": f"{hd['ef']:.3f}" if hd.get("ef") is not None else "—",
             "meaning": "grade-adjusted m/s per beat above rest (higher = fitter at the same HR)"},
            {"key": "decoupling_pct", "label": "Aerobic decoupling",
             "text": f"{hd['decoupling_pct']:+.1f} %" if hd.get("decoupling_pct") is not None else "—",
             "meaning": "efficiency drop 2nd half vs 1st half (< 5 % = aerobically steady)"},
            {"key": "vdot", "label": "HR-based estimate", "text": f"{hd['vdot']:.1f} ± {hd.get('iqr', 0) / 2:.1f}"
             if hd.get("vdot") is not None else "—",
             "meaning": f"Swain-style VO2max from {hd.get('n_chunks', 0)} chunks; test-retest sd 4.7, not used"},
        ]}
    reason = None if items else _not_scored(store, aid)
    return {"id": aid, "scored": bool(items), "sessions": items, "hr_diagnostics": hr,
            "not_scored": reason, "hr_used_in_score": doc.get("hr_used_in_score", False), "built": doc.get("built")}


def _not_scored(store: Store, aid: str) -> dict:
    """Why an activity has no shape score: {key, text}. Mirrors fitness_model.score_session's rules."""
    ga = store.groups()["activities"].get(aid) or {}
    a = store.activity_map().get(aid) or {}
    sport, atype, t = ga.get("sport"), ga.get("atype") or a.get("type"), ga.get("type") or ""
    lab = a.get("type_label") or atype or "activity"
    if sport not in ("run", "ski"):
        return {"key": "sport", "text": f"Only runs and ski sessions are scored; this is a {lab.lower()}."}
    if not a.get("fit"):
        return {"key": "no_fit", "text": "No FIT file, so there is no pace / HR series to score."}
    if sport == "run" and atype in ("treadmill_running", "indoor_running"):
        return {"key": "indoor", "text": f"{lab}: pace comes from the foot pod / treadmill, not GPS, and isn't trusted "
                                         "for the score (the workout itself counts in the lists and in Compare)."}
    if not ga.get("dist_trusted"):
        return {"key": "distance", "text": "No trusted distance (no GPS track), so pace can't be scored."}
    if sport == "run" and t == "race?":
        return {"key": "race_distance", "text": "Race-like run, but not within 3.5 % of 5k / 10k / half / marathon."}
    if sport == "run" and t.startswith("intervals"):
        return {"key": "no_reps", "text": "Intervals without a usable rep analysis (no sustained work bouts found)."}
    if sport == "run":
        return {"key": "no_effort", "text": f"{t.replace('_', ' ').capitalize() or 'Run'}: no pause-free stretch of "
                                            "≥ 20 min, so it doesn't even give a lower bound."}
    return {"key": "no_effort", "text": "No sustained hard ski effort (climb) long enough to score."}


def _run_rebuild(person: str):
    t0 = time.time()
    try:
        fm = _fm()
        doc = fm.build(person, workers=4, progress=False)
        out = ROOT / "data" / person / "fitness.json"
        tmp = out.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":"), allow_nan=False, default=float))
        tmp.replace(out)
        _rebuild.update(error=None)
    except Exception as e:  # report, don't crash the server
        log.exception("fitness rebuild failed")
        _rebuild.update(error=f"{type(e).__name__}: {e}")
    finally:
        _rebuild.update(running=False, finished=dt.datetime.now().isoformat(timespec="seconds"),
                        seconds=round(time.time() - t0, 1))


@router.post("/fitness/rebuild")
def fitness_rebuild(store: Store = Depends(get_store)):
    """Re-run fitness_model.build in a background thread (≈0.1 s with cached features, ≈10 s cold)."""
    with _rlock:
        if _rebuild["running"]:
            return {**_rebuild, "accepted": False}
        _rebuild.update(running=True, started=dt.datetime.now().isoformat(timespec="seconds"), finished=None, error=None)
    threading.Thread(target=_run_rebuild, args=(store.person,), name="fitness-rebuild", daemon=True).start()
    return {**_rebuild, "accepted": True}


@router.get("/fitness/rebuild")
def fitness_rebuild_status():
    return dict(_rebuild)

"""/api/fitness — per-discipline shape scores over time from scripts/fitness_model.py
(artifact data/<p>/fitness.json, reloaded when its mtime changes). Frontend: pages/FitnessPage.tsx and the
"Shape" section of pages/ActivityPage.tsx. Model: docs/fitness-model.md."""

from __future__ import annotations

import datetime as dt
import json
import logging
import math
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
_REBUILD0 = {"running": False, "started": None, "finished": None, "error": None, "seconds": None}
_rebuilds: dict[str, dict] = {}   # per person


def _rebuild_state(person: str) -> dict:
    return _rebuilds.setdefault(person, dict(_REBUILD0))
_rlock = threading.Lock()


def _fm():
    import fitness_model  # scripts/ (on sys.path via app/server/__init__.py)
    return fitness_model


def _doc(store: Store) -> dict:
    p = store.dir / "fitness.json"
    if not p.exists():
        raise HTTPException(404, "no fitness.json yet: run `uv run scripts/fitness_model.py "
                                 f"{store.person}` or POST /api/fitness/rebuild")
    return store._memoize("fitness", lambda: [p], lambda: _prepare(json.loads(p.read_text()), store))


# ------------------------------------------------------------------ formatting helpers
def _dur(s) -> str:
    if s is None:
        return "—"
    s = int(round(s))
    h, m, x = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{x:02d}" if h else f"{m}:{x:02d}"


def _secs(v) -> str:
    """seconds with a unit: "56 s" below a minute, else m:ss"""
    return f"{round(v)} s" if v is not None and round(v) < 60 else _dur(v)


def _pace(v) -> str:
    return _dur(1000 / v) + " /km" if v and v > 0.3 else "—"


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
        # interval / long are two readings of one distance curve (model_version >= 2): any running item informs both
        same = ("interval", "long") if disc in ("interval", "long") and d.get("model") == "distance_curve" else (disc,)
        ev = []
        for s in sessions:
            v = s if variant == "pace" else (s.get("hr") or s)
            if s["discipline"] in same and v["kind"] in ("race", "hard") and (v.get("weight") or 0) > 0.5:
                ev.append((s, v))
        last, lv = max(ev, key=lambda e: e[0]["date"]) if ev else (None, None)
        cur["last_evidence"] = {"date": last["date"], "id": last["id"], "score": last["score"], "kind": lv["kind"]} if last else None


def _prepare(doc: dict, store: Store | None = None) -> dict:
    """Add trend_90d / last_evidence per discipline (both model variants) and a sessions-by-id index
    (memoised with the file). The one-liners use the app's workout name and avg rep pace (store rows) when known,
    so they read the same as Home / Activities."""
    try:
        A = store.activity_map() if store else {}
    except Exception:  # noqa: BLE001 — labels only
        A = {}
    _prep_discs(doc["disciplines"], doc["sessions"])
    for name, v in (doc.get("variants") or {}).items():
        if v.get("disciplines"):
            _prep_discs(v["disciplines"], doc["sessions"], name)
    by_id: dict[str, list] = {}
    for s in doc["sessions"]:
        s["explain"] = explain(s, A.get(str(s["id"])))
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
            # older files may hold NaN (too few races): null them, a NaN would make the response fail
            return _finite(json.loads(p.read_text()).get("variants"))
        except (OSError, ValueError, AttributeError):
            log.warning("unreadable %s", p)
            return None
    return store._memoize("fitness_eval", lambda: [p], build)


def _finite(x):
    """`x` with NaN / ±inf floats replaced by None, recursively (strict JSON for the response)."""
    if isinstance(x, dict):
        return {k: _finite(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_finite(v) for v in x]
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return x


def explain(s: dict, app: dict | None = None) -> str:
    """One line: what was done and what VDOT / race time it is worth. `app` = the store's activity row: its
    workout name and avg rep pace (total rep distance / total rep time) replace the model's for intervals."""
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
        if s["discipline"] == "interval" and c["race"] != "5k":   # fitness.json model_version 1 only
            what += " (counts for interval shape as hard evidence)"
    elif s["discipline"] == "interval":
        rest = c.get("rest_s")
        app = app or {}
        reps = (app.get("workout_label") if app.get("rep_pace_ms") else None) or c.get("sig") or f"{c.get('n_reps', '?')} × {_dur(c.get('rep_s'))}"
        what = f"{reps} @ {_pace(app.get('rep_pace_ms') or c.get('rep_speed'))}" + (f" with {_secs(rest)} rest" if rest is not None else "")
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
    "rest_s": ("Rest", "median recovery between reps; shorter rest makes the same pace harder, so it scores higher"),
    "work_rest": ("Work:rest", "rep time / rest time"),
    "rep_speed": ("Rep pace (model)", "mean pace over the reps the model used"),
    "tau_scale": ("Recovery speed", "how fast you recover in the rests, relative to the textbook model (Skiba τ; 0.35 = faster, fitted on your races)"),
    "score_without_grade": ("Score on the flat", "the same session scored without adjusting for hills"),
    "work_hrr": ("Effort in the reps", "heart rate during the reps, % of heart-rate reserve (context only)"),
    "race": ("Race", "nominal distance (within 3.5 %)"),
    "time_s": ("Time", "moving time"),
    "window_s": ("Best stretch", "the best pause-free stretch used"),
    "gap_speed": ("Hill-adjusted pace", "pace over that stretch, adjusted for climbs and descents"),
    "hrr": ("Effort", "mean heart rate as % of heart-rate reserve (context only)"),
    "climb_m": ("Net climb", "altitude gained over that stretch"),
    "speed": ("Speed", "mean moving speed"),
    "demand": ("Oxygen demand", "oxygen needed for that power (from climb, rolling and air resistance), ml/kg/min"),
    "surface": ("Surface", "roller skis (May–Oct) or snow; sets the rolling resistance"),
    "implausible": ("Rejected", "faster than your interval shape allows"),
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
    out = {k: v for k, v in _strip(doc).items() if k not in ("sessions", "hr_diagnostics", "disciplines", "variants", "predictor")}
    if doc.get("predictor"):   # daily curve / evidence: GET /fitness/predictor
        out["predictor"] = {k: v for k, v in doc["predictor"].items() if k not in ("daily", "evidence") and not k.startswith("_")}
    out.update(disciplines=discs, variants=variants, sessions=sessions, eval_variants=_eval_variants(store),
               rebuild=dict(_rebuild_state(store.person)))
    if hr:
        out["hr_diagnostics"] = doc["hr_diagnostics"]
    return out


@router.get("/fitness/eval")
def fitness_eval(store: Store = Depends(get_store)):
    """Pace vs with-HR model comparison from fitness_eval.json (`variants`); null if not evaluated yet."""
    return _eval_variants(store)


@router.get("/fitness/session/{aid}")
def fitness_session(aid: str, store: Store = Depends(get_store)):
    """Shape evidence for one activity: scores (model_version 1: a 10k race counted for interval and long), components
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


# ------------------------------------------------------------------ race predictor (distance curve)
PRED_KM = (3.0, 5.0, 10.0, 21.0975, 42.195)


def _predictor(doc: dict) -> dict:
    pr = doc.get("predictor")
    if not pr or not pr.get("daily"):
        raise HTTPException(404, "no race predictor in fitness.json (no running evidence, or built before model_version 2): "
                                 "rebuild with `uv run scripts/fitness_model.py <person>` or POST /api/fitness/rebuild")
    return pr


def _day_index(pr: dict) -> dict:
    """{date: daily row} (memoised on the predictor block)."""
    if "_by_date" not in pr:
        pr["_by_date"] = {r["date"]: r for r in pr["daily"]}
    return pr["_by_date"]


@router.get("/fitness/predictor")
def fitness_predictor(store: Store = Depends(get_store)):
    """Daily race-predictor curve (RTS smoothed) at 5k / 10k / half with the state sd (chart band: no race-day noise,
    no extrapolation) and the evidence that fed the curve (km = its effective distance)."""
    pr = _predictor(_doc(store))
    if "_chart" not in pr:
        rows = []
        xs = {nm: math.log2(km / pr["ref_km"]) for nm, km in (("5k", 5.0), ("10k", 10.0), ("half", 21.0975))}
        for r in pr["daily"]:
            row = {"date": r["date"]}
            for nm, x in xs.items():
                row[f"vdot_{nm}"] = round(r["theta"] + x * r["slope"], 2)
            for nm, x in xs.items():
                row[f"sd_{nm}"] = round(math.sqrt(max(r["var_theta"] + 2 * x * r["cov"] + x * x * r["var_slope"], 0)), 2)
            rows.append(row)
        pr["_chart"] = rows
    return {"ref_km": pr["ref_km"], "evidence_km": pr["evidence_km"], "daily": pr["_chart"],
            "evidence": [{**e, "id": str(e["id"])} for e in pr["evidence"]],
            "last_evidence_date": pr.get("last_evidence_date"), "today": pr["daily"][-1]["date"]}


@router.get("/fitness/predict")
def fitness_predict(store: Store = Depends(get_store),
                    date: str | None = Query(None, description="YYYY-MM-DD (default: the latest day, today at build)"),
                    km: str | None = Query(None, description="comma list of distances in km, 0.8-100 (default 3,5,10,21.0975,42.195)")):
    """Race predictions from the distance curve on one day: VDOT, sd (state ⊕ race-day noise ⊕ extrapolation),
    time / pace and a ±1 sd time range per distance; the curve's shape (theta at 5 km, slope per doubling); and how
    much evidence is behind it."""
    fm = _fm()
    doc = _doc(store)
    pr = _predictor(doc)
    first, last = pr["daily"][0]["date"], pr["daily"][-1]["date"]
    day = date or last
    try:
        dt.date.fromisoformat(day)
    except ValueError:
        raise HTTPException(400, f"date must be YYYY-MM-DD, got {day!r}")
    if day < first:
        raise HTTPException(400, f"{day} is before the first running evidence ({first})")
    if day > last:
        raise HTTPException(400, f"{day} is after the latest day of the model ({last}, the build date)")
    lo, hi = fm.CURVE["km_range"]
    try:
        kms = [float(x) for x in km.split(",") if x.strip()] if km else list(PRED_KM)
    except ValueError:
        raise HTTPException(400, f"km must be a comma list of numbers, got {km!r}")
    if not kms:
        raise HTTPException(400, "km is empty")
    bad = [k for k in kms if not (lo <= k <= hi) or not math.isfinite(k)]
    if bad:
        raise HTTPException(400, f"km must be within {lo:g}-{hi:g}: {bad}")
    r = _day_index(pr)[day]
    race_sd, ext = pr.get("race_sd", fm.CURVE["race_sd"]), pr.get("extrap_sd_per_doubling", fm.CURVE["extrap_sd_per_doubling"])
    preds = []
    for k in kms:
        p = fm.curve_predict(r["theta"], r["slope"], r["var_theta"], r["var_slope"], r["cov"], k, pr["evidence_km"],
                             race_sd=race_sd, extrap_sd=ext)
        t = float(fm.race_time(p["vdot"], k * 1000))
        preds.append({"km": k, "label": fm.dist_label(k), "vdot": round(p["vdot"], 2), "sd": round(p["sd"], 2),
                      "sd_state": round(p["sd_state"], 2), "time_s": round(t),
                      "pace_s_per_km": round(t / k, 1),
                      "range_s": [round(float(fm.race_time(p["vdot"] + p["sd"], k * 1000))),
                                  round(float(fm.race_time(p["vdot"] - p["sd"], k * 1000)))],
                      "extrapolated": bool(p["extrapolated"])})
    d0 = dt.date.fromisoformat(day).toordinal()
    strong = [e for e in pr["evidence"] if e["kind"] in ("race", "hard") and (e.get("weight") or 0) > 0.5 and e["date"] <= day]
    lev = max((e["date"] for e in strong), default=None)
    n90 = sum(1 for e in strong if d0 - dt.date.fromisoformat(e["date"]).toordinal() < 90)
    return {"date": day, "predictions": preds,
            "shape": {"theta": round(r["theta"], 2), "slope": round(r["slope"], 2),
                      "slope_sd": round(math.sqrt(max(r["var_slope"], 0)), 2), "slope_text": fm.slope_text(r["slope"])},
            "basis": {"last_evidence_date": lev,
                      "days_since_evidence": d0 - dt.date.fromisoformat(lev).toordinal() if lev else None,
                      "n_evidence_90d": n90}}


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
    _rebuild = _rebuild_state(person)
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
    _rebuild = _rebuild_state(store.person)
    with _rlock:
        if _rebuild["running"]:
            return {**_rebuild, "accepted": False}
        _rebuild.update(running=True, started=dt.datetime.now().isoformat(timespec="seconds"), finished=None, error=None)
    threading.Thread(target=_run_rebuild, args=(store.person,), name="fitness-rebuild", daemon=True).start()
    return {**_rebuild, "accepted": True}


@router.get("/fitness/rebuild")
def fitness_rebuild_status(store: Store = Depends(get_store)):
    return dict(_rebuild_state(store.person))

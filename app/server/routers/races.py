"""/api/races (per person) and /api/community/races (head-to-head): official race results with the pre-race
prediction. Read-only: the per-race rows (run: predicted time from data strictly before race day, index `pct`) are
precomputed in data/<p>/fitness.json `races` by scripts/fitness_model.py (`official_race_results`); the list of
races and everyone's official times come from data/races.json. Frontend: pages/RacesPage.tsx,
pages/CommunityRacesPage.tsx. Docs: docs/app.md, docs/fitness-model.md."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends

from .. import ROOT
from ..deps import get_store, people, store_for
from ..store import Store

router = APIRouter(tags=["races"])

RACES = ROOT / "data" / "races.json"


def _races_json() -> list[dict]:
    """data/races.json `races`, oldest first ([] without the file)."""
    if not RACES.exists():
        return []
    return sorted(json.loads(RACES.read_text()).get("races", []), key=lambda r: r["date"])


def _fitness_races(store: Store) -> list[dict] | None:
    """fitness.json `races` (None: no fitness.json, or built before the field existed)."""
    p = store.dir / "fitness.json"
    if not p.exists():
        return None
    return store._memoize("fitness_races", lambda: [p], lambda: json.loads(p.read_text()).get("races"))


def _field(r: dict) -> list[dict]:
    """everyone's official time in one race, fastest first, with gap_s to the fastest"""
    res = [{"person": k, "official_s": float(v["official_s"]), "official": v.get("official")}
           for k, v in (r.get("results") or {}).items() if v.get("official_s")]
    res.sort(key=lambda x: x["official_s"])
    for x in res:
        x["gap_s"] = round(x["official_s"] - res[0]["official_s"], 1)
    return res


def _summary(rows: list[dict]) -> dict:
    run = [r for r in rows if r["sport"] == "run" and r.get("pct") is not None]
    best = max(run, key=lambda r: r["pct"]) if run else None
    return {"n_races": len(rows), "n_run": len(run), "n_ski": sum(r["sport"] == "ski" for r in rows),
            "n_beat": sum(r["pct"] > 0 for r in run),
            "avg_pct": round(sum(r["pct"] for r in run) / len(run), 2) if run else None,
            "avg_delta_s": round(sum(r["delta_s"] for r in run) / len(run)) if run else None,
            "best": {k: best[k] for k in ("name", "date", "km", "official_s", "predicted_s", "pct", "delta_s")} if best else None}


@router.get("/races")
def races(store: Store = Depends(get_store)):
    """The person's official races, oldest first (fitness.json `races`; run: prediction + index `pct`, > 0 = faster
    than predicted), each with `field` = everyone's time in that race (gap to the fastest), plus `summary`."""
    fr = _fitness_races(store)
    by = {(r["name"], r["date"]): r for r in _races_json()}
    if fr is None:   # no fitness.json (or an old one): official results without predictions
        fr = [{"name": r["name"], "date": r["date"], "sport": r["sport"], "km": float(r["km"]),
               "official_s": float(x["official_s"]), "official": x.get("official"),
               "activity_id": int(x["activity_id"]) if x.get("activity_id") else None,
               "source": "watch" if "watch" in (x.get("source") or "").lower() else "chip"}
              for r in by.values() if (x := (r.get("results") or {}).get(store.person)) and x.get("official_s")]
        stale = True
    else:
        stale = False
    rows = [{**r, "field": _field(by[(r["name"], r["date"])]) if (r["name"], r["date"]) in by else []} for r in fr]
    return {"person": store.person, "races": rows, "summary": _summary(rows), "predictions": not stale}


@router.get("/community/races")
def community_races():
    """Head-to-head: every race in data/races.json (newest first) with each person's official time, gap to the
    fastest, and for run races the index from their own fitness.json (`pct`, `predicted_s`, `delta_s`)."""
    pred: dict[str, dict] = {}
    for p in people():
        for r in _fitness_races(store_for(p)) or []:
            pred[(p, r["name"], r["date"])] = r
    out, seen = [], set()
    for r in reversed(_races_json()):
        res = []
        for x in _field(r):
            f = pred.get((x["person"], r["name"], r["date"])) or {}
            v = (r["results"] or {})[x["person"]]
            res.append({**x, "activity_id": int(v["activity_id"]) if v.get("activity_id") else None,
                        "source": "watch" if "watch" in (v.get("source") or "").lower() else "chip",
                        **{k: f[k] for k in ("predicted_s", "pct", "delta_s") if k in f}})
            seen.add(x["person"])
        out.append({"name": r["name"], "date": r["date"], "sport": r["sport"], "km": float(r["km"]), "results": res})
    ps = [p for p in people() if p in seen] + sorted(seen - set(people()))
    return {"people": ps, "races": out}

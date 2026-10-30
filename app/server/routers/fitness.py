"""EXTENSION POINT: /api/fitness — per-discipline shape scores over time from scripts/fitness_model.py
(artifact data/<p>/fitness.json). Frontend route: /fitness (pages/Stub.tsx -> pages/Fitness.tsx)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ..deps import get_store
from ..store import Store

router = APIRouter(tags=["fitness"])


@router.get("/fitness")
def fitness(store: Store = Depends(get_store)):
    p = store.dir / "fitness.json"
    if p.exists():  # raw passthrough until a proper endpoint exists
        return JSONResponse({"todo": "fitness endpoint not shaped yet", "raw": json.loads(p.read_text())}, 501)
    return JSONResponse({"todo": "fitness not implemented yet (no fitness.json)"}, 501)

"""EXTENSION POINT: /api/search and /api/nearest/{id}.

To be implemented on top of scripts/similarity.py (`similarity.load_index(person)` ->
`ix.search(filters)`, `ix.nearest(id, k, weights)`; artifact data/<p>/similarity_index.json).
Frontend route: /search (app/web/src/pages/Stub.tsx -> replace with pages/Search.tsx).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ..deps import get_store
from ..store import Store

router = APIRouter(tags=["search"])


@router.get("/search")
def search(request: Request, store: Store = Depends(get_store)):
    """Filters (planned): place, route, workout category, signature, rep count, rep length, date range."""
    return JSONResponse({"todo": "search not implemented yet", "params": dict(request.query_params)}, 501)


@router.get("/nearest/{aid}")
def nearest(aid: str, k: int = 10, preset: str | None = None, store: Store = Depends(get_store)):
    return JSONResponse({"todo": "nearest not implemented yet", "id": aid, "k": k, "preset": preset}, 501)

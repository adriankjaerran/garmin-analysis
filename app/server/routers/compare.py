"""EXTENSION POINT: /api/compare?ids=a,b,c — side-by-side metrics, aligned series, shared-route legs.
Frontend route: /compare?ids=... (app/web/src/pages/Stub.tsx -> pages/Compare.tsx)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ..deps import get_store
from ..store import Store

router = APIRouter(tags=["compare"])


@router.get("/compare")
def compare(ids: str = "", store: Store = Depends(get_store)):
    A = store.activity_map()
    found = [A[i] for i in ids.split(",") if i in A]
    return JSONResponse({"todo": "compare not implemented yet", "activities": found}, 501)

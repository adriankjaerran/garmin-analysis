"""/api/meta: person, sports, years and source freshness (sidebar + filters)."""

from __future__ import annotations

import collections

from fastapi import APIRouter, Depends

from ..deps import get_store
from ..store import SPORT_LABEL, SPORTS, Store, type_label

router = APIRouter(tags=["meta"])


@router.get("/meta")
def meta(store: Store = Depends(get_store)):
    acts = store.activities()
    years = sorted({a["date"][:4] for a in acts}, reverse=True)
    sports = collections.Counter(a["sport"] for a in acts)
    types = collections.Counter(a["type"] for a in acts)
    g = store.groups()
    return {
        "person": store.person,
        "n_activities": len(acts),
        "n_tracks": sum(a["track"] for a in acts),
        "first": acts[-1]["date"] if acts else None, "last": acts[0]["date"] if acts else None,
        "years": years,
        "sports": [{"id": s, "label": SPORT_LABEL[s], "n": sports[s]} for s in SPORTS if sports[s]],
        "types": [{"id": t, "label": type_label(t), "n": n} for t, n in types.most_common()],
        "groups_built": g.get("built"),
    }


@router.post("/cache/refresh")
def cache_refresh():
    """Re-scan activities/ and parse new or changed FIT files into the series cache (background)."""
    from ..main import refresh_cache_async
    refresh_cache_async()
    return {"started": True}

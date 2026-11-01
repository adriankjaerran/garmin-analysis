"""/api/settings: per-person app settings (data/<person>/settings.json, scripts/person_settings.py).

`distance_curve` (bool, default true): the personal distance curve of the race predictor. Off = the same VDOT at
every distance (Daniels equivalents of the 5 km shape) on the Race predictor, Fitness, Home and in form deltas.
Frontend: the switch on pages/RacePredictorPage.tsx."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException

from ..deps import get_store
from ..store import Store

router = APIRouter(tags=["settings"])


def _ps():
    import person_settings  # scripts/ (on sys.path via app/server/__init__.py)
    return person_settings


@router.get("/settings")
def get_settings(store: Store = Depends(get_store)):
    """The person's settings with defaults filled in."""
    return {"person": store.person, **_ps().load(store.person)}


@router.put("/settings")
def put_settings(store: Store = Depends(get_store), body: dict = Body(..., examples=[{"distance_curve": False}])):
    """Change settings (only the keys given) -> all settings. 422 for unknown keys / wrong types."""
    try:
        out = _ps().save(store.person, body)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"person": store.person, **out}

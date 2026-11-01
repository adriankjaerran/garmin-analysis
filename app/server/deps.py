"""Shared FastAPI dependencies. Routers get the Store with `store: Store = Depends(get_store)`.

The person comes from the URL: `/api/<person>/...` is rewritten to `/api/...` by `PersonPrefix` (main.py), which
puts the person in `scope["person"]`. Unprefixed `/api/...` uses the default person (`--person` / GARMIN_PERSON)."""

from __future__ import annotations

import os
from functools import lru_cache

from fastapi import Request

from . import ROOT
from .store import Store


def default_person() -> str:
    return os.environ.get("GARMIN_PERSON", "adrian")


def people() -> list[str]:
    """Everyone with data: folders in data/ that have an activities_index.json (default person first)."""
    found = sorted(f.parent.name for f in (ROOT / "data").glob("*/activities_index.json"))
    d = default_person()
    return ([d] if d in found else []) + [p for p in found if p != d]


@lru_cache(maxsize=16)
def store_for(person: str) -> Store:
    return Store(person)


def get_store(request: Request) -> Store:
    return store_for(request.scope.get("person") or default_person())

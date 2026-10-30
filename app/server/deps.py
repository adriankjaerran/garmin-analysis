"""Shared FastAPI dependencies. Routers get the Store with `store: Store = Depends(get_store)`."""

from __future__ import annotations

import os
from functools import lru_cache

from .store import Store


@lru_cache(maxsize=4)
def store_for(person: str) -> Store:
    return Store(person)


def get_store() -> Store:
    return store_for(os.environ.get("GARMIN_PERSON", "adrian"))

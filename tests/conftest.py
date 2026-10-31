"""Shared fixtures: a FastAPI TestClient over the real data in data/adrian (tests skip when it is missing).

The client is created without entering the app's lifespan, so the background FIT-cache refresh doesn't run;
tests only read the existing caches (the compare engine may update its own cache/compare.pkl)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PERSON = os.environ.get("GARMIN_PERSON", "adrian")
DATA = ROOT / "data" / PERSON

needs_data = pytest.mark.skipif(not (DATA / "activities_index.json").exists(), reason=f"no data in {DATA}")


@pytest.fixture(scope="session")
def client():
    if not (DATA / "activities_index.json").exists():
        pytest.skip(f"no data in {DATA}")
    from fastapi.testclient import TestClient

    from app.server.main import create_app

    return TestClient(create_app())


@pytest.fixture(scope="session")
def activities(client) -> list[dict]:
    r = client.get("/api/activities")
    assert r.status_code == 200
    return r.json()["activities"]


@pytest.fixture(scope="session")
def r3_ids(client) -> list[str]:
    return [a["id"] for a in client.get("/api/activities?route=R3").json()["activities"]]

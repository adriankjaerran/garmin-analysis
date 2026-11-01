"""FastAPI app: /api/* routers + the built frontend (app/web/dist) with SPA fallback."""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .deps import people
from .routers import (
    activities,
    compare,
    days,
    fitness,
    home,
    maps,
    meta,
    places,
    search,
    together,
)

DIST = Path(__file__).resolve().parents[1] / "web" / "dist"


log = logging.getLogger("garmin-app")


def refresh_cache_async() -> threading.Thread:
    """Incremental series/track cache build (new or changed FIT files only) in a background thread."""
    from . import fitcache

    def run():
        for person in people():
            try:
                r = fitcache.build(person, quiet=True, workers=4)
                if r["parsed"] or r["reps"]:
                    log.warning("series cache refreshed for %s: %s FIT parsed, %s rep analyses (%.1f s)",
                                person, r["parsed"], r["reps"], r["seconds"])
            except Exception:  # never take the server down for a cache problem
                log.exception("series cache refresh failed for %s", person)
    t = threading.Thread(target=run, name="fitcache", daemon=True)
    t.start()
    return t


class PersonPrefix:
    """ASGI middleware: `/api/<person>/rest` -> `/api/rest` with scope["person"] = person (see deps.get_store).
    Paths whose second segment isn't a person with data pass through unchanged (default person)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/api/"):
            head, _, rest = scope["path"][len("/api/"):].partition("/")
            if head and head in people():
                path = "/api/" + rest
                scope = {**scope, "path": path, "raw_path": path.encode(), "person": head}
        await self.app(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI):
    refresh_cache_async()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Garmin analysis", docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan)
    app.add_middleware(GZipMiddleware, minimum_size=2048)
    app.add_middleware(PersonPrefix)
    # each router owns its paths; later workers extend search/compare/fitness in their own file
    for r in (meta, activities, days, home, maps, places, search, compare, fitness, together):
        app.include_router(r.router, prefix="/api")

    @app.get("/api/{rest:path}", include_in_schema=False)
    def api_404(rest: str):
        raise HTTPException(404, f"unknown endpoint /api/{rest}")

    if (DIST / "assets").exists():
        app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = DIST / path
        if path and f.is_file() and DIST in f.resolve().parents:
            return FileResponse(f)
        index = DIST / "index.html"
        if not index.exists():
            raise HTTPException(404, "frontend not built: cd app/web && npm install && npm run build")
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app

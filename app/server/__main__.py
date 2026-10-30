"""uv run garmin-app [--person adrian] [--port 8765] [--reload]  (or: uv run python -m app.server)"""

import argparse
import os


def main() -> None:
    ap = argparse.ArgumentParser(description="Garmin analysis app: API + built frontend")
    ap.add_argument("--person", default=os.environ.get("GARMIN_PERSON", "adrian"))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--reload", action="store_true", help="auto-reload on backend code changes")
    a = ap.parse_args()
    os.environ["GARMIN_PERSON"] = a.person
    import uvicorn
    uvicorn.run("app.server.main:create_app", factory=True, host=a.host, port=a.port, reload=a.reload,
                reload_dirs=["app/server"] if a.reload else None)


if __name__ == "__main__":
    main()

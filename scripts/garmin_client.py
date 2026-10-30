"""Minimal Garmin Personal API client with request logging and 429 backoff.

Every request is appended to data/<person>/_requests.jsonl so we can study
latency, sizes and rate limiting afterwards.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE = "https://personal.connectapi.garmin.com"
TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"
ROOT = Path(__file__).resolve().parent.parent


def load_key(person: str) -> str:
    name = f"GARMIN_{person.upper()}_API_KEY"
    if os.environ.get(name):
        return os.environ[name]
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith(name + "="):
            value = line.split("=", 1)[1].strip()
            if value:
                return value
    raise SystemExit(f"{name} not set in environment or .env")


class GarminClient:
    def __init__(self, person: str, *, min_interval: float = 0.0, max_backoff_total: float = 1800):
        self.person = person.lower()
        self.data_dir = ROOT / "data" / self.person
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.data_dir / "_requests.jsonl"
        self.session = requests.Session()
        self.min_interval = min_interval
        self.max_backoff_total = max_backoff_total
        self._last = 0.0
        self.count = 0
        self._key = load_key(person)
        self._token_time = 0.0
        self.refresh_token()

    def refresh_token(self) -> None:
        r = self._send("POST", TOKEN_PATH, headers={"Authorization": f"ApiKey {self._key}"})
        r.raise_for_status()
        self.session.headers["Authorization"] = f"Bearer {r.text.strip()}"
        self._token_time = time.time()

    def _log(self, **entry) -> None:
        entry["ts"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        with self.log_path.open("a") as f:
            f.write(json.dumps(entry) + "\n")

    def _send(self, method: str, path: str, *, params=None, headers=None) -> requests.Response:
        wait = self.min_interval - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        backoff, waited = 15.0, 0.0
        while True:
            t0 = time.time()
            r = self.session.request(method, BASE + path, params=params, headers=headers, timeout=120)
            self._last = time.time()
            self.count += 1
            self._log(method=method, path=path, params=params, status=r.status_code,
                      bytes=len(r.content), ms=round((self._last - t0) * 1000),
                      retry_after=r.headers.get("Retry-After"), n=self.count)
            if r.status_code != 429:
                return r
            if waited >= self.max_backoff_total:
                return r
            print(f"  429 on {path} after {self.count} requests; sleeping {backoff:.0f}s")
            time.sleep(backoff)
            waited += backoff
            backoff = min(backoff * 2, 300)

    def get(self, path: str, *, params=None, accept: str = "application/json") -> requests.Response:
        if time.time() - self._token_time > 23 * 3600:
            self.refresh_token()
        return self._send("GET", path, params=params, headers={"Accept": accept})

    def get_json(self, path: str, **params):
        r = self.get(path, params=params or None)
        if r.status_code == 204 or not r.content:
            return r.status_code, None
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, r.text


def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False))

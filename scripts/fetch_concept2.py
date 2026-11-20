"""Fetch Concept2 Logbook workouts (RowErg, SkiErg, BikeErg, ...) for one person.

Usage: uv run scripts/fetch_concept2.py adrian [--full] [--prune]

Token: CONCEPT2_<PERSON>_TOKEN in the environment or .env, a personal read-only token from the Logbook
(Edit Profile > Applications > Concept2 Logbook API; scopes user:read,results:read). API docs:
https://log.concept2.com/developers/documentation/ (no rate limit today; we still keep a small pause).

Writes data/<person>/concept2/:
  user.json                      profile (max HR, weight, ...)
  results.json                   every result row of the list endpoint, newest first
  results/<id>/result.json       one result with workout splits / intervals
  results/<id>/strokes.json      per-stroke data (t, d in dm, p in 1/10 s per 500 m, spm, hr), when the result has it
  results/<id>/<id>.fit          Concept2's FIT export (per-stroke records + work/rest laps), when the result has stroke data
  activities_index.json          Garmin-style index rows (concept2.sync), merged into the activity list by concept2.load_index
and links each FIT into data/<person>/activities/<date>_<id>_<indoor_rowing|skierg|bikeerg>/ (concept2.sync).
Incremental: a result is fetched again only when its list row changed (edited on the Logbook) or --full.
--prune removes result folders that are no longer in the Logbook (deleted there).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

import requests

import concept2

BASE = "https://log.concept2.com"
ROOT = Path(__file__).resolve().parent.parent
ACCEPT = "application/vnd.c2logbook.v1+json"


def load_token(person: str) -> str:
    name = f"CONCEPT2_{person.upper()}_TOKEN"
    if os.environ.get(name):
        return os.environ[name]
    env = ROOT / ".env"
    for line in (env.read_text().splitlines() if env.exists() else []):
        if line.startswith(name + "="):
            value = line.split("=", 1)[1].strip()
            if value:
                return value
    raise SystemExit(f"{name} not set in environment or .env")


def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False))
    tmp.replace(path)


class Concept2Client:
    def __init__(self, token: str, min_interval: float = 0.2):
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}", "Accept": ACCEPT})
        self.min_interval = min_interval
        self._last = 0.0
        self.count = 0

    def get(self, path: str, **params) -> requests.Response:
        wait = self.min_interval - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        for attempt in range(4):
            r = self.s.get(BASE + path, params=params or None, timeout=60)
            self._last = time.time()
            self.count += 1
            if r.status_code not in (429, 500, 502, 503, 504):
                break
            time.sleep(5 * 2 ** attempt)
        if r.status_code == 401:
            raise SystemExit("401 from Concept2: the token is invalid or revoked (regenerate it on the Logbook)")
        return r

    def json(self, path: str, **params):
        r = self.get(path, **params)
        r.raise_for_status()
        return r.json()

    def all_results(self) -> list[dict]:
        out, page = [], 1
        while True:
            body = self.json("/api/users/me/results", number=250, page=page)
            out += body.get("data") or []
            pag = (body.get("meta") or {}).get("pagination") or {}
            if page >= (pag.get("total_pages") or 1):
                return out
            page += 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("person")
    ap.add_argument("--full", action="store_true", help="fetch every result again")
    ap.add_argument("--prune", action="store_true", help="remove results deleted on the Logbook")
    a = ap.parse_args()
    person = a.person.lower()
    out = ROOT / "data" / person / "concept2"
    rdir = out / "results"
    c = Concept2Client(load_token(person))

    user = c.json("/api/users/me")["data"]
    save_json(out / "user.json", user)
    rows = sorted(c.all_results(), key=lambda r: r["date"], reverse=True)
    old = {}
    if (out / "results.json").exists():
        old = {r["id"]: r for r in json.loads((out / "results.json").read_text())}

    new = changed = 0
    for r in rows:
        d = rdir / str(r["id"])
        have = (d / "result.json").exists() and (not r.get("stroke_data") or (d / f"{r['id']}.fit").exists())
        if have and not a.full and old.get(r["id"]) == r:
            continue
        new += not have
        changed += have
        res = c.json(f"/api/users/me/results/{r['id']}")["data"]
        save_json(d / "result.json", res)
        if r.get("stroke_data"):
            st = c.get(f"/api/users/me/results/{r['id']}/strokes")
            if st.ok:
                save_json(d / "strokes.json", st.json().get("data") or [])
            fit = c.get(f"/api/users/me/results/{r['id']}/export/fit")
            if fit.ok and fit.content[8:12] == b".FIT":
                tmp = d / f"{r['id']}.fit.tmp"
                tmp.write_bytes(fit.content)
                tmp.replace(d / f"{r['id']}.fit")
            else:
                print(f"  {r['id']}: no FIT export ({fit.status_code})", file=sys.stderr)
        print(f"  {r['date']}  {r['type']:<8} {r['distance']:>6} m  {r['time_formatted']:>9}  {r['id']}")
    save_json(out / "results.json", rows)

    mine = {r["id"] for r in rows} | concept2.copied_ids(ROOT / "data" / person)   # copied.json: not from this Logbook
    gone = [p for p in rdir.glob("*") if p.is_dir() and p.name.isdigit() and int(p.name) not in mine]
    if gone and a.prune:
        for p in gone:
            shutil.rmtree(p)
    elif gone:
        print(f"{len(gone)} result folder(s) no longer on the Logbook (use --prune to remove): "
              + ", ".join(p.name for p in gone[:10]))
    n_rows, n_fit = concept2.sync(ROOT / "data" / person)
    types = {}
    for r in rows:
        types[r["type"]] = types.get(r["type"], 0) + 1
    print(f"{len(rows)} results ({', '.join(f'{k} {v}' for k, v in sorted(types.items()))}); "
          f"{new} new, {changed} updated; {c.count} requests; {n_fit} FIT files in activities/")


if __name__ == "__main__":
    main()

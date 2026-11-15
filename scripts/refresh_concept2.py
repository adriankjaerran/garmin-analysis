"""Refresh Concept2 erg workouts end to end, in the right order (one command).

Usage: uv run scripts/refresh_concept2.py adrian [--full] [--no-fetch]

  1. fetch_concept2.py <person>      new / edited Logbook workouts (+ cleaning of shared-machine sessions)
  2. session_groups.py <person>      session types, erg reps (re-runs cleaning, reads session_overrides.json)
  3. similarity.py <person> build    workout names, closest activities
  4. erg_fitness.py <person>         Row / SkiErg trend (the app also rebuilds it by itself)
  5. garmin-app-cache <person>       charts / reps cache for the app

--no-fetch skips step 1: use it after editing data/<person>/session_overrides.json (offline).
--full refetches every workout from the Logbook. The running app picks the results up by itself (no restart).
See docs/concept2.md.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(cmd: list[str]) -> None:
    t0 = time.time()
    print(f"\n$ {' '.join(cmd)}", flush=True)
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode:
        sys.exit(f"failed ({r.returncode}): {' '.join(cmd)}")
    print(f"  ok, {time.time() - t0:.1f} s", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("person")
    ap.add_argument("--full", action="store_true", help="refetch every workout")
    ap.add_argument("--no-fetch", action="store_true", help="skip the Logbook fetch (after editing overrides)")
    a = ap.parse_args()
    py, p = sys.executable, a.person
    if not a.no_fetch:
        run([py, "scripts/fetch_concept2.py", p] + (["--full"] if a.full else []))
    run([py, "scripts/session_groups.py", p])
    run([py, "scripts/similarity.py", p, "build"])
    run([py, "scripts/erg_fitness.py", p, "--quiet"])
    run([py, "-m", "app.server.fitcache", p])
    print(f"\ndone: open http://127.0.0.1:8765/{p}/fitness#erg")


if __name__ == "__main__":
    main()

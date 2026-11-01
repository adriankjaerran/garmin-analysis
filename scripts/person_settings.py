"""Per-person app settings: data/<person>/settings.json (written by the app, PUT /api/<person>/settings).

Keys (missing file / key = the default):
  distance_curve  bool, default true. false = the personal distance curve is switched off: race predictions, race
                  equivalents and the `long` shape use the same VDOT at every distance (Daniels equivalents of the
                  5 km shape theta; fitness.json `curve_off`), and form deltas of running sessions are taken against
                  theta instead of the curve at the session's distance. Read by app/server/routers/fitness.py (and
                  home.py through it) and scripts/form_delta.py; docs/fitness-model.md "Distance curve on / off".
"""

from __future__ import annotations

import json
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
DEFAULTS: dict = {"distance_curve": True}
TYPES = {"distance_curve": bool}


def path(person: str) -> Path:
    return DATA / person / "settings.json"


def mtime(person: str) -> int:
    p = path(person)
    return p.stat().st_mtime_ns if p.exists() else 0


def load(person: str) -> dict:
    """The person's settings with defaults filled in; an unreadable file or a wrongly typed value counts as missing."""
    out = dict(DEFAULTS)
    p = path(person)
    if p.exists():
        try:
            raw = json.loads(p.read_text())
        except (OSError, ValueError):
            raw = {}
        for k, t in TYPES.items():
            if isinstance(raw, dict) and isinstance(raw.get(k), t):
                out[k] = raw[k]
    return out


def distance_curve(person: str) -> bool:
    return bool(load(person)["distance_curve"])


def save(person: str, updates: dict) -> dict:
    """Merge `updates` (known keys, right types; else ValueError) into the file; returns the full settings."""
    if not isinstance(updates, dict):
        raise ValueError("settings must be a JSON object")
    bad = [k for k in updates if k not in TYPES]
    if bad:
        raise ValueError(f"unknown setting(s): {', '.join(bad)} (known: {', '.join(TYPES)})")
    wrong = [k for k, v in updates.items() if not isinstance(v, TYPES[k])]
    if wrong:
        raise ValueError(", ".join(f"{k} must be {TYPES[k].__name__}" for k in wrong))
    p = path(person)
    if not p.parent.exists():
        raise ValueError(f"no data folder for {person}")
    cur = {}
    if p.exists():
        try:
            cur = json.loads(p.read_text())
            cur = cur if isinstance(cur, dict) else {}
        except (OSError, ValueError):
            cur = {}
    cur.update(updates)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cur, indent=1) + "\n")
    tmp.replace(p)
    return load(person)

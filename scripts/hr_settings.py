"""Max and resting heart rate from the person's own data -> data/<person>/profile/hr_settings.json.

Usage: uv run scripts/hr_settings.py adrian [--dry-run]

- max_hr: the highest heart rate ever measured in a run (running, track, trail, treadmill). Other sports are
  left out: wrist HR on skis and bikes gives spikes (adrian 2026-03-01 ski: 175 -> 222 -> 206 within 20 s).
  The peak must be real: the summary max when the FIT holds within 4 bpm of it for 12 s, else that held value.
- rest_hr: the median of Garmin's daily resting HR over the last 365 days of data, from days the watch was worn
  for at least 20 h. Days worn only for a run report a "resting" HR of 100+.

A hand-edited file (`"source": "manual"` or no `source`) is never overwritten. Together (effort as % of HR
reserve) reads it, see scripts/together.py hr_norm.
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import session_groups as sg  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
N_CANDIDATES = 25
HOLD_S = 12
FULL_WEAR_H = 20


def _fits(D: Path) -> dict[int, Path]:
    return {int(p.stem): p for p in (D / "activities").glob("*/*.fit")}


def _held(fit: Path):
    """highest HR held for HOLD_S s in one FIT file"""
    s = sg.read_series(fit)
    t, h = np.asarray(s["t"], float), np.asarray(s["hr"], float)
    ok = np.isfinite(h)
    t, h = t[ok], h[ok]
    if len(h) < 10:
        return None
    held, j = 0.0, 0
    for i in range(len(h)):
        while t[i] - t[j] > HOLD_S + 3:
            j += 1
        if t[i] - t[j] >= HOLD_S:
            held = max(held, float(h[j:i + 1].min()))
    return held


def max_hr(person: str) -> dict | None:
    D = ROOT / "data" / person
    idx = json.loads((D / "activities_index.json").read_text())
    acts = idx if isinstance(idx, list) else idx.get("activities", idx)
    fits = _fits(D)
    run = lambda a: "running" in ((a.get("activityType") or {}).get("typeKey") or "")
    cands = sorted((a for a in acts if a.get("maxHR") and run(a) and int(a["activityId"]) in fits), key=lambda a: -a["maxHR"])
    best = None
    for a in cands[:N_CANDIDATES]:
        try:
            held = _held(fits[int(a["activityId"])])
        except Exception:  # noqa: BLE001 — a broken FIT is skipped
            continue
        if not held:
            continue
        row = {"id": int(a["activityId"]), "date": (a.get("startTimeLocal") or "")[:10],
               "type": (a.get("activityType") or {}).get("typeKey"), "summary_max": int(a["maxHR"]), "held_12s": int(held)}
        # the summary max when it is near the held value (a real peak), else the held value
        v = row["summary_max"] if row["summary_max"] - held <= 4 else int(held)
        if best is None or v > best["max_hr"]:
            best = {"max_hr": v, **row}
    return best


def rest_hr(person: str, today: dt.date | None = None) -> dict | None:
    D = ROOT / "data" / person
    rows = {}
    for f in glob.glob(str(D / "ranges" / "daily_summaries_*.json")):
        for r in (json.loads(Path(f).read_text()).get("data") or []):
            hr, wc = r.get("heartRate") or {}, r.get("wellnessChronology") or {}
            if hr.get("restingValue") and (wc.get("totalDurationInMillis") or 0) / 3.6e6 >= FULL_WEAR_H:
                rows[r["calendarDate"]] = hr["restingValue"]
    if not rows:
        return None
    last = dt.date.fromisoformat(max(rows))
    since = str(last - dt.timedelta(days=365))
    v = [x for d, x in rows.items() if d > since]
    return {"rest_hr": int(round(float(np.median(v)))), "days": len(v), "from": since, "to": str(last)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("person")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    out = ROOT / "data" / a.person / "profile" / "hr_settings.json"
    if out.exists():
        old = json.loads(out.read_text())
        if old.get("source", "manual") != "auto":
            print(f"{out} is hand-edited (source {old.get('source', 'manual')!r}): not overwritten")
            return
    mx, rs = max_hr(a.person), rest_hr(a.person)
    res = {"max_hr": mx and mx["max_hr"], "rest_hr": rs and rs["rest_hr"], "source": "auto",
           "built": dt.date.today().isoformat(),
           "max_from": mx and {k: mx[k] for k in ("id", "date", "type", "summary_max", "held_12s")},
           "rest_from": rs and {k: rs[k] for k in ("days", "from", "to")},
           "note": "max = highest HR ever measured in a run (held 12 s), rest = median daily resting HR of the "
                   "last 365 days (full-wear days). Edit by hand and set \"source\": \"manual\" to keep your values."}
    print(json.dumps(res, indent=1))
    if not a.dry_run:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(res, indent=1) + "\n")
        print("wrote", out)


if __name__ == "__main__":
    main()

"""Who drives the shared model calibration (tau_scale, interval_offset, tempo_offset, SIG, distance-curve priors, HR /
detection thresholds)?

A person's data enter the *choice* of a shared parameter only if they are calibration grade; everyone else is
reported as out-of-sample validation (their metrics are printed, never minimised). Personal quantities (shape,
slope, easy pace, HR max / rest) always come from the person's own data, read through the shared parameters.

Rule (`calibration_weight`), from groups.json and data/races.json only (no model fit, so it is not circular):
  - >= MIN_INTERVALS interval sessions with a trusted distance (intervals_track / intervals_road), and
  - >= MIN_RACES running races (official results in data/races.json, or auto-detected `race?` not in not_races).
  weight 1.0 if both hold, else 0.0. 2026-11-04: adrian 99 / 5, rise 62 / 5 -> 1; patrik 11 / 1 -> 0 (few runs,
  3-9 s smart recording, several mislabelled sessions; docs/fitness-model.md "Patrik").
`data/calibration.json` {"weights": {person: w}} overrides the rule (manual decision, e.g. a partial weight).

    import calibration_set as cs
    cs.calibration_persons()       # ["adrian", "rise"]
    cs.validation_persons()        # ["patrik"]
    cs.calibration_weight("rise")  # 1.0
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # repo root (this file is scripts/research/)
MIN_INTERVALS = 30
MIN_RACES = 2


def persons() -> list[str]:
    p = ROOT / "data" / "people.json"
    if p.exists():
        return list(json.loads(p.read_text()))
    return sorted(d.name for d in (ROOT / "data").iterdir() if (d / "groups.json").exists())


def counts(person: str) -> dict:
    """{intervals, races} used by the rule."""
    g = ROOT / "data" / person / "groups.json"
    if not g.exists():
        return {"intervals": 0, "races": 0}
    acts = json.loads(g.read_text())["activities"]
    rj = ROOT / "data" / "races.json"
    R = json.loads(rj.read_text()) if rj.exists() else {}
    official = {str(r["results"][person]["activity_id"]) for r in R.get("races", [])
                if r.get("sport") == "run" and (r.get("results") or {}).get(person, {}).get("official_s")}
    official_n = sum(1 for r in R.get("races", []) if r.get("sport") == "run" and (r.get("results") or {}).get(person, {}).get("official_s"))
    not_races = {str(x["activity_id"]) for x in (R.get("not_races") or {}).get(person, [])}
    auto = {i for i, a in acts.items() if a.get("type") == "race?" and i not in not_races and i not in official}
    n_int = sum(1 for a in acts.values() if a.get("type") in ("intervals_track", "intervals_road") and a.get("dist_trusted"))
    return {"intervals": n_int, "races": official_n + len(auto)}


def calibration_weight(person: str) -> float:
    o = ROOT / "data" / "calibration.json"
    if o.exists():
        w = (json.loads(o.read_text()).get("weights") or {})
        if person in w:
            return float(w[person])
    c = counts(person)
    return 1.0 if c["intervals"] >= MIN_INTERVALS and c["races"] >= MIN_RACES else 0.0


def calibration_persons() -> list[str]:
    return [p for p in persons() if calibration_weight(p) > 0]


def validation_persons() -> list[str]:
    return [p for p in persons() if calibration_weight(p) == 0 and (ROOT / "data" / p / "groups.json").exists()]


if __name__ == "__main__":
    for p in persons():
        print(f"{p:8s} {counts(p)}  weight {calibration_weight(p):.2f}")

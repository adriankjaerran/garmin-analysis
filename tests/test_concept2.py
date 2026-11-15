"""Concept2 erg workouts as activities (scripts/concept2.py, session_groups erg reps / signature, the app's erg rows).

The unit tests use synthetic results; the API tests skip without data/<person>/concept2."""

from __future__ import annotations

import json
import sys

import pytest

from .conftest import DATA, ROOT

sys.path.insert(0, str(ROOT / "scripts"))
import concept2 as c2  # noqa: E402
import session_groups as sg  # noqa: E402

needs_c2 = pytest.mark.skipif(not (DATA / "concept2" / "activities_index.json").exists(), reason="no Concept2 data")


def _result(**kw):
    r = {"id": 123456789, "user_id": 1, "date": "2026-11-06 18:50:00", "date_utc": "2026-11-06 17:50:00",
         "type": "rower", "distance": 6598, "time": 14400, "rest_time": 3600, "rest_distance": 219,
         "workout_type": "VariableInterval", "stroke_rate": 33, "calories_total": 485, "drag_factor": 200,
         "heart_rate": {}, "stroke_data": True, "source": "ErgData iOS",
         "workout": {"intervals": [{"type": "time", "time": 1200, "distance": 550 - i, "stroke_rate": 34,
                                    "rest_time": 300, "rest_distance": 20, "targets": {"pace": 1090}} for i in range(12)]}}
    r.update(kw)
    return r


def _laps(n=12, work=120, rest=30):
    out, t = [], 0.0
    for i in range(n):
        out.append([t, work, 550 - i, None, None, 0, 0])   # work lap (intensity 0 = active)
        t += work
        if i < n - 1:
            out.append([t, rest, 20, None, None, 0, 1])   # rest lap (intensity 1)
            t += rest
    return out


def test_index_row():
    x = c2.index_row(_result())
    assert x["activityId"] == 123456789
    assert x["activityType"]["typeKey"] == "indoor_rowing"
    assert x["startTimeLocal"] == "2026-11-06 18:50:00" and x["startTimeGMT"] == "2026-11-06 17:50:00"
    assert x["movingDuration"] == 1440 and x["duration"] == 1800   # work + rest
    assert x["activityName"] == "RowErg 12×2:00"
    # 1440 s / 6598 m = 1:49.1 /500 m -> 2.80 / (0.2182 s/m)^3 = ~269 W
    assert 265 <= x["avgPower"] <= 272
    assert c2.index_row(_result(type="skierg"))["activityType"]["typeKey"] == "skierg"


def test_workout_names():
    assert c2.workout_name(_result(workout_type="JustRow", time=18000, workout={})) == "30:00"
    assert c2.workout_name(_result(workout_type="FixedTimeSplits", time=18000, workout={})) == "30:00"
    assert c2.workout_name(_result(workout_type="FixedDistanceSplits", distance=2000, workout={})) == "2000m"
    iv = [{"type": "distance", "distance": 500, "time": 900}] * 4
    assert c2.workout_name(_result(workout_type="FixedDistanceInterval", workout={"intervals": iv})) == "4×500m"


def test_erg_ana_and_signature():
    row = c2.index_row(_result())
    ana = c2.erg_ana(row, _laps())
    assert ana["mode"] == "erg" and len(ana["rep_times"]) == 12
    assert ana["rep_starts"][:3] == [0.0, 150.0, 300.0]
    assert all(b[0] == 30 for b in ana["breaks"])
    assert sg.signature(ana) == "12×2:00"
    assert c2.erg_type(row, ana)[0] == "erg_intervals"
    # distance intervals: same distance, different times -> "4×500"
    d = {"mode": "erg", "rep_times": [101.2, 99.8, 100.4, 98.9], "rep_dists": [500, 500, 500, 500]}
    assert sg.signature(d) == "4×500"
    # continuous pieces: no reps; short (<= 12 min) vs steady
    cont = c2.index_row(_result(workout_type="FixedDistanceSplits", distance=2000, time=4400, rest_time=0, workout={}))
    assert c2.erg_ana(cont, _laps(1)) is None
    assert c2.erg_type(cont, None)[0] == "erg_piece"
    assert c2.erg_type(c2.index_row(_result(workout_type="FixedTimeSplits", time=18000, rest_time=0, workout={})), None)[0] == "erg_steady"


@needs_c2
def test_erg_rows_in_app(client, activities):
    erg = [a for a in activities if a.get("erg")]
    assert erg, "Concept2 rows in /api/activities"
    assert {a["sport"] for a in erg} <= {"row", "skierg", "bike"}
    a = next(x for x in erg if x["stype"] == "erg_intervals")
    assert a["erg"]["pace_500_s"] and a["erg"]["watts"]
    d = client.get(f"/api/activities/{a['id']}").json()
    reps = d["reps"]
    assert reps and all(r.get("start") is not None for r in reps)
    if d["series"] and d["series"].get("cad"):   # strokes per minute, not doubled like running cadence
        spm = [v for v in d["series"]["cad"] if v]
        assert max(spm) < 80


@needs_c2
def test_erg_metres_not_in_km_totals(client):
    days = client.get("/api/calendar?sport=row,skierg").json()["days"]
    assert days and all(d["km"] == 0 for d in days) and any(d["s"] > 0 for d in days)


def _shared_strokes(pattern: str, fast=4.9, slow=4.5, slot=60.0):
    """synthetic strokes (1 per s) for a session whose slots follow `pattern` (X = this person, . = someone slower)"""
    out, d = [], 0.0
    for i, c in enumerate(pattern):
        for s in range(int(slot)):
            d += {"X": fast, "F": 4.75, "M": 4.0}.get(c, slow)
            out.append({"t": int((i * slot + s + 1) * 10), "d": int(d * 10), "spm": 66 if c == "X" else 60})   # F: a friend sprinting
    return out


def test_shared_machine_cleaning():
    r = _result(type="skierg", workout_type="FixedTimeSplits", time=24000, rest_time=0, distance=11000, workout={})
    sh = c2.shared_turns(r, _shared_strokes(".X" * 20))
    assert sh and sh["n_turns"] == 20 and sh["on_s"] == 60 and sh["off_s"] == 60
    cr = c2.clean(r, sh)
    assert cr["workout_type"] == "VariableInterval" and len(cr["workout"]["intervals"]) == 20
    assert cr["time"] == 20 * 600 and 20 * 280 < cr["distance"] < 20 * 300   # own metres only
    assert c2.workout_name(cr) == "20×1:00"
    row = c2.index_row(cr)
    assert row["activityName"].endswith("(shared)") and row["c2"]["shared"]["logbook_dist_m"] == 11000
    ana = c2.erg_ana(row, None)
    assert ana["rep_starts"][:2] == [60.0, 180.0] and sg.signature(ana) == "20×1:00"
    # the last stroke lands ~1 s before the end: the last turn (yours) still counts
    assert c2.shared_turns(r, _shared_strokes(".X" * 20)[:-1])["n_turns"] == 20
    # a friend sprinting the last minute (fast, but out of rhythm) is not you; a solo opening piece stays
    fin = _shared_strokes(".X" * 14 + "F", fast=4.9, slot=60.0)   # F = the friend at 4.75 m/s (fast, < 98 % of you)
    assert c2.shared_turns(r, fin)["pattern"].endswith(".X.")
    assert c2.shared_turns(r, _shared_strokes("XXXXX" + ".X" * 12))["pattern"].startswith("XXXXX.")
    # 1 on / 2 off (three people, one machine)
    assert c2.shared_turns(r, _shared_strokes(".X." * 12))["off_s"] == 120
    # three people, a friend's profile: {"who": 2} = the 2nd fastest, {"who": 3} = the slowest
    three = _shared_strokes(".MX" * 12)
    assert c2.shared_turns(r, three)["pattern"] == "..X" * 12
    assert c2.shared_turns(r, three, {"who": 2})["pattern"] == "X.." * 12
    sh3 = c2.shared_turns(r, three, {"who": 3})
    assert sh3["pattern"] == ".X." * 12 and sh3["n_turns"] == 12 and sh3["off_s"] == 120
    # a second, unrecorded machine in the rotation (1:00 here + 1:00 there): totals = both, the other one estimated
    sh2 = c2.shared_turns(r, three, {"who": 2, "second_machine_m": 9900})
    sm = sh2["second_machine"]
    assert (sh2["on_s"], sh2["off_s"]) == (120, 60) and sm["ratio"] == 0.9 and sm["time_s"] == 720
    own = sum(x[2] for x in sh2["turns"])
    cr2 = c2.clean(r, sh2)
    assert cr2["time"] == 2 * 7200 and cr2["distance"] == round(own) + sm["est_m"] and abs(sm["est_m"] - 0.9 * own) < 1
    assert len(cr2["workout"]["intervals"]) == 12 and c2.workout_name(cr2) == "12×2:00, 2 machines"
    # a solo piece that fades, or one that is just even: not shared; override false wins, true forces
    assert c2.shared_turns(r, _shared_strokes("X" * 10 + "." * 20)) is None
    assert c2.shared_turns(r, _shared_strokes("X" * 30)) is None
    assert c2.shared_turns(r, _shared_strokes(".X" * 20), False) is None
    assert c2.shared_turns(r, _shared_strokes("XXX" + "." * 12 + "X.X"), True)["forced"]


def test_turn_profiles_handover_cut():
    """the handover cut is adaptive (until the monitor pace settles within 2 %), but 8-10 s"""
    def turn(settle_s):   # 60 s turn: the monitor pace falls from 2:30 to 1:45 over `settle_s`, then steady
        return [{"t": i * 10, "d": i * 47, "p": int(10 * (105 + max(0, 45 * (1 - i / settle_s)) if i < settle_s else 1050 / 10))}
                for i in range(1, 61)]
    for settle, cut in ((3, 8.0), (9, 9.0), (25, 10.0)):
        st = turn(settle)
        pr = c2.turn_profiles(st, [[0.0, 60.0, 282.0, 60]])[0]
        assert pr["cut_s"] == cut, (settle, pr["cut_s"])
        assert pr["t"][0] >= cut and pr["t"][-1] <= 59 and abs(pr["steady_pace_s"] - 500 / 4.7) < 0.5


def test_manual_session(tmp_path):
    """hand-logged sessions (concept2/manual.json) are merged into the index: totals only, no per-person metres"""
    C = tmp_path / "concept2"; C.mkdir()
    (C / "results.json").write_text(json.dumps([_result()]))
    man = {"id": c2.MANUAL_ID_BASE + 1, "type": "skierg", "date": "2026-11-13 18:10:00", "date_utc": "2026-11-13 17:10:00",
           "name": "10×2:00, 2 machines, 3 people", "workout_type": "VariableInterval", "time": 12000, "rest_time": 6000,
           "distance": None, "stroke_data": False,
           "manual": {"people": ["a", "b", "c"], "on_s": 120, "off_s": 60, "rounds": 10, "session_s": 1800,
                      "machines": [{"dist_m": 7264, "time_s": 1800, "pace_500_s": 123.9}], "total_m": 7264}}
    (C / "manual.json").write_text(json.dumps([man]))
    n, _ = c2.sync(tmp_path)
    rows = c2.rows(tmp_path)
    assert n == 2 and rows[0]["activityId"] == man["id"]   # newest first
    x = rows[0]
    assert x["activityName"] == "SkiErg 10×2:00, 2 machines, 3 people"
    assert x["distance"] is None and x["avgPower"] is None and x["movingDuration"] == 1200 and x["duration"] == 1800
    assert x["c2"]["manual"]["total_m"] == 7264 and "logged by hand" in x["deviceName"]
    assert c2.erg_type(x, None) == ("erg_intervals", "logged by hand: 2:00 on / 1:00 off, 1 machines, no per-person split")

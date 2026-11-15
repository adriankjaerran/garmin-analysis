"""Race predictor: distance-curve math (fitness_model.smooth_curve / curve_predict) and the two API endpoints
(/api/fitness/predictor, /api/fitness/predict)."""

from __future__ import annotations

import json
import math

import pytest

import app.server  # noqa: F401  (puts scripts/ on sys.path)
import fitness_model as fm  # noqa: E402

from .conftest import DATA


# ---------------------------------------------------------------- curve math (no data needed)
def test_prediction_at_reference_is_theta():
    p = fm.curve_predict(52.0, 2.0, 1.0, 0.25, -0.1, 5.0, [5.0, 21.0975])
    assert p["x"] == 0 and p["vdot"] == 52.0
    assert p["sd_state"] == pytest.approx(1.0)
    assert p["sd"] == pytest.approx(math.sqrt(1.0 + fm.CURVE["race_sd"] ** 2))
    assert not p["extrapolated"]
    # one doubling: theta + slope, var = vt + 2 cov + vs
    p10 = fm.curve_predict(52.0, 2.0, 1.0, 0.25, -0.1, 10.0, [5.0, 21.0975])
    assert p10["vdot"] == pytest.approx(54.0) and p10["sd_state"] == pytest.approx(math.sqrt(1.0 - 0.2 + 0.25))


def test_sd_grows_beyond_the_evidence():
    ev = [5.0, 21.0975]
    half = fm.curve_predict(52.0, 2.0, 1.0, 0.25, 0.0, 21.0975, ev)
    mara = fm.curve_predict(52.0, 2.0, 1.0, 0.25, 0.0, 42.195, ev)
    k3 = fm.curve_predict(52.0, 2.0, 1.0, 0.25, 0.0, 3.0, ev)
    assert not half["extrapolated"] and mara["extrapolated"] and k3["extrapolated"]
    assert mara["doublings_outside"] == pytest.approx(1.0)
    # the extrapolation term adds on top of the state + race-day sd
    base = math.sqrt(mara["sd_state"] ** 2 + fm.CURVE["race_sd"] ** 2)
    assert mara["sd"] == pytest.approx(math.sqrt(base ** 2 + fm.CURVE["extrap_sd_per_doubling"] ** 2))
    assert mara["sd"] > half["sd"]
    far = fm.curve_predict(52.0, 2.0, 1.0, 0.25, 0.0, 100.0, ev)
    assert far["sd"] > mara["sd"]
    assert k3["doublings_outside"] == pytest.approx(math.log2(5 / 3))


def test_vdot_held_flat_beyond_the_longest_evidence():
    ev = [5.0, 21.0975]
    half = fm.curve_predict(52.0, 2.0, 1.0, 0.25, 0.0, 21.0975, ev)
    mara = fm.curve_predict(52.0, 2.0, 1.0, 0.25, 0.0, 42.195, ev)
    assert mara["vdot"] == pytest.approx(half["vdot"])
    assert mara["sd_state"] == pytest.approx(half["sd_state"])
    k3 = fm.curve_predict(52.0, 2.0, 1.0, 0.25, 0.0, 3.0, ev)
    assert k3["vdot"] < 52.0                 # below the shortest evidence the curve still applies
    neg = fm.curve_predict(52.0, -1.0, 1.0, 0.25, 0.0, 42.195, ev)
    assert neg["vdot"] == pytest.approx(52.0 - math.log2(42.195 / 5))   # a negative slope keeps applying


def test_smoother_recovers_a_synthetic_curve():
    """Noise-free 5 km and 20 km observations of theta = 50, slope = +2: the smoother finds both, and the state sd at
    an unobserved distance is larger than at the observed ones."""
    obs = []
    for d in range(0, 200, 5):
        obs.append((d, 50.0, 1.0, "race", 0.0))
        obs.append((d + 2, 54.0, 1.0, "race", 2.0))
    days, M, C, w, fc = fm.smooth_curve(obs, -7, 220, q_slope=0.0)
    assert M[100, 0] == pytest.approx(50.0, abs=0.1) and M[100, 1] == pytest.approx(2.0, abs=0.1)
    assert min(w) > 0.99 and len(fc) == len(obs)
    _, s0 = fm.curve_at(M[100], C[100], 0.0)
    _, s4 = fm.curve_at(M[100], C[100], 4.0)
    assert s4 > s0
    # after the last evidence the sd grows (random walk)
    assert C[-1, 0, 0] > C[190, 0, 0]


def test_evidence_km_mapping():
    race = {"discipline": "long", "kind": "race", "type": "race?", "components": {"race": "half"}}
    tempo = {"discipline": "long", "kind": "hard", "type": "tempo", "score": 55.0, "components": {"window_s": 1200, "gap_speed": 4.2}}
    bound = {"discipline": "long", "kind": "bound", "type": "easy", "score": 40.0, "components": {"window_s": 3600, "gap_speed": 3.0}}
    intv = {"discipline": "interval", "kind": "hard", "type": "intervals_track", "components": {}}
    dup = {"discipline": "interval", "kind": "hard", "type": "race?", "components": {"race": "10k"}}
    assert fm.evidence_km(race) == 21.0975
    assert fm.evidence_km(tempo) == pytest.approx(math.sqrt(10 * 21.0975))   # 10k-half mean in log distance
    assert fm.evidence_km(bound) == pytest.approx(10.8)
    assert fm.evidence_km(intv) == 5.0
    assert fm.evidence_km(dup) is None and fm.is_10k_dup(dup)
    assert fm.dist_label(21.0975) == "Half" and fm.dist_label(7.5) == "7.5 km"


# ---------------------------------------------------------------- endpoints (real data)
needs_pred = pytest.mark.skipif(not (DATA / "fitness.json").exists() or
                                not json.loads((DATA / "fitness.json").read_text()).get("predictor"),
                                reason="no predictor in fitness.json")


@needs_pred
def test_predictor_endpoint(client):
    d = client.get("/api/fitness/predictor").json()
    assert {"ref_km", "evidence_km", "daily", "evidence", "last_evidence_date", "today"} <= set(d)
    row = d["daily"][-1]
    assert row["date"] == d["today"]
    assert {"date", "vdot_5k", "vdot_10k", "vdot_half", "sd_5k", "sd_10k", "sd_half"} == set(row)
    assert all(a["date"] < b["date"] for a, b in zip(d["daily"], d["daily"][1:]))
    raw = json.loads((DATA / "fitness.json").read_text())["predictor"]
    assert len(d["daily"]) == len(raw["daily"]) and d["daily"][-1]["vdot_5k"] == pytest.approx(raw["daily"][-1]["theta"], abs=0.01)
    e = d["evidence"][0]
    assert {"date", "id", "km", "vdot", "kind", "weight"} <= set(e) and isinstance(e["id"], str)
    assert d["evidence_km"][0] <= d["evidence_km"][1]


@needs_pred
def test_predict_endpoint(client):
    d = client.get("/api/fitness/predict").json()
    assert {"date", "predictions", "shape", "basis"} <= set(d)
    labels = [p["label"] for p in d["predictions"]]
    assert labels == ["3k", "5k", "10k", "Half", "Marathon"]
    by = {p["label"]: p for p in d["predictions"]}
    assert by["5k"]["vdot"] == pytest.approx(d["shape"]["theta"], abs=0.01)
    assert by["Marathon"]["extrapolated"] and by["Marathon"]["sd"] > by["10k"]["sd"]
    for p in d["predictions"]:
        assert p["range_s"][0] < p["time_s"] < p["range_s"][1]
        assert p["pace_s_per_km"] == pytest.approx(p["time_s"] / p["km"], abs=0.2)
        assert abs(p["time_s"] - fm.race_time(p["vdot"], p["km"] * 1000)) < 1
    assert "per doubling" in d["shape"]["slope_text"]
    assert {"last_evidence_date", "days_since_evidence", "n_evidence_90d"} <= set(d["basis"])
    # custom distance and an earlier day
    first = client.get("/api/fitness/predictor").json()["daily"][0]["date"]
    c = client.get(f"/api/fitness/predict?date={first}&km=7.5").json()
    assert c["date"] == first and c["predictions"][0]["label"] == "7.5 km"


@needs_pred
def test_predict_bad_input(client):
    assert client.get("/api/fitness/predict?date=1990-01-01").status_code == 400
    assert client.get("/api/fitness/predict?date=2999-01-01").status_code == 400
    assert client.get("/api/fitness/predict?date=nope").status_code == 400
    assert client.get("/api/fitness/predict?km=0.5").status_code == 400
    assert client.get("/api/fitness/predict?km=150").status_code == 400
    assert client.get("/api/fitness/predict?km=abc").status_code == 400


@needs_pred
def test_fitness_payload_has_no_daily_predictor(client):
    d = client.get("/api/fitness").json()
    assert "daily" not in (d.get("predictor") or {}) and not any(k.startswith("_") for k in d.get("predictor") or {})
    assert d["disciplines"]["run"]["model"] == "distance_curve" and d["disciplines"]["long"]["model"] == "distance_curve"
    # the 10k counts once: no interval item copies a 10k race
    assert not any(s["discipline"] == "interval" and (s.get("components") or {}).get("race") == "10k" for s in d["sessions"])


# ---------------------------------------------------------------- official races (data/races.json) + ski plausibility
def _ski_feat(speed, climb_m, dur_s=1200, vdot=50.0):
    return {"ski": {"vdot": vdot, "dur_s": dur_s, "start": 0, "end": dur_s, "demand": 45.0, "climb_m": climb_m,
                    "speed": speed, "hrr": 0.8}}


def test_ski_plausibility_filter():
    act = {"type": "ski_easy", "sport": "ski", "surface": "snow"}
    assert fm.score_session(act, _ski_feat(4.0, 200))[0]["kind"] == "bound"
    fast = fm.score_session(act, _ski_feat(8.3, 36, vdot=87.0))[0]      # rise 2024-03-03: lift / car
    climb = fm.score_session(act, _ski_feat(4.4, 481, vdot=120.0))[0]   # rise 2025-01-25: barometer
    assert fast["kind"] == "implausible" and "km/h" in fast["components"]["implausible"]
    assert climb["kind"] == "implausible" and "climb" in climb["components"]["implausible"]


def test_official_races(tmp_path):
    f = tmp_path / "races.json"
    f.write_text(json.dumps({"races": [
        {"name": "Ski", "date": "2026-03-01", "sport": "ski", "km": 90, "results": {"p": {"official_s": 22000, "activity_id": 1}}},
        {"name": "Run", "date": "2026-10-23", "sport": "run", "km": 10, "results": {"p": {"official_s": 2390, "activity_id": 2},
                                                                                  "q": {"official_s": 2500, "activity_id": None}}}]}))
    assert fm.load_official_races("q", f) == {} and fm.load_official_races("x", tmp_path / "missing.json") == {}
    acts = {"1": {"type": "ski_long", "sport": "ski", "surface": "snow"},
            "2": {"type": "tempo", "sport": "run", "dist_trusted": True}}
    fm.apply_official_races("p", acts, f)
    ski = fm.score_session(acts["1"], _ski_feat(3.8, 100))
    assert [(e["kind"], e["sigma"]) for e in ski] == [("hard", fm.SIG["ski_race"])]
    assert fm.apply_hr_intent([{**ski[0], "date": "2026-03-01", "components": {**ski[0]["components"], "hrr": 0.4}}])[0]["kind"] == "hard"
    # a run typed tempo whose watch distance is off: scored from the official 10 km / chip time, one race on the curve
    run = fm.score_session(acts["2"], {"T": 2400, "moving_s": 2388, "dist_m": 10510.0, "cont": {"vdot": 55.0, "dur_s": 1800, "speed": 4.4}})
    race = [e for e in run if e["kind"] == "race"]
    assert len(race) == 1 and race[0]["discipline"] == "long"
    assert race[0]["score"] == pytest.approx(fm.vdot_from_race(10000, 2390))
    assert fm.evidence_km(race[0]) == 10.0
    assert not any(e["kind"] == "hard" and e["discipline"] == "long" for e in run)     # no tempo item
    assert [e for e in run if not fm.is_10k_dup({**e, "type": "tempo"})] == race


def test_tests_are_race_evidence_at_their_own_distance(tmp_path):
    f = tmp_path / "races.json"
    f.write_text(json.dumps({"races": [], "tests": {"p": [{"activity_id": 1, "date": "2026-03-31", "km": 3.0, "time_s": 627}]}}))
    acts = {"1": {"type": "easy", "sport": "run", "dist_trusted": True}}
    fm.apply_official_races("p", acts, f)
    assert fm.load_tests("q", f) == {} and fm.load_official_races("p", f) == {}   # not an official race
    ev = fm.score_session(acts["1"], {"T": 630, "moving_s": 627, "dist_m": 3001.0, "cont": {"vdot": 50.0, "dur_s": 600, "speed": 4.8}})
    assert len(ev) == 1 and ev[0]["kind"] == "race" and ev[0]["sigma"] == fm.SIG["race"]
    assert ev[0]["score"] == pytest.approx(fm.vdot_from_race(3000, 627)) and ev[0]["components"]["test"] == "3k test"
    assert fm.evidence_km(ev[0]) == 3.0


def test_not_races(tmp_path):
    f = tmp_path / "races.json"
    f.write_text(json.dumps({"races": [], "not_races": {"p": [{"activity_id": 1, "note": "track 5k, not a race"}]}}))
    acts = {"1": {"type": "race?"}, "2": {"type": "race?"}, "3": {"type": "tempo"}}
    fm.apply_official_races("p", acts, f)
    assert acts["1"]["type"] == "steady" and acts["1"]["not_race"]
    assert acts["2"]["type"] == "race?" and acts["3"]["type"] == "tempo"
    assert fm.load_not_races("q", f) == set() and fm.load_not_races("p", tmp_path / "missing.json") == set()


# ---------------------------------------------------------------- one run number (`run` = the curve at 10 km)
@needs_pred
def test_run_discipline_is_the_curve_at_10k(client):
    raw = json.loads((DATA / "fitness.json").read_text())
    assert "curve_off" not in raw and raw["model_version"] >= 3
    run = raw["disciplines"]["run"]
    t = raw["predictor"]["daily"][-1]
    assert run["ref_km"] == 10 and run["model"] == "distance_curve"
    # the weekly series = theta + slope (x = log2(10/5) = 1); today's value = the 10k race prediction (VDOT ± sd)
    assert run["weekly"][-1]["mean"] == pytest.approx(t["theta"] + t["slope"], abs=0.02)
    pred = raw["disciplines"]["overall"]["current"]["race_prediction"]
    assert run["current"]["mean"] == pytest.approx(pred["vdot"], abs=0.01) and run["current"]["sd"] == pytest.approx(pred["sd"], abs=0.01)
    assert run["current"]["sd_state"] <= run["current"]["sd"]
    for name in ("interval", "long"):
        assert raw["disciplines"][name].get("legacy") is True
    assert "run" in raw["variants"]["hr"]["disciplines"]
    # every running session carries its 10k equivalent: score - shape_at + the curve at 10 km that day
    days = {r["date"]: r for r in raw["predictor"]["daily"]}
    s = next(s for s in reversed(raw["sessions"]) if s["discipline"] == "interval" and s["kind"] == "hard" and s["date"] in days)
    r = days[s["date"]]
    assert s["score_10k"] == pytest.approx(s["score"] - s["shape_at"] + r["theta"] + r["slope"], abs=0.05)
    # the chart's 10k line is the run series
    ch = client.get("/api/fitness/predictor").json()
    assert ch["daily"][-1]["vdot_10k"] == pytest.approx(run["weekly"][-1]["mean"], abs=0.02)
    assert "distance_curve" not in ch


@needs_pred
def test_session_shape_at_its_own_distance(client):
    """a running session is compared with the curve at its own distance; "now" is the run number"""
    raw = json.loads((DATA / "fitness.json").read_text())
    s = next(s for s in reversed(raw["sessions"]) if s["discipline"] == "interval" and s["kind"] == "hard" and s.get("km"))
    r = client.get(f"/api/fitness/session/{s['id']}").json()
    x = next(x for x in r["sessions"] if x["discipline"] == "interval")
    assert x["shape"]["mean"] == pytest.approx(s["shape_at"], abs=0.01) and x["shape"]["km"] == s["km"]
    assert x["current"]["mean"] == raw["disciplines"]["run"]["current"]["mean"]
    assert x["label"].startswith("Run fitness")
    # the session's own race equivalents follow the curve: at 5 km (its distance) it is the plain Daniels time
    assert abs(x["race_equivalents_s"]["5k"] - fm.race_time(s["score"], 5000)) <= 1


def test_no_distance_curve_setting(client):
    assert client.get("/api/settings").status_code == 404
    d = client.get("/api/fitness/predict").json() if (DATA / "fitness.json").exists() else {}
    assert "distance_curve" not in d and "curve_slope" not in d.get("shape", {})
    assert fm._model([], __import__("datetime").date(2026, 1, 1)) == ({}, None, None)

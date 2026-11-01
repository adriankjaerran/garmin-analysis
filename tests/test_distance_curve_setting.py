"""Per-person settings (scripts/person_settings.py, GET/PUT /api/settings) and the distance curve on / off switch:
flat predictions, the mean-reverting slope, and every place that follows the setting (predictor endpoints, /fitness,
/home, form deltas). The settings file is redirected to a temp folder, the real data/<person>/settings.json is
never touched."""

from __future__ import annotations

import json
import math

import pytest

import app.server  # noqa: F401  (puts scripts/ on sys.path)
import fitness_model as fm  # noqa: E402
import person_settings as ps  # noqa: E402

from .conftest import DATA, PERSON


@pytest.fixture
def settings_dir(tmp_path, monkeypatch):
    (tmp_path / PERSON).mkdir()
    monkeypatch.setattr(ps, "DATA", tmp_path)
    return tmp_path


# ---------------------------------------------------------------- math
def test_flat_prediction_is_theta_everywhere():
    for km in (3.0, 5.0, 10.0, 21.0975, 42.195):
        p = fm.curve_predict(52.0, 2.0, 1.0, 0.25, -0.1, km, [5.0, 21.0975], flat=True)
        assert p["vdot"] == 52.0 and p["sd_state"] == pytest.approx(1.0)
    # the extrapolation sd still applies outside the evidenced range
    m = fm.curve_predict(52.0, 2.0, 1.0, 0.25, -0.1, 42.195, [5.0, 21.0975], flat=True)
    assert m["extrapolated"] and m["sd"] == pytest.approx(math.sqrt(1 + fm.CURVE["race_sd"] ** 2 + fm.CURVE["extrap_sd_per_doubling"] ** 2))


def test_mean_reverting_slope_fades_without_evidence():
    """Evidence of slope +2 for 200 days, then nothing for 1000 days: the random-walk slope stays at +2, the
    mean-reverting one decays towards 0 and its sd towards the stationary sd."""
    obs = []
    for d in range(0, 200, 5):
        obs.append((d, 50.0, 1.0, "race", 0.0))
        obs.append((d + 2, 54.0, 1.0, "race", 2.0))
    kw = dict(q_slope=0.005, slope_sd=1.5)
    _, Mr, Cr, _, _ = fm.smooth_curve(obs, -7, 1200, revert=True, **kw)
    _, Mw, _, _, _ = fm.smooth_curve(obs, -7, 1200, revert=False, **kw)
    assert Mr[150, 1] == pytest.approx(2.0, abs=0.3)          # strong evidence: not shrunk much
    assert Mw[-1, 1] == pytest.approx(Mw[205, 1], abs=1e-6)   # random walk: flat forecast
    assert 0 < Mr[-1, 1] < 0.5 * Mr[205, 1]                   # AR(1): decays towards 0
    assert math.sqrt(Cr[-1, 1, 1]) < 1.5 + 1e-6
    assert fm.CURVE["slope_revert"] and fm.CURVE["slope_prior_sd"] ** 2 > fm.CURVE["q_slope"]


# ---------------------------------------------------------------- settings file
def test_settings_defaults_and_validation(settings_dir):
    assert ps.load(PERSON) == {"distance_curve": True}
    assert ps.save(PERSON, {"distance_curve": False}) == {"distance_curve": False}
    assert json.loads((settings_dir / PERSON / "settings.json").read_text()) == {"distance_curve": False}
    assert ps.distance_curve(PERSON) is False
    for bad in ({"distance_curve": "no"}, {"nope": True}, ["x"]):
        with pytest.raises(ValueError):
            ps.save(PERSON, bad)
    with pytest.raises(ValueError):
        ps.save("no-such-person", {"distance_curve": False})
    (settings_dir / PERSON / "settings.json").write_text("{broken")
    assert ps.load(PERSON) == {"distance_curve": True}


# ---------------------------------------------------------------- endpoints (real fitness.json)
needs_off = pytest.mark.skipif(not (DATA / "fitness.json").exists() or
                               not json.loads((DATA / "fitness.json").read_text()).get("curve_off"),
                               reason="fitness.json without curve_off (rebuild)")


def test_settings_endpoint(client, settings_dir):
    assert client.get("/api/settings").json()["distance_curve"] is True
    r = client.put("/api/settings", json={"distance_curve": False})
    assert r.status_code == 200 and r.json()["distance_curve"] is False
    assert client.get("/api/settings").json()["distance_curve"] is False
    assert client.put("/api/settings", json={"distance_curve": 1}).status_code == 422
    assert client.put("/api/settings", json={"color": "red"}).status_code == 422


@needs_off
def test_curve_off_everywhere(client, settings_dir):
    on_pred = client.get("/api/fitness/predict").json()
    on_fit = client.get("/api/fitness").json()
    on_home = client.get("/api/home").json()["fitness"]
    assert on_pred["distance_curve"] is True and on_fit["distance_curve"] is True
    assert client.put("/api/settings", json={"distance_curve": False}).status_code == 200

    d = client.get("/api/fitness/predict").json()
    theta = d["shape"]["theta"]
    assert d["distance_curve"] is False and d["shape"]["slope"] == 0 and "off" in d["shape"]["slope_text"]
    assert d["shape"]["curve_slope"] == on_pred["shape"]["curve_slope"]
    assert theta == on_pred["shape"]["theta"]
    assert all(p["vdot"] == pytest.approx(theta, abs=0.01) for p in d["predictions"])
    assert {"n_races", "n_tempos", "n_intervals"} <= set(d["basis"])

    ch = client.get("/api/fitness/predictor").json()
    assert ch["distance_curve"] is False
    row = ch["daily"][-1]
    assert row["vdot_5k"] == row["vdot_10k"] == row["vdot_half"] and row["sd_5k"] == row["sd_half"]

    f = client.get("/api/fitness").json()
    assert f["distance_curve"] is False and "curve_off" not in f
    iv, lg = f["disciplines"]["interval"]["current"], f["disciplines"]["long"]["current"]
    assert lg["mean"] == iv["mean"] and lg["sd"] == iv["sd"]
    rp = f["disciplines"]["overall"]["current"]["race_prediction"]
    assert rp["distance_curve"] is False and len(set(rp["vdot_by_distance"].values())) == 1
    eq = f["disciplines"]["overall"]["current"]["race_equivalents_s"]
    assert eq["10k"] == round(fm.race_time(rp["vdot"], 10000))
    if (f.get("variants") or {}).get("hr", {}).get("disciplines"):
        h = f["variants"]["hr"]["disciplines"]
        assert h["long"]["current"]["mean"] == h["interval"]["current"]["mean"]

    home = client.get("/api/home").json()["fitness"]
    assert home["long"]["mean"] == home["interval"]["mean"] and home["interval"]["mean"] == on_home["interval"]["mean"]

    # back on: the curve again
    client.put("/api/settings", json={"distance_curve": True})
    again = client.get("/api/fitness/predict").json()
    assert again["predictions"] == on_pred["predictions"]
    assert client.get("/api/fitness").json()["disciplines"]["long"]["current"]["mean"] == on_fit["disciplines"]["long"]["current"]["mean"]


@needs_off
def test_form_delta_follows_the_setting(settings_dir):
    import form_delta as fd
    on = fd.form_deltas(PERSON)
    s_on = fd.form_series(PERSON)
    ps.save(PERSON, {"distance_curve": False})
    off = fd.form_deltas(PERSON)
    assert on.keys() == off.keys()
    doc = json.loads((DATA / "fitness.json").read_text())
    long_ids = {str(s["id"]) for s in doc["sessions"] if s["discipline"] == "long" and s["kind"] in ("race", "hard")}
    changed = [i for i in long_ids & on.keys() if on[i]["shape"] is not None and on[i]["shape"] != off[i]["shape"]]
    if long_ids & on.keys():
        assert changed                         # long sessions are now compared with theta, not the curve at their km
    s_off = fd.form_series(PERSON)
    assert all(r["long"] == r["interval"] for r in s_off if r["long"] is not None)
    assert any(r["long"] != r["interval"] for r in s_on if r["long"] is not None)
    ps.save(PERSON, {"distance_curve": True})
    assert fd.form_deltas(PERSON) == on

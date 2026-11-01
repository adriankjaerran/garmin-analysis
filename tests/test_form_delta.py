"""Form delta (scripts/form_delta.py) on the real fitness.json files; skips without data."""

from __future__ import annotations

import datetime as dt
import json
import sys

import pytest

from .conftest import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
FA, FP, TJ = (ROOT / "data" / "adrian" / "fitness.json", ROOT / "data" / "patrik" / "fitness.json",
              ROOT / "data" / "together.json")
pytestmark = pytest.mark.skipif(not FA.exists(), reason="no data/adrian/fitness.json")

import form_delta as fd  # noqa: E402
import fitness_model as fm  # noqa: E402

KEYS = {"score", "shape", "delta", "discipline", "kind", "confidence", "text"}


def _sessions(path):
    return json.loads(path.read_text())["sessions"]


def test_obs_from_fitness_json_reproduce_the_model():
    """Rebuilding the evidence from fitness.json and smoothing it gives the stored shape_at (so the LOO refit is the
    same model minus one session)."""
    doc = json.loads(FA.read_text())
    rows = sorted([s for s in doc["sessions"] if s["discipline"] == "interval" and s["kind"] != "implausible"],
                  key=lambda s: s["date"])
    days = [dt.date.fromisoformat(s["date"]).toordinal() for s in rows]
    obs = [(d, s["score"], s["sigma"], s["kind"]) for d, s in zip(days, rows)]
    d0 = min(days) - 7
    _, m, _, _, _ = fm.smooth_series(obs, d0, max(days), doc["params"]["q_per_day"]["interval"])
    err = [abs(m[d - d0] - s["shape_at"]) for d, s in zip(days, rows)]
    assert max(err) < 0.1


def test_hard_session_has_delta_and_bound_has_none():
    ss = _sessions(FA)
    hard = next(s for s in reversed(ss) if s["kind"] == "hard" and s["discipline"] == "interval" and s["weight"] > 0.9)
    f = fd.form_delta("adrian", str(hard["id"]))
    assert f and KEYS <= set(f)
    assert f["delta"] == pytest.approx(f["score"] - f["shape"], abs=0.11)
    assert f["text"].endswith("vs form") and f["confidence"] in ("high", "medium", "low")
    ids_nonbound = {str(s["id"]) for s in ss if s["kind"] in ("race", "hard")}
    bound = next(s for s in ss if s["kind"] == "bound" and str(s["id"]) not in ids_nonbound)
    assert fd.form_delta("adrian", str(bound["id"])) is None
    assert fd.form_delta("adrian", "1") is None
    assert fd.form_delta("nobody", "1") is None


def test_reference_excludes_the_session():
    """A session above its stored shape_at has a larger delta than score - shape_at (it no longer pulls the shape)."""
    for s in _sessions(FA):
        if s["kind"] == "hard" and s["discipline"] == "interval" and s["score"] - s["shape_at"] > 2 and s["weight"] > 0.9:
            f = fd.form_delta("adrian", str(s["id"]))
            if f["discipline"] == "interval":
                assert f["delta"] > s["score"] - s["shape_at"] - 0.05
                return
    pytest.skip("no suitable session")


def test_deltas_plausible_overall():
    ds = [d for d in fd.form_deltas("adrian").values() if d["delta"] is not None and d["discipline"] == "interval"]
    assert len(ds) > 50
    big = [d for d in ds if abs(d["delta"]) > 8]
    assert len(big) <= len(ds) * 0.1
    assert all(d["text"] in ("score unreliable",) or "vs form" in d["text"] for d in ds)


def test_form_series_shape():
    s = fd.form_series("adrian")
    assert len(s) > 100 and [r["date"] for r in s] == sorted(r["date"] for r in s)
    last = s[-1]
    assert {"date", "interval", "long", "ski", "overall", "sd"} <= set(last)
    assert 30 < last["interval"] < 80
    assert fd.form_series("adrian", since=last["date"]) == [last]


@pytest.mark.skipif(not (TJ.exists() and FP.exists()), reason="no together.json / patrik")
def test_together_known_sessions():
    groups = {g["gid"]: g for g in fd.together_deltas()}
    g = groups.get("2026-10-20_2007_Bislet")        # adrian 15×400, patrik 10×400 (partial)
    if g is None:
        pytest.skip("group not in together.json")
    f = {p["person"]: p["form"] for p in g["participants"]}
    assert f["adrian"]["discipline"] == "interval" and abs(f["adrian"]["delta"]) < 6
    assert f["patrik"] is not None and f["patrik"]["delta"] is not None
    hike = next((g for g in groups.values() if g["label"] and g["workout"] == "Hike"), None)
    if hike:
        assert all(p["form"] is None for p in hike["participants"])
    race = groups.get("2023-05-07_1200_Lier-1")
    if race:
        fa = next(p["form"] for p in race["participants"] if p["person"] == "adrian")
        assert fa["kind"] == "race" and abs(fa["delta"]) < 3

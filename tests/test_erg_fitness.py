"""Erg fitness trend (scripts/erg_fitness.py): synthetic sessions always, the real build when data/<p>/concept2 exists."""

from __future__ import annotations

import datetime as dt
import math
import sys

import pytest

from .conftest import DATA, PERSON, ROOT

sys.path.insert(0, str(ROOT / "scripts"))
import erg_fitness as ef  # noqa: E402

C2 = DATA / "concept2" / "results.json"


def _iv(t_s, m, rest_s=0, typ="time"):
    return {"type": typ, "time": round(t_s * 10), "distance": m, "rest_time": round(rest_s * 10)}


def _res(rid, date, ivs=None, t_s=0, m=0, wt="JustRow", typ="rower"):
    r = {"id": rid, "date": f"{date} 12:00:00", "type": typ, "workout_type": wt,
         "time": round(t_s * 10) if not ivs else sum(x["time"] for x in ivs),
         "distance": m if not ivs else sum(x["distance"] for x in ivs)}
    r["workout"] = {"intervals": ivs} if ivs else {}
    return r


def test_pace_watts_and_pauls_law():
    assert ef.watts(120) == pytest.approx(202.5, abs=0.1)          # 2:00 /500 m ≈ 203 W (Concept2's table)
    assert ef.pace_from_watts(ef.watts(105.3)) == pytest.approx(105.3)
    assert ef.paul_2k(110, 2000) == 110
    assert ef.paul_2k(110, 4000) == pytest.approx(105)              # one doubling: 5 s /500 m faster at 2k
    assert ef.paul_2k(100, 500) == pytest.approx(110)
    assert ef.fmt_pace(108.26) == "1:48.3 /500 m"
    assert ef.fmt_pace(59.96, unit=False) == "1:00.0"


def test_intervals_score_on_time_weighted_work_watts():
    ivs = [_iv(120, 550, 30)] * 6 + [_iv(120, 500, 30)] * 6
    s = ef.score_result(_res(1, "2026-01-01", ivs, wt="FixedTimeInterval"))
    w = (ef.watts(120 / 550 * 500) + ef.watts(120 / 500 * 500)) / 2
    assert s["kind"] == "intervals" and s["n_reps"] == 12
    assert s["watts"] == pytest.approx(w, abs=0.1)
    assert s["rest_s"] == 330                                         # the rest after the last rep doesn't count
    f = 1440 / (1440 + 330)
    assert s["d_eff_s"] == pytest.approx(120 * 12 ** f, abs=0.1)     # between one rep (120 s) and all work (1440 s)
    assert 120 < s["d_eff_s"] < 1440
    assert s["pace_2k"] < s["pace"]                                   # longer than 2 km effective -> faster at 2k


def test_short_and_broken_sessions_are_ignored():
    assert ef.score_result(_res(2, "2026-01-01", t_s=40, m=200))["kind"] == "ignored"
    broken = [_iv(240, 539, 60), _iv(240, 0, 60), _iv(240, 0, 60)]
    assert ef.score_result(_res(3, "2026-01-01", broken, typ="skierg", wt="VariableInterval"))["kind"] == "ignored"
    assert ef.score_result({"id": 4, "type": "bike", "date": "2026-01-01 10:00:00"}) is None


def test_test_piece_promotion_and_warmups(monkeypatch):
    """a 2000 m piece at the trend is a test; a slow 2:30 piece the same day as a session is a warm-up"""
    rows = [_res(10 + k, (dt.date(2026, 1, 1) + dt.timedelta(days=3 * k)).isoformat(),
                 [_iv(120, 540, 30)] * 12, wt="VariableInterval") for k in range(10)]
    iv_2k = ef.score_result(rows[-1])["pace_2k"]
    test = _res(50, "2026-02-01", t_s=iv_2k * 4, m=2000, wt="FixedDistanceSplits")
    warm = _res(51, "2026-01-28", t_s=300, m=1100)
    monkeypatch.setattr(ef, "load_results", lambda person: rows + [test, warm])
    doc = ef.build("x", today=dt.date(2026, 2, 10))
    by = {s["id"]: s for s in doc["sessions"]}
    assert by["50"]["kind"] == "test" and by["51"]["kind"] == "warmup"
    row = doc["machines"]["row"]
    assert "skierg" not in doc["machines"]
    assert row["daily"][-1]["date"] == "2026-02-10" and row["current"]["date"] == "2026-02-10"
    assert row["current"]["pace_fast"] < row["current"]["pace"] < row["current"]["pace_slow"]
    pb = row["personal_bests"]
    assert pb["distances"][0]["m"] == 500 and any(d["m"] == 2000 and d["exact"] for d in pb["distances"])
    assert math.isclose(row["current"]["watts"], ef.watts(row["current"]["pace"]), rel_tol=1e-3)


@pytest.mark.skipif(not C2.exists(), reason=f"no {C2}")
def test_real_build_is_sane_and_deterministic():
    a = ef.build(PERSON, today=dt.date(2026, 11, 7))
    b = ef.build(PERSON, today=dt.date(2026, 11, 7))
    a.pop("built"), b.pop("built")
    assert a == b
    assert a["machines"]
    for d in a["machines"].values():
        c = d["current"]
        assert 80 < c["pace"] < 160 and 0 < c["sd"] < 10        # 2k-eq between 1:20 and 2:40 /500 m
        assert all(p["pace_fast"] <= p["pace"] <= p["pace_slow"] for p in d["daily"])
        assert d["personal_bests"]["best_2k_equivalent"]["id"]
    ids = [s["id"] for s in a["sessions"]]
    assert len(ids) == len(set(ids))

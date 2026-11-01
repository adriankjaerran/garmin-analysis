"""Session classification and rep consistency (polish audit 3: #1 Reps tab = label, #3 stale shape, #4 multisport
run, #5 short-stop "intervals", #6 easy vs steady; Frogner 2026-10-06 rep end). Unit tests of the rules plus
checks on the real data (skipped without it)."""

from __future__ import annotations

import json

import numpy as np
import pytest

from .conftest import DATA, ROOT

import app.server  # noqa: F401,E402  (puts scripts/ on sys.path)
import session_groups as sg  # noqa: E402


# ---------------------------------------------------------------- rules
def test_track_set_dists_follow_the_signature():
    # adrian 2026-10-06 Frogner: 3×1800 + 2×900 measured 1754 / 1762 / 1744 / 882 / 880 m (GPS short on the bends)
    t = [416.8, 403.2, 411.7, 187.2, 171.2]
    d = [1753.7, 1761.5, 1744.2, 882.4, 879.9]
    assert sg.track_set_dists(t, d) == [1800, 1800, 1800, 900, 900]
    assert [sg.track_set_m(x) for x in d] == [1600, 1600, 1600, 800, 800]   # the per-rep list alone was wrong
    reps = sg.snap_track_reps([{"time": a, "dist": b} for a, b in zip(t, d)])
    assert [r["dist"] for r in reps] == [1800, 1800, 1800, 900, 900] and reps[0]["gps_dist"] == 1753.7
    # 400 m GPS reps still snap to 400; a mixed session (> 3 groups) falls back to the per-rep list
    assert sg.track_set_dists([75] * 6, [412, 405, 398, 416, 409, 401]) == [400] * 6
    mixed_t, mixed_d = [60, 200, 75, 300, 100], [300, 1000, 400, 1400, 500]
    assert sg.track_set_dists(mixed_t, mixed_d) == [sg.track_set_m(x) for x in mixed_d]


def test_recovery_share():
    # patrik 2026-10-27: a steady 6.4 km split at three ~10 s stops -> 1 recovery of 4 breaks
    assert sg.recovery_share([255.1, 476.5, 213.2, 1301.9, 180.5], [60.75, 9.5, 11.75, 12.1]) == 0.25
    # 45/15 s: 15 s is a third of the rep, a real recovery
    assert sg.recovery_share([45] * 10, [15] * 9) == 1.0
    assert sg.recovery_share([200, 200], []) is None and sg.recovery_share([1, 2, 3], [5]) is None


def test_steady_or_easy():
    sref = 1000 / 330   # easy pace 5:30 /km
    HN = (214.0, 48.0)

    def run(pace_s, hr, mov=3000):
        return {"F": {"speed": 1000 / pace_s, "avg_hr": hr, "moving_s": mov}}
    assert sg.steady_or_easy(run(274, 167), sref, HN) == "steady"      # 2026-10-03: 4:34 /km at HR 167
    assert sg.steady_or_easy(run(284, 162), sref, HN) == "steady"      # 2026-10-10: 4:44 /km at HR 162
    assert sg.steady_or_easy(run(324, 132), sref, HN) == "easy"        # 2026-10-31: 5:24 /km at HR 132
    assert sg.steady_or_easy(run(340, 170), sref, HN) == "easy"        # high HR but slower than easy (heat, hills)
    assert sg.steady_or_easy(run(274, 167, mov=600), sref, HN) == "easy"   # < 15 min
    assert sg.steady_or_easy(run(274, 167), None, HN) == "easy"        # no easy reference


def test_multisport_family():
    assert sg.multisport_family([(1, 1391.7), (1, 2489.3)]) == "run"     # 2026-10-15: two running legs
    assert sg.multisport_family([(1, 1000), (2, 3000)]) is None          # run + bike (most of it on the bike: 75 %)
    assert sg.multisport_family([(2, 9000), (1, 1000)]) == "bike"
    assert sg.multisport_family([]) is None and sg.multisport_family(None) is None


def test_race_prediction_fades_a_stale_shape():
    import fitness_model as fm
    cur = lambda m, s: {"current": {"mean": m, "sd": s}}
    p = fm.race_prediction({"interval": cur(53.0, 1.0), "long": cur(58.4, 2.8)})
    assert p["weights"]["interval"] > 0.85 and 53.0 < p["vdot"] < 54.0
    assert p["sd"] > 1.0                                  # the two shapes disagree: the band widens
    assert p["range_10k_s"][0] < p["race_equivalents_s"]["10k"] < p["range_10k_s"][1]
    assert fm.race_prediction({"ski": cur(55, 1)}) is None


def test_merged_rep_stops_at_the_recovery():
    import together as tg
    D = np.concatenate([np.arange(0, 401) * 4.4, 1760 + np.arange(1, 201) * 0.3])   # 400 s at 4.4 m/s, then walking
    r = {"start": 0.0, "end": 400.0}
    assert not tg._kept_running(D, r, tg._t_at_dist(D, 0.0, 1800))   # the last 40 m are the walk: not a split rep
    D2 = np.concatenate([np.arange(0, 301) * 4.4, 1320 + np.arange(1, 201) * 4.0])  # slowed a bit, kept running
    assert tg._kept_running(D2, {"start": 0.0, "end": 300.0}, tg._t_at_dist(D2, 0.0, 1800))


# ---------------------------------------------------------------- the real data
def _groups(person):
    p = ROOT / "data" / person / "groups.json"
    if not p.exists():
        pytest.skip(f"no {p}")
    return json.loads(p.read_text())["activities"]


def test_groups_types_on_real_data():
    g = _groups("adrian")
    assert g["24737758736"]["sport"] == "run" and g["24737758736"]["type"].startswith("intervals")   # multisport run
    assert g["24589521760"]["type"] == "steady"                                                     # 2026-10-03
    assert g["24631175030"]["sig"] == "3×1800 + 2×900"
    assert sum(1 for a in g.values() if a["type"] == "ski_intervals" and a.get("rep_starts")) >= 18
    gp = _groups("patrik") if (ROOT / "data" / "patrik" / "groups.json").exists() else None
    if gp:
        assert not gp["24874001821"]["type"].startswith("intervals")                                # 2026-10-27 steady


def test_meta_reps_equal_groups_reps(client):
    """one rep list: the activity page's reps (series cache / indoor laps) = groups.json reps for every ski interval
    and Bislett indoor session"""
    from app.server import fitcache as fc
    g = _groups("adrian")
    sdir = DATA / "cache" / "series"
    n = 0
    for aid, a in g.items():
        if not fc.groups_rep_source(a) or not (sdir / f"{aid}.npz").exists():
            continue
        _, meta = fc.load_npz(sdir / f"{aid}.npz")
        if meta.get("reps_source") != "groups":
            pytest.skip("series cache not rebuilt yet (uv run garmin-app-cache adrian)")
        assert len(meta["reps"]) == len(a["reps"]), aid
        assert [round(r["time"]) for r in meta["reps"]] == [round(r[0]) for r in a["reps"]], aid
        n += 1
    assert n >= 18
    r = client.get("/api/activities/23925803195").json()     # 2026-08-10 ski 4×4:30
    assert len(r["reps"]) == 4
    f = client.get("/api/activities/24631175030").json()     # Frogner 3×1800 + 2×900: set distances
    assert [x["dist"] for x in f["reps"] if x["time"] > 100] == [1800, 1800, 1800, 900, 900]


def test_shape_staleness_and_prediction(client):
    d = client.get("/api/fitness").json()
    c = d["disciplines"]["long"]["current"]
    assert {"days_since_evidence", "stale", "last_evidence_date"} <= set(c)
    o = d["disciplines"]["overall"]["current"]
    p = o["race_prediction"]
    assert p and abs(sum(p["weights"].values()) - 1) < 0.01 and o["race_equivalents_s"] == p["race_equivalents_s"]
    h = client.get("/api/home").json()
    assert h["fitness"]["overall"]["race_equivalents_s"]["10k"] == p["race_equivalents_s"]["10k"]

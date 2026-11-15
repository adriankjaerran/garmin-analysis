"""Track sessions from where the reps happen, GPS track reps at their set distance, and user session corrections
(data/<person>/session_overrides.json). Unit tests of the rules plus checks on the real data (skipped without it)."""

from __future__ import annotations

import json

import numpy as np
import pytest

from .conftest import ROOT

import app.server  # noqa: F401,E402  (puts scripts/ on sys.path)
import fitness_model as fm  # noqa: E402
import session_groups as sg  # noqa: E402

BISLET_C = sg.TRACKS["Bislet"][0]


def _ana(starts, times, dists):
    return {"rep_starts": starts, "rep_times": times, "rep_dists": dists, "mode": "gps"}


# ---------------------------------------------------------------- where the reps happen
def _grid(T, where):
    """1 Hz grid with positions: where(t) -> (lat, lon) or None (no fix)"""
    pts = [where(t) for t in range(T + 1)]
    gps = np.array([p is not None for p in pts])
    lat = np.array([p[0] if p else np.nan for p in pts]); lon = np.array([p[1] if p else np.nan for p in pts])
    return {"T": T, "gps": gps, "lat": np.nan_to_num(lat), "lon": np.nan_to_num(lon)}


def test_track_from_rep_location_not_start():
    # start 900 m north (Marienlyst-ish), reps at s 600-1800 on the Bislett oval (60 m from its centre)
    far, oval = (BISLET_C[0] + 0.008, BISLET_C[1]), (BISLET_C[0] + 0.0005, BISLET_C[1])
    G = _grid(2400, lambda t: oval if 600 <= t < 1800 else far)
    spans = sg.track_spans(G)
    assert spans["Bislet"] == [[600, 1800]]
    reps = [(600 + 200 * k, 600 + 200 * k + 150) for k in range(6)]
    assert sg.track_of(spans, reps, sg.TRACK_REP_SHARE) == {"name": "Bislet", "share": 1.0}
    # the same run with its reps on the road: not a track session although it passes the oval
    assert sg.track_of(spans, [(0, 500), (1900, 2300)], sg.TRACK_REP_SHARE) is None
    # reps without GPS (indoors): location unknown -> {"name": None} (session_groups falls back to the start place)
    G2 = _grid(2400, lambda t: None if 600 <= t < 1800 else far)
    r = sg.track_of(sg.track_spans(G2), reps, sg.TRACK_REP_SHARE)
    assert r["name"] is None and r["gps"] == 0.0


def test_frogner_loop_is_a_track_but_not_an_oval():
    assert "Frogner stadion" in sg.TRACKS and "Frogner stadion" not in sg.OVALS
    # 1521-1775 m on the ~890 m Frogner loop: not 1600 (TRACK_SET_M is for 400 m ovals); 1754-1771 m: 1800
    assert sg.group_set_m([1775, 1685, 1708, 1521], oval=False) is None
    assert sg.group_set_m([1775, 1685, 1708, 1521], oval=True) == 1600
    assert sg.group_set_m([1754, 1762, 1744], oval=False) == 1800


def test_frogner_loop_counts_as_road_with_whole_laps():
    lap = sg.LOOPS["Frogner stadion"]
    assert lap == 890 and not sg.flat_track({"name": "Frogner stadion", "share": 1.0, "basis": "reps"})
    assert sg.flat_track({"name": "Bislet"}) and sg.flat_track({"name": "Øya stadion"}) and not sg.flat_track(None)
    # whole laps: 1 lap 847-897 m -> 890, 2 laps 1685-1803 m -> 1780; 1000 m reps there are no laps
    assert sg.group_set_m([877, 882], oval=False, lap=lap) == 890
    assert sg.group_set_m([1754, 1762, 1744], oval=False, lap=lap) == 1780
    assert sg.group_set_m([988, 1009, 1000, 1014], oval=False, lap=lap) is None
    assert sg.group_set_m([976, 950, 965, 943], oval=False, lap=lap) is None   # rise 2026-06-09: 6-11 % over a lap
    # rise 2026-05-19: the 1521 m rep of the 2-lap set (cut short / GPS) keeps its GPS distance
    ana = _ana([100, 700, 1300, 1900], [400, 390, 395, 360], [1775, 1685, 1708, 1521])
    assert [(r[2], r[3]) for r in sg.track_reps(None, ana, oval=False, lap=lap)] == [(1775, 1780), (1685, 1780), (1708, 1780)]
    # scored as road: grade-adjusted, road σ (the reps still at whole laps through track_reps)
    it = lambda a: [e for e in fm.score_session(a, F_RUN) if e["discipline"] == "interval"][0]
    assert it(_act(track={"name": "Frogner stadion", "share": 1.0, "basis": "reps"}))["sigma"] == fm.SIG["int_road"]


def test_oya_is_an_oval_and_dubai_is_not_a_track():
    (la, lo), r = sg.TRACKS["Øya stadion"]
    assert "Øya stadion" in sg.OVALS and abs(la - 63.4265) < 0.001 and abs(lo - 10.3802) < 0.001 and r <= 130
    dubai = (25.2149, 55.2743)
    assert all(sg.hav(c, dubai) > 1000 for c, _ in sg.TRACKS.values())


# ---------------------------------------------------------------- set distances
def test_patrik_1000s_from_lap_presses():
    # patrik 2026-11-03 at Bislett, GPS mode: 6 manual laps of ~240 s at 921-969 m; the detector split two of them
    laps = [[0.0, 422.8, 587, None, None, 0, None], [422.0, 325.2, 1000, None, None, 2, None],
            [748.0, 253.7, 195, None, None, 0, None], [1001.0, 245.2, 953, None, None, 0, None],
            [1246.0, 97.2, 82, None, None, 0, None], [1343.0, 239.9, 955, None, None, 0, None],
            [1583.0, 105.6, 64, None, None, 0, None], [1689.0, 240.3, 921, None, None, 0, None],
            [1929.0, 105.1, 67, None, None, 0, None], [2034.0, 237.2, 944, None, None, 0, None],
            [2271.0, 109.0, 47, None, None, 0, None], [2380.0, 238.1, 938, None, None, 0, None],
            [2618.0, 95.6, 50, None, None, 0, None], [2714.0, 215.6, 969, None, None, 0, None],
            [2929.0, 671.1, 874, None, None, 0, None]]
    ana = _ana([80.7, 418.8, 1000.7, 1343.0, 1519.8, 1689.7, 1822.3, 2033.8, 2380.4, 2715.0],
               [29.6, 37.4, 248.0, 165.9, 65.9, 117.1, 81.0, 239.1, 236.8, 215.8],
               [103, 129, 978, 675, 262, 467, 318, 954, 942, 982])
    w = sg.lap_rep_windows(laps, ana)
    assert [round(x[2]) for x in w] == [953, 955, 921, 944, 938, 969]
    tr = sg.track_reps(laps, ana)
    assert [r[3] for r in tr] == [1000] * 6 and [r[2] for r in tr] == [953, 955, 921, 944, 938, 969]


def test_rise_400s_skip_the_recovery_lap():
    # rise 2026-10-20: 10 manual laps of ~82 s at 411-467 m; a 50 s recovery lap the detector ran into a rep
    work = [(1700.0, 81.2, 411), (1827.0, 83.5, 419), (1960.0, 82.7, 416), (2088.0, 82.3, 429), (2217.0, 82.5, 418),
            (2414.0, 82.3, 451), (2546.0, 83.4, 433), (2680.0, 83.5, 419), (2814.0, 83.3, 438), (2950.0, 81.6, 467)]
    laps = [[t, d, m, None, None, 0, None] for t, d, m in work] + [[2630.0, 50.2, 145, None, None, 0, None]]
    laps.sort()
    ana = _ana([t for t, _, _ in work[:7]] + [2630.0, 2665.8, 2795.8, 2950.6],
               [d for _, d, _ in work[:7]] + [40.0, 100.2, 115.5, 100.0], [m for _, _, m in work[:7]] + [140, 481, 541, 539])
    tr = sg.track_reps(laps, ana)
    assert len(tr) == 10 and {r[3] for r in tr} == {400}


def test_no_snap_when_the_set_distance_is_unclear():
    # GPS partly lost under the stands (rise 2026-03-17): ~105 s reps measured 173-595 m
    ana = _ana([100 + 230 * k for k in range(6)], [104, 107, 100, 106, 105, 102], [494, 173, 450, 520, 595, 485])
    assert sg.track_reps(None, ana) == []
    # a 277 s and a 186 s rep grouped by distance (1106 / 953 m) are not one set distance
    assert sg.track_reps(None, _ana([0, 500], [277, 186], [1106, 953])) == []
    # time-triggered workout laps are time reps (no set distance)
    laps = [[100 + 200 * k, 120.0, 410 + k, None, None, 1, "active"] for k in range(5)]
    ana = _ana([100 + 200 * k for k in range(5)], [120.0] * 5, [410 + k for k in range(5)])
    assert sg.lap_rep_windows(laps, ana) is None


def test_track_rep_grid_scales_the_reps_only():
    T = 300
    v = np.full(T + 1, 2.0); v[100:200] = 4.6   # 460 m measured for a 400 m rep
    G = {"T": T, "v": v.copy(), "d": np.concatenate([[0.0], np.cumsum(v[:-1])])}
    n = sg.track_rep_grid(G, {"track_reps": [[100, 100, 460, 400]]})
    assert n == 1
    assert G["v"][150] == pytest.approx(4.0) and G["v"][50] == 2.0 and G["v"][250] == 2.0
    assert G["d"][200] - G["d"][100] == pytest.approx(400) and G["d"][100] == pytest.approx(200)
    # Bislett indoor sessions keep their own lap grid
    G2 = {"T": T, "v": v.copy(), "d": np.concatenate([[0.0], np.cumsum(v[:-1])])}
    assert sg.track_rep_grid(G2, {"track_reps": [[100, 100, 460, 400]], "indoor_bislett": {"rep_m": [539]}}) == 0


# ---------------------------------------------------------------- scoring: track σ and user overrides
def _act(**kw):
    a = {"type": "intervals_road", "sport": "run", "dist_trusted": True, "atype": "running", "reps": [[240, 1000, 4.2, 170]] * 6,
         "breaks_s": [100] * 5, "sig": "6×1000"}
    a.update(kw)
    return a


F_RUN = {"wbal": {str(k): 50.0 for k in fm.TAU_VARIANTS}, "wbal_flat": 50.0, "moving_s": 3000, "dist_m": 9000}


def test_track_sigma_from_track_field_not_start_place():
    it = lambda a: [e for e in fm.score_session(a, F_RUN) if e["discipline"] == "interval"][0]
    assert it(_act(place="Bislet"))["sigma"] == fm.SIG["int_road"]                  # start place alone: road
    assert it(_act(track={"name": "Bislet", "share": 1.0, "basis": "reps"}))["sigma"] == fm.SIG["int_track"]


def test_override_exclude_and_bound():
    assert fm.score_session(_act(override={"fitness": "exclude"}), F_RUN) == []
    ev = fm.score_session(_act(override={"fitness": "bound"}), F_RUN)
    iv = [e for e in ev if e["discipline"] == "interval"][0]
    assert iv["kind"] == "bound" and iv["sigma"] == fm.SIG["cont_bound"] and iv["components"]["override"] == "bound"
    plain = [e for e in fm.score_session(_act(), F_RUN) if e["discipline"] == "interval"][0]
    assert plain["kind"] == "hard" and plain["score"] == iv["score"]


# ---------------------------------------------------------------- real data
def _groups(person):
    p = ROOT / "data" / person / "groups.json"
    if not p.exists():
        pytest.skip(f"no {p}")
    return json.loads(p.read_text())["activities"]


def test_real_track_sessions():
    gp = _groups("patrik")
    a = gp["24956165417"]   # 2026-11-03 6×1000 at Bislett, start St. Hanshaugen
    assert a["type"] == "intervals_track" and a["track"]["name"] == "Bislet" and a["place"] != "Bislet"
    assert [r[3] for r in a["track_reps"]] == [1000] * 6
    f = gp["24632019253"]   # 2026-10-06 reps on the Frogner loop: road (grade-adjusted), reps at whole laps
    assert f["track"]["name"] == "Frogner stadion" and f["type"] == "intervals_road"
    assert sorted({r[3] for r in f["track_reps"]}) == [890, 1780]
    gr = _groups("rise")
    r = gr["24795023415"]   # 2026-10-20 10×400 at Bislett, start Marienlyst
    assert r["type"] == "intervals_track" and r["track"]["name"] == "Bislet" and len(r["track_reps"]) == 10
    ga = _groups("adrian")
    assert not ga["14901660080"].get("track")   # 2024-04-14: starts at Bislett, reps 2.5 km away on the road
    assert ga["24874084265"]["track"]["name"] == "Bislet" and "track_reps" not in ga["24874084265"]   # watch track mode
    # Øya stadion (Trondheim): 13 sessions; 2022-06-06 8×1000 GPS reps (1017-1041 m) at 1000 m
    oya = [a for a in ga.values() if (a.get("track") or {}).get("name") == "Øya stadion" and a["type"] == "intervals_track"]
    assert len(oya) >= 12
    assert ga["8965670930"]["type"] == "intervals_track" and [r[3] for r in ga["8965670930"]["track_reps"]] == [1000] * 8


def test_real_patrik_overrides():
    p = ROOT / "data" / "patrik" / "session_overrides.json"
    if not p.exists():
        pytest.skip("no patrik overrides")
    gp = _groups("patrik")
    a = gp["24444367861"]   # 2026-09-10 easy run + bike / vehicle tail
    assert a["type"] == "easy" and a["override"]["fitness"] == "exclude" and a["override"]["type_from"].startswith("intervals")
    assert gp["21445453879"]["type"] == "easy"
    assert gp["19082386807"]["type"] == json.loads(p.read_text())["19082386807"]["type"]
    assert gp["21445429328"]["override"] == {"fitness": "bound", "note": json.loads(p.read_text())["21445429328"]["note"]}
    fj = ROOT / "data" / "patrik" / "fitness.json"
    if fj.exists():
        ids = {str(s.get("id")) for s in json.loads(fj.read_text()).get("sessions", [])}
        assert "24444367861" not in ids


def test_level_alt_removes_barometer_drift_on_closed_loops():
    # 3 laps of a flat ~1000 m loop (200 s each, 100 s standing between): the barometer climbs 20 m per lap and sinks
    # back while standing (adrian's FR935 2024-04-14); levelled, every lap closes within a few metres
    lat0, lon0, R = 59.94, 10.75, 1000 / (2 * np.pi)
    ts, alt, la, lo, v = [], [], [], [], []
    t = 0
    for lap in range(3):
        for k in range(200):
            ang = 2 * np.pi * k / 200
            la.append(lat0 + R * np.sin(ang) / 111195.0); lo.append(lon0 + R * (1 - np.cos(ang)) / (111195.0 * np.cos(np.radians(lat0))))
            alt.append(100 + 20 * k / 200); v.append(5.0)
        for k in range(100):
            la.append(lat0); lo.append(lon0); alt.append(120 - 20 * k / 100); v.append(0.0)
    T = len(alt) - 1
    G = {"T": T, "alt": np.array(alt), "lat": np.array(la), "lon": np.array(lo), "gps": np.ones(T + 1, bool),
         "v": np.array(v), "d": np.concatenate([[0.0], np.cumsum(v[:-1])])}
    out, info = fm.level_alt(G)
    assert info and info["pairs"] > 0
    for lap in range(3):
        a, b = 300 * lap, 300 * lap + 199
        assert abs(G["alt"][b] - G["alt"][a]) > 19 and abs(out[b] - out[a]) < 5
    # no revisits (straight line): unchanged
    G2 = {**G, "lat": lat0 + np.arange(T + 1) * 5 / 111195.0, "lon": np.full(T + 1, lon0)}
    out2, _ = fm.level_alt(G2)
    assert np.allclose(out2, G2["alt"])

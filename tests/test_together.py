"""Together API (scripts/together.py + app/server/routers/together.py) on the real adrian x patrik data.
Skips when data/together.json (uv run scripts/together.py build) or a second person is missing."""

from __future__ import annotations

import itertools

import pytest

from .conftest import ROOT

TJ = ROOT / "data" / "together.json"
pytestmark = pytest.mark.skipif(not TJ.exists() or not (ROOT / "data" / "patrik" / "activities_index.json").exists(),
                                reason="no data/together.json or no second person")
B400 = "2026-10-20_2007_Bislet"     # adrian 15×400 vs patrik 10×400
NEAR = "2026-10-27_2004_Bislet"     # adrian 6×1000 while patrik ran steady: near-miss, hidden


def _gids(r):
    return [g["gid"] for g in r.json()["groups"]]


def test_people_keeps_person_folders_only(client):
    r = client.get("/api/people").json()
    assert {"adrian", "patrik"} <= set(r["people"])
    assert "_together" not in r["people"] and "together.json" not in r["people"]
    for p in r["people"]:
        i = r["info"][p]
        assert i["name"] and i["initials"] and i["colour"].startswith("#") and i["colour_dark"].startswith("#")
    assert r["info"]["adrian"]["colour"] != r["info"]["patrik"]["colour"]


@pytest.mark.parametrize("prefix", ["/api", "/api/adrian", "/api/patrik"])
def test_list_under_any_prefix(client, prefix):
    r = client.get(prefix + "/together")
    assert r.status_code == 200, r.text[:300]
    d = r.json()
    assert {"n", "groups", "h2h", "people", "viewer"} <= set(d)
    assert d["viewer"] == (prefix.split("/")[2] if prefix != "/api" else None)
    assert B400 in _gids(r) and NEAR not in _gids(r)   # near-misses hidden by default
    row = next(g for g in d["groups"] if g["gid"] == B400)
    assert row["label"] == "same_workout" and row["kind"] == "intervals" and set(row["people"]) == {"adrian", "patrik"}
    assert row["headline"]["winner"] == "adrian" and row["headline"]["rep_wins"] == {"adrian": 10, "patrik": 0}
    assert {(p["person"], p["reps_done"], p["of"]) for p in row["participants"]} == {("adrian", 15, 15), ("patrik", 10, 15)}
    h = next(x for x in d["h2h"] if {x["a"], x["b"]} == {"adrian", "patrik"})
    assert h["shared_reps"] >= 10 and h["sessions"] >= h["interval_sessions"] >= 1


def test_list_filters(client):
    assert NEAR in _gids(client.get("/api/together?near=1"))
    assert _gids(client.get("/api/together?label=same_race")) == ["2023-05-07_1200_Lier-1"]
    assert all(g["kind"] == "intervals" for g in client.get("/api/together?kind=intervals").json()["groups"])
    assert _gids(client.get("/api/together?from=2026-10-20&to=2026-10-20")) == [B400]
    assert _gids(client.get("/api/together?place=Frogner")) == ["2026-10-06_1911_Frogner-stadion"]
    assert B400 in _gids(client.get("/api/together?q=bislett+400"))
    assert all("hike" in g["sports"] for g in client.get("/api/together?sport=hike").json()["groups"])


def test_session_intervals(client):
    r = client.get(f"/api/patrik/together/{B400}")
    assert r.status_code == 200, r.text[:300]
    s = r.json()
    assert s["viewer"] == "patrik" and s["kind"] == "intervals"
    iv = s["intervals"]
    assert len(iv["slots"]) == 15 and len(iv["shared_slots"]) == 10
    assert all(sl["target"] == {"kind": "dist", "value": 400, "text": "400 m"} for sl in iv["slots"])
    lb = {x["person"]: x for x in iv["leaderboard"]}
    assert lb["adrian"]["rank"] == 1 and lb["adrian"]["rep_wins"] == 10
    # totals over the shared reps = sum of the per-rep results; cumulative ends at the total gap
    for p, x in lb.items():
        tot = sum(iv["reps"][p][i]["result_s"] for i in iv["shared_slots"])
        assert abs(tot - x["total_s"]) < 1.0
    assert abs(iv["cumulative"]["people"]["patrik"][-1]["gap_s"] - lb["patrik"]["gap_s"]) < 0.2
    assert 60 < lb["patrik"]["gap_s"] < 100       # 14:10.7 vs 12:52.3
    for st in iv["standings"]:
        o = st["order"]
        assert not o or (o[0]["rank"] == 1 and o[0]["gap_s"] == 0 and all(a["result_s"] <= b["result_s"] for a, b in itertools.pairwise(o)))
    for p in ("adrian", "patrik"):
        for c in iv["reps"][p][:10]:
            assert 60 < c["result_s"] < 100       # 400 m reps
    cu = iv["rep_closeups"][0]
    assert cu["available"] and cu["leader"] == "adrian"
    for align in ("own", "clock"):
        beh = [v for v in cu[align]["people"]["patrik"]["behind_m"] if v is not None]
        assert 20 < max(beh) < 120 and len(cu[align]["people"]["patrik"]["d_m"]) == cu[align]["n"]
    assert iv["breaks"]["adrian"] and "drop_30" in iv["breaks"]["adrian"][0]
    parts = {p["person"]: p for p in s["participants"]}
    assert parts["patrik"]["hr_norm"]["max"] and parts["patrik"]["display"]["colour"]
    assert any(c["kind"] == "sampling" for c in s["caveats"])


def test_session_race_and_together(client):
    race = client.get("/api/together/2023-05-07_1200_Lier-1").json()
    assert race["kind"] == "race" and race["continuous"]["race"][0]["person"] == "adrian"
    assert 2300 < race["continuous"]["race"][0]["time_s"] < 2400                    # 39:08
    easy = client.get("/api/together/2026-09-10_1841_Bislet").json()
    ap = easy["continuous"]["apart"][0]
    assert ap["with_pct"] >= 90 and len(ap["t_s"]) == len(ap["apart_m"]) == len(ap["with"])
    gd = easy["continuous"]["gap_over_distance"]
    assert all(len(v) == len(gd["x_m"]) for v in gd["people"].values())


def test_replay(client):
    r = client.get(f"/api/adrian/together/{B400}/replay?step_s=2")
    assert r.status_code == 200
    d = r.json()
    assert set(d["people"]) == {"adrian", "patrik"} and len(d["reps"]) == 15
    for v in d["people"].values():
        assert len(v["t"]) == len(v["lat"]) == len(v["lon"]) > 500
        assert all(a < b for a, b in zip(v["t"], v["t"][1:]))


def test_replay_segments_and_passes(client):
    d = client.get(f"/api/together/{B400}/replay").json()
    assert d["segment_kind"] == "rep" and len(d["segments"]) == 15
    s1 = d["segments"][0]
    assert set(s1["people"]) == {"adrian", "patrik"} and s1["target_m"] == 400
    assert s1["total"]["adrian"]["rank"] == 1 and s1["moves"] == []          # adrian leads after every rep
    assert d["segments"][9]["total"]["patrik"]["gap_s"] == pytest.approx(78.4, abs=0.2)
    assert d["segments"][10]["total"] is None and len(d["segments"][10]["people"]) == 1
    assert d["passes"] == []                                                  # adrian ahead in every rep
    # 2026-10-06 (3 × 1800): patrik passes adrian late in rep 2 and takes the overall lead
    f = client.get("/api/together/2026-10-06_1911_Frogner-stadion/replay").json()
    r2 = next(x for x in f["segments"] if x["label"] == "2")
    assert [(p["who"], p["whom"]) for p in r2["passes"]["own"]] == [("patrik", "adrian")]
    assert 1700 < r2["passes"]["own"][0]["at_m"] <= 1800
    assert {"person": "patrik", "from_rank": 2, "to_rank": 1} in r2["moves"]
    assert all(p["t_s"] >= f["segments"][p["seg"]]["start_s"] for p in f["passes"])
    # continuous: per km along the shared route, route_m per person
    lier = client.get("/api/together/2023-05-07_1200_Lier-1/replay").json()
    assert lier["segment_kind"] == "km" and len(lier["segments"]) == 10
    assert all("route_m" in v for v in lier["people"].values())


def test_passes_hysteresis():
    import numpy as np
    import together as tg
    x = np.arange(0, 100, 1.0)
    a = x * 4.0
    jitter = a + np.where(np.arange(100) % 2, 1.5, -1.5)                    # side by side within 1.5 m: no pass
    assert tg._passes(x, {"a": a, "b": jitter}, "rep") == []
    b = np.where(x < 50, a + 10, a - 10)                                      # b leads by 10 m, then a by 10 m
    ps = tg._passes(x, {"a": a, "b": b}, "rep")
    assert [(p["who"], p["whom"], p["x_s"]) for p in ps] == [("a", "b", 50)]


def test_unknown_group_404(client):
    assert client.get("/api/together/1999-01-01_0000_Nowhere").status_code == 404
    assert client.get("/api/together/1999-01-01_0000_Nowhere/replay").status_code == 404


def test_activity_rows_link_to_group(client):
    a = client.get("/api/activities/24795195699").json()["activity"]
    assert a["together"]["gid"] == B400 and a["together"]["with"] == ["patrik"]
    p = client.get("/api/patrik/activities/24795299858").json()["activity"]
    assert p["together"]["gid"] == B400 and p["together"]["with"] == ["adrian"]
    assert client.get("/api/activities/24874084265").json()["activity"]["together"] is None   # near-miss: no link


def test_leaderboard_all_reps_and_suspect(client):
    lb = {x["person"]: x for x in client.get(f"/api/together/{B400}").json()["intervals"]["leaderboard"]}
    a = lb["adrian"]
    assert a["all_n"] == 15 and a["all_total_s"] > a["total_s"] + 4 * 60   # all 15 reps, not the 10 shared ones
    assert "15" in a["suspect"] and a["best"]["label"] != "15"            # 64.5 s for 400 m: flagged, not "best"
    assert 150 < a["pace_s_km"] < 260 and a["all_pace_s_km"]
    assert lb["patrik"]["all_total_s"] == lb["patrik"]["total_s"]


def test_hike_group_sport_place_and_gap():
    import together as tg
    g = tg.find("2026-06-29_0824_Weinan-1")
    assert g["sport"] == "hike" and g["kind"] == "hike" and g["workout"] == "Hike"   # patrik's untyped "run" loses
    assert tg._is_latin(g["place"]["name"]) and g["place"].get("name_local")
    c = tg.session(g["gid"])["continuous"]
    gaps = [v for vs in c["gap_over_distance"]["people"].values() for v in vs if v is not None]
    assert max(gaps) < 600 and c["clock"] == "moving"                              # no joint-stop / projection spikes


def test_race_splits_cover_the_race():
    import together as tg
    c = tg.session("2023-05-07_1200_Lier-1")["continuous"]
    assert len(c["km_splits"]) == 10 and all(len(r["people"]) == 2 for r in c["km_splits"])

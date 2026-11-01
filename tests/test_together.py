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
    assert {"n", "groups", "people", "viewer"} <= set(d) and "h2h" not in d
    assert d["viewer"] == (prefix.split("/")[2] if prefix != "/api" else None)
    assert B400 in _gids(r) and NEAR not in _gids(r)   # near-misses hidden by default
    row = next(g for g in d["groups"] if g["gid"] == B400)
    assert row["label"] == "same_workout" and row["kind"] == "intervals" and set(row["people"]) == {"adrian", "patrik"}
    # a neutral headline: workout · members · fastest rep, no win counts
    h = row["headline"]
    assert h["text"] == "15×400 · 2 members · fastest rep 1:15.6" and "winner" not in h and "rep_wins" not in h
    assert {(p["person"], p["reps_done"], p["of"]) for p in row["participants"]} == {("adrian", 15, 15), ("patrik", 10, 15)}


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
    assert len(iv["slots"]) == 15 and "shared_slots" not in iv and all("shared" not in sl for sl in iv["slots"])
    assert all(sl["target"] == {"kind": "dist", "value": 400, "text": "400 m"} for sl in iv["slots"])
    lb = {x["person"]: x for x in iv["leaderboard"]}
    assert lb["adrian"]["rank"] == 1 and lb["patrik"]["rank"] == 2
    assert not {"rep_wins", "rep_ties", "gap_s", "all_n"} & set(lb["adrian"])
    # each person's own time so far: every full rep, also the ones only they ran (patrik stops after 10)
    cu = iv["cumulative"]["people"]
    assert len(cu["adrian"]) == 15 and cu["adrian"][-1]["n"] == 15 and cu["patrik"][9]["n"] == 10 and cu["patrik"][10] is None
    assert cu["adrian"][-1]["total_s"] == pytest.approx(sum(c["result_s"] for c in iv["reps"]["adrian"]), abs=0.5)
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
    assert s1["total"]["adrian"]["rank"] == 1 and s1["moves"] == []          # same reps so far: ranked, adrian ahead
    assert d["segments"][9]["total"]["patrik"]["gap_s"] == pytest.approx(78.4, abs=0.2)
    # rep 11 only adrian ran: his own time so far (11 reps), not a race
    t11 = d["segments"][10]["total"]
    assert set(t11) == {"adrian"} and t11["adrian"]["n"] == 11 and len(d["segments"][10]["people"]) == 1
    assert d["passes"] == []                                                  # adrian ahead in every rep
    # 2026-10-06 (3 × 1800 + 2 × 900): adrian's GPS measured his 1800s at 1744-1762 m; the rep ends at his lap press
    # (6:43 for rep 2), not 46 m further into the walking recovery (9:48, which let patrik "pass" him at 1780 m)
    f = client.get("/api/together/2026-10-06_1911_Frogner-stadion/replay").json()
    r2 = next(x for x in f["segments"] if x["label"] == "2")
    assert r2["passes"]["own"] == []
    # patrik's two easy "reps" before rep 1 (only he ran them) are his warm-up: not in the totals, so after rep 2 both
    # have 2 reps and the totals so far are ranked again
    assert [x["label"] for x in f["segments"][:2]] == ["WU1", "WU2"] and f["segments"][0]["total"] is None
    assert r2["moves"] == [] and r2["total"]["patrik"]["n"] == 2 and r2["total"]["adrian"]["rank"] == 1
    assert all(p["t_s"] >= f["segments"][p["seg"]]["start_s"] for p in f["passes"])
    # continuous: per km along the shared route, route_m per person
    lier = client.get("/api/together/2023-05-07_1200_Lier-1/replay").json()
    assert lier["segment_kind"] == "km" and len(lier["segments"]) == 10
    assert all("route_m" in v for v in lier["people"].values())


def test_warmup_reps(client):
    """a rep only one person ran, outside the main set, clearly easy for them (slower and lower effort than their main
    reps) is a warm-up: shown, not counted. Hard reps someone ran alone after the others stopped still count."""
    f = client.get("/api/together/2026-10-06_1911_Frogner-stadion").json()["intervals"]
    wu = [sl for sl in f["slots"] if sl["warmup"]]
    assert [(sl["label"], sl["target"]["value"], sl["warmup"]) for sl in wu] == [("WU1", 250, "warm-up"), ("WU2", 600, "warm-up")]
    assert all(c["warmup"] == "warm-up" and "effort" in c["warmup_reason"] for c in f["reps"]["patrik"][:2])
    p = next(x for x in f["leaderboard"] if x["person"] == "patrik")
    assert p["reps_done"] == 5 and p["n_warmup"] == 2 and p["extra_reps"] == 0
    assert {t["value"] for t in p["by_target"]} == {900, 1800}
    b = client.get(f"/api/together/{B400}").json()["intervals"]
    assert not any(sl["warmup"] for sl in b["slots"])                      # adrian's reps 11-15 alone: hard, they count
    assert next(x for x in b["leaderboard"] if x["person"] == "adrian")["reps_done"] == 15


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


def test_leaderboard_every_rep_counts(client):
    """no "reps everyone ran" subset: adrian's 15 and patrik's 10 reps all count; ranked by the distance-weighted pace
    over each person's own full reps (suspect ones left out of the averages); the splits' Avg is the same number"""
    iv = client.get(f"/api/together/{B400}").json()["intervals"]
    lb = {x["person"]: x for x in iv["leaderboard"]}
    a, p = lb["adrian"], lb["patrik"]
    assert a["reps_done"] == 15 and p["reps_done"] == 10
    assert "15" in a["suspect"] and a["n_counted"] == 14 and a["best"]["label"] != "15"   # 64.5 s for 400 m: flagged
    for x in (a, p):
        cells = [c for c in iv["reps"][x["person"]] if c and not c["partial"] and c["result_s"] and not c.get("suspect")]
        assert len(cells) == x["n_counted"]
        avg = sum(c["result_s"] for c in cells) / len(cells)
        assert x["avg_s"] == pytest.approx(avg, abs=0.06)                                   # = the splits' Avg row
        assert x["pace_s_km"] == pytest.approx(1000 * sum(c["result_s"] for c in cells) / (400 * len(cells)), abs=0.06)
        assert x["by_target"] == [{"kind": "dist", "value": 400, "text": "400 m", "n": len(cells), "avg_s": x["avg_s"],
                                   "best_s": min(c["result_s"] for c in cells)}]
    assert a["pace_s_km"] < p["pace_s_km"] and a["gap_s_km"] == 0 and p["gap_s_km"] == pytest.approx(p["pace_s_km"] - a["pace_s_km"], abs=0.11)
    # adrian's reps 11-14 (only he ran them) are in his average: it differs from the first 10 alone
    first10 = sum(c["result_s"] for c in iv["reps"]["adrian"][:10]) / 10
    assert abs(a["avg_s"] - first10) > 0.1


def test_every_rep_ranked_among_whoever_ran_it():
    """Frogner 2026-10-06: a slot two or more people ran is ranked among them; a slot one person ran stays unranked; the leaderboard
    ranks by own pace even when people ran different reps"""
    import together as tg
    s = tg.session("2026-10-06_1911_Frogner-stadion")
    iv = s["intervals"]
    for st in iv["standings"]:
        ran = [k for k, cells in iv["reps"].items() if cells[st["slot"]] and cells[st["slot"]]["result_s"] is not None]
        assert len(st["order"]) == (len(ran) if len(ran) >= 2 else 0)
    paces = [x["pace_s_km"] for x in iv["leaderboard"] if x["rank"] is not None]
    assert paces == sorted(paces) and [x["rank"] for x in iv["leaderboard"] if x["rank"]] == list(range(1, len(paces) + 1))
    assert "fastest rep" not in s["headline"]["text"] and s["headline"]["text"].startswith(s["group"]["workout"])


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

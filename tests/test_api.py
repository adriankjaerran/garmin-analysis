"""API smoke tests: every GET endpoint answers 200 on real data, plus a few invariants the UI relies on."""

from __future__ import annotations

import itertools
import json
import random
from urllib.parse import quote

import pytest

from .conftest import DATA, needs_data

pytestmark = needs_data

SIX_K = "6×1000 · rest ~2:30"     # the Bislett 6×1000 same-workout key


# ---- every list / overview endpoint answers 200 with the expected top-level shape
@pytest.mark.parametrize("path,keys", [
    ("/api/meta", {"person", "n_activities", "years", "sports"}),
    ("/api/activities", {"n", "activities"}),
    ("/api/activities?sport=run&q=6×1000", {"n", "activities"}),
    ("/api/activities?place=Bislet", {"n", "activities"}),
    ("/api/calendar", None),
    ("/api/map/tracks", None),
    ("/api/map/tracks?detail=hi&sport=ski", None),
    ("/api/places", {"n", "places", "areas"}),
    ("/api/places?touched=1", {"n", "places"}),
    ("/api/routes", {"n", "routes"}),
    ("/api/search", {"n", "rows", "families"}),
    ("/api/search?sport=run&rep_count=5-7&rep_len=1000m", {"n", "rows"}),
    ("/api/search/options", None),
    ("/api/groups?kind=place", {"kind", "groups"}),
    ("/api/groups?kind=start_place", {"kind", "groups"}),
    ("/api/groups?kind=region", {"kind", "groups"}),
    ("/api/groups?kind=route", {"kind", "groups"}),
    ("/api/groups?kind=family", {"kind", "groups"}),
    ("/api/groups?kind=signature", {"kind", "groups"}),
    ("/api/groups?kind=exact", {"kind", "groups"}),
    ("/api/groups?kind=workout", {"kind", "groups"}),
    ("/api/fitness", {"disciplines", "sessions"}),
    ("/api/fitness?discipline=interval&hr=1", {"disciplines", "sessions"}),
    ("/api/fitness/rebuild", {"running"}),
    ("/api/home", {"anchor", "latest", "latest_day", "volume", "days", "recent", "tracks", "fitness", "periods"}),
    ("/api/home?date=2025-06-15", {"anchor", "latest", "volume"}),
    ("/api/openapi.json", {"paths"}),
])
def test_get_endpoints(client, path, keys):
    r = client.get(path)
    assert r.status_code == 200, r.text[:300]
    if keys:
        assert keys <= set(r.json()), set(r.json())


def test_unknown_api_is_404(client):
    assert client.get("/api/nope").status_code == 404


def test_days(client, activities):
    last = activities[0]["date"]
    d = client.get(f"/api/days/{last}").json()
    assert {a["id"] for a in d["activities"]} >= {activities[0]["id"]}
    assert client.get("/api/days/2000-01-01").status_code == 200   # empty day still answers


def test_every_route_detail(client):
    for rt in client.get("/api/routes").json()["routes"]:
        r = client.get(f"/api/routes/{rt['id']}")
        assert r.status_code == 200, rt["id"]
        assert len(r.json()["members"]) == rt["n"]


# ---- per-activity endpoints on a sample
def _sample(activities, k=25):
    rnd = random.Random(7)
    return rnd.sample(activities, min(k, len(activities)))


def test_activity_endpoints_sample(client, activities):
    for a in _sample(activities):
        aid = a["id"]
        r = client.get(f"/api/activities/{aid}")
        assert r.status_code == 200, aid
        assert r.json()["activity"]["id"] == aid
        t = client.get(f"/api/activities/{aid}/track")
        if a.get("track"):
            assert t.status_code == 200, aid
            assert client.get(f"/api/activities/{aid}/track?format=geojson").json()["type"] == "Feature"
        else:
            assert t.status_code in (200, 404), aid   # no GPS: 404
        f = client.get(f"/api/fitness/session/{aid}")
        assert f.status_code == 200, aid
        if not f.json()["scored"]:
            assert f.json().get("not_scored", {}).get("text"), aid
        s = client.get(f"/api/compare/suggest?id={aid}")
        assert s.status_code in (200, 404), aid
        if s.status_code == 200:
            for st in s.json()["sets"]:
                assert str(st["ids"][0]) == aid
        n = client.get(f"/api/nearest/{aid}?k=5")
        assert n.status_code in (200, 404), aid


# ---- invariants the pages rely on
def test_bislett_6x1000_same_workout_group_is_17(client):
    groups = {g["key"]: g for g in client.get("/api/groups?kind=exact").json()["groups"]}
    assert groups[SIX_K]["n"] == 17
    d = client.get(f"/api/search?exact_key={quote(SIX_K)}&place=Bislet&place_mode=main").json()
    assert d["n"] == 17
    assert {r["place"] for r in d["rows"]} == {"Bislet"}


def test_r3_has_19_sessions(client, r3_ids):
    rt = client.get("/api/routes/R3").json()
    assert rt["n"] == 19 and len(rt["members"]) == 19
    assert len(r3_ids) == 19
    assert "Holmenkollen" in rt["name"]


def test_place_counts_consistent(client):
    places = {p["id"]: p for p in client.get("/api/places").json()["places"]}
    groups = {g["key"]: g for g in client.get("/api/groups?kind=place").json()["groups"]}
    b = places["Bislet"]
    assert b["name"] == "Bislett"
    assert b["n"] == b["n_main"] == groups["Bislet"]["n_main"]
    assert b["n_touched"] >= b["n_main"]


def test_route_names_single_source(client, activities):
    rows = {r["id"]: r for r in client.get("/api/routes").json()["routes"]}
    for a in activities:
        if a.get("route") in rows and a.get("route_name"):
            assert a["route_name"].startswith(a["route"]) or rows[a["route"]]["name"] in a["route_name"]


def test_compare_intervals_rep_starts_aligned(client):
    ids = client.get(f"/api/search?exact_key={quote(SIX_K)}&sort=-date&limit=6").json()["rows"]
    ids = [r["id"] for r in ids][:6]
    r = client.get(f"/api/compare?ids={','.join(ids)}&mode=intervals&tracks=0")
    assert r.status_code == 200, r.text[:300]
    d = r.json()
    x0 = d["axis"]["slot_x0"]
    assert len(x0) >= 6
    assert len(d["series"]) == len(ids)
    for sid, s in d["series"].items():
        starts = {}
        for x, ph, sl in zip(s["x"], s["phase"], s["slot"]):
            if ph is not None and abs(ph - 1) < 1e-6 and sl is not None and int(sl) not in starts:
                starts[int(sl)] = x
        assert starts, sid
        for k, x in starts.items():
            assert abs(x - x0[k]) <= 2.0, (sid, k, x, x0[k])   # every rep starts at its slot's x (1 s grid)


@pytest.mark.parametrize("mode", ["distance", "moving", "elapsed", "intervals", "breaks"])
def test_compare_modes_r3(client, r3_ids, mode):
    r = client.get(f"/api/compare?ids={','.join(r3_ids)}&mode={mode}")
    assert r.status_code == 200, r.text[:300]
    d = r.json()
    assert d["mode"] in (mode, "route_position")
    assert len(d["sessions"]) == 19
    assert all("hr_avg_moving" in s and "hr_avg_activity" in s for s in d["sessions"])


def _vals(a):
    return [v for v in a if v is not None]


def test_compare_distance_coverage(client, r3_ids):
    """distance mode: every session has coverage, uncovered stretches and the gap at the last common point;
    moving time never decreases along the route and no covered 250 m stretch is impossibly fast"""
    d = client.get(f"/api/compare?ids={','.join(r3_ids)}&mode=distance&tracks=0").json()
    assert d["axis"]["x"] == "route_m"
    for s in d["sessions"]:
        assert {"coverage_pct", "uncovered", "gap_end", "reversed"} <= set(s), s.keys()
        assert 0 <= s["coverage_pct"] <= 100
        ser = d["series"][str(s["id"])]
        mv = _vals(ser["moving_s"])
        assert all(b >= a - 0.5 for a, b in itertools.pairwise(mv))
        assert all(v < 60 for v in _vals(ser["speed_kmh"]))   # rollerski climb: covered bins only
        if s["id"] != d["ids"][0]:
            assert s["gap_end"] is not None and abs(s["gap_end"]["delta_moving_s"]) < s["moving_s"]


def test_compare_route_jump_and_lead_in(client):
    """R30 2026-05-22 (stood 19 min next to a later part of the loop: the old projection jumped 10 km, -2985 s)
    and R11 2023-11-26 (890 m lead-in to the route start: was +323 s within the first 20 m)"""
    A = {a["id"]: a for a in client.get("/api/activities").json()["activities"]}
    if not {"23176966603", "22967930226", "11266836943", "12910143619"} <= set(A):
        pytest.skip("sessions not in this person's data")
    d = client.get("/api/compare?ids=23176966603,22967930226&mode=distance&tracks=0").json()
    s = next(x for x in d["sessions"] if x["id"] == 22967930226)
    assert abs(s["gap_end"]["delta_moving_s"]) < 600, s["gap_end"]
    d = client.get("/api/compare?ids=11266836943,12910143619&mode=distance&tracks=0").json()
    ser = d["series"]["12910143619"]
    first = [(x, v) for x, v in zip(ser["x"], ser["delta_moving_s"]) if v is not None]
    assert all(abs(v) < 60 for x, v in first if x <= first[0][0] + 20), first[:4]


def test_compare_intervals_full_res_and_empty_slots(client):
    """intervals: reps at 1 s (never thinned); a session without a rep in a slot draws nothing in its rep area"""
    ids = ["8884710494", "8835446895"]   # 6×1000 vs 5×1000: slot 6 is empty for the second
    A = {a["id"] for a in client.get("/api/activities").json()["activities"]}
    if not set(ids) <= A:
        pytest.skip("sessions not in this person's data")
    d = client.get(f"/api/compare?ids={','.join(ids)}&mode=intervals&tracks=0").json()
    x0, w = d["axis"]["slot_x0"], d["axis"]["slot_width"]
    for sid, s in d["series"].items():
        has = {k for k, q in enumerate(d["reps"][sid]) if q}
        for k in range(len(x0)):
            xs = [x for x, ph, sl in zip(s["x"], s["phase"], s["slot"]) if ph == 1 and sl == k]
            if k in has:
                assert xs and max(b - a for a, b in itertools.pairwise(xs)) <= 1.01
            else:
                inside = [x for x, v in zip(s["x"], s["speed_kmh"]) if v is not None and x0[k] + 0.5 < x < x0[k] + w[k] - 0.5]
                assert not inside, (sid, k)


def test_compare_clock_jump_sessions_have_reps(client):
    """patrik's FIT files with a watch clock jump (9.4 M s / 1.9 M s): reps are drawn, elapsed time is real"""
    people = client.get("/api/people").json()["people"]
    if "patrik" not in people:
        pytest.skip("no patrik data")
    d = client.get("/api/patrik/compare?ids=23044028473,21445487763&mode=intervals&tracks=0").json()
    for s in d["sessions"]:
        assert s["elapsed_s"] < 4 * 3600, s
    for sid, s in d["series"].items():
        assert sum(1 for p in s["phase"] if p == 1) > 600, sid


def _need(client, ids):
    A = {a["id"] for a in client.get("/api/activities").json()["activities"]}
    if not set(ids) <= A:
        pytest.skip("sessions not in this person's data")


def test_compare_common_start_lead_in(client):
    """R11: 12910143619 jogged 890 m / 324 s to the usual start: cut at the line, table totals aligned (whole session
    in `whole`), distance ahead at 10 min about -235 m instead of -373 m; a parallel street (12069730602) and loops run
    the other way are excluded with a reason; `start` is part of the response cache key"""
    ids = ["11266836943", "12910143619", "12069730602", "9885837163"]
    _need(client, ids)
    own = client.get(f"/api/compare?ids={','.join(ids)}&mode=moving&tracks=0").json()
    assert "align" not in own and len(own["sessions"]) == 4
    d = client.get(f"/api/compare?ids={','.join(ids)}&mode=moving&start=ref&tracks=0").json()
    al = d["align"]
    assert al["applied"] and al["start"]["anchor_id"] == 11266836943 and al["finish"] is None
    q = al["sessions"]
    assert q["11266836943"]["status"] == "anchor"
    assert q["12910143619"]["status"] == "aligned" and abs(q["12910143619"]["offset_s"] - 324) < 10
    assert 850 < q["12910143619"]["offset_m"] < 930
    for i in ("12069730602", "9885837163"):
        assert q[i]["status"] == "excluded" and q[i]["reason"], q[i]
    assert {e["id"] for e in al["excluded"]} == {12069730602, 9885837163}
    assert set(d["activities"]) >= {"12069730602"}       # excluded chips still get their activity row
    assert d["ids"] == [11266836943, 12910143619]
    row = next(s for s in d["sessions"] if s["id"] == 12910143619)
    assert row["whole"]["dist_m"] - row["dist_m"] > 800
    ser = d["series"]["12910143619"]
    assert ser["x"][0] < 5 and ser["t"][0] >= 320          # x from the line, t = the file's elapsed time (map)
    k = min(range(len(ser["x"])), key=lambda j: abs(ser["x"][j] - 600))
    assert -300 < ser["ahead_m"][k] < -150, ser["ahead_m"][k]
    # distance mode: the route axis starts at the line, map time t is file-absolute
    d = client.get(f"/api/compare?ids={','.join(ids[:2])}&mode=distance&start=ref&tracks=0").json()
    ser = d["series"]["12910143619"]
    assert d["axis"]["origin_m"] is not None and ser["t"][0] - ser["elapsed_s"][0] >= 320
    # start_radius_m reaches the engine (the parallel street crosses the line 87 m from its point: still excluded)
    d = client.get(f"/api/compare?ids={','.join(ids[:3])}&mode=moving&start=ref&start_radius_m=80&tracks=0").json()
    assert "80 m" in d["align"]["sessions"]["12069730602"]["reason"]


def test_compare_common_start_anchor_and_late(client):
    """R3: the anchor 19475311436 started its watch at the km 1.86 stop: every other session is cut there, x = 0 at the
    line (route position 1840 m). R4: late joiners keep their join distance and start level with the reference.
    Intervals ignore the start line."""
    A = {a["id"]: a for a in client.get("/api/activities").json()["activities"]}
    if "19475311436" not in A:
        pytest.skip("sessions not in this person's data")
    r3 = [a["id"] for a in A.values() if a.get("route") == "R3"][:8]
    ids = ["19475311436"] + [i for i in r3 if i != "19475311436"][:6]
    d = client.get(f"/api/compare?ids={','.join(ids)}&mode=distance&start=19475311436&tracks=0").json()
    assert 1700 < d["align"]["start"]["route_m"] < 1950
    for sid, q in d["align"]["sessions"].items():
        assert q["status"] in ("anchor", "aligned"), (sid, q)
        if q["status"] == "aligned":
            assert q["offset_s"] > 300 and q["dist_to_line_m"] <= 40
    assert min(min(v for v in s["x"] if v is not None) for s in d["series"].values()) >= 0
    assert d["legs"][0]["from_m"] == 0
    r4 = ["14872637121", "4955905324", "4931666709", "6536647684"]
    _need(client, r4)
    d = client.get(f"/api/compare?ids={','.join(r4)}&mode=moving&start=ref&tracks=0").json()
    q = d["align"]["sessions"]
    assert q["4955905324"]["status"] == "late" and q["4955905324"]["join_m"] > 500
    assert d["align"]["late"] and q["6536647684"]["status"] == "aligned"
    ser = d["series"]["4955905324"]
    assert abs(ser["dist_m"][0] - q["4955905324"]["join_m"]) < 30 and abs(ser["ahead_m"][0]) <= 40
    assert ser["x"][0] > 200                                # enters at the reference's time at its join point
    d = client.get(f"/api/compare?ids={','.join(r4)}&mode=intervals&start=ref&tracks=0")
    if d.status_code == 200:
        assert d.json()["align"]["applied"] is False


def test_compare_common_start_reversed_and_bad_input(client):
    """R13 out & back: sessions that start on the anchor's path but run the route the other way are not late joiners"""
    ids = ["11869396323", "20212154644", "14326254571"]
    _need(client, ids)
    d = client.get(f"/api/compare?ids={','.join(ids)}&mode=moving&start=ref&tracks=0").json()
    for i in ids[1:]:
        assert d["align"]["sessions"][i]["status"] == "excluded", d["align"]["sessions"][i]
    assert client.get(f"/api/compare?ids={','.join(ids)}&mode=moving&start=nope").status_code == 422
    assert client.get(f"/api/compare?ids={','.join(ids)}&mode=moving&start=ref&start_radius_m=5").status_code == 422
    assert client.get(f"/api/compare?ids={','.join(ids)}&mode=moving&start=123456789").status_code == 422


def test_compare_rep1_start(client):
    """start=rep1: x = 0 at rep 1 (6×1000, 1×800 + 5×1000 = late from rep 2, easy run excluded), real breaks kept"""
    ids = ["24874084265", "24288925182", "8884710494", "24913137517"]
    _need(client, ids)
    iv = client.get(f"/api/compare?ids={','.join(ids[:3])}&mode=intervals&tracks=0").json()
    for mode in ("elapsed", "moving", "distance"):
        r = client.get(f"/api/compare?ids={','.join(ids)}&mode={mode}&start=rep1&tracks=0&max_points=5000")
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        al = d["align"]
        assert al["applied"] and al["start"]["kind"] == "rep1"
        q = al["sessions"]
        assert q["24874084265"]["status"] == "anchor" and q["8884710494"]["status"] == "aligned"
        assert q["24288925182"]["status"] == "late" and q["24288925182"]["slot"] == "2"
        assert q["24913137517"]["status"] == "excluded" and 24913137517 not in d["ids"]
        if mode == "distance":
            assert d["axis"]["x"] == "dist_m" and d["axis"]["t0"] == "rep1"
        for i in ("24874084265", "8884710494"):
            assert abs(d["series"][i]["x"][0]) <= 1, (mode, i, d["series"][i]["x"][:3])
        late = d["series"]["24288925182"]["x"][0]
        want = {"elapsed": q["24288925182"]["x_offset_s"], "moving": q["24288925182"]["x_offset_moving_s"],
                "distance": q["24288925182"]["join_m"]}[mode]
        assert abs(late - want) <= (20 if mode == "distance" else 1.5), (mode, late, want)
        if mode == "elapsed":   # real breaks: rep 2 at its own start - rep 1 start
            reps = [x for x in iv["reps"]["24874084265"] if x]
            x2 = reps[1]["start_s"] - reps[0]["start_s"]
            s = d["series"]["24874084265"]
            v = [sp for x, sp in zip(s["x"], s["speed_kmh"]) if x2 + 5 <= x < x2 + 15 and sp is not None]
            w = [sp for x, sp in zip(s["x"], s["speed_kmh"]) if x2 - 12 <= x < x2 - 3 and sp is not None]
            assert sum(v) / len(v) > sum(w) / len(w) + 3
    r = client.get(f"/api/compare?ids={','.join(ids[:2])}&mode=intervals&start=rep1&tracks=0").json()
    assert r["align"]["applied"] is False
    assert client.get("/api/compare?ids=24913137517&mode=moving&start=rep1").status_code == 422


def test_compare_rep_series(client):
    """rep_series: every rep on a 1 s grid from -lead_s, rep start at x = 0 (speed jumps), memo key includes it"""
    ids = client.get(f"/api/search?exact_key={quote(SIX_K)}&sort=-date&limit=6").json()["rows"]
    ids = [r["id"] for r in ids][:6]
    plain = client.get(f"/api/compare?ids={','.join(ids)}&mode=intervals&tracks=0").json()
    assert "rep_series" not in plain
    d = client.get(f"/api/compare?ids={','.join(ids)}&mode=intervals&tracks=0&rep_series=1&rep_lead_s=12").json()
    rs = d["rep_series"]
    assert rs["lead_s"] == 12 and len(rs["slots"]) == len(d["slots"])
    for k, S in enumerate(rs["slots"]):
        assert S["x0"] == -12 and S["n"] == 12 + S["width"] + rs["post_s"] + 1
        for sid, q in S["series"].items():
            assert len(q["speed_kmh"]) == S["n"] and abs(q["start_s"] - d["reps"][sid][k]["start_s"]) < 0.11
            v = q["speed_kmh"]
            pre = [x for x in v[0:9] if x is not None]          # x = -12 .. -4
            post = [x for x in v[17:27] if x is not None]       # x = 5 .. 14
            if pre and post:
                assert sum(post) / len(post) > sum(pre) / len(pre) + 3, (sid, k)


def test_compare_bad_input(client):
    assert client.get("/api/compare?ids=abc").status_code == 422
    assert client.get("/api/compare?ids=1,2&mode=nope").status_code == 422


def test_fitness_json_loads(client):
    raw = json.loads((DATA / "fitness.json").read_text())
    assert raw["disciplines"] and raw["sessions"]
    d = client.get("/api/fitness").json()
    assert {"interval", "long", "ski"} <= set(d["disciplines"])
    assert len(d["sessions"]) == len(raw["sessions"])
    cur = d["disciplines"]["interval"]["current"]
    assert 30 < cur["mean"] < 85


def test_fitness_variants(client):
    raw = json.loads((DATA / "fitness.json").read_text())
    if "variants" not in raw:
        pytest.skip("fitness.json has no model variants")
    d = client.get("/api/fitness").json()
    v = d["variants"]
    assert v["pace"]["label"] and v["hr"]["label"]
    hd = v["hr"]["disciplines"]
    assert {"interval", "long", "ski"} <= set(hd)
    cur = hd["interval"]["current"]
    assert 30 < cur["mean"] < 85 and "trend_90d" in cur and "last_evidence" in cur
    # filters apply to the variant series too
    f = client.get("/api/fitness?discipline=long&from=2024-01-01").json()
    assert set(f["variants"]["hr"]["disciplines"]) == {"long"}
    assert all(w["date"] >= "2024-01-01" for w in f["variants"]["hr"]["disciplines"]["long"]["weekly"])
    # eval comparison: null without fitness_eval.json, else pace/hr per discipline
    ev = d["eval_variants"]
    assert ev == client.get("/api/fitness/eval").json()
    if (DATA / "fitness_eval.json").exists() and ev is not None:
        assert {"pace", "hr"} <= set(ev["default"])
        assert "forecast_mae" in ev["default"]["hr"]["interval"]
    # session endpoint carries the with-HR block
    s = next((s for s in d["sessions"] if s.get("hr")), None)
    if s:
        r = client.get(f"/api/fitness/session/{s['id']}").json()
        blk = next(x for x in r["sessions"] if x["discipline"] == s["discipline"])["hr"]
        assert {"kind", "weight", "p_max", "note", "shape", "label"} <= set(blk)


# ---- Home page payload (/api/home)
def test_home(client, activities):
    import datetime as dt
    anchor = activities[0]["date"]            # the latest activity day: the anchor and the latest active day
    h = client.get(f"/api/home?date={anchor}").json()
    assert h["anchor"] == anchor and h["latest"] == anchor
    day = h["latest_day"]
    assert day["date"] == anchor and "hr" not in day
    assert {a["id"] for a in day["activities"]} == {a["id"] for a in activities if a["date"] == anchor}
    # volume: the "all" row counts every activity of the last 7 / 30 days
    a0 = dt.date.fromisoformat(anchor)
    age = lambda a: (a0 - dt.date.fromisoformat(a["date"])).days
    tot = next(v for v in h["volume"] if v["sport"] == "all")
    assert tot["week"]["n"] == sum(1 for a in activities if 0 <= age(a) < 7)
    assert tot["month"]["n"] == sum(1 for a in activities if 0 <= age(a) < 30)
    assert len(tot["weekly_km"]) == 52 and tot["weekly_s"][-1] == tot["week"]["s"]
    assert sum(v["week"]["n"] for v in h["volume"] if v["sport"] != "all") == tot["week"]["n"]
    # training log: Monday-aligned 5 weeks, days inside it, every id a known activity of that day
    lo, hi = h["periods"]["log"]
    assert dt.date.fromisoformat(lo).weekday() == 0 and (dt.date.fromisoformat(hi) - dt.date.fromisoformat(lo)).days == 34
    by_id = {a["id"]: a for a in activities}
    for d, ids in h["days"].items():
        assert lo <= d <= anchor and all(by_id[i]["date"] == d for i in ids)
    # recent workouts: newest first, with workout fields; tracks = the last 30 days with GPS
    rec = h["recent"]
    assert len(rec) >= 15 and rec[0]["date"] == anchor
    assert all(k in rec[0] for k in ("signature", "rep_count", "rep_speed_ms", "family_label", "dur_s", "track"))
    m0 = h["periods"]["month"][0]
    assert {t["id"] for t in h["tracks"]} == {a["id"] for a in activities if m0 <= a["date"] <= anchor and a["track"]}
    if h["fitness"]:
        assert {"interval", "overall"} <= set(h["fitness"]) and h["fitness"]["interval"]["mean"]
    # an anchor before the data: nothing active, no latest day
    e = client.get("/api/home?date=2000-01-01").json()
    assert e["latest"] is None and e["latest_day"] is None and e["recent"] == [] and e["tracks"] == []
    assert client.get("/api/home?date=bad").status_code == 400


# ---- person prefix: /api/<person>/... picks the person, unprefixed /api/... is the default person
def test_people(client):
    j = client.get("/api/people").json()
    assert j["default"] in j["people"] and j["people"][0] == j["default"]


def test_person_prefix(client):
    base = client.get("/api/meta").json()
    for p in client.get("/api/people").json()["people"]:
        r = client.get(f"/api/{p}/meta")
        assert r.status_code == 200 and r.json()["person"] == p
    assert client.get(f"/api/{base['person']}/activities").json()["n"] == base["n_activities"]
    assert client.get("/api/not-a-person/meta").status_code == 404

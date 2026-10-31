"""API smoke tests: every GET endpoint answers 200 on real data, plus a few invariants the UI relies on."""

from __future__ import annotations

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

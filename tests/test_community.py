"""Community API (scripts/community.py + /api/community/…) on real data, with community.json and the photo folder
redirected to a temp dir (the real data/community.json is never touched)."""

from __future__ import annotations

import io
import json

import pytest

from .conftest import ROOT, needs_data

pytestmark = [needs_data, pytest.mark.skipif(not (ROOT / "data" / "together.json").exists(), reason="no together.json")]


@pytest.fixture()
def cm(tmp_path, monkeypatch):
    import community
    real = (community.STORE, community.STORE.stat().st_mtime_ns if community.STORE.exists() else None)
    monkeypatch.setattr(community, "STORE", tmp_path / "community.json")
    monkeypatch.setattr(community, "PHOTOS", tmp_path / "photos")
    community._STORE_MEMO["key"] = None
    yield community
    community._STORE_MEMO["key"] = None
    assert (real[0].stat().st_mtime_ns if real[0].exists() else None) == real[1], "real community.json touched"


@pytest.fixture()
def gid(client):
    gs = client.get("/api/together").json()["groups"]
    return next(g["gid"] for g in gs if g["kind"] == "intervals")


def _jpeg(w=2400, h=1200, colour=(200, 60, 30)):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (w, h), colour).save(b, "JPEG")
    return b.getvalue()


def test_list_shape(client, cm):
    r = client.get("/api/community/sessions")
    assert r.status_code == 200, r.text[:300]
    d = r.json()
    assert {"n", "people", "categories", "groups", "totals", "h2h"} <= set(d)
    assert [c["id"] for c in d["categories"]] == cm.CAT_IDS
    assert d["n"] == len(d["groups"]) > 0
    for g in d["groups"]:
        assert g["category"] in cm.CAT_IDS and g["category_auto"] in cm.CAT_IDS
        assert {"km", "moving_s", "reps", "climb_m", "person_min_together"} <= set(g["collective"])
        assert g["n_timed"] >= 1 and g["n_guests"] == len(g["guests"])
        assert set(g["form"]) >= {p["person"] for p in g["participants"] if p["role"] != "other"} - {None}
        assert g["photo"] is None and g["kudos"] == []
    assert sum(c["n"] for c in d["categories"]) == d["n"]
    assert not any(g["hidden"] for g in d["groups"])
    assert any(g["hidden"] for g in client.get("/api/community/sessions?near=1").json()["groups"])


def test_list_filters(client, cm):
    d = client.get("/api/community/sessions?category=intervals").json()
    assert d["n"] and all(g["category"] == "intervals" for g in d["groups"])
    assert client.get("/api/community/sessions?from=2026-01-01").json()["groups"][-1]["date"] >= "2026-01-01"
    assert client.get("/api/community/sessions?q=zzzznothing").json()["n"] == 0
    assert client.get("/api/patrik/community/sessions").json()["viewer"] == "patrik"


def test_auto_categories(cm):
    import together as tg
    doc = tg.load()
    for g in doc["groups"]:
        c, why = cm.auto_category(g)
        assert c in cm.CAT_IDS and why
        if g["kind"] == "intervals":
            assert c == "intervals"
        if g["label"] == "same_race":
            assert c == "race"
        if g["sport"] != "run" and g["kind"] != "intervals":
            assert c == "other"


def test_session_patch_kudos_guests(client, cm, gid):
    r = client.get(f"/api/community/sessions/{gid}")
    assert r.status_code == 200, r.text[:300]
    s = r.json()
    assert {"meta", "collective", "form", "records", "members", "nav"} <= set(s)
    assert s["meta"]["category"] == s["meta"]["category_auto"] == "intervals"
    m = client.patch(f"/api/community/sessions/{gid}",
                     json={"title": "  Track night ", "category": "tempo", "note": "windy", "guests": ["Kari", "kari", "Ola"]}).json()
    assert m["title"] == "Track night" and m["category"] == "tempo" and m["category_auto"] == "intervals"
    assert m["guests"] == ["Kari", "Ola"]
    assert client.patch(f"/api/community/sessions/{gid}", json={"category": "bogus"}).status_code == 422
    assert client.patch(f"/api/community/sessions/{gid}", json={"nope": 1}).status_code == 422
    row = next(g for g in client.get("/api/community/sessions").json()["groups"] if g["gid"] == gid)
    assert row["title"] == "Track night" and row["n_guests"] == 2 and row["category"] == "tempo"
    assert client.get("/api/community/sessions?q=windy").json()["n"] == 1
    k = client.post(f"/api/community/sessions/{gid}/kudos", json={"person": "patrik"}).json()
    assert k["kudos"] == ["patrik"] and k["kudoed"]
    assert client.post(f"/api/community/sessions/{gid}/kudos", json={"person": "patrik"}).json()["n_kudos"] == 1
    assert client.post(f"/api/adrian/community/sessions/{gid}/kudos").json()["kudos"] == ["patrik", "adrian"]
    assert client.delete(f"/api/community/sessions/{gid}/kudos?person=patrik").json()["kudos"] == ["adrian"]
    m = client.patch(f"/api/community/sessions/{gid}", json={"category": None, "hidden": True}).json()
    assert m["category"] == "intervals" and m["hidden"]
    assert gid not in {g["gid"] for g in client.get("/api/community/sessions").json()["groups"]}
    assert gid in {g["gid"] for g in client.get("/api/community/sessions?hidden=1").json()["groups"]}
    on_disk = json.loads(cm.STORE.read_text())
    assert on_disk["sessions"][gid]["title"] == "Track night"


def test_unknown_gid(client, cm):
    assert client.get("/api/community/sessions/1999-01-01_0000_nowhere").status_code == 404
    assert client.patch("/api/community/sessions/1999-01-01_0000_nowhere", json={"title": "x"}).status_code == 404


def test_photos(client, cm, gid):
    r = client.post(f"/api/community/sessions/{gid}/photos?by=adrian",
                    files={"file": ("run.jpg", _jpeg(), "image/jpeg")})
    assert r.status_code == 200, r.text[:300]
    p = r.json()
    assert max(p["w"], p["h"]) == cm.PHOTO_MAX_PX and p["by"] == "adrian"
    big, th = client.get(p["url"]), client.get(p["thumb"])
    assert big.status_code == th.status_code == 200 and big.headers["content-type"] == "image/jpeg"
    from PIL import Image
    assert max(Image.open(io.BytesIO(th.content)).size) == cm.THUMB_PX
    # same file again = same photo; a png too
    assert client.post(f"/api/community/sessions/{gid}/photos", files={"file": ("a.jpg", _jpeg(), "image/jpeg")}).json()["id"] == p["id"]
    b = io.BytesIO()
    Image.new("RGBA", (300, 200), (0, 0, 255, 128)).save(b, "PNG")
    p2 = client.post(f"/api/community/sessions/{gid}/photos", files={"file": ("b.png", b.getvalue(), "image/png")}).json()
    s = client.get(f"/api/community/sessions/{gid}").json()
    assert [x["id"] for x in s["meta"]["photos"]] == [p["id"], p2["id"]]
    m = client.patch(f"/api/community/sessions/{gid}", json={"cover": p2["id"]}).json()
    assert m["photos"][0]["id"] == p2["id"]
    row = next(g for g in client.get("/api/community/sessions").json()["groups"] if g["gid"] == gid)
    assert row["n_photos"] == 2 and row["photo"]["thumb"] == p2["thumb"]
    assert client.post(f"/api/community/sessions/{gid}/photos", files={"file": ("x.jpg", b"not an image", "image/jpeg")}).status_code == 422
    assert client.delete(f"/api/community/sessions/{gid}/photos/{p['id']}").status_code == 200
    assert client.get(p["url"]).status_code == 404
    assert client.delete(f"/api/community/sessions/{gid}/photos/{p['id']}").status_code == 404
    assert client.get(f"/api/community/photos/{gid}/..%2Fcommunity.json").status_code == 404


def test_broken_store_is_kept(client, cm, gid):
    cm.STORE.write_text("{not json")
    d = client.get("/api/community/sessions").json()
    assert d["store_error"] and d["n"] > 0
    client.patch(f"/api/community/sessions/{gid}", json={"note": "x"})
    assert list(cm.STORE.parent.glob("community.bad-*.json"))
    assert json.loads(cm.STORE.read_text())["sessions"][gid]["note"] == "x"
    # hand-edited, sloppy types
    cm.STORE.write_text(json.dumps({gid: {"guests": "Kari, Ola", "category": "nonsense", "kudos": {"patrik": 1}}}))
    m = client.get(f"/api/community/sessions/{gid}").json()["meta"]
    assert m["guests"] == ["Kari", "Ola"] and m["category"] == m["category_auto"] and m["kudos"] == ["patrik"]


def test_club(client, cm):
    r = client.get("/api/community/club")
    assert r.status_code == 200, r.text[:300]
    c = r.json()
    assert {"totals", "months", "attendance", "pairs", "pair_matrix", "categories", "records", "highlights",
            "heatmap", "places", "recent"} <= set(c)
    assert c["totals"]["sessions"] == sum(m["sessions"] for m in c["months"]) == sum(x["n"] for x in c["categories"])
    assert sum(map(sum, c["heatmap"]["n"])) == c["totals"]["sessions"]
    assert all(x["streak_weeks"] <= x["best_streak_weeks"] for x in c["attendance"])
    assert c["records"] and all(r["value_s"] > 0 for r in c["records"])
    n = c["pair_matrix"]["n"]
    assert all(n[i][j] == n[j][i] for i in range(len(n)) for j in range(len(n)))
    assert client.get("/api/community/club?from=2026-01-01").json()["totals"]["sessions"] <= c["totals"]["sessions"]


def test_members(client, cm):
    ms = client.get("/api/community/members").json()["members"]
    assert ms
    r = client.get(f"/api/community/members/{ms[0]['person']}")
    assert r.status_code == 200, r.text[:300]
    m = r.json()
    assert {"totals", "sessions", "form_series", "partners", "best_sessions", "personal_bests", "shape"} <= set(m)
    assert m["totals"]["sessions"] == ms[0]["sessions"] == len(m["sessions"])
    assert client.get("/api/community/members/nobody-here").status_code == 404


def test_records_are_improvements(cm):
    B = cm.base()
    for gid, rs in B["records"].items():
        for r in rs:
            assert r["value_s"] < r["prev_s"] and r["kind"] in ("club_best", "pb")


def test_session_records_synthetic(cm):
    """club best / pb / first-effort logic on hand-made efforts"""
    e = lambda gid, p, v: {"gid": gid, "date": gid, "person": p, "key": p, "measure": "rep", "dist_m": 400,  # noqa: E731
                           "label": "400 m", "value_s": v, "n": 1}
    B = {"order": ["g1", "g2", "g3", "g4"], "people": {},
         "efforts": [e("g1", "a", 80), e("g1", "b", 85), e("g2", "b", 82), e("g3", "a", 79), e("g4", "b", 90)]}
    r = cm.session_records(B)
    assert "g1" not in r and "g4" not in r
    assert [(x["kind"], x["person"], x["prev_s"]) for x in r["g2"]] == [("pb", "b", 85)]
    assert [(x["kind"], x["person"], x["improvement_s"]) for x in r["g3"]] == [("club_best", "a", 1.0)]

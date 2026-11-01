"""together.build_groups without data: connected components for N people, near-miss handling, stable group ids."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import together as tg

T0 = 1_760_000_000.0   # 2025-10-09 08:53 UTC


def _act(person, i, s, place="Bislet"):
    return {"person": person, "id": i, "s": s, "e": s + 3600, "tz": 7200,
            "x": {"activityType": {"typeKey": "running"}, "distance": 10000, "activityName": "Run"},
            "meta": {"type": "easy", "place": place, "sig": None},
            "places": {place: {"name": place.title(), "lat": 59.925, "lon": 10.733, "src": "known"}},
            "fit": None, "clock_fixed": False, "prod": None, "sample_s": 1.0}


def _pair(a, b, label, score=0.9):
    return {"a": [a["person"], a["id"]], "b": [b["person"], b["id"]], "label": label, "score": score, "why": label,
            "joint_start_utc": max(a["s"], b["s"]), "joint_pos": (59.925, 10.733), "overlap_start_utc": max(a["s"], b["s"]),
            "offset_s": 1.0}


def test_chain_of_three_is_one_group_and_ids_are_stable():
    A, B, C = _act("anna", 1, T0 + 60), _act("bob", 2, T0 + 120), _act("cara", 3, T0 + 300)
    acts = {(x["person"], x["id"]): x for x in (A, B, C)}
    groups, aliases = tg.build_groups([_pair(A, B, "together"), _pair(B, C, "partly_together", 0.4)], acts)
    assert len(groups) == 1 and aliases == {}
    g = groups[0]
    assert g["people"] == ["anna", "bob", "cara"] and g["label"] == "together" and not g["hidden"]
    assert g["gid"] == "2025-10-09_1054_Bislet"          # local start of the earliest member + place id
    assert {p["person"]: p["offset_s"] for p in g["participants"]} == {"anna": 0.0, "bob": 1.0, "cara": 2.0}
    # a 4th person who started earlier joins later: the id is kept
    D = _act("dan", 4, T0)
    acts[("dan", 4)] = D
    old = {"groups": groups, "aliases": {}}
    g2, _ = tg.build_groups([_pair(A, B, "together"), _pair(B, C, "partly_together", 0.4), _pair(A, D, "together")], acts, old)
    assert len(g2) == 1 and g2[0]["gid"] == g["gid"] and g2[0]["n_people"] == 4


def test_near_miss_is_hidden_or_attached_as_other():
    A, B, C = _act("anna", 1, T0), _act("bob", 2, T0 + 30), _act("cara", 3, T0 + 60)
    acts = {(x["person"], x["id"]): x for x in (A, B, C)}
    groups, _ = tg.build_groups([_pair(A, B, "same_place_diff_workout", 0.1)], acts)
    assert len(groups) == 1 and groups[0]["hidden"]
    groups, _ = tg.build_groups([_pair(A, B, "same_workout"), _pair(B, C, "same_place_diff_workout", 0.1)], acts)
    assert len(groups) == 1 and not groups[0]["hidden"]
    roles = {p["person"]: p["role"] for p in groups[0]["participants"]}
    assert roles["cara"] == "other" and groups[0]["people"] == ["anna", "bob"]


def test_people_info_auto_colours_and_initials(tmp_path, monkeypatch):
    monkeypatch.setattr(tg, "PEOPLE_JSON", tmp_path / "people.json")
    (tmp_path / "people.json").write_text('{"anna": {"name": "Anna", "colour": "#2f5fd0"}}')
    info = tg.people_info(["anna", "andreas", "bob"])
    assert info["anna"]["colour"] == "#2f5fd0" and not info["anna"]["auto"]
    assert info["andreas"]["auto"] and info["andreas"]["colour"] != "#2f5fd0"
    assert len({v["colour"] for v in info.values()}) == 3
    assert info["bob"]["initials"] == "B" and info["anna"]["initials"] != info["andreas"]["initials"]


def test_parse_sig_and_targets():
    assert tg._parse_sig("3×1000 + 2×0:45", 5) == [("dist", 1000)] * 3 + [("time", 45)] * 2
    assert tg._parse_sig("3×1000", 4) is None and tg._parse_sig("10 reps (mixed 1:02–3:09)", 10) is None
    assert tg._infer_target([{"own_dist": 392, "own_time": 86}, {"own_dist": 400, "own_time": 77}]) == ("dist", 400)
    assert tg._infer_target([{"own_dist": 2009, "own_time": 466}, {"own_dist": 3027, "own_time": 771}]) == ("dist", 2000)   # the common distance

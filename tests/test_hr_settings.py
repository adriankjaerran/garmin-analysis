"""Per-person / per-device HR settings (scripts/hr_settings.py) and their use in fitness_model / together / session_groups."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fitness_model as fm  # noqa: E402
import hr_settings as hs  # noqa: E402
import session_groups as sg  # noqa: E402
import together as tg  # noqa: E402

DOC = {
    "max_hr": 200, "rest_hr": 50, "source": "auto",
    "devices": {
        "fr935/wrist": {"source": "wrist", "from": "2018-01-01", "to": "2026-02-18", "n": 500,
                        "trust": {"interval": 0.3, "steady": 0.5}},
        "4315/wrist": {"source": "wrist", "from": "2026-02-18", "to": "2026-11-01", "n": 70,
                       "trust": {"interval": 1.0, "steady": 1.0}},
        "4315/strap": {"source": "strap", "from": "2026-05-01", "to": "2026-06-01", "n": 3,
                       "trust": {"interval": 0.9, "steady": 0.9}},
    },
    "activities": {"1": {"device": "4315/strap"}, "2": {"device": "4315/wrist", "flags": ["hr_high_for_pace"]}},
    "manual": {"max_hr": 198, "devices": {"fr935/wrist": {"trust": {"steady": 0.6}}}},
}


@pytest.fixture
def person(tmp_path, monkeypatch):
    p = tmp_path / "data" / "x" / "profile"
    p.mkdir(parents=True)
    (p / "hr_settings.json").write_text(json.dumps(DOC))
    monkeypatch.setattr(hs, "ROOT", tmp_path)
    monkeypatch.setattr(tg, "DATA", tmp_path / "data")
    monkeypatch.setattr(sg, "ROOT", tmp_path)
    hs._CACHE.clear()
    yield "x"
    hs._CACHE.clear()


def test_trust_anchors_reproduce_the_hand_set_values():
    # adrian's measured medians map to his old hand-set trust: FR935 0.3 / 0.5, 4315 1.0 / 1.0
    assert hs._trust(hs.TC_ANCHOR[0]) == {"interval": 0.3, "steady": 0.5}
    assert hs._trust(hs.TC_ANCHOR[1]) == {"interval": 1.0, "steady": 1.0}
    assert hs._trust(0.0)["interval"] == 0.1 and hs._trust(1.5) == {"interval": 1.0, "steady": 1.0}


def test_confirmed_peak_ignores_one_off_spikes():
    assert hs._confirmed_peak([214, 206, 206, 205, 190]) == 206
    assert hs._confirmed_peak([208, 206, 206]) == 208
    assert hs._confirmed_peak([220, 180]) is None


def test_max_hr_prefers_trusted_devices():
    dev = {"fr935/wrist": {"peak_hr": 209, "n_runs_hr": 400, "trust": {"interval": 0.3}},
           "4315/wrist": {"peak_hr": 207, "n_runs_hr": 50, "trust": {"interval": 1.0}}}
    m = hs.max_hr(dev, 206)
    assert m["value"] == 207 and "4315" in m["from"] and m["check"] is None
    dev = {"3077/wrist": {"peak_hr": 208, "n_runs_hr": 90, "trust": {"interval": 0.4}}}
    assert hs.max_hr(dev, 190)["check"]          # > 10 bpm from the zone max: noted
    assert hs.max_hr({}, 192)["value"] == 192


def test_devices_detects_a_chest_strap():
    raw = {"file_id": [{"product": 4315, "serial_number": 9}],
           "device_info": [{"device_index": 0, "product": 4315, "source_type": 5},
                           {"device_index": 3, "device_type": 120, "source_type": 1, "product": 4130, "serial_number": 7}]}
    d = hs._devices(raw)
    assert d["source"] == "strap" and d["strap"] == {"product": 4130, "serial": 7}
    raw["device_info"] = raw["device_info"][:1]
    assert hs._devices(raw)["source"] == "wrist"
    assert hs.device_key(2691, "wrist") == "fr935/wrist" and hs.device_key(4315, "strap") == "4315/strap"


def test_for_activity_device_manual_and_flags(person):
    a = hs.for_activity(person, 1, "2026-05-10")
    assert a["device"] == "4315/strap" and a["trust"] == {"interval": 0.9, "steady": 0.9} and a["max"] == 198
    # no activity entry: device from the date ranges (wrist preferred); manual trust merged into the device's
    b = hs.for_activity(person, 99, "2025-01-01")
    assert b["device"] == "fr935/wrist" and b["trust"] == {"interval": 0.3, "steady": 0.6}
    assert hs.for_activity(person, 98, "2026-05-10")["device"] == "4315/wrist"
    c = hs.for_activity(person, 2, "2026-07-01")
    assert c["flags"] == ["hr_high_for_pace"] and c["trust"] == {"interval": 0.0, "steady": 0.0}


def test_fitness_model_uses_device_trust(person, monkeypatch):
    item = {"discipline": "interval", "kind": "hard", "score": 55.0, "sigma": 1.5, "date": "2025-01-01",
            "components": {"work_hrr": 0.70}}
    # legacy: FR935 by date -> trust 0.3 -> p >= 0.7, not demoted
    monkeypatch.setattr(fm, "HR_SETTINGS", False)
    assert fm.apply_hr_intent([item])[0]["kind"] == "hard"
    monkeypatch.setattr(fm, "HR_SETTINGS", True)
    strap = {**item, "hr_dev": fm.hr_of(person, 1, "2026-05-10")}
    assert fm.apply_hr_intent([strap])[0]["kind"] == "bound"           # trusted strap, 0.70 HRR: sub-max
    flagged = {**item, "hr_dev": fm.hr_of(person, 2, "2026-07-01")}
    out = fm.apply_hr_intent([flagged])[0]
    assert out["kind"] == "hard" and out["components"]["p_max"] == 1.0  # implausible HR isn't trusted
    assert fm.hr_of("nobody-without-settings", 1, "2025-01-01")["max"] == fm.P["hr_max"]


def test_together_and_session_groups_respect_flags(person):
    assert tg.hr_norm(person)["max"] == 198 and sg.hr_norm(person) == (198.0, 50.0)   # manual max applied
    h = tg.hr_norm(person, 2)
    assert h["flags"] == ["hr_high_for_pace"] and "implausible" in h["warn"] and tg._effort(180, h) is None
    assert tg._effort(180, tg.hr_norm(person, 1)) is not None
    assert sg.hr_flagged(person) == {"2"}
    a = {"F": {"avg_hr": 170, "speed": 3.6, "moving_s": 2400}}
    assert sg.steady_or_easy(a, 3.3, (200.0, 50.0)) == "steady"
    assert sg.steady_or_easy(a, 3.3, (200.0, 50.0), hr_ok=False) == "easy"


def test_borrowed_watch_stays_with_the_wearer(tmp_path):
    # adrian's 6 runs of 2021 on patrik's 3077 (same serial): adrian's runs, patrik's watch
    own = tmp_path / "data" / "patrik" / "profile"
    own.mkdir(parents=True)
    (own / "hr_settings.json").write_text(json.dumps({"devices": {"3077/wrist": {
        "serials": [3350348757], "n": 96, "trust": {"interval": 0.42, "steady": 0.58}, "trust_from": "interval_track_corr"}}}))
    dev = {"3077/wrist": {"serials": [3350348757], "n": 6, "from": "2021-03-30", "to": "2021-09-03",
                          "trust": dict(hs.TRUST_DEFAULT), "trust_from": "default (< 5 interval sessions)", "source": "wrist"},
           "fr935/wrist": {"serials": [1], "n": 554, "from": "2018-12-22", "to": "2026-02-18", "source": "wrist",
                           "trust": {"interval": 0.3, "steady": 0.5}, "trust_from": "interval_track_corr"}}
    assert hs.mark_borrowed("adrian", dev, tmp_path) == {"3077/wrist": "patrik"}
    d = dev["3077/wrist"]
    assert d["borrowed_from"] == "patrik" and d["trust"] == {"interval": 0.42, "steady": 0.58} and "owner" in d["trust_from"]
    assert "borrowed_from" not in dev["fr935/wrist"] and dev["fr935/wrist"]["trust"]["interval"] == 0.3
    # never the date fallback, even when it is the only device that day
    assert hs.device_on({"devices": dev}, "2021-05-01") == "fr935/wrist"
    assert hs.device_on({"devices": {"3077/wrist": d}}, "2021-05-01") is None
    # the owner's own file: not borrowed (more sessions there)
    pd = {"3077/wrist": {"serials": [3350348757], "n": 96, "trust": {"interval": 0.42, "steady": 0.58}}}
    a = tmp_path / "data" / "adrian" / "profile"; a.mkdir(parents=True)
    (a / "hr_settings.json").write_text(json.dumps({"devices": dev}))
    assert hs.mark_borrowed("patrik", pd, tmp_path) == {}


def test_real_adrian_borrowed_3077():
    root = Path(__file__).resolve().parents[1]
    p = root / "data" / "adrian" / "profile" / "hr_settings.json"
    if not p.exists():
        pytest.skip("no adrian hr_settings")
    d = json.loads(p.read_text())
    dev = d["devices"].get("3077/wrist")
    if not dev:
        pytest.skip("no 3077 device")
    assert dev["borrowed_from"] == "patrik"
    ids = [i for i, a in d["activities"].items() if a["device"] == "3077/wrist"]
    assert len(ids) == 6
    g = json.loads((root / "data" / "adrian" / "groups.json").read_text())["activities"]
    gp = json.loads((root / "data" / "patrik" / "groups.json").read_text())["activities"]
    assert all(i in g for i in ids) and not any(i in gp for i in ids)   # adrian's runs, not patrik's
    r = json.loads((root / "data" / "rise" / "profile" / "hr_settings.json").read_text())
    assert r["devices"]["fenix3_hr/wrist"]["trust"]["interval"] > 0.5   # rise's estimated wrist trust (0.57), not capped

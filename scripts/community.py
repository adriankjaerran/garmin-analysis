"""Community: the running club on top of Together (docs/community.md).

Together (scripts/together.py) finds the joint workouts; this module adds what a club wants around them:
  * user metadata per session in data/community.json (title, category override, note, guests, photos, hidden, kudos),
    hand-editable, written atomically, tolerant of a missing / broken / hand-edited file;
  * photos in data/_community/photos/<gid>/ (<id>.jpg ≤ 1600 px + <id>_t.jpg 480 px thumbnail);
  * an automatic category per session (intervals, tempo, long, easy, race, other);
  * collective numbers (km, moving time, reps, climb), form deltas (scripts/form_delta.py,
    optional); no records or rivalry (removed 2026-11-01);
  * the club dashboard and member profiles.

The expensive part (sessions, form deltas) is the "base": computed once per input signature (together's
inputs_sig + activities_index / fitness mtimes + form_delta.py) and memoised; community.json is overlaid per request.
Callers serialise base builds (the API holds compare's lock: the together engine isn't thread-safe).

    uv run scripts/community.py list | club | member <person> | show <gid>
"""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib
import io
import json
import os
import re
import sys
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import together as tg  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
STORE = DATA / "community.json"                  # user metadata (monkeypatched in tests)
PHOTOS = DATA / "_community" / "photos"          # photos per gid (monkeypatched in tests)
FORM_PY = Path(__file__).resolve().parent / "form_delta.py"
VERSION = 1

CATEGORIES = [   # id, label, description (shown as "type" in the app)
    ("intervals", "Intervals", "reps run together (track, road, hills, ski intervals)"),
    ("tempo", "Tempo", "a continuous hard run (tempo / threshold)"),
    ("long", "Long run", "a continuous run of ≥ 90 min or ≥ 16 km together"),
    ("easy", "Easy", "an easy / steady continuous run"),
    ("race", "Race", "the same race"),
    ("other", "Other", "hikes, rides, ski tours, swims, …"),
]
CAT_IDS = [c[0] for c in CATEGORIES]
CAT_INFO = {c[0]: {"id": c[0], "label": c[1], "description": c[2]} for c in CATEGORIES}

LONG_MIN_S = 90 * 60          # continuous run together at least this long ...
LONG_MIN_KM = 16.0            # ... or this far -> long
MAX_PHOTO_BYTES = 15 * 1024 * 1024
PHOTO_MAX_PX = 1600
THUMB_PX = 480
TEXT_MAX = {"title": 120, "note": 4000, "guest": 60, "person": 40}
_GID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,120}$")
_FILE_RE = re.compile(r"^[0-9a-f]{12,40}(_t)?\.jpg$")

_WLOCK = threading.RLock()    # community.json read-modify-write
_BASE: dict = {"key": None, "base": None}
_STORE_MEMO: dict = {"key": None, "doc": None}
_JSON_MEMO: dict = {}
_FORM_MOD: dict = {"mtime": None, "mod": None, "error": None}
_FORM_CACHE: dict = {}


# ------------------------------------------------------------------ small helpers
def _r(v, n=1):
    return None if v is None else round(float(v), n)


def _now_iso():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _mt(p: Path) -> int:
    try:
        return p.stat().st_mtime_ns
    except OSError:
        return 0


def _json(p: Path):
    """JSON file memoised by mtime (None when missing / broken)"""
    k = _mt(p)
    hit = _JSON_MEMO.get(p)
    if hit and hit[0] == k:
        return hit[1]
    try:
        v = json.loads(p.read_text()) if k else None
    except Exception:  # noqa: BLE001
        v = None
    _JSON_MEMO[p] = (k, v)
    return v


def _clean_text(v, n):
    if v is None:
        return None
    v = str(v).strip()
    return v[:n] or None


def _week(d: str) -> tuple:
    y, w, _ = dt.date.fromisoformat(d).isocalendar()
    return (y, w)


def _week_index(d: str) -> int:
    """monotone week number (Monday-based) for streaks"""
    x = dt.date.fromisoformat(d)
    return (x - dt.timedelta(days=x.weekday())).toordinal() // 7


# ------------------------------------------------------------------ community.json (user metadata)
def _empty_doc():
    return {"version": VERSION, "sessions": {}}


def _norm_entry(e) -> dict:
    """one gid's metadata, normalised (hand edits: wrong types are dropped / coerced, unknown keys kept)"""
    e = dict(e) if isinstance(e, dict) else {}
    out = dict(e)
    for k in ("title", "note"):
        out[k] = _clean_text(e.get(k), TEXT_MAX[k]) if isinstance(e.get(k), (str, int, float)) else None
    c = e.get("category")
    out["category"] = c if c in CAT_IDS else None
    gs = e.get("guests")
    if isinstance(gs, str):
        gs = [x for x in re.split(r"[,;\n]", gs)]
    guests = []
    for x in gs if isinstance(gs, list) else []:
        name = x.get("name") if isinstance(x, dict) else x
        name = _clean_text(name, TEXT_MAX["guest"]) if isinstance(name, (str, int)) else None
        if name and name.lower() not in {g.lower() for g in guests}:
            guests.append(name)
    out["guests"] = guests
    out["hidden"] = bool(e.get("hidden", False))
    ph = []
    for p in e.get("photos") if isinstance(e.get("photos"), list) else []:
        if isinstance(p, dict) and isinstance(p.get("id"), str) and re.fullmatch(r"[0-9a-f]{12,40}", p["id"]):
            ph.append(p)
    out["photos"] = ph
    out["cover"] = e.get("cover") if isinstance(e.get("cover"), str) else None
    ku = e.get("kudos")
    if isinstance(ku, dict):                 # {person: n} by hand -> people
        ku = [k for k, v in ku.items() if v]
    kudos = []
    for k in ku if isinstance(ku, list) else []:
        k = _clean_text(k, TEXT_MAX["person"]) if isinstance(k, str) else None
        if k and k not in kudos:
            kudos.append(k)
    out["kudos"] = kudos
    return out


def load_store() -> dict:
    """data/community.json -> {version, sessions: {gid: entry}, error?}; missing -> empty; broken JSON -> empty + error
    (the broken file is backed up before the next write, never silently lost)"""
    k = (str(STORE), _mt(STORE))
    if _STORE_MEMO["key"] == k:
        return _STORE_MEMO["doc"]
    doc = _empty_doc()
    if k[1]:
        try:
            raw = json.loads(STORE.read_text())
            if not isinstance(raw, dict):
                raise ValueError("top level is not an object")
            ss = raw.get("sessions")
            if ss is None and all(isinstance(v, dict) for v in raw.values()) and "version" not in raw:
                ss = raw                     # hand-written {gid: {...}} without the wrapper
            doc = {**{kk: v for kk, v in raw.items() if kk != "sessions"}, "version": VERSION,
                   "sessions": {str(g): _norm_entry(e) for g, e in (ss or {}).items() if isinstance(e, dict)}}
        except Exception as e:  # noqa: BLE001
            doc = {**_empty_doc(), "error": f"data/community.json unreadable ({str(e)[:120]}); edits will back it up first"}
    _STORE_MEMO.update(key=k, doc=doc)
    return doc


def _save_store(doc: dict):
    STORE.parent.mkdir(parents=True, exist_ok=True)
    if doc.get("error") and STORE.exists():          # keep the hand-edited file that didn't parse
        STORE.replace(STORE.with_name(f"community.bad-{int(time.time())}.json"))
    out = {"version": VERSION, "updated": _now_iso(),
           "sessions": {g: _compact(e) for g, e in sorted(doc["sessions"].items()) if _compact(e)}}
    out.update({k: v for k, v in doc.items() if k not in ("version", "updated", "sessions", "error")})
    tmp = STORE.with_name(f".{STORE.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    os.replace(tmp, STORE)
    _STORE_MEMO["key"] = None


def _compact(e: dict) -> dict:
    """drop empty defaults so the file stays readable"""
    return {k: v for k, v in e.items() if v not in (None, [], {}, False, "")}


def _canon(gid: str, doc=None) -> str:
    doc = doc or tg.load(missing_ok=True) or {}
    return (doc.get("aliases") or {}).get(gid, gid)


def entry(gid: str, store=None, tdoc=None) -> dict:
    """metadata for a gid (entries stored under an alias of it are merged in)"""
    store = store or load_store()
    ss = store["sessions"]
    e = ss.get(gid)
    al = [a for a, g in ((tdoc or {}).get("aliases") or {}).items() if g == gid and a in ss]
    if al:
        base = _norm_entry({})
        for a in al + ([gid] if e else []):
            x = ss[a]
            for k, v in x.items():
                if k in ("photos",):
                    base[k] = base.get(k, []) + [p for p in v if p["id"] not in {q["id"] for q in base.get(k, [])}]
                elif k in ("guests", "kudos"):
                    base[k] = base.get(k, []) + [v_ for v_ in v if v_ not in base.get(k, [])]
                elif v not in (None, [], False):
                    base[k] = v
        e = base
    return e or _norm_entry({})


def _mutate(gid: str, fn) -> dict:
    """read-modify-write one gid's entry under the write lock (alias entries folded into the canonical gid)"""
    with _WLOCK:
        _STORE_MEMO["key"] = None
        doc = load_store()
        tdoc = tg.load(missing_ok=True) or {}
        e = entry(gid, doc, tdoc)
        for a, g in (tdoc.get("aliases") or {}).items():
            if g == gid:
                doc["sessions"].pop(a, None)
        e = fn(_norm_entry(e)) or e
        doc["sessions"][gid] = _norm_entry(e)
        _save_store(doc)
        return doc["sessions"][gid]


def patch(gid: str, body: dict) -> dict:
    """title / category (one of CAT_IDS; null or "" or "auto" = automatic) / note / guests (list of names or a comma
    string) / hidden / cover (photo id or null). Unknown keys -> ValueError."""
    allowed = {"title", "category", "note", "guests", "hidden", "cover"}
    bad = set(body) - allowed
    if bad:
        raise ValueError(f"unknown fields: {', '.join(sorted(bad))} (allowed: {', '.join(sorted(allowed))})")
    if "category" in body and body["category"] not in (None, "", "auto", *CAT_IDS):
        raise ValueError(f"category must be one of {', '.join(CAT_IDS)} (or null for automatic)")
    if "guests" in body and not isinstance(body["guests"], (list, str, type(None))):
        raise ValueError("guests: a list of names")
    if "hidden" in body and not isinstance(body["hidden"], (bool, type(None))):
        raise ValueError("hidden: true / false")

    def fn(e):
        for k in ("title", "note"):
            if k in body:
                e[k] = _clean_text(body[k], TEXT_MAX[k]) if body[k] is not None else None
        if "category" in body:
            e["category"] = body["category"] if body["category"] in CAT_IDS else None
        if "guests" in body:
            e["guests"] = body["guests"] or []
        if "hidden" in body:
            e["hidden"] = bool(body["hidden"])
        if "cover" in body:
            c = body["cover"]
            if c is not None and c not in {p["id"] for p in e["photos"]}:
                raise ValueError(f"no photo {c} in this session")
            e["cover"] = c
        return e
    return _mutate(gid, fn)


def kudos(gid: str, person: str, on: bool = True) -> list[str]:
    person = _clean_text(person, TEXT_MAX["person"])
    if not person:
        raise ValueError("person required")

    def fn(e):
        ks = [k for k in e["kudos"] if k != person]
        e["kudos"] = ks + [person] if on else ks
        return e
    return _mutate(gid, fn)["kudos"]


# ------------------------------------------------------------------ photos
def _open_image(data: bytes):
    from PIL import Image, ImageOps
    try:
        import pillow_heif  # optional: iPhone HEIC
        pillow_heif.register_heif_opener()
    except Exception:  # noqa: BLE001, S110
        pass
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"not an image Pillow can read (jpg, png, webp, heic): {str(e)[:100]}")
    try:
        im = ImageOps.exif_transpose(im)
    except Exception:  # noqa: BLE001, S110
        pass
    if im.mode not in ("RGB", "L"):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        im = im.convert("RGBA")
        bg.paste(im, mask=im.split()[-1])
        im = bg
    return im.convert("RGB")


def _exif_time(data: bytes):
    try:
        from PIL import Image
        ex = Image.open(io.BytesIO(data)).getexif()
        v = ex.get_ifd(0x8769).get(36867) or ex.get(306)      # DateTimeOriginal, DateTime
        return dt.datetime.strptime(v, "%Y:%m:%d %H:%M:%S").isoformat() if v else None
    except Exception:  # noqa: BLE001
        return None


def photo_dir(gid: str) -> Path:
    if not _GID_RE.match(gid) or gid in (".", ".."):
        raise ValueError("bad gid")
    return PHOTOS / gid


def add_photo(gid: str, data: bytes, filename: str | None = None, by: str | None = None) -> dict:
    """store a photo: ≤ 1600 px jpg + 480 px thumbnail; id = content hash (the same file twice = one photo)"""
    if not data:
        raise ValueError("empty file")
    if len(data) > MAX_PHOTO_BYTES:
        raise ValueError(f"file too large ({len(data) / 1e6:.1f} MB > {MAX_PHOTO_BYTES // (1024 * 1024)} MB)")
    from PIL import Image
    im = _open_image(data)
    pid = hashlib.sha1(data).hexdigest()[:16]
    d = photo_dir(gid)
    d.mkdir(parents=True, exist_ok=True)
    big = im.copy()
    big.thumbnail((PHOTO_MAX_PX, PHOTO_MAX_PX), Image.Resampling.LANCZOS)
    th = im.copy()
    th.thumbnail((THUMB_PX, THUMB_PX), Image.Resampling.LANCZOS)
    for img, name, q in ((big, f"{pid}.jpg", 85), (th, f"{pid}_t.jpg", 80)):
        tmp = d / f".{name}.tmp"
        img.save(tmp, "JPEG", quality=q, optimize=True, progressive=True)
        os.replace(tmp, d / name)
    rec = {"id": pid, "by": _clean_text(by, TEXT_MAX["person"]), "at": _now_iso(), "taken": _exif_time(data),
           "name": _clean_text(filename, 120), "w": big.width, "h": big.height, "orig_w": im.width, "orig_h": im.height,
           "bytes": (d / f"{pid}.jpg").stat().st_size}

    def fn(e):
        if pid not in {p["id"] for p in e["photos"]}:
            e["photos"].append(rec)
        return e
    e = _mutate(gid, fn)
    return photo_out(gid, next(p for p in e["photos"] if p["id"] == pid))


def delete_photo(gid: str, pid: str) -> dict:
    found = {}

    def fn(e):
        found["x"] = any(p["id"] == pid for p in e["photos"])
        e["photos"] = [p for p in e["photos"] if p["id"] != pid]
        if e.get("cover") == pid:
            e["cover"] = None
        return e
    e = _mutate(gid, fn)
    if not found["x"]:
        raise KeyError(f"no photo {pid} in {gid}")
    for name in (f"{pid}.jpg", f"{pid}_t.jpg"):
        try:
            (photo_dir(gid) / name).unlink()
        except FileNotFoundError:
            pass
    return e


def photo_file(gid: str, file: str) -> Path | None:
    if not _FILE_RE.match(file):
        return None
    try:
        p = photo_dir(gid) / file
    except ValueError:
        return None
    return p if p.is_file() else None


def photo_out(gid: str, p: dict) -> dict:
    base = f"/api/community/photos/{gid}/{p['id']}"
    return {"id": p["id"], "url": f"{base}.jpg", "thumb": f"{base}_t.jpg", "by": p.get("by"), "at": p.get("at"),
            "taken": p.get("taken"), "name": p.get("name"), "w": p.get("w"), "h": p.get("h")}


# ------------------------------------------------------------------ inputs per person (index, groups.json)
def _acts(person) -> dict:
    """{activity id str: {moving_s, climb_m, kcal, hr, dist_km, dur_s, type, start_local}} from activities_index.json +
    groups.json (memoised by mtime)"""
    pi, pg = DATA / person / "activities_index.json", DATA / person / "groups.json"
    k = ("acts", person, _mt(pi), _mt(pg))
    hit = _JSON_MEMO.get(("acts", person))
    if hit and hit[0] == k:
        return hit[1]
    out = {}
    for a in _json(pi) or []:
        out[str(a.get("activityId"))] = {
            "moving_s": a.get("movingDuration") or a.get("duration"), "climb_m": a.get("elevationGain"),
            "kcal": a.get("calories"), "hr": a.get("averageHR"), "dist_km": (a.get("distance") or 0) / 1000 or None,
            "dur_s": a.get("duration"), "start_local": a.get("startTimeLocal"), "name": a.get("activityName")}
    for aid, g in ((_json(pg) or {}).get("activities") or {}).items():
        x = out.setdefault(str(aid), {})
        x["type"] = g.get("type")
        if g.get("moving_s"):
            x["moving_s"] = g["moving_s"]      # session_groups' moving time (pauses removed consistently)
        x["route"] = g.get("route")
    _JSON_MEMO[("acts", person)] = (k, out)
    return out


def _members(g) -> list[dict]:
    """participants that count (near-miss partners, role "other", don't)"""
    ps = [p for p in g["participants"] if p["role"] != "other"]
    return ps or list(g["participants"])


def _keys(g) -> list[tuple[str, dict]]:
    seen, out = Counter(), []
    for p in g["participants"]:
        seen[p["person"]] += 1
        out.append((p["person"] if seen[p["person"]] == 1 else f"{p['person']}#{seen[p['person']]}", p))
    return out


# ------------------------------------------------------------------ categories
def auto_category(g) -> tuple[str, str]:
    """(category, reason). Rules in order (docs/community.md#categories):
    1 race: group kind race / label same_race;  2 intervals: kind intervals (≥ 2 shared reps);
    3 other: group sport not run (hike, bike, ski, swim, other);
    4 intervals: every member logged an interval type with ≥ 2 reps (near-misses: different rep sets);
    5 tempo: the group workout is "Tempo run" or most typed members logged tempo;
    6 long: "Long run" / a member typed long, or the time together ≥ 90 min / the shorter member's run ≥ 16 km;
    7 easy: any other run."""
    ms = _members(g)
    if g["kind"] == "race" or g["label"] == "same_race":
        return "race", "same race start"
    if g["kind"] == "intervals":
        return "intervals", f"same reps ({g.get('workout') or 'intervals'})"
    if g["sport"] != "run":
        return "other", f"{g['sport']} ({g.get('workout') or g['kind']})"
    types = [p.get("type") for p in ms]
    if ms and all(t in tg.IV and (p.get("n_reps") or 0) >= 2 for t, p in zip(types, ms)):
        return "intervals", "every member logged intervals"
    typed = [t for t in types if t and t not in tg.GENERIC_TYPES and t not in tg.IV]
    wk = (g.get("workout") or "").lower()
    if wk.startswith("tempo") or (typed and Counter(typed).most_common(1)[0][0] == "tempo"):
        return "tempo", "logged as tempo"
    if wk.startswith("long") or "long" in typed:
        return "long", "logged as a long run"
    span = _together_s(g)
    km = min((p.get("dist_km") or 0) for p in ms) if ms else 0
    if span >= LONG_MIN_S:
        return "long", f"{span / 60:.0f} min together"
    if km >= LONG_MIN_KM and g["label"] != "partly_together":
        return "long", f"{km:.1f} km each"
    return "easy", "continuous run"


def _together_s(g) -> float:
    """time the members spent together: the together span (partly together) or the shorter member's duration"""
    ms = _members(g)
    sp = [p["together"] for p in ms if p.get("together")]
    if sp:
        return max((s.get("to_min") or 0) - (s.get("from_min") or 0) for s in sp) * 60
    return min((p.get("dur_s") or 0) for p in ms) if ms else 0


# ------------------------------------------------------------------ form deltas (scripts/form_delta.py, optional)
def _form_module():
    m = _mt(FORM_PY)
    if _FORM_MOD["mtime"] != m:
        _FORM_MOD.update(mtime=m, mod=None, error=None)
        if m:
            try:
                mod = sys.modules.get("form_delta")
                mod = importlib.reload(mod) if mod else importlib.import_module("form_delta")
                _FORM_MOD["mod"] = mod if hasattr(mod, "form_delta") else None
            except Exception as e:  # noqa: BLE001
                _FORM_MOD["error"] = str(e)[:200]
        else:
            _FORM_MOD["error"] = "scripts/form_delta.py missing"
    return _FORM_MOD["mod"]


def form_sig() -> tuple:
    return (_mt(FORM_PY), *(_mt(DATA / p / "fitness.json") for p in tg.people()))


def form(person: str, aid: str) -> dict | None:
    """{score, shape, delta, discipline} or None (no module, no score, error)"""
    mod = _form_module()
    if mod is None:
        return None
    k = (person, str(aid), _FORM_MOD["mtime"], _mt(DATA / person / "fitness.json"))
    if k in _FORM_CACHE:
        return _FORM_CACHE[k]
    try:
        r = mod.form_delta(person, str(aid))
        r = {kk: (_r(v, 2) if isinstance(v, float) else v) for kk, v in r.items()} if isinstance(r, dict) else None
    except Exception:  # noqa: BLE001
        r = None
    _FORM_CACHE[k] = r
    return r


# ------------------------------------------------------------------ the base (per group, independent of community.json)
def base_key() -> tuple:
    acts = tuple((_mt(DATA / p / "activities_index.json"), _mt(DATA / p / "groups.json")) for p in tg.people())
    return (tg.inputs_sig(), form_sig(), acts, _mt(Path(__file__)))


def base(doc=None) -> dict:
    """{key, built_s, list: together list_payload(near=True), rows: {gid: row}, sessions: {gid: session | None},
    groups: {gid: group}, cat: {gid: (cat, reason)}, collective: {gid}, form: {gid: {key: form | None}},
    order: [gids oldest first]} — memoised by base_key()"""
    k = base_key()
    if _BASE["key"] == k and _BASE["base"] is not None:
        return _BASE["base"]
    T0 = time.time()
    doc = doc or tg.load()
    lp = tg.list_payload(doc, near=True)
    rows = {r["gid"]: r for r in lp["groups"]}
    groups = {g["gid"]: g for g in doc["groups"]}
    sessions = {}
    for gid, g in groups.items():
        s = None
        if not g["no_track"]:
            try:
                s = tg.session(gid, doc)
            except Exception:  # noqa: BLE001
                s = None
        sessions[gid] = s
    B = {"key": k, "list": lp, "rows": rows, "groups": groups, "sessions": sessions, "cat": {}, "collective": {},
         "form": {}, "people": tg.people_info(), "form_available": _form_module() is not None,
         "form_error": _FORM_MOD["error"]}
    for gid, g in groups.items():
        B["cat"][gid] = auto_category(g)
        B["collective"][gid] = collective(g, sessions[gid])
        B["form"][gid] = {key: (form(p["person"], p["activity_id"]) if p["role"] != "other" else None)
                          for key, p in _keys(g)}
    B["order"] = sorted(groups, key=lambda x: (groups[x].get("start_utc") or groups[x]["date"], x))
    B["built_s"] = round(time.time() - T0, 3)
    _BASE.update(key=k, base=B)
    return B


def collective(g, s) -> dict:
    """sums over the members (role other left out): km, moving s, reps (every full rep each member ran), climb, kcal"""
    ms = _members(g)
    per = {}
    km = mov = climb = kcal = 0.0
    reps = 0
    lb = {x["person"]: x for x in ((s or {}).get("intervals") or {}).get("leaderboard") or []}
    for key, p in _keys(g):
        if p not in ms:
            continue
        a = _acts(p["person"]).get(str(p["activity_id"]), {})
        n_r = (lb.get(key) or {}).get("reps_done")
        n_r = n_r if n_r is not None else (p.get("n_reps") or 0)
        x = {"km": _r(p.get("dist_km") or a.get("dist_km") or 0, 2), "moving_s": round(a.get("moving_s") or p.get("dur_s") or 0),
             "dur_s": p.get("dur_s"), "climb_m": round(a["climb_m"]) if a.get("climb_m") is not None else None,
             "kcal": round(a["kcal"]) if a.get("kcal") else None, "reps": n_r, "avg_hr": a.get("hr")}
        per[key] = x
        km += x["km"] or 0
        mov += x["moving_s"] or 0
        climb += x["climb_m"] or 0
        kcal += x["kcal"] or 0
        reps += n_r or 0
    return {"km": _r(km, 1), "moving_s": round(mov), "hours": _r(mov / 3600, 2), "reps": reps,
            "climb_m": round(climb), "kcal": round(kcal) or None, "n_members": len(per), "per_person": per}


# ------------------------------------------------------------------ list
def _photos_out(gid, e):
    ph = [photo_out(gid, p) for p in e["photos"]]
    if e.get("cover"):
        ph.sort(key=lambda p: p["id"] != e["cover"])
    return ph


def _meta(gid, e, B) -> dict:
    cat, why = B["cat"].get(gid, ("other", ""))
    return {"title": e.get("title"), "category": e.get("category") or cat, "category_auto": cat,
            "category_reason": why, "category_manual": bool(e.get("category")), "note": e.get("note"),
            "guests": e.get("guests") or [], "photos": _photos_out(gid, e), "cover": e.get("cover"),
            "kudos": e.get("kudos") or [], "hidden": bool(e.get("hidden"))}


def _form_short(f):
    """delta per member; None when there is no score or form_delta calls it unreliable (confidence "none")"""
    return {k: v.get("delta") if v and v.get("confidence") != "none" else None for k, v in f.items()}


def community_fields(gid, B, store, tdoc, viewer=None) -> dict:
    g = B["groups"][gid]
    e = entry(gid, store, tdoc)
    m = _meta(gid, e, B)
    ph = m["photos"]
    co = B["collective"][gid]
    fm = B["form"].get(gid, {})
    timed = [k for k, p in _keys(g) if p["role"] != "other" and p["has_fit"]]
    return {"category": m["category"], "category_auto": m["category_auto"], "category_manual": m["category_manual"],
            "title": m["title"], "display_title": m["title"] or g.get("workout") or g["label_text"], "note": m["note"],
            "n_timed": len({k.split("#")[0] for k in timed}), "timed": timed,
            "n_members": len({p["person"] for p in _members(g)}),
            "guests": m["guests"], "n_guests": len(m["guests"]),
            "photo": {"thumb": ph[0]["thumb"], "url": ph[0]["url"], "w": ph[0]["w"], "h": ph[0]["h"]} if ph else None,
            "n_photos": len(ph), "kudos": m["kudos"], "n_kudos": len(m["kudos"]),
            "kudoed": (viewer in m["kudos"]) if viewer else None, "mine": (viewer in g["people"]) if viewer else None,
            "user_hidden": m["hidden"],
            "collective": {k: v for k, v in co.items() if k != "per_person"},
            "form": _form_short(fm),
            "form_info": {k: ({kk: v.get(kk) for kk in ("delta", "text", "confidence", "discipline", "score", "shape")}
                              if v else None) for k, v in fm.items()}}


def categories_out(counts: Counter) -> list[dict]:
    return [{**CAT_INFO[c], "n": counts.get(c, 0)} for c in CAT_IDS]


def list_sessions(category=None, person=None, date_from=None, date_to=None, q=None, near=False, sport=None, kind=None,
                  label=None, place=None, hidden=False, viewer=None) -> dict:
    """the community list: together's rows + community fields; filters like together's + category, q over
    title / note / guests too, hidden=1 also the sessions hidden by the user"""
    T0 = time.time()
    tdoc = tg.load()
    B = base(tdoc)
    store = load_store()
    flt = {"person": person, "sport": sport, "kind": kind, "label": label, "near": near, "place": place,
           "date_from": date_from, "date_to": date_to}
    keep = [g["gid"] for g in tg.rows(tdoc, **flt)]
    cats = set(category.split(",")) if category else None
    ql = q.lower().split() if q else None
    out, counts = [], Counter()
    for gid in keep:
        row = B["rows"].get(gid)
        if row is None:
            continue
        cf = community_fields(gid, B, store, tdoc, viewer)
        if cf["user_hidden"] and not hidden:
            continue
        if ql:
            g = B["groups"][gid]
            info = B["people"]
            hay = " ".join([gid, g["date"], g["label"], g["label_text"], g["kind"], g.get("workout") or "",
                            str(g["place"].get("name")), str(g["place"].get("name_local") or ""),
                            *(info.get(p, {}).get("name", p) for p in g["people"]), *(p.get("workout") or "" for p in g["participants"]),
                            cf["title"] or "", cf["note"] or "", *cf["guests"], cf["category"],
                            CAT_INFO[cf["category"]]["label"]]).lower()
            if not all(w in hay for w in ql):
                continue
        counts[cf["category"]] += 1
        if cats and cf["category"] not in cats:
            continue
        out.append({**row, **cf})
    return {"n": len(out), "viewer": viewer, "built": B["list"].get("built"), "people": B["people"],
            "categories": categories_out(counts),
            "form_available": B["form_available"], "store_error": store.get("error"),
            "groups": out, "compute_ms": round((time.time() - T0) * 1000, 1)}


# ------------------------------------------------------------------ one session
def get_session(gid, viewer=None) -> dict:
    tdoc = tg.load()
    B = base(tdoc)
    g = tg.find(gid, tdoc)
    if g is None:
        raise KeyError(gid)
    gid = g["gid"]
    store = load_store()
    e = entry(gid, store, tdoc)
    m = _meta(gid, e, B)
    co = B["collective"][gid]
    fm = B["form"].get(gid, {})
    info = B["people"]
    s = B["sessions"].get(gid) or {}
    lb = {x["person"]: x for x in (s.get("intervals") or {}).get("leaderboard") or []}
    members = []
    for key, p in _keys(g):
        x = lb.get(key) or {}
        members.append({"key": key, "person": p["person"], "display": info.get(p["person"]), "role": p["role"],
                        "activity_id": p["activity_id"], "workout": p.get("workout"), "type": p.get("type"),
                        "has_fit": p["has_fit"], "timed": p["has_fit"] and p["role"] != "other",
                        "form": fm.get(key), "numbers": co["per_person"].get(key),
                        "rank": x.get("rank"), "reps_done": x.get("reps_done"), "n_counted": x.get("n_counted"),
                        "of": x.get("of"), "avg_s": x.get("avg_s"), "pace_s_km": x.get("pace_s_km"),
                        "by_target": x.get("by_target"), "best": x.get("best")})
    # neighbours (newest-first list order) and earlier sessions at the same place
    vis = [x for x in reversed(B["order"]) if not B["groups"][x]["hidden"]]
    i = vis.index(gid) if gid in vis else None
    same = [x for x in vis if x != gid and B["groups"][x]["place"].get("id") == g["place"].get("id")]
    return {"gid": gid, "requested_gid": gid, "viewer": viewer, "date": g["date"], "start_local": g["start_local"],
            "place": g["place"], "workout": g.get("workout"), "kind": g["kind"], "label": g["label"],
            "label_text": g["label_text"], "people": {p: info.get(p) for p in g["people"]},
            "meta": m, "category_info": CAT_INFO[m["category"]], "categories": categories_out(Counter()),
            "display_title": m["title"] or g.get("workout") or g["label_text"],
            "n_timed": len({k.split("#")[0] for k, p in _keys(g) if p["role"] != "other" and p["has_fit"]}),
            "n_guests": len(m["guests"]), "kudoed": (viewer in m["kudos"]) if viewer else None,
            "collective": co, "form": fm, "form_available": B["form_available"], "members": members,
            "headline": (B["rows"].get(gid) or {}).get("headline"),
            "nav": {"newer": vis[i - 1] if i else None, "older": vis[i + 1] if i is not None and i + 1 < len(vis) else None},
            "same_place": [{"gid": x, "date": B["groups"][x]["date"], "workout": B["groups"][x].get("workout"),
                            "category": (entry(x, store, tdoc).get("category") or B["cat"][x][0]),
                            "people": B["groups"][x]["people"]} for x in same[:12]],
            "store_error": store.get("error")}


# ------------------------------------------------------------------ club dashboard
def _visible(B, store, tdoc, date_from=None, date_to=None):
    out = []
    for gid in B["order"]:
        g = B["groups"][gid]
        if g["hidden"] or entry(gid, store, tdoc).get("hidden"):
            continue
        if (date_from and g["date"] < date_from) or (date_to and g["date"] > date_to):
            continue
        out.append(gid)
    return out


def _streaks(dates: list[str], today: str) -> dict:
    ws = sorted({_week_index(d) for d in dates})
    best = cur = 0
    run, prev = 0, None
    for w in ws:
        run = run + 1 if prev is not None and w == prev + 1 else 1
        best = max(best, run)
        prev = w
    if ws and _week_index(today) - ws[-1] <= 1:      # alive: a session this week or last week
        cur = run
    return {"streak_weeks": cur, "best_streak_weeks": best, "weeks_active": len(ws)}


def club(date_from=None, date_to=None, viewer=None) -> dict:
    T0 = time.time()
    tdoc = tg.load()
    B = base(tdoc)
    store = load_store()
    gids = _visible(B, store, tdoc, date_from, date_to)
    G = B["groups"]
    info = B["people"]
    today = date_to or dt.date.today().isoformat()
    cat_of = {gid: entry(gid, store, tdoc).get("category") or B["cat"][gid][0] for gid in gids}

    # totals + months
    months = defaultdict(lambda: {"sessions": 0, "km": 0.0, "moving_s": 0, "reps": 0,
                                  "climb_m": 0, "members": set(), "by_category": Counter(), "gids": []})
    for gid in gids:
        co = B["collective"][gid]
        mth = months[G[gid]["date"][:7]]
        mth["sessions"] += 1
        mth["km"] += co["km"]
        mth["moving_s"] += co["moving_s"]
        mth["reps"] += co["reps"]
        mth["climb_m"] += co["climb_m"]
        mth["members"] |= {p["person"] for p in _members(G[gid])}
        mth["by_category"][cat_of[gid]] += 1
        mth["gids"].append(gid)
    series = []
    if months:   # every month between the first and the last (zeros included, for a chart)
        y, m = map(int, min(months).split("-"))
        end = max(months)
        while f"{y:04d}-{m:02d}" <= end:
            k = f"{y:04d}-{m:02d}"
            x = months.get(k) or {"sessions": 0, "km": 0.0, "moving_s": 0, "reps": 0,
                                  "climb_m": 0, "members": set(), "by_category": Counter(), "gids": []}
            series.append({"month": k, "sessions": x["sessions"], "km": _r(x["km"], 1), "hours": _r(x["moving_s"] / 3600, 1),
                           "reps": x["reps"],
                           "climb_m": x["climb_m"], "members": sorted(x["members"]), "n_members": len(x["members"]),
                           "by_category": dict(x["by_category"]), "gids": x["gids"]})
            m += 1
            if m == 13:
                y, m = y + 1, 1
    co_all = [B["collective"][g] for g in gids]
    guests = Counter(n for gid in gids for n in entry(gid, store, tdoc).get("guests") or [])
    totals = {"sessions": len(gids), "km": _r(sum(c["km"] for c in co_all), 1),
              "hours": _r(sum(c["moving_s"] for c in co_all) / 3600, 1),
              "reps": sum(c["reps"] for c in co_all), "climb_m": sum(c["climb_m"] for c in co_all),
              "kcal": sum(c["kcal"] or 0 for c in co_all),
              "members": len({p["person"] for g in gids for p in _members(G[g])}), "guests": len(guests),
              "guest_visits": sum(guests.values()),
              "photos": sum(len(entry(g, store, tdoc).get("photos") or []) for g in gids),
              "kudos": sum(len(entry(g, store, tdoc).get("kudos") or []) for g in gids),
              "places": len({G[g]["place"].get("id") for g in gids}),
              "first": G[gids[0]]["date"] if gids else None, "last": G[gids[-1]]["date"] if gids else None}
    year = today[:4]
    ytd = [g for g in gids if G[g]["date"][:4] == year]
    totals["this_year"] = {"year": year, "sessions": len(ytd), "km": _r(sum(B["collective"][g]["km"] for g in ytd), 1),
                           "hours": _r(sum(B["collective"][g]["moving_s"] for g in ytd) / 3600, 1)}

    # attendance
    att = defaultdict(lambda: {"sessions": 0, "dates": [], "km": 0.0, "moving_s": 0, "reps": 0, "by_category": Counter(),
                               "gids": [], "places": Counter()})
    for gid in gids:
        for key, p in _keys(G[gid]):
            if p["role"] == "other" or "#" in key:
                continue
            a = att[p["person"]]
            a["sessions"] += 1
            a["dates"].append(G[gid]["date"])
            n = B["collective"][gid]["per_person"].get(key) or {}
            a["km"] += n.get("km") or 0
            a["moving_s"] += n.get("moving_s") or 0
            a["reps"] += n.get("reps") or 0
            a["by_category"][cat_of[gid]] += 1
            a["gids"].append(gid)
            a["places"][G[gid]["place"].get("name")] += 1
    attendance = []
    for person, a in att.items():
        fd = [f["delta"] for gid in a["gids"] for k, f in (B["form"].get(gid) or {}).items()
              if f and k.split("#")[0] == person and f.get("delta") is not None and f.get("confidence") != "none"]
        attendance.append({"person": person, "display": info.get(person), "sessions": a["sessions"],
                           "share": _r(a["sessions"] / len(gids), 3) if gids else None,
                           "first_seen": min(a["dates"]), "last_seen": max(a["dates"]), "last_gid": a["gids"][-1],
                           "days_since": (dt.date.fromisoformat(today) - dt.date.fromisoformat(max(a["dates"]))).days,
                           **_streaks(a["dates"], today), "km": _r(a["km"], 1), "hours": _r(a["moving_s"] / 3600, 1),
                           "reps": a["reps"], "by_category": dict(a["by_category"]),
                           "favourite_place": a["places"].most_common(1)[0][0] if a["places"] else None,
                           "avg_form_delta": _r(sum(fd) / len(fd), 2) if fd else None, "n_form": len(fd)})
    attendance.sort(key=lambda x: (x["sessions"], x["last_seen"]), reverse=True)

    highlights = _highlights(B, gids, info)
    places = Counter(G[g]["place"].get("id") for g in gids)
    place_rows = [{"id": pid, "name": next(G[g]["place"].get("name") for g in gids if G[g]["place"].get("id") == pid),
                   "sessions": n, "last": max(G[g]["date"] for g in gids if G[g]["place"].get("id") == pid)}
                  for pid, n in places.most_common()]
    M = motivation(B, store, tdoc)
    company = {}
    for a in attendance:
        ce = company_effect(B, store, tdoc, a["person"], M["sessions"].get(a["person"]), date_from, date_to) \
            if (date_from or date_to) else M["company"].get(a["person"])
        if ce:
            company[a["person"]] = _company_short(ce)
            a["company"] = company[a["person"]]
    G_ = goals(B, store, tdoc)
    return {"from": date_from, "to": date_to, "today": today, "viewer": viewer, "people": info, "totals": totals,
            "months": series, "attendance": attendance,
            "categories": [{**CAT_INFO[c], "n": sum(1 for g in gids if cat_of[g] == c),
                            "km": _r(sum(B["collective"][g]["km"] for g in gids if cat_of[g] == c), 1),
                            "hours": _r(sum(B["collective"][g]["moving_s"] for g in gids if cat_of[g] == c) / 3600, 1)}
                           for c in CAT_IDS],
            "highlights": highlights,
            "places": place_rows, "guests": [{"name": n, "visits": v} for n, v in guests.most_common()],
            "company": company, "goal": G_["goal"], "goals": G_["goals"],
            "goal_metrics": G_["metrics"],
            "form_available": B["form_available"], "store_error": store.get("error"),
            "compute_ms": round((time.time() - T0) * 1000, 1)}


def _highlights(B, gids, info) -> list[dict]:
    """the standout moments over the given groups: closest finish (rep / race), biggest form jump, longest session,
    biggest turnout, most km together, most reps, most climb; each {kind, gid, date, person?, value, unit, text}"""
    G = B["groups"]
    nm = lambda p: (info.get(p.split("#")[0]) or {}).get("name", p)  # noqa: E731
    out = []
    cands = []
    for gid in gids:
        s = B["sessions"].get(gid) or {}
        iv = s.get("intervals") or {}
        for st in iv.get("standings") or []:
            o = st["order"]
            if len(o) >= 2:
                cands.append((o[1]["gap_s"], gid, f"Rep {st['label']}", o[0]["person"], o[1]["person"]))
        r = (s.get("continuous") or {}).get("race") or []
        if len(r) >= 2:
            cands.append((r[1]["gap_s"], gid, "the race", r[0]["person"], r[1]["person"]))
    if cands:
        gap, gid, what, a, b = min(cands)
        out.append({"kind": "closest_finish", "gid": gid, "date": G[gid]["date"], "person": a, "other": b, "value": gap,
                    "unit": "s", "text": f"{what}: {nm(a)} and {nm(b)} {gap:.1f} s apart" if gap > 0
                    else f"{what}: {nm(a)} and {nm(b)} level"})
    fj = [(f["delta"], gid, k) for gid in gids for k, f in (B["form"].get(gid) or {}).items() if f and f.get("delta") is not None and f.get("confidence") not in ("none", "low")]
    if fj:
        d, gid, k = max(fj)
        out.append({"kind": "form_jump", "gid": gid, "date": G[gid]["date"], "person": k.split("#")[0], "value": d,
                    "unit": "VDOT", "text": f"{nm(k)} {d:+.1f} vs their form (VDOT)"})
    best = lambda f: max(gids, key=f) if gids else None  # noqa: E731
    for kind, f, unit, txt in (
        ("longest_session", lambda g: _together_s(G[g]), "min", lambda g, v: f"{v / 60:.0f} min, the longest session"),
        ("most_km", lambda g: B["collective"][g]["km"], "km", lambda g, v: f"{v:.1f} km between us"),
        ("most_reps", lambda g: B["collective"][g]["reps"], "reps", lambda g, v: f"{v} reps in one session"),
        ("most_climb", lambda g: B["collective"][g]["climb_m"], "m", lambda g, v: f"{v} m climbed together"),
        ("biggest_turnout", lambda g: B["collective"][g]["n_members"], "members", lambda g, v: f"{v} members"),
    ):
        g = best(f)
        if g is not None and f(g) and not (kind == "biggest_turnout" and f(g) < 3):
            v = f(g)
            out.append({"kind": kind, "gid": g, "date": G[g]["date"], "person": None,
                        "value": round(v / 60) if unit == "min" else v, "unit": unit, "text": txt(g, v)})
    return out


# ------------------------------------------------------------------ members
def members(viewer=None) -> dict:
    c = club(viewer=viewer)
    return {"people": c["people"], "members": c["attendance"]}


def _member_gids(B, store, tdoc, person) -> list[str]:
    G = B["groups"]
    return [g for g in _visible(B, store, tdoc) if person in G[g]["people"]
            and any(p["person"] == person and p["role"] != "other" for p in G[g]["participants"])]


def _person_sessions(B, store, tdoc, person, gids=None) -> list[dict]:
    """a member's community sessions, oldest first (the rows of member()["sessions"] + _key / _aid)"""
    G = B["groups"]
    gids = _member_gids(B, store, tdoc, person) if gids is None else gids
    sess = []
    for gid in gids:
        g = G[gid]
        e = entry(gid, store, tdoc)
        keys = [k for k, p in _keys(g) if p["person"] == person and p["role"] != "other"]
        if not keys:
            continue
        k = keys[0]
        part = next(p for kk, p in _keys(g) if kk == k)
        f = (B["form"].get(gid) or {}).get(k)
        n = B["collective"][gid]["per_person"].get(k) or {}
        s = B["sessions"].get(gid) or {}
        lb = {x["person"]: x for x in (s.get("intervals") or {}).get("leaderboard") or []}
        race = {x["person"]: x for x in (s.get("continuous") or {}).get("race") or []}
        x = lb.get(k) or {}
        sess.append({"gid": gid, "date": g["date"], "start_local": g["start_local"], "place": g["place"].get("name"),
                     "workout": part.get("workout"), "group_workout": g.get("workout"), "title": e.get("title"),
                     "category": e.get("category") or B["cat"][gid][0], "with": [p for p in g["people"] if p != person],
                     "role": part["role"], "form": f, "numbers": n,
                     "rank": x.get("rank") or (race.get(k) or {}).get("rank"), "of_people": len(lb) or len(race) or None,
                     "reps_done": x.get("reps_done"), "avg_s": x.get("avg_s"), "pace_s_km": x.get("pace_s_km"),
                     "n_photos": len(e.get("photos") or []), "kudos": len(e.get("kudos") or []),
                     "_key": k, "_aid": str(part["activity_id"])})
    return sess


def _public(rows):
    return [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]


def member(person: str, viewer=None) -> dict:
    T0 = time.time()
    tdoc = tg.load()
    B = base(tdoc)
    store = load_store()
    G = B["groups"]
    gids = _member_gids(B, store, tdoc, person)
    if not gids and person not in B["people"]:
        raise KeyError(person)
    info = B["people"]
    today = dt.date.today().isoformat()
    sess = _person_sessions(B, store, tdoc, person, gids)
    form_series, partners = [], defaultdict(lambda: {"sessions": 0, "last": None, "gids": []})
    for s_ in sess:
        f = s_["form"]
        if f and f.get("delta") is not None and f.get("confidence") != "none":
            form_series.append({"date": s_["date"], "gid": s_["gid"], **f})
        for p in G[s_["gid"]]["people"]:
            if p != person:
                pp = partners[p]
                pp["sessions"] += 1
                pp["last"] = s_["date"]
                pp["gids"].append(s_["gid"])
    plist = [{"person": p, "display": info.get(p), **x} for p, x in sorted(partners.items(), key=lambda kv: -kv[1]["sessions"])]
    totals = {"sessions": len(sess), "km": _r(sum((s["numbers"].get("km") or 0) for s in sess), 1),
              "hours": _r(sum((s["numbers"].get("moving_s") or 0) for s in sess) / 3600, 1),
              "reps": sum((s["numbers"].get("reps") or 0) for s in sess),
              "climb_m": sum((s["numbers"].get("climb_m") or 0) for s in sess),
              "first": sess[0]["date"] if sess else None, "last": sess[-1]["date"] if sess else None,
              **_streaks([s["date"] for s in sess], today),
              "avg_form_delta": _r(sum(f["delta"] for f in form_series) / len(form_series), 2) if form_series else None}
    return {"person": person, "display": info.get(person), "viewer": viewer, "totals": totals,
            "by_category": dict(Counter(s["category"] for s in sess)),
            "sessions": _public(list(reversed(sess))), "form_series": form_series, "partners": plist,
            "company": company_effect(B, store, tdoc, person, sess),
            "form_available": B["form_available"], "compute_ms": round((time.time() - T0) * 1000, 1)}


# ------------------------------------------------------------------ motivation: company effect, goals
COMPANY_WINDOW_D = 60         # solo form deltas of the same discipline within ± this many days of a joint session
PACE_WINDOW_D = 90            # solo interval sessions of the same rep family within ± this many days
COMPANY_EVEN = 0.3            # |diff| below this (VDOT) = "about the same"


def _form_deltas_all(person) -> dict:
    """{activity id: form_delta} for every scored session of the person (form_delta.form_deltas), {} without it"""
    mod = _form_module()
    if mod is None or not hasattr(mod, "form_deltas"):
        return {}
    k = ("all", person, _FORM_MOD["mtime"], _mt(DATA / person / "fitness.json"))
    if k not in _FORM_CACHE:
        try:
            _FORM_CACHE[k] = mod.form_deltas(person) or {}
        except Exception:  # noqa: BLE001
            _FORM_CACHE[k] = {}
    return _FORM_CACHE[k]


def _joint_aids(B, person) -> set[str]:
    """every activity of the person that is in any together group (hidden / near-miss too): never "solo" """
    return {str(p["activity_id"]) for g in B["groups"].values() for p in g["participants"] if p["person"] == person}


def _rep_family(reps) -> tuple[str, float, float] | None:
    """(family, pace s/km, median rep m) from groups.json reps [[dur_s, dist_m, speed, hr], ...]: the reps near the
    median distance (warm-up blocks and strides dropped). short < 600 m ≤ medium < 1400 m ≤ long"""
    rs = [r for r in reps or [] if isinstance(r, (list, tuple)) and len(r) >= 2 and (r[1] or 0) >= 150 and (r[0] or 0) >= 20]
    if len(rs) < 2:
        return None
    ds = sorted(r[1] for r in rs)
    med = ds[len(ds) // 2]
    keep = [r for r in rs if 0.6 * med <= r[1] <= 1.6 * med]
    if len(keep) < 2:
        return None
    fam = "short" if med < 600 else "medium" if med < 1400 else "long"
    return fam, sum(r[0] for r in keep) / sum(r[1] for r in keep) * 1000, med


REP_FAMILY = {"short": "short reps (< 600 m)", "medium": "medium reps (600 m – 1.4 km)", "long": "long reps (≥ 1.4 km)"}


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def company_effect(B, store, tdoc, person, sess=None, date_from=None, date_to=None) -> dict:
    """"Faster together than alone?": the person's form deltas in joint sessions vs their solo sessions of the same
    discipline within ±60 days of a joint one (same period), and interval rep pace per rep family (short / medium /
    long reps) joint vs solo within ±90 days. diff > 0 = better with company."""
    sess = _person_sessions(B, store, tdoc, person) if sess is None else sess
    sess = [s for s in sess if (not date_from or s["date"] >= date_from) and (not date_to or s["date"] <= date_to)]
    nm = (B["people"].get(person) or {}).get("name", person)
    joint_aids = _joint_aids(B, person)
    joint = [{"gid": s["gid"], "date": s["date"], "delta": s["form"]["delta"], "discipline": s["form"].get("discipline"),
              "confidence": s["form"].get("confidence")}
             for s in sess if s["form"] and s["form"].get("delta") is not None and s["form"].get("confidence") != "none"]
    solo_all = [{"activity_id": aid, "date": f.get("date"), "delta": f["delta"], "discipline": f.get("discipline"),
                 "confidence": f.get("confidence")}
                for aid, f in _form_deltas_all(person).items()
                if aid not in joint_aids and f.get("delta") is not None and f.get("confidence") != "none" and f.get("date")]
    dn = lambda d: dt.date.fromisoformat(d).toordinal()  # noqa: E731
    solo = [x for x in solo_all if any(j["discipline"] == x["discipline"] and abs(dn(j["date"]) - dn(x["date"])) <= COMPANY_WINDOW_D
                                       for j in joint)]
    acts = _acts(person)
    types = {aid: a.get("type") for aid, a in acts.items()}
    for x in solo:
        x["type"] = types.get(x["activity_id"])
        x["name"] = (acts.get(x["activity_id"]) or {}).get("name")
    by_disc = []
    for d in sorted({j["discipline"] for j in joint}):
        js, ss = [j["delta"] for j in joint if j["discipline"] == d], [x["delta"] for x in solo if x["discipline"] == d]
        ja, sa = _mean(js), _mean(ss)
        by_disc.append({"discipline": d, "n_joint": len(js), "n_solo": len(ss), "joint_avg": _r(ja, 2), "solo_avg": _r(sa, 2),
                        "diff": _r(ja - sa, 2) if ja is not None and sa is not None else None})
    # paired: each joint session against the solo sessions of its discipline in its own window
    # joint_avg / solo_avg are over exactly the sessions in these pairs, so "together x (n) · alone y (m)" adds up to diff
    # (solo_avg = the mean of each pair's solo baseline, so together − alone = diff exactly)
    pairs, j_used, base, s_used = [], [], [], set()
    for j in joint:
        w = [x for x in solo if x["discipline"] == j["discipline"] and abs(dn(j["date"]) - dn(x["date"])) <= COMPANY_WINDOW_D]
        if w:
            b = sum(x["delta"] for x in w) / len(w)
            pairs.append(j["delta"] - b)
            j_used.append(j["delta"])
            base.append(b)
            s_used.update(x["activity_id"] for x in w)
    ja = _mean(j_used)
    sa = _mean(base)
    diff = _mean(pairs)
    n_cmp = len(pairs)
    verdict = None if diff is None else "company" if diff >= COMPANY_EVEN else "alone" if diff <= -COMPANY_EVEN else "even"
    text = (None if diff is None else f"{nm} runs {diff:+.1f} better with company" if verdict == "company"
            else f"{nm} runs {abs(diff):.1f} better alone" if verdict == "alone" else f"{nm} runs about the same with company and alone")
    # rep pace per family
    pace = []
    jfam = []
    for s in sess:
        if s["category"] != "intervals":
            continue
        rf = _rep_family(_reps(person, s["_aid"]))
        if rf:
            jfam.append({"gid": s["gid"], "date": s["date"], "family": rf[0], "pace_s_km": rf[1], "rep_m": rf[2]})
    sfam = []
    for aid, a in acts.items():
        if aid in joint_aids or not str(a.get("type") or "").startswith("intervals") or not a.get("start_local"):
            continue
        rf = _rep_family(_reps(person, aid))
        if rf:
            sfam.append({"activity_id": aid, "date": a["start_local"][:10], "family": rf[0], "pace_s_km": rf[1], "rep_m": rf[2]})
    for fam in ("short", "medium", "long"):
        js = [x for x in jfam if x["family"] == fam]
        ss = [x for x in sfam if x["family"] == fam and any(abs(dn(j["date"]) - dn(x["date"])) <= PACE_WINDOW_D for j in js)]
        if not js or not ss:
            continue
        jp, sp = _mean([x["pace_s_km"] for x in js]), _mean([x["pace_s_km"] for x in ss])
        d = sp - jp
        pace.append({"family": fam, "label": REP_FAMILY[fam], "n_joint": len(js), "n_solo": len(ss),
                     "joint_s_km": _r(jp, 1), "solo_s_km": _r(sp, 1), "diff_s_km": _r(d, 1),
                     "text": (f"{abs(d):.0f} s/km faster per rep with company" if d >= 1 else
                              f"{abs(d):.0f} s/km faster per rep alone" if d <= -1 else "same rep pace with company and alone"),
                     "joint": [{"gid": x["gid"], "date": x["date"], "pace_s_km": _r(x["pace_s_km"], 1)} for x in js],
                     "solo": [{"activity_id": x["activity_id"], "date": x["date"], "pace_s_km": _r(x["pace_s_km"], 1)}
                              for x in sorted(ss, key=lambda x: x["date"])]})
    return {"person": person, "window_days": COMPANY_WINDOW_D, "n_joint": len(joint), "n_solo": len(solo), "n_compared": n_cmp,
            "n_joint_cmp": len(j_used), "n_solo_cmp": len(s_used),
            "joint_avg": _r(ja, 2), "solo_avg": _r(sa, 2), "diff": _r(diff, 2), "verdict": verdict, "text": text,
            "thin": n_cmp < 3 or len(solo) < 3, "by_discipline": by_disc, "pace": pace,
            "joint": joint, "solo": sorted(solo, key=lambda x: x["date"])}


def _reps(person, aid):
    pg = DATA / person / "groups.json"
    return (((_json(pg) or {}).get("activities") or {}).get(str(aid)) or {}).get("reps")


def _all_members(B, store, tdoc) -> list[str]:
    G = B["groups"]
    return sorted({p["person"] for g in _visible(B, store, tdoc) for p in G[g]["participants"] if p["role"] != "other"})


def motivation(B, store, tdoc) -> dict:
    """per member: sessions (oldest first) and the company effect (shared by club / members)"""
    ps = _all_members(B, store, tdoc)
    sess = {p: _person_sessions(B, store, tdoc, p) for p in ps}
    return {"sessions": sess, "company": {p: company_effect(B, store, tdoc, p, sess[p]) for p in ps}}


def _company_short(c):
    return {k: c.get(k) for k in ("diff", "verdict", "text", "n_joint", "n_solo", "n_compared", "n_joint_cmp", "n_solo_cmp", "joint_avg", "solo_avg", "thin")} \
        | {"pace": [{k: x[k] for k in ("family", "label", "diff_s_km", "text", "n_joint", "n_solo", "joint_s_km", "solo_s_km")}
                    for x in c.get("pace") or []]}


# ---- goals (community.json "goals": [{id, title, metric, target, from, to, created}])
GOAL_METRICS = {"sessions": ("sessions", "sessions"), "km": ("km", "km together"), "hours": ("h", "hours moving"),
                "reps": ("reps", "reps"),
                "climb_m": ("m", "metres climbed")}


def _season(today: dt.date):
    y, m = today.year, today.month
    if m in (12, 1, 2):
        y0 = y if m == 12 else y - 1
        return "Winter", dt.date(y0, 12, 1), dt.date(y0 + 1, 3, 1) - dt.timedelta(days=1)
    for name, a, b in (("Spring", 3, 5), ("Summer", 6, 8), ("Autumn", 9, 11)):
        if a <= m <= b:
            return name, dt.date(y, a, 1), (dt.date(y, b + 1, 1) - dt.timedelta(days=1))
    raise AssertionError


def default_goal(today=None) -> dict:
    today = today or dt.date.today()
    name, a, b = _season(today)
    return {"id": "default", "title": f"12 sessions this {name.lower()}", "metric": "sessions", "target": 12,
            "from": a.isoformat(), "to": b.isoformat(), "default": True}


def _norm_goal(g) -> dict | None:
    if not isinstance(g, dict):
        return None
    try:
        tgt = float(g.get("target"))
        a, b = dt.date.fromisoformat(str(g.get("from"))[:10]), dt.date.fromisoformat(str(g.get("to"))[:10])
    except (TypeError, ValueError):
        return None
    if g.get("metric") not in GOAL_METRICS or tgt <= 0 or b < a:
        return None
    gid = str(g.get("id") or "") or hashlib.sha1(json.dumps(g, sort_keys=True, default=str).encode()).hexdigest()[:8]
    return {**g, "id": gid, "title": _clean_text(g.get("title"), TEXT_MAX["title"]) or f"{tgt:g} {GOAL_METRICS[g['metric']][1]}",
            "target": int(tgt) if tgt == int(tgt) else tgt, "from": a.isoformat(), "to": b.isoformat()}


def stored_goals(store=None) -> list[dict]:
    store = store or load_store()
    return [x for x in (_norm_goal(g) for g in store.get("goals") or []) if x]


def goal_progress(B, store, tdoc, g, today=None) -> dict:
    today = today or dt.date.today()
    gids = _visible(B, store, tdoc, g["from"], g["to"])
    C = B["collective"]
    m = g["metric"]

    def val(gid, per=None):
        c = C[gid] if per is None else (C[gid]["per_person"].get(per) or {})
        if m == "sessions":
            return 1
        if m == "km":
            return c.get("km") or 0
        if m == "hours":
            return (c.get("moving_s") or 0) / 3600
        if m == "reps":
            return c.get("reps") or 0
        return c.get("climb_m") or 0
    v = sum(val(x) for x in gids)
    per = Counter()
    for x in gids:
        for k in C[x]["per_person"]:
            if "#" not in k:
                per[k] += val(x, k)
    a, b = dt.date.fromisoformat(g["from"]), dt.date.fromisoformat(g["to"])
    days = (b - a).days + 1
    gone = min(days, max(0, (today - a).days + 1))
    expected = g["target"] * gone / days
    left = max(0, (b - today).days) if today <= b else 0
    status = ("done" if v >= g["target"] else "upcoming" if today < a else "ended" if today > b
              else "ahead" if v >= expected * 1.1 else "on_track" if v >= expected * 0.9 else "behind")
    unit, what = GOAL_METRICS[m]
    fmt = lambda x: f"{x:.0f}" if m in ("sessions", "reps", "climb_m") or x >= 100 else f"{x:.1f}"  # noqa: E731
    togo = max(0.0, g["target"] - v)
    text = (f"{fmt(v)} of {fmt(g['target'])} {unit}" + (" · done 🎉" if status == "done" else
            f" · {fmt(togo)} to go · {left} days left" if status not in ("ended", "upcoming") else
            " · ended" if status == "ended" else f" · starts {g['from']}"))
    return {**g, "unit": unit, "metric_label": what, "value": _r(v, 1), "pct": _r(min(1.0, v / g["target"]), 3),
            "expected": _r(expected, 1), "expected_pct": _r(min(1.0, expected / g["target"]), 3), "remaining": _r(togo, 1),
            "days": days, "days_left": left, "status": status, "text": text, "n_sessions": len(gids),
            "last_gid": gids[-1] if gids else None,
            "by_member": [{"person": p, "value": _r(x, 1)} for p, x in per.most_common()],
            "default": bool(g.get("default"))}


def goals(B=None, store=None, tdoc=None, today=None) -> dict:
    """{goal: the primary goal with progress (first running one, else the newest; default "12 sessions this <season>"
    when none is stored), goals: [all with progress], metrics}"""
    tdoc = tdoc or tg.load()
    B = B or base(tdoc)
    store = store or load_store()
    today = today or dt.date.today()
    gs = stored_goals(store) or [default_goal(today)]
    rows = [goal_progress(B, store, tdoc, g, today) for g in gs]
    running = [r for r in rows if r["from"] <= today.isoformat() <= r["to"]]
    prim = running[0] if running else max(rows, key=lambda r: r["to"])
    return {"goal": prim, "goals": rows, "metrics": [{"id": k, "unit": u, "label": w} for k, (u, w) in GOAL_METRICS.items()]}


def _validate_goal_body(body, partial=True):
    allowed = {"title", "metric", "target", "from", "to"}
    bad = set(body) - allowed
    if bad:
        raise ValueError(f"unknown fields: {', '.join(sorted(bad))} (allowed: {', '.join(sorted(allowed))})")
    if not partial and not {"metric", "target", "from", "to"} <= set(body):
        raise ValueError("metric, target, from and to are required")
    if "metric" in body and body["metric"] not in GOAL_METRICS:
        raise ValueError(f"metric must be one of {', '.join(GOAL_METRICS)}")
    if "target" in body:
        try:
            if float(body["target"]) <= 0:
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError("target: a positive number")
    for k in ("from", "to"):
        if k in body:
            try:
                dt.date.fromisoformat(str(body[k]))
            except ValueError:
                raise ValueError(f"{k}: a date YYYY-MM-DD")


def _mutate_goals(fn):
    with _WLOCK:
        _STORE_MEMO["key"] = None
        doc = load_store()
        gs = stored_goals(doc)
        gs = fn(gs)
        doc["goals"] = gs
        _save_store(doc)
        return gs


def patch_goal(goal_id: str, body: dict) -> dict:
    """edit a goal (id "default" = the built-in default: stored on first edit) -> the stored goal"""
    _validate_goal_body(body)
    out = {}

    def fn(gs):
        cur = next((g for g in gs if g["id"] == goal_id), None)
        if cur is None:
            if goal_id != "default":
                raise KeyError(goal_id)
            cur = {k: v for k, v in default_goal().items() if k != "default"}
            cur["id"] = hashlib.sha1(_now_iso().encode()).hexdigest()[:8]
            cur["created"] = _now_iso()
            gs = [cur] + gs
        new = _norm_goal({**cur, **body, **({"title": body["title"]} if "title" in body else {})})
        if new is None:
            raise ValueError("invalid goal (from must be ≤ to)")
        gs = [new if g["id"] == cur["id"] else g for g in gs]
        out["g"] = new
        return gs
    _mutate_goals(fn)
    return out["g"]


def add_goal(body: dict) -> dict:
    _validate_goal_body(body, partial=False)
    g = _norm_goal({**body, "id": hashlib.sha1((_now_iso() + json.dumps(body, sort_keys=True, default=str)).encode()).hexdigest()[:8],
                    "created": _now_iso()})
    if g is None:
        raise ValueError("invalid goal (from must be ≤ to)")
    _mutate_goals(lambda gs: [g] + gs)
    return g


def delete_goal(goal_id: str):
    found = {}

    def fn(gs):
        found["x"] = any(g["id"] == goal_id for g in gs)
        return [g for g in gs if g["id"] != goal_id]
    _mutate_goals(fn)
    if not found["x"]:
        raise KeyError(goal_id)


# ------------------------------------------------------------------ CLI
def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["list", "club", "member", "show", "categories"])
    ap.add_argument("arg", nargs="?")
    a = ap.parse_args()
    if a.cmd == "list":
        r = list_sessions(near=True)
        for x in r["groups"]:
            c = x["collective"]
            print(f"{x['gid']:38} {x['category']:9} {x['n_timed']}+{x['n_guests']} {c['km']:6.1f} km "
                  f"{c['hours']:5.2f} h  form {x['form']}")
        print(f"{r['compute_ms']} ms")
    elif a.cmd == "categories":
        B = base()
        for gid in reversed(B["order"]):
            print(f"{gid:38} {B['cat'][gid][0]:9} {B['cat'][gid][1]}")
    elif a.cmd == "club":
        print(json.dumps(club(), indent=1, ensure_ascii=False, default=str)[:6000])
    elif a.cmd == "member":
        print(json.dumps(member(a.arg or "adrian"), indent=1, ensure_ascii=False, default=str)[:6000])
    elif a.cmd == "show":
        print(json.dumps(get_session(a.arg), indent=1, ensure_ascii=False, default=str)[:6000])


if __name__ == "__main__":
    main()

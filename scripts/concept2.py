"""Concept2 Logbook workouts (RowErg, SkiErg, BikeErg) as activities of the pipeline (library).

scripts/fetch_concept2.py downloads the raw results into data/<person>/concept2/ and then calls `sync(D)`, which
- writes data/<person>/concept2/activities_index.json: one Garmin-style index row per result (activityId = the
  Concept2 result id, which never collides with Garmin ids: ~1e8 vs >= 3e9), typeKey `indoor_rowing` / `skierg` /
  `bikeerg`, start time, work time / distance, calories, average watts and stroke rate (`c2` holds the Concept2
  extras: workout type, drag factor, intervals);
- links each FIT export into data/<person>/activities/<date>_<id>_<typeKey>/ (with list_item.json), so the FIT
  globs of session_groups / similarity / the app cache pick them up like any watch file.

Hand-logged sessions (no Logbook result, e.g. the monitor wasn't recorded) live in data/<person>/concept2/manual.json
(Logbook-shaped rows with a `manual` block, ids from MANUAL_ID_BASE; the fetch never touches the file) and are merged
into the index by `sync`: totals only, no strokes / FIT, kept out of the Row / SkiErg trend (erg_fitness reads
results.json only).

`load_index(D)` is the one reader of the activity list: Garmin's activities_index.json + the Concept2 rows.
Cleaning (`clean`, docs/concept2.md#cleaning-shared-machine-sessions): a continuous session where friends take turns
on the same machine (1 min on / 1 min off, ...) is detected from the strokes and rewritten as this person's own
interval workout (their turns = reps, the others' turns = rest); `load_result` returns the cleaned result.
`erg_ana(D, aid, laps_c)` gives the rep analysis of an interval workout from its FIT laps (work / rest) and the
Concept2 interval definitions (time / distance / calorie targets), in session_groups' compact `ana` format.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

TYPE_KEY = {"rower": "indoor_rowing", "skierg": "skierg", "bike": "bikeerg", "dynamic": "indoor_rowing",
            "slides": "indoor_rowing", "multierg": "indoor_rowing"}
FAMILY = {"indoor_rowing": "row", "skierg": "skierg", "bikeerg": "bike"}   # typeKey -> sport family
MACHINE = {"indoor_rowing": "RowErg", "skierg": "SkiErg", "bikeerg": "BikeErg"}
ERG_TYPES = tuple(FAMILY)
ERG_SPORTS = ("row", "skierg")
INTERVAL_TYPES = ("FixedTimeInterval", "FixedDistanceInterval", "FixedCalorieInterval", "FixedWattMinuteInterval",
                  "VariableInterval", "VariableIntervalUndefinedRest")
MANUAL_ID_BASE = 2_900_000_000   # ids of hand-logged sessions (manual.json): above Concept2's ~1e8, below Garmin's 3e9
SHORT_PIECE_S = 12 * 60   # a continuous piece of at most this (work time) is a "short piece" (test-like), else steady


def is_erg(type_key: str | None) -> bool:
    return type_key in FAMILY


def watts(pace_s_per_500: float | None, type_key: str = "indoor_rowing") -> float | None:
    """Concept2's pace -> watts: P = 2.80 / (s per m)^3 (BikeErg pace is per 1000 m: s per m = pace / 1000)"""
    if not pace_s_per_500 or pace_s_per_500 <= 0:
        return None
    spm = pace_s_per_500 / (1000 if type_key == "bikeerg" else 500)
    return 2.80 / spm ** 3


def index_row(r: dict) -> dict:
    """Garmin-style index row of one Concept2 result (results/<id>/result.json, else the list row)"""
    tk = TYPE_KEY.get(r.get("type"), "indoor_rowing")
    work_s = (r.get("time") or 0) / 10
    rest_s = (r.get("rest_time") or 0) / 10
    dist = r.get("distance") or 0
    hr = r.get("heart_rate") or {}
    man = r.get("manual")
    pace = work_s / dist * (1000 if tk == "bikeerg" else 500) if dist and work_s else None
    w = (r.get("workout") or {})
    wt = r.get("workout_type") or "unknown"
    return {
        "activityId": r["id"],
        "activityName": f"{MACHINE[tk]} {workout_name(r)}" + (" (shared)" if r.get("shared") else ""),
        "activityType": {"typeKey": tk},
        "startTimeLocal": r["date"][:19],
        "startTimeGMT": (r.get("date_utc") or "")[:19] or None,
        "duration": work_s + rest_s, "elapsedDuration": work_s + rest_s, "movingDuration": work_s,
        "distance": None if man and not dist else dist, "elevationGain": 0, "elevationLoss": 0,
        "averageHR": hr.get("average") or None, "maxHR": hr.get("max") or None,
        "calories": r.get("calories_total"),
        "avgPower": round(watts(pace, tk)) if pace else None,
        "averageStrokeRate": r.get("stroke_rate"),
        "locationName": None, "startLatitude": None, "startLongitude": None,
        "manufacturer": "CONCEPT2", "deviceName": f"Concept2 {MACHINE[tk]} ({'logged by hand' if man else r.get('source') or 'Logbook'})",
        "source": "concept2",
        "c2": {"type": r.get("type"), "user_id": r.get("user_id"), "workout_type": wt, "drag_factor": r.get("drag_factor"),
               "stroke_count": r.get("stroke_count"), "stroke_data": bool(r.get("stroke_data")),
               "rest_distance": r.get("rest_distance"), "rest_time_s": rest_s or None,
               "weight_class": r.get("weight_class"), "comments": r.get("comments"),
               "intervals": [_iv(x) for x in (w.get("intervals") or [])] or None,
               "splits": [_iv(x) for x in (w.get("splits") or [])] or None,
               # shared-machine session (clean): own turns are the intervals; Logbook totals, pattern, others' pace
               "shared": {k: v for k, v in r["shared"].items() if k != "turns"} if r.get("shared") else None,
               # hand-logged (manual.json): what the monitors showed, who was there; no per-person split
               "manual": man},
    }


def _iv(x: dict) -> dict:
    """one Concept2 split / interval, seconds and metres"""
    out = {"type": x.get("type"), "time_s": (x.get("time") or 0) / 10, "dist_m": x.get("distance"),
           "spm": x.get("stroke_rate"), "kcal": x.get("calories_total"),
           "rest_s": (x.get("rest_time") or 0) / 10 if "rest_time" in x else None, "rest_m": x.get("rest_distance"),
           "hr": (x.get("heart_rate") or {}).get("average") or None, "machine": x.get("machine"),
           "start_s": x.get("start_s")}
    tg = x.get("targets") or {}
    if tg.get("pace"):
        out["target_pace_s"] = tg["pace"] / 10
    return {k: v for k, v in out.items() if v is not None}


def mmss(s: float) -> str:
    s = int(round(s))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def workout_name(r: dict) -> str:
    """'12×2:00', '4×500m', '30:00', '2000m', or 'intervals' (variable); a shared session over two machines
    (`second_machine`): its real turns, '15×2:00, 2 machines'"""
    if r.get("name"):   # hand-logged sessions (manual.json) name themselves
        return r["name"]
    sh = r.get("shared") or {}
    if sh.get("second_machine"):
        return f"{len(sh['turns'])}×{mmss(sh['on_s'])}, 2 machines"
    wt = r.get("workout_type") or ""
    ivs = (r.get("workout") or {}).get("intervals") or []
    if wt in INTERVAL_TYPES and ivs:
        lab = [(f"{(x.get('distance') or 0)}m" if x.get("type") == "distance" else
                f"{x.get('calories_total')}cal" if x.get("type") == "calorie" else mmss((x.get("time") or 0) / 10))
               for x in ivs]
        parts, prev, n = [], None, 0
        for l in lab + [None]:
            if l == prev:
                n += 1
                continue
            if prev is not None:
                parts.append(f"{n}×{prev}")
            prev, n = l, 1
        return " + ".join(parts) if len(parts) <= 3 else f"{len(lab)} intervals"
    if wt == "FixedDistanceSplits":
        return f"{r.get('distance')}m"
    return mmss((r.get("time") or 0) / 10)


# ---------------------------------------------------------------- files
def c2_dir(D: Path) -> Path:
    return D / "concept2"


def rows(D: Path) -> list[dict]:
    p = c2_dir(D) / "activities_index.json"
    return json.loads(p.read_text()) if p.exists() else []


def manual(D: Path) -> list[dict]:
    """hand-logged sessions: data/<person>/concept2/manual.json (Logbook-shaped rows + `manual`, see the module doc)"""
    p = c2_dir(D) / "manual.json"
    return json.loads(p.read_text() or "[]") if p.exists() else []


def load_index(D: Path) -> list[dict]:
    """the activity list of a person: Garmin's activities_index.json + the Concept2 rows (concept2/activities_index.json)"""
    p = D / "activities_index.json"
    return (json.loads(p.read_text()) if p.exists() else []) + rows(D)


def index_paths(D: Path) -> list[Path]:
    """files whose mtime changes the merged index (for mtime memo keys)"""
    return [D / "activities_index.json", c2_dir(D) / "activities_index.json"]


def result(D: Path, aid) -> dict | None:
    """the raw Logbook result (results/<id>/result.json)"""
    p = c2_dir(D) / "results" / str(aid) / "result.json"
    return json.loads(p.read_text()) if p.exists() else None


def strokes(D: Path, aid) -> list[dict] | None:
    p = c2_dir(D) / "results" / str(aid) / "strokes.json"
    return json.loads(p.read_text()) if p.exists() else None


def overrides(D: Path) -> dict:
    """{result id str: override} from data/<person>/session_overrides.json, key "erg_shared" (see `clean`)"""
    p = D / "session_overrides.json"
    try:
        o = json.loads(p.read_text() or "{}") if p.exists() else {}
    except ValueError:
        return {}
    return {k: v["erg_shared"] for k, v in o.items() if isinstance(v, dict) and "erg_shared" in v}


def load_result(D: Path, aid, row: dict | None = None) -> dict | None:
    """the result as this person did it: cleaned (shared-machine sessions -> own turns, `clean`), else raw.
    Cleaning is cached in results/<id>/clean.json (rebuilt when the result, strokes or override changes)."""
    r = result(D, aid) or row
    if r is None:
        return None
    d = c2_dir(D) / "results" / str(aid)
    ov = overrides(D).get(str(aid))
    key = [CLEAN_VERSION, ov] + [(p.stat().st_mtime_ns if p.exists() else 0) for p in (d / "result.json", d / "strokes.json")]
    cp = d / "clean.json"
    cached = None
    if cp.exists():
        try:
            c = json.loads(cp.read_text())
            cached = c if c.get("key") == key else None
        except ValueError:
            pass
    if cached is None:
        st = strokes(D, aid) if r.get("stroke_data") else None
        cached = {"key": key, "shared": shared_turns(r, st, ov) if st else None}
        if d.exists():
            cp.write_text(json.dumps(cached, ensure_ascii=False))
    return clean(r, cached["shared"])


# ---------------------------------------------------------------- cleaning: shared-machine sessions
CLEAN_VERSION = 8
SHARED = {"slot_s": 60.0,        # turns change on whole minutes from the start
          "settle_s": 15.0,      # the first 15 s of a slot are left out when telling people apart (flywheel carry-over)
          "min_s": 600.0,        # only continuous sessions of >= 10 min
          "contrast": 0.04,      # this person's slots >= 4 % faster than the others'
          "min_runs": 3,         # >= 3 separate turns ...
          "runs_share": 0.4,     # ... and turns alternate (separate turns >= 40 % of this person's slots)
          "max_other_run": 3,    # nobody else rows more than 3 slots in a row
          "rhythm": 0.8,         # a regular session: >= 80 % of slots follow one on / off rhythm ...
          "off_rhythm_speed": 0.98}  # ... and a last-turn slot out of rhythm is yours only at >= 98 % of your turns' median speed


def _jenks(x, k: int):
    """optimal 1-D k-clustering of x (dynamic programming; n is small) -> (sorted x, [(a, b) index ranges], ssw)"""
    import numpy as np
    xs = np.sort(np.asarray(x, float)); n = len(xs)
    cs = np.concatenate([[0], np.cumsum(xs)]); c2s = np.concatenate([[0], np.cumsum(xs ** 2)])
    ss = lambda a, b: c2s[b] - c2s[a] - (cs[b] - cs[a]) ** 2 / (b - a)
    dp = np.full((k + 1, n + 1), np.inf); arg = np.zeros((k + 1, n + 1), int); dp[0, 0] = 0
    for j in range(1, k + 1):
        for b in range(j, n + 1):
            for a in range(j - 1, b):
                v = dp[j - 1, a] + ss(a, b)
                if v < dp[j, b]:
                    dp[j, b], arg[j, b] = v, a
    cuts, b = [], n
    for j in range(k, 0, -1):
        cuts.append((int(arg[j, b]), b)); b = arg[j, b]
    return xs, sorted(cuts), float(dp[k, n])


def shared_turns(r: dict, st: list[dict] | None, override=None) -> dict | None:
    """Detect a shared-machine session: friends take turns on one erg (1 min on / 1 min off, 2 on / 1 off, 1 on /
    2 off ...) and the Logbook records everyone's strokes as one continuous piece. The session is cut into slots of
    `slot_s` (60 s) from the start; each slot's speed (leaving out its first 15 s, where the flywheel still carries the
    previous person's speed) is clustered (1-D, 2 or 3 groups); this person = the fastest group. It counts as shared
    when the fast slots are >= 4 % faster, form >= 3 separate turns that alternate with the others, and nobody else
    rows > 3 slots in a row. A run of consecutive own slots is one turn (2 min on).
    override (session_overrides.json "erg_shared"): false = never shared; true = shared whatever the checks say;
    {"slot_s": 120} = other turn length (implies true); {"who": "others"} = this person is the slower one (a friend's
    profile with a copy of the owner's recording, docs/concept2.md); {"who": 2} / {"who": 3} = the 2nd / 3rd fastest
    of three people; {"second_machine_m": 10842} = a second, unrecorded machine in the rotation (its Logbook total;
    this person's share there is estimated from their turns here, see `clean`). Returns {turns: [[start s, dur s, m, spm]], pattern, ...} or None."""
    import numpy as np
    if override is False or not st or (r.get("workout_type") in INTERVAL_TYPES and not override):
        return None
    P = {**SHARED, **(override if isinstance(override, dict) else {})}
    t = np.array([s.get("t", 0) for s in st], float) / 10
    d = np.array([s.get("d", 0) for s in st], float) / 10
    spm = np.array([s.get("spm") or 0 for s in st], float)
    if len(t) < 30 or t[-1] < P["min_s"]:
        return None
    L, sk = float(P["slot_s"]), float(P["settle_s"])
    n = int((t[-1] + 0.1 * L) // L)   # the last stroke lands ~1 s before the end: keep a last slot that is >= 90 % there
    if n < 6:
        return None
    at = lambda x: float(np.interp(x, t, d))
    end = lambda i: min((i + 1) * L, t[-1])
    sp = np.array([(at(end(i)) - at(i * L + sk)) / (end(i) - i * L - sk) for i in range(n)])
    if not np.all(np.isfinite(sp)) or sp.max() <= 0:
        return None
    def candidate(k):
        """this person's slots = the fastest of k speed groups; (mask, checks, ok)"""
        xs, cuts, _ = _jenks(sp, k)
        me = sp >= xs[cuts[-1][0]] - 1e-9
        if me.all() or not me.any():
            return None
        if k == 3:   # three groups only when the fastest is clearly apart from the next (else 2 groups: 2025-12-07)
            mid = xs[cuts[-2][0]:cuts[-2][1]]
            if not len(mid) or sp[me].mean() / mid.mean() - 1 < P["contrast"]:
                return None
        contrast = sp[me].mean() / sp[~me].mean() - 1
        runs, others = _runs(me), [j - i for i, j in _runs(~me)]
        checks = {"contrast": round(float(contrast), 3), "n_turns": len(runs), "own_slots": int(me.sum()),
                  "longest_other_run": max(others), "groups": k}
        ok = (contrast >= P["contrast"] and len(runs) >= P["min_runs"] and len(runs) >= P["runs_share"] * me.sum()
              and (~me).sum() >= 3 and max(others) <= P["max_other_run"])
        return me, runs, others, checks, ok

    cands = [c for c in (candidate(3), candidate(2)) if c]
    pick = next((c for c in cands if c[4]), None) or (cands[-1] if cands and override else None)
    if pick is None:
        return None
    me, runs, others, checks, ok = pick
    me = _rhythm(me, sp, runs, others, P)
    who = P.get("who")
    if who == "others":   # the friend's profile, from the owner's recording: the other slots are theirs
        me = ~me
    elif isinstance(who, int) and who > 1:   # the who-th fastest of three or more people (2026-11-15: 3 on one SkiErg)
        xs, cuts, _ = _jenks(sp, max(3, who))
        a, b = cuts[-who]
        me = (sp >= xs[a] - 1e-9) & (sp <= xs[b - 1] + 1e-9)
    if (rr := _runs(me)) != runs:
        runs, others = rr, [j - i for i, j in _runs(~me)]
        checks |= {"n_turns": len(runs), "own_slots": int(me.sum()), "longest_other_run": max(others)}
    turns = []
    for i, j in runs:
        a, b = i * L, j * L
        m = (t >= a) & (t < b)
        turns.append([round(a, 1), round(b - a, 1), round(at(b) - at(a), 1),
                      int(round(float(np.median(spm[m])))) if m.any() and spm[m].any() else None])
    on = float(np.median([j - i for i, j in runs])) * L
    off = float(np.median(others)) * L
    second = None
    if P.get("second_machine_m") and r.get("distance"):
        # two machines, only this one recorded (2026-11-15: 3 people, 1:00 here + 1:00 on the other = 2:00 on / 1:00
        # off): this person's metres on the other machine ~ their turns here x (other machine's total / this one's)
        ratio = float(P["second_machine_m"]) / float(r["distance"])
        own_m = sum(x[2] for x in turns)
        second = {"logbook_dist_m": int(P["second_machine_m"]), "ratio": round(ratio, 4),
                  "est_m": int(round(own_m * ratio)), "time_s": round(sum(x[1] for x in turns), 1),
                  "on_here_s": on, "off_here_s": off}
        on, off = 2 * on, off - on
    return {"slot_s": L, "second_machine": second, "turns": turns, "pattern": "".join("X" if x else "." for x in me),
            "on_s": on, "off_s": off, "others_pace_500_s": round(500 / float(sp[~me].mean()), 1),
            "logbook_time_s": (r.get("time") or 0) / 10, "logbook_dist_m": r.get("distance"),
            "forced": bool(override) and not ok, **checks}


def _rhythm(me, sp, runs, others, P):
    """in a regular on / off session, drop own slots that break the rhythm unless they are as fast as your turns
    (a friend sprinting the last minute: 2026-08-13, 2026-01-25, 2026-10-24)"""
    import numpy as np
    on, off = int(round(np.median([j - i for i, j in runs]))), int(round(np.median(others)))
    per, n = on + off, len(me)
    best = None
    for ph in range(per):
        exp = np.array([(i - ph) % per < on for i in range(n)])
        fit = float((exp == me).mean())
        if best is None or fit > best[0]:
            best = (fit, exp)
    fit, exp = best
    if fit < P["rhythm"] or not (me & exp).any():
        return me
    ref = float(np.median(sp[me & exp]))
    end = np.arange(n) >= n - per   # only the finish: a longer solo piece earlier on is real (2026-10-24 opens 5 min)
    return me & (exp | ~end | (sp >= P["off_rhythm_speed"] * ref))


def _runs(mask):
    """[(start, end)) index ranges of consecutive True"""
    out, i, n = [], 0, len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j]:
                j += 1
            out.append((i, j)); i = j
        else:
            i += 1
    return out


def clean(r: dict, sh: dict | None) -> dict:
    """the result with a shared-machine session rewritten as this person's own interval workout: intervals = own
    turns (time, metres, stroke rate, start, rest until the next own turn), time / distance = own work; the Logbook's
    totals stay in `shared`. Calories and the Logbook's splits (everyone's) are dropped."""
    if not sh:
        return r
    T = sh["turns"]
    ivs = []
    for k, (a, dur, m, spm) in enumerate(T):
        nxt = T[k + 1][0] if k + 1 < len(T) else None
        ivs.append({"type": "time", "time": int(round(dur * 10)), "distance": int(round(m)), "stroke_rate": spm,
                    "rest_time": int(round((nxt - a - dur) * 10)) if nxt is not None else 0, "start_s": a})
    work = sum(x["time"] for x in ivs)
    dist, rest = sum(x["distance"] for x in ivs), sum(x["rest_time"] for x in ivs)
    if sm := sh.get("second_machine"):   # + the other machine (estimated): work and metres on both, rest = the off time
        dist, work, rest = dist + sm["est_m"], work + int(round(sm["time_s"] * 10)), max(0, rest - int(round(sm["time_s"] * 10)))
    return {**r, "workout_type": "VariableInterval", "time": work, "distance": dist,
            "rest_time": rest, "rest_distance": None, "calories_total": None,
            "stroke_rate": int(round(sum((x["stroke_rate"] or 0) * x["time"] for x in ivs) / sum(x["time"] for x in ivs))) if work else None,
            "workout": {"intervals": ivs}, "shared": sh}


def _link(src: Path, dst: Path) -> None:
    if dst.exists():
        if dst.stat().st_ino == src.stat().st_ino or (dst.stat().st_size == src.stat().st_size
                                                     and dst.stat().st_mtime_ns >= src.stat().st_mtime_ns):
            return
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        dst.write_bytes(src.read_bytes())


def sync(D: Path) -> tuple[int, int]:
    """concept2/results -> concept2/activities_index.json + activities/<date>_<id>_<typeKey>/ folders.
    Returns (rows, FIT files linked). Folders of results no longer in the Logbook are removed."""
    C = c2_dir(D)
    lst = json.loads((C / "results.json").read_text()) if (C / "results.json").exists() else []
    lst = sorted(lst + manual(D), key=lambda r: r["date"], reverse=True)   # + hand-logged sessions, newest first
    out, n_fit, keep = [], 0, set()
    for r0 in lst:
        r = r0 if r0.get("manual") else load_result(D, r0["id"], r0)   # cleaned: shared-machine sessions -> own turns
        row = index_row(r)
        out.append(row)
        fit = C / "results" / str(r["id"]) / f"{r['id']}.fit"
        if not fit.exists():
            continue
        folder = D / "activities" / f"{row['startTimeLocal'][:10]}_{r['id']}_{row['activityType']['typeKey']}"
        folder.mkdir(parents=True, exist_ok=True)
        keep.add(folder.name)
        _link(fit, folder / fit.name)
        li = folder / "list_item.json"
        js = json.dumps(row, indent=2, ensure_ascii=False)
        if not li.exists() or li.read_text() != js:
            li.write_text(js)
        n_fit += 1
    for f in (D / "activities").glob("*_*_*"):   # stale erg folders (deleted / re-dated on the Logbook)
        parts = f.name.split("_", 2)
        if f.is_dir() and len(parts) == 3 and parts[2] in ERG_TYPES and f.name not in keep:
            for x in f.iterdir():
                x.unlink()
            f.rmdir()
    tmp = C / "activities_index.json.tmp"
    tmp.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    tmp.replace(C / "activities_index.json")
    return len(out), n_fit


# ---------------------------------------------------------------- reps
def erg_ana(row: dict, laps_c: list | None) -> dict | None:
    """Rep analysis of an erg interval workout (session_groups compact `ana`, mode "erg").
    Reps = the FIT's work laps (intensity active) in order; rests = the rest laps. The Concept2 interval list gives
    the nominal target of each rep (rep_kind "time" / "distance" / "calorie" and rep_nominal: s, m or kcal), which
    labels the signature ("12×2:00", "4×500"). laps_c: [start s, dur s, dist m, cadence, mean HR, trigger, intensity]
    (session_groups.worker; intensity 0 active, 1 rest). None for continuous workouts or without laps."""
    c2 = row.get("c2") or {}
    if c2.get("shared"):   # shared machine (clean): reps = own turns, from the strokes
        return _shared_ana(c2)
    if c2.get("workout_type") not in INTERVAL_TYPES or not laps_c:
        return None
    work = [l for l in laps_c if (l[6] if len(l) > 6 else 0) in (0, None, 5) and l[1] > 0]
    rest = [l for l in laps_c if len(l) > 6 and l[6] in (1, 4)]
    ivs = c2.get("intervals") or []
    if len(work) < 2:
        return None
    if ivs and len(ivs) != len(work):   # FIT laps and Concept2 intervals disagree: trust the Concept2 numbers
        work = work[:len(ivs)]
    kinds, nominal, times, dists = [], [], [], []
    for i, l in enumerate(work):
        iv = ivs[i] if i < len(ivs) else {}
        k = iv.get("type") or "time"
        kinds.append(k)
        t = iv.get("time_s") or l[1]
        d = iv.get("dist_m") or l[2]
        times.append(round(float(t), 1))
        dists.append(round(float(d)) if d else None)
        nominal.append(round(t) if k == "time" else (d if k == "distance" else iv.get("kcal")))
    starts = [round(float(l[0]), 1) for l in work]
    breaks = []
    for i in range(len(work) - 1):
        b0 = work[i][0] + work[i][1]
        gap = max(0.0, work[i + 1][0] - b0)
        rd = sum(r[2] or 0 for r in rest if b0 - 1 <= r[0] < work[i + 1][0])
        breaks.append((round(gap, 1), rd, True))
    return {"mode": "erg", "threshold": None, "rep_times": times, "rep_dists": dists,
            "rep_speeds": [round(d / t, 3) if d and t else None for d, t in zip(dists, times)],
            "rep_hr": [(ivs[i].get("hr") if i < len(ivs) else None) or (work[i][4] if len(work[i]) > 4 else None)
                       for i in range(len(work))],
            "rep_starts": starts, "breaks": breaks, "rep_kind": kinds, "rep_nominal": nominal,
            "laps": [(l[1], l[2], "rest" if len(l) > 6 and l[6] == 1 else "active", "manual") for l in laps_c]}


def _shared_ana(c2: dict) -> dict | None:
    ivs = c2.get("intervals") or []
    if len(ivs) < 2:
        return None
    times = [round(float(x["time_s"]), 1) for x in ivs]
    dists = [round(float(x.get("dist_m") or 0)) or None for x in ivs]
    starts = [round(float(x.get("start_s") or 0), 1) for x in ivs]
    # rest = the others' turns: this person doesn't move the machine (0 m, standing the whole break)
    breaks = [(g, 0, g) for g in (round(starts[i + 1] - starts[i] - times[i], 1) for i in range(len(ivs) - 1))]
    return {"mode": "erg", "threshold": None, "rep_times": times, "rep_dists": dists,
            "rep_speeds": [round(d / t, 3) if d and t else None for d, t in zip(dists, times)],
            "rep_hr": [x.get("hr") for x in ivs], "rep_starts": starts, "breaks": breaks,
            "rep_kind": ["time"] * len(ivs), "rep_nominal": [round(t) for t in times], "laps": [], "shared": True}


def erg_type(row: dict, ana: dict | None) -> tuple[str, str]:
    """(session type, detail) of an erg session: erg_intervals / erg_piece (continuous <= 12 min) / erg_steady"""
    c2 = row.get("c2") or {}
    wt = c2.get("workout_type") or "unknown"
    if (man := c2.get("manual")) and man.get("on_s"):   # hand-logged rotation (manual.json): no reps, totals only
        return "erg_intervals", (f"logged by hand: {mmss(man['on_s'])} on / {mmss(man['off_s'])} off, "
                                 f"{len(man.get('machines') or [])} machines, no per-person split")
    if ana and len(ana["rep_times"]) >= 2:
        med = sorted(ana["rep_times"])[len(ana["rep_times"]) // 2]
        length = "short" if med < 100 else ("medium" if med < 300 else "long")
        sh = c2.get("shared")
        if sh:
            if sh.get("second_machine"):
                return "erg_intervals", (f"shared, 2 machines: {mmss(sh['on_s'])} on / {mmss(sh['off_s'])} off; reps = own "
                                         f"turns on the recorded machine, the other machine estimated")
            return "erg_intervals", f"shared machine: {mmss(sh['on_s'])} on / {mmss(sh['off_s'])} off, own turns only"
        return "erg_intervals", f"{length} reps"
    work = row.get("movingDuration") or 0
    if work <= SHORT_PIECE_S:
        return "erg_piece", f"{row.get('distance')} m" if wt == "FixedDistanceSplits" else ""
    return "erg_steady", ""


# ---------------------------------------------------------------- shared sessions: turn profiles (charts)
HANDOVER = {"min_s": 8.0,    # the flywheel / monitor carries the previous person's pace for 4-8 s (2026-11-07, 60 swaps)
            "max_s": 10.0,   # never cut more: slower after that is the rower's own start (user decision 2026-11-15)
            "tol": 0.02,     # settled = within 2 % of the turn's steady pace ...
            "run": 5,        # ... for 5 strokes in a row
            "end_s": 1.0}    # the last second before the swap sags


def turn_profiles(st: list[dict], turns: list) -> list[dict]:
    """per own turn of a shared-machine session: the handover cut (`cut_s`: until the monitor pace settles within 2 %
    of the turn's steady level, clamped to 8-10 s), the steady pace /500 m after the cut, and the trimmed stroke
    pace series (`t` = s into the turn, `p` = s /500 m). turns = shared_turns()["turns"] ([start, dur, m, spm])."""
    import numpy as np
    H = HANDOVER
    t = np.array([s.get("t", 0) for s in st], float) / 10
    d = np.array([s.get("d", 0) for s in st], float) / 10
    p = np.array([s.get("p", 0) for s in st], float) / 10
    at = lambda x: float(np.interp(x, t, d))
    out = []
    for k, (a, dur, m, spm) in enumerate(turns):
        b = a + dur
        msk = (t >= a) & (t < b) & (p > 0)
        tt, pp = t[msk] - a, p[msk]
        core = pp[(tt >= H["max_s"]) & (tt <= dur - H["end_s"])]
        ref = float(np.median(core)) if len(core) else None
        cut = H["max_s"]
        if ref:
            ok = np.abs(pp / ref - 1) <= H["tol"]
            for i in range(len(ok) - H["run"] + 1):
                if ok[i:i + H["run"]].all():
                    cut = tt[i]
                    break
        cut = float(min(H["max_s"], max(H["min_s"], cut)))
        e = dur - H["end_s"]
        steady = (e - cut) / (at(a + e) - at(a + cut)) * 500 if at(a + e) > at(a + cut) else None
        keep = (tt >= cut) & (tt <= e)
        out.append({"n": k + 1, "start_s": a, "time_s": dur, "dist_m": m, "spm": spm,
                    "pace_s": round(dur / m * 500, 1) if m else None, "cut_s": round(cut, 1),
                    "steady_pace_s": round(steady, 1) if steady else None,
                    "t": [round(float(x), 1) for x in tt[keep]], "p": [round(float(x), 1) for x in pp[keep]]})
    return out

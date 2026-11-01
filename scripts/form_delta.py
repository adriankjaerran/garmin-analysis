"""Form delta: how well a session went relative to the person's form going into it (VDOT points).

    uv run scripts/form_delta.py adrian <activity_id>     # one session (JSON)
    uv run scripts/form_delta.py adrian --series          # weekly shape trend per discipline
    uv run scripts/form_delta.py adrian --together        # every participant of every group in data/together.json

Definition (docs/fitness-model.md, "Form delta"):
    delta = session score - shape on the day before, from the shape model refitted WITHOUT this session
The session score is the fitness model's own per-session score (fitness.json `sessions[].score`, Daniels VDOT scale)
for the session's main race/hard evidence item (race > hard, then smaller sigma, then interval). Easy runs, long-run
bounds, hikes, rides and unscored types -> None. The reference is `fitness_model.smooth_series` (robust Kalman + RTS,
same q / sigmas / robust weights as fitness.json) on every other evidence item of that discipline, read at the day
before: the session can't pull its own reference towards itself. Running (interval + long) uses the distance curve
(`fitness_model.smooth_curve` on every other running item, read at the session's own distance `km`) when fitness.json
has one (model_version >= 2). It uses later sessions too (like the shape curve),
so an old session's delta can move a little when fitness.json is rebuilt.

Only fitness.json is read (no FIT files). One leave-one-out refit per race/hard session (~1.5 s for adrian, cold),
cached in memory and in data/<person>/cache/form_delta.json, keyed by fitness.json mtime + size.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fitness_model as fm  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
KIND_RANK = {"race": 0, "hard": 1}          # bound / implausible never give a delta
CONF_SD = (2.0, 3.0)                        # total sd (reference sd ⊕ session sigma): < 2 high, < 3 medium, else low
OUTLIER_Z = 3.0                             # |delta| / total sd above this: flagged (glitch, mislabel or partial session)
SPARSE_SD = 3.0                             # reference sd above this: little evidence near the session
CODE_VERSION = 4                            # bump when the definition changes (invalidates the disk cache)

_memo: dict[str, tuple[list, dict]] = {}


def _fitness_path(person: str) -> Path:
    return DATA / person / "fitness.json"


def _sign(x: float) -> str:
    return f"+{x:.1f}" if x >= 0.05 else (f"−{abs(x):.1f}" if x <= -0.05 else "±0.0")


def _conf(total_sd: float) -> str:
    return "high" if total_sd < CONF_SD[0] else "medium" if total_sd < CONF_SD[1] else "low"


def _compute(doc: dict) -> dict:
    """{activity id: [evidence rows + ref / ref_sd]}; ref only for race / hard items (leave-one-out smoother)."""
    by_disc: dict[str, list[dict]] = {}
    for s in doc.get("sessions") or []:
        if s.get("kind") == "implausible" or s.get("score") is None:
            continue
        by_disc.setdefault(s["discipline"], []).append(s)
    qs = {**fm.P["q_per_day"], **((doc.get("params") or {}).get("q_per_day") or {})}
    out: dict[str, list[dict]] = {}
    curve = bool(doc.get("predictor")) and any(s.get("km") for s in doc.get("sessions") or [])
    if curve:
        # running (interval + long) is one distance curve in fitness.json (model_version >= 2): the reference is the
        # leave-one-out curve at the session's own distance (`km`), same q / robust weights as fitness_model
        rows = sorted([s for d in ("interval", "long") for s in by_disc.pop(d, []) if s.get("km")], key=lambda s: s["date"])
        _curve_refs(rows, doc, out)
    for disc, rows in by_disc.items():
        rows = sorted(rows, key=lambda s: s["date"])
        days = [dt.date.fromisoformat(s["date"]).toordinal() for s in rows]
        # the reference uses the shape model's robust weight (sigma / sqrt(weight)): a session the model rejected
        # (glitch / mislabel, weight << 1) must not inflate everyone else's form either
        obs = [(d, float(s["score"]), float(s["sigma"]) / math.sqrt(max(float(s.get("weight") if s.get("weight") is not None else 1.0), 0.02)),
                s["kind"]) for d, s in zip(days, rows)]
        d0, d1 = min(days) - 7, max(days)       # same grid start as fitness_model._model
        for j, (d, s) in enumerate(zip(days, rows)):
            r = {k: s.get(k) for k in ("id", "date", "discipline", "type", "score", "kind", "sigma", "weight", "shape_at")}
            r["hr_kind"] = (s.get("hr") or {}).get("kind")
            r["n_other"] = sum(1 for i, o in enumerate(obs) if i != j and o[3] != "bound")
            if s["kind"] in KIND_RANK and r["n_other"]:
                _, m, sd, _, _ = fm.smooth_series(obs[:j] + obs[j + 1:], d0, d1, qs[disc])
                k = max(d - 1 - d0, 0)
                r["ref"], r["ref_sd"] = float(m[k]), float(sd[k])
            out.setdefault(str(s["id"]), []).append(r)
    return out


def _curve_refs(rows: list[dict], doc: dict, out: dict) -> None:
    """Leave-one-out distance-curve reference for the running rows (fitness_model.smooth_curve), read on the day
    before at the row's own distance."""
    if not rows:
        return
    cp = {**fm.CURVE, **(doc.get("curve") or {})}
    days = [dt.date.fromisoformat(s["date"]).toordinal() for s in rows]
    obs = [(d, float(s["score"]), float(s["sigma"]) / math.sqrt(max(float(s.get("weight") if s.get("weight") is not None else 1.0), 0.02)),
            s["kind"], math.log2(float(s["km"]) / cp["ref_km"])) for d, s in zip(days, rows)]
    d0, d1 = min(days) - 7, max(days)
    for j, (d, s) in enumerate(zip(days, rows)):
        r = {k: s.get(k) for k in ("id", "date", "discipline", "type", "score", "kind", "sigma", "weight", "shape_at", "km")}
        r["hr_kind"] = (s.get("hr") or {}).get("kind")
        r["n_other"] = sum(1 for i, o in enumerate(obs) if i != j and o[3] != "bound")
        if s["kind"] in KIND_RANK and r["n_other"]:
            _, M, C, _, _ = fm.smooth_curve(obs[:j] + obs[j + 1:], d0, d1, q_theta=cp["q_theta"], q_slope=cp["q_slope"],
                                            slope_sd=cp["slope_prior_sd"])
            k = max(d - 1 - d0, 0)
            m, sd = fm.curve_at(M[k], C[k], obs[j][4])
            r["ref"], r["ref_sd"] = float(m), float(sd)
        out.setdefault(str(s["id"]), []).append(r)


def _table(person: str) -> dict:
    """Memoised in-process and on disk (data/<person>/cache/form_delta.json) by fitness.json mtime + code version."""
    p = _fitness_path(person)
    if not p.exists():
        return {}
    key = [p.stat().st_mtime_ns, p.stat().st_size, CODE_VERSION]
    hit = _memo.get(person)
    if hit and hit[0] == key:
        return hit[1]
    cf = DATA / person / "cache" / "form_delta.json"
    res = None
    if cf.exists():
        try:
            c = json.loads(cf.read_text())
            res = c["rows"] if c.get("key") == key else None
        except (ValueError, KeyError):
            res = None
    if res is None:
        res = _compute(json.loads(p.read_text()))
        try:
            cf.parent.mkdir(exist_ok=True)
            tmp = cf.with_suffix(".tmp")
            tmp.write_text(json.dumps({"key": key, "rows": res}))
            tmp.replace(cf)
        except OSError:
            pass
    _memo[person] = (key, res)
    return res


def _delta(rows: list[dict]) -> dict | None:
    cand = [r for r in rows if r["kind"] in KIND_RANK]
    if not cand:
        return None
    r = min(cand, key=lambda r: (KIND_RANK[r["kind"]], r["sigma"], r["discipline"] != "interval"))
    score = r["score"]
    flags = []
    if r.get("hr_kind") == "bound":
        flags.append("hr_not_maximal")          # the HR-intent variant demoted it: HR says it wasn't all-out
    if (r.get("weight") or 0) < 0.5:
        flags.append("downweighted")            # the shape model itself mostly ignored this session
    base = {"score": round(score, 1), "discipline": r["discipline"], "kind": r["kind"], "date": r["date"],
            "weight": r.get("weight"), "basis": "loo_smoother_day_before"}
    if "ref" not in r:
        # the only race/hard evidence in this discipline: nothing else to say what the form was
        return {**base, "shape": None, "delta": None, "confidence": "none", "text": "no earlier form to compare",
                "ref_sd": None, "z": None, "flags": flags + ["no_reference"]}
    ref, ref_sd = r["ref"], r["ref_sd"]
    total = math.sqrt(ref_sd ** 2 + r["sigma"] ** 2)
    delta = score - ref
    z = delta / total
    if ref_sd > SPARSE_SD:
        flags.append("sparse_reference")
    if abs(z) > OUTLIER_Z:
        flags.append("outlier")
    conf = "low" if "outlier" in flags else _conf(total)
    text = f"{_sign(delta)} vs form"
    if "outlier" in flags and "downweighted" in flags:
        conf, text = "none", "score unreliable"   # far off and rejected by the shape model itself: glitch / mislabel
    return {**base, "shape": round(ref, 1), "delta": round(delta, 1), "confidence": conf, "text": text,
            "ref_sd": round(ref_sd, 2), "z": round(z, 2), "flags": flags}


def form_delta(person: str, activity_id: str) -> dict | None:
    """Session score minus the person's shape the day before (VDOT points), or None when the session has no
    race/hard score (easy runs, long-run bounds, hikes, rides, unscored types such as multi_sport / treadmill).

    -> {score, shape, delta, discipline, kind, confidence (high|medium|low|none), text ("+2.1 vs form"),
        date, ref_sd (sd of the reference), z (delta / sqrt(ref_sd² + session sigma²)), weight (the shape model's
        robust weight 0..1 for this session), flags, basis}.
    flags: hr_not_maximal (HR-intent variant demoted it), downweighted (model weight < 0.5), sparse_reference
    (ref_sd > 3), outlier (|z| > 3), no_reference (the only race/hard item of its discipline: shape / delta / z None).
    outlier + downweighted: confidence "none", text "score unreliable" (delta still given)."""
    rows = _table(person).get(str(activity_id))
    return _delta(rows) if rows else None


def form_deltas(person: str) -> dict[str, dict]:
    """{activity id: form_delta} for every session that has one (memoised by fitness.json mtime)."""
    return {i: d for i, rows in _table(person).items() if (d := _delta(rows)) is not None}


def form_series(person: str, since: str | None = None) -> list[dict]:
    """Weekly shape trend from fitness.json (RTS-smoothed, the same curve as the fitness page), oldest first:
    [{date, interval, long, ski, overall, sd: {discipline: sd}}]; a discipline is None before its first evidence.
    interval / long / ski in VDOT (ski: VDOT-equivalent), overall in % of personal best."""
    p = _fitness_path(person)
    if not p.exists():
        return []
    doc = json.loads(p.read_text())
    rows: dict[str, dict] = {}
    discs = [d for d in ("interval", "long", "ski", "overall") if d in (doc.get("disciplines") or {})]
    for disc in discs:
        for w in doc["disciplines"][disc].get("weekly") or []:
            if since and w["date"] < since:
                continue
            r = rows.setdefault(w["date"], {"date": w["date"], **{d: None for d in ("interval", "long", "ski", "overall")}, "sd": {}})
            r[disc] = w["mean"]
            r["sd"][disc] = w["sd"]
    return [rows[k] for k in sorted(rows)]


def together_deltas(path: Path | None = None) -> list[dict]:
    """[{gid, date, label, hidden, workout, participants: [{person, activity_id, role, type, form}]}]."""
    path = path or DATA / "together.json"
    doc = json.loads(path.read_text())
    out = []
    for g in doc.get("groups") or []:
        out.append({"gid": g["gid"], "date": g["date"], "label": g.get("label"), "hidden": bool(g.get("hidden")),
                    "workout": g.get("workout"),
                    "participants": [{"person": p["person"], "activity_id": str(p["activity_id"]), "role": p.get("role"),
                                      "type": p.get("type"), "workout": p.get("workout"),
                                      "form": form_delta(p["person"], p["activity_id"])} for p in g.get("participants") or []]})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("person", nargs="?", default="adrian")
    ap.add_argument("activity_id", nargs="?")
    ap.add_argument("--together", action="store_true", help="deltas for every participant of every together group")
    ap.add_argument("--series", action="store_true", help="weekly shape trend")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.together:
        res = together_deltas()
        if a.json:
            print(json.dumps(res, indent=1, ensure_ascii=False))
            return
        for g in res:
            print(f"{g['gid']}  {g['label']}{'  (hidden)' if g['hidden'] else ''}  {g['workout'] or ''}")
            for p in g["participants"]:
                f = p["form"]
                if f is None:
                    s = "—  (no race/hard score)"
                elif f["delta"] is None or f["confidence"] == "none":
                    s = (f"{f['text']}  (score {f['score']:.1f}, delta {f['delta']}) {f['discipline']}/{f['kind']}"
                         + (f"  [{', '.join(f['flags'])}]" if f["flags"] else ""))
                else:
                    s = (f"{f['text']:<16} score {f['score']:5.1f} − shape {f['shape']:5.1f} ±{f['ref_sd']:.1f}  "
                         f"{f['discipline']}/{f['kind']}  z {f['z']:+.1f}  {f['confidence']}"
                         + (f"  [{', '.join(f['flags'])}]" if f["flags"] else ""))
                print(f"    {p['person']:<7} {p['role']:<8} {p['type']:<16} {s}")
        return
    if a.series:
        print(json.dumps(form_series(a.person), indent=None if not a.json else 1, ensure_ascii=False))
        return
    if not a.activity_id:
        ap.error("activity_id, --series or --together")
    print(json.dumps(form_delta(a.person, a.activity_id), indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()

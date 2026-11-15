"""G10 audit: leakage / bug checks on the shared harness + end-to-end spot-check of 10 random targets.

    /tmp/shape-venv/bin/python research/shape-models/approaches/g10-audit/audit.py
Reads only. Writes data/_research/shape/g10/audit_spotcheck.csv and audit_checks.json.
"""
from __future__ import annotations

import collections
import datetime as dt
import glob
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
import common as C  # noqa: E402
import harness as H  # noqa: E402

OUT = C.OUT / "g10"; OUT.mkdir(parents=True, exist_ok=True)
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30)


def fit_best(path, target_m):
    """Independent recomputation: fastest elapsed-time window covering target_m of the FIT's own distance field
    (no grid, no pause handling, no clipping). Returns seconds."""
    import fitparse
    t, d = [], []
    for m in fitparse.FitFile(str(path)).get_messages("record"):
        v = m.get_values()
        dist = v.get("distance") if v.get("distance") is not None else v.get("enhanced_distance")
        if v.get("timestamp") is None or dist is None:
            continue
        t.append(v["timestamp"].timestamp()); d.append(float(dist))
    t = np.array(t); d = np.maximum.accumulate(np.array(d))
    if len(d) < 2 or d[-1] - d[0] < target_m:
        return np.nan, d[-1] - d[0] if len(d) else np.nan
    best = np.inf
    j = 0
    for i in range(len(d)):
        while j < len(d) and d[j] - d[i] < target_m:
            j += 1
        if j >= len(d):
            break
        # interpolate the end point
        f = (d[i] + target_m - d[j - 1]) / max(d[j] - d[j - 1], 1e-9)
        te = t[j - 1] + f * (t[j] - t[j - 1])
        best = min(best, te - t[i])
    return best, d[-1] - d[0]


def main():
    A, S, T = H.load_data()
    races = json.loads((C.DATA / "races.json").read_text())
    checks = {}

    # 1. history cut-off and covariate leakage for every target
    bad_hist, bad_self = 0, 0
    for t in T.itertuples():
        h = H.history(t.person, t.day)
        if len(h.acts) and h.acts.day.max() >= t.day or len(h.segs) and h.segs.day.max() >= t.day \
                or len(h.targets) and h.targets.day.max() >= t.day:
            bad_hist += 1
        if (h.acts.activity_id == t.activity_id).any() or (h.segs.activity_id == t.activity_id).any():
            bad_self += 1
    checks["targets_with_future_or_same_day_history"] = bad_hist
    checks["targets_whose_own_activity_is_in_history"] = bad_self
    outcome_cols = {"time_s", "vdot", "note"}
    checks["outcome_keys_passed_to_predict_fn"] = sorted(outcome_cols & set(H.TARGET_KEYS))

    # 2. races vs races.json
    rr = []
    for r in races["races"]:
        if r.get("sport") != "run":
            continue
        for p, x in (r.get("results") or {}).items():
            tg = T[(T.tier == "race") & (T.activity_id == int(x["activity_id"]))]
            a = A[A.activity_id == int(x["activity_id"])]
            rr.append({"race": r["name"], "person": p, "km_json": r["km"], "s_json": x["official_s"],
                       "source_json": x.get("source"), "in_targets": len(tg) == 1,
                       "km_tgt": tg.km.iloc[0] if len(tg) else None, "s_tgt": tg.time_s.iloc[0] if len(tg) else None,
                       "source_parquet": a.race_time_source.iloc[0] if len(a) else None,
                       "watch_km": a.dist_km.iloc[0] if len(a) else None,
                       "moving_s": a.moving_s.iloc[0] if len(a) else None})
    R = pd.DataFrame(rr)
    R["match"] = (R.km_json == R.km_tgt) & (R.s_json == R.s_tgt)
    print(R.to_string(index=False))
    checks["races_all_match"] = bool(R["match"].all())
    checks["races_source_missing_defaulted_to_chip"] = R[R.source_json.isna()][["race", "person"]].values.tolist()

    # 3. fitness.json sessions with two running items (interval + long) under one id: prep keeps only the last
    lost = []
    for p in C.PERSONS:
        f = json.loads((C.DATA / p / "fitness.json").read_text())
        by = collections.defaultdict(list)
        for s in f["sessions"]:
            if s.get("discipline") in ("interval", "long"):
                by[str(s["id"])].append(s)
        for k, v in by.items():
            if len(v) > 1:
                kept = A[A.activity_id == int(k)]
                for s in v:
                    if not len(kept) or s["discipline"] != kept.prod_discipline.iloc[0]:
                        lost.append({"person": p, "id": k, "date": s["date"], "discipline": s["discipline"],
                                     "kind": s["kind"], "score": s["score"]})
    L = pd.DataFrame(lost)
    print("\nlost production items (dict overwrite in prep_data.load_person):")
    print(L.groupby(["person", "discipline", "kind"]).agg(n=("score", "size"), mean=("score", "mean")).to_string())
    L.to_csv(OUT / "lost_prod_items.csv", index=False)
    checks["lost_prod_items"] = L.groupby("person").size().to_dict()

    # 4. GPS-long: race best effort vs official
    g = []
    for x in A[A.is_official_race].itertuples():
        d = {5.0: "5k", 10.0: "10k", 21.0975: "HM"}[x.race_km]
        be = getattr(x, f"be_{d}_s"); beg = getattr(x, f"beg_{d}_s")
        g.append({"person": x.person, "date": x.date.date(), "race": x.race_name, "surface": x.surface,
                  "watch/official km": round(x.dist_km / x.race_km, 4),
                  "vdot_official": round(x.race_vdot, 2), "vdot_best_raw": round(float(C.vdot(x.race_km * 1000, be)), 2),
                  "vdot_best_gap": round(float(C.vdot(x.race_km * 1000, beg)), 2)})
    G = pd.DataFrame(g); G["raw-off"] = G.vdot_best_raw - G.vdot_official; G["gap-off"] = G.vdot_best_gap - G.vdot_official
    print("\nGPS best effort vs official:\n" + G.to_string(index=False))
    checks["gps_bias_raw_median_adrian_rise"] = float(G[G.person != "patrik"]["raw-off"].median())
    checks["gps_bias_gap_median_adrian_rise"] = float(G[G.person != "patrik"]["gap-off"].median())

    # 5. suspect segments: hard_effort targets on suspect segments, implausible unflagged bests
    he = T[T.tier == "hard_effort"].copy(); he["label"] = he.target_id.str.split(":").str[1]
    m = he.merge(S[S.seg_kind == "best_gap"][["activity_id", "label", "suspect"]], on=["activity_id", "label"], how="left")
    checks["hard_effort_on_suspect_segment"] = m[m.suspect == True].target_id.tolist()  # noqa: E712
    hi = S[(S.vdot > 66) & ~S.suspect & S.pace_ok & S.seg_kind.isin(["best", "best_gap"]) & (S.target_m >= 1000)]
    checks["unflagged_bests_vdot_gt_66"] = hi.groupby(["person", "activity_id"]).vdot.max().round(1).reset_index().values.tolist()

    # 6. spot-check 10 random targets end to end
    # stratified so every tier is exercised (a plain random 10 is ~all interval targets): 3 race, 4 interval, 3 hard
    pick = pd.concat([T[T.tier == k].sample(n, random_state=10) for k, n in (("race", 3), ("interval", 4), ("hard_effort", 3))])
    fits = {}
    for p in C.PERSONS:
        for f in glob.glob(str(C.DATA / p / "activities" / "*" / "*.fit")):
            fits[Path(f).stem] = f
    fj = {p: {str(s["id"]) + ":" + s["discipline"]: s for s in json.loads((C.DATA / p / "fitness.json").read_text())["sessions"]}
          for p in C.PERSONS}
    rows = []
    for t in pick.itertuples():
        a = A[A.activity_id == t.activity_id].iloc[0]
        h = H.history(t.person, t.day)
        r = {"target_id": t.target_id, "person": t.person, "date": t.date.date(), "tier": t.tier, "km": t.km,
             "vdot": round(t.vdot, 2), "hist_max_day_lt_target": (h.acts.day.max() < t.day) if len(h.acts) else True,
             "date_matches_start_local": str(a.start_local)[:10] == str(t.date.date())}
        if t.tier == "race":
            ok = [x for x in races["races"] if (x.get("results") or {}).get(t.person, {}).get("activity_id") == t.activity_id]
            r["source_value"] = ok[0]["results"][t.person]["official_s"] if ok else None
            r["check"] = "races.json official_s == time_s" if ok and ok[0]["results"][t.person]["official_s"] == t.time_s else "MISMATCH"
        elif t.tier == "interval":
            s = fj[t.person].get(f"{t.activity_id}:interval")
            r["source_value"] = s["score"] if s else None
            r["check"] = "fitness.json interval score == vdot" if s and abs(s["score"] - t.vdot) < 1e-6 else "MISMATCH"
        else:
            lab = t.target_id.split(":")[1]
            raw = S[(S.activity_id == t.activity_id) & (S.seg_kind == "best") & (S.label == lab)]
            fb, fd = fit_best(fits[str(t.activity_id)], C.STD_DIST[lab])
            r["source_value"] = round(fb, 1)
            r["check"] = (f"FIT raw best {fb:.1f}s vs harness raw best {raw.time_s.iloc[0]:.1f}s, GAP target {t.time_s:.1f}s; "
                          f"FIT dist {fd / 1000:.2f} km vs groups {a.dist_km}")
        rows.append(r)
    SP = pd.DataFrame(rows)
    print("\nspot-check:\n" + SP.to_string(index=False))
    SP.to_csv(OUT / "audit_spotcheck.csv", index=False)
    (OUT / "audit_checks.json").write_text(json.dumps(checks, indent=1, default=str))
    print(json.dumps(checks, indent=1, default=str))


if __name__ == "__main__":
    main()

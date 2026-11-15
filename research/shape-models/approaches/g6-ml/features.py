"""G6 feature engineering: history features as of a prediction day (only rows with day < prediction day).

    /tmp/shape-venv/bin/python research/shape-models/approaches/g6-ml/features.py   # builds data/_research/shape/g6/rows.parquet

Rows = (a) every harness target (race / interval / hard_effort) and (b) extra "hard segment" training rows: GAP best
efforts (seg_kind best_gap, >= 1 km, pace_ok, not suspect) from hard sessions (intervals / tempo / race / time trial)
or with last-third HR >= 0.85 HRR on a device with steady trust >= 0.5. Each row gets history features computed from
harness.history(person, day) (strictly before the row's day) + descriptors of the effort itself known in advance
(distance, kind, surface, terrain, climb). Labels: vdot (and resid = vdot - production prediction at that km/day).
"""
from __future__ import annotations

import functools
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
import common as C  # noqa: E402
import harness as H  # noqa: E402

OUTDIR = C.OUT / "g6"
HARD_WO = ("intervals", "tempo", "race", "time_trial")
SURF = ("road", "track", "indoor_track", "treadmill", "trail")
KINDS = ("race", "interval", "hard_effort", "seg_intervals", "seg_tempo", "seg_race", "seg_time_trial", "seg_hr")
DEVICES = ("fr935", "4315", "3077", "fenix3_hr", "strap")


def _max_since(df, day, k, col="vdot"):
    x = df[df.day >= day - k]
    return float(x[col].max()) if len(x) else np.nan


def _age_of_max(df, day, k, col="vdot"):
    x = df[df.day >= day - k]
    if not len(x):
        return np.nan
    return float(day - x.loc[x[col].idxmax(), "day"])


@functools.lru_cache(maxsize=8192)
def hist_feats(person, day):
    """Features of `person`'s history strictly before ordinal `day` (person-level, target independent)."""
    h = H.history(person, day)
    a, s, t = h.acts, h.segs, h.targets
    f = {}
    # --- production curve state (theta at 5 km, slope per doubling, their sd)
    st = H._prod_state(person, day)
    if st is not None:
        m, cv, ekm = st
        f.update(prod_theta=float(m[0]), prod_slope=float(m[1]), prod_sd_theta=math.sqrt(cv[0, 0]),
                 prod_sd_slope=math.sqrt(cv[1, 1]), prod_ekm_lo=ekm[0] if ekm else np.nan,
                 prod_ekm_hi=ekm[1] if ekm else np.nan)
    # --- past targets: interval scores, races, hard efforts
    iv = t[t.tier == "interval"]
    f["iv_n_90"] = float((iv.day >= day - 90).sum())
    f["iv_last"] = float(iv.vdot.iloc[-1]) if len(iv) else np.nan
    f["iv_last_age"] = float(day - iv.day.iloc[-1]) if len(iv) else np.nan
    for k in (14, 42, 90, 365):
        x = iv[iv.day >= day - k]
        f[f"iv_max_{k}"] = float(x.vdot.max()) if len(x) else np.nan
        f[f"iv_med_{k}"] = float(x.vdot.median()) if len(x) else np.nan
    rc = t[t.tier == "race"]
    f["race_last"] = float(rc.vdot.iloc[-1]) if len(rc) else np.nan
    f["race_last_age"] = float(day - rc.day.iloc[-1]) if len(rc) else np.nan
    f["race_last_km"] = float(rc.km.iloc[-1]) if len(rc) else np.nan
    f["race_n"] = float(len(rc))
    he = t[t.tier == "hard_effort"]
    f["he_max_180"] = _max_since(he, day, 180)
    # --- best efforts (GAP and raw) by distance and recency
    sg = s[s.pace_ok & ~s.suspect]
    bg = sg[sg.seg_kind == "best_gap"]
    br = sg[sg.seg_kind == "best"]
    for lab in ("1k", "3k", "5k", "10k"):
        g = bg[bg.label == lab]
        for k in (42, 90, 365):
            f[f"bg_{lab}_max_{k}"] = _max_since(g, day, k)
        f[f"bg_{lab}_age_90"] = _age_of_max(g, day, 90)
        f[f"bg_{lab}_q90_90"] = float(g[g.day >= day - 90].vdot.quantile(0.9)) if (g.day >= day - 90).any() else np.nan
    f["br_5k_max_90"] = _max_since(br[br.label == "5k"], day, 90)
    f["br_3k_max_90"] = _max_since(br[br.label == "3k"], day, 90)
    hard = bg[bg.workout.isin(HARD_WO)]
    f["bg_hard_max_90"] = _max_since(hard, day, 90)
    f["bg_hard_age_90"] = _age_of_max(hard, day, 90)
    # --- training load (computed from runs strictly before day) + the last run's all-sport columns
    ru = a[a.usable]
    for k in (7, 28, 42, 365):
        f[f"km_{k}"] = float(ru[ru.day >= day - k].dist_km.sum())
    f["h_28"] = float(ru[ru.day >= day - 28].moving_s.sum()) / 3600
    f["n_runs_28"] = float((ru.day >= day - 28).sum())
    f["n_hard_28"] = float(((ru.day >= day - 28) & ru.workout.isin(HARD_WO)).sum())
    f["acwr"] = f["km_7"] / (f["km_28"] / 4) if f["km_28"] > 0 else np.nan
    if len(a):
        last = a.iloc[-1]
        lag = day - last.day
        # last run's ctl/atl are "before that run": decay them to `day` (no new load counted after the last run)
        f["ctl"] = float(last.ctl) * math.exp(-lag / 42) if pd.notna(last.ctl) else np.nan
        f["atl"] = float(last.atl) * math.exp(-lag / 7) if pd.notna(last.atl) else np.nan
        f["tsb"] = f["ctl"] - f["atl"]
        f["all_h_28_lastrun"] = float(last.all_h_28d)
        f["ski_h_28_lastrun"] = float(last.ski_h_28d)
        f["days_since_run"] = float(lag)
        hd = a[a.workout.isin(HARD_WO)]
        f["days_since_hard"] = float(day - hd.day.iloc[-1]) if len(hd) else np.nan
        f["history_days"] = float(day - a.day.iloc[0])
    # --- HR-pace efficiency (easy / steady / long, HR ok, pace ok), device-aware
    e = a[a.workout.isin(["easy", "steady", "long"]) & a.hr_ok & a.pace_ok & a.ef_gap_per_hr.notna()]
    for k in (28, 90, 365):
        x = e[e.day >= day - k]
        f[f"ef_{k}"] = float(np.average(x.ef_gap_per_hr, weights=x.trust_steady.clip(0.1))) if len(x) else np.nan
    f["ef_ratio_28_365"] = f["ef_28"] / f["ef_365"] if f["ef_365"] and not np.isnan(f["ef_365"]) else np.nan
    x = e[e.day >= day - 90]
    f["hrr_easy_90"] = float(x.hrr_mean.median()) if len(x) else np.nan
    f["decoup_90"] = float(x.decoupling.median()) if len(x) else np.nan
    f["hr_trust_recent"] = float(a.trust_steady.iloc[-5:].mean()) if len(a) else np.nan
    f["strap_share_90"] = float((a[a.day >= day - 90].hr_source == "strap").mean()) if (a.day >= day - 90).any() else np.nan
    # --- surface / terrain mix of the last 42 days (km-weighted)
    r42 = ru[ru.day >= day - 42]
    km42 = r42.dist_km.sum()
    for sname in SURF:
        f[f"mix_{sname}"] = float(r42[r42.surface == sname].dist_km.sum() / km42) if km42 > 0 else np.nan
    f["mix_climb_per_km"] = float(r42.climb_m.sum() / km42) if km42 > 0 else np.nan
    f["mix_grade_abs"] = float(np.average(r42.grade_abs_mean.fillna(0), weights=r42.dist_km)) if km42 > 0 else np.nan
    return f


def target_feats(person, day, tgt, kind):
    """Descriptors of the effort to be predicted (known before it) + production prediction at its distance."""
    km = float(tgt["km"])
    f = {"km": km, "logkm": math.log2(km / 5.0), "is_race": float(kind == "race"),
         "person_adrian": float(person == "adrian"), "person_rise": float(person == "rise"),
         "person_patrik": float(person == "patrik"),
         "flat_course": float(bool(tgt.get("flat_course"))) if tgt.get("flat_course") is not None else np.nan,
         "climb_per_km": float(tgt["climb_per_km"]) if pd.notna(tgt.get("climb_per_km")) else np.nan,
         "t_grade_abs": float(tgt["grade_abs_mean"]) if pd.notna(tgt.get("grade_abs_mean")) else np.nan,
         "t_trust": float(tgt.get("trust_steady")) if pd.notna(tgt.get("trust_steady")) else np.nan}
    for k in KINDS:
        f[f"kind_{k}"] = float(kind == k)
    for sname in SURF:
        f[f"t_surf_{sname}"] = float(tgt.get("surface") == sname)
    dev = str(tgt.get("hr_device") or "")
    for d in DEVICES:
        f[f"t_dev_{d}"] = float(d in dev)
    st = H._prod_state(person, day)
    if st is not None:
        fm = C.import_production()
        m, cv, ekm = st
        p = fm.curve_predict(m[0], m[1], cv[0, 0], cv[1, 1], cv[0, 1], km, ekm)
        f["prod_pred"] = float(p["vdot"]); f["prod_sd"] = float(p["sd"])
    else:
        f["prod_pred"] = np.nan; f["prod_sd"] = np.nan
    return f


def row_feats(person, day, tgt, kind):
    f = dict(hist_feats(person, int(day)))
    f.update(target_feats(person, int(day), tgt, kind))
    # relative features (envelope vs production)
    for c in ("iv_max_42", "bg_3k_max_90", "bg_5k_max_90", "bg_hard_max_90", "race_last"):
        f[f"{c}_m_prod"] = f.get(c, np.nan) - f.get("prod_theta", np.nan)
    return f


TARGET_KIND = {"race": "race", "interval": "interval", "hard_effort": "hard_effort"}


def seg_rows():
    """Hard-segment training rows (GAP best efforts >= 1 km)."""
    A, S, _ = H.load_data()
    s = S[(S.seg_kind == "best_gap") & S.pace_ok & ~S.suspect & (S.target_m >= 1000) & S.vdot.between(20, 85)]
    a = A.set_index(["person", "activity_id"])
    s = s.join(a[["terrain", "flat_course", "hr_device", "trust_steady", "dist_km"]],
               on=["person", "activity_id"])
    hr_hard = (s.hrr_last3 >= 0.85) & (s.hr_cover >= 0.8) & (s.trust_steady >= 0.5) & (s.target_m >= 3000)
    keep = s.workout.isin(HARD_WO) | hr_hard
    s = s[keep].copy()
    s["kind"] = np.where(s.workout.isin(HARD_WO), "seg_" + s.workout.astype(str), "seg_hr")
    s["km"] = s.target_m / 1000
    s["climb_per_km"] = s.climb_m / (s.dist_m / 1000)
    # weight: each session's segment rows share 0.5 in total
    s["w"] = 0.5 / s.groupby(["person", "activity_id"]).activity_id.transform("size")
    return s


def build():
    _, _, T = H.load_data()
    rows = []
    for _, t in T.iterrows():
        tgt = t.to_dict()
        f = row_feats(t.person, t.day, tgt, TARGET_KIND[t.tier])
        f.update(row_id=t.target_id, person=t.person, day=int(t.day), date=t.date, kind=t.tier, vdot=float(t.vdot),
                 w=1.0 if t.tier == "race" else 0.5, src="target", tier=t.tier, activity_id=t.activity_id)
        rows.append(f)
    S = seg_rows()
    for _, r in S.iterrows():
        tgt = {"km": r.km, "surface": r.surface, "flat_course": r.flat_course, "climb_per_km": r.climb_per_km,
               "grade_abs_mean": r.grade_abs_mean, "hr_device": r.hr_device, "trust_steady": r.trust_steady}
        f = row_feats(r.person, r.day, tgt, r.kind)
        f.update(row_id=f"{r.activity_id}:{r.label}:seg", person=r.person, day=int(r.day), date=r.date, kind=r.kind,
                 vdot=float(r.vdot), w=float(r.w), src="seg", tier="seg", activity_id=r.activity_id)
        rows.append(f)
    D = pd.DataFrame(rows)
    D["resid"] = D.vdot - D.prod_pred
    OUTDIR.mkdir(parents=True, exist_ok=True)
    D.to_parquet(OUTDIR / "rows.parquet", index=False)
    return D


if __name__ == "__main__":
    import time
    t0 = time.time()
    D = build()
    print(D.shape, round(time.time() - t0, 1), "s")
    print(D.groupby(["person", "kind"]).size().unstack(0))

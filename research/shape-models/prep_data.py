"""Build the model-ready running dataset for the shape-model research.

    /tmp/shape-venv/bin/python research/shape-models/prep_data.py [--workers 8] [--persons adrian,rise,patrik]

Writes data/_research/shape/{activities,segments,targets}.parquet (+ prep_report.json). Reads only (never writes):
data/<person>/{groups.json, activities_index.json, activities/*/<id>.fit, fitness.json, profile/hr_settings.json,
ranges/daily_summaries_*.json}, data/races.json. The 1 Hz series and grade-adjusted speed reuse the production code
(scripts/session_groups.py read_series / to_grid / find_pauses, scripts/fitness_model.py level_alt / gap_factor),
imported read-only. Column docs and cleaning rules: research/shape-models/DATA.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import math
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

V_MAX = 8.0           # m/s: faster grid speed = GPS spike (production rule)
HARD_WORKOUTS = ("intervals", "tempo", "race", "time_trial")
BE_CONT_MIN = 3000.0  # hard_effort targets: best efforts of >= 3 km


# ------------------------------------------------------------------ per-run series (worker)
def _series(fit, act):
    """1 Hz grid as in fitness_model.session_series, keeping lat/lon/gps and the raw altitude."""
    fm = C.import_production(); import session_groups as sg  # noqa: E402
    G = sg.to_grid(sg.read_series(Path(fit)))
    sg.indoor_grid(G, act)
    sg.track_rep_grid(G, act)
    T = G["T"]
    v_raw = G["v"].copy()
    v = np.clip(G["v"], 0, V_MAX)
    flat = act.get("atype") in ("track_running", "treadmill_running", "indoor_running") or sg.flat_track(act.get("track"))
    w = fm.P["grade_window_s"]
    grade = np.zeros(T + 1)
    alt_raw = G.get("alt")
    alt = None
    if "alt" in G and not flat:
        alt = fm.level_alt(G)[0]
        dh, dd = fm._shift_diff(alt, w), fm._shift_diff(G["d"], w)
        grade = np.where(dd > 30, dh / np.maximum(dd, 1), 0.0)
        jump = np.abs(np.diff(alt, prepend=alt[0])) > fm.P["vz_glitch"]
        if jump.any():
            bad = np.convolve(jump.astype(float), np.ones(2 * w + 1), mode="same") > 0
            grade[bad] = 0.0
    grade = np.clip(grade, -fm.P["grade_clip"], fm.P["grade_clip"])
    vg = v * fm.gap_factor(grade)
    vg_strava = v * C.gap_factor_strava(grade)
    legs, cur = [], 0
    for a, b in sg.find_pauses(G):
        if a > cur:
            legs.append((cur, a))
        cur = b
    if cur < T:
        legs.append((cur, T + 1))
    return dict(G=G, T=T, v=v, v_raw=v_raw, vg=vg, vg_strava=vg_strava, grade=grade, alt=alt, alt_raw=alt_raw,
                flat=flat, legs=legs, moving=~G["stopped"], hr=G.get("hr"))


def _climb(alt, thr=3.0):
    if alt is None or len(alt) < 2:
        return 0.0, 0.0
    ref, u, d = alt[0], 0.0, 0.0
    for a in alt[::2]:
        if a - ref >= thr:
            u += a - ref; ref = a
        elif ref - a >= thr:
            d += ref - a; ref = a
    return u, d


def _nanmean(x):
    x = np.asarray(x, float); x = x[~np.isnan(x)]
    return float(x.mean()) if len(x) else np.nan


def _seg(S, a, b, hr_rest, hr_max):
    """Stats of grid seconds [a, b)."""
    b1 = min(b, S["T"])
    d = S["G"]["d"]
    dist = float(d[b1] - d[a]); dur = b - a
    hr = S["hr"]
    h = hr[a:b] if hr is not None else np.array([])
    hl = hr[a + 2 * (b - a) // 3: b] if hr is not None else np.array([])
    mv = S["moving"][a:b]
    gr = S["grade"][a:b]
    up, dn = _climb(S["alt"][a:b1 + 1]) if S["alt"] is not None else (0.0, 0.0)
    hm = _nanmean(h); hlm = _nanmean(hl)
    cover = float(np.mean(~np.isnan(h))) if len(h) else 0.0
    rng = (hr_max - hr_rest) if hr_max and hr_rest else np.nan
    return {"start_s": int(a), "dur_s": float(dur), "dist_m": dist,
            "speed": dist / dur if dur else np.nan,
            "gap_speed": float(S["vg"][a:b].mean()) if dur else np.nan,
            "gap_speed_strava": float(S["vg_strava"][a:b].mean()) if dur else np.nan,
            "grade_mean": float(gr.mean()) if dur else np.nan,
            "grade_abs_mean": float(np.abs(gr).mean()) if dur else np.nan,
            "climb_m": up, "desc_m": dn,
            "hr_mean": hm, "hr_max": float(np.nanmax(h)) if cover > 0 else np.nan, "hr_last3": hlm,
            "hr_cover": cover,
            "hrr_mean": (hm - hr_rest) / rng if rng == rng else np.nan,
            "hrr_last3": (hlm - hr_rest) / rng if rng == rng else np.nan,
            "moving_share": float(mv.mean()) if dur else np.nan,
            "glitch_s": int((S["v_raw"][a:b] > V_MAX).sum())}


def _best_effort(cum, D):
    """Fastest time to cover D of the cumulative distance `cum` (1 Hz): (t, i0, i1) or None."""
    n = len(cum)
    if n < 2 or cum[-1] - cum[0] < D:
        return None
    tgt = cum + D
    j = np.searchsorted(cum, tgt, side="left")
    ok = j < n
    i = np.nonzero(ok)[0]
    if not len(i):
        return None
    j = j[ok]
    prev = cum[j - 1]; nxt = cum[j]
    frac = np.where(nxt > prev, (tgt[ok] - prev) / np.maximum(nxt - prev, 1e-9), 0.0)
    t = (j - 1 - i) + frac
    t = np.where(j - 1 >= i, t, j - i)
    k = int(np.argmin(t))
    return float(t[k]), int(i[k]), int(j[k])


def run_job(job):
    """Per run: (activity-level features, segment rows) from the FIT. Never raises."""
    aid, fit, act, hr_rest, hr_max = job
    try:
        return aid, _run_features(aid, fit, act, hr_rest, hr_max)
    except Exception as e:  # noqa: BLE001
        return aid, {"err": repr(e)[:200]}


def _run_features(aid, fit, act, hr_rest, hr_max):
    S = _series(fit, act)
    G, T = S["G"], S["T"]
    mv = S["moving"]
    d = G["d"]
    f = {"grid_dist_m": float(d[-1]), "grid_moving_s": int(mv.sum()), "grid_elapsed_s": int(T)}
    gps = G.get("gps")
    f["gps_share"] = float(gps.mean()) if gps is not None else 0.0
    f["glitch_s"] = int((S["v_raw"] > V_MAX).sum())
    f["max_step_m"] = float(np.max(np.diff(d))) if T > 1 else 0.0
    f["flat_course"] = bool(S["flat"])
    # elevation / grade (levelled altitude; flat courses -> 0)
    up, dn = _climb(S["alt"]) if S["alt"] is not None else (0.0, 0.0)
    f["climb_m"], f["desc_m"] = up, dn
    rup, rdn = _climb(S["alt_raw"]) if S["alt_raw"] is not None else (np.nan, np.nan)
    f["climb_raw_m"], f["desc_raw_m"] = rup, rdn
    g = S["grade"][mv] if mv.any() else np.array([0.0])
    f["grade_abs_mean"] = float(np.abs(g).mean())
    f["grade_abs_p90"] = float(np.percentile(np.abs(g), 90))
    f["grade_share_gt4"] = float((np.abs(g) > 0.04).mean())
    f["alt_range_m"] = float(np.nanmax(S["alt"]) - np.nanmin(S["alt"])) if S["alt"] is not None else 0.0
    sv = S["v"][mv].sum()
    f["gap_ratio"] = float(S["vg"][mv].sum() / sv) if sv > 0 else 1.0
    f["gap_ratio_strava"] = float(S["vg_strava"][mv].sum() / sv) if sv > 0 else 1.0
    f["speed_cv"] = float(np.std(S["v"][mv]) / max(np.mean(S["v"][mv]), 0.1)) if mv.sum() > 30 else np.nan
    # tortuosity: mean |heading change| per km over 20 s GPS steps while moving (track ovals ~ 900 deg/km)
    f["turn_deg_per_km"] = np.nan
    if gps is not None and "lat" in G:
        idx = np.arange(0, T + 1, 20); idx = idx[gps[idx] & mv[idx]]
        if len(idx) > 5:
            y = np.radians(G["lat"][idx]) * 6371000.0
            x = np.radians(G["lon"][idx]) * 6371000.0 * math.cos(math.radians(float(np.nanmean(G["lat"][idx]))))
            dx, dy = np.diff(x), np.diff(y); st = np.hypot(dx, dy)
            keep = st > 15
            if keep.sum() > 4:
                hd = np.degrees(np.arctan2(dy[keep], dx[keep]))
                dh = np.abs((np.diff(hd) + 180) % 360 - 180)
                f["turn_deg_per_km"] = float(dh.sum() / (st[keep].sum() / 1000))
            f["lat"] = float(np.nanmean(G["lat"][idx])); f["lon"] = float(np.nanmean(G["lon"][idx]))
    # HR on moving seconds
    hr = S["hr"]
    rng = hr_max - hr_rest
    if hr is not None and mv.sum() > 60:
        hm = hr[mv]; ok = ~np.isnan(hm)
        f["hr_cover"] = float(ok.mean())
        if ok.sum() > 30:
            f["hr_mean"] = float(hm[ok].mean())
            f["hr_p95"] = float(np.percentile(hm[ok], 95))
            # held 12 s peak
            hh = np.where(np.isnan(hr), 0, hr)
            if len(hh) > 12:
                f["hr_peak12"] = float(np.max(np.convolve(hh, np.ones(12) / 12, mode="valid")))
            f["hrr_mean"] = (f["hr_mean"] - hr_rest) / rng
            # efficiency factor: GAP m/s per beat, moving seconds after 10 min, HR present
            k = mv.copy(); k[:600] = False
            k &= ~np.isnan(hr) if hr is not None else k
            if k.sum() > 300:
                f["ef_gap_per_hr"] = float(S["vg"][k].mean() / hr[k].mean())
                # decoupling (Pa:HR) between halves of those seconds
                ii = np.nonzero(k)[0]; h1, h2 = ii[: len(ii) // 2], ii[len(ii) // 2:]
                e1 = S["vg"][h1].mean() / hr[h1].mean(); e2 = S["vg"][h2].mean() / hr[h2].mean()
                f["decoupling"] = float(e1 / e2 - 1)
    else:
        f["hr_cover"] = 0.0
    # pause-free legs
    legs = [(a, b) for a, b in S["legs"] if b - a >= 30]
    f["n_legs"] = len(legs)
    f["longest_leg_m"] = max((float(d[min(b, T)] - d[a]) for a, b in legs), default=0.0)
    segs = []
    # best efforts (raw distance and GAP distance) inside one pause-free leg
    for kind, arr in (("best", None), ("best_gap", S["vg"])):
        for lab, D in C.STD_DIST.items():
            best = None
            for a, b in legs:
                cum = (d[a:b] - d[a]) if arr is None else np.concatenate([[0.0], np.cumsum(arr[a:b - 1])])
                r = _best_effort(cum, D)
                if r and (best is None or r[0] < best[0]):
                    best = (r[0], a + r[1], a + r[2])
            if best:
                t, i0, i1 = best
                s = _seg(S, i0, max(i1, i0 + 1), hr_rest, hr_max)
                s.update(seg_kind=kind, label=lab, target_m=D, time_s=t,
                         vdot=float(C.vdot(D, t)))
                segs.append(s)
                f[f"{'be' if kind == 'best' else 'beg'}_{lab}_s"] = t
    # per-km splits along the whole activity (elapsed time; pauses inside flagged by moving_share)
    if d[-1] >= 1000:
        marks = np.searchsorted(d, np.arange(1000, d[-1] + 1e-6, 1000.0))
        prev = 0
        for k, m in enumerate(marks):
            m = int(min(m, T))
            if m - prev >= 60:
                s = _seg(S, prev, m, hr_rest, hr_max)
                s.update(seg_kind="km", label=f"km{k + 1}", target_m=1000.0, time_s=float(m - prev),
                         vdot=float(C.vdot(s["dist_m"], s["dur_s"])))
                segs.append(s)
            prev = m
    # reps from groups.json (rep detector / lap presses / Bislett indoor grid)
    reps = act.get("reps") or []
    starts = act.get("rep_starts") or []
    breaks = act.get("breaks_s") or []
    trs = {round(r[0]): r for r in (act.get("track_reps") or [])}
    for k, (r, st) in enumerate(zip(reps, starts)):
        a = int(round(st)); b = int(round(st + r[0]))
        if b <= a or b > T + 1:
            continue
        s = _seg(S, a, b, hr_rest, hr_max)
        tr = trs.get(a) or next((x for x in trs.values() if abs(x[0] - st) < 3), None)
        rd = float(r[1]) if r[1] is not None else np.nan   # None: time-only reps (Bislett indoor, no distance)
        s.update(seg_kind="rep", label=f"rep{k + 1}", target_m=float(tr[3]) if tr else rd, time_s=float(r[0]),
                 rep_dist_m=rd, rest_after_s=float(breaks[k]) if k < len(breaks) else np.nan,
                 vdot=float(C.vdot(rd, r[0])) if rd == rd else np.nan)
        segs.append(s)
    for s in segs:
        if s["dur_s"] > 0:
            s["vdot_gap"] = float(C.vdot_from_speed(s["gap_speed"], s["dur_s"])) if s["gap_speed"] > 0.5 else np.nan
    return {"f": f, "segs": segs}


# ------------------------------------------------------------------ main-process helpers
def fit_paths(person):
    out = {}
    for p in glob.glob(str(C.DATA / person / "activities" / "*" / "*.fit")):
        out[Path(p).stem] = p
    return out


def rest_hr_daily(person):
    """{ordinal day: resting HR} from daily summaries."""
    out = {}
    for f in sorted(glob.glob(str(C.DATA / person / "ranges" / "daily_summaries_*.json"))):
        try:
            for x in json.loads(Path(f).read_text()).get("data", []):
                r = (x.get("heartRate") or {}).get("restingValue")
                if r and 30 <= r <= 100:
                    out[dt.date.fromisoformat(x["calendarDate"]).toordinal()] = r
        except Exception:  # noqa: BLE001
            pass
    return out


def rest_hr_30d(daily, day, default):
    vals = [daily[k] for k in range(day - 30, day) if k in daily]
    return float(np.median(vals)) if len(vals) >= 5 else float(default)


def workout_of(a, official, not_race):
    t = a.get("type") or ""
    if official:
        return "race"
    if t == "race?":
        return "time_trial" if not_race else "race_auto"
    if t.startswith("intervals"):
        return "intervals"
    return {"easy": "easy", "steady": "steady", "long": "long", "tempo": "tempo", "run_nogps": "unknown_nogps",
            "run_other": "other"}.get(t, t or "other")


def surface_of(a, f):
    """(surface, reason). Rules in DATA.md. trail is a rough guess (no map surface data)."""
    at = a.get("atype")
    trk = (a.get("track") or {}).get("name") if a.get("track") else None
    if at == "treadmill_running" or a.get("type") == "intervals_treadmill":
        return "treadmill", "atype/type"
    if at == "indoor_running" or a.get("indoor_bislett"):
        return "indoor_track", "atype/indoor_bislett"
    if at == "track_running" or (a.get("track") and trk not in (None, "Frogner stadion")) or \
            (a.get("track") and trk is None):
        return "track", "track mode / groups track"
    if f.get("gps_share", 0) < 0.3:
        return "unknown_nogps", "gps_share<0.3"
    if at == "trail_running":
        return "trail", "atype trail_running"
    return "road", "default (GPS outdoors; trail vs road is not observable without map data, see terrain)"


def terrain_of(f):
    """flat / rolling / hilly / steep from the levelled climb per km (track/indoor/treadmill: flat)."""
    if f.get("flat_course"):
        return "flat"
    dk = max(f.get("grid_dist_m", 0) / 1000, 0.5)
    cpk = (f.get("climb_m", 0) + f.get("desc_m", 0)) / 2 / dk
    return "flat" if cpk < 5 else "rolling" if cpk < 12 else "hilly" if cpk < 25 else "steep"


def load_person(person, races, workers):
    D = C.DATA / person
    groups = json.loads((D / "groups.json").read_text())
    acts = groups["activities"]; places = groups.get("places", {}); areas = groups.get("areas", {})
    idx = {str(a["activityId"]): a for a in json.loads((D / "activities_index.json").read_text())}
    hs = json.loads((D / "profile" / "hr_settings.json").read_text())
    hr_max, hr_rest_def = float(hs["max_hr"]), float(hs["rest_hr"])
    devs = hs.get("devices", {}); hact = hs.get("activities", {})
    fitj = json.loads((D / "fitness.json").read_text())
    # Production items keyed on (id, discipline): a session can carry both an interval/hard score and a long/bound
    # item (G10 bug A1: a plain {id: item} dict kept only the bound and dropped 32 interval scores). The interval item
    # fills prod_*, the long item of such a session goes to prod_bound_*; every item also goes to prod_items.parquet.
    prod_all = {}
    for s in fitj["sessions"]:
        if s.get("discipline") in ("interval", "long"):
            prod_all[(str(s["id"]), s["discipline"])] = s
    prod, prod_second = {}, {}
    for (k, disc), s in prod_all.items():
        if disc == "interval" or (k, "interval") not in prod_all:
            prod[k] = s
        else:
            prod_second[k] = s
    item_rows = []
    for (k, disc), s in prod_all.items():
        pc_ = s.get("components") or {}
        item_rows.append({"person": person, "activity_id": int(k), "date": pd.Timestamp(s["date"][:10]),
                          "day": dt.date.fromisoformat(s["date"][:10]).toordinal(), "discipline": disc,
                          "kind": s.get("kind"), "score": s.get("score"), "km": s.get("km"), "sigma": s.get("sigma"),
                          "window_s": pc_.get("window_s"), "gap_speed": pc_.get("gap_speed"),
                          "rep_s": pc_.get("rep_s"), "rest_s": pc_.get("rest_s")})
    daily_rest = rest_hr_daily(person)
    off = {}
    for r in races.get("races", []):
        if r.get("sport") != "run":
            continue
        x = (r.get("results") or {}).get(person)
        if x and x.get("activity_id"):
            off[str(x["activity_id"])] = {"race_name": r["name"], "race_km": float(r["km"]),
                                          "official_s": float(x["official_s"]), "race_source": x.get("source", "chip")}
    not_races = {str(x["activity_id"]) for x in (races.get("not_races") or {}).get(person, [])}
    fits = fit_paths(person)

    run_ids = [k for k, a in acts.items() if a.get("sport") == "run"]
    jobs = []
    for k in run_ids:
        a = acts[k]
        if k in fits:
            day = dt.date.fromisoformat(a["date"]).toordinal()
            jobs.append((k, fits[k], a, rest_hr_30d(daily_rest, day, hr_rest_def), hr_max))
    t0 = time.time()
    with Pool(workers) as pool:
        res = dict(pool.imap_unordered(run_job, jobs, chunksize=4))
    print(f"  {person}: {len(jobs)} FITs in {time.time() - t0:.1f}s, "
          f"{sum('err' in r for r in res.values())} errors", flush=True)

    rows, segrows = [], []
    for k in run_ids:
        a = acts[k]; ix = idx.get(k, {}); r = res.get(k, {})
        f = r.get("f", {}) if r else {}
        day = dt.date.fromisoformat(a["date"]).toordinal()
        o = off.get(k)
        dev = (hact.get(k) or {}).get("device")
        dv = devs.get(dev, {}) if dev else {}
        tr = dv.get("trust", {}) if dv else {}
        flags = (hact.get(k) or {}).get("flags") or []
        surf, why = surface_of(a, f)
        trk = (a.get("track") or {}).get("name") if a.get("track") else None
        pr = prod.get(k)
        pc = (pr or {}).get("components") or {}
        rest = rest_hr_30d(daily_rest, day, hr_rest_def)
        row = {
            "person": person, "activity_id": int(k), "date": pd.Timestamp(a["date"]), "day": day,
            "start_local": ix.get("startTimeLocal"), "atype": a.get("atype"), "type": a.get("type"),
            "type_detail": a.get("type_detail"), "workout": workout_of(a, o, k in not_races),
            "sig": a.get("sig"), "place": (places.get(a.get("place")) or {}).get("name", a.get("place")),
            "area": (areas.get(a.get("area")) or {}).get("name", a.get("area")), "route": a.get("route"),
            "track_name": trk, "surface": surf, "surface_rule": why, "terrain": terrain_of(f), "indoor_bislett": bool(a.get("indoor_bislett")),
            "frogner_loop": trk == "Frogner stadion", "dist_trusted": bool(a.get("dist_trusted")),
            "override_fitness": (a.get("override") or {}).get("fitness"),
            "dist_km": a.get("dist_km"), "moving_s": a.get("moving_s"), "elapsed_s": a.get("dur_s"),
            "pause_s": a.get("pause_s"), "n_pauses": a.get("n_pauses"),
            "dist_km_garmin": (ix.get("distance") or 0) / 1000 or np.nan,
            "avg_hr_garmin": ix.get("averageHR"), "max_hr_garmin": ix.get("maxHR"),
            "egain_garmin": ix.get("elevationGain"), "eloss_garmin": ix.get("elevationLoss"),
            "wrist_temp_min": ix.get("minTemperature"), "wrist_temp_max": ix.get("maxTemperature"),
            "garmin_te_aer": ix.get("aerobicTrainingEffect"), "garmin_te_ana": ix.get("anaerobicTrainingEffect"),
            "garmin_load": ix.get("activityTrainingLoad"),
            "hr_device": dev, "hr_source": dv.get("source"), "trust_interval": tr.get("interval", 0.5),
            "trust_steady": tr.get("steady", 0.5), "hr_flags": ",".join(flags) or None,
            "hr_max_person": hr_max, "hr_rest_30d": rest,
            "is_official_race": bool(o), "race_name": (o or {}).get("race_name"), "race_km": (o or {}).get("race_km"),
            "official_s": (o or {}).get("official_s"), "race_time_source": (o or {}).get("race_source"),
            "not_race": k in not_races,
            "n_reps": len(a.get("reps") or []) or 0,
            "prod_discipline": (pr or {}).get("discipline"), "prod_kind": (pr or {}).get("kind"),
            "prod_score": (pr or {}).get("score"), "prod_km": (pr or {}).get("km"), "prod_sigma": (pr or {}).get("sigma"),
            "prod_window_s": pc.get("window_s"), "prod_gap_speed": pc.get("gap_speed"), "prod_rep_s": pc.get("rep_s"),
            "prod_rest_s": pc.get("rest_s"), "prod_work_rest": pc.get("work_rest"), "prod_work_hrr": pc.get("work_hrr"),
            "prod_hrr": pc.get("hrr"), "fit_error": r.get("err") if r else "no fit",
            "prod_n_items": int((k, "interval") in prod_all) + int((k, "long") in prod_all),
            "prod_bound_kind": (prod_second.get(k) or {}).get("kind"),
            "prod_bound_score": (prod_second.get(k) or {}).get("score"),
            "prod_bound_km": (prod_second.get(k) or {}).get("km"),
            "prod_bound_sigma": (prod_second.get(k) or {}).get("sigma"),
        }
        row.update(f)
        if o:
            row["race_vdot"] = C.vdot(o["race_km"] * 1000, o["official_s"])
        reps = a.get("reps") or []
        if reps:
            rt = np.array([x[0] for x in reps], float)
            rdist = np.array([np.nan if x[1] is None else x[1] for x in reps], float)
            row.update(rep_time_med=float(np.median(rt)), rep_dist_med=float(np.median(rdist)),
                       rep_speed_mean=float(np.nansum(rdist) / rt.sum()) if np.isfinite(rdist).all() else np.nan,
                       rest_med=float(np.median(a.get("breaks_s"))) if a.get("breaks_s") else np.nan,
                       rep_total_m=float(rdist.sum()) if np.isfinite(rdist).all() else np.nan)
            row["work_rest"] = row["rep_time_med"] / row["rest_med"] if row.get("rest_med") else np.nan
        rows.append(row)
        for s in (r.get("segs") or []):
            segrows.append({"person": person, "activity_id": int(k), "date": pd.Timestamp(a["date"]), "day": day, **s})
    A = pd.DataFrame(rows).sort_values(["date", "start_local"]).reset_index(drop=True)
    Sg = pd.DataFrame(segrows)
    # all-sport hours per day (ski etc. load) for training-load features
    allday = {}
    for k, a in acts.items():
        dd = dt.date.fromisoformat(a["date"]).toordinal()
        h = (a.get("moving_s") or a.get("dur_s") or 0) / 3600
        allday.setdefault(dd, {"all": 0.0, "ski": 0.0})
        allday[dd]["all"] += h
        if a.get("sport") == "ski":
            allday[dd]["ski"] += h
    I = pd.DataFrame(item_rows).sort_values(["day", "activity_id", "discipline"]).reset_index(drop=True)
    return A, Sg, allday, I


def add_quality(A):
    """usable flag + quality notes (cleaning rules, DATA.md)."""
    q = []
    for _, r in A.iterrows():
        n = []
        if pd.notna(r["fit_error"]):
            n.append("no_series")
        if r.get("override_fitness") == "exclude":
            n.append("user_exclude")
        if r.get("prod_kind") == "implausible":
            n.append("implausible_prod")
        dk = r["dist_km"] or 0
        if dk < 1.0 or (r["moving_s"] or 0) < 300:
            n.append("tiny")
        if (r["moving_s"] or 0) > 0 and dk * 1000 / r["moving_s"] > 6.5:
            n.append("too_fast_for_run")
        if r["surface"] == "treadmill" or not r["dist_trusted"]:
            n.append("dist_untrusted")
        if (r.get("glitch_s") or 0) > 10:
            n.append("gps_glitch")
        hrbad = not (r.get("hr_cover") or 0) >= 0.75 or pd.notna(r.get("hr_flags"))
        if hrbad:
            n.append("hr_bad")
        q.append(",".join(n) or None)
    A["quality"] = q
    A["usable"] = ~A["quality"].fillna("").str.contains("no_series|user_exclude|implausible_prod|tiny|too_fast_for_run")
    A["hr_ok"] = ~A["quality"].fillna("").str.contains("hr_bad|no_series")
    A["pace_ok"] = A["usable"] & ~A["quality"].fillna("").str.contains("dist_untrusted|gps_glitch")
    return A


def add_load(A, allday):
    """Training-load features from days strictly before the run's date (no same-day leakage)."""
    A = A.copy()
    # Banister TRIMP per run: moving min * hrr * 0.64 e^(1.92 hrr) (men's coefficients); runs without usable HR get
    # the person's median TRIMP per minute of the same workout type
    hrr = A["hrr_mean"].where(A["hr_ok"])
    mins = A["moving_s"].fillna(0) / 60
    A["trimp"] = mins * hrr * 0.64 * np.exp(1.92 * hrr)
    per_min = (A["trimp"] / mins.replace(0, np.nan))
    fill = per_min.groupby(A["workout"]).transform("median").fillna(per_min.median())
    A["trimp"] = A["trimp"].fillna(mins * fill)
    d0 = int(A["day"].min()) - 400; d1 = int(A["day"].max()) + 1
    n = d1 - d0 + 1
    km = np.zeros(n); hrs = np.zeros(n); tr = np.zeros(n); nr = np.zeros(n); hard = np.zeros(n)
    allh = np.zeros(n); skih = np.zeros(n)
    for _, r in A[A["usable"]].iterrows():
        i = int(r["day"]) - d0
        km[i] += r["dist_km"] or 0; hrs[i] += (r["moving_s"] or 0) / 3600; tr[i] += r["trimp"]; nr[i] += 1
        hard[i] += r["workout"] in HARD_WORKOUTS or (r["workout"] in ("race_auto",))
    for dd, x in allday.items():
        if d0 <= dd <= d1:
            allh[dd - d0] += x["all"]; skih[dd - d0] += x["ski"]

    def roll(x, w):  # sum over the w days before each day (exclusive)
        c = np.concatenate([[0.0], np.cumsum(x)])
        i = np.arange(n)
        return c[i] - c[np.maximum(i - w, 0)]
    ewm = {}
    for name, tau in (("atl", 7.0), ("ctl", 42.0)):
        a = np.exp(-1 / tau); s = 0.0; out = np.zeros(n)
        for i in range(n):
            out[i] = s           # value entering day i (days < i only)
            s = s * a + tr[i] * (1 - a)
        ewm[name] = out
    last_hard = np.full(n, np.nan); last_run = np.full(n, np.nan); lh = lr = None
    for i in range(n):
        last_hard[i] = (i - lh) if lh is not None else np.nan
        last_run[i] = (i - lr) if lr is not None else np.nan
        if hard[i]:
            lh = i
        if nr[i]:
            lr = i
    ii = (A["day"] - d0).astype(int).to_numpy()
    for w in (7, 28, 42, 365):
        A[f"run_km_{w}d"] = roll(km, w)[ii]
    for w in (7, 28):
        A[f"run_h_{w}d"] = roll(hrs, w)[ii]
        A[f"all_h_{w}d"] = roll(allh, w)[ii]
    A["ski_h_28d"] = roll(skih, 28)[ii]
    A["n_runs_28d"] = roll(nr, 28)[ii]
    A["n_hard_28d"] = roll(hard, 28)[ii]
    A["trimp_7d"] = roll(tr, 7)[ii]
    A["atl"] = ewm["atl"][ii]; A["ctl"] = ewm["ctl"][ii]; A["tsb"] = A["ctl"] - A["atl"]
    A["days_since_hard"] = last_hard[ii]; A["days_since_run"] = last_run[ii]
    return A


def build_targets(A, Sg):
    """Evaluation targets (DATA.md 'Targets'). One row per target: km, time_s, vdot, tier, weight + covariates that
    are known before the effort (no HR, no outcome)."""
    T = []
    cov = ["surface", "terrain", "flat_course", "climb_m", "desc_m", "grade_abs_mean", "hr_device", "trust_interval",
           "trust_steady", "wrist_temp_max", "place", "track_name", "indoor_bislett", "atype", "type", "workout"]
    for _, r in A[A["usable"]].iterrows():
        base = {"person": r["person"], "activity_id": r["activity_id"], "date": r["date"], "day": r["day"],
                **{c: r.get(c) for c in cov}}
        base["climb_per_km"] = (r["climb_m"] or 0) / max(r["dist_km"] or 1, 0.5) if r["climb_m"] == r["climb_m"] else np.nan
        if r["is_official_race"]:
            T.append({**base, "tier": "race", "km": r["race_km"], "time_s": r["official_s"],
                      "vdot": C.vdot(r["race_km"] * 1000, r["official_s"]), "weight": 1.0,
                      "target_id": f"{r['activity_id']}:race", "note": r["race_name"]})
            continue
        if r["workout"] == "intervals" and r["prod_kind"] == "hard" and r["prod_discipline"] == "interval" \
                and r["prod_score"] == r["prod_score"] and r["prod_score"] is not None:
            T.append({**base, "tier": "interval", "km": 5.0, "time_s": C.race_time(r["prod_score"], 5000.0),
                      "vdot": float(r["prod_score"]), "weight": 0.5, "target_id": f"{r['activity_id']}:interval",
                      "note": f"production W'bal score ({r['sig']})"})
            continue
        if not r["pace_ok"] or r["workout"] == "intervals":
            continue
        # hard continuous best effort: tempo / time trial / auto race, or a high-HR continuous effort on a trusted device
        s = Sg[(Sg["activity_id"] == r["activity_id"]) & (Sg["seg_kind"] == "best_gap") & (Sg["target_m"] >= BE_CONT_MIN)]
        if s.empty:
            continue
        b = s.loc[s["vdot"].idxmax()]
        intent = r["workout"] in ("tempo", "time_trial", "race_auto")
        hr_hard = bool(r["hr_ok"]) and (r["trust_steady"] or 0) >= 0.8 and (b["hrr_last3"] or 0) >= 0.88 \
            and b["hr_cover"] >= 0.8
        if not (intent or hr_hard):
            continue
        T.append({**base, "tier": "hard_effort", "km": b["target_m"] / 1000, "time_s": b["time_s"],
                  "vdot": b["vdot"], "weight": 0.5, "target_id": f"{r['activity_id']}:{b['label']}",
                  "note": f"best GAP {b['label']} ({'intent ' + r['workout'] if intent else 'HR'})",
                  "climb_per_km": (b["climb_m"] or 0) / (b["dist_m"] / 1000) if b["dist_m"] > 0 else np.nan,
                  "grade_abs_mean": b["grade_abs_mean"]})
    T = pd.DataFrame(T)
    T["band"] = T["km"].map(C.band_of)
    return T.sort_values(["person", "date"]).reset_index(drop=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--persons", default=",".join(C.PERSONS))
    args = ap.parse_args()
    races = json.loads((C.DATA / "races.json").read_text())
    C.OUT.mkdir(parents=True, exist_ok=True)
    As, Ss, Is = [], [], []
    for p in args.persons.split(","):
        A, Sg, allday, I = load_person(p, races, args.workers)
        Is.append(I)
        A = add_quality(A)
        A = add_load(A, allday)
        As.append(A); Ss.append(Sg)
    A = pd.concat(As, ignore_index=True)
    Sg = pd.concat(Ss, ignore_index=True)
    # whole-run VDOT (moving time) and GAP version, for convenience
    A["vdot_whole"] = C.vdot(A["dist_km"] * 1000, A["moving_s"])
    A["vdot_whole_gap"] = C.vdot_from_speed(A["dist_km"] * 1000 / A["moving_s"] * A["gap_ratio"], A["moving_s"])
    Sg = Sg.merge(A[["activity_id", "usable", "pace_ok", "hr_ok", "surface", "workout"]], on="activity_id", how="left")
    Sg["suspect"] = (Sg["glitch_s"] > 0) | (Sg["vdot"] > 80)   # GPS spike inside, or faster than any human
    T = build_targets(A, Sg)
    A.to_parquet(C.OUT / "activities.parquet", index=False)
    Sg.to_parquet(C.OUT / "segments.parquet", index=False)
    T.to_parquet(C.OUT / "targets.parquet", index=False)
    I = pd.concat(Is, ignore_index=True)
    # only items of runs in activities (groups.json sport == run); usable flag for convenience
    I = I.merge(A[["person", "activity_id", "usable", "start_local"]], on=["person", "activity_id"], how="inner")
    I.to_parquet(C.OUT / "prod_items.parquet", index=False)
    rep = {"built": dt.datetime.now().isoformat(timespec="seconds"),
           "activities": A.groupby("person").size().to_dict(),
           "usable": A[A["usable"]].groupby("person").size().to_dict(),
           "segments": Sg.groupby(["person", "seg_kind"]).size().unstack(fill_value=0).to_dict(orient="index"),
           "targets": T.groupby(["person", "tier", "band"]).size().reset_index().values.tolist(),
           "surface": A.groupby(["person", "surface"]).size().unstack(fill_value=0).to_dict(orient="index"),
           "workout": A.groupby(["person", "workout"]).size().unstack(fill_value=0).to_dict(orient="index"),
           "quality": A["quality"].fillna("ok").str.split(",").explode().groupby(A["person"]).value_counts()
           .unstack(fill_value=0).to_dict(orient="index")}
    (C.OUT / "prep_report.json").write_text(json.dumps(rep, indent=1, default=str))
    print(json.dumps(rep, indent=1, default=str))


if __name__ == "__main__":
    main()

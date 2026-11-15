"""Per-person, per-device heart-rate settings -> data/<person>/profile/hr_settings.json.

Usage: uv run scripts/hr_settings.py adrian [--dry-run] [--workers 8]

Every run / ski FIT is scanned (cached in data/<person>/cache/hr_scan.pkl) for:
- the device: the recording watch (file_id product + serial) and the HR source: `strap` when device_info lists an
  external heart-rate sensor (ANT+ / BLE device type 120), else `wrist` (the watch's optical sensor).
- HR quality: coverage, the highest HR held 12 s, cadence lock (optical HR locked onto the step cadence),
  steady 3-min chunks (speed, HR) for HR-vs-pace, and for interval sessions how HR follows the reps.

From that, per person:
- `max_hr`: the person's max HR (one number, used for %HRR everywhere): per device the highest held-12 s run peak with
  >= 2 other sessions within 3 bpm; the highest of those over devices with interval trust >= 0.5, sanity-checked
  against the watch's HR-max zones (see `max_hr`).
- `rest_hr`: median Garmin daily resting HR of the last 365 days, full-wear days only (>= 20 h worn). The fitness
  model keeps its own 30-day rolling median per day and uses this only as the fallback.
- `devices`: one entry per watch + HR source ("fr935/wrist", "4315/wrist", "4315/strap", ...), with date range,
  quality metrics, and `trust` {interval, steady} for the HR-intent variant of the fitness model.
- borrowed watches: a device whose watch serial is another person's main watch (more sessions in their
  hr_settings.json; adrian's 6 runs of 2021 on patrik's 3077) gets `borrowed_from` <owner>. The runs stay the
  person's own; the device is never the date fallback (`device_on`), and without enough interval sessions of its own
  it takes the owner's measured trust for that watch (same sensor) instead of the default (`mark_borrowed`).
- `activities`: {id: {device, source, flags}}; flags mark implausible HR (`cadence_lock`, `hr_high_for_pace`,
  `hr_dropout`). Flagged sessions aren't trusted by the HR-intent variant (trust 0).

Manual override: `"source": "manual"` (or no `source`) keeps the whole file. To override single values keep
`"source": "auto"` and put them in `"manual": {...}` (e.g. {"max_hr": 200, "devices": {"3077/wrist": {"trust":
{"interval": 0.2}}}}): the builder copies `manual` over every rebuild and lookups apply it last.
Lookups for the other scripts: `settings(person)`, `for_activity(person, id, date)`. Docs: docs/data-layout.md,
docs/fitness-model.md ("Heart-rate settings per person and device").
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import math
import pickle
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parent.parent
SCAN_VERSION = 2
HOLD_S = 12
FULL_WEAR_H = 20
CHUNK_S = 180
HR_SENSOR_TYPE = 120          # ANT+ device type heart_rate
PRODUCT_KEY = {2691: "fr935", 2413: "fenix3_hr"}   # else the product number (adrian's new watch: "4315")
# trust anchors: adrian's hand-set values (docs/fitness-model.md, HR-intent variant) at his measured metrics
TRUST_DEFAULT = {"interval": 0.5, "steady": 0.5}    # too little data to judge a device
LOCK_TOL = 3                  # bpm: |HR - steps/min| within this counts as locked
LOCK_SHARE = 0.5              # a run with >= 50 % of its moving HR samples within LOCK_TOL of the cadence ...
LOCK_CORR = 0.3               # ... and HR following the cadence second by second (high-pass correlation): cadence lock
HIGH_FOR_PACE_BPM = 15        # session HR this far above the device's usual HR at that pace: implausible
DROPOUT_SHARE = 0.25          # > 25 % of moving time without HR: dropout


# ------------------------------------------------------------------ lookups (cheap, used by other scripts)
_CACHE: dict = {}


def path(person: str) -> Path:
    return ROOT / "data" / person / "profile" / "hr_settings.json"


def settings(person: str) -> dict:
    """The person's hr_settings.json with `manual` applied ({} when missing). Cached per file mtime."""
    p = path(person)
    try:
        m = p.stat().st_mtime_ns
    except OSError:
        return {}
    c = _CACHE.get(person)
    if c and c[0] == m:
        return c[1]
    doc = json.loads(p.read_text())
    doc = _apply_manual(doc)
    _CACHE[person] = (m, doc)
    return doc


def _apply_manual(doc: dict) -> dict:
    man = doc.get("manual") or {}
    if not man:
        return doc
    doc = {**doc, "devices": {k: dict(v) for k, v in (doc.get("devices") or {}).items()}}
    for k in ("max_hr", "rest_hr"):
        if man.get(k):
            doc[k] = man[k]
    for dk, dv in (man.get("devices") or {}).items():
        d = doc["devices"].setdefault(dk, {})
        for k, v in dv.items():
            d[k] = {**(d.get(k) or {}), **v} if isinstance(v, dict) and isinstance(d.get(k), dict) else v
    if man.get("activities"):
        doc["activities"] = {**(doc.get("activities") or {}), **man["activities"]}
    return doc


def device_on(doc: dict, date: str) -> str | None:
    """device key of the person's main device on a date (from the devices' date ranges; wrist preferred)"""
    best = None
    for k, d in (doc.get("devices") or {}).items():
        if d.get("borrowed_from"):   # someone else's watch, worn a few times: never the person's watch by date
            continue
        if d.get("from", "") <= date <= d.get("to", "9999"):
            if best is None or (d.get("source") == "wrist" and d.get("n", 0) >= (doc["devices"][best].get("n", 0))):
                best = k
    return best


def for_activity(person: str, act_id=None, date: str | None = None) -> dict:
    """HR normalisation for one activity: {max, rest, device, source, trust {interval, steady}, flags}.
    `rest` is the person's rest_hr (the fitness model uses its own daily value). trust is 0 for flagged sessions."""
    doc = settings(person)
    a = (doc.get("activities") or {}).get(str(act_id)) if act_id is not None else None
    dev = (a or {}).get("device") or (device_on(doc, date) if date else None)
    D = (doc.get("devices") or {}).get(dev) or {}
    flags = list((a or {}).get("flags") or [])
    trust = dict(TRUST_DEFAULT | (D.get("trust") or {}))
    trust = {k: trust[k] for k in ("interval", "steady")}
    if flags:
        trust = {"interval": 0.0, "steady": 0.0}
    return {"max": D.get("max_hr") or doc.get("max_hr"), "rest": doc.get("rest_hr"), "device": dev,
            "source": D.get("source"), "trust": trust, "flags": flags}


# ------------------------------------------------------------------ FIT scan
def _fields():
    """extra FIT fields for the scan (gps_quality's fast reader decodes only what is listed)"""
    import gps_quality as gq
    gq._WANT[0][3] = "serial_number"
    gq._WANT[23].update({0: "device_index", 1: "device_type", 3: "serial_number", 25: "source_type"})
    gq._WANT[20].update({2: "altitude", 3: "heart_rate", 4: "cadence", 53: "fractional_cadence", 78: "enhanced_altitude"})
    return gq


def _devices(raw) -> dict:
    """{product, serial, source, strap}: the recording watch and whether an external HR sensor was connected"""
    fid = (raw.get("file_id") or [{}])[0]
    prod = fid.get("product") or fid.get("garmin_product")
    out = {"product": prod, "serial": fid.get("serial_number"), "source": "wrist", "strap": None}
    for d in raw.get("device_info") or []:
        st, ty = d.get("source_type"), d.get("device_type", d.get("antplus_device_type"))
        if st in (1, 2, 3, "antplus", "bluetooth", "bluetooth_low_energy") and ty in (HR_SENSOR_TYPE, "heart_rate"):
            out["source"] = "strap"
            out["strap"] = {"product": d.get("product") or d.get("garmin_product"), "serial": d.get("serial_number")}
    return out


def device_key(product, source) -> str:
    k = PRODUCT_KEY.get(product, product if isinstance(product, str) else (str(product) if product else "unknown"))
    return f"{k}/{source}"


def scan_fit(fit: str, act: dict) -> dict:
    """HR quality of one activity (see module doc). act: groups.json entry (sport, type, date)."""
    gq = _fields()
    import session_groups as sg
    try:
        raw = gq._read_fast(Path(fit))
    except Exception:  # noqa: BLE001
        raw = gq._read_fitparse(Path(fit))
    out = {**_devices(raw), "date": act["date"], "sport": act["sport"], "type": act["type"]}
    recs = sorted((r for r in raw["record"] if "timestamp" in r), key=lambda r: r["timestamp"])
    if len(recs) < 30:
        return out
    jumps = sg.fit_clock_jumps(raw, recs)
    t = np.array([gq.unjump(r["timestamp"], jumps) if jumps else r["timestamp"] for r in recs], float)
    t -= t[0]
    keep = np.concatenate([[True], np.diff(t) > 0])
    recs = [r for r, k in zip(recs, keep) if k]; t = t[keep]
    T = int(min(t[-1], 86400))
    if T < 300:
        return out
    g = np.arange(T + 1, dtype=float)
    col = lambda f: np.array([f(r) for r in recs], float)
    hr = col(lambda r: r["heart_rate"] if r.get("heart_rate") and 30 < r["heart_rate"] < 230 else np.nan)
    dist = col(lambda r: r["distance"] / 100 if r.get("distance") is not None else np.nan)
    cad = col(lambda r: 2 * (r["cadence"] + r.get("fractional_cadence", 0) / 128) if r.get("cadence") else np.nan)
    alt = col(lambda r: (r.get("enhanced_altitude", r.get("altitude")) / 5 - 500) if r.get("enhanced_altitude", r.get("altitude")) is not None else np.nan)

    def near(x, gap=5):   # nearest sample within `gap` s, else nan
        ok = ~np.isnan(x)
        if ok.sum() < 2:
            return np.full(T + 1, np.nan)
        tt, xx = t[ok], x[ok]
        i = np.clip(np.searchsorted(tt, g), 1, len(tt) - 1)
        j = np.where(np.abs(g - tt[i - 1]) <= np.abs(tt[i] - g), i - 1, i)
        y = xx[j].copy()
        y[np.abs(tt[j] - g) > gap] = np.nan
        return y

    H = near(hr)
    dm = ~np.isnan(dist)
    d = np.interp(g, t[dm], np.maximum.accumulate(dist[dm])) if dm.sum() >= 2 else np.zeros(T + 1)
    v = np.zeros(T + 1)
    v[5:-5] = (d[10:] - d[:-10]) / 10
    A = near(alt, 30)
    run = act["sport"] == "run"
    mv = v > (1.5 if run else 1.0)
    out["moving_s"] = int(mv.sum())
    if mv.sum() < 300:
        return out
    hm = H[mv]
    out["hr_cov"] = round(float((~np.isnan(hm)).mean()), 3)
    if np.isnan(H).all():
        return out
    hh = hm[~np.isnan(hm)]
    if len(hh) < 60:
        return out
    out["hr_p50"], out["hr_p95"] = round(float(np.median(hh)), 1), round(float(np.percentile(hh, 95)), 1)
    # highest HR held HOLD_S s (min over a 12 s window, whole file)
    Hf = np.where(np.isnan(H), -1, H)
    if T > HOLD_S:
        w = np.lib.stride_tricks.sliding_window_view(Hf, HOLD_S + 1).min(axis=1)
        out["held"] = float(w.max()) if w.max() > 0 else None
    if run:
        C = near(cad)
        ok = mv & ~np.isnan(H) & ~np.isnan(C) & (C >= 130)
        if ok.sum() >= 120:
            out["spm"] = round(float(np.median(C[ok])), 1)
            out["lock"] = round(float((np.abs(H[ok] - C[ok]) <= LOCK_TOL).mean()), 3)
            # a real lock makes HR follow the cadence second by second: correlation of the 31 s high-pass of both
            # (real HR is smooth and lags; ~0 when HR and cadence are only numerically close at race effort)
            k = np.ones(31) / 31
            Hi, Ci = np.where(np.isnan(H), np.nanmedian(H), H), np.where(np.isnan(C), np.nanmedian(C), C)
            hh, cc = Hi - np.convolve(Hi, k, "same"), Ci - np.convolve(Ci, k, "same")
            ok[:30] = False; ok[-30:] = False
            if ok.sum() >= 120 and hh[ok].std() > 0 and cc[ok].std() > 0:
                out["lock_corr"] = round(float(np.corrcoef(hh[ok], cc[ok])[0, 1]), 3)
        # steady 3-min chunks from 10 min in: (speed, mean HR, grade); speed CV <= 10 %, no gaps
        ch = []
        for s in range(600, T - CHUNK_S, CHUNK_S):
            x, h = v[s:s + CHUNK_S], H[s:s + CHUNK_S]
            if not mv[s:s + CHUNK_S].all() or np.isnan(h).mean() > 0.1 or x.mean() < 1.8 or x.std() / x.mean() > 0.10:
                continue
            gr = (A[s + CHUNK_S] - A[s]) / max(d[s + CHUNK_S] - d[s], 1) if not (np.isnan(A[s]) or np.isnan(A[s + CHUNK_S])) else 0.0
            if abs(gr) > 0.03:
                continue
            ch.append((round(float(x.mean()), 3), round(float(np.nanmean(h)), 1)))
        out["chunks"] = ch
        # intervals: how HR follows the reps: best correlation of HR with the 30 s mean speed 0-40 s earlier
        if act["type"].startswith("intervals"):
            vs = np.convolve(v, np.ones(30) / 30, mode="same")
            best = None
            for lag in range(0, 41, 5):
                a, b = vs[:len(vs) - lag], H[lag:]
                ok = ~np.isnan(b) & (a > 0.5)
                if ok.sum() > 300:
                    r = float(np.corrcoef(a[ok], b[ok])[0, 1])
                    best = r if best is None or r > best else best
            out["track_corr"] = None if best is None else round(best, 3)
            thr = 0.85 * np.percentile(v[mv], 95)
            hot = (v > thr) & ~np.isnan(H)
            if hot.sum() > 30:
                out["work_hr_p90"] = round(float(np.percentile(H[hot], 90)), 1)
    return out


def _job(j):
    fit, act = j
    try:
        return scan_fit(fit, act)
    except Exception as e:  # noqa: BLE001
        return {"err": repr(e)[:120], "date": act["date"], "sport": act["sport"], "type": act["type"]}


def scan(person: str, workers=8, progress=True) -> dict:
    """{activity id: scan_fit result} for every run / ski in groups.json, cached per FIT (mtime, size)."""
    D = ROOT / "data" / person
    acts = json.loads((D / "groups.json").read_text())["activities"]
    fits = {p.stem: p for p in (D / "activities").glob("*/*.fit")}
    cf = D / "cache" / "hr_scan.pkl"
    cache = pickle.loads(cf.read_bytes()) if cf.exists() else {}
    if cache.get("_v") != SCAN_VERSION:
        cache = {"_v": SCAN_VERSION}
    key = lambda p: (p.stat().st_mtime_ns, p.stat().st_size)
    out, jobs = {}, []
    for i, a in acts.items():
        if a["sport"] not in ("run", "ski") or i not in fits:
            continue
        c = cache.get(i)
        if c and c["key"] == key(fits[i]) and c["type"] == a["type"]:
            out[i] = c["s"]
        else:
            jobs.append((i, str(fits[i]), {k: a[k] for k in ("date", "sport", "type")}))
    if jobs:
        js = [(f, a) for _, f, a in jobs]
        if workers > 1 and len(js) > 4:
            with Pool(workers) as pool:
                res = pool.map(_job, js, chunksize=4)
        else:
            res = [_job(x) for x in js]
        for (i, f, a), r in zip(jobs, res):
            out[i] = r
            cache[i] = {"key": key(Path(f)), "type": a["type"], "s": r}
        cf.parent.mkdir(exist_ok=True)
        cf.write_bytes(pickle.dumps(cache))
    if progress:
        print(f"hr scan {person}: {len(out)} activities ({len(jobs)} read)")
    return out


# ------------------------------------------------------------------ per-device statistics
STEADY_SLOPE = 20.0           # bpm per m/s: HR-vs-pace slope for the local residual (steady chunks)
LOCAL_DAYS = 90               # neighbours on the same device within +-90 days
TC_ANCHOR = (0.506, 0.86)     # adrian's median interval track_corr on the FR935 / the 4315 (2018-2026 data)
TRUST_AT_ANCHOR = {"interval": (0.3, 1.0), "steady": (0.5, 1.0)}   # adrian's hand-set trust at those anchors
MIN_INTERVALS = 5             # interval sessions needed to estimate a device's trust
MIN_PEAK_RUNS = 10            # runs with HR needed before a device's peak can set max_hr
PEAK_CONFIRM = (3, 2)         # a peak counts when >= 2 other sessions are within 3 bpm of it


def _trust(tc: float) -> dict:
    """trust {interval, steady} from the median interval track_corr, linear through adrian's two anchors"""
    x = (tc - TC_ANCHOR[0]) / (TC_ANCHOR[1] - TC_ANCHOR[0])
    return {k: round(float(np.clip(lo + (hi - lo) * x, 0.1, 1.0)), 2) for k, (lo, hi) in TRUST_AT_ANCHOR.items()}


def _local_resid(rows) -> dict:
    """{id: HR above the device's usual at that pace} for runs with >= 2 steady chunks: the session's median of
    (HR - STEADY_SLOPE * speed) minus the median of the same over the device's other runs within +-LOCAL_DAYS"""
    rows = [(i, v) for i, v in rows if v["sport"] == "run" and len(v.get("chunks") or []) >= 2]
    if len(rows) < 6:
        return {}
    o = np.array([dt.date.fromisoformat(v["date"]).toordinal() for _, v in rows])
    z = np.array([np.median([h - STEADY_SLOPE * s for s, h in v["chunks"]]) for _, v in rows])
    out = {}
    for j, (i, _) in enumerate(rows):
        nb = (np.abs(o - o[j]) <= LOCAL_DAYS) & (np.arange(len(rows)) != j)
        if nb.sum() >= 4:
            out[i] = float(z[j] - np.median(z[nb]))
    return out


def _confirmed_peak(vals):
    """highest held-12 s peak with >= 2 other sessions within 3 bpm (one-off spikes don't set max HR)"""
    tol, k = PEAK_CONFIRM
    v = sorted(vals, reverse=True)
    for i, x in enumerate(v):
        if sum(1 for y in v if y >= x - tol) >= k + 1:
            return x
    return None


def mad_sd(x):
    x = np.asarray(x, float)
    return float(1.4826 * np.median(np.abs(x - np.median(x)))) if len(x) else None


def build(person: str, workers=8, progress=True) -> dict:
    S = scan(person, workers, progress)
    by = {}
    for i, v in S.items():
        by.setdefault(device_key(v.get("product"), v.get("source")), []).append((i, v))
    devices, acts = {}, {}
    for k, rows in sorted(by.items()):
        runs = [(i, v) for i, v in rows if v["sport"] == "run" and v.get("hr_p50")]
        resid = _local_resid(rows)
        rsd = mad_sd(list(resid.values())) if len(resid) >= 8 else None
        hi = max(HIGH_FOR_PACE_BPM, 2.5 * rsd) if rsd else None
        for i, v in rows:
            fl = []
            if (v.get("lock") or 0) >= LOCK_SHARE and (v.get("lock_corr") or 0) >= LOCK_CORR:
                fl.append("cadence_lock")
            if v.get("hr_cov") is not None and v["hr_cov"] < 1 - DROPOUT_SHARE:
                fl.append("hr_dropout")
            if hi and resid.get(i) is not None and resid[i] > hi:
                fl.append("hr_high_for_pace")
            acts[i] = {"device": k, **({"flags": fl} if fl else {})}
        flagged = {i for i, a in acts.items() if a.get("flags")}
        tcs = [v["track_corr"] for i, v in rows if v.get("track_corr") is not None and i not in flagged]
        tc = float(np.median(tcs)) if len(tcs) >= MIN_INTERVALS else None
        peaks = [v["held"] for i, v in runs if v.get("held") and i not in flagged]
        top = sorted(((v["held"], v["date"], i) for i, v in runs if v.get("held") and i not in flagged), reverse=True)[:5]
        dates = sorted(v["date"] for _, v in rows)
        p0 = rows[0][1]
        devices[k] = {
            "product": p0.get("product"), "source": p0.get("source"),
            "serials": sorted({v.get("serial") for _, v in rows if v.get("serial")}),
            **({"straps": sorted({json.dumps(v["strap"], sort_keys=True) for _, v in rows if v.get("strap")})} if p0.get("source") == "strap" else {}),
            "from": dates[0], "to": dates[-1], "n": len(rows), "n_runs_hr": len(runs),
            "trust": _trust(tc) if tc is not None else dict(TRUST_DEFAULT),
            "trust_from": "interval_track_corr" if tc is not None else f"default (< {MIN_INTERVALS} interval sessions)",
            "metrics": {
                "interval_track_corr": round(tc, 3) if tc is not None else None, "n_intervals": len(tcs),
                "steady_resid_sd_bpm": round(rsd, 1) if rsd else None, "n_steady_runs": len(resid),
                "hr_coverage_median": round(float(np.median([v["hr_cov"] for _, v in rows if v.get("hr_cov") is not None])), 3)
                if any(v.get("hr_cov") is not None for _, v in rows) else None,
                "flagged": {f: sum(1 for i, _ in rows if f in (acts[i].get("flags") or [])) for f in ("cadence_lock", "hr_dropout", "hr_high_for_pace")},
            },
            "peak_hr": _confirmed_peak(peaks) if len(peaks) >= 3 else None,
            "peak_top": [{"hr": int(h), "date": d, "id": int(i)} for h, d, i in top],
        }
    return {"devices": devices, "activities": acts}


def mark_borrowed(person: str, devices: dict, root: Path | None = None) -> dict:
    """In place: devices recorded on another person's watch. A device is borrowed when one of its watch serials is in
    another person's hr_settings.json on the same device key with more sessions there (that person owns the watch).
    Sets `borrowed_from`; a device whose trust is the default (< MIN_INTERVALS interval sessions) takes the owner's
    measured trust for that watch (`trust_from` "owner ..."). Returns {device key: owner}."""
    root = ROOT if root is None else root
    out = {}
    for f in sorted((root / "data").glob("*/profile/hr_settings.json")):
        other = f.parent.parent.name
        if other == person:
            continue
        try:
            od = json.loads(f.read_text()).get("devices") or {}
        except (OSError, ValueError):
            continue
        for k, d in devices.items():
            o = od.get(k) or {}
            if set(d.get("serials") or []) & set(o.get("serials") or []) and o.get("n", 0) > d.get("n", 0) \
                    and not o.get("borrowed_from"):
                d["borrowed_from"] = out[k] = other
                if not str(d.get("trust_from", "")).startswith("interval") and str(o.get("trust_from", "")).startswith("interval"):
                    d["trust"] = dict(o["trust"])
                    d["trust_from"] = f"owner {other}'s {k} (borrowed watch, < {MIN_INTERVALS} own interval sessions)"
    return out


def zones_max(person: str):
    try:
        z = json.loads((ROOT / "data" / person / "profile" / "heart_rate_zones.json").read_text())
        z = next((x for x in z if x.get("sport") == "DEFAULT"), z[0]) if isinstance(z, list) else z
        return z.get("maxHeartRateUsed")
    except (OSError, ValueError, IndexError, AttributeError):
        return None


def max_hr(devices: dict, zmax) -> dict:
    """The person's max HR: the highest confirmed peak over devices with interval trust >= 0.5 and >= MIN_PEAK_RUNS runs
    with HR (a device that under-reads hard efforts, like the FR935, doesn't set it); else the confirmed peak of the most
    trusted device; else the watch's zone max. Checks: 150..225 and within 10 bpm of the zone max (when set) -> `check`
    note, value kept."""
    ok = [(k, d) for k, d in devices.items() if d.get("peak_hr") and d["n_runs_hr"] >= MIN_PEAK_RUNS]
    good = [(k, d) for k, d in ok if d["trust"]["interval"] >= 0.5]
    if good:
        k, d = max(good, key=lambda kd: kd[1]["peak_hr"])
        v, src = int(d["peak_hr"]), f"confirmed peak on {k}"
    elif ok:
        k, d = max(ok, key=lambda kd: (kd[1]["trust"]["interval"], kd[1]["n_runs_hr"]))
        v, src = int(d["peak_hr"]), f"confirmed peak on {k} (low-trust device)"
    elif any(d.get("peak_hr") for d in devices.values()):
        k, d = max(((k, d) for k, d in devices.items() if d.get("peak_hr")), key=lambda kd: kd[1]["peak_hr"])
        v, src = int(d["peak_hr"]), f"confirmed peak on {k} (few runs)"
    elif zmax:
        v, src = int(zmax), "watch HR zones"
    else:
        return {"value": None, "from": None, "check": "no HR data"}
    chk = []
    if not 150 <= v <= 225:
        chk.append(f"{v} outside 150-225")
    if zmax and abs(v - zmax) > 10:
        chk.append(f"{v} differs from the watch's zone max {zmax} by > 10 bpm")
    return {"value": v, "from": src, "zones_max": zmax, "check": "; ".join(chk) or None}


def rest_hr(person: str) -> dict | None:
    D = ROOT / "data" / person
    rows = {}
    for f in glob.glob(str(D / "ranges" / "daily_summaries_*.json")):
        for r in (json.loads(Path(f).read_text()).get("data") or []):
            hr, wc = r.get("heartRate") or {}, r.get("wellnessChronology") or {}
            if hr.get("restingValue") and (wc.get("totalDurationInMillis") or 0) / 3.6e6 >= FULL_WEAR_H:
                rows[r["calendarDate"]] = hr["restingValue"]
    if not rows:
        return None
    last = dt.date.fromisoformat(max(rows))
    since = str(last - dt.timedelta(days=365))
    v = [x for d, x in rows.items() if d > since]
    return {"rest_hr": int(round(float(np.median(v)))), "days": len(v), "from": since, "to": str(last)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("person")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    out = path(a.person)
    old = json.loads(out.read_text()) if out.exists() else {}
    if old and old.get("source", "manual") != "auto":
        print(f"{out} is hand-edited (source {old.get('source', 'manual')!r}): not overwritten")
        return
    B = build(a.person, a.workers)
    bor = mark_borrowed(a.person, B["devices"])
    if bor:
        print("borrowed watches:", bor)
    mx, rs = max_hr(B["devices"], zones_max(a.person)), rest_hr(a.person)
    res = {"version": 2, "max_hr": mx["value"], "rest_hr": rs and rs["rest_hr"], "source": "auto",
           "built": dt.date.today().isoformat(),
           "max_from": mx, "rest_from": rs and {k: rs[k] for k in ("days", "from", "to")},
           "manual": old.get("manual") or {},
           "note": "Built by scripts/hr_settings.py (docs/data-layout.md). Override single values in `manual` "
                   "(kept on rebuild), or set \"source\": \"manual\" to freeze the whole file.",
           "devices": B["devices"], "activities": B["activities"]}
    show = {k: v for k, v in res.items() if k != "activities"}
    print(json.dumps(show, indent=1))
    print("flagged:", {i: a["flags"] for i, a in B["activities"].items() if a.get("flags")})
    if not a.dry_run:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(res, indent=1) + "\n")
        print("wrote", out)


if __name__ == "__main__":
    main()

"""Prototype: detect workouts done together by >= 2 people  (v2).

    cd /Users/adrian/Code/garmin-analysis
    uv run python -I /tmp/together/together_proto.py [--json /tmp/together/out/pairs.json] [--groups /tmp/together/out/together.json]

(-I: /tmp/together is shared with other scratch files, e.g. an inspect.py that shadows the stdlib.)
Read-only on data/ (no caches written: rep detectors are called directly, not through compare's cache).
Pipeline: index-level candidates (time overlap) -> per pair metrics on the overlap (1 Hz, absolute UTC time)
-> pair label -> groups (connected components over "together" edges) for N people.
"""
import sys, json, math, datetime as dt, itertools, argparse
from pathlib import Path
import numpy as np

ROOT = Path("/Users/adrian/Code/garmin-analysis")
sys.path.insert(0, str(ROOT / "scripts"))
import session_groups as sg, gps_quality as gq   # noqa: E402

# ------------------------------------------------------------------ thresholds
PRE_MARGIN_S = 600     # index candidates: time windows overlap (with this margin)
MIN_OVERLAP_S = 300    # less shared time than this: nothing to judge
NEAR_M = 50            # "with each other": 60 s rolling median distance <= NEAR_M ...
ROLL_S = 61
DV_MS = 0.8            # ... and 60 s rolling median |speed difference| <= DV_MS (on a track, being close is often chance)
MOVE_MS = 1.0          # ... counted over seconds where at least one is moving
SMOOTH_S = 15
TOG_FULL = 0.60        # with-share of the joint moving time -> together
TOG_OVERRIDE = 0.80    # with-share so high that the rep structure doesn't matter (side by side all the time)
TOG_PART_S = 600       # >= 10 min with each other -> partly together / warm-up together
REP_MIN_S = 30         # reps shorter than this are ignored (strides, detector noise)
ONSET_DT_S = 12        # other's speed step within this of the rep start (after the pair's clock offset)
ONSET_RATIO = 1.3      # other's speed after / before the rep start
ONSET_ON = 0.7         # other is "on" this share of the first min(dur, 120 s) of the rep
PACE_RATIO = 0.7       # other's speed in the rep >= this x own rep speed
REP_POS_M = 60         # positions at the rep start within this (if both have GPS)
SHARED_FULL = 0.75     # shared rep time / own rep time (in the overlap), best side -> same workout
SHARED_MIN = 2
RACE_START_S, RACE_START_M = 120, 150
IDX_START_M, IDX_START_S = 500, 900   # index-only (no FIT / no GPS) plausibility
IV = ("intervals_track", "intervals_road", "intervals_treadmill", "ski_intervals")
UNTYPED = ("multi_sport", "run_other", "other")   # no classifier verdict: use the detector's reps if any
BISLET = (59.92527, 10.73306)


def people():
    return sorted(p.parent.name for p in (ROOT / "data").glob("*/activities_index.json"))


def index(person):
    a = json.loads((ROOT / "data" / person / "activities_index.json").read_text())
    g = json.loads((ROOT / "data" / person / "groups.json").read_text())
    fits = {int(p.stem): p for p in (ROOT / "data" / person / "activities").glob("*/*.fit")}
    out = []
    for x in a:
        s = dt.datetime.fromisoformat(x["startTimeGMT"]).replace(tzinfo=dt.timezone.utc).timestamp()
        e = s + (x.get("elapsedDuration") or x.get("duration") or 0)
        i = int(x["activityId"])
        f = fits.get(i)
        fix = true_start(f, e - s) if f else None   # watch clock jumps: Garmin's start time can be days-months early
        fixed = fix is not None and abs(fix - s) > 60
        if fixed:
            s, e = fix, fix + (e - s)
        out.append({"person": person, "id": i, "s": s, "e": e, "x": x, "meta": g["activities"].get(str(i)) or {},
                    "fit": f, "places": g["places"], "clock_fixed": fixed})
    return out


def true_start(f, elapsed):
    """UTC start from the last record minus the elapsed time, only for files whose record span exceeds the
    session elapsed time by > 60 s (clock jump); None otherwise. (Production: cache per FIT mtime+size.)"""
    raw = gq._read_fast(f)
    ts = [r["timestamp"] for r in raw["record"] if "timestamp" in r]
    if len(ts) < 2 or max(ts) - min(ts) - elapsed <= 60:
        return None
    return max(ts) + gq.FIT_EPOCH - elapsed


_cache = {}


def _smooth(v, k):
    return np.convolve(np.pad(v, (k // 2, k - 1 - k // 2), mode="edge"), np.ones(k) / k, mode="valid")


def load(a):
    """1 Hz grid + absolute time of grid second 0 + reps [(start, end)] in grid s"""
    k = (a["person"], a["id"])
    if k in _cache:
        return _cache[k]
    f, m = a["fit"], a["meta"]
    raw = gq._read_fast(f)
    ts = sorted(r["timestamp"] for r in raw["record"] if "timestamp" in r)
    s = sg.read_series(f)
    G = sg.to_grid(s)
    # anchored at the LAST record: after a clock jump the post-jump (GPS-set) clock is the right one
    base = ts[-1] + gq.FIT_EPOCH - s["t"][-1]
    gps = G["gps"].copy() if G["gps"] is not None else np.zeros(G["T"] + 1, bool)
    ind = m.get("indoor_bislett")
    if ind:   # indoor Bislett: positions are stale / garbage near the track, speed from the lap reps
        sg.indoor_grid(G, m)
        if "lat" in G:
            near = np.hypot((G["lat"] - BISLET[0]) * 111320, (G["lon"] - BISLET[1]) * 55700) < 400
            gps &= ~near
    reps, src = [], None
    typ = m.get("type")
    if ind and ind.get("rep_laps"):
        reps, src = [(t0, t0 + d) for t0, d, _ in ind["rep_laps"]], "indoor laps"
    elif typ == "ski_intervals":
        import ski_intervals as si
        reps, src = [(r["start"], r["end"]) for r in si.analyse(f)["reps"]], "ski detector"
    elif (m.get("sport") == "run" and typ in IV) or typ in UNTYPED or m.get("atype") == "multi_sport":
        import run_analysis as ra
        try:
            reps, src = [(r["start"], r["end"]) for r in ra.analyse(f)["reps"]], "run detector"
        except Exception as e:
            print("rep err", k, e, file=sys.stderr)
    reps = [(x, y) for x, y in reps if y - x >= REP_MIN_S]
    prod = next((x.get("product") for x in raw.get("file_id", []) if x.get("product")), None)
    S = {"prod": prod, "G": G, "gps": gps, "base": base, "vs": _smooth(G["v"], SMOOTH_S), "v5": _smooth(G["v"], 5),
         "reps": reps, "rep_src": src, "T": G["T"], "jumps": s["clock_jumps"]}
    _cache[k] = S
    return S


def xy(lat, lon, o):
    return (lat - o[0]) * 111320.0, (lon - o[1]) * 111320.0 * math.cos(math.radians(o[0]))


def at(S, t, key):
    """grid series `key` at absolute times t (linear; NaN outside)"""
    y = S["G"].get(key) if key in S["G"] else S.get(key)
    if y is None:
        return np.full(len(t), np.nan)
    return np.interp(t - S["base"], np.arange(S["T"] + 1), y, left=np.nan, right=np.nan)


def gps_at(S, t):
    g = np.round(t - S["base"]).astype(int)
    ok = (g >= 0) & (g <= S["T"])
    out = np.zeros(len(t), bool)
    out[ok] = S["gps"][g[ok]]
    return out


def pos_lag(A, B, t, sel, o):
    """tau: B's position at t+tau best matches A's at t (median distance over `sel`)"""
    ax, ay = xy(at(A, t, "lat"), at(A, t, "lon"), o)
    best = (None, np.inf)
    for tau in np.arange(-15, 15.01, 0.25):
        bx, by = xy(at(B, t + tau, "lat"), at(B, t + tau, "lon"), o)
        d = np.hypot(ax - bx, ay - by)[sel]
        d = d[np.isfinite(d)]
        if len(d) > 120 and np.median(d) < best[1]:
            best = (float(tau), float(np.median(d)))
    return best


def onset(S, t0, lo=-20, hi=25):
    """speed step near absolute time t0: (onset time, v before, v after) or None"""
    tt = np.arange(t0 + lo - 40, t0 + 130, 1.0)
    v = at(S, tt, "v5")
    if np.isnan(v).mean() > 0.3:
        return None
    rel = tt - t0
    pre = np.nanmedian(v[(rel >= -40) & (rel <= -5)])
    post = np.nanmedian(v[(rel >= 10) & (rel <= 60)])
    thr = (pre + post) / 2
    w = np.flatnonzero((rel >= lo) & (rel <= hi) & (v >= thr))
    if not len(w) or post < 2.0:
        return None
    return t0 + rel[w[0]], pre, post


def follows(S_own, S_oth, reps, tau, both_gps_fn, o):
    """which of S_own's reps (absolute) S_oth starts too: [(k, dt_onset, dpos)]"""
    out = []
    for k, (s, e) in enumerate(reps):
        on_own = onset(S_own, s, -8, 8)
        on_oth = onset(S_oth, s + tau)
        if not on_oth:
            continue
        t_oth, pre, post = on_oth
        if post < ONSET_RATIO * max(pre, 0.3):
            continue
        if on_own and post < PACE_RATIO * on_own[2]:   # other's "rep" much slower than own: jogging, not the rep
            continue
        dur = min(e - s, 120)
        tt = np.arange(s + 15, s + max(16, dur), 1.0)
        vo = at(S_oth, tt + tau, "v5")
        if np.nanmean(vo >= (pre + post) / 2) < ONSET_ON:
            continue
        ref = on_own[0] if on_own else s
        dto = t_oth - ref   # includes tau (raw clock difference)
        if abs(dto - tau) > ONSET_DT_S:
            continue
        dpos = None
        if both_gps_fn(np.array([s]))[0]:
            pa = xy(at(S_own, np.array([s]), "lat"), at(S_own, np.array([s]), "lon"), o)
            pb = xy(at(S_oth, np.array([s + tau]), "lat"), at(S_oth, np.array([s + tau]), "lon"), o)
            dpos = float(np.hypot(pa[0] - pb[0], pa[1] - pb[1])[0])
            if dpos > REP_POS_M:
                continue
        out.append((k, round(float(dto), 1), None if dpos is None else round(dpos)))
    return out


def segments(mask, min_len=60, gap=90):
    runs, cur = [], None
    for i in np.flatnonzero(mask):
        if cur and i - cur[1] <= gap:
            cur[1] = i
        else:
            if cur:
                runs.append(cur)
            cur = [i, i]
    if cur:
        runs.append(cur)
    return [(a, b) for a, b in runs if b - a >= min_len]


def rolling_median(x, k):
    n = len(x)
    out = np.full(n, np.nan)
    h = k // 2
    for i in range(0, n):
        w = x[max(0, i - h): i + h + 1]
        w = w[np.isfinite(w)]
        if len(w) >= k // 3:
            out[i] = np.median(w)
    return out


def first_fix(x, S):
    if S is not None and S["gps"].any():
        j = int(np.argmax(S["gps"]))
        return (float(S["G"]["lat"][j]), float(S["G"]["lon"][j]))
    if x["x"].get("startLatitude"):
        return (x["x"]["startLatitude"], x["x"]["startLongitude"])
    return None


def pair(a, b):
    """metrics + label for two activities of different people"""
    r = {"a": [a["person"], a["id"]], "b": [b["person"], b["id"]]}
    if a["fit"] is None or b["fit"] is None:
        sa, sb = first_fix(a, None), first_fix(b, None)
        r["start_dist_m"] = round(sg.hav(sa, sb)) if sa and sb else None
        r["dstart_s"] = round(b["s"] - a["s"])
        ov = min(a["e"], b["e"]) - max(a["s"], b["s"])
        r["overlap_s"] = round(ov)
        r["overlap_pct"] = round(100 * ov / max(1, min(a["e"] - a["s"], b["e"] - b["s"])))
        ok = r["start_dist_m"] is not None and r["start_dist_m"] <= IDX_START_M and abs(r["dstart_s"]) <= IDX_START_S \
            and r["overlap_pct"] >= 50
        r["label"], r["why"] = ("unverified", "no FIT on one side: index start points %s m / %s s apart, overlap %s %%"
                                % (r["start_dist_m"], r["dstart_s"], r["overlap_pct"])) if ok else ("not_together", "index only, no match")
        r["score"] = 0.5 if ok else 0
        return r
    A, B = load(a), load(b)
    t0, t1 = max(A["base"], B["base"]), min(A["base"] + A["T"], B["base"] + B["T"])
    r["dstart_s"] = round(B["base"] - A["base"])
    r["overlap_s"] = max(0, round(t1 - t0))
    r["share_of"] = (round(100 * r["overlap_s"] / max(1, A["T"])), round(100 * r["overlap_s"] / max(1, B["T"])))
    r["overlap_pct"] = max(r["share_of"])   # of the shorter session
    sa, sb = first_fix(a, A), first_fix(b, B)
    r["start_dist_m"] = round(sg.hav(sa, sb)) if sa and sb else None
    if r["overlap_s"] < MIN_OVERLAP_S:
        r["label"], r["why"], r["score"] = "not_together", "no time overlap", 0
        return r
    t = np.arange(math.ceil(t0), math.floor(t1) + 1, 1.0)
    both = gps_at(A, t) & gps_at(B, t)
    r["both_gps_pct"] = round(100 * both.mean())
    va, vb = at(A, t, "vs"), at(B, t, "vs")
    mov = (va > MOVE_MS) | (vb > MOVE_MS)
    o = sa or sb or (0, 0)
    tau = 0.0
    if both.sum() >= 120:
        ax, ay = xy(at(A, t, "lat"), at(A, t, "lon"), o)
        bx, by = xy(at(B, t, "lat"), at(B, t, "lon"), o)
        d = np.hypot(ax - bx, ay - by)
        d[~both] = np.nan
        db = d[both]
        for R in (25, 50, 100):
            r[f"colo{R}"] = round(100 * float((db <= R).mean()))
        dr = rolling_median(d, ROLL_S)
        dv = rolling_median(np.abs(va - vb), ROLL_S)
        w = both & (dr <= NEAR_M) & (dv <= DV_MS)
        r["with_s"] = int((w & mov).sum())
        r["with_pct_gps"] = round(100 * float((w & mov).sum()) / max(1, (both & mov).sum()))   # of the joint moving time with GPS on both
        r["with_pct"] = round(100 * float((w & mov).sum()) / max(1, mov.sum()))               # of the joint moving time
        r["gps_cover_pct"] = round(100 * float((both & mov).sum()) / max(1, mov.sum()))
        segs = segments(w)
        r["with_segs_min"] = [(round(float(t[x] - t0) / 60, 1), round(float(t[y] - t0) / 60, 1)) for x, y in segs]
        # clock / recording offset from side-by-side running (both moving, close)
        sel = w & (va > 2) & (vb > 2) & (d <= 30)
        if sel.sum() > 120:
            lag, dmed = pos_lag(A, B, t, sel, o)
            r["tau_pos_s"], r["tau_pos_dmed_m"], r["tau_pos_n"] = lag, round(dmed, 1), int(sel.sum())
    else:
        r["with_pct"] = None
        r["gps_cover_pct"] = 0
    # speed cross-correlation (works without GPS too)
    best = (None, -2)
    va5 = at(A, t, "v5")
    for lag in range(-20, 21):
        vb_ = at(B, t + lag, "v5")
        ok = np.isfinite(va5) & np.isfinite(vb_)
        if ok.sum() > 120 and np.std(vb_[ok]) > 0.05 and np.std(va5[ok]) > 0.05:
            c = float(np.corrcoef(va5[ok], vb_[ok])[0, 1])
            if c > best[1]:
                best = (lag, c)
    r["speed_lag_s"], r["speed_corr"] = best[0], round(best[1], 2)
    # ---- rep structure (reps inside the overlap only)
    ra = [(x + A["base"], y + A["base"]) for x, y in A["reps"] if t0 <= x + A["base"] <= t1]
    rb = [(x + B["base"], y + B["base"]) for x, y in B["reps"] if t0 <= x + B["base"] <= t1]
    r["n_reps"] = (len(ra), len(rb))
    r["rep_src"] = (A["rep_src"], B["rep_src"])
    r["products"] = (A["prod"], B["prod"])
    r["rec_dt"] = (float(np.median(np.diff(sg.read_series(a["fit"])["t"]))), float(np.median(np.diff(sg.read_series(b["fit"])["t"]))))
    tau = r.get("tau_pos_s") or 0.0
    bg = lambda tt: gps_at(A, tt) & gps_at(B, tt + tau)   # noqa: E731
    fa = follows(A, B, ra, tau, bg, o) if ra else []           # B starts A's reps
    fb = follows(B, A, rb, -tau, lambda tt: gps_at(B, tt) & gps_at(A, tt - tau), o) if rb else []  # A starts B's reps
    if len(fa) >= SHARED_MIN or len(fb) >= SHARED_MIN:
        offs = [x[1] for x in fa] + [-x[1] for x in fb]   # B onset - A onset
        r["tau_onset_s"] = round(float(np.median(offs)), 1)
    r["follow"] = {"b_in_a": len(fa), "a_in_b": len(fb)}
    r["follow_detail"] = {"b_in_a": fa, "a_in_b": fb}
    r["label"], r["why"], r["score"], r["shared_reps"] = classify(r, a, b, ra, rb, fa, fb)
    # where / when the joint part starts: first shared rep, else first with-second, else the later start
    cand = []
    if fa:
        cand.append(ra[fa[0][0]][0])
    if fb:
        cand.append(rb[fb[0][0]][0])
    if r.get("with_segs_min"):
        cand.append(t0 + 60 * r["with_segs_min"][0][0])
    tj = min(cand) if cand else t0
    r["joint_start_utc"] = dt.datetime.fromtimestamp(tj, dt.timezone.utc).isoformat(timespec="seconds")
    for S in (A, B):
        if gps_at(S, np.array([tj]))[0]:
            r["joint_pos"] = (round(float(at(S, np.array([tj]), "lat")[0]), 5), round(float(at(S, np.array([tj]), "lon")[0]), 5))
            break
    return r


def classify(r, a, b, ra, rb, fa, fb):
    ta, tb = a["meta"].get("type"), b["meta"].get("type")
    wp = r.get("with_pct")
    wf = (wp or 0) / 100
    ws = r.get("with_s", 0)
    na, nb = len(ra), len(rb)
    shared = max(len(fa), len(fb))
    struct = na >= 2 or nb >= 2
    tog_note = ""
    if wp is not None and ws >= TOG_PART_S:
        tog_note = f"; with each other {wp} % ({ws // 60} min: {r['with_segs_min'][:4]} min)"
    # 1. side by side nearly all the time: together whatever the detectors say
    if wp is not None and wf >= TOG_OVERRIDE:
        if struct and shared >= SHARED_MIN:
            return "same_workout", f"with each other {wp} % of the joint moving time, {shared} shared reps (reps {na}/{nb})", round(wf, 2), shared
        return "together", f"with each other {wp} % of the joint moving time", round(wf, 2), shared
    # 2. structured: shared reps decide
    if struct:
        if shared >= SHARED_MIN:
            # coverage = shared / reps of the side with fewer reps (a side without detected reps doesn't limit)
            # coverage: share of a side's rep time that was shared (the side that did less decides; a side without
            # detected reps doesn't count). Rep time, not count: detectors count warm-up blocks / short strides as reps
            covs = []
            for reps, fol in ((ra, fa), (rb, fb)):
                tot = sum(e - s_ for s_, e in reps)
                if len(reps) >= 2 and tot > 0:
                    covs.append(sum(reps[k][1] - reps[k][0] for k, *_ in fol) / tot)
            cov = max(covs)
            who = "b_in_a" if len(fa) >= len(fb) else "a_in_b"
            extra = f"{shared} shared reps (reps {na}/{nb}, {who}), onset offset {r.get('tau_onset_s')} s"
            if cov >= SHARED_FULL:
                return "same_workout", extra + tog_note, round(min(1, cov), 2), shared
            return "partly_together", extra + f", at most {round(100 * cov)} % of one side's rep time" + tog_note, round(cov * 0.8, 2), shared
        lab = "same_place_diff_workout" if (wp is None or (r.get("colo100") or 0) >= 20 or ws >= TOG_PART_S) else "not_together"
        if wp is None and not near_start(r):
            lab = "not_together"
        if wp is not None and 180 <= ws < TOG_PART_S:
            tog_note = f"; with each other {ws // 60} min ({r['with_segs_min'][:4]} min: warm-up / cool-down?)"
        return lab, f"reps {na}/{nb}, {shared} started together" + tog_note, round(0.3 * min(1, ws / 1800), 2) if lab != "not_together" else 0, 0
    # 3. continuous
    if wp is None:
        sc = r.get("speed_corr") or 0
        if sc >= 0.6:
            return "unverified", f"no GPS on one side; speed profiles correlate {sc}", 0.4, 0
        return "not_together", f"no GPS on one side, speed corr {sc}", 0, 0
    race = ("race" in (ta or "")) and ("race" in (tb or ""))
    if race and abs(r["dstart_s"]) <= RACE_START_S and (r["start_dist_m"] or 1e9) <= RACE_START_M:
        return "same_race", f"both race?, starts {r['dstart_s']} s / {r['start_dist_m']} m apart; with each other {wp} %", 0.6, 0
    if wf >= TOG_FULL:
        return "together", f"with each other {wp} % of the joint moving time", round(wf, 2), 0
    if ws >= TOG_PART_S:
        return "partly_together", f"with each other {wp} % ({ws // 60} min: {r['with_segs_min'][:4]} min)", round(wf, 2), 0
    if (r.get("colo100") or 0) >= 30 or near_start(r):
        return "same_place_diff_workout", f"same place & time, apart (with {wp} %, colo100 {r.get('colo100')} %)", 0.1, 0
    return "not_together", f"with {wp} %", 0, 0


def near_start(r):
    return r.get("start_dist_m") is not None and r["start_dist_m"] <= 300


SHOW = ("together", "same_workout", "partly_together", "same_race", "unverified")
RANK = {"same_workout": 6, "together": 6, "same_race": 5, "partly_together": 4, "unverified": 3,
        "same_place_diff_workout": 2, "not_together": 0}


def build_groups(pairs, acts):
    """connected components over edges with a SHOW label; nodes = (person, activity id)"""
    par = {}

    def f(x):
        while par.setdefault(x, x) != x:
            par[x] = par[par[x]]
            x = par[x]
        return x
    edges = [p for p in pairs if p["label"] in SHOW]
    for p in edges:
        par[f(tuple(p["a"]))] = f(tuple(p["b"]))
    comp = {}
    for p in edges:
        comp.setdefault(f(tuple(p["a"])), []).append(p)
    groups = []
    for _, es in comp.items():
        nodes = sorted({tuple(e["a"]) for e in es} | {tuple(e["b"]) for e in es}, key=lambda n: acts[n]["s"])
        first = acts[nodes[0]]
        # place ids are per person (adrian's Oslo-5 != patrik's Oslo-5): keep (person, place id)
        pref = next(((n[0], acts[n]["meta"]["place"]) for n in nodes if acts[n]["meta"].get("place")), (None, None))
        jp = next((e["joint_pos"] for e in es if e.get("joint_pos")), None)
        if jp:   # nearest named place of anyone (known > landmark > geocode > generic) within 600 m of the joint start
            cands = []
            for n in nodes:
                for pid, pl in acts[n]["places"].items():
                    dd = sg.hav(jp, (pl["lat"], pl["lon"]))
                    if dd <= 600:
                        cands.append(({"known": 0, "landmark": 1, "geocode": 2}.get(pl.get("src"), 3), dd, n[0], pid))
            if cands:
                pref = min(cands)[2:]
        place = pref[1]
        pname = acts[next(n for n in nodes if n[0] == pref[0])]["places"].get(place, {}).get("name", place) if pref[0] else None
        loc = dt.datetime.fromisoformat(first["x"]["startTimeLocal"])
        best = max(es, key=lambda e: (RANK[e["label"]], e.get("score", 0)))
        gid = f"{loc:%Y-%m-%d_%H%M}_{(place or 'unknown').replace(' ', '-').replace('.', '')}"
        groups.append({
            "id": gid, "joint_start_utc": min((e.get("joint_start_utc") or "9") for e in es), "date": f"{loc:%Y-%m-%d}", "start_local": f"{loc:%H:%M}", "place": {"person": pref[0], "id": place, "name": pname},
            "label": best["label"], "score": best.get("score"),
            "people": sorted({n[0] for n in nodes}),
            "members": [{"person": n[0], "id": n[1], "type": acts[n]["meta"].get("type"), "sig": acts[n]["meta"].get("sig"),
                         "km": acts[n]["meta"].get("dist_km"), "dur_s": acts[n]["meta"].get("dur_s")} for n in nodes],
            "pairs": [{k: e.get(k) for k in ("a", "b", "label", "score", "why", "with_pct", "shared_reps", "dstart_s",
                                              "start_dist_m", "overlap_s", "tau_pos_s", "tau_onset_s", "speed_lag_s")} for e in es],
        })
    return sorted(groups, key=lambda g: g["id"], reverse=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    ap.add_argument("--groups")
    args = ap.parse_args()
    P = {p: index(p) for p in people()}
    acts = {(x["person"], x["id"]): x for p in P for x in P[p]}
    out = []
    for p, q in itertools.combinations(sorted(P), 2):
        for a in P[p]:
            for b in P[q]:
                if min(a["e"], b["e"]) - max(a["s"], b["s"]) < -PRE_MARGIN_S:
                    continue
                try:
                    r = pair(a, b)
                except Exception as e:
                    import traceback; traceback.print_exc()
                    r = {"a": [p, a["id"]], "b": [q, b["id"]], "label": "error", "why": repr(e)}
                r.update(date=a["x"]["startTimeLocal"], ta=a["meta"].get("type"), tb=b["meta"].get("type"),
                         siga=a["meta"].get("sig"), sigb=b["meta"].get("sig"), place=a["meta"].get("place"),
                         placeb=b["meta"].get("place"), kma=a["meta"].get("dist_km"), kmb=b["meta"].get("dist_km"))
                out.append(r)
                print(f"{r['date'][:16]} {a['id']} {b['id']} {r['label']:24} {r.get('why')}")
    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=1, default=str))
    if args.groups:
        G = build_groups([r for r in out if r["label"] != "error"], acts)
        Path(args.groups).write_text(json.dumps({"version": 1, "built": dt.datetime.now().isoformat(timespec="seconds"),
                                                 "people": sorted(P), "groups": G}, indent=1, default=str))


if __name__ == "__main__":
    main()

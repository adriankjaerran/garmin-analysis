"""Prototype: common start line detection. Not part of the repo."""
import sys, math, numpy as np
sys.path.insert(0, "/Users/adrian/Code/garmin-analysis/scripts")
import compare as cmp
import session_groups as sg

R_LINE = 40.0      # m: max cross-track distance at the start line
SHAPE_M = 200.0    # m: template length after the start
SHAPE_TOL = 25.0   # m: max mean distance from template points to the session path (order-free)
LATE_MAX = 150.0   # m: a session starting this far past the line (on the anchor's path) is kept as "late"

def xy(G, o):
    return sg.local_xy(G["lat"], G["lon"], o)

def geo_d(G):
    """cumulative GPS path length per grid second (not the watch distance field: that can run ahead of stale fixes)"""
    if "_geo" not in G:
        x, y = sg.local_xy(G["lat"], G["lon"], (G["lat"][0], G["lon"][0]))
        st = np.hypot(np.diff(x), np.diff(y)); st[~(G["gps"][1:] & G["gps"][:-1])] = 0
        G["_geo"] = np.concatenate([[0], np.cumsum(st)])
    return G["_geo"]

def at_dist(G, t0, offs):
    """times at GPS path length geo(t0)+offs (1 Hz grid interpolation)"""
    d = geo_d(G); T = np.arange(G["T"] + 1, dtype=float)
    d0 = np.interp(t0, T, d)
    nz = np.concatenate([[True], np.diff(d) > 0])
    tt = np.interp(d0 + offs, d[nz], T[nz], right=np.nan)
    return tt

def pos_at(G, t, o):
    T = np.arange(G["T"] + 1, dtype=float)
    x, y = xy(G, o)
    ok = np.isfinite(t)
    X = np.full(len(t), np.nan); Y = np.full(len(t), np.nan)
    X[ok] = np.interp(t[ok], T, x); Y[ok] = np.interp(t[ok], T, y)
    return X, Y

def anchor_line(S, point=None):
    """start moment tA (first moving second with GPS, or closest approach to `point`), start point P, heading h, template"""
    G = S["G"]
    gps = G["gps"]
    if point is None:
        mov = ~S["paused"] & gps
        tA = float(np.argmax(mov))
        P = (float(G["lat"][int(tA)]), float(G["lon"][int(tA)]))
    else:
        o = point
        x, y = xy(G, o); dd = np.hypot(x, y); dd[~gps] = np.inf
        tA = float(np.argmax(dd < 30)) if (dd < 30).any() else float(np.argmin(dd))
        P = point
    tt = at_dist(G, tA, np.array([10, 20, 30, 40, 50.0]))
    X, Y = pos_at(G, tt, P)
    x0, y0 = pos_at(G, np.array([tA]), P)
    h = np.array([np.nanmean(X) - x0[0], np.nanmean(Y) - y0[0]]); h /= np.linalg.norm(h)
    offs = np.arange(0, SHAPE_M + 1, 10.0)
    TX, TY = pos_at(G, at_dist(G, tA, offs), P)
    TX -= x0[0]; TY -= y0[0]   # template relative to the anchor's own start fix
    return {"tA": tA, "P": P, "h": h, "tpl": (TX, TY), "offs": offs, "x0": (x0[0], y0[0])}

def shape_err(G, tc, A, o_xy, k0=0):
    """mean distance from each template point (from template index k0) to the session's path over the next
    SHAPE_M*2 m of own distance: order-free, so milling around at a stop or GPS distance noise doesn't matter"""
    TX, TY = A["tpl"]
    TX, TY = TX[k0:], TY[k0:]
    t1 = at_dist(G, tc, np.array([2 * SHAPE_M]))[0]
    if not np.isfinite(t1):
        t1 = G["T"]
    a, b = int(math.floor(tc)), int(math.ceil(t1)) + 1
    x, y = xy(G, A["P"])
    gx, gy = x[a:b] - o_xy[0], y[a:b] - o_xy[1]
    ok = np.isfinite(TX)
    if ok.sum() < 5 or len(gx) < 2:
        return np.inf
    D = np.hypot(TX[ok][:, None] - gx[None, :], TY[ok][:, None] - gy[None, :])
    j = D.argmin(1)
    # order: the template must be traversed forwards (rejects passing the same streets in reverse)
    if np.mean(np.diff(j) >= 0) < 0.7:
        return np.inf
    return float(D.min(1).mean())

def departure(S, tc, u, R, P=None):
    """start-zone stop: if the session pauses between the crossing and getting 2R past the line, start at the end of that pause"""
    T = S["G"]["T"]
    i = int(tc)
    x, y = xy(S["G"], P)
    after = np.nonzero(np.hypot(x[i:], y[i:]) > 2 * R)[0]
    leave = i + int(after[0]) if len(after) else T
    t0 = tc
    for a, b in S["P"]:
        if a < leave and b > tc:
            t0 = max(t0, float(b))
    return t0

def find_start(S, A):
    G = S["G"]
    if G.get("gps") is None or not G["gps"].any():
        return {"ok": False, "why": "no GPS"}
    x, y = xy(G, A["P"]); h = A["h"]
    u = x * h[0] + y * h[1]; w = -x * h[1] + y * h[0]
    gps = G["gps"]
    T = G["T"]
    cand = []
    for i in np.nonzero((u[:-1] < 0) & (u[1:] >= 0) & gps[:-1] & gps[1:])[0]:
        f = -u[i] / (u[i + 1] - u[i]); tc = i + f
        wc = w[i] + f * (w[i + 1] - w[i])
        if abs(wc) > R_LINE:
            continue
        a, b = max(0, i - 5), min(T, i + 6)
        dx, dy = x[b] - x[a], y[b] - y[a]
        if dx * h[0] + dy * h[1] < 0.5 * math.hypot(dx, dy):   # crossing forward, roughly along the heading
            continue
        cand.append((tc, wc))
    dmin = float(np.min(np.hypot(x, y)[gps]))
    for k, (tc, wc) in enumerate(cand):
        # point on the line where the session crossed (for the shape comparison use the line point, not P)
        ox = A["x0"][0] + wc * -h[1]; oy = A["x0"][1] + wc * h[0]
        e = shape_err(G, tc, A, (ox, oy))
        if e <= SHAPE_TOL:
            # departure: same pass, later forward crossings before the session gets 2*R_LINE past the line
            j = k
            for k2 in range(k + 1, len(cand)):
                t2 = cand[k2][0]
                gd = geo_d(G)
                if (np.hypot(x, y)[int(tc):int(t2) + 1].max() < 2 * R_LINE and gd[int(t2)] - gd[int(tc)] < 4 * R_LINE
                        and shape_err(G, t2, A, (ox, oy)) <= SHAPE_TOL):
                    j = k2
                else:
                    break
            tc, wc = cand[j]
            t0 = departure(S, tc, u, R_LINE, A['P'])
            d = float(np.interp(tc, np.arange(T + 1), G["d"]))
            mt = float(np.interp(tc, np.arange(T + 1), S["mt"]))
            return {"ok": True, "kind": "crossing", "t_cross": round(tc, 1), "t_s": round(t0, 1), "zone_pause_s": round(t0 - tc), "moving_s": round(mt, 1), "dist_m": round(d),
                    "line_offset_m": round(wc, 1), "shape_err_m": round(e, 1), "n_cand": len(cand), "pass": j + 1}
    # no crossing: join the anchor's path within its first LATE_MAX m (late start / side street), moving forward
    GA = A["G"]
    ta = at_dist(GA, A["tA"], np.arange(0, 3000, 10.0))
    AX, AY = pos_at(GA, ta, A["P"])
    okA = np.isfinite(AX)
    AX, AY = AX[okA], AY[okA]
    nlate = int(LATE_MAX / 10) + 1
    for i in np.nonzero(gps)[0]:
        dd = np.hypot(AX[:nlate] - x[i], AY[:nlate] - y[i])
        k = int(np.argmin(dd))
        if dd[k] >= R_LINE or S["paused"][i]:
            continue
        # follows the anchor's path from there? compare the next 150 m with the template from k
        e = shape_err(G, float(i), A, A["x0"], k0=k)
        if e <= SHAPE_TOL:
            d = float(G["d"][i])
            return {"ok": True, "kind": "join" if k > 2 else "at_line", "t_s": departure(S, float(i), u, R_LINE, A['P']), "t_join": float(i), "late_m": k * 10, "off_m": round(float(dd[k])),
                    "dist_m": round(d), "shape_err_m": round(e, 1)}
        break
    rev = sum(1 for i in np.nonzero((u[:-1] >= 0) & (u[1:] < 0) & gps[:-1] & gps[1:])[0] if abs(w[i]) <= R_LINE)
    # starts further along the anchor's path?
    i0 = int(np.argmax(~S["paused"] & gps))
    dd = np.hypot(AX - x[i0], AY - y[i0]); k = int(np.argmin(dd))
    if dd[k] < R_LINE:
        return {"ok": False, "why": f"starts {k*10} m after the start line, on the anchor's path", "late_m": k * 10}
    why = f"crosses the start line only in the opposite direction ({rev}x)" if rev else f"never crosses the start line (closest {dmin:.0f} m)"
    return {"ok": False, "why": why, "dmin_m": round(dmin)}

def run(person, anchor, ids, point=None):
    st = cmp.store(person)
    SA = st.session(anchor)
    A = anchor_line(SA, point); A["G"] = SA["G"]
    print(f"anchor {anchor} tA={A['tA']} P={A['P'][0]:.5f},{A['P'][1]:.5f} heading={math.degrees(math.atan2(A['h'][0], A['h'][1])):.0f}°")
    out = {}
    for i in ids:
        try:
            S = st.session(i)
        except Exception as e:
            out[i] = {"ok": False, "why": str(e)}; continue
        out[i] = find_start(S, A) if i != anchor else {"ok": True, "kind": "anchor", "t_s": A["tA"]}
        print(" ", i, S["meta"].get("date"), out[i])
    return A, out

if __name__ == "__main__":
    person = sys.argv[1]; anchor = int(sys.argv[2]); ids = [int(x) for x in sys.argv[3].split(",")]
    pt = tuple(map(float, sys.argv[4].split(","))) if len(sys.argv) > 4 else None
    run(person, anchor, ids, pt)

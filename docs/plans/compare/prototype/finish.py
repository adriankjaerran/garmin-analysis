"""prototype: common finish line (anchor's last moving fix), first forward crossing after the start once >= 80 % of the anchor's distance is covered"""
import os, sys, math, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, "/Users/adrian/Code/garmin-analysis/scripts")
import start_align as sa, compare as cmp
person, anchor = sys.argv[1], int(sys.argv[2]); ids = [int(x) for x in sys.argv[3].split(",")]
st = cmp.store(person)
SA = st.session(anchor); GA = SA["G"]
A, res = sa.run(person, anchor, ids)
mov = ~SA["paused"] & GA["gps"]
tE = float(len(mov) - 1 - np.argmax(mov[::-1]))
PE = (float(GA["lat"][int(tE)]), float(GA["lon"][int(tE)]))
gA = sa.geo_d(GA)
tb = np.interp(gA[int(tE)] - np.array([50, 40, 30, 20, 10.0]), gA, np.arange(GA["T"] + 1))
X, Y = sa.pos_at(GA, tb, PE)
h = -np.array([X.mean(), Y.mean()]); h /= np.linalg.norm(h)
LA = gA[int(tE)] - gA[int(A["tA"])]
print(f"finish {PE[0]:.5f},{PE[1]:.5f} anchor start->finish {LA:.0f} m, {tE - A['tA']:.0f} s")
for i in ids:
    r = res[i]
    if not r.get("ok"):
        continue
    S = st.session(i); G = S["G"]; x, y = sa.xy(G, PE); u = x * h[0] + y * h[1]; w = -x * h[1] + y * h[0]
    g = sa.geo_d(G); t0 = r["t_s"]; g0 = np.interp(t0, np.arange(G["T"] + 1), g)
    found = None
    for k in np.nonzero((u[:-1] < 0) & (u[1:] >= 0) & G["gps"][:-1] & G["gps"][1:])[0]:
        if k < t0 or g[k] - g0 < 0.8 * LA or abs(w[k]) > sa.R_LINE:
            continue
        f = -u[k] / (u[k + 1] - u[k]); found = k + f; break
    how = "line"
    if found is None:   # stops just before the line (point-to-point finish): closest approach of the first visit within R
        rr = np.hypot(x, y); ok = (np.arange(len(rr)) >= t0) & (g - g0 >= 0.8 * LA) & (rr <= sa.R_LINE) & G["gps"]
        if ok.any():
            a = int(np.argmax(ok)); b = a
            while b + 1 < len(rr) and rr[b + 1] <= sa.R_LINE: b += 1
            found = float(a + np.argmin(rr[a:b + 1])); how = f"arrival ({rr[int(found)]:.0f} m short)"
    if found is None:
        dm = float(np.min(np.hypot(x, y)[int(t0):])) if int(t0) < len(x) else None
        print(f"  {i} no finish crossing (closest {dm:.0f} m), session ends {G['T'] - t0:.0f} s after its start"); continue
    tail = G["T"] - found
    print(f"  {i} start {t0:.0f} s, finish {found:.1f} s -> {found - t0:.0f} s, {g[int(found)] - g0:.0f} m GPS, tail trimmed {tail:.0f} s, {how}")

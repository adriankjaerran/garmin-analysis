"""validate start-line times against the route projection: time each session first reaches the anchor's route position"""
import os, sys, json, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, "/Users/adrian/Code/garmin-analysis/scripts")
import start_align as sa, compare as cmp
person, rid, anchor = sys.argv[1], sys.argv[2], int(sys.argv[3])
pt = tuple(map(float, sys.argv[4].split(","))) if len(sys.argv) > 4 else None
st = cmp.store(person); ctx = st.route(rid)
ids = ctx["R"]["members"]
A, res = sa.run(person, anchor, ids, pt)
tpA, _, _ = st.progress(anchor, ctx)
pA = float(np.interp(A["tA"], np.arange(len(tpA)), tpA))
print("anchor route position", round(pA))
errs = []
for i in ids:
    tp, on, d = st.progress(i, ctx)
    tp = np.maximum.accumulate(tp)
    k = np.nonzero(tp >= pA)[0]
    tr = int(k[0]) if len(k) else None
    r = res[i]
    e = (r["t_s"] - tr) if (r.get("ok") and tr is not None) else None
    if e is not None and r["kind"] != "join": errs.append(abs(e))
    S = st.session(i); G = S["G"]
    mdiff = None
    if r.get("ok") and tr is not None:
        mdiff = round(float(np.interp(r["t_s"], np.arange(G["T"]+1), S["mt"]) - S["mt"][tr]), 1)
        x, y = sa.xy(G, A["P"]); tc = r.get("t_cross", r.get("t_join", r["t_s"]))
        dP = round(float(np.hypot(np.interp(tc, np.arange(G["T"]+1), x), np.interp(tc, np.arange(G["T"]+1), y))))
    else: dP = None
    print(f"  {i} mov_diff={mdiff} dist_to_P_at_cross={dP} zone_pause={r.get('zone_pause_s')}", end="")
    print(f"  {i} dir={d} proj_t={tr} found={r.get('t_s')} kind={r.get('kind')} diff={None if e is None else round(e,1)} {r.get('why','')}")
if errs: print("abs diff s: median", np.median(errs), "max", max(errs), "n", len(errs))

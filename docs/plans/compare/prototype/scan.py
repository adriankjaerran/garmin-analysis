import sys, math, numpy as np
sys.path.insert(0, "/Users/adrian/Code/garmin-analysis/scripts")
import compare as cmp
st = cmp.store(sys.argv[1]); g = st.groups()
for rid, R in g["routes"].items():
    if len(R["members"]) < 3: continue
    try: m = st.session(R["medoid"])["G"]
    except Exception: continue
    if m.get("gps") is None: continue
    i = int(np.argmax(m["gps"])); P = (m["lat"][i], m["lon"][i]); far = []
    for j in R["members"]:
        try: G = st.session(j)["G"]
        except Exception: continue
        if G.get("gps") is None or not G["gps"].any(): continue
        k = int(np.argmax(G["gps"])); x, y = __import__("session_groups").local_xy(G["lat"][k], G["lon"][k], P)
        d = math.hypot(x, y)
        if d > 100: far.append((j, round(d)))
    print(rid, R["name"], R["shape"], len(R["members"]), "medoid", R["medoid"], "far>100", far[:6])

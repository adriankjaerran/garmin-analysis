"""G3 diagnostics: device offsets / noise, correction coefficients, and how much the HR signal adds to production.
Writes data/_research/shape/g3/diag_*.parquet and prints the summary tables used in REPORT.md."""
import sys, warnings
from pathlib import Path
warnings.filterwarnings("ignore")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[1]))
import numpy as np, pandas as pd
import harness as H, model as M, common as CM

PERS = ("adrian", "rise", "patrik")
OUT = CM.OUT / "g3"


def device_table():
    rows = []
    for p in PERS:
        st = M.hr_state(p, 739924, M._key(M._cfg(cal_days=None)))      # all history up to 2026-11-04
        if st is None:
            print(p, "no state"); continue
        R = st["runs"]
        d, g, b = st["corr"]
        for dev, n in R.dev.value_counts().items():
            r = R[(R.dev == dev) & R.theta.notna()]
            res = r.x - r.theta
            rows.append(dict(person=p, device=dev, runs=n, offset_vs_theta=round(st["offs"][dev], 2),
                             raw_median=round(float(res.median()), 2) if len(r) else None,
                             run_sd_robust=round(st["sds"][dev], 2),
                             corr_x_theta=round(float(np.corrcoef(r.x, r.theta)[0, 1]), 2) if len(r) > 5 else None,
                             drift_ml_per_min=round(d, 3), grade_ml_per_pct=round(g, 2), b=round(b, 1)))
    T = pd.DataFrame(rows)
    print(T.to_string(index=False))
    return T


def rise_interleaved():
    """rise: strap vs wrist runs within +-45 days of each other (same fitness): paired difference of x."""
    st = M.hr_state("rise", 739924, M._key(M._cfg(cal_days=None)))
    R = st["runs"]
    s, w = R[R.dev.str.endswith("strap")], R[R.dev.str.endswith("wrist")]
    diffs = []
    for r in s.itertuples():
        o = w[(w.day - r.day).abs() <= 45]
        if len(o) >= 2:
            diffs.append(r.x - o.x.median())
    diffs = np.array(diffs)
    print(f"rise strap - wrist (same +-45 d): n={len(diffs)} median {np.median(diffs):+.2f} mean {diffs.mean():+.2f} "
          f"se {diffs.std(ddof=1)/np.sqrt(len(diffs)):.2f}")
    return diffs


def adrian_switch():
    """adrian: FR935 -> 4315 (2026-02-18): x - theta in the 240 d before vs after the switch."""
    st = M.hr_state("adrian", 739924, M._key(M._cfg(cal_days=None)))
    R = st["runs"]; R = R[R.theta.notna()]
    sw = pd.Timestamp("2026-02-18").toordinal()
    a = R[(R.day < sw) & (R.day >= sw - 240) & (R.dev == "fr935/wrist")]
    b = R[(R.day >= sw) & (R.dev == "4315/wrist")]
    ra, rb = a.x - a.theta, b.x - b.theta
    print(f"adrian x - theta: FR935 last 240 d n={len(a)} median {ra.median():+.2f} sd {ra.std():.2f}; "
          f"4315 n={len(b)} median {rb.median():+.2f} sd {rb.std():.2f}; jump {rb.median()-ra.median():+.2f}")


def informativeness():
    """Across targets: does (hr_alone - production) predict (actual - production)? slope = optimal HR weight."""
    rows = []
    for name, fn, kw in [("alone", M.hr_alone, {}), ("alone_q0.1", M.hr_alone, {"q": 0.1}),
                         ("alone_targets", M.hr_alone, {"cal": "targets"})]:
        a = H.evaluate(M.make(fn, name, **kw), persons=("adrian", "rise"), name=name)["preds"]
        p = H.evaluate(H.production_curve, persons=("adrian", "rise"))["preds"]
        x = a.assign(prod=p.pred.values).dropna(subset=["pred", "prod"])
        for (tier, per), g in list(x.groupby(["tier", "person"])) + [(("all", "all"), x)]:
            u = (g.pred - g["prod"]).to_numpy(); v = (g.actual - g["prod"]).to_numpy()
            if len(g) < 4:
                continue
            sl = float(np.sum(u * v) / np.sum(u * u))
            se = float(np.sqrt(np.sum((v - sl * u) ** 2) / (len(u) - 1) / np.sum(u * u)))
            rows.append(dict(variant=name, tier=tier, person=per, n=len(g), corr=round(float(np.corrcoef(u, v)[0, 1]), 2),
                             slope=round(sl, 2), slope_se=round(se, 2), sd_hr_minus_prod=round(float(u.std()), 2)))
    T = pd.DataFrame(rows)
    print(T.to_string(index=False))
    return T


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True, parents=True)
    device_table().to_parquet(OUT / "diag_devices.parquet", index=False)
    rise_interleaved(); adrian_switch()
    informativeness().to_parquet(OUT / "diag_informativeness.parquet", index=False)

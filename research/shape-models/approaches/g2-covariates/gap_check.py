"""G2: which GAP correction fits these runners? One-step-ahead (time-forward) residuals of non-flat observations under
observation scales raw / minetti-half (production) / Strava, with and without a learned kappa * g term, plus kappa
from the full-data fits.

    /tmp/shape-venv/bin/python research/shape-models/approaches/g2-covariates/gap_check.py
"""
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1])); sys.path.insert(0, str(HERE))
from model import Config, Fit, prepare  # noqa: E402

pd.set_option("display.width", 200)
rows = []
for sub in (False, True):
    for gap in ("raw", "min", "strava"):
        for kap in (False, True):
            cfg = replace(Config(), gap=gap, kappa=kap, include_sub=sub, robust="sym")
            O = prepare(cfg)
            f = Fit(cfg, O)
            f._filter()
            r, s = f.innov[:, 0], f.innov[:, 1]
            hilly = (~O.flat.to_numpy()) & (np.abs(O.g_min.to_numpy()) > 0.5) & (O.day.to_numpy() > O.day.min() + 365)
            z = r[hilly] / s[hilly]
            # robust score: median |resid| and Huber-clipped NLL
            nll = np.mean(0.5 * np.minimum(z ** 2, 9) + np.log(s[hilly]))
            ff = Fit(cfg, O).run()
            k = ff.effects(); kk = k[(k.effect == "kappa") & (k.who == "pooled")]
            rows.append({"sub": sub, "scale": gap, "kappa_free": kap, "n_hilly": int(hilly.sum()),
                         "medAE": float(np.median(np.abs(r[hilly]))), "MAE": float(np.mean(np.abs(r[hilly]))),
                         "nll_clip": float(nll),
                         "kappa": float(kk["mean"].iloc[0]) if len(kk) else np.nan,
                         "kappa_sd": float(kk["sd"].iloc[0]) if len(kk) else np.nan})
            # residual vs g (no kappa, full-data smoothed fit), binned
            if not kap:
                ff2 = Fit(cfg, O); ff2.cfg = cfg; ff2 = ff2.run()
D = pd.DataFrame(rows)
print(D.to_string(index=False, float_format="{:.3f}".format))

# binned residuals (no kappa, sym, online one-step) vs minetti-half shift, for the three scales
cfg = replace(Config(), kappa=False, include_sub=True, robust="sym")
out = []
for gap in ("raw", "min", "strava"):
    c = replace(cfg, gap=gap); O = prepare(c); f = Fit(c, O); f._filter()
    O = O.assign(res=f.innov[:, 0])
    O = O[(~O.flat) & (O.day > O.day.min() + 365)]
    O["gbin"] = pd.cut(O.g_min, [-5, -0.5, 0.5, 1.5, 3, 20])
    out.append(O.groupby("gbin", observed=True).res.agg(["size", "median"]).add_prefix(gap + "_"))
print(pd.concat(out, axis=1).to_string(float_format="{:.2f}".format))

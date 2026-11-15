"""G1 recommended predict_fn: production 2-state distance curve (theta + slope * log2(km/5)) with a more mobile
slope (AR(1) q_slope 0.04 VDOT²/doubling²/day, stationary sd 2.0; production: 0.005 / 1.5), chosen by history-only
one-step forecast log-lik. Everything else as production (q_theta 0.05, asymmetric IRLS weights, extrapolation).

    from model import predict            # predict(person, hist, target) -> {"vdot", "sd", "sd_state"}
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[1]))
import g1  # noqa: E402

CFG = g1.Cfg(q_slope=0.04, slope_sd=2.0)
predict = g1.make_predict(CFG, "g1_mobile_slope")
predict_production = g1.make_predict(g1.Cfg(), "g1_curve2_production")

"""G8 predict functions for the ensemble (G9).

    import sys; sys.path.insert(0, "research/shape-models/approaches/g8-distance-curve")
    from model import predict, MODELS

`predict` = production's 2-state filter (theta + slope * log2(km/5), slope OU) on G8's re-derived evidence (identical
to production at these settings) with a faster-moving slope (q_slope 0.02 instead of 0.005): race MAE adrian + rise
1.31 vs 1.34 (noise-level; q_s was chosen looking at the races, so treat it as 1 tuned knob). Other members:
different curve families with a time-varying level and a slowly varying (discounted, half-life 180 d) shape.
All return {"vdot", "sd"}; they abstain (None) before the first race / hard evidence. Needs
data/_research/shape/g8/session_feats.parquet (features.py).
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1])); sys.path.insert(0, str(HERE))
import curves as K  # noqa: E402

predict = K.make_prodk(q_slope=0.02)

MODELS = {
    "g8_prodK_qs0.02": predict,
    "g8_prod_hl180": K.make_predict("prod", half_life=180),
    "g8_prod+0.5_hl180": K.make_predict("prod+0.5", half_life=180),
    "g8_riegel_hl180": K.make_predict("riegel", half_life=180),
    "g8_pt_hl180": K.make_predict("pt", half_life=180),
}

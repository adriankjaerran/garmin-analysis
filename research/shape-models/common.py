"""Shared helpers for the shape-model research: paths, Daniels VDOT, grade-adjusted pace, dataset loading.

Interpreter: /tmp/shape-venv/bin/python (numpy, pandas, scipy, sklearn, lightgbm, xgboost, statsmodels, pyarrow,
matplotlib, fitparse). Run from the repo root, e.g.
    /tmp/shape-venv/bin/python research/shape-models/harness.py
The project venv (`uv run python`) has numpy only (no pandas), so it can't read the parquet files.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
OUT = DATA / "_research" / "shape"          # datasets (health data: stays under data/, gitignored)
SCRIPTS = ROOT / "scripts"
PERSONS = ("adrian", "rise", "patrik")
STD_DIST = {"400m": 400.0, "1k": 1000.0, "1mi": 1609.344, "3k": 3000.0, "5k": 5000.0, "10k": 10000.0,
            "HM": 21097.5}
NOMINAL_KM = {"5k": 5.0, "10k": 10.0, "half": 21.0975, "marathon": 42.195}


# ------------------------------------------------------------------ Daniels VDOT
def daniels_vo2(v_ms):
    """O2 cost (ml/kg/min) of running at v m/s on the flat (Daniels & Gilbert)."""
    v = np.asarray(v_ms, float) * 60
    return -4.60 + 0.182258 * v + 0.000104 * v * v


def daniels_pct(t_s):
    """Share of VO2max sustainable for an all-out effort of t seconds (Daniels & Gilbert)."""
    t = np.asarray(t_s, float) / 60
    return 0.8 + 0.1894393 * np.exp(-0.012778 * t) + 0.2989558 * np.exp(-0.1932605 * t)


def vdot(dist_m, time_s):
    """Daniels VDOT of covering dist_m in time_s (vectorised)."""
    d = np.asarray(dist_m, float); t = np.asarray(time_s, float)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = daniels_vo2(d / t) / daniels_pct(t)
    return float(out) if out.ndim == 0 else out


def vdot_from_speed(v_ms, t_s):
    return daniels_vo2(v_ms) / daniels_pct(t_s)


def speed_for(vd, t_s):
    """Speed (m/s) a runner of VDOT `vd` holds for t seconds."""
    target = np.asarray(vd, float) * daniels_pct(t_s)
    a, b, c = 0.000104, 0.182258, -4.60 - target
    return (-b + np.sqrt(b * b - 4 * a * c)) / (2 * a) / 60


def race_time(vd, dist_m, iters=50):
    """Equivalent all-out time (s) for dist_m at VDOT vd (vectorised bisection)."""
    vd = np.asarray(vd, float); d = np.asarray(dist_m, float)
    vd, d = np.broadcast_arrays(vd, d)
    lo, hi = d / 12.0, d / 1.0
    for _ in range(iters):
        m = 0.5 * (lo + hi)
        fast = speed_for(vd, m) * m > d
        hi = np.where(fast, m, hi); lo = np.where(fast, lo, m)
    out = 0.5 * (lo + hi)
    return float(out) if out.ndim == 0 else out


def pct_time_error(pred_vdot, true_vdot, dist_m):
    """% time error of predicting `pred_vdot` when the effort was `true_vdot` (+ = predicted too slow)."""
    tp = race_time(pred_vdot, dist_m); tt = race_time(true_vdot, dist_m)
    return 100 * (np.asarray(tp) - np.asarray(tt)) / np.asarray(tt)


def riegel(t1, d1, d2, k=1.06):
    return t1 * (d2 / d1) ** k


# ------------------------------------------------------------------ grade-adjusted pace
def minetti_cost(i):
    """Minetti et al. 2002 energy cost of running (J/kg/m) at grade i (fraction, rise/run)."""
    i = np.asarray(i, float)
    return 155.4 * i**5 - 30.4 * i**4 - 43.3 * i**3 + 46.3 * i**2 + 19.5 * i + 3.6


def gap_factor_minetti(grade, downhill_share=0.5, clip=0.20):
    """Equivalent-flat speed multiplier (production rule): Minetti uphill, `downhill_share` of Minetti's downhill
    saving (Minetti overstates it at running speed; production uses 0.5), grade clipped to +-clip."""
    g = np.clip(np.asarray(grade, float), -clip, clip)
    f = minetti_cost(g) / 3.6
    return np.where(g < 0, 1 + downhill_share * (f - 1), f)


def gap_factor_strava(grade, clip=0.30):
    """Strava-style empirical GAP (2017 HR-based model, quadratic approximation of the published curve):
    relative cost = 1 + 0.0298 g + 0.0017 g^2 with g in percent. Minimum cost near -9 % (~0.87), steep descents
    cost more again, unlike Minetti with full downhill saving."""
    g = np.clip(np.asarray(grade, float), -clip, clip) * 100
    return 1 + 0.0298 * g + 0.0017 * g * g


def gap_speed(v_ms, grade, model="minetti_half"):
    if model == "minetti_half":
        return np.asarray(v_ms) * gap_factor_minetti(grade, 0.5)
    if model == "minetti":
        return np.asarray(v_ms) * gap_factor_minetti(grade, 1.0)
    if model == "strava":
        return np.asarray(v_ms) * gap_factor_strava(grade)
    if model == "none":
        return np.asarray(v_ms, float)
    raise ValueError(model)


# ------------------------------------------------------------------ misc
def band_of(km):
    """Distance band used in the harness: short (<= 6 km), 10k (6-15 km), HM+ (> 15 km)."""
    km = float(km)
    return "short" if km <= 6.0 else ("10k" if km <= 15.0 else "HM+")


def fmt_time(s):
    if s is None or (isinstance(s, float) and math.isnan(s)):
        return "-"
    s = int(round(s)); h, r = divmod(s, 3600); m, sec = divmod(r, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def load(name="activities", persons=PERSONS):
    """Load a dataset table (activities | segments | targets) as one pandas DataFrame."""
    import pandas as pd
    return pd.read_parquet(OUT / f"{name}.parquet").query("person in @persons").reset_index(drop=True)


def import_production():
    """Import scripts/fitness_model.py (read-only use) for the production-model baseline."""
    import sys
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    import fitness_model as fm  # noqa: E402
    return fm

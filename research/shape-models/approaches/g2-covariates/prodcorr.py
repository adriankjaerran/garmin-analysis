"""G2 -> production curve: correct production's own per-session evidence with the G2 effects estimated time-forward
(the G2 pooled model's filtered state on day d-1: all persons, observations of days < d), then run production's
smooth_curve / curve_predict unchanged.

Corrections (each switchable):
  surf    road interval sessions - beta_int_road, Bislett indoor - beta_int_indoor (to the track-interval scale)
  gap     residual GAP: minus kappa * g (g = minetti-half shift of the session: reps for intervals, whole run else)
  heat    minus beta_heat * max(0, wrist_temp_max - 24)
  course  races are observed raw: plus (1 - kappa) * g_race (the real part of the course's GAP shift) so they enter
          as flat-equivalent; predictions subtract it again from the target course's expected shift
Predictions are mapped back to each target's scale (interval targets get their surface / heat effect added back,
races their heat / course effect).

    /tmp/shape-venv/bin/python research/shape-models/approaches/g2-covariates/prodcorr.py
"""
from __future__ import annotations

import functools
import math
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1])); sys.path.insert(0, str(HERE))
import common as C  # noqa: E402
import harness as Hh  # noqa: E402
from model import Config, Predictor, _ghat  # noqa: E402
import obs as OBS  # noqa: E402

fm = C.import_production()


@functools.lru_cache(maxsize=1)
def _session_g():
    """Per activity: minetti-half / Strava GAP shift (VDOT) of the scored part (reps for intervals, whole run else)."""
    A, S, T = Hh.load_data()
    reps = S[(S.seg_kind == "rep") & S.speed.notna() & (S.dur_s > 20)]
    vs = C.vdot_from_speed
    rg = pd.Series(vs(reps.gap_speed, reps.dur_s) - vs(reps.speed, reps.dur_s), index=reps.index).groupby(reps.activity_id).mean()
    v = A.dist_km * 1000 / A.moving_s
    whole = pd.Series(vs(v * A.gap_ratio, A.moving_s) - vs(v, A.moving_s), index=A.activity_id.values)
    flat = (A.flat_course.fillna(False).astype(bool) | A.surface.isin(["track", "indoor_track", "treadmill"])).to_numpy()
    g = {}
    for aid, wk, fl in zip(A.activity_id, A.workout, flat):
        if fl:
            g[aid] = 0.0
        elif wk == "intervals" and aid in rg.index:
            g[aid] = float(rg[aid])
        else:
            x = whole.get(aid, np.nan)
            g[aid] = float(x) if x == x else 0.0
    return g


class ProdCorr:
    def __init__(self, corr=("surf", "gap", "heat", "course"), g2cfg=Config(), race_offset=False, name=None):
        self.corr = set(corr)
        self.g2 = Predictor(g2cfg)
        self.race_offset = race_offset
        self.__name__ = name or "prodcorr[" + ",".join(sorted(self.corr)) + "]"
        self._cache = {}

    def beta(self, day):
        st = self.g2.state_at(day)
        if st is None:
            return {}, None
        m, P, O_hist = st
        L = self.g2.L
        b = {k: float(m[L.ix[f"b:{k}"]]) for k in L.effects}
        return b, O_hist

    def per_person_beta(self, day, person):
        st = self.g2.state_at(day)
        m = st[0]; L = self.g2.L
        out = {}
        for k in L.effects:
            v = m[L.ix[f"b:{k}"]]
            if k in L.pooled:
                v += m[L.ix[f"u:{person}:{k}"]]
            out[k] = float(v)
        return out

    def state(self, person, day):
        key = (person, day)
        if key in self._cache:
            return self._cache[key]
        h = Hh.history(person, day)
        a = h.acts
        a = a[a.prod_discipline.isin(["interval", "long"]) & a.prod_kind.isin(["hard", "race", "bound"]) &
              a.prod_km.notna() & a.prod_score.notna()]
        if a.empty:
            self._cache[key] = None
            return None
        b = self.per_person_beta(day, person) if self.g2.state_at(day) is not None else {}
        kap = b.get("kappa", 0.0); heat = b.get("heat", 0.0)
        g = _session_g()
        obs = []
        for r in a.itertuples():
            y = float(r.prod_score)
            if "surf" in self.corr and r.prod_discipline == "interval" and r.prod_kind == "hard":
                if r.surface == "road":
                    y -= b.get("int_road", 0.0)
                elif r.surface == "indoor_track":
                    y -= b.get("int_indoor", 0.0)
            gg = g.get(r.activity_id, 0.0)
            if r.prod_kind == "race":
                if "course" in self.corr:
                    y += (1 - kap) * gg
            elif "gap" in self.corr:
                y -= kap * gg
            t = r.wrist_temp_max
            if "heat" in self.corr and t == t and t is not None:
                y -= heat * max(0.0, float(t) - 24.0)
            obs.append((int(r.day), y, float(r.prod_sigma), r.prod_kind, fm.curve_x(float(r.prod_km))))
        d0 = min(o[0] for o in obs)
        days, M, Cv, w, _ = fm.smooth_curve(obs, d0, day - 1)
        obs_s = sorted(obs, key=lambda o: o[0])
        ev = [2 ** o[4] * fm.CURVE["ref_km"] for o, wt in zip(obs_s, w) if o[3] in ("race", "hard") and wt > 0.5]
        out = (M[-1], Cv[-1], (min(ev), max(ev)) if ev else None, b)
        self._cache[key] = out
        return out

    def __call__(self, person, hist, target):
        st = self.state(person, int(hist.day))
        if st is None:
            return None
        m, cv, ekm, b = st
        p = fm.curve_predict(m[0], m[1], cv[0, 0], cv[1, 1], cv[0, 1], float(target["km"]), ekm)
        v, sd = float(p["vdot"]), float(p["sd"])
        kap = b.get("kappa", 0.0)
        t = target.get("wrist_temp_max")
        if "heat" in self.corr and t is not None and t == t:
            v += b.get("heat", 0.0) * max(0.0, float(t) - 24.0)
        tier = target["tier"]
        if tier == "interval" and "surf" in self.corr:
            v += b.get("int_road", 0.0) if target["surface"] == "road" else (
                b.get("int_indoor", 0.0) if target["surface"] == "indoor_track" else 0.0)
        if tier == "race":
            if self.race_offset:
                v += b.get("race", 0.0)
            if "course" in self.corr:
                flat = bool(target.get("flat_course")) or target["surface"] in ("track", "indoor_track")
                c = target.get("climb_per_km")
                if not flat and c is not None and c == c:
                    gh = _ghat(self.g2.O[self.g2.O.day < hist.day], "min")["g_min"] * min(float(c), 30.0)
                    v -= (1 - kap) * gh
        return {"vdot": v, "sd": sd}


VARIANTS = {
    "prodcorr_none": dict(corr=()),
    "prodcorr_all": dict(corr=("surf", "gap", "heat", "course")),
    "prodcorr_surf": dict(corr=("surf",)),
    "prodcorr_gap": dict(corr=("gap",)),
    "prodcorr_heat": dict(corr=("heat",)),
    "prodcorr_course": dict(corr=("course",)),
    "prodcorr_all_raceoff": dict(corr=("surf", "gap", "heat", "course"), race_offset=True),
    "prodcorr_all_sym": dict(corr=("surf", "gap", "heat", "course"), g2cfg=replace(Config(), robust="sym")),
    "prodcorr_all_strava": dict(corr=("surf", "gap", "heat", "course"), g2cfg=replace(Config(), gap="strava")),
    "prodcorr_surf_heat": dict(corr=("surf", "heat")),
}

if __name__ == "__main__":
    from run import row
    names = sys.argv[1:] or list(VARIANTS)
    rows = []
    for nm in names:
        r = Hh.evaluate(ProdCorr(name=nm, **VARIANTS[nm]), name=nm)
        r["preds"].assign(model=nm).to_parquet(C.OUT / "g2" / f"preds_{nm}.parquet", index=False)
        rows.append(row(r))
        print(pd.DataFrame(rows[-1:]).to_string(index=False, header=len(rows) == 1, float_format="{:.2f}".format),
              flush=True)

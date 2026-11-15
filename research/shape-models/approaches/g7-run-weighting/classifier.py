"""G7: weak labels + time-forward p(maximal | run features) classifier.

Weak label of a candidate (features.py) = "on the envelope": r = y - ref(day, km) >= -LABEL_GAP, where ref is the
production distance curve *smoothed* (RTS, two-sided) on the production evidence of the training cut-off's history
(data < checkpoint day). Races are always maximal and are not classified.

Time-forward: checkpoints every STEP days; the model of checkpoint c is fitted on all persons' candidates with
day < c (labels from each person's own history < c) and is used for targets with day >= c until the next one.

Classifier quality is measured on the candidates of the following period, with labels from the reference smoothed on
all data (evaluation only; never used to fit).

    /tmp/shape-venv/bin/python research/shape-models/approaches/g7-run-weighting/classifier.py
"""
from __future__ import annotations

import functools
import math
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parents[1]))
import common as C  # noqa: E402
import harness as H  # noqa: E402
import features as F  # noqa: E402

warnings.filterwarnings("ignore")
LABEL_GAP = 1.5
STEP = 61
MIN_TRAIN = 80
FEATSETS = ("full", "nodelta", "hr_only", "delta_only")


@functools.lru_cache(maxsize=None)
def ref_curve(person, cut):
    """Smoothed production curve (days, mean (n,2)) from production evidence with day < cut."""
    fm = C.import_production()
    a = H.history(person, cut).acts
    a = a[a.prod_discipline.isin(["interval", "long"]) & a.prod_kind.isin(["hard", "race", "bound"]) &
          a.prod_km.notna() & a.prod_score.notna()]
    if len(a) < 3:
        return None
    obs = [(int(r.day), float(r.prod_score), float(r.prod_sigma), r.prod_kind, fm.curve_x(float(r.prod_km)))
           for r in a.itertuples()]
    d0 = min(o[0] for o in obs)
    days, M, _, _, _ = fm.smooth_curve(obs, d0, cut - 1)
    return days, M


def residual_vs_ref(D, cut):
    """r = y - ref(day, x) for candidates with day < cut (NaN where no ref)."""
    r = np.full(len(D), np.nan)
    for p in D.person.unique():
        rc = ref_curve(p, int(cut))
        if rc is None:
            continue
        days, M = rc
        idx = np.where((D.person == p) & (D.day < cut) & (D.day >= days[0]))[0]
        k = D.day.to_numpy()[idx] - days[0]
        r[idx] = D.y.to_numpy()[idx] - (M[k, 0] + D.x.to_numpy()[idx] * M[k, 1])
    return r


def select(X, fs):
    if fs == "full":
        return X
    if fs == "nodelta":
        return X.drop(columns=[c for c in ("delta", "delta_z") if c in X])
    if fs == "hr_only":
        return X[["hrr_eff", "hrr_eff_trusted", "trust", "hrr_mean", "hr_p95_rel", "is_int"]]
    if fs == "delta_only":
        return X[["delta", "delta_z", "is_int"]]
    raise ValueError(fs)


def make_model(kind):
    if kind == "logit":
        from sklearn.impute import SimpleImputer
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
        return make_pipeline(SimpleImputer(strategy="median", add_indicator=False), StandardScaler(),
                             LogisticRegression(C=0.3, max_iter=2000))
    if kind == "lgbm":
        import lightgbm as lgb
        return lgb.LGBMClassifier(n_estimators=150, learning_rate=0.03, num_leaves=7, min_child_samples=15,
                                  subsample=0.8, subsample_freq=1, colsample_bytree=0.8, verbose=-1, n_jobs=1)
    raise ValueError(kind)


def make_bias_model():
    """E[r | features, maximal]: shortfall of a maximal-labelled effort vs the envelope (ridge, no delta)."""
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    return make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), Ridge(alpha=10.0))


def checkpoints(D):
    nonrace = D[D.src != "race"]
    days = np.sort(nonrace.day.unique())
    first = int(days[min(MIN_TRAIN, len(days) - 1)])
    return list(range(first, int(D.day.max()) + 2, STEP))


@functools.lru_cache(maxsize=1)
def load():
    D = pd.read_parquet(F.CAND) if F.CAND.exists() else F.build_candidates()
    return D


@functools.lru_cache(maxsize=None)
def fitted(label_gap=LABEL_GAP, model="logit", fs="full"):
    """{checkpoint: (P (n candidates,) p(maximal) for all candidates, B bias E[r|max] for all candidates)}"""
    D = load()
    X = F.feature_matrix(D)
    out = {}
    for c in checkpoints(D):
        r = residual_vs_ref(D, c)
        is_race = (D.src == "race").to_numpy()
        tr = (D.day < c).to_numpy() & (~np.isnan(r) | is_race)
        if fs == "full":
            tr &= X["delta"].notna().to_numpy()
        yl = ((r[tr] >= -label_gap) | is_race[tr]).astype(int)      # weak labels: races always maximal
        if tr.sum() < MIN_TRAIN or yl.min() == yl.max():
            continue
        Xs = select(X, fs)
        if model == "logit_split":     # separate logits for interval sessions and continuous efforts (+ races)
            Pp = np.zeros(len(D))
            gi = (D.src == "int").to_numpy()
            for g in (gi, ~gi):
                m = make_model("logit").fit(Xs[tr & g], ((r[tr & g] >= -label_gap) | is_race[tr & g]).astype(int))
                Pp[g] = m.predict_proba(Xs[g])[:, 1]
        else:
            m = make_model(model).fit(Xs[tr], yl)
            Pp = m.predict_proba(Xs)[:, 1]
        pos = tr.copy(); pos[tr] = yl == 1
        pos &= ~np.isnan(r)
        bm = make_bias_model().fit(select(X, "nodelta")[pos], np.clip(r[pos], -LABEL_GAP, 4))
        B = bm.predict(select(X, "nodelta"))
        # centre on the interval positives: the truncation at -LABEL_GAP lifts E[r | positive] by ~+0.5 for every
        # kind; only the shortfall *relative to* interval sessions (production's scale) is a real offset
        B = B - B[pos & (D.src == "int").to_numpy()].mean()
        out[c] = (Pp, B)
    return out


def p_at(day, **kw):
    """(P, B) of the latest checkpoint <= day, or None."""
    fit = fitted(**kw)
    cs = [c for c in fit if c <= day]
    return fit[cs[-1]] if cs else None


# ------------------------------------------------------------------ rule baselines
def p_prod_rules(D):
    """Production inclusion: interval hard sessions and tempos are 'hard' (p 1), everything else a bound (p 0)."""
    return ((D.src == "int") | (D.workout == "tempo")).astype(float).to_numpy()


def p_hr_intent(D, thr=0.85, sd=0.04):
    """Production's HR-intent p_max = trust * Phi((hrr - thr)/sd) + (1 - trust); 0.5 without HR."""
    h = D.hrr_eff.where(D.hr_ok)
    z = (h - thr) / sd
    phi = 0.5 * (1 + np.vectorize(math.erf)(z.fillna(0) / math.sqrt(2)))
    p = D.trust * phi + (1 - D.trust)
    return np.where(h.isna(), 0.5, p)


# ------------------------------------------------------------------ evaluation
def evaluate_classifiers():
    from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
    D = load()
    end = int(D.day.max()) + 1
    r_final = residual_vs_ref(D, end)
    y_final = (r_final >= -LABEL_GAP).astype(float)
    cs = checkpoints(D)
    ev = (D.src != "race").to_numpy() & ~np.isnan(r_final) & (D.day >= cs[0]).to_numpy()
    rows, preds = [], {}
    rules = {"prod_rules (int+tempo=1)": p_prod_rules(D), "hr_intent (prod formula)": p_hr_intent(D)}
    for name, p in rules.items():
        preds[name] = p
    for model in ("logit", "logit_split", "lgbm"):
        for fs in FEATSETS:
            fit = fitted(model=model, fs=fs)
            p = np.full(len(D), np.nan)
            for i in np.where(ev)[0]:
                c = [k for k in fit if k <= D.day[i]]
                if c:
                    p[i] = fit[c[-1]][0][i]
            preds[f"{model}/{fs}"] = p
    for name, p in preds.items():
        for who, msk in (("all", ev), ("adrian", ev & (D.person == "adrian").to_numpy()),
                         ("rise", ev & (D.person == "rise").to_numpy()),
                         ("cont only", ev & (D.src == "cont").to_numpy()),
                         ("int only", ev & (D.src == "int").to_numpy())):
            m = msk & ~np.isnan(p)
            yy, pp = y_final[m], np.clip(p[m], 1e-3, 1 - 1e-3)
            rows.append({"scorer": name, "subset": who, "n": int(m.sum()), "pos_rate": yy.mean(),
                         "auc": roc_auc_score(yy, pp) if 0 < yy.mean() < 1 else np.nan,
                         "brier": brier_score_loss(yy, pp), "logloss": log_loss(yy, pp, labels=[0, 1])})
    R = pd.DataFrame(rows)
    D2 = D.assign(r_final=r_final, label_final=y_final, ev=ev)
    for k, v in preds.items():
        D2[f"p::{k}"] = v
    D2.to_parquet(F.G7 / "classifier_eval.parquet", index=False)
    return R, D2


if __name__ == "__main__":
    R, D2 = evaluate_classifiers()
    with pd.option_context("display.width", 200, "display.float_format", "{:.3f}".format, "display.max_rows", 200):
        print(R[R.subset == "all"].to_string(index=False))
        print(R[R.subset != "all"].pivot(index="scorer", columns="subset", values="auc").to_string())
        print(R[R.subset != "all"].pivot(index="scorer", columns="subset", values="brier").to_string())
        R.to_csv(F.G7 / "classifier_quality.csv", index=False)
        print(R[R.subset != "all"].pivot(index="scorer", columns="subset", values="n").iloc[:1].to_string())

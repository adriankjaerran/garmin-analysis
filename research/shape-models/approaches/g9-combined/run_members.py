"""Evaluate one round-1 member predict_fn through harness.evaluate and save its per-target predictions.

    /tmp/shape-venv/bin/python research/shape-models/approaches/g9-combined/run_members.py <member>
Members: production g1 g2 g3 g4 g5 g6 g7 g8. Output: data/_research/shape/g9/members/<member>.parquet
"""
import importlib.util
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SM = HERE.parents[1]
sys.path.insert(0, str(SM))
import common as C  # noqa: E402
import harness as H  # noqa: E402

OUT = C.OUT / "g9" / "members"
DIRS = {"g1": "g1-kalman", "g2": "g2-covariates", "g3": "g3-hr-efficiency", "g4": "g4-load-response",
        "g6": "g6-ml", "g7": "g7-run-weighting", "g8": "g8-distance-curve"}


def load_member(name):
    if name == "production":
        return H.production_curve
    if name == "g5":
        sys.path.insert(0, str(SM / "approaches" / "g5-envelope"))
        import g5
        from dataclasses import replace
        return g5.make_tobit(replace(g5.Cfg(), min_m=1600.0, tempo_shortfall=True), "g5_tobit_1mi_shortfall")
    d = SM / "approaches" / DIRS[name]
    sys.path.insert(0, str(d))
    spec = importlib.util.spec_from_file_location(f"{name}_model", d / "model.py")
    mod = importlib.util.module_from_spec(spec); sys.modules[spec.name] = mod; spec.loader.exec_module(mod)
    return mod.predict


if __name__ == "__main__":
    name = sys.argv[1]
    t0 = time.time()
    fn = load_member(name)
    r = H.evaluate(fn, persons=("adrian", "rise", "patrik"), name=name, verbose=True)
    OUT.mkdir(parents=True, exist_ok=True)
    r["preds"].assign(model=name).to_parquet(OUT / f"{name}.parquet", index=False)
    print(name, r["headline"], round(time.time() - t0, 1), "s")

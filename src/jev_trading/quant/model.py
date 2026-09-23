"""Persist/load fitted quant baselines (LightGBM booster + logistic pipeline)."""
from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np
import polars as pl


class QuantPredictor:
    """P(future_return_15 > cost) from the LightGBM baseline (logreg kept for audit)."""

    def __init__(self, lgbm, logreg, feature_cols: list[str], metrics: dict):
        self._lgbm = lgbm
        self.logreg = logreg
        self.feature_cols = feature_cols
        self.metrics = metrics

    def predict_up15(self, X: pl.DataFrame) -> pl.Series:
        mat = X.select(self.feature_cols).to_numpy()
        # Real probability inference. Use predict_proba when sklearn wrapper available;
        # Booster.predict() with binary objective also yields probabilities.
        if hasattr(self._lgbm, "predict_proba"):
            proba = np.asarray(self._lgbm.predict_proba(mat)[:, 1], dtype=float)
        else:
            proba = np.asarray(self._lgbm.predict(mat), dtype=float)
        return pl.Series("p_up15", proba)

    def predict_dn15(self, X: pl.DataFrame) -> pl.Series:
        # For binary classifier, down probability is complement; preserves semantics.
        up = self.predict_up15(X).to_numpy()
        return pl.Series("p_dn15", 1.0 - up)


def save_models(result: dict, out_dir: str | Path = "models/") -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    result["lgbm"].booster_.save_model(str(out / "lgbm.txt"))
    import pickle
    (out / "lgbm_sklearn.pkl").write_bytes(pickle.dumps(result["lgbm"]))
    (out / "logistic.pkl").write_bytes(pickle.dumps(result["logreg"]))
    (out / "metrics.json").write_text(
        json.dumps({"metrics": result["metrics"], "feature_cols": result["feature_cols"]}, indent=2)
    )
    return out


def load_models(model_dir: str | Path = "models/") -> QuantPredictor:
    import pickle
    d = Path(model_dir)
    payload = json.loads((d / "metrics.json").read_text())
    # Prefer sklearn wrapper so predict_proba is available; fall back to Booster.
    sklearn_path = d / "lgbm_sklearn.pkl"
    if sklearn_path.exists():
        lgbm = pickle.loads(sklearn_path.read_bytes())
    else:
        from lightgbm import Booster
        lgbm = Booster(model_file=str(d / "lgbm.txt"))
    return QuantPredictor(
        lgbm,
        pickle.loads((d / "logistic.pkl").read_bytes()),
        payload["feature_cols"],
        payload["metrics"],
    )

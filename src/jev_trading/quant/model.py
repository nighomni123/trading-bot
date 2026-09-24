"""Persist/load fitted quant baselines (LightGBM booster + logistic pipeline)."""
from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np
import polars as pl


class QuantPredictor:
    """P(future_return_15 > cost) from the LightGBM baseline (logreg kept for audit)."""

    def __init__(self, lgbm, logreg, feature_cols: list[str], metrics: dict, reg_model=None):
        self._lgbm = lgbm
        self.logreg = logreg
        self.reg_model = reg_model
        self.feature_cols = feature_cols
        self.metrics = metrics

    def predict_up15(self, X: pl.DataFrame) -> pl.Series:
        mat = X.select(self.feature_cols).to_numpy()
        # Real probability inference. Prefer sklearn wrapper predict_proba;
        # fall back to Booster.predict (binary objective yields probabilities,
        # not class labels) but never to sklearn predict() which yields labels.
        if hasattr(self._lgbm, "predict_proba"):
            proba = np.asarray(self._lgbm.predict_proba(mat)[:, 1], dtype=float)
        elif hasattr(self._lgbm, "predict") and hasattr(self._lgbm, "booster_"):
            # LightGBM sklearn wrapper without predict_proba? Unlikely; fall back
            # to Booster.predict which returns probabilities for binary objective.
            # If this branch hits, the model was loaded as sklearn but missing
            # predict_proba — treat as error rather than silent degradation.
            raise RuntimeError(
                "sklearn-wrapped LGBMClassifier loaded without predict_proba; "
                "probability inference impossible. Ensure lgbm_sklearn.pkl exists."
            )
        else:
            # Raw Booster: predict() returns float probabilities for binary objective.
            proba = np.asarray(self._lgbm.predict(mat), dtype=float)
        # Enforce continuous probability constraints explicitly.
        proba = np.clip(proba, 0.0, 1.0)
        return pl.Series("p_up15", proba)

    def predict_dn15(self, X: pl.DataFrame) -> pl.Series:
        # For binary classifier, down probability is complement; preserves semantics.
        up = self.predict_up15(X).to_numpy()
        return pl.Series("p_dn15", 1.0 - up)

    def predict_expected_return_15(self, X: pl.DataFrame) -> pl.Series:
        """Return a real regression prediction; never synthesize an economic edge."""
        if self.reg_model is None:
            raise RuntimeError("no regression head loaded; train an economic target before inference")
        mat = X.select(self.feature_cols).to_numpy()
        return pl.Series("expected_return_15", np.asarray(self.reg_model.predict(mat), dtype=float))

    def predict_economic_output(self, X: pl.DataFrame) -> dict:
        """Fail closed until dedicated excursion/uncertainty heads are trained."""
        raise RuntimeError(
            "economic output requires trained return, excursion, and uncertainty heads; "
            "no synthetic fallback is permitted"
        )


def save_models(result: dict, out_dir: str | Path = "models/") -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    result["lgbm"].booster_.save_model(str(out / "lgbm.txt"))
    import pickle
    (out / "lgbm_sklearn.pkl").write_bytes(pickle.dumps(result["lgbm"]))
    (out / "logistic.pkl").write_bytes(pickle.dumps(result["logreg"]))
    if result.get("reg_model") is not None:
        (out / "lgbm_reg.pkl").write_bytes(pickle.dumps(result["reg_model"]))
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
    reg_model = None
    reg_path = d / "lgbm_reg.pkl"
    if reg_path.exists():
        reg_model = pickle.loads(reg_path.read_bytes())
    return QuantPredictor(
        lgbm,
        pickle.loads((d / "logistic.pkl").read_bytes()),
        payload["feature_cols"],
        payload["metrics"],
        reg_model=reg_model,
    )

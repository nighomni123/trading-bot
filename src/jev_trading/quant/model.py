"""Persist/load fitted quant baselines (LightGBM booster + logistic pipeline)."""
from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np
import polars as pl


class QuantPredictor:
    """P(future_return_15 > cost) from the LightGBM baseline (logreg kept for audit)."""

    def __init__(self, lgbm, logreg, feature_cols: list[str], metrics: dict, reg_model=None, economic_bundle=None):
        self._lgbm = lgbm
        self.logreg = logreg
        self.reg_model = reg_model
        self.economic_bundle = economic_bundle
        self.feature_cols = feature_cols
        self.metrics = metrics

    def predict_up15(self, X: pl.DataFrame) -> pl.Series:
        if self.economic_bundle is not None:
            values = self.economic_bundle.predict_direction(X)["p_up_15"]
            return pl.Series("p_up15", np.clip(values, 0.0, 1.0))
        if self._lgbm is None:
            raise RuntimeError("no direction model loaded; train a binary or economic-v2 model")
        mat = X.select(self.feature_cols).to_numpy()
        # Legacy binary fallback retained only for EXP-008 baseline artifacts.
        if hasattr(self._lgbm, "predict_proba"):
            proba = np.asarray(self._lgbm.predict_proba(mat)[:, 1], dtype=float)
        elif hasattr(self._lgbm, "predict") and hasattr(self._lgbm, "booster_"):
            raise RuntimeError(
                "sklearn-wrapped LGBMClassifier loaded without predict_proba; "
                "probability inference impossible. Ensure lgbm_sklearn.pkl exists."
            )
        else:
            proba = np.asarray(self._lgbm.predict(mat), dtype=float)
        proba = np.clip(proba, 0.0, 1.0)
        return pl.Series("p_up15", proba)

    def predict_dn15(self, X: pl.DataFrame) -> pl.Series:
        if self.economic_bundle is not None:
            values = self.economic_bundle.predict_direction(X)["p_dn_15"]
            return pl.Series("p_dn15", np.clip(values, 0.0, 1.0))
        # Legacy baseline only: the old binary label made up its complement class.
        up = self.predict_up15(X).to_numpy()
        return pl.Series("p_dn15", 1.0 - up)

    def predict_direction15(self, X: pl.DataFrame) -> pl.DataFrame:
        """Return explicit up/flat/down probabilities; fail closed without v2 heads."""
        if self.economic_bundle is None:
            raise RuntimeError("explicit direction probabilities require a Phase 2 economic bundle")
        values = self.economic_bundle.predict_direction(X)
        return pl.DataFrame(values)

    def predict_expected_return_15(self, X: pl.DataFrame) -> pl.Series:
        """Return a real regression prediction; never synthesize an economic edge."""
        if self.economic_bundle is not None:
            values = self.economic_bundle.predict_execution_return(X, 15)
            return pl.Series("expected_return_15", values)
        if self.reg_model is None:
            raise RuntimeError("no regression head loaded; train an economic target before inference")
        mat = X.select(self.feature_cols).to_numpy()
        return pl.Series("expected_return_15", np.asarray(self.reg_model.predict(mat), dtype=float))

    def predict_economic_output(self, X: pl.DataFrame) -> dict:
        """Return complete real economic outputs, or fail closed without v2 heads."""
        if self.economic_bundle is None:
            raise RuntimeError(
                "economic output requires trained return, excursion, holding, and uncertainty heads; "
                "no synthetic fallback is permitted"
            )
        return self.economic_bundle.predict_economic_output(X)


def save_models(result: dict, out_dir: str | Path = "models/") -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    result["lgbm"].booster_.save_model(str(out / "lgbm.txt"))
    import pickle
    (out / "lgbm_sklearn.pkl").write_bytes(pickle.dumps(result["lgbm"]))
    (out / "logistic.pkl").write_bytes(pickle.dumps(result["logreg"]))
    if result.get("reg_model") is not None:
        (out / "lgbm_reg.pkl").write_bytes(pickle.dumps(result["reg_model"]))
    if result.get("economic_bundle") is not None:
        from jev_trading.quant.engine import save_economic_bundle
        save_economic_bundle(result["economic_bundle"], out)
    (out / "metrics.json").write_text(
        json.dumps({"metrics": result["metrics"], "feature_cols": result["feature_cols"]}, indent=2)
    )
    return out


def load_models(model_dir: str | Path = "models/") -> QuantPredictor:
    import pickle
    d = Path(model_dir)
    economic_bundle = None
    if (d / "economic_bundle.pkl").exists():
        from jev_trading.quant.engine import load_economic_bundle
        economic_bundle = load_economic_bundle(d)
    metrics_path = d / "metrics.json"
    if metrics_path.exists():
        payload = json.loads(metrics_path.read_text())
        feature_cols = payload.get("feature_cols", list(economic_bundle.feature_cols) if economic_bundle else [])
        metrics = payload.get("metrics", {})
    elif economic_bundle is not None:
        feature_cols = list(economic_bundle.feature_cols)
        metrics = {"model_version": "economic-v2"}
    else:
        raise FileNotFoundError(f"no model artifacts found in {d}")
    lgbm = None
    if (d / "lgbm_sklearn.pkl").exists():
        lgbm = pickle.loads((d / "lgbm_sklearn.pkl").read_bytes())
    elif (d / "lgbm.txt").exists():
        from lightgbm import Booster
        lgbm = Booster(model_file=str(d / "lgbm.txt"))
    reg_model = None
    reg_path = d / "lgbm_reg.pkl"
    if reg_path.exists():
        reg_model = pickle.loads(reg_path.read_bytes())
    logreg = None
    if (d / "logistic.pkl").exists():
        logreg = pickle.loads((d / "logistic.pkl").read_bytes())
    return QuantPredictor(
        lgbm,
        logreg,
        feature_cols,
        metrics,
        reg_model=reg_model,
        economic_bundle=economic_bundle,
    )

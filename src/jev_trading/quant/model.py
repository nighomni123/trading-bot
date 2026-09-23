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
        """Phase 6: regression prediction for economic evaluation."""
        if self.reg_model is None:
            # Fallback: use probability-weighted approximate return magnitude.
            # This preserves backward compatibility for models without regression.
            p_up = self.predict_up15(X).to_numpy()
            # Approximate gross return magnitude from historical average (0.0014 threshold context)
            # Not a replacement for a real regression head, but avoids None crashes.
            return pl.Series("expected_return_15", p_up * 0.003 - (1.0 - p_up) * 0.002)
        mat = X.select(self.feature_cols).to_numpy()
        return pl.Series("expected_return_15", np.asarray(self.reg_model.predict(mat), dtype=float))

    def predict_economic_output(self, X: pl.DataFrame) -> dict:
        """Return a dict with all Phase 6 economic predictions for a given X."""
        from jev_trading.quant.economic import EconomicQuantOutput
        p_up = self.predict_up15(X).to_numpy()
        p_dn = 1.0 - p_up
        exp_ret = self.predict_expected_return_15(X).to_numpy()
        # Simple excursion estimates derived from label-engine patterns (approximate)
        # For measurement-first design, we use fixed estimates based on feature-based patterns.
        # A real upgrade path would train separate excursion models.
        expected_downside_15 = -np.clip(np.abs(exp_ret) + 0.0005, 0.0, 0.05)
        # Uncertainty proxy: variance of predictions across nearby rows (simplified to constant)
        uncertainty = float(np.var(p_up) if len(p_up) > 1 else 0.01)
        # If we have raw label info, we could compute exact excursions; here we approximate.
        out_list = []
        for i in range(len(X)):
            out_list.append(EconomicQuantOutput(
                p_up_15=float(p_up[i]),
                p_dn_15=float(p_dn[i]),
                expected_return_15=float(exp_ret[i]),
                expected_downside_15=float(-abs(exp_ret[i]) - 0.0005),
                expected_favorable_excursion_15=float(abs(exp_ret[i]) + 0.001),
                expected_adverse_excursion_15=float(-abs(exp_ret[i]) - 0.0005),
                uncertainty=uncertainty,
                horizon_min=15,
            ))
        # Note: this is a per-row approximation; full economic evaluation uses evaluate_economic_opportunity.
        return {"rows": out_list, "p_up_15": pl.Series("p_up15", p_up), "expected_return_15": pl.Series("expected_return_15", exp_ret)}


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

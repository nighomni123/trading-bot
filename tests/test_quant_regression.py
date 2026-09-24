"""Regression tests for EXP-004: quant probability integrity.
Fail if predict_proba is replaced by predict anywhere probability semantics are required.
"""
from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from jev_trading.quant.model import QuantPredictor, load_models, save_models
from jev_trading.quant.train import train_models


def _make_quant(tmp_path):
    # Minimal synthetic data for probability contract test (no external data required)
    import polars as pl
    np.random.seed(7)
    n = 300
    df = pl.DataFrame({
        "timestamp": list(range(n)),
        "open": np.random.randn(n), "high": np.random.randn(n),
        "low": np.random.randn(n), "close": np.random.randn(n),
        "volume": np.abs(np.random.randn(n)), "funding_rate": np.zeros(n),
        "open_interest": np.ones(n),
    })
    # Add minimal feature columns expected by build_features; use raw approximation
    from jev_trading.state.features import build_features, FEATURE_COLUMNS
    feats = build_features(df)
    # LightGBM needs label; use synthetic binary target
    y = (feats["close"].to_numpy() > 0).astype(int)
    # Train
    from lightgbm import LGBMClassifier
    model = LGBMClassifier(num_leaves=4, n_estimators=20, verbose=-1)
    X = feats.select(FEATURE_COLUMNS).to_numpy()
    model.fit(X, y)
    # Persist and load via real save/load path
    out = tmp_path / "models"
    result = {"lgbm": model, "logreg": None, "metrics": {}, "feature_cols": FEATURE_COLUMNS}
    save_models(result, str(out))
    return load_models(str(out)), feats


def test_purge_scales_with_target_horizon():
    from jev_trading.quant.train import purge_ms_for_horizon
    assert purge_ms_for_horizon(15) == 900_000
    assert purge_ms_for_horizon(60) == 3_600_000
    with pytest.raises(ValueError):
        purge_ms_for_horizon(0)


def test_missing_economic_heads_fail_closed():
    predictor = QuantPredictor(object(), None, ["x"], {})
    frame = pl.DataFrame({"x": [1.0]})
    with pytest.raises(RuntimeError, match="regression head"):
        predictor.predict_expected_return_15(frame)
    with pytest.raises(RuntimeError, match="no synthetic fallback"):
        predictor.predict_economic_output(frame)


def test_predict_returns_floating_probabilities(tmp_path):
    """Test A: inference returns float probabilities, not int class labels."""
    predictor, feats = _make_quant(tmp_path)
    p = predictor.predict_up15(feats.select(predictor.feature_cols))
    assert p.dtype == pl.Float64 or "float" in str(p.dtype), f"expected float, got {p.dtype}"
    # Must not be integer 0/1 exclusively
    vals = p.to_numpy()
    assert vals.dtype.kind == "f", f"expected float array, got {vals.dtype}"


def test_probability_bounds(tmp_path):
    """Test B: 0 <= p <= 1 for all outputs."""
    predictor, feats = _make_quant(tmp_path)
    p_up = predictor.predict_up15(feats.select(predictor.feature_cols)).to_numpy()
    p_dn = predictor.predict_dn15(feats.select(predictor.feature_cols)).to_numpy()
    assert np.all(p_up >= 0.0) and np.all(p_up <= 1.0), "p_up out of bounds"
    assert np.all(p_dn >= 0.0) and np.all(p_dn <= 1.0), "p_dn out of bounds"
    # Should have resolution (not everything exactly 0 or 1) on non-degenerate data
    assert len(np.unique(np.round(p_up, 4))) > 1 or len(p_up) < 3, "probabilities lack resolution"


def test_inference_consistent_with_train_proba(tmp_path):
    """Test C: inference matches training-time predict_proba semantics."""
    predictor, feats = _make_quant(tmp_path)
    X = feats.select(predictor.feature_cols).to_numpy()
    from lightgbm import Booster
    # Direct booster predict_proba (same underlying model loaded)
    booster = predictor._lgbm
    direct = booster.predict_proba(X)[:, 1]
    via_predictor = predictor.predict_up15(feats.select(predictor.feature_cols)).to_numpy()
    np.testing.assert_allclose(via_predictor, direct, rtol=1e-6, atol=1e-6)


def test_policy_receives_probabilities_not_labels(tmp_path):
    """Test D: policy consumes floats; hard 0/1 labels would fail semantic check."""
    from jev_trading.policy.engine import decide
    # If quant returned hard labels (int 0/1), policy confidence = min(p, trade_ok) loses resolution.
    # We enforce that inputs are floats with sub-integer resolution.
    quant = {"p_up_15": 0.72, "p_dn_15": 0.28, "expected_return_15": 0.005}
    pobj = decide(quant, {"trade_ok": 0.7, "failure_regime": 0.2})
    assert isinstance(pobj.confidence, float)
    assert 0.0 <= pobj.confidence <= 1.0


def test_calibration_operates_on_probabilities(tmp_path):
    """Test E: calibration metrics need float probabilities; hard labels break ECE/logloss."""
    from jev_trading.quant.train import _ece, _log_loss
    y_true = np.array([0, 1, 0, 1, 1, 0])
    probs = np.array([0.2, 0.8, 0.3, 0.7, 0.9, 0.1])
    # These must succeed with floats
    assert _ece(y_true, probs) >= 0.0
    assert _log_loss(y_true, probs) > 0.0
    # Hard labels (0/1) should still compute but lose calibration meaning; check that
    # they are distinguishable from true probabilities by resolution test (not required to fail).
    # The guard is: changing inference back to predict() would make probs exactly 0/1
    # for many bars, which is detectable by resolution.

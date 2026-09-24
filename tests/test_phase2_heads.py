"""Synthetic multi-output head and economic-output integration checks."""
from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from jev_trading.quant.engine import save_economic_bundle, train_economic_bundle
from jev_trading.state.features import build_phase2_features

T0 = 1_609_459_200_000
MIN = 60_000


def synthetic_bars(n: int = 5000) -> pl.DataFrame:
    close = []
    for i in range(n):
        block = (i // 120) % 3
        step = {0: 0.0006, 1: 0.0, 2: -0.0006}[block]
        close.append(100.0 * (1.0 + (i // 120) * 0.01 + (i % 120) * step))
    close = np.asarray(close)
    open_ = np.r_[close[0], close[:-1]]
    return pl.DataFrame({
        "timestamp": [T0 + i * MIN for i in range(n)],
        "open": open_,
        "high": close * 1.001,
        "low": close * 0.999,
        "close": close,
        "volume": 100.0 + np.arange(n),
        "funding_rate": np.full(n, 0.0001),
        "open_interest": 1_000_000.0 + np.arange(n) * 10.0,
    })


def train_small_bundle():
    bars = synthetic_bars()
    return bars, train_economic_bundle(
        bars,
        train_end=T0 + 3200 * MIN,
        valid_start=T0 + 3200 * MIN,
        valid_end=T0 + 4000 * MIN,
        feature_set="base",
        lgbm_params={"n_estimators": 5, "num_leaves": 4},
        seeds=(7, 17),
    )


def test_multiclass_outputs_are_explicit_bounded_and_normalized():
    bars, bundle = train_small_bundle()
    features = build_phase2_features(bars).select(["timestamp", *bundle.feature_cols]).drop_nulls().tail(20)
    direction = bundle.predict_direction(features)
    stacked = np.column_stack([direction["p_up_15"], direction["p_flat_15"], direction["p_dn_15"]])
    assert np.allclose(stacked.sum(axis=1), 1.0)
    assert np.all(stacked >= 0) and np.all(stacked <= 1)
    assert not np.allclose(direction["p_dn_15"], 1 - direction["p_up_15"])


def test_expected_return_uses_ensemble_mean_not_first_seed():
    bars, bundle = train_small_bundle()
    features = build_phase2_features(bars).select(["timestamp", *bundle.feature_cols]).drop_nulls().tail(20)
    matrix = features.select(list(bundle.feature_cols)).to_numpy()
    expected = np.column_stack([model.predict(matrix) for model in bundle.execution_return_ensemble_15]).mean(axis=1)
    np.testing.assert_allclose(bundle.predict_execution_return(features, 15), expected)


def test_complete_output_has_real_heads_and_uncertainty():
    bars, bundle = train_small_bundle()
    features = build_phase2_features(bars).select(["timestamp", *bundle.feature_cols]).drop_nulls().tail(20)
    output = bundle.predict_economic_output(features)
    assert len(output["rows"]) == 20
    assert np.all(output["uncertainty_15"] >= 0) and np.all(output["uncertainty_15"] <= 1)
    for row in output["rows"]:
        assert row.expected_return_15 is not None
        assert row.expected_favorable_excursion_15 is not None
        assert row.expected_adverse_excursion_15 is not None
        assert row.holding_time_minutes is not None
        assert row.side in {"LONG", "SHORT"}


def test_bundle_roundtrip_and_frozen_oos_guard(tmp_path):
    bars, bundle = train_small_bundle()
    hashes = save_economic_bundle(bundle, tmp_path)
    assert set(hashes) == {"economic_bundle.pkl", "economic_metadata.json", "metrics.json"}
    from jev_trading.quant.model import load_models
    predictor = load_models(tmp_path)
    features = build_phase2_features(bars).select(["timestamp", *bundle.feature_cols]).drop_nulls().tail(5)
    assert predictor.predict_direction15(features).height == 5
    assert len(predictor.predict_economic_output(features)["rows"]) == 5
    with pytest.raises(ValueError, match="2025"):
        train_economic_bundle(
            bars,
            train_end=T0 + 3200 * MIN,
            valid_start=T0 + 3200 * MIN,
            valid_end=1_767_225_600_000,
            feature_set="base",
            lgbm_params={"n_estimators": 2, "num_leaves": 2},
            seeds=(1, 2),
        )

"""Phase 3 payoff, cost, calibration, and split-integrity tests."""
from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from scripts.run_phase3 import _conditional_timeout_prediction, _fit_side_probability_model
from jev_trading.quant.payoff import (
    CostModel,
    FundingIndex,
    binary_probability_metrics,
    chronological_masks,
    execution_components,
    expected_value,
    select_non_overlapping,
    simulate_corrected_fixed_horizon,
)

T0 = 1_735_689_600_000
MINUTE = 60_000


def test_exact_fill_fees_and_slippage_are_charged_once():
    costs = CostModel()
    long = execution_components(
        np.array([1.0]), np.array([100.0]), np.array([110.0]), costs, np.array([0.0])
    )
    expected_entry = 100.0 * 1.0002
    expected_exit = 110.0 * 0.9998
    expected_gross = (expected_exit - expected_entry) / expected_entry
    assert long["entry_fill"][0] == pytest.approx(expected_entry)
    assert long["exit_fill"][0] == pytest.approx(expected_exit)
    assert long["gross_fill_return"][0] == pytest.approx(expected_gross)
    assert long["slippage_cost"][0] == pytest.approx(0.1 - expected_gross)
    assert long["fees"][0] == pytest.approx(0.0005 * (expected_entry + expected_exit) / expected_entry)
    assert long["net"][0] == pytest.approx(expected_gross - long["fees"][0])


def test_fee_stress_scales_only_fees_and_reduces_net_return():
    base = execution_components(
        np.array([1.0]), np.array([100.0]), np.array([101.0]), CostModel(), np.array([0.0])
    )
    stressed = execution_components(
        np.array([1.0]), np.array([100.0]), np.array([101.0]),
        CostModel(fee_multiplier=3.0), np.array([0.0]),
    )
    assert stressed["fees"] == pytest.approx(3.0 * base["fees"])
    assert stressed["slippage_cost"] == pytest.approx(base["slippage_cost"])
    assert stressed["net"] < base["net"]


def test_short_payoff_encodes_sign():
    costs = CostModel()
    short = execution_components(
        np.array([-1.0]), np.array([100.0]), np.array([90.0]), costs, np.array([0.0])
    )
    assert short["gross_fill_return"][0] > 0.09
    assert short["raw_reference_return"][0] == pytest.approx(0.1)


def test_expected_value_uses_all_three_outcomes():
    assert expected_value(
        {"TP_FIRST": 0.5, "SL_FIRST": 0.2, "TIMEOUT": 0.3},
        {"TP_FIRST": 0.004, "SL_FIRST": -0.003, "TIMEOUT": 0.0},
    ) == pytest.approx(0.0014)
    with pytest.raises(ValueError, match="sum to one"):
        expected_value(
            {"TP_FIRST": 0.5, "SL_FIRST": 0.2, "TIMEOUT": 0.2},
            {"TP_FIRST": 1.0, "SL_FIRST": 1.0, "TIMEOUT": 1.0},
        )


def test_funding_uses_discrete_visible_prints_not_minute_proration():
    # Entry at 08:00, timeout exit at 16:00: the 08:00 print is excluded;
    # the new 16:00 print is charged once.
    timestamps = np.array([T0, T0 + 8 * 60 * MINUTE, T0 + 16 * 60 * MINUTE], dtype=np.int64)
    rates = np.array([0.0001, 0.0001, 0.0002])
    closes = np.array([100.0, 100.0, 100.0])
    funding = FundingIndex(timestamps, rates, closes)
    assert funding.expected_event_count(np.array([0]), np.array([2]))[0] == 1
    cost = funding.actual_cost_fraction(
        np.array([1.0]), np.array([0]), np.array([2]), np.array([100.02])
    )
    assert cost[0] == pytest.approx(-0.0002 * 100.0 / 100.02)


def test_probability_metrics_report_calibration_and_distribution():
    y = np.array([0, 0, 1, 1])
    p = np.array([0.1, 0.25, 0.75, 0.9])
    metrics = binary_probability_metrics(y, p)
    assert metrics["accuracy"] == 1.0
    assert metrics["roc_auc"] == 1.0
    assert metrics["brier"] < 0.1
    assert metrics["mean_ece"] >= 0.0
    assert sum(metrics["class_counts"]) == 4


def test_side_probability_head_keeps_three_class_mass_when_middle_class_is_absent():
    rng = np.random.default_rng(7)
    X = rng.normal(size=(200, 4))
    y = np.where(np.arange(200) % 2, 2, 0).astype(np.int8)
    config = {"model": {"params": {
        "n_estimators": 2, "num_leaves": 3, "learning_rate": 0.1,
        "verbosity": -1, "deterministic": True, "force_col_wise": True,
    }, "seed": 7}}
    probability = _fit_side_probability_model(X, y, X, config, 7)
    assert probability.shape == (200, 3)
    np.testing.assert_allclose(probability.sum(axis=1), 1.0)
    np.testing.assert_allclose(probability[:, 1], 0.0, atol=1e-15)
    assert np.all(probability[:, 0] >= 0) and np.all(probability[:, 2] >= 0)


def test_conditional_timeout_head_uses_training_mean_below_two_rows():
    X = np.arange(40, dtype=float).reshape(20, 2)
    target = np.arange(20, dtype=float)
    outcomes = np.array([1] + [0] * 19, dtype=np.int8)
    indices = np.arange(20)
    config = {"model": {
        "params": {"n_estimators": 2, "num_leaves": 3, "verbosity": -1},
        "seed": 7,
        "conditional_timeout_minimum_rows": 2,
    }}
    prediction = _conditional_timeout_prediction(
        X, target, indices, outcomes, X, config
    )
    np.testing.assert_allclose(prediction, 0.0)


def test_non_overlap_uses_actual_row_spacing():
    selected = select_non_overlapping(
        np.array([0, 1, 5, 15]), np.ones(16), np.zeros(16), 10
    )
    np.testing.assert_array_equal(selected, [0, 15])


def test_corrected_fixed_horizon_uses_exact_components_and_no_synthetic_penalty():
    n = 50
    timestamps = T0 + np.arange(n, dtype=np.int64) * MINUTE
    entry = np.full(n, 100.0)
    exit_ = np.full(n, 100.1)
    funding = FundingIndex(timestamps, np.zeros(n), np.full(n, 100.0))
    side = np.ones(n)
    predicted = np.full(n, 0.01)
    uncertainty = np.zeros(n)
    result = simulate_corrected_fixed_horizon(
        timestamps, entry, exit_, side, predicted, uncertainty, funding,
        np.arange(0, 40), horizon_minutes=15, min_edge_multiplier=1.0,
    )
    assert result["trade_count"] == 3
    assert result["uncertainty_penalty_applied"] is False
    assert all(trade["fees"] > 0 for trade in result["trades"])
    assert all(trade["slippage_cost"] > 0 for trade in result["trades"])


def test_walk_forward_masks_enforce_sixty_minute_purge_and_embargo():
    timestamps = np.arange(0, 360, dtype=np.int64) * MINUTE
    train, valid = chronological_masks(timestamps, 120 * MINUTE, 120 * MINUTE, 360 * MINUTE, 60)
    assert timestamps[train].max() == 59 * MINUTE
    assert timestamps[valid].min() == 180 * MINUTE
    assert timestamps[valid].max() == 299 * MINUTE
    with pytest.raises(ValueError, match="chronological"):
        chronological_masks(timestamps, 121 * MINUTE, 120 * MINUTE, 240 * MINUTE, 60)


def test_feature_frame_remains_point_in_time_for_phase3_state():
    n = 1800
    close = 100.0 + np.arange(n) * 0.01
    frame = pl.DataFrame({
        "timestamp": T0 + np.arange(n, dtype=np.int64) * MINUTE,
        "open": close,
        "high": close + 0.1,
        "low": close - 0.1,
        "close": close,
        "volume": np.full(n, 1.0),
        "funding_rate": np.zeros(n),
        "open_interest": np.full(n, 1000.0),
    })
    from jev_trading.state.features import FEATURE_COLUMNS, build_features

    base = build_features(frame).select(FEATURE_COLUMNS)
    changed_close = close.copy()
    changed_close[1600:] *= 2.0
    changed = build_features(frame.with_columns(close=pl.Series(changed_close))).select(FEATURE_COLUMNS)
    assert base.head(1600).equals(changed.head(1600))
    assert not base.tail(100).equals(changed.tail(100))

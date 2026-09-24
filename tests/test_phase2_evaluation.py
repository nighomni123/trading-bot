"""Phase 2 metric, uncertainty, and fixed-horizon economic checks."""
from __future__ import annotations

import numpy as np

from jev_trading.quant.evaluation import (
    direction_metrics,
    regression_metrics,
    simulate_fixed_horizon,
    simulate_fixed_horizon_segments,
    uncertainty_reliability,
)


def test_direction_metrics_require_three_explicit_probabilities():
    result = direction_metrics(
        np.array([0, 1, 2, 0]),
        np.array([[0.8, 0.1, 0.1], [0.1, 0.8, 0.1], [0.1, 0.1, 0.8], [0.7, 0.2, 0.1]]),
    )
    assert result["accuracy"] == 1.0
    assert result["brier"] < 0.5
    assert result["log_loss"] > 0


def test_uncertainty_reliability_reports_error_buckets():
    result = uncertainty_reliability(
        np.array([0.0, 0.1, 0.4, 0.8]),
        np.array([0.0, 0.0, 0.0, 0.0]),
        np.array([0.05, 0.2, 0.5, 0.9]),
    )
    assert len(result["buckets"]) == 4
    assert result["errors_monotonic"] is True
    assert result["spearman_error_vs_uncertainty"] > 0


def test_fixed_horizon_simulation_uses_explicit_direction_and_costs():
    n = 40
    timestamps = np.arange(n, dtype=np.int64) * 60_000
    p_up = np.full(n, 0.7)
    p_flat = np.full(n, 0.2)
    p_down = np.full(n, 0.1)
    predicted = np.full(n, 0.01)
    actual = np.full(n, 0.01)
    funding = np.zeros(n)
    uncertainty = np.zeros(n)
    result = simulate_fixed_horizon(
        timestamps, p_up, p_flat, p_down, predicted, actual, funding, uncertainty,
        horizon_minutes=15,
    )
    assert result["trade_count"] == 3
    assert result["expectancy"] > 0
    assert result["net_return_sum"] > 0
    stressed = simulate_fixed_horizon(
        timestamps, p_up, p_flat, p_down, predicted, actual, funding, uncertainty,
        horizon_minutes=15, fee_mult=3.0,
    )
    assert stressed["net_return_sum"] < result["net_return_sum"]


def test_segment_runner_does_not_compress_gaps():
    timestamps = np.array([0, 60_000, 180_000, 240_000], dtype=np.int64)
    result = simulate_fixed_horizon_segments(
        timestamps, np.ones(4), np.zeros(4), np.zeros(4),
        np.ones(4), np.ones(4), np.zeros(4), np.zeros(4),
    )
    assert result["segment_count"] == 2
    assert result["candidate_bars"] == 4


def test_fixed_horizon_simulation_rejects_gaps():
    try:
        simulate_fixed_horizon(
            np.array([0, 60_000, 180_000]), np.ones(3), np.zeros(3), np.zeros(3),
            np.ones(3), np.ones(3), np.zeros(3), np.zeros(3),
        )
    except ValueError as exc:
        assert "contiguous" in str(exc)
    else:
        raise AssertionError("gapped timestamps must fail")

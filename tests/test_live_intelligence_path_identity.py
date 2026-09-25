"""Barrier identity and path-output invariants."""
from __future__ import annotations

import polars as pl

from jev_trading.live_intelligence.quant import analyze_path, build_completed_path_samples
from jev_trading.live_intelligence.schemas import Side
from tests.test_live_intelligence import environment


def _frame(values):
    return pl.DataFrame({
        "timestamp": [index * 60_000 for index in range(len(values))],
        "open": values,
        "high": [value + 0.05 for value in values],
        "low": [value - 0.05 for value in values],
        "close": values,
    })


def test_analyze_path_never_pools_different_barrier_identities():
    frame = _frame([100 + index * 0.1 for index in range(20)])
    one_percent = build_completed_path_samples(
        frame, side=Side.LONG, target_fraction=0.01, stop_fraction=0.01, horizon_minutes=2,
    )
    two_percent = build_completed_path_samples(
        frame, side=Side.LONG, target_fraction=0.02, stop_fraction=0.02, horizon_minutes=2,
    )
    result = analyze_path(
        environment(), one_percent + two_percent, side=Side.LONG,
        target_fraction=0.01, stop_fraction=0.01, horizon_minutes=2, horizon_seconds=120,
    )
    assert result.empirical_sample_size == len(one_percent)
    assert result.evidence["barrier_identity"] == {
        "side": "LONG", "target_fraction": 0.01, "stop_fraction": 0.01, "horizon_minutes": 2,
    }
    assert result.expected_return is not None
    assert result.expected_mfe is not None
    assert result.expected_mae is not None
    assert result.expected_duration_seconds is not None


def test_timeout_uses_next_open_and_reports_duration():
    frame = _frame([100.0] * 6)
    samples = build_completed_path_samples(
        frame, side=Side.LONG, target_fraction=0.01, stop_fraction=0.01, horizon_minutes=1,
    )
    assert samples
    assert all(sample.timeout and sample.return_fraction == 0 for sample in samples)
    result = analyze_path(
        environment(), samples, side=Side.LONG, target_fraction=0.01, stop_fraction=0.01,
        horizon_minutes=1, horizon_seconds=60,
    )
    assert result.path_probabilities == {"target": 0.0, "stop": 0.0, "timeout": 1.0}
    assert result.expected_duration_seconds == 60


def test_same_bar_target_stop_touch_is_stop_first():
    frame = pl.DataFrame({
        "timestamp": [0, 60_000, 120_000],
        "open": [100.0, 100.0, 100.0],
        "high": [100.1, 102.0, 100.1],
        "low": [99.9, 98.0, 99.9],
        "close": [100.0, 100.0, 100.0],
    })
    sample = build_completed_path_samples(
        frame, side=Side.LONG, target_fraction=0.01, stop_fraction=0.01, horizon_minutes=1,
    )[0]
    assert sample.stop_first is True
    assert sample.target_first is False
    assert sample.return_fraction == -0.01

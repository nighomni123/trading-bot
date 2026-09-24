"""Phase 1 excursion and first-touch label checks on synthetic bars."""
from __future__ import annotations

import polars as pl
import pytest

from jev_trading.labels.engine import compute_labels, compute_path_labels

T0 = 1_672_531_200_000
MIN = 60_000


def bars_with(highs: list[float], lows: list[float], n: int | None = None) -> pl.DataFrame:
    n = n or len(highs)
    return pl.DataFrame({
        "timestamp": [T0 + i * MIN for i in range(n)],
        "open": [100.0] * n,
        "high": highs,
        "low": lows,
        "close": [100.0] * n,
        "volume": [1.0] * n,
        "funding_rate": [0.0] * n,
        "open_interest": [1.0] * n,
    })


def test_mfe_mae_match_each_forward_horizon():
    n, high_at, low_at = 160, 80, 120
    highs = [100.0] * n
    lows = [100.0] * n
    highs[high_at] = 110.0
    lows[low_at] = 90.0
    labels = compute_labels(bars_with(highs, lows))
    for horizon in (5, 15, 30, 60):
        row = high_at - horizon
        assert row >= 0
        assert labels[f"mfe_{horizon}m"][row] == pytest.approx(0.10)
        assert labels[f"mae_{horizon}m"][row] == pytest.approx(0.0)
        low_row = low_at - horizon
        assert labels[f"mae_{horizon}m"][low_row] == pytest.approx(-0.10)
        expected_high = 0.10 if low_row < high_at <= low_at else 0.0
        assert labels[f"mfe_{horizon}m"][low_row] == pytest.approx(expected_high)
        if high_at + horizon < n:
            assert labels[f"mfe_{horizon}m"][high_at] == pytest.approx(0.0)
        else:
            assert labels[f"mfe_{horizon}m"][high_at] is None
        if low_at + horizon < n:
            assert labels[f"mae_{horizon}m"][low_at] == pytest.approx(0.0)
        else:
            assert labels[f"mae_{horizon}m"][low_at] is None


def test_path_race_order_and_same_bar_tie():
    n = 12
    highs = [100.0] * n
    lows = [100.0] * n
    highs[2] = 101.0
    lows[5] = 99.0
    first = compute_path_labels(bars_with(highs, lows), 0.001, 0.001, horizon=8)
    assert first["time_to_tp"][0] == 2
    assert first["time_to_sl"][0] == 5
    assert first["tp_before_sl"][0] == 1

    reverse = compute_path_labels(
        bars_with(
            [101.0 if i == 5 else 100.0 for i in range(n)],
            [99.0 if i == 2 else 100.0 for i in range(n)],
        ),
        0.001,
        0.001,
        horizon=8,
    )
    assert reverse["tp_before_sl"][0] == 0
    assert reverse["time_to_tp"][0] == 5
    assert reverse["time_to_sl"][0] == 2

    tie_highs = [100.0] * n
    tie_lows = [100.0] * n
    tie_highs[3] = 101.0
    tie_lows[3] = 99.0
    tie = compute_path_labels(bars_with(tie_highs, tie_lows), 0.001, 0.001, horizon=8)
    assert tie["time_to_tp"][0] == 3
    assert tie["time_to_sl"][0] == 3
    assert tie["tp_before_sl"][0] == 0

    quiet = compute_path_labels(bars_with([100.0] * n, [100.0] * n), 0.001, 0.001, horizon=8)
    assert quiet["tp_before_sl"][0] is None
    assert quiet["timeout"][0] == 1


def test_path_tail_and_optional_wide_columns():
    n = 20
    labels = compute_path_labels(bars_with([100.0] * n, [100.0] * n), 0.001, 0.001, horizon=10)
    assert labels["time_to_tp"][n - 10] is None
    assert labels["time_to_sl"][n - 10] is None
    wide = compute_labels(bars_with([100.0] * n, [100.0] * n), path_pairs=((0.001, 0.001),))
    assert "tp_before_sl_10bp_10bp" in wide.columns
    assert wide["tp_before_sl_10bp_10bp"][n - 10] is None

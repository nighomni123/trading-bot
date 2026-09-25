"""Dedicated Phase 3 executable barrier-label contract tests."""
from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from jev_trading.labels.barriers import (
    SL_FIRST,
    TIMEOUT,
    TP_FIRST,
    BarrierConfig,
    ExecutionBarrierCache,
    barrier_grid,
    compute_execution_barrier_labels,
)

T0 = 1_735_689_600_000
MINUTE = 60_000
CONFIG = BarrierConfig(tp_bps=10, sl_bps=10, horizon_minutes=5)


def bars(
    n: int | None = None,
    *,
    high: list[float] | None = None,
    low: list[float] | None = None,
    open_: list[float] | None = None,
) -> pl.DataFrame:
    n = len(high or low or open_ or ()) if (high or low or open_) else (n or 10)
    opens = open_ or [100.0] * n
    return pl.DataFrame({
        "timestamp": [T0 + i * MINUTE for i in range(n)],
        "open": opens,
        "high": high or [100.0] * n,
        "low": low or [100.0] * n,
        "close": opens,
        "volume": [1.0] * n,
        "funding_rate": [0.0] * n,
        "open_interest": [1.0] * n,
    })


def test_grid_is_pre_registered_and_has_one_hundred_cells():
    grid = barrier_grid()
    assert len(grid) == 100
    assert len({config.config_id for config in grid}) == 100


def test_tp_first_and_uses_next_open_as_entry_reference():
    frame = bars(high=[100.0, 100.1] + [100.0] * 8, low=[100.0] * 10)
    labels = compute_execution_barrier_labels(frame, CONFIG)
    assert labels["long_outcome"][0] == TP_FIRST
    assert labels["long_time_to_tp"][0] == 1
    assert labels["short_outcome"][0] == SL_FIRST  # high is the short stop
    changed_entry = frame.with_columns(
        pl.Series("open", [100.0] + [200.0] * 9),
        pl.Series("high", [100.0, 200.1] + [200.0] * 8),
        pl.Series("low", [100.0] + [200.0] * 9),
    )
    shifted = compute_execution_barrier_labels(changed_entry, CONFIG)
    assert shifted["long_outcome"][0] == TIMEOUT


def test_sl_first_and_short_tp_are_side_mirrored():
    frame = bars(low=[100.0, 99.9] + [100.0] * 8)
    labels = compute_execution_barrier_labels(frame, CONFIG)
    assert labels["long_outcome"][0] == SL_FIRST
    assert labels["long_time_to_sl"][0] == 1
    assert labels["short_outcome"][0] == TP_FIRST
    assert labels["short_time_to_tp"][0] == 1


def test_timeout_and_exact_touch_are_inclusive():
    quiet = compute_execution_barrier_labels(bars(), CONFIG)
    assert quiet["long_outcome"][0] == TIMEOUT
    assert quiet["short_outcome"][0] == TIMEOUT
    exact = compute_execution_barrier_labels(
        bars(high=[100.0, 100.1] + [100.0] * 8, low=[100.0, 99.9] + [100.0] * 8), CONFIG
    )
    assert exact["long_time_to_tp"][0] == 1
    assert exact["long_time_to_sl"][0] == 1


def test_same_bar_touch_is_conservative_sl_and_flagged():
    frame = bars(high=[100.0, 100.1] + [100.0] * 8, low=[100.0, 99.9] + [100.0] * 8)
    labels = compute_execution_barrier_labels(frame, CONFIG)
    assert labels["long_outcome"][0] == SL_FIRST
    assert labels["short_outcome"][0] == SL_FIRST
    assert labels["long_ambiguous"][0] is True
    assert labels["short_ambiguous"][0] is True


def test_missing_bars_are_rejected_not_compressed():
    frame = bars(8).with_columns(
        pl.Series("timestamp", [T0 + i * MINUTE for i in range(7)] + [T0 + 10 * MINUTE])
    )
    with pytest.raises(ValueError, match="contiguous"):
        compute_execution_barrier_labels(frame, CONFIG)


def test_incomplete_tail_windows_are_null():
    frame = bars(8)
    labels = compute_execution_barrier_labels(frame, CONFIG)
    assert labels["valid"][0] is True
    assert labels["valid"][2] is False
    assert labels["long_outcome"][2] is None
    assert labels["timeout_return"][2] is None


def test_side_symmetry_preserves_first_touch_offsets():
    n = 12
    high = [100.0] * n
    low = [100.0] * n
    high[4], low[2] = 101.0, 99.0
    labels = compute_execution_barrier_labels(bars(high=high, low=low), CONFIG)
    assert labels["long_time_to_tp"][0] == 4
    assert labels["long_time_to_sl"][0] == 2
    assert labels["short_time_to_tp"][0] == 2
    assert labels["short_time_to_sl"][0] == 4
    assert labels["long_outcome"][0] == SL_FIRST
    assert labels["short_outcome"][0] == TP_FIRST


def test_cache_shares_excursions_and_does_not_mutate_inputs():
    frame = bars(20, high=[100.1] + [100.0] * 19, low=[99.9] + [100.0] * 19)
    before = frame.clone()
    cache = ExecutionBarrierCache(frame)
    long = cache.labels(BarrierConfig(10, 10, 5))
    short = cache.labels(BarrierConfig(10, 10, 5))
    assert frame.equals(before)
    np.testing.assert_allclose(long.long_mfe[:15], short.short_mfe[:15])

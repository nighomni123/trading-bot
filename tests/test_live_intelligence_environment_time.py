"""Causal completed-bucket environment invariants."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import polars as pl

from jev_trading.contracts import BAR_COLUMNS
from jev_trading.environment import build_market_environment
from jev_trading.live_intelligence.schemas import DataQuality
from tests.test_live_intelligence import T0, bars


def _decision() -> datetime:
    return datetime.fromtimestamp((T0 + 300 * 60_000 + 30_000) / 1000, tz=timezone.utc)


def test_all_timeframe_timestamps_are_at_or_before_decision():
    env = build_market_environment(
        bars(),
        quality=DataQuality(safe_for_trading=True, stale=False),
        decision_timestamp=_decision(),
    )
    assert all(state.timestamp <= env.decision_timestamp for state in env.timeframes.values())


def test_incomplete_one_minute_bar_is_excluded_from_environment():
    frame = bars()
    last = frame.row(-1, named=True)
    future_open = int(last["timestamp"]) + 60_000
    future = pl.DataFrame([{
        "timestamp": future_open,
        "open": last["close"],
        "high": last["close"],
        "low": last["close"],
        "close": last["close"],
        "volume": 1.0,
        "funding_rate": 0.0,
        "open_interest": 1.0,
    }], schema={column: pl.Int64 if column == "timestamp" else pl.Float64 for column in BAR_COLUMNS})
    extended = pl.concat([frame, future])
    decision = datetime.fromtimestamp((future_open + 30_000) / 1000, tz=timezone.utc)
    env = build_market_environment(
        extended,
        quality=DataQuality(safe_for_trading=True, stale=False),
        decision_timestamp=decision,
    )
    assert env.timestamp == datetime.fromtimestamp(future_open / 1000, tz=timezone.utc)
    assert all(state.timestamp <= decision for state in env.timeframes.values())

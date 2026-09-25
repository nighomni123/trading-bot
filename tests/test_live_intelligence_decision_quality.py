"""Causal-slicing, outcome, and paired-comparison tests (no provider calls)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import polars as pl

from jev_trading.contracts import BAR_COLUMNS
from jev_trading.live_intelligence.decision_quality import (
    causal_window,
    future_outcome,
    summarize,
)


def _frame(start: int, n: int):
    rows = []
    for i in range(n):
        close = 100.0 + i
        rows.append({
            "timestamp": start + i * 60_000, "open": close, "high": close + 1, "low": close - 1,
            "close": close, "volume": 1.0, "funding_rate": 0.0, "open_interest": 1.0,
        })
    return pl.DataFrame(rows, schema={c: pl.Int64 if c == "timestamp" else pl.Float64 for c in BAR_COLUMNS})


BASE = int(datetime(2023, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def test_causal_window_never_contains_future_bars():
    frame = _frame(BASE, 500)
    at = datetime.fromtimestamp((BASE + 300 * 60_000) / 1000, tz=timezone.utc)
    window = causal_window(frame, at, warmup=400)
    assert window["timestamp"].max() <= int(at.timestamp() * 1000)


def test_future_outcome_measures_only_what_follows():
    frame = _frame(BASE, 60)
    at = datetime.fromtimestamp((BASE + 10 * 60_000) / 1000, tz=timezone.utc)
    long = future_outcome(frame, at, "LONG", horizon_minutes=5)
    short = future_outcome(frame, at, "SHORT", horizon_minutes=5)
    # Rising market: long is positive, short mirrors it.
    assert long["realized_return"] > 0
    assert short["realized_return"] < 0
    assert long["mfe"] > long["mae"]
    assert long["bars_ahead"] == 5


def test_future_outcome_is_none_when_no_future_data():
    frame = _frame(BASE, 10)
    at = datetime.fromtimestamp((BASE + 9 * 60_000) / 1000, tz=timezone.utc)
    result = future_outcome(frame, at, "LONG", horizon_minutes=5)
    assert result["realized_return"] is None
    assert result["bars_ahead"] == 0


def test_summary_separates_selected_from_rejected():
    rows = [
        {"policy_eligible": True, "outcome": {"realized_return": 0.02, "mfe": 0.03, "mae": -0.001}},
        {"policy_eligible": True, "outcome": {"realized_return": 0.01, "mfe": 0.02, "mae": -0.002}},
        {"policy_eligible": False, "outcome": {"realized_return": -0.02, "mfe": 0.001, "mae": -0.03}},
    ]
    result = summarize(rows, "policy_eligible")
    assert result["total"] == 3
    assert result["selected"]["n"] == 2
    assert result["rejected"]["n"] == 1
    assert result["selected"]["mean_return"] > result["rejected"]["mean_return"]

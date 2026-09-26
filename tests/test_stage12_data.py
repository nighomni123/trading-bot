"""Stage 12 data coverage and timestamp-contract tests."""
from __future__ import annotations

import polars as pl
import pytest

from jev_trading.research.data import (
    HORIZONS, THRESHOLD_BPS, TIMESTAMP_CONTRACT, data_coverage, file_hash, load_core,
)

MINUTE_MS = 60_000


def test_horizons_are_pre_registered_and_exact():
    assert HORIZONS == {"1h": 60, "2h": 120, "4h": 240, "8h": 480,
                        "12h": 720, "24h": 1440, "48h": 2880, "72h": 4320}
    assert all(v == int(k[:-1]) * 60 for k, v in HORIZONS.items())


def test_timestamp_contract_is_explicit():
    assert TIMESTAMP_CONTRACT["entry_timestamp"] == "open[t+1]"
    assert "open[t+h+1]" in TIMESTAMP_CONTRACT["exit_timestamp"]


def test_thresholds_are_symmetric_and_pre_registered():
    assert THRESHOLD_BPS == (20, 50, 100, 200, 500)


def test_coverage_reports_per_field_and_no_blend():
    cov = data_coverage()
    assert cov["instrument"] == "BTCUSDT"
    assert cov["contract_type"] == "PERPETUAL"
    assert cov["rows"] > 2_000_000
    assert set(cov["field_coverage"]) >= {"open", "close", "funding_rate", "open_interest"}
    # Every field reports its own availability window.
    for field, info in cov["field_coverage"].items():
        assert "coverage_percentage" in info
        assert "available_from" in info
    assert cov["monotonic"] is True
    assert cov["duplicate_timestamps"] == 0


def test_coverage_flags_missing_order_book_and_short_trade_history():
    cov = data_coverage()
    assert cov["order_book_coverage"]["available"] is False
    assert "NOT multi-year" in cov["trade_flow_coverage"]["note"]


def test_load_core_is_sorted_and_utc_milliseconds(tmp_path):
    frame = load_core()
    assert frame["timestamp"].is_sorted()
    assert frame["timestamp"].min() > 1_600_000_000_000  # ms, not seconds

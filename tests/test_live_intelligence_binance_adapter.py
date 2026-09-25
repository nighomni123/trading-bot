"""Operational Binance depth, mark, and index snapshot behavior."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from jev_trading.data.normalization import BinancePerpAdapter
from tests.test_live_intelligence import T0, bars


def test_snapshot_populates_coherent_depth_mark_and_index(monkeypatch):
    adapter = BinancePerpAdapter()
    monkeypatch.setattr(adapter, "fetch_closed_bars", lambda *, limit=5: bars(5).tail(limit))
    monkeypatch.setattr(adapter, "_order_book", lambda: {
        "bid_price": 100.0, "bid_quantity": 2.0,
        "ask_price": 100.02, "ask_quantity": 3.0,
        "last_update_id": 123,
    })
    monkeypatch.setattr(adapter, "_premium_index", lambda: {
        "markPrice": "100.01", "indexPrice": "100.00", "time": T0 + 300_000,
    })
    received = datetime.fromtimestamp((T0 + 5 * 60_000) / 1000, tz=timezone.utc)
    tick = adapter.snapshot(now=received)[0]
    assert tick.bid == 100.0
    assert tick.ask == 100.02
    assert tick.mid == pytest.approx(100.01)
    assert tick.bid_depth == 2.0
    assert tick.ask_depth == 3.0
    assert tick.mark == 100.01
    assert tick.index == 100.0
    assert tick.metadata["book_last_update_id"] == 123


def test_missing_depth_remains_missing(monkeypatch):
    adapter = BinancePerpAdapter()
    monkeypatch.setattr(adapter, "fetch_closed_bars", lambda *, limit=5: bars(5).tail(limit))
    monkeypatch.setattr(adapter, "_order_book", lambda: None)
    monkeypatch.setattr(adapter, "_premium_index", lambda: None)
    received = datetime.fromtimestamp((T0 + 5 * 60_000) / 1000, tz=timezone.utc)
    tick = adapter.snapshot(now=received)[0]
    assert tick.bid is None
    assert tick.bid_depth is None
    assert tick.mark is None
    assert tick.index is None

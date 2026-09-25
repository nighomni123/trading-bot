"""Binance/Bybit live-feed normalization, sequencing, and source wiring."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import threading
import time

import polars as pl
import pytest

from jev_trading.contracts import BAR_COLUMNS
from jev_trading.data.normalization import DataFabric
from jev_trading.data.venue_adapters import (
    BINANCE_BOOK_WS,
    BINANCE_WS,
    BinanceLivePerpAdapter,
    BinanceLiveState,
    BybitPerpAdapter,
    BybitLiveState,
    RecoverableFeedError,
    WebSocketRunner,
    fetch_bybit_range,
)
from jev_trading.environment import build_market_environment
from jev_trading.live_intelligence.config import load_settings
from tests.test_live_intelligence import T0, bars, tick

MINUTE = 60_000


def at(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


def test_binance_combined_stream_normalizes_trade_flow_book_and_liquidations():
    state = BinanceLiveState()
    state.apply({"e": "24hrTicker", "E": T0, "s": "BTCUSDT", "c": "100", "o": "1200"}, 1)
    state.apply({
        "e": "depthUpdate", "E": T0, "s": "BTCUSDT", "u": 7,
        "b": [["100", "1"], ["99", "2"]],
        "a": [["101", "3"], ["102", "4"]],
    }, 1, book=True)
    state.apply({
        "e": "aggTrade", "E": T0, "T": T0, "s": "BTCUSDT",
        "a": 1, "p": "100", "q": "1.5", "m": False,
    }, 1)
    state.apply({
        "e": "aggTrade", "E": T0, "T": T0, "s": "BTCUSDT",
        "a": 2, "p": "100", "q": "0.5", "m": True,
    }, 1)
    state.apply({
        "e": "markPriceUpdate", "E": T0, "s": "BTCUSDT",
        "p": "100.1", "i": "100", "r": "0.0001",
    }, 1)
    state.apply({
        "e": "forceOrder", "E": T0, "s": "BTCUSDT",
        "o": {"S": "SELL", "q": "2", "T": T0},
    }, 1)

    fields = state.fields(at(T0 + 1_000))
    assert fields is not None
    assert fields["last"] == 100
    assert fields["bid_depth"] == 3
    assert fields["ask_depth"] == 7
    assert fields["buy_volume"] == 1.5
    assert fields["sell_volume"] == 0.5
    assert fields["mark"] == 100.1
    assert fields["open_interest"] == 1200
    assert fields["liquidation_long"] == 0
    assert fields["liquidation_short"] == 2
    assert "/market/stream" in BINANCE_WS
    assert "/public/stream" in BINANCE_BOOK_WS


def test_bybit_state_aggregates_depth_50_trades_and_liquidations():
    state = BybitLiveState()
    state.apply({"success": True, "op": "subscribe"}, 1)
    state.apply({
        "topic": "orderbook.50.BTCUSDT", "type": "snapshot", "ts": T0,
        "data": {
            "s": "BTCUSDT", "u": 10, "seq": 1,
            "b": [["100", "2"], ["99", "3"]],
            "a": [["101", "4"], ["102", "5"]],
        },
    }, 1)
    state.apply({
        "topic": "orderbook.50.BTCUSDT", "type": "delta", "ts": T0,
        "data": {
            "s": "BTCUSDT", "u": 11, "seq": 2,
            "b": [["100", "0"], ["99", "4"]],
            "a": [["101", "5"]],
        },
    }, 1)
    state.apply({
        "topic": "tickers.BTCUSDT", "type": "snapshot", "ts": T0,
        "data": {
            "symbol": "BTCUSDT", "lastPrice": "100", "markPrice": "100.1",
            "indexPrice": "100", "fundingRate": "0.0001", "openInterest": "900",
            "bid1Price": "99", "ask1Price": "101", "bid1Size": "1", "ask1Size": "1",
        },
    }, 1)
    state.apply({
        "topic": "tickers.BTCUSDT", "type": "delta", "ts": T0,
        "data": {"symbol": "BTCUSDT", "lastPrice": "100"},
    }, 1)
    state.apply({
        "topic": "publicTrade.BTCUSDT", "type": "snapshot", "ts": T0,
        "data": [
            {"T": T0, "s": "BTCUSDT", "S": "Buy", "v": "1", "p": "100", "i": "a"},
            {"T": T0, "s": "BTCUSDT", "S": "Sell", "v": "2", "p": "100", "i": "b"},
        ],
    }, 1)
    state.apply({
        "topic": "allLiquidation.BTCUSDT", "type": "snapshot", "ts": T0,
        "data": [
            {"T": T0, "s": "BTCUSDT", "S": "Buy", "v": "3", "p": "99"},
            {"T": T0, "s": "BTCUSDT", "S": "Sell", "v": "4", "p": "101"},
        ],
    }, 1)

    fields = state.fields(at(T0 + 1_000))
    assert fields is not None
    assert fields["bid"] == 99
    assert fields["ask"] == 101
    assert fields["bid_depth"] == 4
    assert fields["ask_depth"] == 10
    assert fields["buy_volume"] == 1
    assert fields["sell_volume"] == 2
    assert fields["liquidation_long"] == 3
    assert fields["liquidation_short"] == 4
    assert fields["open_interest"] == 900
    assert fields["metadata"]["depth_levels"] == 50


def test_bybit_book_gap_forces_new_snapshot_session():
    state = BybitLiveState()
    state.apply({
        "topic": "orderbook.50.BTCUSDT", "type": "snapshot", "ts": T0,
        "data": {"s": "BTCUSDT", "u": 10, "seq": 1, "b": [["100", "1"]], "a": [["101", "1"]]},
    }, 1)
    with pytest.raises(RecoverableFeedError, match="book_gap"):
        state.apply({
            "topic": "orderbook.50.BTCUSDT", "type": "delta", "ts": T0,
            "data": {"s": "BTCUSDT", "u": 12, "seq": 3, "b": [], "a": []},
        }, 1)
    state.apply({
        "topic": "orderbook.50.BTCUSDT", "type": "snapshot", "ts": T0,
        "data": {"s": "BTCUSDT", "u": 1, "seq": 4, "b": [["100", "2"]], "a": [["101", "3"]]},
    }, 2)
    fields = state.fields(at(T0 + 1_000))
    assert fields is not None
    assert fields["metadata"]["feed_generation"] == 2
    assert fields["bid_depth"] == 2
    assert fields["ask_depth"] == 3


def test_websocket_runner_reconnects_with_a_new_generation():
    class FakeSocket:
        def __init__(self, messages):
            self.messages = list(messages)
            self.sent = []

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def send(self, value):
            self.sent.append(value)

        def recv(self, timeout=None):
            if self.messages:
                return json.dumps(self.messages.pop(0))
            raise OSError("closed")

    reconnected = threading.Event()
    sockets = iter([
        FakeSocket([{"e": "ticker", "E": T0, "s": "BTCUSDT", "c": "100", "o": "1"}]),
        FakeSocket([{"e": "ticker", "E": T0, "s": "BTCUSDT", "c": "101", "o": "2"}]),
    ])

    def factory(*_args, **_kwargs):
        try:
            socket = next(sockets)
        except StopIteration:
            raise OSError("no more sockets")
        reconnected.set()
        return socket

    seen = []
    runner = WebSocketRunner(
        "wss://example.invalid", lambda payload, generation: seen.append(generation),
        connect_factory=factory, reconnect_seconds=(0.01,),
    )
    runner.start()
    assert runner.first_message.wait(1)
    assert reconnected.wait(1)
    time_limit = time.monotonic() + 1
    while len(seen) < 2 and time.monotonic() < time_limit:
        time.sleep(0.005)
    runner.close()
    assert seen[:2] == [1, 2]


def test_bybit_rest_backfill_is_point_in_time(monkeypatch):
    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def fake_get(url, params, timeout):
        if url.endswith("/kline"):
            return Response({"retCode": 0, "result": {"list": [
                [str(T0 + MINUTE), "101", "102", "100", "101", "2", "202"],
                [str(T0), "100", "101", "99", "100", "1", "100"],
            ]}})
        if url.endswith("/funding/history"):
            return Response({"retCode": 0, "result": {"list": [
                {"fundingRateTimestamp": str(T0), "fundingRate": "0.0002"},
            ]}})
        return Response({"retCode": 0, "result": {"list": [
            {"timestamp": str(T0), "openInterest": "1234"},
        ], "nextPageCursor": ""}})

    monkeypatch.setattr("jev_trading.data.venue_adapters.requests.get", fake_get)
    frame = fetch_bybit_range(T0, T0 + 2 * MINUTE)
    assert frame.columns == list(BAR_COLUMNS)
    assert frame["timestamp"].to_list() == [T0, T0 + MINUTE]
    assert frame["funding_rate"].to_list() == [0.0002, 0.0002]
    assert frame["open_interest"].to_list() == [1234.0, 1234.0]


def test_binance_bar_backfill_is_cached(monkeypatch):
    adapter = BinanceLivePerpAdapter()
    now = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    end = now - now % MINUTE
    frame = pl.DataFrame({
        "timestamp": [end - 2 * MINUTE, end - MINUTE],
        "open": [100.0, 100.5], "high": [101.0, 101.5],
        "low": [99.0, 100.0], "close": [100.5, 101.0], "volume": [1.0, 2.0],
    }, schema={"timestamp": pl.Int64, **{name: pl.Float64 for name in BAR_COLUMNS[1:6]}})
    calls = {"klines": 0}
    monkeypatch.setattr("jev_trading.data.venue_adapters.fetch_klines", lambda *args: calls.__setitem__("klines", calls["klines"] + 1) or frame)
    monkeypatch.setattr("jev_trading.data.venue_adapters.fetch_funding", lambda *args: pl.DataFrame(schema={"timestamp": pl.Int64, "funding_rate": pl.Float64}))
    monkeypatch.setattr("jev_trading.data.venue_adapters.fetch_open_interest", lambda *args: pl.DataFrame(schema={"timestamp": pl.Int64, "open_interest": pl.Float64}))
    assert adapter.fetch_closed_bars(limit=2).height == 2
    assert adapter.fetch_closed_bars(limit=2).height == 2
    assert calls["klines"] == 1


def test_optional_stale_bybit_is_visible_without_poisoning_binance():
    decision = at(T0 + 300 * MINUTE)
    primary = tick(decision)
    secondary = tick(
        decision - timedelta(seconds=60),
        event_timestamp=decision - timedelta(seconds=60),
        received_timestamp=decision - timedelta(seconds=60),
        source="bybit", source_role="secondary", venue="bybit", last=100.1,
        bid=100.0, ask=100.2, mid=100.1,
    )
    fabric = DataFabric(required_roles=("primary",))
    fabric.ingest([primary, secondary])
    quality = fabric.quality(now=decision)
    assert quality.safe_for_trading is True
    assert quality.source_health["bybit"].healthy is False
    env = build_market_environment(
        bars(), ticks=[primary, secondary], quality=quality,
        decision_timestamp=decision,
    )
    assert "binance-futures:last" in env.cross_market.observations
    assert "bybit:last" not in env.cross_market.observations


def test_bybit_adapter_and_default_config_are_wired():
    settings = load_settings()
    assert settings.market.primary_source == "binance"
    assert settings.market.secondary_source == "bybit"
    adapter = BybitPerpAdapter()
    state = adapter._state
    state.apply({
        "topic": "tickers.BTCUSDT", "type": "snapshot", "ts": T0,
        "data": {
            "symbol": "BTCUSDT", "lastPrice": "100", "markPrice": "100",
            "indexPrice": "100", "fundingRate": "0", "openInterest": "1",
            "bid1Price": "99", "ask1Price": "101", "bid1Size": "1", "ask1Size": "1",
        },
    }, 1)
    result = adapter.snapshot(now=at(T0 + 1_000))[0]
    assert result.source == "bybit"
    assert result.source_role == "secondary"
    assert result.metadata["transport"] == "websocket"

    skewed = BybitPerpAdapter()
    skewed._state.apply({
        "topic": "tickers.BTCUSDT", "type": "snapshot", "ts": T0 + 500,
        "data": {
            "symbol": "BTCUSDT", "lastPrice": "100", "markPrice": "100",
            "indexPrice": "100", "fundingRate": "0", "openInterest": "1",
            "bid1Price": "99", "ask1Price": "101", "bid1Size": "1", "ask1Size": "1",
        },
    }, 1)
    normalized = skewed.snapshot(now=at(T0))[0]
    assert normalized.event_timestamp == at(T0)
    assert normalized.metadata["source_event_timestamp"] == at(T0 + 500).isoformat()

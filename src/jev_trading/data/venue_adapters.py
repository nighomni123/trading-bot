"""Binance/Bybit REST backfill and public-WebSocket live adapters."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
import random
import threading
import time
from threading import Lock
from typing import Any, Callable

import polars as pl
import requests
from websockets.sync.client import connect

from jev_trading.data.fetch import (
    DAY_MS,
    MINUTE_MS,
    fetch_funding,
    fetch_klines,
    fetch_open_interest,
    merge_enrichment,
)
from jev_trading.data.normalization import BinancePerpAdapter, MarketDataAdapter
from jev_trading.live_intelligence.schemas import (
    DataEventType,
    DataQuality,
    MarketTick,
    MarketType,
    SourceHealth,
)

BINANCE_WS = (
    "wss://fstream.binance.com/market/stream?streams="
    "btcusdt@aggTrade/btcusdt@ticker/btcusdt@markPrice@1s/btcusdt@forceOrder"
)
BINANCE_BOOK_WS = (
    "wss://fstream.binance.com/public/stream?streams=btcusdt@depth5@100ms"
)
BYBIT_WS = "wss://stream.bybit.com/v5/public/linear"
BYBIT_REST = "https://api.bybit.com"
MINUTE = 60_000


class RecoverableFeedError(RuntimeError):
    """A sequence/reset error that requires a fresh WebSocket session."""


def _utc(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


def _positive(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _nonnegative(value: Any) -> float | None:
    parsed = _number(value)
    return parsed if parsed is not None and parsed >= 0 else None


def _minute(value: int) -> int:
    return value - value % MINUTE


def _trade(
    buckets: dict[int, list[float]], timestamp: int, side: str, size: float
) -> None:
    values = buckets.setdefault(_minute(timestamp), [0.0, 0.0])
    values[0 if side == "buy" else 1] += size


def _bucket_values(
    buckets: dict[int, list[float]], timestamp: int
) -> tuple[float, float]:
    buy, sell = buckets.get(_minute(timestamp), (0.0, 0.0))
    return buy, sell


def _prune(buckets: dict[int, list[float]], timestamp: int) -> None:
    for key in tuple(buckets):
        if key < timestamp - 2 * MINUTE:
            del buckets[key]


class WebSocketRunner:
    """Persistent WebSocket with heartbeat, backoff, and planned 23h rotation."""

    def __init__(
        self,
        url: str,
        on_message: Callable[[dict[str, Any], int], None],
        *,
        subscribe: Callable[[Any], None] | None = None,
        heartbeat: Callable[[Any], None] | None = None,
        connect_factory: Callable[..., Any] = connect,
        reconnect_seconds: tuple[float, ...] = (1, 2, 4, 8, 15, 30),
    ) -> None:
        self.url = url
        self.on_message = on_message
        self.subscribe = subscribe
        self.heartbeat = heartbeat
        self.connect_factory = connect_factory
        self.reconnect_seconds = reconnect_seconds
        self.connected = False
        self.generation = 0
        self.last_error: str | None = None
        self.first_message = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="jev-public-websocket", daemon=True
        )
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)

    def _run(self) -> None:
        attempt = 0
        delays = self.reconnect_seconds
        while not self._stop.is_set():
            opened = time.monotonic()
            try:
                with self.connect_factory(
                    self.url, open_timeout=10, close_timeout=5, max_size=2**20
                ) as socket:
                    opened = time.monotonic()
                    self.generation += 1
                    generation = self.generation
                    self.connected = True
                    self.last_error = None
                    if self.subscribe:
                        self.subscribe(socket)
                    next_ping = opened + 20
                    while not self._stop.is_set():
                        if time.monotonic() - opened >= 23 * 60 * 60:
                            self.last_error = "scheduled_rotation"
                            break
                        if self.heartbeat and time.monotonic() >= next_ping:
                            self.heartbeat(socket)
                            next_ping = time.monotonic() + 20
                        try:
                            raw = socket.recv(timeout=1)
                        except TimeoutError:
                            continue
                        self.first_message.set()
                        try:
                            payload = json.loads(raw)
                        except (TypeError, json.JSONDecodeError):
                            continue
                        if isinstance(payload, dict):
                            self.on_message(payload, generation)
            except RecoverableFeedError as exc:
                self.last_error = str(exc)
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
            finally:
                self.connected = False
            if self._stop.is_set():
                return
            if time.monotonic() - opened >= 60:
                attempt = 0
            self._stop.wait(delays[min(attempt, len(delays) - 1)] + random.uniform(0, 0.25))
            attempt += 1


@dataclass
class BinanceLiveState:
    symbol: str = "BTCUSDT"
    generation: int = 0
    event_ms: int | None = None
    last: float | None = None
    bid: float | None = None
    ask: float | None = None
    bid_depth: float | None = None
    ask_depth: float | None = None
    bid_notional: float | None = None
    ask_notional: float | None = None
    book_update_id: int | None = None
    book_event_ms: int | None = None
    mark: float | None = None
    index: float | None = None
    funding_rate: float | None = None
    open_interest: float | None = None
    trade_active: bool = False
    liquidation_active: bool = False
    last_trade_id: int | None = None
    trades: dict[int, list[float]] = field(default_factory=dict)
    liquidations: dict[int, list[float]] = field(default_factory=dict)

    def _generation(self, value: int) -> None:
        if value == self.generation:
            return
        self.generation = value
        self.event_ms = None
        self.last = self.bid = self.ask = None
        self.bid_depth = self.ask_depth = self.bid_notional = self.ask_notional = None
        self.book_update_id = self.book_event_ms = None
        self.mark = self.index = self.funding_rate = None
        self.open_interest = self.last_trade_id = None
        self.trade_active = self.liquidation_active = False
        self.trades.clear()
        self.liquidations.clear()

    def apply(
        self, payload: dict[str, Any], generation: int, *, book: bool = False
    ) -> None:
        if not book:
            self._generation(generation)
        event = payload.get("data", payload)
        if not isinstance(event, dict) or event.get("s") != self.symbol:
            return
        kind = event.get("e")
        timestamp = int(event.get("E", event.get("T", 0)))
        if kind == "aggTrade":
            self.last = _positive(event.get("p")) or self.last
            _trade(
                self.trades,
                int(event.get("T", timestamp)),
                "sell" if event.get("m") else "buy",
                _nonnegative(event.get("q")) or 0.0,
            )
            self.trade_active = True
            self.last_trade_id = int(event["a"]) if event.get("a") is not None else None
        elif kind == "depthUpdate":
            bids, asks = event.get("b") or [], event.get("a") or []
            if bids and asks:
                self.bid, self.ask = _positive(bids[0][0]), _positive(asks[0][0])
                self.bid_depth = sum(_nonnegative(row[1]) or 0 for row in bids[:5])
                self.ask_depth = sum(_nonnegative(row[1]) or 0 for row in asks[:5])
                self.bid_notional = sum(
                    (_positive(row[0]) or 0) * (_nonnegative(row[1]) or 0)
                    for row in bids[:5]
                )
                self.ask_notional = sum(
                    (_positive(row[0]) or 0) * (_nonnegative(row[1]) or 0)
                    for row in asks[:5]
                )
                self.book_update_id = int(event["u"]) if event.get("u") is not None else None
                self.book_event_ms = timestamp or None
        elif kind in {"ticker", "24hrTicker"}:
            self.last = _positive(event.get("c")) or self.last
            self.open_interest = _nonnegative(event.get("o"))
        elif kind in {"markPrice", "markPriceUpdate"}:
            self.mark, self.index = _positive(event.get("p")), _positive(event.get("i"))
            self.funding_rate = _number(event.get("r"))
        elif kind == "forceOrder":
            order = event.get("o") or {}
            at = int(order.get("T", timestamp))
            side = 0 if order.get("S") == "BUY" else 1
            values = self.liquidations.setdefault(_minute(at), [0.0, 0.0])
            values[side] += _nonnegative(order.get("q")) or 0.0
            self.liquidation_active = True
        if timestamp:
            self.event_ms = max(self.event_ms or timestamp, timestamp)

    def fields(self, now: datetime, max_age_ms: int = 15_000) -> dict[str, Any] | None:
        if not self.generation or self.event_ms is None:
            return None
        age = int(now.timestamp() * 1000) - self.event_ms
        if age < 0 or age > max_age_ms:
            return None
        _prune(self.trades, self.event_ms)
        _prune(self.liquidations, self.event_ms)
        buy, sell = _bucket_values(self.trades, self.event_ms)
        long_liq, short_liq = _bucket_values(self.liquidations, self.event_ms)
        book_age = (
            int(now.timestamp() * 1000) - self.book_event_ms
            if self.book_event_ms is not None
            else None
        )
        book_fresh = book_age is not None and -1_000 <= book_age <= max_age_ms
        bid = self.bid if book_fresh else None
        ask = self.ask if book_fresh else None
        bid_depth = self.bid_depth if book_fresh else None
        ask_depth = self.ask_depth if book_fresh else None
        bid_notional = self.bid_notional if book_fresh else None
        ask_notional = self.ask_notional if book_fresh else None
        return {
            "event_timestamp": _utc(self.event_ms),
            "last": self.last,
            "bid": bid,
            "ask": ask,
            "bid_depth": bid_depth,
            "ask_depth": ask_depth,
            "mid": (bid + ask) / 2 if bid and ask else None,
            "mark": self.mark,
            "index": self.index,
            "open_interest": self.open_interest,
            "funding_rate": self.funding_rate,
            "buy_volume": buy if self.trade_active else None,
            "sell_volume": sell if self.trade_active else None,
            "liquidation_long": long_liq if self.liquidation_active else None,
            "liquidation_short": short_liq if self.liquidation_active else None,
            "metadata": {
                "feed_generation": self.generation,
                "transport": "websocket",
                "depth_levels": 5 if book_fresh else 0,
                "book_last_update_id": self.book_update_id if book_fresh else None,
                "last_trade_id": self.last_trade_id,
                "bid_depth_notional": bid_notional,
                "ask_depth_notional": ask_notional,
                "trade_stream_active": self.trade_active,
                "liquidation_stream_active": self.liquidation_active,
            },
        }


@dataclass
class BybitLiveState:
    symbol: str = "BTCUSDT"
    generation: int = 0
    event_ms: int | None = None
    last: float | None = None
    mark: float | None = None
    index: float | None = None
    funding_rate: float | None = None
    open_interest: float | None = None
    ticker_bid: float | None = None
    ticker_ask: float | None = None
    ticker_bid_size: float | None = None
    ticker_ask_size: float | None = None
    bids: dict[float, float] = field(default_factory=dict)
    asks: dict[float, float] = field(default_factory=dict)
    book_update_id: int | None = None
    book_sequence: int | None = None
    last_trade_id: str | None = None
    trades: dict[int, list[float]] = field(default_factory=dict)
    liquidations: dict[int, list[float]] = field(default_factory=dict)
    trade_active: bool = False
    liquidation_active: bool = False
    book_active: bool = False

    def _generation(self, value: int) -> None:
        if value == self.generation:
            return
        self.generation = value
        self.event_ms = None
        self.last = self.mark = self.index = self.funding_rate = None
        self.open_interest = self.ticker_bid = self.ticker_ask = None
        self.ticker_bid_size = self.ticker_ask_size = None
        self.bids.clear()
        self.asks.clear()
        self.book_update_id = self.book_sequence = self.last_trade_id = None
        self.trades.clear()
        self.liquidations.clear()
        self.trade_active = self.liquidation_active = self.book_active = False

    @staticmethod
    def _levels(book: dict[float, float], rows: list[Any]) -> None:
        for row in rows:
            if not isinstance(row, list) or len(row) < 2:
                continue
            price, size = _positive(row[0]), _nonnegative(row[1])
            if price is None or size is None:
                continue
            if size == 0:
                book.pop(price, None)
            else:
                book[price] = size

    def _book(self) -> tuple[float | None, float | None, float | None, float | None, float | None, float | None]:
        if not self.bids or not self.asks:
            return None, None, None, None, None, None
        bid, ask = max(self.bids), min(self.asks)
        if bid > ask:
            return None, None, None, None, None, None
        bids = sorted(self.bids.items(), reverse=True)[:50]
        asks = sorted(self.asks.items())[:50]
        return (
            bid,
            ask,
            sum(size for _, size in bids),
            sum(size for _, size in asks),
            sum(price * size for price, size in bids),
            sum(price * size for price, size in asks),
        )

    def apply(self, payload: dict[str, Any], generation: int) -> None:
        self._generation(generation)
        if payload.get("success") is False:
            raise RecoverableFeedError(f"bybit_error:{payload.get('ret_msg', 'unknown')}")
        if payload.get("op") == "subscribe" and payload.get("success") is True:
            failed = (payload.get("data") or {}).get("failTopics") or []
            if failed:
                raise RecoverableFeedError(f"bybit_subscribe_failed:{failed}")
            self.trade_active = self.liquidation_active = self.book_active = True
            return
        topic = payload.get("topic", "")
        data = payload.get("data")
        timestamp = int(payload.get("ts", payload.get("cts", 0)))
        if topic == f"tickers.{self.symbol}" and isinstance(data, dict):
            self.last = _positive(data.get("lastPrice")) or self.last
            self.mark = _positive(data.get("markPrice")) or self.mark
            self.index = _positive(data.get("indexPrice")) or self.index
            if "fundingRate" in data:
                self.funding_rate = _number(data.get("fundingRate"))
            if "openInterest" in data:
                self.open_interest = _nonnegative(data.get("openInterest"))
            self.ticker_bid = _positive(data.get("bid1Price")) or self.ticker_bid
            self.ticker_ask = _positive(data.get("ask1Price")) or self.ticker_ask
            if "bid1Size" in data:
                self.ticker_bid_size = _nonnegative(data.get("bid1Size"))
            if "ask1Size" in data:
                self.ticker_ask_size = _nonnegative(data.get("ask1Size"))
        elif topic == f"publicTrade.{self.symbol}" and isinstance(data, list):
            for item in data:
                if not isinstance(item, dict) or item.get("s") != self.symbol:
                    continue
                at = int(item.get("T", timestamp))
                self.last = _positive(item.get("p")) or self.last
                _trade(
                    self.trades,
                    at,
                    "buy" if item.get("S") == "Buy" else "sell",
                    _nonnegative(item.get("v")) or 0.0,
                )
                self.trade_active = True
                self.last_trade_id = str(item.get("i")) if item.get("i") else self.last_trade_id
                timestamp = max(timestamp, at)
        elif topic == f"allLiquidation.{self.symbol}" and isinstance(data, (list, dict)):
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict) or item.get("s") != self.symbol:
                    continue
                at = int(item.get("T", timestamp))
                side = 0 if item.get("S") == "Buy" else 1
                values = self.liquidations.setdefault(_minute(at), [0.0, 0.0])
                values[side] += _nonnegative(item.get("v")) or 0.0
                self.liquidation_active = True
                timestamp = max(timestamp, at)
        elif topic == f"orderbook.50.{self.symbol}" and isinstance(data, dict):
            update = int(data["u"]) if data.get("u") is not None else None
            snapshot = payload.get("type") == "snapshot"
            if snapshot or update == 1:
                self.bids.clear()
                self.asks.clear()
            elif self.book_update_id is None:
                raise RecoverableFeedError("bybit_book_delta_without_snapshot")
            if update is not None and not snapshot:
                if update <= self.book_update_id:
                    return
                if update != self.book_update_id + 1:
                    raise RecoverableFeedError(
                        f"bybit_book_gap:{self.book_update_id}->{update}"
                    )
            self._levels(self.bids, data.get("b") or [])
            self._levels(self.asks, data.get("a") or [])
            self.bids = dict(sorted(self.bids.items(), reverse=True)[:50])
            self.asks = dict(sorted(self.asks.items())[:50])
            self.book_update_id = update
            self.book_sequence = int(data["seq"]) if data.get("seq") is not None else None
            self.book_active = True
        if timestamp:
            self.event_ms = max(self.event_ms or timestamp, timestamp)

    def fields(self, now: datetime, max_age_ms: int = 15_000) -> dict[str, Any] | None:
        if not self.generation or self.event_ms is None:
            return None
        age = int(now.timestamp() * 1000) - self.event_ms
        if age < 0 or age > max_age_ms:
            return None
        _prune(self.trades, self.event_ms)
        _prune(self.liquidations, self.event_ms)
        buy, sell = _bucket_values(self.trades, self.event_ms)
        long_liq, short_liq = _bucket_values(self.liquidations, self.event_ms)
        bid, ask, bid_depth, ask_depth, bid_notional, ask_notional = self._book()
        if bid is None:
            bid, ask = self.ticker_bid, self.ticker_ask
            bid_depth, ask_depth = self.ticker_bid_size, self.ticker_ask_size
        return {
            "event_timestamp": _utc(self.event_ms),
            "last": self.last,
            "bid": bid,
            "ask": ask,
            "bid_depth": bid_depth,
            "ask_depth": ask_depth,
            "mid": (bid + ask) / 2 if bid and ask else None,
            "mark": self.mark,
            "index": self.index,
            "open_interest": self.open_interest,
            "funding_rate": self.funding_rate,
            "buy_volume": buy if self.trade_active else None,
            "sell_volume": sell if self.trade_active else None,
            "liquidation_long": long_liq if self.liquidation_active else None,
            "liquidation_short": short_liq if self.liquidation_active else None,
            "metadata": {
                "feed_generation": self.generation,
                "transport": "websocket",
                "depth_levels": 50 if self.book_update_id is not None else 1,
                "book_last_update_id": self.book_update_id,
                "book_sequence": self.book_sequence,
                "last_trade_id": self.last_trade_id,
                "bid_depth_notional": bid_notional,
                "ask_depth_notional": ask_notional,
                "trade_stream_active": self.trade_active,
                "liquidation_stream_active": self.liquidation_active,
                "orderbook_stream_active": self.book_active,
            },
        }


def _bybit_subscribe(socket: Any) -> None:
    socket.send(json.dumps({
        "req_id": "jev-btcusdt",
        "op": "subscribe",
        "args": [
            "tickers.BTCUSDT",
            "orderbook.50.BTCUSDT",
            "publicTrade.BTCUSDT",
            "allLiquidation.BTCUSDT",
        ],
    }))


def _bybit_ping(socket: Any) -> None:
    socket.send(json.dumps({"req_id": "jev-ping", "op": "ping"}))


def _bybit_get(path: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
    response = requests.get(f"{BYBIT_REST}{path}", params=params, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("retCode") != 0:
        raise requests.RequestException(f"Bybit error: {payload}")
    return payload


def fetch_bybit_range(
    start_ms: int,
    end_ms: int,
    *,
    symbol: str = "BTCUSDT",
    timeout: float = 30,
    enrich: bool = True,
) -> pl.DataFrame:
    kline_rows: list[list[str]] = []
    cursor = start_ms
    while cursor < end_ms:
        payload = _bybit_get("/v5/market/kline", {
            "category": "linear", "symbol": symbol, "interval": "60",
            "start": cursor, "end": end_ms - 1, "limit": 1000,
        }, timeout)
        page = payload.get("result", {}).get("list") or []
        if not page:
            break
        kline_rows.extend(page)
        latest = max(int(row[0]) for row in page)
        if latest < cursor or (latest < end_ms - 1 and len(page) < 1000):
            break
        cursor = latest + MINUTE
    rows = [row for row in kline_rows if start_ms <= int(row[0]) < end_ms]
    if not rows:
        return pl.DataFrame(schema={
            "timestamp": pl.Int64, "open": pl.Float64, "high": pl.Float64,
            "low": pl.Float64, "close": pl.Float64, "volume": pl.Float64,
        })
    bars = pl.DataFrame({
        "timestamp": [int(row[0]) for row in rows],
        "open": [float(row[1]) for row in rows],
        "high": [float(row[2]) for row in rows],
        "low": [float(row[3]) for row in rows],
        "close": [float(row[4]) for row in rows],
        "volume": [float(row[5]) for row in rows],
    }).unique(subset=["timestamp"], keep="last").sort("timestamp")
    if not enrich:
        return bars.with_columns(
            pl.lit(None, dtype=pl.Float64).alias("funding_rate"),
            pl.lit(None, dtype=pl.Float64).alias("open_interest"),
        )
    funding_rows: list[dict[str, Any]] = []
    cursor = end_ms
    while cursor > start_ms - 2 * DAY_MS:
        payload = _bybit_get("/v5/market/funding/history", {
            "category": "linear", "symbol": symbol,
            "startTime": start_ms - 2 * DAY_MS, "endTime": cursor, "limit": 200,
        }, timeout)
        page = payload.get("result", {}).get("list") or []
        if not page:
            break
        funding_rows.extend(page)
        oldest = min(int(row["fundingRateTimestamp"]) for row in page)
        if oldest <= start_ms - 2 * DAY_MS or len(page) < 200 or oldest >= cursor:
            break
        cursor = oldest - 1
    funding = pl.DataFrame({
        "timestamp": [int(row["fundingRateTimestamp"]) for row in funding_rows],
        "funding_rate": [float(row["fundingRate"]) for row in funding_rows],
    }, schema={"timestamp": pl.Int64, "funding_rate": pl.Float64}).unique(
        subset=["timestamp"], keep="last"
    ).sort("timestamp")
    oi_rows: list[dict[str, Any]] = []
    cursor = None
    seen: set[str] = set()
    while True:
        params: dict[str, Any] = {
            "category": "linear", "symbol": symbol, "intervalTime": "5min",
            "startTime": start_ms - 2 * DAY_MS, "endTime": end_ms - 1, "limit": 200,
        }
        if cursor:
            params["cursor"] = cursor
        payload = _bybit_get("/v5/market/open-interest", params, timeout)
        result = payload.get("result", {})
        page = result.get("list") or []
        oi_rows.extend(page)
        cursor = result.get("nextPageCursor") or ""
        if not page or not cursor or cursor in seen:
            break
        seen.add(cursor)
    oi = pl.DataFrame({
        "timestamp": [int(row["timestamp"]) for row in oi_rows],
        "open_interest": [float(row["openInterest"]) for row in oi_rows],
    }, schema={"timestamp": pl.Int64, "open_interest": pl.Float64}).unique(
        subset=["timestamp"], keep="last"
    ).sort("timestamp")
    return merge_enrichment(bars, funding, oi)


def _merge_bars(existing: pl.DataFrame | None, fresh: pl.DataFrame) -> pl.DataFrame:
    if fresh.is_empty():
        return existing if existing is not None else fresh
    if existing is None or existing.is_empty():
        return fresh
    return pl.concat([existing, fresh], how="vertical").unique(
        subset=["timestamp"], keep="last"
    ).sort("timestamp")


def _live_tick(adapter: Any, fields: dict[str, Any], received: datetime) -> MarketTick:
    return MarketTick(
        source=adapter.source,
        source_role=adapter.source_role,
        instrument=adapter.instrument,
        venue=adapter.venue,
        market_type=adapter.market_type,
        received_timestamp=received,
        event_type=DataEventType.TICK,
        **fields,
    )


def _live_health(source: str, role: str, fields: dict[str, Any], now: datetime) -> DataQuality:
    event = fields["event_timestamp"]
    age = int((now - event).total_seconds() * 1000)
    healthy = 0 <= age <= 15_000
    return DataQuality(
        safe_for_trading=healthy, stale=not healthy, timestamp_lag_ms=max(0, age),
        source_health={source: SourceHealth(
            source=source, role=role, healthy=healthy,
            last_event_timestamp=event, last_received_timestamp=now, age_ms=max(0, age),
        )},
    )


@dataclass
class BinanceLivePerpAdapter(BinancePerpAdapter):
    source_role: str = "primary"
    _bars: pl.DataFrame | None = field(default=None, init=False)
    _bar_lock: Lock = field(default_factory=Lock, init=False)
    _live_seen: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self._state = BinanceLiveState(self.instrument.removesuffix("_PERP"))
        self._websocket = WebSocketRunner(BINANCE_WS, self._state.apply)
        self._book_websocket = WebSocketRunner(
            BINANCE_BOOK_WS,
            lambda payload, generation: self._state.apply(
                payload, generation, book=True
            ),
        )

    def start(self) -> None:
        self._websocket.start()
        self._book_websocket.start()

    def close(self) -> None:
        self._websocket.close()
        self._book_websocket.close()

    def _rest_bars(self, start: int, end: int, enrich: bool) -> pl.DataFrame:
        symbol = self.instrument.removesuffix("_PERP")
        bars = fetch_klines(start, end, symbol)
        if bars.is_empty():
            return bars
        if not enrich:
            return bars.with_columns(
                pl.lit(None, dtype=pl.Float64).alias("funding_rate"),
                pl.lit(None, dtype=pl.Float64).alias("open_interest"),
            )
        return merge_enrichment(
            bars,
            fetch_funding(start - 2 * DAY_MS, end, symbol),
            fetch_open_interest(start - 2 * DAY_MS, end, symbol),
        )

    def fetch_closed_bars(self, *, limit: int = 3000) -> pl.DataFrame:
        if limit < 1:
            raise ValueError("limit must be positive")
        with self._bar_lock:
            now = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
            end = now - now % MINUTE
            if self._bars is None or self._bars.height < limit:
                start, enrich = end - limit * MINUTE, True
            else:
                latest = int(self._bars["timestamp"][-1])
                if latest + MINUTE >= end:
                    return self._bars.tail(limit)
                start, enrich = latest + MINUTE, False
            self._bars = _merge_bars(self._bars, self._rest_bars(start, end, enrich))
            return self._bars.tail(limit)

    def snapshot(self, *, now: datetime | None = None) -> list[MarketTick]:
        received = now or datetime.now(tz=timezone.utc)
        fields = self._state.fields(received)
        if fields is not None:
            if fields["event_timestamp"] > received:
                return []
            self._live_seen = True
            return [_live_tick(self, fields, received)]
        return [] if self._live_seen else super().snapshot(now=received)

    def health(self, *, now: datetime | None = None) -> DataQuality:
        current = now or datetime.now(tz=timezone.utc)
        fields = self._state.fields(current)
        if fields is not None:
            return _live_health(self.source, self.source_role, fields, current)
        if self._live_seen or (self._state.generation and self._state.event_ms is not None):
            return DataQuality(
                safe_for_trading=False, stale=True, missing_sources=(self.source,),
                source_health={self.source: SourceHealth(
                    source=self.source, role=self.source_role, healthy=False,
                    reason="websocket_stale",
                )},
            )
        return super().health(now=current)


@dataclass
class BybitPerpAdapter:
    source: str = "bybit"
    venue: str = "bybit"
    instrument: str = "BTCUSDT_PERP"
    market_type: MarketType = MarketType.PERPETUAL
    source_role: str = "secondary"
    timeout_seconds: float = 30.0
    _bars: pl.DataFrame | None = field(default=None, init=False)
    _bar_lock: Lock = field(default_factory=Lock, init=False)
    _live_seen: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self._state = BybitLiveState(self.instrument.removesuffix("_PERP"))
        self._websocket = WebSocketRunner(
            BYBIT_WS, self._state.apply, subscribe=_bybit_subscribe,
            heartbeat=_bybit_ping,
        )

    def start(self) -> None:
        self._websocket.start()

    def close(self) -> None:
        self._websocket.close()

    def fetch_closed_bars(self, *, limit: int = 3000) -> pl.DataFrame:
        if limit < 1:
            raise ValueError("limit must be positive")
        with self._bar_lock:
            now = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
            end = now - now % MINUTE
            if self._bars is None or self._bars.height < limit:
                start, enrich = end - limit * MINUTE, True
            else:
                latest = int(self._bars["timestamp"][-1])
                if latest + MINUTE >= end:
                    return self._bars.tail(limit)
                start, enrich = latest + MINUTE, False
            self._bars = _merge_bars(
                self._bars,
                fetch_bybit_range(
                    start, end, symbol=self.instrument.removesuffix("_PERP"),
                    timeout=self.timeout_seconds, enrich=enrich,
                ),
            )
            return self._bars.tail(limit)

    def fetch_ohlcv(self, *, limit: int = 3000) -> pl.DataFrame:
        return self.fetch_closed_bars(limit=limit)

    def _ticker(self) -> dict[str, Any] | None:
        try:
            response = requests.get(
                f"{BYBIT_REST}/v5/market/tickers",
                params={"category": "linear", "symbol": self.instrument.removesuffix("_PERP")},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            values = payload.get("result", {}).get("list") or []
            if payload.get("retCode") != 0 or not values:
                return None
            return {**values[0], "_server_time": payload.get("time")}
        except (requests.RequestException, AttributeError, TypeError, ValueError):
            return None

    def snapshot(self, *, now: datetime | None = None) -> list[MarketTick]:
        received = now or datetime.now(tz=timezone.utc)
        fields = self._state.fields(received)
        if fields is not None:
            if fields["event_timestamp"] > received:
                return []
            self._live_seen = True
            return [_live_tick(self, fields, received)]
        if self._live_seen:
            return []
        ticker = self._ticker()
        if ticker is None:
            return []
        try:
            event = _utc(int(ticker["_server_time"]))
            bid, ask = float(ticker["bid1Price"]), float(ticker["ask1Price"])
            if event > received or bid <= 0 or ask <= 0 or bid > ask:
                return []
            return [MarketTick(
                source=self.source, source_role=self.source_role,
                instrument=self.instrument, venue=self.venue,
                market_type=self.market_type, event_timestamp=event,
                received_timestamp=received, event_type=DataEventType.TICK,
                last=float(ticker["lastPrice"]), bid=bid, ask=ask,
                mid=(bid + ask) / 2, mark=float(ticker["markPrice"]),
                index=float(ticker["indexPrice"]),
                bid_depth=float(ticker["bid1Size"]), ask_depth=float(ticker["ask1Size"]),
                open_interest=float(ticker["openInterest"]),
                funding_rate=float(ticker["fundingRate"]),
                metadata={"transport": "rest-bootstrap", "depth_levels": 1},
            )]
        except (KeyError, TypeError, ValueError):
            return []

    def health(self, *, now: datetime | None = None) -> DataQuality:
        current = now or datetime.now(tz=timezone.utc)
        fields = self._state.fields(current)
        if fields is not None:
            return _live_health(self.source, self.source_role, fields, current)
        if self._live_seen or (self._state.generation and self._state.event_ms is not None):
            return DataQuality(
                safe_for_trading=False, stale=True, missing_sources=(self.source,),
            )
        try:
            bars = self.fetch_closed_bars(limit=3)
            latest = int(bars["timestamp"][-1]) + MINUTE
        except Exception:
            return DataQuality(
                safe_for_trading=False, stale=True, missing_sources=(self.source,)
            )
        if bars.is_empty():
            return DataQuality(
                safe_for_trading=False, stale=True, missing_sources=(self.source,)
            )
        age = max(0, int(current.timestamp() * 1000) - latest)
        healthy = age <= 15_000
        return DataQuality(
            safe_for_trading=healthy, stale=not healthy, timestamp_lag_ms=age,
            source_health={self.source: SourceHealth(
                source=self.source, role=self.source_role, healthy=healthy,
                last_event_timestamp=_utc(latest), last_received_timestamp=current,
                age_ms=age,
            )},
        )


@dataclass
class CompositeMarketDataAdapter:
    primary: MarketDataAdapter
    secondary: tuple[MarketDataAdapter, ...] = ()

    @property
    def source(self) -> str:
        return self.primary.source

    @property
    def venue(self) -> str:
        return self.primary.venue

    @property
    def instrument(self) -> str:
        return self.primary.instrument

    def start(self) -> None:
        for adapter in (self.primary, *self.secondary):
            if hasattr(adapter, "start"):
                adapter.start()

    def close(self) -> None:
        for adapter in (self.primary, *self.secondary):
            if hasattr(adapter, "close"):
                adapter.close()

    def fetch_closed_bars(self, *, limit: int = 3000) -> pl.DataFrame:
        return self.primary.fetch_closed_bars(limit=limit)

    def snapshot(self, *, now: datetime | None = None) -> list[MarketTick]:
        ticks = list(self.primary.snapshot(now=now))
        for adapter in self.secondary:
            try:
                ticks.extend(adapter.snapshot(now=now))
            except Exception as exc:
                print(f"WARN: {adapter.source} snapshot unavailable: {type(exc).__name__}")
        return ticks

    def health(self, *, now: datetime | None = None) -> DataQuality:
        return self.primary.health(now=now)

"""Live market-data adapters and source-aware normalization.

The active loop only consumes closed bars and explicit source observations. It
never averages venues or silently fills unavailable order-book/flow fields.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol
import math

import polars as pl
import requests

from jev_trading.contracts import BAR_COLUMNS, INSTRUMENT
from jev_trading.data.fetch import (
    KLINES_URL,
    fetch_funding,
    fetch_klines,
    fetch_open_interest,
    merge_enrichment,
    MINUTE_MS,
)
from jev_trading.environment.features import TIMEFRAME_MS
from jev_trading.live_intelligence.schemas import (
    DataEventType,
    DataQuality,
    MarketTick,
    MarketType,
    SourceHealth,
)


def utc_datetime(timestamp_ms: int) -> datetime:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)


class MarketDataAdapter(Protocol):
    source: str
    venue: str
    instrument: str

    def fetch_closed_bars(self, *, limit: int = 3000) -> pl.DataFrame: ...

    def snapshot(self, *, now: datetime | None = None) -> list[MarketTick]: ...

    def health(self, *, now: datetime | None = None) -> DataQuality: ...


@dataclass
class BinancePerpAdapter:
    """Public Binance USDT-perpetual adapter; credentials are not required."""

    source: str = "binance"
    venue: str = "binance-futures"
    instrument: str = INSTRUMENT
    market_type: MarketType = MarketType.PERPETUAL
    timeout_seconds: float = 30.0
    _last_sequence: int | None = field(default=None, init=False)

    def fetch_closed_bars(self, *, limit: int = 3000) -> pl.DataFrame:
        if limit < 1:
            raise ValueError("limit must be positive")
        now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        end_ms = now_ms - (now_ms % MINUTE_MS)
        start_ms = end_ms - limit * MINUTE_MS
        bars = fetch_klines(start_ms, end_ms, self.instrument.removesuffix("_PERP"))
        if bars.is_empty():
            return bars
        funding = fetch_funding(start_ms - 2 * 86_400_000, end_ms, self.instrument.removesuffix("_PERP"))
        oi = fetch_open_interest(start_ms - 2 * 86_400_000, end_ms, self.instrument.removesuffix("_PERP"))
        return merge_enrichment(bars, funding, oi)

    def fetch_ohlcv(self, *, limit: int = 3000) -> pl.DataFrame:
        """Canonical OHLCV accessor used by environment/replay callers."""
        return self.fetch_closed_bars(limit=limit)

    def _order_book(self) -> dict | None:
        """Return best bid/ask prices and quantities from public depth."""
        try:
            response = requests.get(
                KLINES_URL.replace("/klines", "/depth"),
                params={"symbol": self.instrument.removesuffix("_PERP"), "limit": 5},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            bids = payload.get("bids") or []
            asks = payload.get("asks") or []
            if not bids or not asks:
                return None
            return {
                "bid_price": float(bids[0][0]), "bid_quantity": float(bids[0][1]),
                "ask_price": float(asks[0][0]), "ask_quantity": float(asks[0][1]),
                "last_update_id": payload.get("lastUpdateId"),
            }
        except (requests.RequestException, TypeError, ValueError):
            return None

    def _premium_index(self) -> dict | None:
        try:
            response = requests.get(
                KLINES_URL.replace("/klines", "/premiumIndex"),
                params={"symbol": self.instrument.removesuffix("_PERP")},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, dict) else None
        except (requests.RequestException, ValueError):
            return None

    def snapshot(self, *, now: datetime | None = None) -> list[MarketTick]:
        received = now or datetime.now(tz=timezone.utc)
        bars = self.fetch_closed_bars(limit=5)
        if bars.is_empty():
            return []
        row = bars.row(-1, named=True)
        event_ms = int(row["timestamp"]) + MINUTE_MS
        event_time = utc_datetime(event_ms)
        if event_time > received:
            # A clock skew must not make a future bar visible.
            return []
        result = MarketTick(
            source=self.source,
            source_role="primary",
            instrument=self.instrument,
            venue=self.venue,
            market_type=self.market_type,
            event_timestamp=event_time,
            received_timestamp=received,
            event_type=DataEventType.BAR,
            last=float(row["close"]),
            mark=None,
            volume=float(row["volume"]),
            open_interest=float(row["open_interest"]) if row.get("open_interest") is not None else None,
            funding_rate=float(row["funding_rate"]) if row.get("funding_rate") is not None else None,
            metadata={"bar_open_timestamp": int(row["timestamp"])},
        )
        premium = self._premium_index()
        if premium and premium.get("markPrice") is not None and premium.get("indexPrice") is not None:
            result = result.model_copy(update={
                "mark": float(premium["markPrice"]),
                "index": float(premium["indexPrice"]),
                "metadata": {
                    **result.metadata,
                    "mark_index_timestamp_precise": False,
                    "mark_index_source_time": premium.get("time"),
                },
            })
        book = self._order_book()
        if book:
            result = result.model_copy(update={
                "bid": book["bid_price"],
                "ask": book["ask_price"],
                "mid": (book["bid_price"] + book["ask_price"]) / 2,
                "bid_depth": book["bid_quantity"],
                "ask_depth": book["ask_quantity"],
                "metadata": {
                    **result.metadata,
                    "book_timestamp_precise": False,
                    "book_last_update_id": book.get("last_update_id"),
                },
            })
        return [result]

    def health(self, *, now: datetime | None = None) -> DataQuality:
        current = now or datetime.now(tz=timezone.utc)
        try:
            bars = self.fetch_closed_bars(limit=3)
        except Exception:
            return DataQuality(safe_for_trading=False, stale=True, missing_sources=(self.source,))
        if bars.is_empty():
            return DataQuality(safe_for_trading=False, stale=True, missing_sources=(self.source,))
        latest = int(bars["timestamp"][-1]) + MINUTE_MS
        age = max(0, int(current.timestamp() * 1000) - latest)
        return DataQuality(
            safe_for_trading=age <= 15_000,
            stale=age > 15_000,
            timestamp_lag_ms=age,
            source_health={self.source: SourceHealth(
                source=self.source, role="primary", healthy=age <= 15_000,
                last_event_timestamp=utc_datetime(latest), last_received_timestamp=current,
                age_ms=age,
            )},
        )


@dataclass
class ReplayAdapter:
    """Deterministic adapter used by integration tests and offline replay."""

    source: str
    venue: str
    instrument: str
    bars: pl.DataFrame
    ticks: list[MarketTick] = field(default_factory=list)
    role: str = "primary"

    def fetch_closed_bars(self, *, limit: int = 3000) -> pl.DataFrame:
        return self.bars.tail(limit)

    def snapshot(self, *, now: datetime | None = None) -> list[MarketTick]:
        return list(self.ticks)

    def health(self, *, now: datetime | None = None) -> DataQuality:
        current = now or datetime.now(tz=timezone.utc)
        if not self.ticks:
            return DataQuality(safe_for_trading=False, stale=True, missing_sources=(self.source,))
        latest = max(t.received_timestamp for t in self.ticks)
        age = max(0, int((current - latest).total_seconds() * 1000))
        healthy = age <= 15_000
        return DataQuality(
            safe_for_trading=healthy,
            stale=not healthy,
            timestamp_lag_ms=age,
            source_health={self.source: SourceHealth(
                source=self.source, role=self.role, healthy=healthy,
                last_event_timestamp=max(t.event_timestamp for t in self.ticks),
                last_received_timestamp=latest, age_ms=age,
            )},
        )


class DataFabric:
    """Source-aware observation store with event-time and bar integrity checks."""

    def __init__(self, required_roles: tuple[str, ...] = ("primary",), *, max_age_ms: int = 15_000):
        self.required_roles = required_roles
        self.max_age_ms = max_age_ms
        self._observations: list[MarketTick] = []
        self._seen_event_keys: set[tuple] = set()
        self._last_by_source: dict[tuple[str, str, str], MarketTick] = {}
        self._duplicates = 0
        self._historical_bar_gaps = 0
        self._sequence_problems: list[str] = []
        self._inconsistent: list[str] = []
        self._incoherent: list[str] = []
        self._event_time_problems: list[str] = []
        self._bar_problems: list[str] = []
        self._last_bar_timestamp: int | None = None

    def ingest_bars(self, bars: pl.DataFrame) -> None:
        """Validate the current closed-bar frame before environment construction.

        A missing minute inside the decision window (the longest timeframe the
        environment builds) makes the feed unsafe. Older holes cannot affect the
        buckets that are still in use, so they are counted, not fatal.
        """
        self._bar_problems = []
        self._last_bar_timestamp = None
        self._historical_bar_gaps = 0
        if bars is None or bars.is_empty():
            self._bar_problems.append("bars_empty")
            return
        required = {"timestamp", "open", "high", "low", "close", "volume"}
        missing = required - set(bars.columns)
        if missing:
            self._bar_problems.append(f"bar_columns_missing:{sorted(missing)}")
            return
        frame = bars.select(["timestamp", "open", "high", "low", "close", "volume"]).sort("timestamp")
        timestamps = [int(value) for value in frame["timestamp"].to_list()]
        decision_window_start = timestamps[-1] - (max(TIMEFRAME_MS.values()) * MINUTE_MS)
        for left, right in zip(timestamps, timestamps[1:]):
            if right - left != MINUTE_MS:
                if right >= decision_window_start:
                    self._bar_problems.append(f"bar_gap:{left}->{right}")
                else:
                    self._historical_bar_gaps += 1
        for row in frame.iter_rows(named=True):
            try:
                open_price = float(row["open"])
                high = float(row["high"])
                low = float(row["low"])
                close = float(row["close"])
                volume = float(row["volume"])
            except (TypeError, ValueError):
                self._bar_problems.append(f"bar_values_invalid:{row['timestamp']}")
                continue
            if min(open_price, high, low, close) <= 0 or high < max(open_price, close) or low > min(open_price, close) or high < low or volume < 0:
                self._bar_problems.append(f"bar_ohlcv_invalid:{row['timestamp']}")
        self._last_bar_timestamp = timestamps[-1] + MINUTE_MS

    def ingest(self, observations: list[MarketTick]) -> None:
        """Absorb observations, dropping exact retransmissions of a known event.

        A repeat of an event the fabric already holds is a benign retransmission
        (a quiet tape, a REST bootstrap polled twice), so it is deduplicated and
        counted rather than treated as corruption. Out-of-order, sequence-gap,
        crossed-book and incoherent observations remain fatal.
        """
        for tick in observations:
            key = (
                tick.source,
                tick.venue,
                tick.instrument,
                tick.event_type,
                tick.event_timestamp,
                tick.sequence,
                tick.metadata.get("feed_generation"),
            )
            if key in self._seen_event_keys:
                self._duplicates += 1
                continue
            self._seen_event_keys.add(key)
            source_key = (tick.source, tick.venue, tick.instrument)
            previous = self._last_by_source.get(source_key)
            if previous and tick.event_timestamp < previous.event_timestamp:
                self._event_time_problems.append(f"{tick.source}:{previous.event_timestamp.isoformat()}->{tick.event_timestamp.isoformat()}")
            if previous and tick.sequence is not None and previous.sequence is not None:
                if tick.sequence <= previous.sequence:
                    self._sequence_problems.append(f"{tick.source}:{previous.sequence}->{tick.sequence}")
                elif tick.sequence != previous.sequence + 1:
                    self._sequence_problems.append(f"{tick.source}:gap:{previous.sequence}->{tick.sequence}")
            if tick.bid is not None and tick.ask is not None and tick.bid > tick.ask:
                self._inconsistent.append(f"{tick.source}:{tick.event_timestamp.isoformat()}")
            if tick.last is not None and tick.bid is not None and tick.ask is not None:
                midpoint = (tick.bid + tick.ask) / 2
                if midpoint > 0 and abs(tick.last - midpoint) / midpoint > 0.05:
                    self._incoherent.append(f"{tick.source}:last_mid:{tick.event_timestamp.isoformat()}")
            self._observations.append(tick)
            self._last_by_source[source_key] = tick

    def latest(self, source: str | None = None) -> list[MarketTick]:
        values = list(self._last_by_source.values())
        if source:
            values = [tick for tick in values if tick.source == source]
        return values

    def quality(self, *, now: datetime | None = None) -> DataQuality:
        current = now or datetime.now(tz=timezone.utc)
        health: dict[str, SourceHealth] = {}
        missing: list[str] = []
        stale = False
        event_time_problems = list(self._event_time_problems)
        if self._last_bar_timestamp is not None and self._last_bar_timestamp > int(current.timestamp() * 1000):
            event_time_problems.append(f"future_bar:{self._last_bar_timestamp}")
        required_roles = set(self.required_roles)
        for source_key, tick in self._last_by_source.items():
            event_age_signed = int((current - tick.event_timestamp).total_seconds() * 1000)
            receive_age_signed = int((current - tick.received_timestamp).total_seconds() * 1000)
            event_age = max(0, event_age_signed)
            receive_age = max(0, receive_age_signed)
            future = event_age_signed < 0 or receive_age_signed < 0
            healthy = not future and event_age <= self.max_age_ms
            # Optional venues stay visible as unhealthy without failing Binance.
            if not healthy and tick.source_role in required_roles:
                stale = True
            health_key = tick.source
            if health_key in health:
                health_key = f"{tick.source}:{tick.venue}:{tick.instrument}"
            health[health_key] = SourceHealth(
                source=tick.source, role=tick.source_role, healthy=healthy,
                last_event_timestamp=tick.event_timestamp, last_received_timestamp=tick.received_timestamp,
                age_ms=event_age, receive_age_ms=receive_age, duplicate_events=self._duplicates,
                sequence_problems=tuple(self._sequence_problems),
                gaps=sum("gap" in item for item in self._sequence_problems),
            )
        for role in self.required_roles:
            if not any(item.role == role for item in health.values()):
                missing.append(role)
        safe = (
            not stale
            and not missing
            and not self._sequence_problems
            and not self._inconsistent
            and not self._incoherent
            and not event_time_problems
            and not self._bar_problems
        )
        return DataQuality(
            safe_for_trading=safe, stale=stale, missing_sources=tuple(missing),
            timestamp_lag_ms=max((item.age_ms or 0 for item in health.values()), default=None),
            sequence_problems=tuple(self._sequence_problems), duplicate_events=self._duplicates,
            historical_bar_gaps=self._historical_bar_gaps,
            gaps=sum("gap" in item for item in self._sequence_problems) + sum("bar_gap" in item for item in self._bar_problems),
            inconsistent_prices=tuple(self._inconsistent), event_time_problems=tuple(event_time_problems),
            bar_timestamp_problems=tuple(self._bar_problems), incoherent_data=tuple(self._incoherent),
            source_health=health,
        )

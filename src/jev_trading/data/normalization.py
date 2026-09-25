"""Live market-data adapters and source-aware normalization.

The active loop only consumes closed bars and explicit source observations. It
never averages venues or silently fills unavailable order-book/flow fields.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

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

    def _book_ticker(self) -> dict | None:
        """Best effort book context; failure is represented as missing data."""
        try:
            response = requests.get(
                KLINES_URL.replace("/klines", "/ticker/bookTicker"),
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
            mark=float(row["close"]),
            volume=float(row["volume"]),
            open_interest=float(row["open_interest"]) if row.get("open_interest") is not None else None,
            funding_rate=float(row["funding_rate"]) if row.get("funding_rate") is not None else None,
            metadata={"bar_open_timestamp": int(row["timestamp"])},
        )
        book = self._book_ticker()
        if book and book.get("bidPrice") and book.get("askPrice"):
            result = result.model_copy(update={
                "bid": float(book["bidPrice"]),
                "ask": float(book["askPrice"]),
                "mid": (float(book["bidPrice"]) + float(book["askPrice"])) / 2,
                "metadata": {**result.metadata, "book_timestamp_precise": False},
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
    """Source-aware observation store with duplicate/sequence/health checks."""

    def __init__(self, required_roles: tuple[str, ...] = ("primary",), *, max_age_ms: int = 15_000):
        self.required_roles = required_roles
        self.max_age_ms = max_age_ms
        self._observations: list[MarketTick] = []
        self._last_by_source: dict[str, MarketTick] = {}
        self._duplicates = 0
        self._sequence_problems: list[str] = []
        self._inconsistent: list[str] = []

    def ingest(self, observations: list[MarketTick]) -> None:
        for tick in observations:
            key = (tick.source, tick.instrument, tick.event_timestamp, tick.event_type, tick.sequence)
            if any((o.source, o.instrument, o.event_timestamp, o.event_type, o.sequence) == key for o in self._observations[-1000:]):
                self._duplicates += 1
                continue
            previous = self._last_by_source.get(tick.source)
            if previous and tick.sequence is not None and previous.sequence is not None:
                if tick.sequence <= previous.sequence:
                    self._sequence_problems.append(f"{tick.source}:{previous.sequence}->{tick.sequence}")
                elif tick.sequence != previous.sequence + 1:
                    self._sequence_problems.append(f"{tick.source}:gap:{previous.sequence}->{tick.sequence}")
            if tick.bid is not None and tick.ask is not None and tick.bid > tick.ask:
                self._inconsistent.append(f"{tick.source}:{tick.event_timestamp.isoformat()}")
            self._observations.append(tick)
            self._last_by_source[tick.source] = tick

    def latest(self, source: str | None = None) -> list[MarketTick]:
        if source:
            tick = self._last_by_source.get(source)
            return [tick] if tick else []
        return list(self._last_by_source.values())

    def quality(self, *, now: datetime | None = None) -> DataQuality:
        current = now or datetime.now(tz=timezone.utc)
        health: dict[str, SourceHealth] = {}
        missing: list[str] = []
        stale = False
        for source, tick in self._last_by_source.items():
            age = max(0, int((current - tick.received_timestamp).total_seconds() * 1000))
            healthy = age <= self.max_age_ms
            stale |= not healthy
            health[source] = SourceHealth(
                source=source, role=tick.source_role, healthy=healthy,
                last_event_timestamp=tick.event_timestamp, last_received_timestamp=tick.received_timestamp,
                age_ms=age, duplicate_events=self._duplicates,
                sequence_problems=tuple(self._sequence_problems),
                gaps=sum("gap" in item for item in self._sequence_problems),
            )
        for role in self.required_roles:
            if not any(item.role == role for item in health.values()):
                missing.append(role)
        safe = not stale and not missing and not self._sequence_problems and not self._inconsistent
        return DataQuality(
            safe_for_trading=safe, stale=stale, missing_sources=tuple(missing),
            timestamp_lag_ms=max((item.age_ms or 0 for item in health.values()), default=None),
            sequence_problems=tuple(self._sequence_problems), duplicate_events=self._duplicates,
            gaps=sum("gap" in item for item in self._sequence_problems),
            inconsistent_prices=tuple(self._inconsistent), source_health=health,
        )

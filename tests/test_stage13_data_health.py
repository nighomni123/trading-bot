"""Stage 13: live data health, source isolation, and completed-bucket contracts."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import polars as pl
import pytest
from pydantic import ValidationError

from jev_trading.data.normalization import DataFabric
from jev_trading.environment import build_market_environment
from jev_trading.live_intelligence.schemas import (
    DataEventType, DataQuality, MarketTick, MarketType, TimeframeState,
)
from tests.test_live_intelligence import bars, tick

UTC = timezone.utc


def _tick(now: datetime, **overrides) -> MarketTick:
    return tick(now, **overrides)


def test_live_data_timestamp_order():
    """event <= received <= decision; a violation is rejected, never repaired."""
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        _tick(now, event_timestamp=now, received_timestamp=now - timedelta(seconds=1))
    with pytest.raises(ValidationError):
        _tick(now, event_timestamp=now, received_timestamp=now, decision_timestamp=now - timedelta(seconds=1))
    valid = _tick(now, event_timestamp=now - timedelta(seconds=1), received_timestamp=now, decision_timestamp=now)
    assert valid.event_timestamp <= valid.received_timestamp <= valid.decision_timestamp


def test_event_after_decision_time_is_unhealthy_not_clamped():
    now = datetime.now(UTC)
    fabric = DataFabric(max_age_ms=15_000)
    fabric.ingest([_tick(now + timedelta(seconds=5), event_timestamp=now + timedelta(seconds=5), received_timestamp=now + timedelta(seconds=5))])
    quality = fabric.quality(now=now)
    assert quality.safe_for_trading is False
    assert quality.stale is True


def test_freshness_uses_event_time_not_receive_time():
    now = datetime.now(UTC)
    fabric = DataFabric(max_age_ms=15_000)
    fabric.ingest([_tick(now, event_timestamp=now - timedelta(seconds=30), received_timestamp=now)])
    quality = fabric.quality(now=now)
    assert quality.stale is True
    assert quality.source_health["primary"].receive_age_ms == 0
    assert quality.source_health["primary"].age_ms >= 30_000


def test_completed_timeframe_only():
    """Inside 12:37 the newest completed 5m bucket is 12:35, never 12:40."""
    from tests.test_live_intelligence import aligned_start_ms
    start = aligned_start_ms(datetime.now(UTC), 300)
    count = (int(datetime.now(UTC).timestamp() * 1000) - start) // 60_000
    frame = pl.DataFrame(
        {
            "timestamp": [start + index * 60_000 for index in range(count)],
            "open": [100.0] * count, "high": [101.0] * count, "low": [99.0] * count,
            "close": [100.0] * count, "volume": [1.0] * count,
            "funding_rate": [0.0001] * count, "open_interest": [1000.0] * count,
        }
    )
    decision = datetime.fromtimestamp((start + count * 60_000 - 2 * 60_000) / 1000, tz=UTC)
    environment = build_market_environment(
        frame, quality=DataQuality(safe_for_trading=True, stale=False), decision_timestamp=decision,
    )
    for name, state in environment.timeframes.items():
        assert state.completed is True
        assert state.bucket_start < state.bucket_end
        assert state.timestamp == state.bucket_end
        assert state.bucket_end <= decision
    for name, minutes in (("5m", 5), ("15m", 15), ("1h", 60), ("4h", 240)):
        state = environment.timeframes[name]
        assert (state.bucket_start - state.bucket_start.replace(hour=0, minute=0, second=0, microsecond=0)).total_seconds() % (minutes * 60) == 0
    with pytest.raises(ValidationError, match="completed"):
        TimeframeState(
            timeframe="5m", bucket_start=decision, bucket_end=decision + timedelta(minutes=5),
            completed=False, timestamp=decision + timedelta(minutes=5),
            open=1, high=1, low=1, close=1, volume=0, trend_direction="FLAT",
        )


def test_gap_rejection():
    """A hole inside the decision window is fatal; older holes are counted."""
    start = 1_700_000_000_000 // (240 * 60_000) * (240 * 60_000)
    timestamps = [start + index * 60_000 for index in range(600)]
    frame = pl.DataFrame({
        "timestamp": timestamps, "open": [100.0] * 600, "high": [101.0] * 600,
        "low": [99.0] * 600, "close": [100.0] * 600, "volume": [1.0] * 600,
        "funding_rate": [0.0001] * 600, "open_interest": [1000.0] * 600,
    })
    now = datetime.now(UTC)
    recent = frame.filter(pl.col("timestamp") != timestamps[-3])
    fabric = DataFabric(max_age_ms=15_000)
    fabric.ingest_bars(recent)
    fabric.ingest([tick(now)])
    unsafe = fabric.quality(now=now)
    assert unsafe.safe_for_trading is False
    assert any("bar_gap" in item for item in unsafe.bar_timestamp_problems)

    historical = frame.filter(pl.col("timestamp") != timestamps[3])
    other = DataFabric(max_age_ms=15_000)
    other.ingest_bars(historical)
    other.ingest([tick(now)])
    safe = other.quality(now=now)
    assert safe.safe_for_trading is True
    assert safe.historical_bar_gaps == 1


def test_gap_in_history_refuses_to_build_a_complete_bucket():
    """A hole inside the newest bucket makes the environment fall back, not infer."""
    from tests.test_live_intelligence import aligned_start_ms
    start = aligned_start_ms(datetime.now(UTC), 300)
    count = (int(datetime.now(UTC).timestamp() * 1000) - start) // 60_000
    timestamps = [start + index * 60_000 for index in range(count)]
    frame = pl.DataFrame({
        "timestamp": timestamps, "open": [100.0] * count, "high": [101.0] * count,
        "low": [99.0] * count, "close": [100.0] * count, "volume": [1.0] * count,
        "funding_rate": [0.0001] * count, "open_interest": [1000.0] * count,
    })
    decision = datetime.fromtimestamp((start + count * 60_000) / 1000, tz=UTC)
    quality = DataQuality(safe_for_trading=True, stale=False)
    intact = build_market_environment(frame, quality=quality, decision_timestamp=decision)
    newest = intact.timeframes["5m"].bucket_start
    # Remove one 1m bar from the middle of that completed 5m bucket.
    holed = frame.filter(pl.col("timestamp") != int((newest + timedelta(minutes=1)).timestamp() * 1000))
    rebuilt = build_market_environment(holed, quality=quality, decision_timestamp=decision)
    assert rebuilt.timeframes["5m"].bucket_start == newest - timedelta(minutes=5)
    assert rebuilt.timeframes["5m"].completed is True


def test_primary_source_isolation():
    """A secondary venue may add context but never sets the executable price."""
    now = datetime.now(UTC)
    primary = tick(now, source="binance", source_role="primary", last=100.0, bid=99.9, ask=100.1)
    secondary = tick(now, source="bybit", venue="bybit", source_role="secondary", last=42.0, bid=41.9, ask=42.1)
    environment = build_market_environment(
        bars(), ticks=[secondary, primary],
        quality=DataQuality(safe_for_trading=True, stale=False), decision_timestamp=now,
    )
    assert environment.price.last == 100.0
    assert environment.price.source == "binance"
    assert "bybit:last" in environment.cross_market.observations


def test_bybit_stale_data_cannot_make_binance_healthy():
    now = datetime.now(UTC)
    fabric = DataFabric(max_age_ms=15_000)
    fabric.ingest([
        tick(now - timedelta(seconds=60), source="binance", source_role="primary",
             event_timestamp=now - timedelta(seconds=60), received_timestamp=now - timedelta(seconds=60)),
        tick(now, source="bybit", source_role="secondary"),
    ])
    quality = fabric.quality(now=now)
    assert quality.source_health["binance"].healthy is False
    assert quality.safe_for_trading is False


def test_wrong_instrument_observations_are_rejected():
    now = datetime.now(UTC)
    wrong = tick(now, instrument="ETHUSDT_PERP")
    environment = build_market_environment(
        bars(), ticks=[wrong], quality=DataQuality(safe_for_trading=True, stale=False),
        decision_timestamp=now,
    )
    assert environment.price.last != 100.0 or environment.price.source != "ETHUSDT_PERP"
    assert all("ETHUSDT" not in key for key in environment.cross_market.observations)


def test_spot_vs_perpetual_rejection():
    now = datetime.now(UTC)
    spot = tick(now, market_type=MarketType.SPOT)
    environment = build_market_environment(
        bars(), ticks=[spot], quality=DataQuality(safe_for_trading=True, stale=False),
        decision_timestamp=now,
    )
    assert environment.cross_market.observations == {}
    assert environment.market_type == MarketType.PERPETUAL
    # The bar close is the fallback, never a spot print.
    assert environment.price.last == float(bars()["close"][-1])


def test_book_staleness_nulls_the_book_not_the_feed():
    from jev_trading.data.venue_adapters import BinanceLiveState
    state = BinanceLiveState()
    now = int(datetime.now(UTC).timestamp() * 1000)
    state.generation = 1
    state.apply({"stream": "x", "data": {"s": "BTCUSDT", "e": "depthUpdate", "E": now - 20_000,
                                          "T": now - 20_000, "b": [["100", "1"]], "a": [["101", "1"]], "u": 5}}, 1, book=True)
    state.apply({"stream": "x", "data": {"s": "BTCUSDT", "e": "markPrice", "E": now, "T": now,
                                          "p": "100.5", "i": "100.4", "r": "0.0001"}}, 1)
    fields = state.fields(datetime.fromtimestamp(now / 1000, tz=UTC), max_age_ms=15_000)
    assert fields["bid"] is None and fields["ask"] is None
    assert fields["mark"] == 100.5
    assert fields["metadata"]["book_event_timestamp"] is None
    assert fields["metadata"]["mark_event_timestamp"] is not None
    assert fields["metadata"]["last_event_timestamp"] == now


def test_missing_liquidation_is_null():
    from jev_trading.data.venue_adapters import BinanceLiveState
    state = BinanceLiveState()
    now = int(datetime.now(UTC).timestamp() * 1000)
    state.generation = 1
    state.apply({"stream": "x", "data": {"s": "BTCUSDT", "e": "aggTrade", "E": now, "T": now,
                                          "a": 1, "p": "100", "q": "1", "m": False}}, 1)
    fields = state.fields(datetime.fromtimestamp(now / 1000, tz=UTC))
    assert fields["liquidation_long"] is None
    assert fields["liquidation_short"] is None
    assert fields["open_interest"] is None
    assert fields["metadata"]["liquidation_stream_active"] is False


def test_retransmitted_event_is_deduplicated_not_fatal():
    now = datetime.now(UTC)
    fabric = DataFabric(max_age_ms=15_000)
    event = tick(now)
    for _ in range(3):
        fabric.ingest([event])
    quality = fabric.quality(now=now)
    assert quality.duplicate_events == 2
    assert quality.safe_for_trading is True
    assert len(fabric.latest()) == 1

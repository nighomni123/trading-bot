"""Data-fabric event-time and bar-integrity behavior."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import polars as pl
import pytest
from pydantic import ValidationError

from jev_trading.data.normalization import DataFabric
from jev_trading.environment import build_market_environment
from jev_trading.live_intelligence.schemas import DataQuality, MarketTick
from tests.test_live_intelligence import bars, tick

UTC = timezone.utc


def test_freshness_uses_event_time_not_receive_time():
    now = datetime.now(UTC)
    fabric = DataFabric(max_age_ms=15_000)
    fabric.ingest([tick(now - timedelta(seconds=60), event_timestamp=now - timedelta(seconds=60), received_timestamp=now)])
    quality = fabric.quality(now=now)
    assert quality.stale is True
    assert quality.safe_for_trading is False
    assert quality.event_time_problems == ()
    assert quality.source_health["primary"].age_ms >= 60_000


def test_fresh_duplicate_is_unsafe():
    now = datetime.now(UTC)
    fabric = DataFabric(max_age_ms=15_000)
    event = tick(now)
    fabric.ingest([event, event])
    quality = fabric.quality(now=now)
    assert quality.duplicate_events == 1
    assert quality.safe_for_trading is False


def test_bar_gap_is_recorded_as_unsafe():
    now = datetime.now(UTC)
    fabric = DataFabric(max_age_ms=15_000)
    frame = pl.DataFrame({
        "timestamp": [0, 120_000],
        "open": [100.0, 101.0],
        "high": [101.0, 102.0],
        "low": [99.0, 100.0],
        "close": [100.5, 101.5],
        "volume": [1.0, 1.0],
    })
    fabric.ingest_bars(frame)
    fabric.ingest([tick(now)])
    quality = fabric.quality(now=now)
    assert any("bar_gap" in item for item in quality.bar_timestamp_problems)
    assert quality.safe_for_trading is False


def test_midpoint_incoherence_is_rejected_at_schema_boundary():
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        MarketTick(
            source="primary", source_role="primary", instrument="BTCUSDT_PERP",
            venue="binance-futures", market_type=tick(now).market_type,
            event_timestamp=now, received_timestamp=now, event_type=tick(now).event_type,
            last=100.0, bid=99.0, ask=101.0, mid=100.5,
        )


def test_unknown_liquidations_remain_unknown():
    now = datetime.now(UTC)
    env = build_market_environment(
        bars(), ticks=[tick(now)], quality=DataQuality(safe_for_trading=True, stale=False),
        decision_timestamp=now,
    )
    assert env.derivatives.liquidations == {}

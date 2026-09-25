"""Build a normalized, multi-timeframe MarketEnvironment from closed bars."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

import polars as pl

from jev_trading.contracts import BAR_COLUMNS
from jev_trading.environment.features import TIMEFRAME_MS, aggregate_timeframe, timeframe_features, utc_from_ms
from jev_trading.live_intelligence.schemas import (
    CrossMarketState,
    DataQuality,
    DerivativesState,
    FlowState,
    LiquidityState,
    MarketEnvironment,
    MarketEvent,
    MarketStructureState,
    MarketTick,
    MarketType,
    PositionState,
    PriceState,
    TimeframeState,
    VolatilityState,
)


def _direction(close: float, previous: float | None, features: dict[str, float | None]) -> str:
    if previous is None:
        return "FLAT"
    if close > previous:
        return "UP"
    if close < previous:
        return "DOWN"
    return features.get("trend_direction", "FLAT") or "FLAT"


def build_market_environment(
    bars: pl.DataFrame,
    *,
    instrument: str = "BTCUSDT_PERP",
    venue: str = "binance-futures",
    market_type: MarketType = MarketType.PERPETUAL,
    ticks: Iterable[MarketTick] = (),
    events: tuple[MarketEvent, ...] = (),
    position: PositionState = PositionState(),
    quality: DataQuality | None = None,
    decision_timestamp: datetime | None = None,
) -> MarketEnvironment:
    """Create an environment using only bars/ticks available at decision time.

    The latest bar is treated as closed at its open timestamp plus one minute.
    A caller must pass a decision timestamp at or after that event time.
    """
    missing = [column for column in BAR_COLUMNS if column not in bars.columns]
    if missing:
        raise ValueError(f"missing bar columns: {missing}")
    if bars.is_empty():
        raise ValueError("cannot build environment from empty bars")
    frame = bars.select(BAR_COLUMNS).sort("timestamp")
    timestamps = frame["timestamp"].to_list()
    if any(b <= a for a, b in zip(timestamps, timestamps[1:])):
        raise ValueError("bar timestamps must be strictly increasing")
    latest = frame.row(-1, named=True)
    event_time = utc_from_ms(int(latest["timestamp"]) + 60_000)
    decision_time = decision_timestamp or datetime.now(tz=timezone.utc)
    if decision_time.tzinfo is None:
        raise ValueError("decision_timestamp must be timezone-aware")
    decision_time = decision_time.astimezone(timezone.utc)
    if decision_time < event_time:
        raise ValueError("decision timestamp precedes latest closed bar")

    timeframes: dict[str, TimeframeState] = {}
    for timeframe, minutes in TIMEFRAME_MS.items():
        aggregated = aggregate_timeframe(frame, timeframe)
        if aggregated.is_empty():
            continue
        row = aggregated.row(-1, named=True)
        features = timeframe_features(aggregated)
        previous_close = float(aggregated["close"][-2]) if aggregated.height > 1 else None
        trend = _direction(float(row["close"]), previous_close, {"trend_direction": "FLAT"})
        timeframes[timeframe] = TimeframeState(
            timeframe=timeframe,
            timestamp=utc_from_ms(int(row["bucket"]) + minutes * 60_000),
            open=float(row["open"]), high=float(row["high"]), low=float(row["low"]), close=float(row["close"]),
            volume=float(row["volume"]), trend_direction=trend, **features,
        )

    # A full environment requires the requested multi-timeframe set. If the
    # warmup is short, fail closed instead of fabricating longer-horizon data.
    required = set(TIMEFRAME_MS)
    if required - set(timeframes):
        raise ValueError(f"insufficient history for timeframes: {sorted(required - set(timeframes))}")

    tick_list = list(ticks)
    latest_tick = next((tick for tick in tick_list if tick.source_role == "primary"), tick_list[0] if tick_list else None)
    close = float(latest_tick.last) if latest_tick and latest_tick.last is not None else float(latest["close"])
    bid = latest_tick.bid if latest_tick else None
    ask = latest_tick.ask if latest_tick else None
    mark = latest_tick.mark if latest_tick else None
    index = latest_tick.index if latest_tick else None
    price = PriceState(
        source=latest_tick.source if latest_tick else venue,
        venue=latest_tick.venue if latest_tick else venue,
        last=close, bid=bid, ask=ask,
        mid=(bid + ask) / 2 if bid is not None and ask is not None else None,
        mark=mark, index=index,
        spread_fraction=latest_tick.spread_fraction if latest_tick else None,
    )
    def tf(name: str) -> TimeframeState:
        return timeframes[name]
    structure = MarketStructureState(
        trend_1m=tf("1m").trend_direction, trend_5m=tf("5m").trend_direction,
        trend_15m=tf("15m").trend_direction, trend_1h=tf("1h").trend_direction,
        trend_4h=tf("4h").trend_direction,
        range_position_15m=tf("15m").range_position,
        distance_from_vwap_15m=tf("15m").distance_from_vwap,
        breakout_state="UP" if tf("15m").close > tf("15m").open else "DOWN",
        compression_state="UNKNOWN",
    )
    vol = VolatilityState(
        realized_1m=tf("1m").realized_volatility, realized_5m=tf("5m").realized_volatility,
        realized_15m=tf("15m").realized_volatility, realized_1h=tf("1h").realized_volatility,
        atr_15m=tf("15m").atr_fraction, regime="EXPANSION" if (tf("5m").realized_volatility or 0) > (tf("15m").realized_volatility or 0) else "COMPRESSION",
    )
    buy_volume = latest_tick.buy_volume if latest_tick else None
    sell_volume = latest_tick.sell_volume if latest_tick else None
    imbalance = ((buy_volume - sell_volume) / (buy_volume + sell_volume)
                 if buy_volume is not None and sell_volume is not None and buy_volume + sell_volume > 0 else None)
    flow = FlowState(volume=float(latest["volume"]), buy_volume=buy_volume, sell_volume=sell_volume, imbalance=imbalance)
    depth_bid = latest_tick.bid_depth if latest_tick else None
    depth_ask = latest_tick.ask_depth if latest_tick else None
    liquidity = LiquidityState(
        spread=price.spread_fraction, bid_depth=depth_bid, ask_depth=depth_ask,
        depth=(depth_bid + depth_ask) if depth_bid is not None and depth_ask is not None else None,
        imbalance=((depth_bid - depth_ask) / (depth_bid + depth_ask)
                   if depth_bid is not None and depth_ask is not None and depth_bid + depth_ask > 0 else None),
        top_level_notional=(bid * depth_bid + ask * depth_ask) if bid and ask and depth_bid is not None and depth_ask is not None else None,
    )
    derivatives = DerivativesState(
        open_interest=float(latest["open_interest"]) if latest.get("open_interest") is not None else None,
        oi_change=None, funding=float(latest["funding_rate"]) if latest.get("funding_rate") is not None else None,
        basis=((mark or close) / index - 1.0) if mark and index else None,
        mark_index_divergence=((mark or close) / index - 1.0) if mark and index else None,
        liquidations={"long": latest_tick.liquidation_long or 0.0, "short": latest_tick.liquidation_short or 0.0} if latest_tick else {},
    )
    data_quality = quality or DataQuality(safe_for_trading=False, stale=True, missing_sources=("primary",))
    return MarketEnvironment(
        timestamp=event_time, decision_timestamp=decision_time, instrument=instrument,
        venue=venue, market_type=market_type, price=price, timeframes=timeframes,
        structure=structure, volatility=vol, flow=flow, liquidity=liquidity,
        derivatives=derivatives, events=events, cross_market=CrossMarketState(),
        position=position, data_quality=data_quality,
    )

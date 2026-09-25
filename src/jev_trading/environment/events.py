"""Causal deterministic market-event detection."""
from __future__ import annotations

from jev_trading.live_intelligence.schemas import MarketEnvironment, MarketEvent


def _event(kind: str, env: MarketEnvironment, severity: float, features: dict) -> MarketEvent:
    return MarketEvent(
        event_type=kind, event_timestamp=env.timestamp, detection_timestamp=env.decision_timestamp,
        instrument=env.instrument, source=env.price.source, severity=min(1.0, max(0.0, severity)),
        features_at_detection=features,
    )


def detect_events(env: MarketEnvironment, config=None) -> tuple[MarketEvent, ...]:
    expansion_ratio = float(getattr(config, "volatility_expansion_ratio", 1.5)) if config else 1.5
    volume_z = float(getattr(config, "volume_shock_z", 2.0)) if config else 2.0
    return_z = float(getattr(config, "return_shock_sigma", 3.0)) if config else 3.0
    oi_shock = float(getattr(config, "oi_shock_fraction", 0.03)) if config else 0.03
    funding_extreme = float(getattr(config, "funding_extreme", 0.0005)) if config else 0.0005
    acceleration_threshold = float(getattr(config, "trend_acceleration_threshold", 0.001)) if config else 0.001
    events: list[MarketEvent] = []
    five = env.timeframes["5m"]
    fifteen = env.timeframes["15m"]
    if five.realized_volatility is not None and fifteen.realized_volatility:
        ratio = five.realized_volatility / fifteen.realized_volatility
        if ratio > expansion_ratio:
            events.append(_event("volatility_expansion", env, 0.7, {"ratio": ratio}))
        elif ratio < 1 / expansion_ratio:
            events.append(_event("volatility_compression", env, 0.6, {"ratio": ratio}))
    if fifteen.volume_z is not None and abs(fifteen.volume_z) >= volume_z:
        events.append(_event("volume_shock", env, min(1.0, abs(fifteen.volume_z) / (volume_z * 2)), {"volume_z": fifteen.volume_z}))
    if fifteen.return_fraction is not None and fifteen.realized_volatility and abs(fifteen.return_fraction) >= return_z * fifteen.realized_volatility:
        events.append(_event("return_shock", env, 0.8, {"return": fifteen.return_fraction}))
    if fifteen.previous_high is not None and fifteen.close > fifteen.previous_high:
        events.append(_event("range_breakout", env, 0.8, {"close": fifteen.close, "previous_high": fifteen.previous_high}))
    elif fifteen.previous_low is not None and fifteen.close < fifteen.previous_low:
        events.append(_event("range_breakout", env, 0.8, {"close": fifteen.close, "previous_low": fifteen.previous_low}))
    if fifteen.previous_high is not None and fifteen.open >= fifteen.previous_high and fifteen.close < fifteen.previous_high:
        events.append(_event("range_failure", env, 0.8, {"open": fifteen.open, "close": fifteen.close, "previous_high": fifteen.previous_high}))
    if fifteen.previous_low is not None and fifteen.open <= fifteen.previous_low and fifteen.close > fifteen.previous_low:
        events.append(_event("range_failure", env, 0.8, {"open": fifteen.open, "close": fifteen.close, "previous_low": fifteen.previous_low}))
    if fifteen.previous_trend_direction and fifteen.previous_trend_direction != fifteen.trend_direction:
        events.append(_event("trend_transition", env, 0.7, {"from": fifteen.previous_trend_direction, "to": fifteen.trend_direction}))
    if fifteen.trend_acceleration is not None and abs(fifteen.trend_acceleration) >= acceleration_threshold:
        events.append(_event("trend_acceleration", env, min(1.0, abs(fifteen.trend_acceleration) / acceleration_threshold), {"acceleration": fifteen.trend_acceleration}))
    if env.derivatives.oi_change is not None and abs(env.derivatives.oi_change) >= oi_shock:
        events.append(_event("oi_shock", env, 0.7, {"oi_change": env.derivatives.oi_change}))
    if env.derivatives.funding is not None and abs(env.derivatives.funding) >= funding_extreme:
        events.append(_event("funding_extreme", env, 0.6, {"funding": env.derivatives.funding}))
    liquidations = env.derivatives.liquidations
    if liquidations and any(value is not None and value > 0 for value in liquidations.values()):
        events.append(_event("liquidation_burst", env, 0.7, {"liquidations": liquidations}))
    if env.liquidity.spread is not None and env.liquidity.spread > 0.0005:
        events.append(_event("spread_expansion", env, 0.6, {"spread": env.liquidity.spread}))
    if env.flow.imbalance is not None and abs(env.flow.imbalance) >= 0.5:
        events.append(_event("order_flow_imbalance", env, 0.6, {"imbalance": env.flow.imbalance}))
    return tuple(events)

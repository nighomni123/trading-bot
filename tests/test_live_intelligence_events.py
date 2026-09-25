"""Causal event-family coverage."""
from __future__ import annotations

from jev_trading.environment import detect_events
from jev_trading.live_intelligence.config import QuantConfig
from jev_trading.live_intelligence.schemas import DataQuality
from tests.test_live_intelligence import environment


def _with_timeframe(env, timeframe, **updates):
    states = dict(env.timeframes)
    states[timeframe] = states[timeframe].model_copy(update=updates)
    return env.model_copy(update={"timeframes": states})


def test_compression_breakout_failure_transition_acceleration_and_oi_events():
    env = environment(quality=DataQuality(safe_for_trading=True, stale=False))
    env = _with_timeframe(env, "5m", realized_volatility=0.1)
    env = _with_timeframe(env, "15m", realized_volatility=1.0, previous_high=120.0, close=121.0, open=120.5, previous_trend_direction="DOWN", trend_direction="UP", trend_acceleration=0.01)
    env = env.model_copy(update={"derivatives": env.derivatives.model_copy(update={"oi_change": 0.1})})
    kinds = {event.event_type for event in detect_events(env, QuantConfig())}
    assert {"volatility_compression", "range_breakout", "trend_transition", "trend_acceleration", "oi_shock"} <= kinds


def test_events_are_attached_and_timestamp_safe():
    env = environment(quality=DataQuality(safe_for_trading=True, stale=False))
    env = _with_timeframe(env, "15m", previous_high=120.0, close=121.0)
    events = detect_events(env, QuantConfig())
    attached = env.model_copy(update={"events": events})
    assert all(event.event_timestamp <= attached.decision_timestamp for event in attached.events)
    assert "range_breakout" in {event.event_type for event in attached.events}


def test_unknown_liquidation_data_does_not_create_zero_liquidation_event():
    env = environment(quality=DataQuality(safe_for_trading=True, stale=False))
    assert env.derivatives.liquidations == {}
    assert "liquidation_burst" not in {event.event_type for event in detect_events(env, QuantConfig())}

"""Regime classification from multi-timeframe environment components."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RegimeAssessment:
    trend: str
    volatility: str
    structure: str
    derivatives: str
    confidence: float
    reason: str


def assess_regime(env) -> RegimeAssessment:
    trends = [env.timeframes[name].trend_direction for name in ("15m", "1h", "4h")]
    bullish = trends.count("UP")
    bearish = trends.count("DOWN")
    trend = "BULLISH" if bullish >= 2 else "BEARISH" if bearish >= 2 else "MIXED"
    vol = env.volatility.regime
    derivatives = "CROWDED" if abs(env.derivatives.funding or 0) >= 0.0005 else "NEUTRAL"
    confidence = min(1.0, max(trend == "MIXED" and 0.3, max(bullish, bearish) / 3))
    return RegimeAssessment(
        trend=trend, volatility=vol, structure=env.structure.breakout_state,
        derivatives=derivatives, confidence=confidence,
        reason=f"higher-timeframe trend={trend}; volatility={vol}; derivatives={derivatives}",
    )

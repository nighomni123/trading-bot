"""Laya stub: typed decision interface for frontier strategy allocation.

Real Laya inference deferred — see `ponytail:` note. This module defines
schemas, a deterministic replay stub, and an offline harness insertion point.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

# ponytail: real Laya typed-decision call deferred until Phase 5 specialist
# evidence justifies it. Stub returns deterministic heuristics for replay.


class RegimeChoice(str, Enum):
    TREND = "TREND"
    MEAN_REVERSION = "MEAN_REVERSION"
    BREAKOUT = "BREAKOUT"
    CHOP = "CHOP"


class SignalQuality(str, Enum):
    WEAK = "WEAK"
    MODERATE = "MODERATE"
    STRONG = "STRONG"


class StrategyProfile(BaseModel):
    """Frontier-selected strategy weights / selection for this bar."""
    strategy: RegimeChoice = Field(..., description="Active strategy family")
    confidence: float = Field(..., ge=0.0, le=1.0)
    abstain: float = Field(..., ge=0.0, le=1.0)
    selectivity: float = Field(0.5, ge=0.0, le=1.0, description="Higher = fewer trades")


class LayaDecision(BaseModel):
    """Typed decision output from frontier/Laya layer (not BUY/SELL)."""
    ts: int = Field(..., description="Epoch ms")
    strategy_profile: StrategyProfile
    market_quality: SignalQuality = SignalQuality.MODERATE
    cost_environment: str = Field("NORMAL", description="CHEAP / NORMAL / EXPENSIVE")
    # ponytail: real inference deferred; stub uses deterministic heuristics
    stub_source: str = Field("replay_heuristic", description="audit trail")


class FrontierState(BaseModel):
    """Compressed state fed to frontier / Laya (audit #3 expanded)."""
    ts: int
    returns_1m: float = 0.0
    returns_5m: float = 0.0
    returns_15m: float = 0.0
    returns_1h: float = 0.0
    realized_vol: float = 0.0
    volatility_percentile: float = 0.5
    funding_z: float = 0.0
    expected_return: float = 0.0
    expected_edge_after_cost: float = 0.0
    prediction_entropy: float = 0.0
    model_disagreement: float = 0.0
    current_strategy: Optional[str] = None


def replay_laya_decision(frontier_state: FrontierState) -> LayaDecision:
    """Offline replay stub: deterministic heuristic allocator for harness.

    Not a real model — produces auditable artifacts for incremental-value
    measurement (Quant vs Quant+Policy vs Quant+Policy+Laya).
    """
    # ponytail: deterministic heuristic, no real Laya call; upgrade when Phase 5
    # specialist signals create a meaningful allocation problem.
    profile = StrategyProfile(
        strategy=RegimeChoice.TREND,
        confidence=0.6,
        abstain=0.15,
        selectivity=0.5,
    )
    return LayaDecision(
        ts=frontier_state.ts,
        strategy_profile=profile,
        stub_source="replay_heuristic",
    )

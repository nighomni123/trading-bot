"""Strategy router: quant specialist outputs → StrategyProfile (stub).

Real arbitration deferred until specialist models (Phase 4) demonstrate
measurable differentiated economic signals.
"""
from __future__ import annotations

from typing import Optional

from jev_trading.frontier.laya import StrategyProfile, RegimeChoice


class StrategyRouter:
    """Routes multi-specialist outputs to a single StrategyProfile.

    ponytail: single-pass deterministic router; no real Laya model call.
    Upgrade path: replace with Laya typed-decision inference when Phase 5
    specialist evidence justifies it.
    """

    def route(
        self,
        momentum_edge: float,
        mean_revert_edge: float,
        breakout_edge: float,
        cost_environment: str = "NORMAL",
    ) -> StrategyProfile:
        edges = {
            RegimeChoice.TREND: momentum_edge,
            RegimeChoice.MEAN_REVERSION: mean_revert_edge,
            RegimeChoice.BREAKOUT: breakout_edge,
        }
        best = max(edges, key=edges.get)  # type: ignore[arg-type]
        # Abstain if best edge is weak relative to cost
        abstain = 0.3 if max(edges.values()) < 0.001 else 0.05
        return StrategyProfile(
            strategy=best,
            confidence=min(0.99, max(0.0, max(edges.values()) * 20)),
            abstain=min(0.99, max(0.0, abstain)),
            selectivity=0.5,
        )

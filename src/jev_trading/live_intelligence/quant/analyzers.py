"""Typed deterministic Quant analyzers.

These are analytical instruments, not a return predictor. Every analyzer
returns evidence and limitations; none can create an order.
"""
from __future__ import annotations

from datetime import datetime
from statistics import median
from typing import Callable

from jev_trading.live_intelligence.schemas import (
    CandidateTrade,
    EconomicValue,
    MarketEnvironment,
    QuantAnalysisResult,
)


def _result(name: str, version: str, env: MarketEnvironment, evidence: dict, limitations=(), **kwargs) -> QuantAnalysisResult:
    return QuantAnalysisResult(
        analysis_name=name, analyzer_version=version, timestamp=env.decision_timestamp,
        evidence=evidence, limitations=limitations, **kwargs,
    )


def trend_analyzer(env: MarketEnvironment) -> QuantAnalysisResult:
    evidence = {f"trend_{tf}": state.trend_direction for tf, state in env.timeframes.items()}
    evidence["alignment"] = sum(state.trend_direction == env.timeframes["4h"].trend_direction for state in env.timeframes.values()) / len(env.timeframes)
    return _result("trend", "trend-v1", env, evidence)


def volatility_analyzer(env: MarketEnvironment) -> QuantAnalysisResult:
    values = [state.realized_volatility for state in env.timeframes.values() if state.realized_volatility is not None]
    evidence = {"values": values, "current_regime": env.volatility.regime, "atr_15m": env.volatility.atr_15m}
    limitations = ("percentile requires a longer retained history",) if len(values) < 10 else ()
    return _result("volatility", "volatility-v1", env, evidence, limitations)


def market_structure_analyzer(env: MarketEnvironment) -> QuantAnalysisResult:
    state = env.timeframes["15m"]
    evidence = {"high": state.high, "low": state.low, "close": state.close, "range_position": state.range_position, "breakout_state": env.structure.breakout_state}
    return _result("market_structure", "structure-v1", env, evidence)


def breakout_analyzer(env: MarketEnvironment) -> QuantAnalysisResult:
    five, fifteen = env.timeframes["5m"], env.timeframes["15m"]
    evidence = {"five_minute_return": five.return_fraction, "fifteen_minute_range_position": fifteen.range_position, "breakout_state": env.structure.breakout_state}
    return _result("breakout", "breakout-v1", env, evidence, ("No historical analog sample is attached; no continuation probability is estimated.",))


def mean_reversion_analyzer(env: MarketEnvironment) -> QuantAnalysisResult:
    state = env.timeframes["15m"]
    evidence = {"range_position": state.range_position, "distance_from_vwap": state.distance_from_vwap}
    return _result("mean_reversion", "mean-reversion-v1", env, evidence)


def volume_flow_analyzer(env: MarketEnvironment) -> QuantAnalysisResult:
    return _result("volume_flow", "flow-v1", env, {"volume": env.flow.volume, "buy_volume": env.flow.buy_volume, "sell_volume": env.flow.sell_volume, "imbalance": env.flow.imbalance}, ("Missing trade-side data remains missing.",))


def derivatives_analyzer(env: MarketEnvironment) -> QuantAnalysisResult:
    return _result("derivatives", "derivatives-v1", env, {"open_interest": env.derivatives.open_interest, "oi_change": env.derivatives.oi_change, "funding": env.derivatives.funding, "basis": env.derivatives.basis, "relationship": env.derivatives.price_oi_relationship})


def open_interest_analyzer(env: MarketEnvironment) -> QuantAnalysisResult:
    return _result("open_interest", "open-interest-v1", env, {"open_interest": env.derivatives.open_interest, "oi_change": env.derivatives.oi_change, "relationship": env.derivatives.price_oi_relationship})


def funding_analyzer(env: MarketEnvironment) -> QuantAnalysisResult:
    return _result("funding", "funding-v1", env, {"funding": env.derivatives.funding, "funding_change": env.derivatives.funding_change})


def liquidation_analyzer(env: MarketEnvironment) -> QuantAnalysisResult:
    return _result("liquidation", "liquidation-v1", env, {"liquidations": env.derivatives.liquidations})


def liquidity_analyzer(env: MarketEnvironment) -> QuantAnalysisResult:
    return _result("liquidity", "liquidity-v1", env, {"spread": env.liquidity.spread, "depth": env.liquidity.depth, "imbalance": env.liquidity.imbalance, "top_level_notional": env.liquidity.top_level_notional}, ("Order-book fields are unavailable unless explicitly supplied by a live source.",))


def path_analyzer(env: MarketEnvironment, candidate: CandidateTrade | None = None) -> QuantAnalysisResult:
    evidence = {"candidate_side": candidate.side.value if candidate else None, "target": candidate.target if candidate else None, "stop": candidate.stop if candidate else None}
    return _result("path", "path-v1", env, evidence, ("No empirical path sample is attached in this live call; policy must abstain without evidence.",), horizon_seconds=candidate.max_holding_seconds if candidate else None)


def opportunity_analyzer(env: MarketEnvironment, economic_value: EconomicValue | None = None) -> QuantAnalysisResult:
    evidence = {"economic_value": economic_value.model_dump() if economic_value else None}
    return _result("opportunity", "opportunity-v1", env, evidence, ("Economic value is required before an entry proposal.",))


ANALYZERS: dict[str, Callable] = {
    "trend_analyzer": trend_analyzer,
    "volatility_analyzer": volatility_analyzer,
    "market_structure_analyzer": market_structure_analyzer,
    "breakout_analyzer": breakout_analyzer,
    "mean_reversion_analyzer": mean_reversion_analyzer,
    "volume_flow_analyzer": volume_flow_analyzer,
    "derivatives_analyzer": derivatives_analyzer,
    "open_interest_analyzer": open_interest_analyzer,
    "funding_analyzer": funding_analyzer,
    "liquidation_analyzer": liquidation_analyzer,
    "liquidity_analyzer": liquidity_analyzer,
    "path_analyzer": path_analyzer,
    "opportunity_analyzer": opportunity_analyzer,
}


class QuantRegistry:
    def __init__(self) -> None:
        self._analyzers = dict(ANALYZERS)

    def names(self) -> tuple[str, ...]:
        return tuple(self._analyzers)

    def run(self, name: str, env: MarketEnvironment, *, candidate: CandidateTrade | None = None, economic_value: EconomicValue | None = None) -> QuantAnalysisResult:
        if name not in self._analyzers:
            raise KeyError(name)
        if name == "path_analyzer":
            return self._analyzers[name](env, candidate)
        if name == "opportunity_analyzer":
            return self._analyzers[name](env, economic_value)
        return self._analyzers[name](env)

    def run_all(self, env: MarketEnvironment, *, candidate: CandidateTrade | None = None, economic_value: EconomicValue | None = None) -> tuple[QuantAnalysisResult, ...]:
        return tuple(self.run(name, env, candidate=candidate, economic_value=economic_value) for name in self._analyzers)

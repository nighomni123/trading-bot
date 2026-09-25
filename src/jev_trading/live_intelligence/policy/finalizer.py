"""Deterministic policy finalization.

Policy may translate validated intelligence into an action, but it never sizes
positions and never overrides risk/data quality.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from ..config import LiveSettings, PolicyThresholds
from ..schemas import (
    CandidateTrade,
    EconomicValue,
    JevEvaluation,
    JevState,
    MarketEnvironment,
    PolicyAction,
    PolicyDecision,
    PositionState,
    Side,
    StrategyHypothesis,
)


def make_candidate(environment: MarketEnvironment, hypothesis: StrategyHypothesis, *, settings: LiveSettings) -> CandidateTrade | None:
    if hypothesis.abstain or hypothesis.direction.value == "NONE" or not hypothesis.primary_strategy:
        return None
    price = environment.price.last
    if price is None:
        return None
    state = environment.timeframes["15m"]
    atr_fraction = state.atr_fraction or 0.001
    minimum = price * settings.policy.minimum_distance_bps / 10_000
    target_distance = max(price * atr_fraction * settings.policy.target_atr_multiple, minimum)
    stop_distance = max(price * atr_fraction * settings.policy.stop_atr_multiple, minimum)
    if hypothesis.direction.value == "LONG":
        return CandidateTrade(side=Side.LONG, entry_reference=price, target=price + target_distance, stop=price - stop_distance,
                              max_holding_seconds=settings.policy.maximum_holding_seconds, strategy_id=hypothesis.primary_strategy,
                              strategy_version=hypothesis.prompt_version, entry_condition="; ".join(hypothesis.entry_conditions))
    return CandidateTrade(side=Side.SHORT, entry_reference=price, target=price - target_distance, stop=price + stop_distance,
                          max_holding_seconds=settings.policy.maximum_holding_seconds, strategy_id=hypothesis.primary_strategy,
                          strategy_version=hypothesis.prompt_version, entry_condition="; ".join(hypothesis.entry_conditions))


class PolicyFinalizer:
    def __init__(self, settings: LiveSettings):
        self.settings = settings
        self.thresholds: PolicyThresholds = settings.policy

    def finalize(
        self,
        environment: MarketEnvironment,
        hypothesis: StrategyHypothesis,
        economic_value: EconomicValue | None,
        jev: JevEvaluation | None,
        *,
        position: PositionState | None = None,
        candidate: CandidateTrade | None = None,
        decision_id: str | None = None,
    ) -> PolicyDecision:
        now = environment.decision_timestamp
        ident = decision_id or str(uuid4())
        reasons: list[str] = []
        if not environment.data_quality.safe_for_trading:
            return PolicyDecision(decision_id=ident, timestamp=now, action=PolicyAction.DATA_UNSAFE, reasons=("data_quality_unsafe",), policy_version="policy-v1")
        if position and position.side != Side.FLAT:
            price = environment.price.last
            stop_hit = False
            target_hit = False
            if price is not None and position.current_stop is not None:
                stop_hit = (position.side == Side.LONG and price <= position.current_stop) or (position.side == Side.SHORT and price >= position.current_stop)
            if price is not None and position.current_target is not None:
                target_hit = (position.side == Side.LONG and price >= position.current_target) or (position.side == Side.SHORT and price <= position.current_target)
            timed_out = position.opened_at is not None and (now - position.opened_at).total_seconds() >= self.settings.policy.maximum_holding_seconds
            if stop_hit or target_hit or timed_out or (jev and jev.recommended_state == JevState.EXIT):
                reason = "stop_hit" if stop_hit else "target_hit" if target_hit else "max_holding_timeout" if timed_out else "jev_exit"
                return PolicyDecision(decision_id=ident, timestamp=now, action=PolicyAction.EXIT, confidence=jev.confidence if jev else 1.0, reasons=(reason,), policy_version="policy-v1", hypothesis_id=hypothesis.hypothesis_id)
            return PolicyDecision(decision_id=ident, timestamp=now, action=PolicyAction.HOLD, confidence=jev.confidence if jev else 0.0, reasons=("position_aware_hold",), policy_version="policy-v1", hypothesis_id=hypothesis.hypothesis_id)
        if hypothesis.abstain:
            reasons.append("frontier_abstain")
        if economic_value is None:
            reasons.append("missing_economic_value")
        elif economic_value.sample_size < self.settings.quant.path_minimum_samples:
            reasons.append("insufficient_path_samples")
        elif economic_value.net_expected_value < self.thresholds.minimum_net_expected_value:
            reasons.append("net_expected_value_below_minimum")
        if jev is None:
            reasons.append("missing_jev")
        elif jev.recommended_state != JevState.ENTER:
            reasons.append(f"jev_state_{jev.recommended_state.value.lower()}")
        if jev and jev.probabilities.get("stop", 1.0) > self.thresholds.maximum_failure_probability:
            reasons.append("jev_failure_probability_exceeded")
        if environment.liquidity.top_level_notional is not None and environment.liquidity.top_level_notional < self.thresholds.minimum_liquidity_notional:
            reasons.append("liquidity_below_minimum")
        if reasons:
            action = PolicyAction.NO_TRADE
            output_candidate = None
        else:
            action = PolicyAction.ENTER_LONG if candidate and candidate.side == Side.LONG else PolicyAction.ENTER_SHORT
            output_candidate = candidate
            reasons.append("deterministic_entry_gates_passed")
        return PolicyDecision(decision_id=ident, timestamp=now, action=action, confidence=(jev.confidence if jev else 0.0), reasons=tuple(reasons), candidate=output_candidate, policy_version="policy-v1", hypothesis_id=hypothesis.hypothesis_id)

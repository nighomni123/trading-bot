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
    TradeProposal,
)
from .conditions import evaluate_condition


def resolve_levels(
    price: float,
    atr_fraction: float,
    policy: PolicyThresholds,
    proposal: TradeProposal | None,
) -> tuple[float, float, tuple[str, ...]]:
    """Stop and target distance in price units, plus any clamping notes.

    The model may propose both levels, and code still owns the bound: a proposal
    is clamped into the permitted ATR band, never adopted verbatim. An absurd
    stop therefore becomes the ATR default rather than the model's own number.
    """
    atr_bps = atr_fraction * 10_000
    minimum = price * policy.minimum_distance_bps / 10_000
    stop_default = max(price * atr_fraction * policy.stop_atr_multiple, minimum)
    target_default = max(price * atr_fraction * policy.target_atr_multiple, minimum)
    notes: list[str] = []

    if proposal is None or proposal.proposed_stop_bps is None:
        return stop_default, target_default, tuple(notes)

    floor_bps = policy.minimum_distance_bps
    stop_ceiling = min(atr_bps * policy.maximum_stop_atr_multiple, policy.maximum_stop_bps)
    stop_bps = min(max(proposal.proposed_stop_bps, floor_bps), stop_ceiling)
    if abs(stop_bps - proposal.proposed_stop_bps) > 1e-9:
        notes.append("stop_clamped_to_policy_band")

    target_bps: float | None = None
    if proposal.proposed_target_bps is not None:
        target_ceiling = min(atr_bps * policy.maximum_target_atr_multiple, policy.maximum_target_bps)
        target_bps = min(max(proposal.proposed_target_bps, floor_bps), target_ceiling)
        if abs(target_bps - proposal.proposed_target_bps) > 1e-9:
            notes.append("target_clamped_to_policy_band")

    stop_distance = max(price * stop_bps / 10_000, minimum)
    target_distance = max(price * (target_bps or atr_bps * policy.target_atr_multiple) / 10_000, minimum)
    return stop_distance, target_distance, tuple(notes)


def make_candidate(
    environment: MarketEnvironment,
    hypothesis: StrategyHypothesis,
    *,
    settings: LiveSettings,
    strategy_registry=None,
) -> CandidateTrade | None:
    if hypothesis.abstain or hypothesis.direction.value == "NONE" or not hypothesis.primary_strategy:
        return None
    strategy_version = hypothesis.prompt_version
    if strategy_registry is not None:
        try:
            definition = strategy_registry.validate(
                hypothesis.primary_strategy,
                market=environment.instrument,
                regime=hypothesis.regime,
            )
        except ValueError:
            return None
        strategy_version = definition.version
    price = environment.price.last
    if price is None:
        return None
    state = environment.timeframes["15m"]
    atr_fraction = state.atr_fraction or 0.001
    proposal = hypothesis.trade_proposal
    stop_distance, target_distance, _ = resolve_levels(
        price, atr_fraction, settings.policy, proposal
    )
    holding = (
        min(proposal.intended_holding_seconds, settings.policy.maximum_holding_seconds)
        if proposal is not None
        else settings.policy.maximum_holding_seconds
    )
    # A conditional entry is only actionable once its rule provably holds. An
    # unmet condition is "not yet", not a rejection, so the runner keeps the
    # proposal alive until it triggers or expires.
    trigger = "immediate"
    if proposal is not None and proposal.entry_trigger == "ON_CONDITION":
        if not evaluate_condition(environment, proposal.entry_condition):
            return None
        trigger = proposal.entry_condition.describe() if proposal.entry_condition else "immediate"
    else:
        trigger = "; ".join(hypothesis.entry_conditions) or "immediate"

    if hypothesis.direction.value == "LONG":
        return CandidateTrade(side=Side.LONG, entry_reference=price, target=price + target_distance, stop=price - stop_distance,
                              max_holding_seconds=holding, strategy_id=hypothesis.primary_strategy,
                              strategy_version=strategy_version, entry_condition=trigger)
    return CandidateTrade(side=Side.SHORT, entry_reference=price, target=price - target_distance, stop=price + stop_distance,
                          max_holding_seconds=holding, strategy_id=hypothesis.primary_strategy,
                          strategy_version=strategy_version, entry_condition=trigger)


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
        require_jev: bool = True,
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
        proposal = hypothesis.trade_proposal
        if proposal is not None and now >= proposal.expires_at:
            # An expired proposal is an abstention, never a stale approval: the
            # model said this trade was good for a window, and the window closed.
            reasons.append("proposal_expired")
        if economic_value is None:
            reasons.append("missing_economic_value")
        elif economic_value.sample_size < self.settings.quant.path_minimum_samples:
            reasons.append("insufficient_path_samples")
        elif economic_value.net_expected_value < self.thresholds.minimum_net_expected_value:
            reasons.append("net_expected_value_below_minimum")
        if require_jev and jev is None:
            reasons.append("missing_jev")
        elif jev is not None and jev.recommended_state != JevState.ENTER:
            reasons.append(f"jev_state_{jev.recommended_state.value.lower()}")
        if jev is not None and jev.valid_until <= now:
            # An expired evaluation is an abstention, never a stale approval.
            reasons.append("jev_expired")
        if jev and jev.recommended_state == JevState.ENTER:
            if jev.target_probability is None or jev.entry_quality is None or jev.failure_risk is None:
                reasons.append("jev_evidence_incomplete")
            else:
                if jev.target_probability < self.settings.jev.minimum_target_probability:
                    reasons.append("jev_target_probability_below_minimum")
                if jev.entry_quality < self.settings.jev.minimum_entry_quality:
                    reasons.append("jev_entry_quality_below_minimum")
                if jev.failure_risk > self.settings.jev.maximum_failure_probability:
                    reasons.append("jev_failure_risk_above_maximum")
        if jev and jev.probabilities.get("stop", 1.0) > self.settings.jev.maximum_stop_probability:
            reasons.append("jev_stop_probability_above_maximum")
        if environment.liquidity.top_level_notional is None:
            # Policy cannot approve what it cannot measure.
            reasons.append("missing_liquidity")
        elif environment.liquidity.top_level_notional < self.thresholds.minimum_liquidity_notional:
            reasons.append("liquidity_below_minimum")
        if reasons:
            action = PolicyAction.NO_TRADE
            output_candidate = None
        else:
            action = PolicyAction.ENTER_LONG if candidate and candidate.side == Side.LONG else PolicyAction.ENTER_SHORT
            output_candidate = candidate
            reasons.append("deterministic_entry_gates_passed")
        return PolicyDecision(decision_id=ident, timestamp=now, action=action, confidence=(jev.confidence if jev else 0.0), reasons=tuple(reasons), candidate=output_candidate, policy_version="policy-v1", hypothesis_id=hypothesis.hypothesis_id)

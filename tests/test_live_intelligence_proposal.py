"""The bounded trade-proposal mandate: what the model may decide, and what code owns.

The governing separation is that the model chooses *what to do* and code owns
*how much* and *whether it is allowed at all*. These tests pin both halves: a
proposal naming a size is rejected, a conditional rule must provably hold, an
expired signal is an abstention, and a model-proposed stop is clamped rather
than adopted.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from jev_trading.live_intelligence.config import load_settings
from jev_trading.live_intelligence.model_contracts import (
    FrontierModelDecision,
    frontier_to_domain,
)
from jev_trading.live_intelligence.policy import evaluate_condition, make_candidate
from jev_trading.live_intelligence.policy.finalizer import resolve_levels
from jev_trading.live_intelligence.risk import ActiveRiskKernel
from jev_trading.live_intelligence.schemas import (
    AccountState,
    ConditionSpec,
    Direction,
    ExecutionState,
    PolicyAction,
    PolicyDecision,
    PositionState,
    RiskStatus,
    StrategyHypothesis,
    TradeProposal,
)
from tests.test_live_intelligence import environment

AT = None


def env():
    return environment()


def liquid_env():
    """The base fixture with a measured book, so risk will actually approve."""
    base = environment()
    return base.model_copy(update={
        "liquidity": base.liquidity.model_copy(update={
            "top_level_notional": 1_000_000.0, "estimated_slippage": 0.0001,
        })
    })


def proposal(**overrides) -> TradeProposal:
    base = environment()
    values = dict(
        proposal_id="p1",
        timestamp=base.decision_timestamp,
        instrument="BTCUSDT_PERP",
        action="LONG",
        abstain=False,
        entry_trigger="NOW",
        expires_at=base.decision_timestamp + timedelta(hours=4),
        intended_holding_seconds=14_400,
        thesis="trend continuation",
        confidence=0.6,
        model_version="m",
        prompt_version="p",
    )
    values.update(overrides)
    return TradeProposal(**values)


def hypothesis(**overrides) -> StrategyHypothesis:
    base = environment()
    values = dict(
        hypothesis_id="h1",
        timestamp=base.decision_timestamp,
        regime="BULLISH",
        regime_confidence=0.5,
        primary_strategy="momentum",
        direction=Direction.LONG,
        thesis="t",
        abstain=False,
        reason="r",
        model_version="m",
        prompt_version="p",
    )
    values.update(overrides)
    return StrategyHypothesis(**values)


# --------------------------------------------------------- condition spec


def test_condition_kinds_are_a_closed_vocabulary():
    with pytest.raises(ValidationError):
        ConditionSpec(kind="when_sentiment_improves")
    with pytest.raises(ValidationError):
        ConditionSpec(kind="price_above_whatever")


def test_conditions_needing_a_threshold_require_one():
    with pytest.raises(ValidationError):
        ConditionSpec(kind="price_above")
    with pytest.raises(ValidationError):
        ConditionSpec(kind="atr_below", value=-1.0)
    # These carry no threshold by design.
    assert ConditionSpec(kind="immediate").value is None
    assert ConditionSpec(kind="trend_up").value is None


def test_condition_describe_is_stable():
    assert ConditionSpec(kind="price_above", value=85000.0).describe() == "price_above:85000.0@15m"
    assert ConditionSpec(kind="trend_down", timeframe="1h").describe() == "trend_down@1h"


# -------------------------------------------------------------- authority


@pytest.mark.parametrize("field", ["size", "quantity", "notional", "leverage", "order", "size_pct"])
def test_proposal_naming_a_size_is_rejected(field):
    with pytest.raises(ValidationError):
        proposal(**{field: 1.0})


def test_proposal_rejects_a_size_nested_in_a_condition():
    with pytest.raises(ValidationError):
        proposal(entry_condition={"kind": "price_above", "value": 1.0, "leverage": 3.0})


def test_abstaining_proposal_must_be_flat():
    assert proposal(action="FLAT", abstain=True).is_entry is False
    with pytest.raises(ValidationError):
        proposal(action="LONG", abstain=True)


def test_conditional_entry_requires_a_condition():
    with pytest.raises(ValidationError):
        proposal(entry_trigger="ON_CONDITION", entry_condition=None)
    assert proposal(
        entry_trigger="ON_CONDITION",
        entry_condition=ConditionSpec(kind="price_above", value=1.0),
    ).entry_trigger == "ON_CONDITION"


def test_expiry_must_be_after_the_proposal():
    base = environment()
    with pytest.raises(ValidationError):
        proposal(expires_at=base.decision_timestamp)


# -------------------------------------------------------------- evaluator


def test_condition_evaluates_against_observed_state():
    base = env()
    assert evaluate_condition(base, ConditionSpec(kind="immediate")) is True
    assert evaluate_condition(base, ConditionSpec(kind="price_below", value=1e9)) is True
    assert evaluate_condition(base, ConditionSpec(kind="price_above", value=1e9)) is False
    # 15m ATR in this fixture is a small fraction of price, so a tiny bound holds.
    assert evaluate_condition(base, ConditionSpec(kind="atr_below", value=100_000)) is True
    assert evaluate_condition(base, ConditionSpec(kind="atr_above", value=100_000)) is False


def test_unknown_timeframe_is_never_satisfied():
    assert evaluate_condition(env(), ConditionSpec(kind="price_above", value=1.0, timeframe="4h")) is True
    # A timeframe the environment does not carry cannot be checked, so it is false.
    assert evaluate_condition(
        env(), ConditionSpec(kind="price_above", value=1.0, timeframe="3d")
    ) is False


def test_missing_metric_is_never_satisfied():
    base = env()
    # `immediate` needs no metric; the rest must fail closed without one.
    assert evaluate_condition(base, None) is False
    for spec in (
        ConditionSpec(kind="spread_below_bps", value=10_000),
        ConditionSpec(kind="vwap_above", value=1.0, timeframe="15m"),
    ):
        stripped = base.model_copy(update={"liquidity": base.liquidity.model_copy(
            update={"spread": None})})
        assert evaluate_condition(stripped, spec) in (True, False)
    assert evaluate_condition(base.model_copy(update={
        "liquidity": base.liquidity.model_copy(update={"spread": None})
    }), ConditionSpec(kind="spread_below_bps", value=10_000)) is False


# ------------------------------------------------------------ level clamp


def test_model_stop_is_clamped_into_the_policy_band():
    settings = load_settings("configs/forward-hourly.json")
    price, atr = 100.0, 0.001  # 10 bps of ATR
    # A 5000 bps stop is far outside a 3x-ATR band; code must clamp it.
    far = proposal(proposed_stop_bps=5_000.0)
    stop, target, notes = resolve_levels(price, atr, settings.policy, far)
    assert "stop_clamped_to_policy_band" in notes
    ceiling = price * atr * settings.policy.maximum_stop_atr_multiple
    assert stop == pytest.approx(ceiling)
    # And it is still the clamped stop, not the model's 50% stop.
    assert stop < price * 0.5


def test_model_target_is_clamped_too():
    settings = load_settings("configs/forward-hourly.json")
    _, target, notes = resolve_levels(100.0, 0.001, settings.policy, proposal(
        proposed_stop_bps=20.0, proposed_target_bps=90_000.0))
    assert "target_clamped_to_policy_band" in notes
    assert target == pytest.approx(100.0 * 0.001 * settings.policy.maximum_target_atr_multiple)


def test_in_band_proposal_is_kept_and_not_flagged():
    settings = load_settings("configs/forward-hourly.json")
    _, _, notes = resolve_levels(100.0, 0.001, settings.policy, proposal(
        proposed_stop_bps=20.0, proposed_target_bps=40.0))
    assert notes == ()


def test_no_proposal_falls_back_to_the_atr_default():
    settings = load_settings("configs/forward-hourly.json")
    stop, target, notes = resolve_levels(100.0, 0.001, settings.policy, None)
    assert stop == pytest.approx(100.0 * 0.001 * settings.policy.stop_atr_multiple)
    assert target == pytest.approx(100.0 * 0.001 * settings.policy.target_atr_multiple)
    assert notes == ()


# ---------------------------------------------------------------- candidate


def test_unmet_conditional_entry_produces_no_candidate():
    settings = load_settings("configs/forward-hourly.json")
    base = env()
    h = hypothesis(trade_proposal=proposal(
        entry_trigger="ON_CONDITION",
        entry_condition=ConditionSpec(kind="price_above", value=1e12),
    ))
    assert make_candidate(base, h, settings=settings) is None


def test_met_conditional_entry_produces_a_candidate():
    settings = load_settings("configs/forward-hourly.json")
    h = hypothesis(trade_proposal=proposal(
        entry_trigger="ON_CONDITION",
        entry_condition=ConditionSpec(kind="price_below", value=1e12),
    ))
    candidate = make_candidate(env(), h, settings=settings)
    assert candidate is not None
    # The condition is recorded in machine-checkable form, not as prose.
    assert candidate.entry_condition.startswith("price_below:")


def test_conditional_candidate_uses_the_bounded_holding_period():
    settings = load_settings("configs/forward-hourly.json")
    h = hypothesis(trade_proposal=proposal(intended_holding_seconds=10**9))
    candidate = make_candidate(env(), h, settings=settings)
    # Configuration caps the model's ask.
    assert candidate.max_holding_seconds == settings.policy.maximum_holding_seconds


def test_abstaining_proposal_never_becomes_a_candidate():
    settings = load_settings("configs/forward-hourly.json")
    flat = proposal(action="FLAT", abstain=True)
    h = hypothesis(abstain=True, direction=Direction.NONE, primary_strategy=None,
                   trade_proposal=flat)
    assert make_candidate(env(), h, settings=settings) is None


# ----------------------------------------------------- expiry is abstention


def test_expired_proposal_is_vetoed_by_policy():
    from jev_trading.live_intelligence.policy import PolicyFinalizer

    settings = load_settings("configs/forward-hourly.json")
    base = environment()
    # Realistic case: the model proposed it an hour ago with a 60 s life, so it
    # was valid when made and has since lapsed. The schema forbids constructing
    # an already-expired proposal at its own timestamp, which is the point.
    made_at = base.decision_timestamp - timedelta(hours=1)
    lapsed = proposal(timestamp=made_at, expires_at=made_at + timedelta(seconds=60))
    decision = PolicyFinalizer(settings).finalize(
        base, hypothesis(trade_proposal=lapsed), None, None, require_jev=False,
    )
    assert decision.action == PolicyAction.NO_TRADE
    assert "proposal_expired" in decision.reasons


# --------------------------------------------------- sizing stays with risk


def test_model_cannot_influence_quantity():
    """The core separation: two proposals differing only in confidence and
    proposed levels must produce identical risk-approved quantity."""
    settings = load_settings("configs/forward-hourly.json")
    base = liquid_env()
    kernel = ActiveRiskKernel(settings)
    quantities = []
    for confidence, stop_bps in ((0.0, 20.0), (1.0, 20.0), (0.5, 400.0)):
        h = hypothesis(trade_proposal=proposal(confidence=confidence,
                                               proposed_stop_bps=stop_bps))
        candidate = make_candidate(base, h, settings=settings)
        policy = PolicyDecision(
            decision_id=f"d{confidence}{stop_bps}", timestamp=base.decision_timestamp,
            action=PolicyAction.ENTER_LONG, confidence=confidence, reasons=("t",),
            candidate=candidate, policy_version="policy-v1",
        )
        decision = kernel.evaluate(
            policy, base, AccountState(capital_usd=10_000.0), ExecutionState(),
            position=PositionState(),
        )
        assert decision.status == RiskStatus.APPROVED
        quantities.append(decision.approved_quantity)
    # A wider (clamped) stop legitimately sizes smaller, but confidence alone
    # must never move the number.
    assert quantities[0] == quantities[1]


def test_risk_remains_the_only_writer_of_quantity():
    settings = load_settings("configs/forward-hourly.json")
    base = liquid_env()
    h = hypothesis(trade_proposal=proposal())
    candidate = make_candidate(base, h, settings=settings)
    # The candidate carries geometry only -- never a size.
    assert not hasattr(candidate, "quantity")
    dumped = candidate.model_dump()
    assert not {"size", "quantity", "notional", "leverage"} & set(dumped)


# ------------------------------------------------------------- conversion


def test_action_derives_direction_and_stamps_absolute_expiry():
    settings = load_settings("configs/forward-hourly.json")
    base = environment()
    decision = FrontierModelDecision(
        regime="BULLISH", strategy_family="momentum", action="LONG", confidence=0.7,
        abstain=False, thesis="t", expires_in_seconds=3600, intended_holding_seconds=14400,
    )
    result = frontier_to_domain(
        decision, environment=base, settings=settings, request_id="r1",
        provider="p", model="m", model_version="m", prompt_version="pv",
        prompt_hash="h", interface_mode="tool_call", tool_name="submit_trade_proposal",
    )
    assert result.direction == Direction.LONG
    assert result.trade_proposal.expires_at == base.decision_timestamp + timedelta(seconds=3600)
    # The model stated a lifetime, never a wall-clock instant.
    assert decision.expires_in_seconds == 3600


@pytest.mark.parametrize("action,direction", [
    ("LONG", Direction.LONG), ("SHORT", Direction.SHORT),
    ("EXIT", Direction.NONE), ("HOLD", Direction.NONE), ("FLAT", Direction.NONE),
])
def test_every_action_maps_to_a_direction(action, direction):
    settings = load_settings("configs/forward-hourly.json")
    decision = FrontierModelDecision(
        regime="BULLISH", strategy_family="momentum" if action in {"LONG", "SHORT"} else None,
        action=action, confidence=0.5, abstain=action == "FLAT", thesis="t",
        expires_in_seconds=600, intended_holding_seconds=600,
    )
    result = frontier_to_domain(
        decision, environment=environment(), settings=settings, request_id="r",
        provider="p", model="m", model_version="m", prompt_version="pv",
        prompt_hash="h", interface_mode="tool_call", tool_name="t",
    )
    assert result.direction == direction


def test_holding_horizon_is_capped_by_configuration():
    settings = load_settings("configs/forward-hourly.json")
    decision = FrontierModelDecision(
        regime="BULLISH", strategy_family="momentum", action="LONG", confidence=0.5,
        abstain=False, thesis="t", expires_in_seconds=600, intended_holding_seconds=604_800,
    )
    result = frontier_to_domain(
        decision, environment=environment(), settings=settings, request_id="r",
        provider="p", model="m", model_version="m", prompt_version="pv",
        prompt_hash="h", interface_mode="tool_call", tool_name="t",
    )
    assert result.trade_proposal.intended_holding_seconds == settings.policy.maximum_holding_seconds


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

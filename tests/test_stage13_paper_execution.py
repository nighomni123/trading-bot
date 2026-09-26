"""Stage 13: paper execution contract, accounting, and funding model."""
from __future__ import annotations

from datetime import timedelta

import pytest

from jev_trading.live_intelligence.config import load_settings
from jev_trading.live_intelligence.execution import PaperExecutor
from jev_trading.live_intelligence.risk import ActiveRiskKernel
from jev_trading.live_intelligence.schemas import (
    ExecutionIntent, PolicyAction, RiskDecision, RiskStatus, Side,
)
from tests.test_live_intelligence import environment


def _approved(decision_id: str, timestamp, quantity: float = 1.0) -> RiskDecision:
    return RiskDecision(
        decision_id=decision_id, timestamp=timestamp, status=RiskStatus.APPROVED,
        approved_quantity=quantity, approved_notional=quantity * 100,
        maximum_loss_usd=10, reasons=("fixture",), risk_config_version="fixture",
    )


def _at(env, timestamp, *, open_price: float, last_price: float | None = None, funding_rate: float | None = None,
        estimated_slippage: float | None = None):
    one = env.timeframes["1m"].model_copy(update={
        "open": open_price, "timestamp": timestamp,
        "bucket_start": timestamp - timedelta(minutes=1), "bucket_end": timestamp,
    })
    liquidity = env.liquidity
    if estimated_slippage is not None:
        liquidity = liquidity.model_copy(update={"estimated_slippage": estimated_slippage})
    return env.model_copy(update={
        "timestamp": timestamp,
        "decision_timestamp": timestamp,
        "timeframes": {**env.timeframes, "1m": one},
        "liquidity": liquidity,
        "price": env.price.model_copy(update={"last": open_price if last_price is None else last_price}),
        "derivatives": env.derivatives if funding_rate is None else env.derivatives.model_copy(update={"funding": funding_rate}),
    })


def _intent(action: PolicyAction, side: Side, created_at, *, stop: float, target: float) -> ExecutionIntent:
    return ExecutionIntent(
        intent_id=f"i-{action.value}", decision_id=f"d-{action.value}", mode="PAPER", action=action,
        side=side, quantity=1, reference_price=100, stop=stop, target=target,
        created_at=created_at, earliest_execution_at=created_at + timedelta(minutes=1),
        expires_at=created_at + timedelta(minutes=10), strategy_id="fixture", strategy_version="v1",
    )


def _round_trip(settings, *, entry_open: float, exit_open: float, side: Side, funding_rate: float | None = None,
                hold: timedelta = timedelta(0)):
    executor = PaperExecutor(settings, ActiveRiskKernel(settings))
    base = environment()
    stop, target = (90.0, 130.0) if side == Side.LONG else (130.0, 90.0)
    intent = _intent(
        PolicyAction.ENTER_LONG if side == Side.LONG else PolicyAction.ENTER_SHORT,
        side, base.decision_timestamp, stop=stop, target=target,
    )
    entry_env = _at(base, base.decision_timestamp + timedelta(minutes=1), open_price=entry_open, funding_rate=funding_rate)
    executor.execute(intent, _approved(intent.decision_id, entry_env.timestamp), entry_env)
    if hold:
        # Marking forward is what actually crosses a funding boundary.
        executor.mark(entry_open, entry_env.timestamp + hold)
    exit_at = entry_env.timestamp + hold
    exit_intent = ExecutionIntent(
        intent_id=f"exit-{side.value}", decision_id=f"exit-d-{side.value}", mode="PAPER", action=PolicyAction.EXIT,
        side=side, quantity=1, reference_price=exit_open, created_at=exit_at,
        earliest_execution_at=exit_at + timedelta(minutes=1), strategy_id="fixture",
    )
    exit_env = _at(base, exit_intent.earliest_execution_at, open_price=exit_open, funding_rate=funding_rate)
    executor.execute(exit_intent, _approved(exit_intent.decision_id, exit_env.timestamp), exit_env)
    return executor


def test_next_open_execution():
    settings = load_settings()
    executor = PaperExecutor(settings, ActiveRiskKernel(settings))
    base = environment()
    intent = _intent(PolicyAction.ENTER_LONG, Side.LONG, base.decision_timestamp, stop=90, target=110)
    # The last price is irrelevant: only the next bucket's open can fill.
    fill_env = _at(base, base.decision_timestamp + timedelta(minutes=1), open_price=100.0, last_price=130.0)
    fill = executor.execute(intent, _approved(intent.decision_id, fill_env.timestamp), fill_env)
    assert fill.intended_price == 100.0
    assert fill.fill_price == pytest.approx(100.0 * (1 + settings.costs.slippage_bps_per_side / 10_000))
    assert fill.price_source == "primary_1m_open"
    assert fill.bar_timestamp == base.decision_timestamp
    assert fill.experiment_id == settings.experiment_id
    assert fill.execution_model_version == "paper-next-open-v1"

    # A later bucket is not the decision's execution bucket: no late fill.
    for bucket_start in (base.decision_timestamp - timedelta(minutes=5), base.decision_timestamp - timedelta(minutes=1)):
        fresh = PaperExecutor(settings, ActiveRiskKernel(settings))
        late = _at(base, base.decision_timestamp + timedelta(minutes=1), open_price=100.0)
        late = late.model_copy(update={"timeframes": {
            **late.timeframes,
            "1m": late.timeframes["1m"].model_copy(update={"bucket_start": bucket_start}),
        }})
        with pytest.raises(ValueError, match="next-open execution"):
            fresh.execute(intent, _approved(intent.decision_id, late.timestamp), late)
        assert fresh.position.side == Side.FLAT


def test_slippage_cap():
    settings = load_settings()
    executor = PaperExecutor(settings, ActiveRiskKernel(settings))
    base = environment()
    intent = _intent(PolicyAction.ENTER_LONG, Side.LONG, base.decision_timestamp, stop=90, target=110)
    wide = _at(base, base.decision_timestamp + timedelta(minutes=1), open_price=100.0,
               estimated_slippage=settings.risk.maximum_slippage_bps / 5_000 * 2)
    with pytest.raises(ValueError, match="exceeds the configured envelope"):
        executor.execute(intent, _approved(intent.decision_id, wide.timestamp), wide)
    assert executor.position.side == Side.FLAT

    expensive = settings.model_copy(update={
        "costs": settings.costs.model_copy(update={"slippage_bps_per_side": settings.risk.maximum_slippage_bps + 1.0}),
    })
    strict = PaperExecutor(expensive, ActiveRiskKernel(expensive))
    with pytest.raises(ValueError, match="exceeds the configured envelope"):
        strict.execute(intent, _approved(intent.decision_id, wide.timestamp), wide)


def test_liquidity_gate():
    """Unknown liquidity is a rejection in policy and in risk."""
    from jev_trading.live_intelligence.policy import PolicyFinalizer, make_candidate
    from jev_trading.live_intelligence.schemas import (
        AccountState, ExecutionState, PolicyDecision, PositionState,
    )
    from tests.test_live_intelligence import hypothesis
    settings = load_settings()
    kernel = ActiveRiskKernel(settings)
    base = environment()
    env = base.model_copy(update={"liquidity": base.liquidity.model_copy(update={"top_level_notional": None})})
    policy = PolicyDecision(
        decision_id="d", timestamp=env.decision_timestamp, action=PolicyAction.ENTER_LONG,
        reasons=("fixture",), candidate=make_candidate(env, hypothesis(), settings=settings),
        policy_version="fixture",
    )
    decision = kernel.evaluate(policy, env, AccountState(capital_usd=10_000), ExecutionState(), position=PositionState())
    assert decision.status == RiskStatus.REJECTED
    assert "missing_liquidity_data" in decision.reasons
    assert "missing_liquidity" in PolicyFinalizer(settings).finalize(
        env, hypothesis(), None, None, decision_id="d",
    ).reasons


def test_entry_fee_accounting():
    settings = load_settings()
    executor = _round_trip(settings, entry_open=100.0, exit_open=100.0, side=Side.LONG)
    trade = executor.last_trade
    assert trade.gross_pnl_usd == pytest.approx(0.0, abs=0.2)
    assert trade.fees_usd == pytest.approx(2 * 100.0 * settings.costs.fee_bps_per_side / 10_000, rel=0.05)
    assert trade.net_pnl_usd == pytest.approx(trade.gross_pnl_usd - trade.fees_usd)


def test_exit_fee_accounting():
    settings = load_settings()
    executor = _round_trip(settings, entry_open=100.0, exit_open=110.0, side=Side.LONG)
    trade = executor.last_trade
    assert trade.gross_pnl_usd > 0
    assert trade.fees_usd > 0
    assert trade.net_pnl_usd == pytest.approx(trade.gross_pnl_usd - trade.fees_usd - trade.funding_usd)
    assert trade.slippage_usd > 0
    assert executor.cumulative_fees == pytest.approx(trade.fees_usd)


def test_short_round_trip_pnl():
    settings = load_settings()
    executor = _round_trip(settings, entry_open=100.0, exit_open=90.0, side=Side.SHORT)
    trade = executor.last_trade
    assert trade.side == Side.SHORT
    assert trade.gross_pnl_usd > 0
    assert trade.net_pnl_usd > 0


def test_funding():
    settings = load_settings()
    live = settings.model_copy(update={"paper": settings.paper.model_copy(update={"funding_model": "LIVE"})})
    eight_hours = timedelta(hours=8)
    paid = _round_trip(live, entry_open=100.0, exit_open=100.0, side=Side.LONG, funding_rate=0.001, hold=eight_hours)
    received = _round_trip(live, entry_open=100.0, exit_open=100.0, side=Side.SHORT, funding_rate=0.001, hold=eight_hours)
    # The long pays and the short receives across the same funding interval.
    assert paid.last_trade.funding_usd > 0
    assert received.last_trade.funding_usd < 0
    unheld = _round_trip(live, entry_open=100.0, exit_open=100.0, side=Side.LONG, funding_rate=0.001)
    assert unheld.last_trade.funding_usd == 0.0, "no funding event was spanned"
    disabled = _round_trip(settings, entry_open=100.0, exit_open=100.0, side=Side.LONG, funding_rate=0.001, hold=eight_hours)
    assert disabled.last_trade.funding_usd == 0.0
    assert disabled.cumulative_funding == 0.0


def test_stop_target_and_timeout_paths_are_deterministic():
    settings = load_settings()
    gap = PaperExecutor(settings, ActiveRiskKernel(settings))
    base = environment()
    intent = _intent(PolicyAction.ENTER_LONG, Side.LONG, base.decision_timestamp, stop=95, target=105)
    gap_env = _at(base, base.decision_timestamp + timedelta(minutes=1), open_price=90.0)
    with pytest.raises(ValueError, match="outside planned barriers"):
        gap.execute(intent, _approved(intent.decision_id, gap_env.timestamp), gap_env)
    assert gap.position.side == Side.FLAT and gap.last_trade is None

    from jev_trading.live_intelligence.policy import PolicyFinalizer
    from jev_trading.live_intelligence.schemas import PositionState
    policy = PolicyFinalizer(settings)
    long_position = PositionState(
        side=Side.LONG, quantity=1, entry_price=100.0, current_stop=99.0, current_target=110.0,
        opened_at=base.decision_timestamp - timedelta(minutes=1),
    )
    stopped = policy.finalize(
        _at(base, base.decision_timestamp, open_price=100.0, last_price=98.0),
        __import__("tests.test_live_intelligence", fromlist=["hypothesis"]).hypothesis(),
        None, None, position=long_position, decision_id="d",
    )
    assert stopped.action == PolicyAction.EXIT and "stop_hit" in stopped.reasons
    timed_out = policy.finalize(
        _at(base, base.decision_timestamp, open_price=100.0, last_price=101.0),
        __import__("tests.test_live_intelligence", fromlist=["hypothesis"]).hypothesis(),
        None, None, position=long_position.model_copy(update={
            "opened_at": base.decision_timestamp - timedelta(seconds=settings.policy.maximum_holding_seconds + 1),
        }), decision_id="d",
    )
    assert timed_out.action == PolicyAction.EXIT and "max_holding_timeout" in timed_out.reasons
    held = policy.finalize(
        _at(base, base.decision_timestamp, open_price=100.0, last_price=101.0),
        __import__("tests.test_live_intelligence", fromlist=["hypothesis"]).hypothesis(),
        None, None, position=long_position, decision_id="d",
    )
    assert held.action == PolicyAction.HOLD

"""Next-open, gap, fee, funding, and explicit partial-fill semantics."""
from __future__ import annotations

from datetime import timedelta

import pytest

from jev_trading.live_intelligence.config import PaperConfig, load_settings
from jev_trading.live_intelligence.execution import PaperExecutor
from jev_trading.live_intelligence.risk import ActiveRiskKernel
from jev_trading.live_intelligence.schemas import ExecutionIntent, PolicyAction, RiskDecision, RiskStatus, Side
from tests.test_live_intelligence import environment


def _approved(decision_id: str, timestamp, quantity: float = 1.0) -> RiskDecision:
    return RiskDecision(
        decision_id=decision_id, timestamp=timestamp, status=RiskStatus.APPROVED,
        approved_quantity=quantity, approved_notional=quantity * 100,
        maximum_loss_usd=10, reasons=("fixture",), risk_config_version="fixture",
    )


def _at(env, timestamp, *, open_price: float, last_price: float | None = None, funding_rate: float | None = None):
    one = env.timeframes["1m"].model_copy(update={"open": open_price, "timestamp": timestamp})
    derivatives = env.derivatives if funding_rate is None else env.derivatives.model_copy(update={"funding": funding_rate})
    return env.model_copy(update={
        "timestamp": timestamp,
        "decision_timestamp": timestamp,
        "timeframes": {**env.timeframes, "1m": one},
        "price": env.price.model_copy(update={"last": open_price if last_price is None else last_price}),
        "derivatives": derivatives,
    })


def _intent(action: PolicyAction, side: Side, created_at, *, stop: float, target: float) -> ExecutionIntent:
    return ExecutionIntent(
        intent_id=f"i-{action.value}", decision_id=f"d-{action.value}", mode="PAPER", action=action,
        side=side, quantity=1, reference_price=100, stop=stop, target=target,
        created_at=created_at, earliest_execution_at=created_at + timedelta(minutes=1),
        expires_at=created_at + timedelta(minutes=10), strategy_id="fixture", strategy_version="v1",
    )


def test_fill_uses_next_one_minute_open_not_last_price():
    settings = load_settings()
    executor = PaperExecutor(settings, ActiveRiskKernel(settings))
    base = environment()
    created = base.decision_timestamp
    intent = _intent(PolicyAction.ENTER_LONG, Side.LONG, created, stop=90, target=110)
    fill_env = _at(base, created + timedelta(minutes=1), open_price=100, last_price=130)
    fill = executor.execute(intent, _approved(intent.decision_id, fill_env.timestamp), fill_env)
    assert fill.intended_price == 100
    assert fill.fill_price == pytest.approx(100.02)


def test_long_and_short_round_trips_work():
    settings = load_settings()
    base = environment()
    for action, side, stop, target, exit_open in (
        (PolicyAction.ENTER_LONG, Side.LONG, 90, 110, 105),
        (PolicyAction.ENTER_SHORT, Side.SHORT, 110, 90, 95),
    ):
        executor = PaperExecutor(settings, ActiveRiskKernel(settings))
        created = base.decision_timestamp
        entry = _intent(action, side, created, stop=stop, target=target)
        entry_env = _at(base, created + timedelta(minutes=1), open_price=100)
        executor.execute(entry, _approved(entry.decision_id, entry_env.timestamp), entry_env)
        assert executor.position.side == side
        exit_intent = ExecutionIntent(
            intent_id=f"exit-{side.value}", decision_id=f"exit-d-{side.value}", mode="PAPER",
            action=PolicyAction.EXIT, side=side, quantity=1, reference_price=exit_open,
            created_at=entry_env.timestamp, earliest_execution_at=entry_env.timestamp + timedelta(minutes=1),
            strategy_id="fixture",
        )
        exit_env = _at(base, exit_intent.earliest_execution_at, open_price=exit_open)
        executor.execute(exit_intent, _approved(exit_intent.decision_id, exit_env.timestamp), exit_env)
        assert executor.position.side == Side.FLAT
        assert executor.last_trade is not None
        assert executor.last_trade.net_pnl_usd > 0


def test_entry_gap_through_stop_is_rejected_without_position():
    settings = load_settings()
    executor = PaperExecutor(settings, ActiveRiskKernel(settings))
    base = environment()
    created = base.decision_timestamp
    intent = _intent(PolicyAction.ENTER_LONG, Side.LONG, created, stop=95, target=105)
    gap_env = _at(base, created + timedelta(minutes=1), open_price=90)
    with pytest.raises(ValueError, match="outside planned barriers"):
        executor.execute(intent, _approved(intent.decision_id, gap_env.timestamp), gap_env)
    assert executor.position.side == Side.FLAT
    assert executor.last_trade is None


def test_trade_net_pnl_includes_entry_and_exit_fees():
    settings = load_settings()
    executor = PaperExecutor(settings, ActiveRiskKernel(settings))
    base = environment()
    created = base.decision_timestamp
    entry = _intent(PolicyAction.ENTER_LONG, Side.LONG, created, stop=90, target=110)
    entry_env = _at(base, created + timedelta(minutes=1), open_price=100)
    entry_fill = executor.execute(entry, _approved(entry.decision_id, entry_env.timestamp), entry_env)
    exit_intent = ExecutionIntent(
        intent_id="exit-fees", decision_id="exit-d-fees", mode="PAPER", action=PolicyAction.EXIT,
        side=Side.LONG, quantity=1, reference_price=102, created_at=entry_env.timestamp,
        earliest_execution_at=entry_env.timestamp + timedelta(minutes=1), strategy_id="fixture",
    )
    exit_env = _at(base, exit_intent.earliest_execution_at, open_price=102)
    exit_fill = executor.execute(exit_intent, _approved(exit_intent.decision_id, exit_env.timestamp), exit_env)
    trade = executor.last_trade
    assert trade.fees_usd == pytest.approx(entry_fill.fee_usd + exit_fill.fee_usd)
    assert trade.net_pnl_usd == pytest.approx(trade.gross_pnl_usd - trade.fees_usd)


def test_visible_funding_rate_is_charged_once_per_interval():
    settings = load_settings()
    executor = PaperExecutor(settings, ActiveRiskKernel(settings))
    base = environment()
    created = base.decision_timestamp
    entry = _intent(PolicyAction.ENTER_LONG, Side.LONG, created, stop=90, target=120)
    entry_env = _at(base, created + timedelta(minutes=1), open_price=100, funding_rate=0.001)
    executor.execute(entry, _approved(entry.decision_id, entry_env.timestamp), entry_env)
    funding_time = entry_env.timestamp + timedelta(hours=8)
    executor.mark(100, funding_time)
    executor.mark(100, funding_time + timedelta(minutes=1))
    assert executor.cumulative_funding == pytest.approx(0.1)
    exit_intent = ExecutionIntent(
        intent_id="exit-funding", decision_id="exit-d-funding", mode="PAPER", action=PolicyAction.EXIT,
        side=Side.LONG, quantity=1, reference_price=100, created_at=funding_time,
        earliest_execution_at=funding_time + timedelta(minutes=1), strategy_id="fixture",
    )
    exit_env = _at(base, exit_intent.earliest_execution_at, open_price=100, funding_rate=0.001)
    executor.execute(exit_intent, _approved(exit_intent.decision_id, exit_env.timestamp), exit_env)
    assert executor.last_trade.funding_usd == pytest.approx(0.1)
    assert executor.last_trade.net_pnl_usd == pytest.approx(
        executor.last_trade.gross_pnl_usd - executor.last_trade.fees_usd - executor.last_trade.funding_usd
    )


def test_partial_fill_configuration_is_explicitly_removed():
    assert "partial_fill_ratio" not in PaperConfig.model_fields

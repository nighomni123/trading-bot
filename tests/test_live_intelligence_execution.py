"""Pending next-bar paper execution and close-trade tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jev_trading.live_intelligence.config import load_settings
from jev_trading.live_intelligence.execution import PaperExecutor
from jev_trading.live_intelligence.risk import ActiveRiskKernel
from jev_trading.live_intelligence.schemas import ExecutionIntent, PolicyAction, RiskDecision, RiskStatus, Side
from tests.test_live_intelligence import environment

UTC = timezone.utc


def approved(decision_id: str, quantity: float, timestamp: datetime) -> RiskDecision:
    return RiskDecision(decision_id=decision_id, timestamp=timestamp, status=RiskStatus.APPROVED, approved_quantity=quantity, approved_notional=quantity * 100, maximum_loss_usd=quantity, reasons=("test",), risk_config_version="test")


def test_paper_entry_waits_for_next_bar_and_exit_creates_trade(tmp_path: Path):
    settings = load_settings()
    risk = ActiveRiskKernel(settings)
    executor = PaperExecutor(settings, risk)
    env = environment(now=datetime.now(UTC))
    decision_time = env.decision_timestamp
    entry_intent = ExecutionIntent(intent_id="entry-intent", decision_id="entry-decision", mode="PAPER", action=PolicyAction.ENTER_LONG, side=Side.LONG, quantity=0.1, reference_price=100, stop=99, target=102, created_at=decision_time, earliest_execution_at=decision_time + timedelta(minutes=1), strategy_id="momentum")
    entry_risk = approved("entry-decision", 0.1, decision_time)
    with pytest.raises(ValueError):
        executor.execute(entry_intent, entry_risk, env)
    later_time = decision_time + timedelta(minutes=1)
    one_minute = env.timeframes["1m"].model_copy(update={"open": 100.0, "timestamp": later_time})
    later = env.model_copy(update={
        "timestamp": later_time,
        "decision_timestamp": later_time,
        "timeframes": {**env.timeframes, "1m": one_minute},
    })
    entry_fill = executor.execute(entry_intent, entry_risk, later)
    assert entry_fill.mode.value == "PAPER"
    assert executor.position.side == Side.LONG
    exit_intent = ExecutionIntent(intent_id="exit-intent", decision_id="exit-decision", mode="PAPER", action=PolicyAction.EXIT, side=Side.LONG, quantity=0.1, reference_price=102, created_at=later.timestamp, earliest_execution_at=later.timestamp + timedelta(minutes=1), strategy_id="momentum")
    exit_risk = approved("exit-decision", 0.1, later.timestamp)
    exit_time = later.timestamp + timedelta(minutes=1)
    exit_one_minute = one_minute.model_copy(update={"open": 102.0, "timestamp": exit_time})
    exit_env = later.model_copy(update={
        "timestamp": exit_time,
        "decision_timestamp": exit_time,
        "timeframes": {**later.timeframes, "1m": exit_one_minute},
        "price": later.price.model_copy(update={"last": 102.0}),
    })
    exit_fill = executor.execute(exit_intent, exit_risk, exit_env)
    assert exit_fill.mode.value == "PAPER"
    assert executor.position.side == Side.FLAT
    assert executor.last_trade is not None
    assert executor.last_trade.entry_decision_id == "entry-decision"
    assert executor.last_trade.exit_decision_id == "exit-decision"

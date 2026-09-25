"""Additional safety invariants for the active architecture."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from jev_trading.live_intelligence.schemas import (
    CandidateTrade,
    EconomicValue,
    ExecutionIntent,
    JevEvaluation,
    PolicyAction,
    PolicyDecision,
    RiskDecision,
    RiskStatus,
    Side,
)
from jev_trading.live_intelligence.execution import PaperExecutor
from jev_trading.live_intelligence.config import load_settings
from jev_trading.live_intelligence.risk import ActiveRiskKernel
from tests.test_live_intelligence import environment, hypothesis, safe_quality

UTC = timezone.utc


def test_risk_rejects_excessive_spread():
    settings = load_settings()
    env = environment()
    env = env.model_copy(update={"liquidity": env.liquidity.model_copy(update={"spread": 0.002})})
    candidate = CandidateTrade(side=Side.LONG, entry_reference=100, target=102, stop=99, max_holding_seconds=900, strategy_id="momentum", strategy_version="v1", entry_condition="test")
    policy = PolicyDecision(decision_id="d", timestamp=env.decision_timestamp, action=PolicyAction.ENTER_LONG, reasons=(), candidate=candidate, policy_version="test")
    result = ActiveRiskKernel(settings).evaluate(policy, env, __import__("jev_trading.live_intelligence.schemas", fromlist=["AccountState"]).AccountState(capital_usd=10000), __import__("jev_trading.live_intelligence.schemas", fromlist=["ExecutionState"]).ExecutionState(), position=__import__("jev_trading.live_intelligence.schemas", fromlist=["PositionState"]).PositionState())
    assert result.status == RiskStatus.REJECTED
    assert "spread_limit" in result.reasons


def test_malformed_jev_cannot_create_execution_intent():
    with pytest.raises(ValidationError):
        JevEvaluation(decision_id="d", request_id="r", timestamp=datetime.now(UTC), valid_until=datetime.now(UTC), confidence=2, recommended_state="ENTER", reason="bad", model_version="x", prompt_version="jev-evaluator-v1")


def test_execution_intent_rejects_live_mode_and_wrong_side():
    now = datetime.now(UTC)
    common = dict(intent_id="i", decision_id="d", reference_price=100, created_at=now, earliest_execution_at=now + timedelta(minutes=1), strategy_id="x", quantity=1)
    with pytest.raises(ValidationError):
        ExecutionIntent(mode="LIVE", action=PolicyAction.ENTER_LONG, side=Side.LONG, **common)
    with pytest.raises(ValidationError):
        ExecutionIntent(mode="PAPER", action=PolicyAction.ENTER_LONG, side=Side.SHORT, **common)


def test_rejected_risk_decision_cannot_be_positive():
    with pytest.raises(ValidationError):
        RiskDecision(decision_id="d", timestamp=datetime.now(UTC), status=RiskStatus.REJECTED, approved_quantity=1, approved_notional=100, reasons=("x",), risk_config_version="v")


def test_paper_executor_refuses_unapproved_risk():
    settings = load_settings()
    now = datetime.now(UTC)
    intent = ExecutionIntent(intent_id="i", decision_id="d", mode="PAPER", action=PolicyAction.ENTER_LONG, side=Side.LONG, quantity=1, reference_price=100, created_at=now, earliest_execution_at=now + timedelta(minutes=1), strategy_id="x")
    risk = RiskDecision(decision_id="d", timestamp=now, status=RiskStatus.REJECTED, reasons=("blocked",), risk_config_version="v")
    with pytest.raises(ValueError):
        PaperExecutor(settings, ActiveRiskKernel(settings)).execute(intent, risk, environment(now=now))

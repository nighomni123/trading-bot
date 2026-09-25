"""Semantic ledger links and durable checkpoint tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jev_trading.ledger import DecisionLedger
from jev_trading.live_intelligence.config import load_settings
from jev_trading.live_intelligence.execution import PaperExecutor
from jev_trading.live_intelligence.frontier.client import DisabledFrontierClient
from jev_trading.live_intelligence.jev.client import DisabledJevClient
from jev_trading.live_intelligence.risk import ActiveRiskKernel
from jev_trading.live_intelligence.runner import ShadowRunner
from jev_trading.live_intelligence.schemas import (
    AccountState, DecisionRecord, ExecutionIntent, ExecutionState, PaperFill,
    PolicyAction, PolicyDecision, PositionState, RiskDecision, RiskStatus, Side, Versions,
)
from jev_trading.data.normalization import ReplayAdapter
from tests.test_live_intelligence import bars, environment, hypothesis, safe_quality


def _approved_decision(tmp_path: Path):
    settings = load_settings()
    env = environment(quality=safe_quality())
    env = env.model_copy(update={"liquidity": env.liquidity.model_copy(update={"top_level_notional": 100000.0})})
    candidate = __import__("jev_trading.live_intelligence.policy", fromlist=["make_candidate"]).make_candidate(env, hypothesis(), settings=settings)
    policy = PolicyDecision(
        decision_id="d", timestamp=env.decision_timestamp, action=PolicyAction.ENTER_LONG,
        reasons=("fixture",), candidate=candidate, policy_version="fixture",
    )
    risk = ActiveRiskKernel(settings).evaluate(policy, env, AccountState(capital_usd=10000), ExecutionState(), position=PositionState())
    intent = ExecutionIntent(
        intent_id="i", decision_id="d", mode="PAPER", action=PolicyAction.ENTER_LONG,
        side=Side.LONG, quantity=risk.approved_quantity, reference_price=env.price.last,
        stop=candidate.stop, target=candidate.target, created_at=env.decision_timestamp,
        earliest_execution_at=env.decision_timestamp + timedelta(minutes=1),
        strategy_id="momentum", strategy_version="v1",
    )
    record = DecisionRecord(
        decision_id="d", experiment_id=settings.experiment_id, timestamp=env.decision_timestamp,
        market_environment=env, frontier_hypothesis=hypothesis(), policy_decision=policy,
        risk_decision=risk, execution_intent=intent, position_before=PositionState(), position_after=PositionState(),
        versions=Versions(code_version="test", experiment_id=settings.experiment_id, frontier_model="f", frontier_prompt="p", jev_model="j", jev_prompt="p", quant_analyzers={}, policy="p", risk="r", strategy_registry="s"),
    )
    return settings, record, intent, risk


def test_fill_must_reference_the_decision_intent(tmp_path: Path):
    _settings, record, intent, _risk = _approved_decision(tmp_path)
    ledger = DecisionLedger(tmp_path / "ledger.jsonl")
    ledger.append_decision(record)
    bogus = PaperFill(
        fill_id="f", intent_id="not-the-intent", decision_id="d",
        execution_timestamp=intent.earliest_execution_at, intended_price=100,
        fill_price=100, quantity=intent.quantity, fee_usd=0, slippage_usd=0, status="FILLED", mode="PAPER",
    )
    with pytest.raises(ValueError, match="execution intent"):
        ledger.append_fill(bogus)
    valid = bogus.model_copy(update={"intent_id": intent.intent_id})
    ledger.append_fill(valid)
    assert ledger.fills()[0].intent_id == intent.intent_id


def test_checkpoint_restores_or_fails_closed(tmp_path: Path):
    settings = load_settings().model_copy(update={
        "research": load_settings().research.model_copy(update={"root": str(tmp_path / "research")}),
        "observability": load_settings().observability.model_copy(update={"metrics_file": str(tmp_path / "metrics.json")}),
    })
    ledger_path = tmp_path / "ledger.jsonl"
    adapter = ReplayAdapter("primary", "binance-futures", "BTCUSDT_PERP", bars(), [])
    runner = ShadowRunner(settings, adapter, DisabledFrontierClient(), DisabledJevClient(), ledger_path=ledger_path)
    record = runner.run_once()
    assert runner.checkpoint_path.exists()
    restored = ShadowRunner(settings, adapter, DisabledFrontierClient(), DisabledJevClient(), ledger_path=ledger_path)
    assert restored.ledger.last_hash == runner.ledger.last_hash
    assert restored.account.capital_usd == runner.account.capital_usd
    assert restored.position == runner.position
    runner.checkpoint_path.write_text('{"schema_version":1,"ledger_hash":"wrong"}')
    with pytest.raises(RuntimeError, match="checkpoint"):
        ShadowRunner(settings, adapter, DisabledFrontierClient(), DisabledJevClient(), ledger_path=ledger_path)


def test_missing_checkpoint_for_nonempty_ledger_fails_closed(tmp_path: Path):
    settings = load_settings()
    ledger_path = tmp_path / "ledger.jsonl"
    runner = ShadowRunner(settings, ReplayAdapter("primary", "binance-futures", "BTCUSDT_PERP", bars(), []), DisabledFrontierClient(), DisabledJevClient(), ledger_path=ledger_path)
    runner.run_once()
    runner.checkpoint_path.unlink()
    with pytest.raises(RuntimeError, match="checkpoint"):
        ShadowRunner(settings, ReplayAdapter("primary", "binance-futures", "BTCUSDT_PERP", bars(), []), DisabledFrontierClient(), DisabledJevClient(), ledger_path=ledger_path)

"""Authoritative active-risk configuration and runtime state tests."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from jev_trading.data.normalization import ReplayAdapter
from jev_trading.live_intelligence.config import load_settings
from jev_trading.live_intelligence.execution import PaperExecutor
from jev_trading.live_intelligence.frontier.client import DisabledFrontierClient
from jev_trading.live_intelligence.jev.client import DisabledJevClient
from jev_trading.live_intelligence.policy import make_candidate
from jev_trading.live_intelligence.risk import ActiveRiskKernel
from jev_trading.live_intelligence.runner import ShadowRunner
from jev_trading.live_intelligence.schemas import (
    AccountState,
    ExecutionIntent,
    ExecutionState,
    PolicyAction,
    PolicyDecision,
    PositionState,
    RiskStatus,
    Side,
)
from tests.test_live_intelligence import bars, environment, hypothesis, safe_quality


def _entry(settings):
    env = environment(quality=safe_quality())
    env = env.model_copy(update={"liquidity": env.liquidity.model_copy(update={"top_level_notional": 100_000.0})})
    candidate = make_candidate(env, hypothesis(), settings=settings)
    policy = PolicyDecision(
        decision_id="d", timestamp=env.decision_timestamp, action=PolicyAction.ENTER_LONG,
        reasons=("fixture",), candidate=candidate, policy_version="fixture",
    )
    return env, candidate, policy


def test_configured_kill_switch_rejects_entry(tmp_path: Path):
    settings = load_settings().model_copy(update={
        "risk": load_settings().risk.model_copy(update={"kill_switch": True}),
    })
    env, _, policy = _entry(settings)
    result = ActiveRiskKernel(settings).evaluate(
        policy, env, AccountState(capital_usd=10_000), ExecutionState(), position=PositionState(),
    )
    assert result.status == RiskStatus.REJECTED
    assert "configured_kill_switch" in result.reasons


def test_daily_loss_and_drawdown_reject_entry():
    settings = load_settings()
    env, _, policy = _entry(settings)
    risk = ActiveRiskKernel(settings)
    daily = risk.evaluate(
        policy, env,
        AccountState(capital_usd=10_000, daily_realized_pnl_usd=-100, peak_equity_usd=10_000),
        ExecutionState(), position=PositionState(),
    )
    assert daily.status == RiskStatus.REJECTED
    assert "daily_loss_limit" in daily.reasons
    drawdown = risk.evaluate(
        policy, env,
        AccountState(capital_usd=8_000, peak_equity_usd=10_000),
        ExecutionState(), position=PositionState(),
    )
    assert drawdown.status == RiskStatus.REJECTED
    assert "maximum_drawdown" in drawdown.reasons


def test_order_rate_and_cooldown_reject_entry():
    settings = load_settings()
    env, _, policy = _entry(settings)
    risk = ActiveRiskKernel(settings)
    for index in range(settings.risk.maximum_orders_per_minute + 1):
        risk.record_order(env.decision_timestamp - timedelta(seconds=index + 1))
    limited = risk.evaluate(policy, env, AccountState(capital_usd=10_000), ExecutionState(), position=PositionState())
    assert limited.status == RiskStatus.REJECTED
    assert "order_rate_limit" in limited.reasons
    cooled = risk.evaluate(
        policy, env, AccountState(capital_usd=10_000),
        ExecutionState(cooldown_until=env.decision_timestamp + timedelta(seconds=30)),
        position=PositionState(),
    )
    assert cooled.status == RiskStatus.REJECTED
    assert "cooldown" in cooled.reasons


def test_configured_slippage_cannot_bypass_risk_cap():
    settings = load_settings()
    settings = settings.model_copy(update={
        "costs": settings.costs.model_copy(update={"slippage_bps_per_side": 100.0}),
    })
    env, _, policy = _entry(settings)
    assert env.liquidity.estimated_slippage is None
    result = ActiveRiskKernel(settings).evaluate(
        policy, env, AccountState(capital_usd=10_000), ExecutionState(), position=PositionState(),
    )
    assert result.status == RiskStatus.REJECTED
    assert "slippage_limit" in result.reasons


def test_runner_syncs_open_position_and_order_state_after_fill(tmp_path: Path):
    settings = load_settings()
    runner = ShadowRunner(
        settings,
        ReplayAdapter("primary", "binance-futures", "BTCUSDT_PERP", bars(), []),
        DisabledFrontierClient(), DisabledJevClient(), ledger_path=tmp_path / "ledger.jsonl",
    )
    env, candidate, _ = _entry(settings)
    risk = ActiveRiskKernel(settings).evaluate(
        PolicyDecision(
            decision_id="d", timestamp=env.decision_timestamp, action=PolicyAction.ENTER_LONG,
            reasons=("fixture",), candidate=candidate, policy_version="fixture",
        ),
        env, AccountState(capital_usd=10_000), ExecutionState(), position=PositionState(),
    )
    intent = ExecutionIntent(
        intent_id="i", decision_id="d", mode="PAPER", action=PolicyAction.ENTER_LONG,
        side=Side.LONG, quantity=risk.approved_quantity, reference_price=env.price.last,
        stop=candidate.stop, target=candidate.target, created_at=env.decision_timestamp,
        earliest_execution_at=env.decision_timestamp + timedelta(minutes=1),
        expires_at=env.decision_timestamp + timedelta(minutes=2), strategy_id="momentum",
    )
    executor = PaperExecutor(settings, runner.risk)
    fill_time = intent.earliest_execution_at
    one_minute = env.timeframes["1m"].model_copy(update={"open": env.price.last, "timestamp": fill_time})
    fill_env = env.model_copy(update={
        "timestamp": fill_time,
        "decision_timestamp": fill_time,
        "timeframes": {**env.timeframes, "1m": one_minute},
    })
    executor.execute(intent, risk, fill_env)
    runner.paper = executor
    runner.position = executor.position
    runner.risk.record_order(env.decision_timestamp)
    runner._sync_runtime_state(env)
    assert runner.account.open_positions == 1
    assert runner.execution.orders_last_minute == 1
    assert runner.execution.feed_healthy is True

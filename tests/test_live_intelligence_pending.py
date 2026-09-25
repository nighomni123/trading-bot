"""Pending-intent revalidation invariants."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from jev_trading.live_intelligence.config import load_settings
from jev_trading.live_intelligence.policy import make_candidate
from jev_trading.live_intelligence.risk import ActiveRiskKernel
from jev_trading.live_intelligence.runner import ShadowRunner
from jev_trading.live_intelligence.schemas import (
    AccountState,
    DataQuality,
    ExecutionIntent,
    ExecutionState,
    PolicyAction,
    PolicyDecision,
    PositionState,
    RiskStatus,
    Side,
)
from tests.test_live_intelligence import bars, environment, hypothesis, safe_quality


def _pending_fixture(tmp_path: Path):
    settings = load_settings()
    env = environment(quality=safe_quality())
    env = env.model_copy(update={
        "liquidity": env.liquidity.model_copy(update={"top_level_notional": 100_000.0}),
    })
    candidate = make_candidate(env, hypothesis(), settings=settings)
    policy = PolicyDecision(
        decision_id="pending-policy",
        timestamp=env.decision_timestamp,
        action=PolicyAction.ENTER_LONG,
        reasons=("fixture",),
        candidate=candidate,
        policy_version="fixture",
    )
    risk = ActiveRiskKernel(settings).evaluate(
        policy,
        env,
        AccountState(capital_usd=10_000.0, peak_equity_usd=10_000.0),
        ExecutionState(),
        position=PositionState(),
    )
    assert risk.status == RiskStatus.APPROVED
    intent = ExecutionIntent(
        intent_id="pending-intent",
        decision_id="pending-policy",
        mode="PAPER",
        action=PolicyAction.ENTER_LONG,
        side=Side.LONG,
        quantity=risk.approved_quantity,
        reference_price=env.price.last,
        stop=candidate.stop,
        target=candidate.target,
        created_at=env.decision_timestamp - timedelta(minutes=1),
        earliest_execution_at=env.decision_timestamp,
        expires_at=env.decision_timestamp + timedelta(minutes=10),
        strategy_id=candidate.strategy_id,
        strategy_version=candidate.strategy_version,
    )
    runner = ShadowRunner(
        settings,
        __import__("jev_trading.data.normalization", fromlist=["ReplayAdapter"]).ReplayAdapter(
            "primary", "binance-futures", "BTCUSDT_PERP", bars(), [],
        ),
        __import__("jev_trading.live_intelligence.frontier.client", fromlist=["DisabledFrontierClient"]).DisabledFrontierClient(),
        __import__("jev_trading.live_intelligence.jev.client", fromlist=["DisabledJevClient"]).DisabledJevClient(),
        ledger_path=tmp_path / "ledger.jsonl",
    )
    runner._pending_intent = intent
    runner._pending_risk = risk
    return runner, env, policy, risk, intent


def test_pending_order_is_cancelled_when_current_data_is_unsafe(tmp_path: Path):
    runner, env, policy, risk, _ = _pending_fixture(tmp_path)
    unsafe = env.model_copy(update={
        "data_quality": DataQuality(safe_for_trading=False, stale=True, missing_sources=("primary",)),
    })
    valid, executed = runner._revalidate_pending(unsafe, policy, risk, policy.candidate)
    assert (valid, executed) == (False, False)
    assert runner._pending_intent is None
    assert runner.ledger.fills() == ()


def test_pending_data_guard_is_independent_of_lower_layers(tmp_path: Path, monkeypatch):
    runner, env, policy, risk, _ = _pending_fixture(tmp_path)
    unsafe = env.model_copy(update={
        "data_quality": DataQuality(safe_for_trading=False, stale=False, missing_sources=("primary",)),
    })
    monkeypatch.setattr(runner.paper, "execute", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("executor reached")))
    valid, executed = runner._revalidate_pending(unsafe, policy, risk, policy.candidate)
    assert (valid, executed) == (False, False)
    assert runner._pending_intent is None


def test_pending_order_is_cancelled_when_kill_switch_is_enabled(tmp_path: Path):
    runner, env, policy, _, intent = _pending_fixture(tmp_path)
    runner.risk.halt("test-kill-switch")
    risk = runner.risk.evaluate(
        policy,
        env,
        AccountState(capital_usd=10_000.0, peak_equity_usd=10_000.0),
        ExecutionState(),
        position=PositionState(),
    )
    valid, executed = runner._revalidate_pending(env, policy, risk, policy.candidate)
    assert (valid, executed) == (False, False)
    assert runner._pending_intent is None
    assert runner.ledger.fills() == ()


def test_pending_order_is_cancelled_when_current_risk_limit_is_exceeded(tmp_path: Path):
    runner, env, policy, _, _ = _pending_fixture(tmp_path)
    risk = runner.risk.evaluate(
        policy,
        env,
        AccountState(
            capital_usd=10_000.0,
            peak_equity_usd=10_000.0,
            daily_realized_pnl_usd=-100.0,
        ),
        ExecutionState(),
        position=PositionState(),
    )
    valid, executed = runner._revalidate_pending(env, policy, risk, policy.candidate)
    assert (valid, executed) == (False, False)
    assert runner._pending_intent is None
    assert runner.ledger.fills() == ()


def test_expired_pending_order_is_cancelled(tmp_path: Path):
    runner, env, policy, risk, intent = _pending_fixture(tmp_path)
    runner._pending_intent = intent.model_copy(update={
        "expires_at": env.decision_timestamp - timedelta(seconds=1),
    })
    valid, executed = runner._revalidate_pending(env, policy, risk, policy.candidate)
    assert (valid, executed) == (False, False)
    assert runner._pending_intent is None
    assert runner.ledger.fills() == ()


def test_position_change_cancels_entry_pending_order(tmp_path: Path):
    runner, env, policy, risk, _ = _pending_fixture(tmp_path)
    changed = PositionState(
        side=Side.SHORT,
        quantity=0.1,
        entry_price=100.0,
        current_stop=101.0,
        current_target=99.0,
    )
    runner.position = changed
    runner.paper.position = changed
    valid, executed = runner._revalidate_pending(env, policy, risk, policy.candidate)
    assert (valid, executed) == (False, False)
    assert runner._pending_intent is None
    assert runner.ledger.fills() == ()
    assert runner.position.side == Side.SHORT

"""Position-aware stop, target, and timeout exits."""
from __future__ import annotations

from datetime import timedelta

from jev_trading.live_intelligence.config import load_settings
from jev_trading.live_intelligence.policy import PolicyFinalizer
from jev_trading.live_intelligence.schemas import JevState, PolicyAction, PositionState, Side
from tests.test_live_intelligence import environment, hypothesis


def open_position(**overrides) -> PositionState:
    values = dict(
        side=Side.LONG, quantity=0.1, entry_price=100, current_stop=99,
        current_target=102, strategy_id="momentum", strategy_version="v1",
    )
    values.update(overrides)
    return PositionState(**values)


def test_policy_exits_on_stop_target_and_timeout():
    settings = load_settings()
    policy = PolicyFinalizer(settings)
    hyp = hypothesis()
    base = environment()
    stop_env = base.model_copy(update={"price": base.price.model_copy(update={"last": 98.9})})
    assert policy.finalize(stop_env, hyp, None, None, position=open_position()).action == PolicyAction.EXIT
    target_env = base.model_copy(update={"price": base.price.model_copy(update={"last": 102.1})})
    assert policy.finalize(target_env, hyp, None, None, position=open_position()).action == PolicyAction.EXIT
    old = open_position(opened_at=base.decision_timestamp - timedelta(seconds=settings.policy.maximum_holding_seconds + 1))
    timed = policy.finalize(base, hyp, None, None, position=old)
    assert timed.action == PolicyAction.EXIT
    assert "max_holding_timeout" in timed.reasons

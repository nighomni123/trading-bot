import pytest

from pydantic import ValidationError

from jev_trading.contracts import Action
from jev_trading.risk.kernel import MarketCheck, Portfolio, Proposal, RiskConfig, RiskKernel

NOW = 1_700_000_000_000
PRICE = 50_000.0


def market(spread_bps: float = 1.0, last_update_ms: int = NOW) -> MarketCheck:
    return MarketCheck(spread_bps=spread_bps, last_update_ms=last_update_ms, now_ms=NOW)


def portfolio(**over) -> Portfolio:
    base = dict(position_btc=0.0, capital_usd=10_000.0, daily_pnl_usd=0.0, open_orders=0)
    base.update(over)
    return Portfolio(**base)


def entry(confidence: float = 0.5, size: float = 0.01) -> Proposal:
    return Proposal(action=Action.ENTER_LONG, confidence=confidence, suggested_size_btc=size)


def test_config_loads_from_risk_json():
    k = RiskKernel("configs/risk.json")
    assert k.cfg.model_dump() == {
        "max_position_btc": 0.1,
        "max_leverage": 2.0,
        "daily_loss_limit_usd": 50.0,
        "trade_loss_limit_usd": 20.0,
        "max_spread_bps": 5.0,
        "stale_data_ms": 15000,
        "max_orders_per_min": 12,
        "risk_per_trade_pct": 1.0,
        "cooldown_ms": 0,
        "max_drawdown_pct": 0.0,
    }
    assert RiskKernel().cfg.max_leverage == 2.0


def test_confidence_099_rejected_on_daily_loss_breach():
    k = RiskKernel()
    d = k.evaluate(
        {"action": "ENTER_LONG", "confidence": 0.99, "suggested_size_btc": 0.01},
        portfolio(daily_pnl_usd=-50.0),
        market(),
        NOW,
        PRICE,
    )
    assert not d.allowed
    assert "daily_loss_limit_usd" in d.reason
    assert d.halted
    assert d.approved_size_btc == 0.0


def test_halt_latches_until_reset_manual():
    k = RiskKernel()
    k.evaluate(entry(confidence=0.99), portfolio(daily_pnl_usd=-50.0), market(), NOW, PRICE)
    assert k.halted
    d = k.evaluate(entry(confidence=0.99), portfolio(), market(), NOW, PRICE)
    assert not d.allowed
    assert "halted" in d.reason
    k.reset_manual()
    assert not k.halted
    d = k.evaluate(entry(confidence=0.99), portfolio(), market(), NOW, PRICE)
    assert d.allowed


def test_stale_market_rejected_and_latches_halt():
    k = RiskKernel()
    d = k.evaluate(entry(), portfolio(), market(last_update_ms=NOW - 15_001), NOW, PRICE)
    assert not d.allowed
    assert "stale_data_ms" in d.reason
    assert k.halted


def test_spread_too_wide_rejected():
    k = RiskKernel()
    d = k.evaluate(entry(), portfolio(), market(spread_bps=6.0), NOW, PRICE)
    assert not d.allowed
    assert "max_spread_bps" in d.reason
    assert not k.halted


def test_size_clamped_to_max_position_and_risk_cap():
    k = RiskKernel()
    d = k.evaluate(entry(size=1.0), portfolio(capital_usd=10_000_000.0), market(), NOW, PRICE)
    assert d.allowed
    assert d.approved_size_btc == 0.1
    d = k.evaluate(entry(size=0.01), portfolio(capital_usd=10_000.0), market(), NOW, PRICE)
    assert d.allowed
    assert d.approved_size_btc == pytest.approx(10_000.0 * 1.0 / 100.0 / PRICE)


def test_exit_allowed_when_flat():
    k = RiskKernel()
    d = k.evaluate(Proposal(action=Action.EXIT, confidence=0.9), portfolio(), market(), NOW, PRICE)
    assert d.allowed
    assert d.reason == "ok"
    assert d.approved_size_btc == 0.0


def test_rate_limit_rejects_13th_approved_decision_in_60s():
    k = RiskKernel()
    exit_proposal = Proposal(action=Action.EXIT, confidence=0.9)
    for _ in range(12):
        assert k.evaluate(exit_proposal, portfolio(), market(), NOW, PRICE).allowed
    d = k.evaluate(exit_proposal, portfolio(), market(), NOW, PRICE)
    assert not d.allowed
    assert "max_orders_per_min" in d.reason


def test_cooldown_blocks_enter_until_close_plus_duration():
    k = RiskKernel()
    k.cfg.cooldown_ms = 60_000
    exit_proposal = Proposal(action=Action.EXIT, confidence=0.9)
    assert k.evaluate(exit_proposal, portfolio(), market(), NOW, PRICE).allowed
    k.register_fill(-1.0, close_time_ms=NOW)

    blocked_at = MarketCheck(spread_bps=1.0, last_update_ms=NOW + 59_999, now_ms=NOW + 59_999)
    blocked = k.evaluate(entry(), portfolio(), blocked_at, NOW + 59_999, PRICE)
    assert not blocked.allowed
    assert blocked.reason == "cooldown_ms"

    ready_at = MarketCheck(spread_bps=1.0, last_update_ms=NOW + 60_000, now_ms=NOW + 60_000)
    ready = k.evaluate(entry(), portfolio(), ready_at, NOW + 60_000, PRICE)
    assert ready.allowed
    assert ready.reason == "ok"


def test_drawdown_halt_uses_peak_to_current_daily_pnl():
    k = RiskKernel()
    k.cfg.max_drawdown_pct = 10.0
    k.register_fill(100.0)
    d = k.evaluate(entry(), portfolio(capital_usd=1_000.0, daily_pnl_usd=-11.0), market(), NOW, PRICE)
    assert not d.allowed
    assert d.reason == "max_drawdown_pct"
    assert k.halted


def test_invalid_risk_config_is_rejected_fail_closed():
    base = {
        "max_position_btc": 0.1,
        "max_leverage": 2.0,
        "daily_loss_limit_usd": 50.0,
        "trade_loss_limit_usd": 20.0,
        "max_spread_bps": 5.0,
        "stale_data_ms": 15_000,
        "max_orders_per_min": 12,
        "risk_per_trade_pct": 1.0,
    }
    with pytest.raises(ValidationError):
        RiskConfig.model_validate({**base, "cooldown_ms": -1})
    with pytest.raises(ValidationError):
        RiskConfig.model_validate({**base, "max_drawdown_pct": float("nan")})


def test_no_action_always_allowed():
    k = RiskKernel()
    d = k.evaluate(
        Proposal(action=Action.NO_ACTION, confidence=0.0),
        portfolio(),
        market(spread_bps=99.0),
        NOW,
        PRICE,
    )
    assert d.allowed
    assert d.approved_size_btc == 0.0


def test_enter_long_rejected_at_position_limit():
    k = RiskKernel()
    d = k.evaluate(entry(), portfolio(position_btc=0.1), market(), NOW, PRICE)
    assert not d.allowed
    assert "max_position_btc" in d.reason


def test_leverage_breach_rejected():
    k = RiskKernel()
    d = k.evaluate(
        entry(size=0.01), portfolio(position_btc=0.05, capital_usd=100.0), market(), NOW, PRICE
    )
    assert not d.allowed
    assert "max_leverage" in d.reason


def test_register_fill_trips_kill_switch_on_daily_loss():
    k = RiskKernel()
    for _ in range(3):
        k.register_fill(-19.0)
    assert k.halted
    d = k.evaluate(entry(confidence=0.99), portfolio(), market(), NOW, PRICE)
    assert not d.allowed
    assert "halted:daily_loss_limit_usd" in d.reason


def test_trade_loss_halt_survives_reset_daily():
    k = RiskKernel()
    k.register_fill(-25.0)
    assert k.halted
    k.reset_daily()
    assert k.halted
    assert not k.evaluate(entry(), portfolio(), market(), NOW, PRICE).allowed
    k.reset_manual()
    assert not k.halted
    assert k.evaluate(entry(), portfolio(), market(), NOW, PRICE).allowed

import pytest

from jev_trading.contracts import Action
from jev_trading.policy.engine import PolicyProposal, decide, load_config
from jev_trading.risk.kernel import Proposal, RiskKernel

NOW = 1_700_000_000_000
EPS = 1e-9


def quant(**over) -> dict:
    base = {"p_up_15": 0.5, "p_dn_15": 0.5}
    base.update(over)
    return base


def jev(**over) -> dict:
    base = {"trade_ok": 0.5, "failure_regime": 0.5}
    base.update(over)
    return base


def test_policy_config_loads():
    cfg = load_config()
    assert cfg["enter_long"] == {
        "p_up_15": 0.60,
        "jev_trade_ok": 0.75,
        "jev_failure_max": 0.35,
        "min_edge_over_cost": 2.0,
    }
    assert cfg["enter_short"]["p_dn_15"] == 0.60
    assert cfg["exit"] == {"p_up_below": 0.45, "p_dn_below": 0.45}


def test_boundary_exactly_at_thresholds_passes():
    at = jev(trade_ok=0.75, failure_regime=0.35)
    assert decide(quant(p_up_15=0.60), at).action == Action.ENTER_LONG
    assert decide(quant(p_dn_15=0.60), at).action == Action.ENTER_SHORT


def test_boundary_epsilon_below_fails():
    at = jev(trade_ok=0.75, failure_regime=0.35)
    assert decide(quant(p_up_15=0.60 - EPS), at).action == Action.NO_ACTION
    assert decide(quant(p_up_15=0.60), jev(trade_ok=0.75 - EPS, failure_regime=0.35)).action == Action.NO_ACTION
    assert decide(quant(p_up_15=0.60), jev(trade_ok=0.75, failure_regime=0.35 + EPS)).action == Action.NO_ACTION


def test_enter_long_all_pass():
    d = decide(quant(p_up_15=0.8), jev(trade_ok=0.9, failure_regime=0.1))
    assert d.action == Action.ENTER_LONG
    assert d.confidence == pytest.approx(0.8)  # min(p_up_15, trade_ok)
    assert d.suggested_size_btc == 0.0  # sizing is risk's job
    assert d.reasons
    assert d.reasons[-1] == "ENTER_LONG"
    assert any("p_up_15" in r and "pass" in r for r in d.reasons)
    assert not any("edge" in r for r in d.reasons)  # no expected_return_15 -> skipped


def test_edge_gate_activates_on_expected_return():
    q = quant(p_up_15=0.8, expected_return_15=0.001)  # below 2.0 * 0.0014 hurdle
    d = decide(q, jev(trade_ok=0.9, failure_regime=0.1))
    assert d.action == Action.NO_ACTION
    assert any("edge" in r and "fail" in r for r in d.reasons)
    q = quant(p_up_15=0.8, expected_return_15=0.003)  # above hurdle
    d = decide(q, jev(trade_ok=0.9, failure_regime=0.1))
    assert d.action == Action.ENTER_LONG
    assert any("edge" in r and "pass" in r for r in d.reasons)


def test_enter_short_mirror():
    d = decide(quant(p_dn_15=0.8), jev(trade_ok=0.9, failure_regime=0.1))
    assert d.action == Action.ENTER_SHORT
    assert d.confidence == pytest.approx(0.8)
    assert d.suggested_size_btc == 0.0


def test_no_action_when_failure_high_despite_strong_p_up():
    d = decide(quant(p_up_15=0.95, p_dn_15=0.1), jev(trade_ok=0.9, failure_regime=0.6))
    assert d.action == Action.NO_ACTION
    assert d.confidence == 0.0
    assert any("failure_regime" in r and "fail" in r for r in d.reasons)
    assert d.reasons[-1] == "NO_ACTION"


def test_confidence_in_unit_interval():
    cases = [
        (quant(p_up_15=0.99), jev(trade_ok=0.98, failure_regime=0.1)),
        (quant(p_up_15=0.6, p_dn_15=0.6), jev(trade_ok=0.75, failure_regime=0.35)),
        (quant(), jev()),
        (quant(p_up_15=0.6), jev(trade_ok=0.75, failure_regime=0.99)),
    ]
    for q, j in cases:
        assert 0.0 <= decide(q, j).confidence <= 1.0


def test_reasons_non_empty_and_cover_checked_thresholds():
    d = decide(quant(p_up_15=0.9), jev(trade_ok=0.9, failure_regime=0.6))  # fails on failure_regime only
    assert d.reasons
    joined = "\n".join(d.reasons)
    for key in ("p_up_15", "p_dn_15", "trade_ok", "failure_regime"):
        assert key in joined
    assert "pass" in joined and "fail" in joined


def test_custom_cfg_override_respected():
    cfg = {
        "enter_long": {"p_up_15": 0.90, "jev_trade_ok": 0.75, "jev_failure_max": 0.35, "min_edge_over_cost": 2.0},
        "enter_short": {"p_dn_15": 0.60, "jev_trade_ok": 0.75, "jev_failure_max": 0.35, "min_edge_over_cost": 2.0},
        "exit": {"p_up_below": 0.45, "p_dn_below": 0.45},
    }
    q, j = quant(p_up_15=0.8), jev(trade_ok=0.9, failure_regime=0.1)
    assert decide(q, j).action == Action.ENTER_LONG  # default thr 0.60
    assert decide(q, j, cfg).action == Action.NO_ACTION  # override thr 0.90


def test_proposal_feeds_risk_kernel():
    pol = decide(quant(p_up_15=0.8), jev(trade_ok=0.9, failure_regime=0.1))
    assert isinstance(pol, PolicyProposal)
    assert isinstance(pol, Proposal)
    decision = RiskKernel().evaluate(
        pol,
        {"position_btc": 0.0, "capital_usd": 10_000.0, "daily_pnl_usd": 0.0, "open_orders": 0},
        {"spread_bps": 1.0, "last_update_ms": NOW, "now_ms": NOW},
        NOW,
        50_000.0,
    )
    assert decision.allowed
    assert decision.reason == "ok"
    assert decision.approved_size_btc == 0.0  # policy suggests 0; risk owns sizing

"""Cross-arm reporting: after-cost truth, benchmarks, and an honest verdict.

The point of these tests is that the report cannot flatter a result. A profitable
arm that only beats cash fails, an arm that spends more on inference than it made
fails, and an arm whose model cost was never measured says so.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import polars as pl
import pytest

from jev_trading.ledger import DecisionLedger
from jev_trading.live_intelligence.arms import (
    ArmReport,
    buy_and_hold,
    compare_arms,
    render_markdown,
    summarize_arm,
    verdict,
)
from jev_trading.live_intelligence.schemas import (
    AccountState,
    DataQuality,
    DecisionRecord,
    Direction,
    ExecutionState,
    PolicyAction,
    PolicyDecision,
    PositionState,
    QuantEvidence,
    RiskDecision,
    RiskStatus,
    Side,
    StrategyHypothesis,
    TradeProposal,
    Versions,
)
from tests.test_live_intelligence import environment, safe_quality

UTC = timezone.utc


class _Record:
    """A minimal stand-in shaped like the fields the report reads."""

    def __init__(self, **kw):
        self.timestamp = kw.get("timestamp") or datetime(2024, 1, 1, tzinfo=UTC)
        self.policy_decision = kw.get("policy_decision")
        self.risk_decision = kw.get("risk_decision")
        self.market_environment = kw.get("market_environment")
        self.frontier_hypothesis = kw.get("frontier_hypothesis")
        self.jev_evaluation = kw.get("jev_evaluation")
        self.quant_analyses = kw.get("quant_analyses", ())
        self.economic_value = kw.get("economic_value")
        self.execution_intent = kw.get("execution_intent")
        self.pending_cancellation = kw.get("pending_cancellation")
        self.provider_failures = kw.get("provider_failures", ())
        self.runtime_state = kw.get("runtime_state")
        self.trade_proposal = kw.get("trade_proposal")


def _policy(action=PolicyAction.NO_TRADE, reasons=("r",)):
    return PolicyDecision(
        decision_id="d", timestamp=datetime(2024, 1, 1, tzinfo=UTC), action=action,
        reasons=reasons, policy_version="v1",
    )


def _risk(status=RiskStatus.REJECTED):
    approved = status == RiskStatus.APPROVED
    return RiskDecision(
        decision_id="d", timestamp=datetime(2024, 1, 1, tzinfo=UTC), status=status,
        reasons=("x",), risk_config_version="v1",
        approved_quantity=1.0 if approved else 0.0,
        approved_notional=100.0 if approved else 0.0,
    )


def _hypothesis(input_tokens=0, output_tokens=0, reported=False, provider="openai_compatible"):
    return StrategyHypothesis(
        hypothesis_id="h", timestamp=datetime(2024, 1, 1, tzinfo=UTC), regime="BULLISH",
        regime_confidence=0.5, primary_strategy="momentum", direction=Direction.LONG,
        thesis="t", abstain=False, reason="r", model_version="m", prompt_version="p",
        provider=provider, input_tokens=input_tokens, output_tokens=output_tokens,
        usage_reported=reported,
    )


def _record(**kw):
    base = dict(
        policy_decision=_policy(), risk_decision=_risk(),
        market_environment=environment(quality=safe_quality()),
        runtime_state={"account": {"capital_usd": 10_000.0}, "position": {"side": "FLAT", "quantity": 0.0}},
    )
    base.update(kw)
    return _Record(**base)


class _Trade:
    def __init__(self, gross=0.0, fees=1.0, slippage=0.5, funding=0.1, qty=1.0, entry=100.0, exit=101.0):
        self.gross_pnl_usd = gross
        self.fees_usd = fees
        self.slippage_usd = slippage
        self.funding_usd = funding
        self.net_pnl_usd = gross - fees - funding
        self.quantity = qty
        self.entry_price = entry
        self.exit_price = exit


# ------------------------------------------------------------------ totals


def test_costs_are_subtracted_and_spend_is_reported_separately():
    report = summarize_arm(
        "B", [_record()], [_Trade(gross=10.0, fees=2.0, funding=0.5)],
        ledger_path="x", capital_usd=10_000.0,
    )
    assert report.gross_pnl_usd == 10.0
    assert report.net_pnl_usd == pytest.approx(7.5)
    assert report.net_after_model_usd == pytest.approx(7.5)  # unpriced model


def test_model_spend_is_subtracted_from_the_result():
    records = [_record(frontier_hypothesis=_hypothesis(1_000_000, 0, reported=True))]
    free = summarize_arm("B", records, [], ledger_path="x", capital_usd=10_000.0)
    priced = summarize_arm(
        "B", records, [], ledger_path="x", capital_usd=10_000.0,
        model_cost_per_mtok_usd=5.0,
    )
    # One million input tokens at $5/Mtok is $5, and it must come off the top.
    assert priced.model_cost_usd == pytest.approx(5.0)
    assert priced.net_after_model_usd == pytest.approx(-5.0)
    assert free.model_cost_usd == 0.0


def test_unreported_usage_is_flagged_not_assumed_free():
    report = summarize_arm(
        "C", [_record(frontier_hypothesis=_hypothesis(500, 500, reported=False))],
        [], ledger_path="x", capital_usd=10_000.0,
    )
    assert report.input_tokens == 500
    # Tokens arrived without a provider-reported flag, so cost is unmeasured --
    # which the verdict must surface rather than treat as free.
    assert report.usage_reported is False
    assert "model_cost_unmeasured" in verdict({"C": report}, {}, "A")["arms"]["C"]["reasons"]


def test_abstentions_are_counted_and_not_failures():
    records = [_record() for _ in range(3)] + [
        _record(policy_decision=_policy(PolicyAction.HOLD), risk_decision=_risk(RiskStatus.APPROVED))
    ]
    report = summarize_arm("A", records, [], ledger_path="x", capital_usd=10_000.0)
    assert report.decisions == 4
    assert report.abstentions == 3
    assert report.policy_actions["NO_TRADE"] == 3
    # Staying flat three times out of four is a result, not a fault.


# --------------------------------------------------------------- drawdown


def test_drawdown_uses_the_runs_own_equity_series():
    def with_capital(value):
        return _record(runtime_state={"account": {"capital_usd": value},
                                      "position": {"side": "FLAT", "quantity": 0.0}})
    report = summarize_arm(
        "A", [with_capital(10_000.0), with_capital(9_000.0), with_capital(9_500.0)],
        [], ledger_path="x", capital_usd=10_000.0,
    )
    # Peak 10 000 -> trough 9 000 is a 1 000 drawdown, not a trade-sequence one.
    assert report.max_drawdown_usd == pytest.approx(1_000.0)
    assert report.peak_equity_usd == pytest.approx(10_000.0)


def test_turnover_is_summed_both_ways():
    report = summarize_arm(
        "A", [], [_Trade(qty=2.0, entry=100.0, exit=110.0), _Trade(qty=1.0, entry=50.0, exit=40.0)],
        ledger_path="x", capital_usd=10_000.0,
    )
    assert report.turnover_usd == pytest.approx(2.0 * 210.0 + 1.0 * 90.0)


# ------------------------------------------------------------- benchmarks


def test_buy_and_hold_is_charged_the_same_round_trip():
    frame = pl.DataFrame({
        "timestamp": [1_000_000, 2_000_000, 3_000_000],
        "close": [100.0, 110.0, 120.0],
    })
    result = buy_and_hold(frame, capital_usd=10_000.0, fee_bps_per_side=5.0, slippage_bps_per_side=2.0)
    # 120/100 - 1 = +20%, minus 14 bps of round-trip cost.
    assert result["return_fraction"] == pytest.approx(0.20 - 0.0014, abs=1e-6)
    assert result["net_pnl_usd"] == pytest.approx(10_000.0 * (0.20 - 0.0014), abs=0.01)
    assert result["round_trip_cost_fraction"] == pytest.approx(0.0014)


def test_buy_and_hold_respects_the_window():
    frame = pl.DataFrame({
        "timestamp": [1_000, 2_000, 3_000, 4_000],
        "close": [100.0, 200.0, 400.0, 800.0],
    })
    inside = buy_and_hold(frame, capital_usd=1.0, start_ms=2_000, end_ms=3_000,
                          fee_bps_per_side=0.0, slippage_bps_per_side=0.0)
    assert inside["entry"] == 200.0
    assert inside["exit"] == 400.0


def test_buy_and_hold_degrades_safely_on_short_input():
    empty = pl.DataFrame({"timestamp": [], "close": []})
    assert buy_and_hold(empty, capital_usd=1.0)["net_pnl_usd"] == 0.0
    single = pl.DataFrame({"timestamp": [1], "close": [100.0]})
    assert buy_and_hold(single, capital_usd=1.0)["net_pnl_usd"] == 0.0


# ---------------------------------------------------------------- verdict


def _report(**kw) -> ArmReport:
    base = dict(arm="B", ledger="x", trades=10, net_pnl_usd=1_000.0, model_cost_usd=0.0)
    base.update(kw)
    return ArmReport(**base)


def test_beating_cash_but_not_passive_exposure_is_not_supported():
    outcome = verdict({"B": _report(net_pnl_usd=500.0, usage_reported=True)},
                      {"buy_and_hold": {"net_pnl_usd": 2_000.0}}, "A")
    reasons = outcome["arms"]["B"]["reasons"]
    assert "does_not_beat_passive_exposure" in reasons
    assert outcome["arms"]["B"]["supported"] is False


def test_negative_after_model_cost_is_not_supported():
    # Profitable gross, but inference cost exceeded the result.
    outcome = verdict({"C": _report(net_pnl_usd=10.0, model_cost_usd=11.0, usage_reported=True)},
                      {"buy_and_hold": {"net_pnl_usd": 0.0}}, "A")
    assert "negative_after_model_cost" in outcome["arms"]["C"]["reasons"]


def test_few_trades_cannot_infer_anything():
    outcome = verdict({"A": _report(trades=1)}, {}, "A")
    assert "too_few_trades_to_infer_anything" in outcome["arms"]["A"]["reasons"]


def test_a_genuinely_better_arm_is_supported():
    outcome = verdict(
        {"B": _report(net_pnl_usd=5_000.0, model_cost_usd=10.0, usage_reported=True)},
        {"buy_and_hold": {"net_pnl_usd": 1_000.0}}, "A",
    )
    assert outcome["arms"]["B"]["supported"] is True
    assert outcome["arms"]["B"]["reasons"] == []


def test_incremental_uses_after_model_not_gross():
    reports = {
        "A": _report(arm="A", net_pnl_usd=1_000.0, trades=10),
        "C": _report(arm="C", net_pnl_usd=1_400.0, trades=12, model_cost_usd=1_200.0),
    }
    from jev_trading.live_intelligence import arms as arms_module

    original = arms_module.load_arm
    arms_module.load_arm = lambda arm, path, **kw: reports[arm]
    try:
        result = compare_arms({"A": "a", "C": "c"}, capital_usd=10_000.0)
    finally:
        arms_module.load_arm = original
    # C looks 400 better gross but is 800 worse after paying for itself.
    assert result["incremental"]["C_minus_A"]["net_after_model_usd"] == pytest.approx(-800.0)
    assert result["incremental"]["C_minus_A"]["net_pnl_usd"] == pytest.approx(400.0)
    assert reports["C"].model_cost_usd == 1_200.0


def test_render_includes_benchmarks_and_verdict():
    from jev_trading.live_intelligence import arms as arms_module

    empty = ArmReport(arm="A", ledger="a")
    original = arms_module.load_arm
    arms_module.load_arm = lambda arm, path, **kw: empty
    try:
        result = compare_arms({"A": "a", "B": "b"}, capital_usd=10_000.0)
    finally:
        arms_module.load_arm = original
    markdown = render_markdown(result)
    assert "Benchmarks" in markdown
    assert "cash" in markdown
    assert "Verdict" in markdown
    assert "not evidence of an edge" in markdown


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

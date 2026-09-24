"""Deterministic economic-cost checks; no model fitting or market data."""
from __future__ import annotations

from jev_trading.quant.economic import EconomicQuantOutput, evaluate_economic_opportunity


def quant(expected_return: float, uncertainty: float = 0.1) -> EconomicQuantOutput:
    return EconomicQuantOutput(
        p_up_15=0.9,
        p_dn_15=0.1,
        expected_return_15=expected_return,
        expected_downside_15=-0.001,
        expected_favorable_excursion_15=0.002,
        expected_adverse_excursion_15=-0.001,
        uncertainty=uncertainty,
        holding_time_minutes=15.0,
    )


def test_round_trip_costs_are_not_double_counted():
    result = evaluate_economic_opportunity(quant(0.003))
    assert result.cost_fees == 0.001
    assert result.cost_slippage == 0.0004
    assert result.expected_net_edge == 0.0016
    assert result.cost_fees + result.cost_slippage == 0.0014


def test_gross_edge_must_clear_costs():
    result = evaluate_economic_opportunity(quant(0.0005))
    assert result.expected_net_edge < 0
    assert result.trade_decision == "NO_TRADE"


def test_missing_excursion_or_holding_prediction_fails_closed():
    incomplete = quant(0.003).model_copy(update={"expected_adverse_excursion_15": None})
    result = evaluate_economic_opportunity(incomplete)
    assert result.trade_decision == "NO_TRADE"
    assert any("missing real prediction" in reason for reason in result.decision_reasons)


def test_funding_is_scaled_from_eight_hour_rate():
    result = evaluate_economic_opportunity(quant(0.003), funding_rate=0.00048, hold_bars_estimate=15)
    assert result.cost_funding == 0.000015

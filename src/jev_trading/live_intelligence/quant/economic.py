"""Deterministic economic accounting for candidate paths."""
from __future__ import annotations

from datetime import timedelta

from jev_trading.live_intelligence.schemas import (
    CandidateTrade,
    CostAssumptions,
    EconomicValue,
    Side,
)


def calculate_economic_value(
    candidate: CandidateTrade,
    probabilities: dict[str, float],
    costs: CostAssumptions,
    *,
    timeout_return_fraction: float = 0.0,
    sample_size: int = 0,
) -> EconomicValue:
    """Calculate cost-adjusted value without inventing a probability.

    ``probabilities`` must contain target/stop/timeout and represent a
    caller-supplied historical/conditional estimate. Missing or insufficient
    evidence must be handled by the caller as abstention.
    """
    if set(probabilities) != {"target", "stop", "timeout"}:
        raise ValueError("probabilities must contain target, stop, timeout")
    p_target, p_stop, p_timeout = (probabilities[key] for key in ("target", "stop", "timeout"))
    if any(value < 0 or value > 1 for value in (p_target, p_stop, p_timeout)):
        raise ValueError("probabilities must be in [0, 1]")
    if abs(p_target + p_stop + p_timeout - 1.0) > 1e-8:
        raise ValueError("probabilities must sum to one")
    sign = 1.0 if candidate.side == Side.LONG else -1.0
    entry = candidate.entry_reference
    target_return = sign * (candidate.target / entry - 1.0)
    stop_return = sign * (candidate.stop / entry - 1.0)
    timeout_return = sign * timeout_return_fraction
    gross = p_target * target_return + p_stop * stop_return + p_timeout * timeout_return
    holding_fraction = costs.holding_seconds / (8 * 60 * 60)
    fees = 2 * costs.fee_bps_per_side / 10_000
    slippage = 2 * costs.slippage_bps_per_side / 10_000
    latency = costs.latency_bps / 10_000
    funding = sign * costs.funding_rate_per_8h * holding_fraction
    net = gross - fees - slippage - latency - funding
    downside = p_stop * abs(stop_return)
    reward = abs(target_return)
    return EconomicValue(
        side=candidate.side, sample_size=sample_size, probabilities=probabilities,
        gross_expected_payoff=gross, fees=fees, slippage=slippage, funding=funding,
        latency=latency, net_expected_value=net, expected_downside=downside,
        risk_reward=reward / abs(stop_return) if stop_return else None,
        expected_duration_seconds=float(costs.holding_seconds),
        cost_assumptions=costs,
    )

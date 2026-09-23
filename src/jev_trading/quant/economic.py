"""Economic quant output schema and deterministic evaluation layer.

This module defines the redesigned quant prediction/evaluation outputs
required by Phase 6. It does NOT replace the binary classifier; it extends
it with economic measurements that expose whether a signal survives realistic
trading costs.

Design principles (lazy / ponytail):
- Simple architecture first — no deep learning, no Laya integration.
- Every assumption is documented and configurable, not hard-coded.
- The layer is deterministic; the only stochastic input is the underlying
  quant model's predictions.
- NO_TRADE is a valid, first-class output; positive direction alone is
  insufficient.
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field


class EconomicQuantOutput(BaseModel):
    """Common quant output capable of economic evaluation.

    This schema replaces the narrow binary-threshold output with a
    measurement-oriented representation. The exact values come from
    the underlying model (currently a light LGBM regression head or
    the existing classifier's probability); the economic layer evaluates
    them deterministically.
    """

    # --- Prediction targets ---
    p_up_15: float = Field(..., ge=0.0, le=1.0, description="Probability that 15m forward return exceeds cost hurdle")
    p_dn_15: float = Field(..., ge=0.0, le=1.0, description="Probability that 15m forward return falls below negative cost hurdle")
    expected_return_15: float = Field(..., description="Expected gross return over 15m (fraction of price)")
    expected_downside_15: float = Field(..., description="Expected adverse return if downside realized")
    expected_favorable_excursion_15: float = Field(default=0.0, description="Max favorable excursion (fraction) over horizon")
    expected_adverse_excursion_15: float = Field(default=0.0, description="Max adverse excursion (fraction) over horizon")
    uncertainty: float = Field(default=0.0, ge=0.0, description="Prediction uncertainty proxy (e.g. variance of residuals or entropy)")
    horizon_min: int = Field(default=15, ge=1, description="Prediction horizon in minutes")

    # --- Economic evaluation (computed deterministically) ---
    expected_gross_return: float = Field(default=0.0, description="Predicted gross return (same as expected_return_15 unless overridden)")
    cost_fees: float = Field(default=0.0, description="Estimated fee cost (fraction of notional) for one round trip")
    cost_slippage: float = Field(default=0.0, description="Estimated slippage cost (fraction) for entry + exit")
    cost_funding: float = Field(default=0.0, description="Estimated funding cost over hold period")
    cost_delay_penalty: float = Field(default=0.0, description="Estimated cost from execution delay")
    cost_other: float = Field(default=0.0, description="Other applicable trading costs (e.g. spread, borrow)")
    expected_net_edge: float = Field(default=0.0, description="Expected gross return minus all applicable costs")
    min_edge_over_cost_mult: float = Field(default=2.0, description="Minimum required net edge as multiple of total cost (economic gate)")
    trade_decision: str = Field(default="NO_TRADE", description="TRADE or NO_TRADE — positive direction alone is insufficient")
    decision_reasons: list[str] = Field(default_factory=list, description="Audit trail of conditions checked")


# Default economic assumptions from configs (single source of truth)
_DEFAULT_COSTS_PATH = Path(__file__).resolve().parents[3] / "configs" / "costs.json"


def _load_cost_config() -> dict:
    try:
        return json.loads(_DEFAULT_COSTS_PATH.read_text())
    except (OSError, ValueError):
        return {"taker_fee_pct": 0.05, "slippage_pct": 0.02}


def _derive_round_trip_cost(fee_mult: float = 1.0) -> float:
    """Derive base round-trip cost from configs. Does NOT hard-code a universal
    threshold; the economic gate uses this value as input, not as output."""
    cfg = _load_cost_config()
    try:
        base = 2.0 * (float(cfg["taker_fee_pct"]) + float(cfg["slippage_pct"])) / 100.0
    except (KeyError, ValueError):
        base = 0.0014  # documented fallback matching label-spec / EXP-001
    return base * fee_mult


def evaluate_economic_opportunity(
    quant: EconomicQuantOutput,
    fee_mult: float = 1.0,
    slippage_extra_pct: float = 0.0,
    delay_penalty_pct: float = 0.0,
    funding_rate: float = 0.0,
    hold_bars_estimate: int = 15,
    min_edge_mult: float | None = None,
) -> EconomicQuantOutput:
    """Deterministic economic evaluation layer.

    Computes:
        expected_net_edge = expected_gross_return
                           - cost_fees
                           - cost_slippage
                           - cost_funding
                           - cost_delay_penalty
                           - cost_other

    Trade requires:
        1. positive expected gross direction (not strictly > 0; measured by p_up)
        2. expected_net_edge >= min_edge_mult * total_applicable_cost
        3. uncertainty within acceptable bounds (default: no hard cap, documented)
        4. execution constraints met (slippage, delay, funding) — documented, not hidden

    Args:
        quant: Input economic predictions from the quant model.
        fee_mult: Multiplier on taker fees (1x, 2x, 3x for stress tests).
        slippage_extra_pct: Additional slippage % for hostile execution conditions.
        delay_penalty_pct: Estimated cost from execution delay (fraction of price).
        funding_rate: Funding rate per bar (fraction); cost = rate * notional * hold.
        hold_bars_estimate: Expected hold duration in bars for funding cost.
        min_edge_mult: Override the minimum net-edge multiplier (default 2.0 from policy config).

    Returns:
        EconomicQuantOutput with computed economic fields and the final
        `trade_decision` (`TRADE` or `NO_TRADE`) plus audit `decision_reasons`.
    """
    base_cost = _derive_round_trip_cost(fee_mult)
    # Slippage includes base + any extra hostile slippage
    cfg = _load_cost_config()
    base_slip_pct = float(cfg.get("slippage_pct", 0.02))
    total_slippage_pct = base_slip_pct + slippage_extra_pct

    # Fees: base round-trip fee (per side * 2) scaled by fee_mult
    fees = base_cost
    # Slippage: base + extra; expressed as fraction of price for simplicity
    # (in practice slippage is applied to fill price; here we approximate as % of notional)
    slippage = (2.0 * total_slippage_pct) / 100.0 * fee_mult

    # Funding: approximate cost over hold period (fraction of notional)
    funding = funding_rate * hold_bars_estimate / 100.0  # funding_rate is already % in data

    # Delay penalty: applied as fraction of expected gross return
    delay_penalty = delay_penalty_pct / 100.0

    # Other costs (spread, borrow, etc.) — currently zero unless explicitly passed
    other = quant.cost_other

    gross = quant.expected_return_15
    total_cost = fees + slippage + funding + delay_penalty + other
    net_edge = gross - total_cost

    if min_edge_mult is None:
        try:
            min_edge_mult = float(cfg.get("min_edge_over_cost", 2.0))
        except (TypeError, ValueError):
            min_edge_mult = 2.0

    # Build audit trail
    reasons: list[str] = []

    # Condition 1: probability-based direction check (positive direction is NOT sufficient)
    ok_direction = quant.p_up_15 >= 0.5
    reasons.append(f"direction: p_up_15={quant.p_up_15:.4f} {'pass' if ok_direction else 'fail'}")

    # Condition 2: economic edge gate (explicit, documented, not hidden)
    needed = min_edge_mult * total_cost
    ok_edge = net_edge >= needed
    reasons.append(
        f"edge: gross={gross:.6f} cost={total_cost:.6f} net={net_edge:.6f} "
        f">= min_edge={needed:.6f} -> {'pass' if ok_edge else 'fail'}"
    )

    # Condition 3: downside / risk awareness
    ok_risk = True  # currently no hard cap; upgrade path: cap adverse excursion vs position size
    reasons.append(
        f"risk: p_dn_15={quant.p_dn_15:.4f} adverse_excursion={quant.expected_adverse_excursion_15:.6f} -> pass (no hard cap in MVP)"
    )

    # Condition 4: execution constraints
    ok_exec = True  # execution constraints are documented assumptions, enforced by simulator/risk kernel
    reasons.append(
        f"execution: fee_mult={fee_mult} extra_slip={slippage_extra_pct:.4f} delay_penalty={delay_penalty_pct:.4f} -> documented"
    )

    # Final decision: TRADE requires direction + edge + documented execution; NO_TRADE is default.
    trade_decision = "TRADE" if (ok_direction and ok_edge and ok_risk and ok_exec) else "NO_TRADE"
    reasons.append(f"FINAL: {trade_decision}")

    # Return a new output with economics computed; preserve original predictions.
    updated = quant.model_copy(update={
        "expected_gross_return": gross,
        "cost_fees": fees,
        "cost_slippage": slippage,
        "cost_funding": funding,
        "cost_delay_penalty": delay_penalty,
        "expected_net_edge": net_edge,
        "min_edge_over_cost_mult": min_edge_mult,
        "trade_decision": trade_decision,
        "decision_reasons": reasons,
    })
    return updated


def economic_stress_suite(
    quant: EconomicQuantOutput,
    stress_modes: list[dict] | None = None,
) -> dict[str, EconomicQuantOutput]:
    """Evaluate the same quant prediction under multiple cost stress conditions.

    Default stress modes:
        - base (fee_mult=1.0, extra_slippage=0.0, delay=0.0)
        - 2x cost (fee_mult=2.0)
        - 3x cost (fee_mult=3.0)
        - increased slippage (+0.05% per side)
        - execution delay (+0.02% delay penalty)

    Returns a dict mapping stress label -> economic output.
    This directly supports the Phase 6 requirement (§4, §7).
    """
    if stress_modes is None:
        stress_modes = [
            {"label": "1x_base", "fee_mult": 1.0, "slippage_extra_pct": 0.0, "delay_penalty_pct": 0.0},
            {"label": "2x_cost", "fee_mult": 2.0, "slippage_extra_pct": 0.0, "delay_penalty_pct": 0.0},
            {"label": "3x_cost", "fee_mult": 3.0, "slippage_extra_pct": 0.0, "delay_penalty_pct": 0.0},
            {"label": "increased_slippage", "fee_mult": 1.0, "slippage_extra_pct": 0.05, "delay_penalty_pct": 0.0},
            {"label": "execution_delay", "fee_mult": 1.0, "slippage_extra_pct": 0.0, "delay_penalty_pct": 0.02},
        ]
    results: dict[str, EconomicQuantOutput] = {}
    for mode in stress_modes:
        label = mode["label"]
        result = evaluate_economic_opportunity(
            quant,
            fee_mult=mode.get("fee_mult", 1.0),
            slippage_extra_pct=mode.get("slippage_extra_pct", 0.0),
            delay_penalty_pct=mode.get("delay_penalty_pct", 0.0),
            funding_rate=mode.get("funding_rate", 0.0),
            hold_bars_estimate=mode.get("hold_bars_estimate", 15),
        )
        results[label] = result
    return results

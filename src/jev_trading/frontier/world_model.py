"""World model: compresses live bars + Jev answers + PnL feedback into a WorldDigest."""

from __future__ import annotations

import statistics
from collections import defaultdict
from typing import NamedTuple

import polars as pl
from pydantic import BaseModel, Field

from jev_trading.state.features import STATE_FIELDS


class Regime(NamedTuple):
    """Classified market regime from latest state."""
    volatility: str  # low / normal / high
    trend: str       # flat / trending
    funding: str     # normal / extreme_long / extreme_short
    duration_bars: int  # bars since regime change (approx)


class PNLStats(NamedTuple):
    """Per-trade PnL statistics over the window."""
    gross_per_trade: float
    net_per_trade: float
    cost_per_trade: float
    cost_ratio: float      # costs / |gross| when gross != 0, else 0
    n_trades: int
    win_rate: float


class WorldDigest(BaseModel):
    """Compressed adaptive market intelligence — the frontier model's memory."""
    ts: int = Field(..., description="Epoch ms for this digest")
    regime: Regime
    calibration_drift: float = Field(..., ge=0.0, description="|predicted - observed| over window")
    jev_distributions: dict[str, list[float]] = Field(
        default_factory=dict, description="Histogram (10 bins) of answers per question"
    )
    pnl: PNLStats
    feature_summary: dict[str, float] = Field(
        default_factory=dict, description="Key feature stats for the window"
    )


def classify_regime(state: dict) -> Regime:
    """Classify market regime from a single state dict.

    Uses trend_score, vol_regime, funding_z, realized_vol_30m from STATE_FIELDS.
    """
    # Default to normal regime if features missing (warmup)
    trend_score = state.get("trend_score", 0.0)
    vol_regime = state.get("vol_regime", 0.0)
    funding_z = state.get("funding_z", 0.0)
    rv_30m = state.get("rv_30m", 0.0)

    # Volatility: low/normal/high based on vol_regime (ratio to 1d mean)
    if vol_regime < -0.3:
        volatility = "low"
    elif vol_regime > 0.5:
        volatility = "high"
    else:
        volatility = "normal"

    # Trend: flat/trending based on |trend_score|
    if abs(trend_score) < 0.15:
        trend = "flat"
    else:
        trend = "trending"

    # Funding: normal/extreme_long/extreme_short based on funding_z
    if funding_z > 2.0:
        funding = "extreme_long"
    elif funding_z < -2.0:
        funding = "extreme_short"
    else:
        funding = "normal"

    # Duration: approximate — we don't track history here, default to 0
    # The caller (build_digest) can update this if tracking consecutive windows
    duration_bars = 0

    return Regime(
        volatility=volatility,
        trend=trend,
        funding=funding,
        duration_bars=duration_bars,
    )


def _compute_histogram(values: list[float], bins: int = 10) -> list[float]:
    """Compute normalized histogram (10 bins) for values in [0, 1]."""
    if not values:
        return [0.0] * bins
    hist = [0] * bins
    for v in values:
        # Clamp to [0, 1] and find bin
        v = max(0.0, min(1.0, v))
        idx = min(int(v * bins), bins - 1)
        hist[idx] += 1
    total = sum(hist)
    return [c / total for c in hist]


def _compute_calibration_drift(p_up_values: list[float], outcomes: list[int]) -> float:
    """Expected calibration error: mean |predicted_prob - observed_rate| over decile bins."""
    if not p_up_values or not outcomes or len(p_up_values) != len(outcomes):
        return 0.0
    bins = 10
    total_err = 0.0
    total_weight = 0
    for b in range(bins):
        lo = b / bins
        hi = (b + 1) / bins
        mask = [i for i, p in enumerate(p_up_values) if lo < p <= hi]
        if not mask:
            continue
        predicted = statistics.mean(p_up_values[i] for i in mask)
        observed = statistics.mean(outcomes[i] for i in mask)
        weight = len(mask)
        total_err += weight * abs(predicted - observed)
        total_weight += weight
    return total_err / total_weight if total_weight > 0 else 0.0


def _compute_pnl_stats(
    p_up_values: list[float],
    outcomes: list[int],
    fees: list[float],
) -> PNLStats:
    """Compute PnL stats from trades that occurred."""
    n_trades = len(fees)
    if n_trades == 0:
        return PNLStats(0.0, 0.0, 0.0, 0.0, 0, 0.0)

    # Simplified: assume each trade was entered when p_up >= threshold
    # Gross = outcome * reward - (1-outcome) * loss, but we only have binary outcome
    # Approximation: gross per trade = (1 if win else -1) * threshold - fee
    # Since threshold is embedded in outcomes, we use outcome as proxy for gross direction
    # Real implementation would track actual entry/exit prices.
    # ponytail: simplified gross estimate — upgrade with real trade PnL from simulator
    gross_per_trade = statistics.mean(
        (1.0 if o == 1 else -1.0) * 0.0014 for o in outcomes
    ) if outcomes else 0.0
    cost_per_trade = statistics.mean(fees) if fees else 0.0
    net_per_trade = gross_per_trade - cost_per_trade
    cost_ratio = abs(cost_per_trade / gross_per_trade) if gross_per_trade != 0 else 0.0
    win_rate = statistics.mean(outcomes) if outcomes else 0.0

    return PNLStats(
        gross_per_trade=gross_per_trade,
        net_per_trade=net_per_trade,
        cost_per_trade=cost_per_trade,
        cost_ratio=cost_ratio,
        n_trades=n_trades,
        win_rate=win_rate,
    )


def _compute_feature_summary(state_dicts: list[dict]) -> dict[str, float]:
    """Compute key feature statistics from state dicts."""
    if not state_dicts:
        return {}

    keys = ["ret_15m", "rv_30m", "trend_score", "funding_z", "volume_z"]
    summary = {}
    for k in keys:
        vals = [s.get(k, 0.0) for s in state_dicts if k in s and s[k] is not None]
        if vals:
            summary[f"mean_{k}"] = statistics.mean(vals)
            summary[f"std_{k}"] = statistics.stdev(vals) if len(vals) > 1 else 0.0
    return summary


def build_digest(
    bars: pl.DataFrame,
    state_dicts: list[dict],
    jev_answers: list[dict[str, float]],
    p_up_values: list[float],
    outcomes: list[int],
    fees: list[float],
    ts: int,
) -> WorldDigest:
    """Build a WorldDigest from a window of live data.

    Args:
        bars: DataFrame with BAR_COLUMNS (for regime from latest bar)
        state_dicts: List of state dicts (one per bar in window)
        jev_answers: List of dicts {question_name: answer} per bar where Jev was asked
        p_up_values: List of quant p_up_15 values (one per bar)
        outcomes: List of 0/1 (did 15m return exceed threshold? one per bar considered)
        fees: List of fee costs per trade that occurred
        ts: Epoch ms timestamp for this digest

    Returns:
        WorldDigest with compressed market intelligence
    """
    # Regime from latest state
    latest_state = state_dicts[-1] if state_dicts else {}
    regime = classify_regime(latest_state)

    # Calibration drift
    calibration_drift = _compute_calibration_drift(p_up_values, outcomes)

    # Jev distributions (histograms per question)
    jev_by_question: dict[str, list[float]] = defaultdict(list)
    for ans_dict in jev_answers:
        for q_name, val in ans_dict.items():
            jev_by_question[q_name].append(val)
    jev_distributions = {
        q_name: _compute_histogram(vals) for q_name, vals in jev_by_question.items()
    }

    # PnL stats
    pnl = _compute_pnl_stats(p_up_values, outcomes, fees)

    # Feature summary
    feature_summary = _compute_feature_summary(state_dicts)

    return WorldDigest(
        ts=ts,
        regime=regime,
        calibration_drift=calibration_drift,
        jev_distributions=jev_distributions,
        pnl=pnl,
        feature_summary=feature_summary,
    )

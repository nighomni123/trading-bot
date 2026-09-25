"""Phase 3 cost-adjusted payoff, probability, and trade metrics.

This module is isolated from the frozen Phase 2 evaluator.  It treats a trade as
entry-notional return, applies side-adjusted entry/exit slippage exactly once,
charges fees on both actual fill notionals, and distinguishes predicted funding
from realized discrete funding prints.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    log_loss,
    precision_recall_fscore_support,
    roc_auc_score,
)

from jev_trading.labels.barriers import BarrierConfig, BarrierLabels, SL_FIRST, TIMEOUT, TP_FIRST

FUNDING_INTERVAL_MS = 8 * 60 * 60_000


@dataclass(frozen=True)
class CostModel:
    taker_fee_bps_per_side: float = 5.0
    slippage_bps_per_side: float = 2.0
    fee_multiplier: float = 1.0
    extra_slippage_bps_per_side: float = 0.0

    @property
    def fee_rate(self) -> float:
        return self.taker_fee_bps_per_side * self.fee_multiplier / 10_000.0

    @property
    def slippage_rate(self) -> float:
        return (self.slippage_bps_per_side + self.extra_slippage_bps_per_side) / 10_000.0

    @property
    def reference_round_trip_cost(self) -> float:
        return 2.0 * (self.fee_rate + self.slippage_rate)

    def as_dict(self) -> dict[str, float]:
        return {
            "taker_fee_bps_per_side": self.taker_fee_bps_per_side,
            "slippage_bps_per_side": self.slippage_bps_per_side,
            "fee_multiplier": self.fee_multiplier,
            "extra_slippage_bps_per_side": self.extra_slippage_bps_per_side,
            "reference_round_trip_cost": self.reference_round_trip_cost,
        }


class FundingIndex:
    """Discrete funding-event accounting aligned to visible bar timestamps."""

    def __init__(self, timestamps: np.ndarray, rates: np.ndarray, closes: np.ndarray):
        self.timestamps = np.asarray(timestamps, dtype=np.int64)
        self.rates = np.asarray(rates, dtype=float)
        self.closes = np.asarray(closes, dtype=float)
        if not (len(self.timestamps) == len(self.rates) == len(self.closes)):
            raise ValueError("funding arrays must align")
        changes = np.r_[False, self.rates[1:] != self.rates[:-1]]
        self.changes = changes
        event_cash = np.where(changes, self.rates * self.closes, 0.0)
        self.prefix = np.r_[0.0, np.cumsum(event_cash)]

    def expected_event_count(self, decision_indices: np.ndarray, exit_indices: np.ndarray) -> np.ndarray:
        entry_ts = self.timestamps[np.asarray(decision_indices, dtype=int) + 1]
        exit_ts = self.timestamps[np.asarray(exit_indices, dtype=int)]
        return np.floor(exit_ts / FUNDING_INTERVAL_MS).astype(np.int64) - np.floor(
            entry_ts / FUNDING_INTERVAL_MS
        ).astype(np.int64)

    def expected_cost_fraction(
        self,
        side: np.ndarray,
        decision_indices: np.ndarray,
        exit_indices: np.ndarray,
        entry_fill: np.ndarray,
        visible_rate: np.ndarray,
    ) -> np.ndarray:
        side = np.asarray(side, dtype=float)
        decision_indices = np.asarray(decision_indices, dtype=int)
        exit_indices = np.asarray(exit_indices, dtype=int)
        count = self.expected_event_count(decision_indices, exit_indices)
        return -side * np.asarray(visible_rate, dtype=float) * count / np.asarray(entry_fill, dtype=float)

    def actual_cost_fraction(
        self,
        side: np.ndarray,
        decision_indices: np.ndarray,
        exit_indices: np.ndarray,
        entry_fill: np.ndarray,
    ) -> np.ndarray:
        side = np.asarray(side, dtype=float)
        decision_indices = np.asarray(decision_indices, dtype=int)
        exit_indices = np.asarray(exit_indices, dtype=int)
        event_cash = self.prefix[exit_indices + 1] - self.prefix[decision_indices + 2]
        return -side * event_cash / np.asarray(entry_fill, dtype=float)


def execution_components(
    side: np.ndarray,
    entry_reference: np.ndarray,
    exit_reference: np.ndarray,
    costs: CostModel,
    funding_fraction: np.ndarray,
) -> dict[str, np.ndarray]:
    """Return exact fill-to-fill return components as fractions of entry notional."""
    side = np.atleast_1d(np.asarray(side, dtype=float))
    entry_reference = np.atleast_1d(np.asarray(entry_reference, dtype=float))
    exit_reference = np.atleast_1d(np.asarray(exit_reference, dtype=float))
    entry_fill = entry_reference * (1.0 + side * costs.slippage_rate)
    exit_fill = exit_reference * (1.0 - side * costs.slippage_rate)
    raw_reference_return = side * (exit_reference - entry_reference) / entry_reference
    gross_fill_return = side * (exit_fill - entry_fill) / entry_fill
    slippage_cost = raw_reference_return - gross_fill_return
    fees = costs.fee_rate * (entry_fill + exit_fill) / entry_fill
    funding = np.asarray(funding_fraction, dtype=float)
    net = gross_fill_return - fees - funding
    return {
        "entry_fill": entry_fill,
        "exit_fill": exit_fill,
        "raw_reference_return": raw_reference_return,
        "gross_fill_return": gross_fill_return,
        "slippage_cost": slippage_cost,
        "fees": fees,
        "funding": funding,
        "net": net,
    }


def chronological_masks(
    timestamps: np.ndarray,
    train_end_ms: int,
    valid_start_ms: int,
    valid_end_ms: int,
    purge_minutes: int = 60,
) -> tuple[np.ndarray, np.ndarray]:
    """Expanding-fold masks with a horizon-aware purge on both sides."""
    if train_end_ms > valid_start_ms or valid_start_ms >= valid_end_ms:
        raise ValueError("split boundaries must be chronological")
    if purge_minutes < 0:
        raise ValueError("purge_minutes must be non-negative")
    timestamps = np.asarray(timestamps, dtype=np.int64)
    purge_ms = purge_minutes * 60_000
    train = timestamps < train_end_ms - purge_ms
    valid = (timestamps >= valid_start_ms + purge_ms) & (timestamps < valid_end_ms - purge_ms)
    if not train.any() or not valid.any():
        raise ValueError("empty chronological train or validation split")
    if timestamps[train].max() >= timestamps[valid].min():
        raise ValueError("purge/embargo did not isolate split boundary")
    return train, valid


def expected_value(probabilities: dict[str, float], payoffs: dict[str, float]) -> float:
    """Expected payoff for TP_FIRST/SL_FIRST/TIMEOUT with sign-safe payoffs."""
    if set(probabilities) != {"TP_FIRST", "SL_FIRST", "TIMEOUT"}:
        raise ValueError("probabilities must contain TP_FIRST, SL_FIRST, and TIMEOUT")
    if set(payoffs) != set(probabilities):
        raise ValueError("payoffs and probabilities must have identical outcomes")
    total = sum(probabilities.values())
    if not np.isclose(total, 1.0, atol=1e-8):
        raise ValueError("probabilities must sum to one")
    return float(sum(probabilities[name] * payoffs[name] for name in probabilities))


def _reliability(y_true: np.ndarray, probability: np.ndarray, bins: int = 10) -> tuple[list[dict], float]:
    rows: list[dict] = []
    error = 0.0
    edges = np.linspace(0.0, 1.0, bins + 1)
    for index in range(bins):
        if index == 0:
            mask = (probability >= edges[index]) & (probability <= edges[index + 1])
        else:
            mask = (probability > edges[index]) & (probability <= edges[index + 1])
        count = int(mask.sum())
        if not count:
            continue
        predicted = float(probability[mask].mean())
        observed = float(y_true[mask].mean())
        ece = count / len(y_true) * abs(predicted - observed)
        error += ece
        rows.append({
            "bin": index,
            "lower": float(edges[index]),
            "upper": float(edges[index + 1]),
            "n": count,
            "mean_probability": predicted,
            "observed_frequency": observed,
        })
    return rows, float(error)


def probability_metrics(
    y_true: np.ndarray,
    probabilities: np.ndarray,
    class_names: Iterable[str],
) -> dict:
    y_true = np.asarray(y_true, dtype=int)
    probabilities = np.asarray(probabilities, dtype=float)
    names = list(class_names)
    if probabilities.shape != (len(y_true), len(names)):
        raise ValueError("probability matrix shape does not match labels/classes")
    if not np.isfinite(probabilities).all() or (probabilities < 0).any():
        raise ValueError("probabilities must be finite and non-negative")
    probability_mass = probabilities.sum(axis=1, keepdims=True)
    if (probability_mass <= 0).any():
        raise ValueError("each probability row must have positive mass")
    probabilities = probabilities / probability_mass
    indices = np.arange(len(names))
    one_hot = np.eye(len(names), dtype=int)[y_true]
    precision, recall, f1, support = precision_recall_fscore_support(
        y_true, probabilities.argmax(axis=1), labels=indices, zero_division=0
    )
    per_class_ece = []
    reliability = {}
    for class_index, name in enumerate(names):
        rows, ece = _reliability(one_hot[:, class_index], probabilities[:, class_index])
        per_class_ece.append(ece)
        reliability[name] = rows
    macro_auc = None
    if len(np.unique(y_true)) == len(names):
        try:
            macro_auc = float(roc_auc_score(y_true, probabilities, multi_class="ovr", average="macro"))
        except ValueError:
            macro_auc = None
    return {
        "n": int(len(y_true)),
        "accuracy": float(accuracy_score(y_true, probabilities.argmax(axis=1))),
        "log_loss": float(log_loss(y_true, probabilities, labels=indices)),
        "brier": float(np.mean(np.sum((probabilities - one_hot) ** 2, axis=1))),
        "macro_auc_ovr": macro_auc,
        "mean_ece": float(np.mean(per_class_ece)),
        "class_counts": np.bincount(y_true, minlength=len(names)).tolist(),
        "precision": precision.tolist(),
        "recall": recall.tolist(),
        "f1": f1.tolist(),
        "support": support.tolist(),
        "mean_probability": probabilities.mean(axis=0).tolist(),
        "reliability": reliability,
    }


def binary_probability_metrics(y_true: np.ndarray, probability: np.ndarray) -> dict:
    y_true = np.asarray(y_true, dtype=int)
    probability = np.asarray(probability, dtype=float)
    if probability.shape != (len(y_true),):
        raise ValueError("binary probability shape mismatch")
    metrics = probability_metrics(
        y_true,
        np.column_stack([1.0 - probability, probability]),
        ("NO_TRADE", "TRADE"),
    )
    metrics["roc_auc"] = (
        float(roc_auc_score(y_true, probability)) if len(np.unique(y_true)) == 2 else None
    )
    return metrics


def _max_drawdown(returns: np.ndarray) -> float:
    if not len(returns):
        return 0.0
    equity = np.cumsum(returns)
    peak = np.maximum.accumulate(np.r_[0.0, equity])[1:]
    return float(np.max(peak - equity))


def trade_metrics(trades: list[dict]) -> dict:
    net = np.asarray([trade["net"] for trade in trades], dtype=float)
    gross = np.asarray([trade["gross"] for trade in trades], dtype=float)
    wins = net[net > 0]
    losses = net[net < 0]
    return {
        "trade_count": int(len(net)),
        "win_rate": float(len(wins) / len(net)) if len(net) else None,
        "expectancy": float(net.mean()) if len(net) else None,
        "net_return_sum": float(net.sum()) if len(net) else 0.0,
        "gross_return_sum": float(gross.sum()) if len(gross) else 0.0,
        "fees": float(sum(trade["fees"] for trade in trades)),
        "slippage": float(sum(trade["slippage_cost"] for trade in trades)),
        "funding": float(sum(trade["funding"] for trade in trades)),
        "sharpe": float(net.mean() / net.std(ddof=1)) if len(net) > 1 and net.std(ddof=1) else None,
        "sortino": float(net.mean() / losses.std(ddof=1)) if len(losses) > 1 else None,
        "max_drawdown": _max_drawdown(net),
        "profit_factor": float(wins.sum() / abs(losses.sum())) if len(losses) else None,
        "turnover_notional": float(2.0 * len(net)),
    }


def select_non_overlapping(
    candidate_indices: np.ndarray,
    predicted_ev: np.ndarray,
    required_edge: np.ndarray,
    horizon_minutes: int,
) -> np.ndarray:
    indices = np.unique(np.asarray(candidate_indices, dtype=int))
    ev = np.asarray(predicted_ev, dtype=float)[indices]
    required = np.asarray(required_edge, dtype=float)[indices]
    eligible = indices[np.isfinite(ev) & (ev >= required)]
    selected: list[int] = []
    next_free = -1
    for index in eligible:
        index = int(index)
        if index < next_free:
            continue
        selected.append(index)
        next_free = index + horizon_minutes
    return np.asarray(selected, dtype=int)


def realized_barrier_trades(
    timestamps: np.ndarray,
    entry_open: np.ndarray,
    exit_open: np.ndarray,
    labels: BarrierLabels,
    config: BarrierConfig,
    indices: np.ndarray,
    sides: np.ndarray,
    costs: CostModel,
    funding: FundingIndex,
    long_time_to_tp: np.ndarray,
    long_time_to_sl: np.ndarray,
    short_time_to_tp: np.ndarray,
    short_time_to_sl: np.ndarray,
    long_ambiguous: np.ndarray,
    short_ambiguous: np.ndarray,
) -> list[dict]:
    indices = np.asarray(indices, dtype=int)
    sides = np.asarray(sides, dtype=int)[indices]
    outcomes = np.where(
        sides == 1,
        labels.long_outcome[indices],
        labels.short_outcome[indices],
    )
    tp_time = np.where(sides == 1, long_time_to_tp[indices], short_time_to_tp[indices])
    sl_time = np.where(sides == 1, long_time_to_sl[indices], short_time_to_sl[indices])
    ambiguous = np.where(sides == 1, long_ambiguous[indices], short_ambiguous[indices])
    entry = np.asarray(entry_open, dtype=float)[indices]
    tp_reference = entry * (1.0 + sides * config.tp_bps / 10_000.0)
    sl_reference = entry * (1.0 - sides * config.sl_bps / 10_000.0)
    timeout_reference = np.asarray(exit_open, dtype=float)[indices + config.horizon_minutes + 1]
    exit_reference = np.where(outcomes == TP_FIRST, tp_reference, np.where(outcomes == SL_FIRST, sl_reference, timeout_reference))
    event_offset = np.where(
        outcomes == TP_FIRST,
        tp_time,
        np.where(outcomes == SL_FIRST, sl_time, config.horizon_minutes + 1),
    )
    exit_indices = indices + event_offset.astype(int)
    entry_fill = entry * (1.0 + sides * costs.slippage_rate)
    funding_cost = funding.actual_cost_fraction(sides, indices, exit_indices, entry_fill)
    components = execution_components(sides, entry, exit_reference, costs, funding_cost)
    trades = []
    for row, index in enumerate(indices):
        trades.append({
            "timestamp": int(timestamps[index]),
            "decision_index": int(index),
            "side": int(sides[row]),
            "outcome": ("SL_FIRST", "TIMEOUT", "TP_FIRST")[int(outcomes[row])],
            "holding_bars": int(event_offset[row]),
            "ambiguous_touch": bool(ambiguous[row]),
            "entry_fill": float(components["entry_fill"][row]),
            "exit_fill": float(components["exit_fill"][row]),
            "raw_reference_return": float(components["raw_reference_return"][row]),
            "gross": float(components["gross_fill_return"][row]),
            "slippage_cost": float(components["slippage_cost"][row]),
            "fees": float(components["fees"][row]),
            "funding": float(components["funding"][row]),
            "net": float(components["net"][row]),
        })
    return trades


def simulate_corrected_fixed_horizon(
    timestamps: np.ndarray,
    entry_open: np.ndarray,
    exit_open: np.ndarray,
    side: np.ndarray,
    predicted_return: np.ndarray,
    uncertainty: np.ndarray,
    funding: FundingIndex,
    candidate_indices: np.ndarray,
    horizon_minutes: int = 15,
    costs: CostModel | None = None,
    min_edge_multiplier: float = 2.0,
    apply_uncertainty_penalty: bool = False,
) -> dict:
    """Corrected fixed-horizon accounting used only for the EXP-020 ablation."""
    costs = costs or CostModel()
    indices = np.asarray(candidate_indices, dtype=int)
    indices = indices[indices + horizon_minutes + 1 < len(exit_open)]
    order = np.argsort(indices)
    indices = indices[order]
    side = np.asarray(side, dtype=float)[indices]
    predicted = np.asarray(predicted_return, dtype=float)[indices]
    uncertainty = np.asarray(uncertainty, dtype=float)[indices]
    entry = np.asarray(entry_open, dtype=float)[indices]
    predicted_exit = entry * (1.0 + side * predicted)
    exit_indices = indices + horizon_minutes + 1
    entry_fill = entry * (1.0 + side * costs.slippage_rate)
    visible_rate = funding.rates[indices]
    predicted_funding = funding.expected_cost_fraction(
        side, indices, exit_indices, entry_fill, visible_rate
    )
    predicted_components = execution_components(
        side, entry, predicted_exit, costs, predicted_funding
    )
    penalty = (
        uncertainty * np.abs(predicted_components["gross_fill_return"])
        if apply_uncertainty_penalty
        else np.zeros(len(indices))
    )
    adjusted = predicted_components["net"] - penalty
    expected_funding = np.abs(predicted_funding)
    required = min_edge_multiplier * (costs.reference_round_trip_cost + expected_funding)
    eligible_rows = np.flatnonzero(np.isfinite(adjusted) & (adjusted >= required))
    selected_rows: list[int] = []
    next_free = -1
    for row in eligible_rows:
        index = int(indices[row])
        if index < next_free:
            continue
        selected_rows.append(int(row))
        next_free = index + horizon_minutes
    selected_rows_array = np.asarray(selected_rows, dtype=int)
    selected = indices[selected_rows_array]
    actual_side = np.asarray(side, dtype=int)[selected_rows_array]
    actual_entry = entry[selected_rows_array]
    actual_exit = np.asarray(exit_open, dtype=float)[selected + horizon_minutes + 1]
    actual_exit_indices = selected + horizon_minutes + 1
    actual_entry_fill = actual_entry * (1.0 + actual_side * costs.slippage_rate)
    actual_funding = funding.actual_cost_fraction(
        actual_side, selected, actual_exit_indices, actual_entry_fill
    )
    realized = execution_components(
        actual_side, actual_entry, actual_exit, costs, actual_funding
    )
    trades = []
    for row, index in enumerate(selected):
        trades.append({
            "timestamp": int(timestamps[index]),
            "decision_index": int(index),
            "side": int(actual_side[row]),
            "holding_bars": horizon_minutes,
            "predicted_gross": float(predicted_components["gross_fill_return"][selected_rows_array[row]]),
            "predicted_net": float(predicted_components["net"][selected_rows_array[row]]),
            "uncertainty_penalty": float(penalty[selected_rows_array[row]]),
            "raw_reference_return": float(realized["raw_reference_return"][row]),
            "gross": float(realized["gross_fill_return"][row]),
            "slippage_cost": float(realized["slippage_cost"][row]),
            "fees": float(realized["fees"][row]),
            "funding": float(realized["funding"][row]),
            "net": float(realized["net"][row]),
        })
    result = trade_metrics(trades)
    result["mean_predicted_edge_all_candidates"] = float(predicted_components["net"].mean())
    result["mean_uncertainty_adjusted_edge_all_candidates"] = float(adjusted.mean())
    result["trades"] = trades
    result["cost_model"] = costs.as_dict()
    result["min_edge_multiplier"] = min_edge_multiplier
    result["uncertainty_penalty_applied"] = apply_uncertainty_penalty
    return result

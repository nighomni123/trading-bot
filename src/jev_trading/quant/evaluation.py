"""Validation metrics and fixed-horizon economic simulation for Phase 2 heads."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from sklearn.metrics import accuracy_score, log_loss, roc_auc_score

COST_PATH = Path(__file__).resolve().parents[3] / "configs" / "costs.json"
POLICY_PATH = Path(__file__).resolve().parents[3] / "configs" / "policy.json"


def regression_metrics(y: np.ndarray, prediction: np.ndarray) -> dict[str, float | int]:
    y = np.asarray(y, dtype=float)
    prediction = np.asarray(prediction, dtype=float)
    error = prediction - y
    corr = float(np.corrcoef(y, prediction)[0, 1]) if np.std(y) and np.std(prediction) else 0.0
    return {
        "n": int(len(y)),
        "mae": float(np.mean(np.abs(error))),
        "rmse": float(np.sqrt(np.mean(error * error))),
        "corr": corr,
        "prediction_mean": float(np.mean(prediction)),
        "prediction_std": float(np.std(prediction)),
        "target_mean": float(np.mean(y)),
        "target_std": float(np.std(y)),
    }


def direction_metrics(y_true: np.ndarray, probabilities: np.ndarray) -> dict[str, float | int]:
    y_true = np.asarray(y_true, dtype=int)
    probabilities = np.asarray(probabilities, dtype=float)
    if probabilities.shape != (len(y_true), 3):
        raise ValueError("direction probabilities must have shape (n, 3)")
    if not np.allclose(probabilities.sum(axis=1), 1.0, atol=1e-6):
        raise ValueError("direction probabilities must sum to one")
    one_hot = np.eye(3, dtype=int)[y_true]
    macro_auc = None
    if len(np.unique(y_true)) == 3:
        macro_auc = float(roc_auc_score(y_true, probabilities, multi_class="ovr", average="macro"))
    return {
        "n": int(len(y_true)),
        "accuracy": float(accuracy_score(y_true, probabilities.argmax(axis=1))),
        "log_loss": float(log_loss(y_true, probabilities, labels=[0, 1, 2])),
        "brier": float(np.mean(np.sum((probabilities - one_hot) ** 2, axis=1))),
        "macro_auc_ovr": macro_auc,
        "class_counts": np.bincount(y_true, minlength=3).tolist(),
    }


def uncertainty_reliability(
    y_true: np.ndarray,
    prediction: np.ndarray,
    uncertainty: np.ndarray,
    bins: int = 5,
) -> dict:
    y_true = np.asarray(y_true, dtype=float)
    prediction = np.asarray(prediction, dtype=float)
    uncertainty = np.asarray(uncertainty, dtype=float)
    error = np.abs(prediction - y_true)
    order = np.argsort(uncertainty, kind="mergesort")
    groups = np.array_split(order, min(bins, len(order)))
    bucket_rows = []
    for index, group in enumerate(groups):
        if not len(group):
            continue
        bucket_rows.append({
            "bucket": index,
            "n": int(len(group)),
            "uncertainty_low": float(uncertainty[group].min()),
            "uncertainty_high": float(uncertainty[group].max()),
            "mean_uncertainty": float(uncertainty[group].mean()),
            "mean_absolute_error": float(error[group].mean()),
        })
    errors = [row["mean_absolute_error"] for row in bucket_rows]
    uncertainty_values = [row["mean_uncertainty"] for row in bucket_rows]
    rank_error = np.argsort(np.argsort(errors))
    rank_uncertainty = np.argsort(np.argsort(uncertainty_values))
    rank_corr = float(np.corrcoef(rank_error, rank_uncertainty)[0, 1]) if len(errors) > 1 else 0.0
    return {
        "spearman_error_vs_uncertainty": rank_corr,
        "errors_monotonic": bool(all(a <= b for a, b in zip(errors, errors[1:]))),
        "buckets": bucket_rows,
    }


def _costs(fee_mult: float = 1.0, slippage_extra_pct: float = 0.0, delay_penalty_pct: float = 0.0) -> dict[str, float]:
    cfg = json.loads(COST_PATH.read_text())
    return {
        "fees": 2.0 * float(cfg["taker_fee_pct"]) / 100.0 * fee_mult,
        "slippage": 2.0 * (float(cfg["slippage_pct"]) + slippage_extra_pct) / 100.0,
        "delay": delay_penalty_pct / 100.0,
    }


def _max_drawdown(returns: np.ndarray) -> float:
    if not len(returns):
        return 0.0
    equity = np.cumsum(returns)
    peak = np.maximum.accumulate(np.r_[0.0, equity])[1:]
    drawdown = peak - equity
    return float(np.max(drawdown))


def predicted_edge_summary(
    p_up: np.ndarray,
    p_flat: np.ndarray,
    p_dn: np.ndarray,
    predicted_return: np.ndarray,
    uncertainty: np.ndarray,
    funding_rate: np.ndarray,
    *,
    horizon_minutes: int = 15,
    fee_mult: float = 1.0,
    slippage_extra_pct: float = 0.0,
    delay_penalty_pct: float = 0.0,
    min_edge_mult: float | None = None,
) -> dict[str, float | int | None]:
    """Summarize predicted economic edge before any realized outcome is attached."""
    if min_edge_mult is None:
        policy = json.loads(POLICY_PATH.read_text())
        min_edge_mult = float(policy["enter_long"]["min_edge_over_cost"])
    costs = _costs(fee_mult, slippage_extra_pct, delay_penalty_pct)
    fixed_cost = costs["fees"] + costs["slippage"] + costs["delay"]
    p_up, p_flat, p_dn = map(np.asarray, (p_up, p_flat, p_dn))
    predicted_return = np.asarray(predicted_return, dtype=float)
    uncertainty = np.asarray(uncertainty, dtype=float)
    funding_rate = np.asarray(funding_rate, dtype=float)
    side = np.where(
        (p_up >= p_flat) & (p_up >= p_dn),
        1.0,
        np.where((p_dn >= p_flat) & (p_dn >= p_up), -1.0, 0.0),
    )
    funding = side * funding_rate * horizon_minutes / 480.0
    gross = side * predicted_return
    net = gross - fixed_cost - funding
    adjusted = net - uncertainty * np.abs(gross)
    required = min_edge_mult * (fixed_cost + np.abs(funding))
    eligible = (side != 0) & (adjusted >= required)
    return {
        "eligible_bars": int(eligible.sum()),
        "mean_predicted_gross": float(np.mean(gross)),
        "mean_predicted_net_edge": float(np.mean(net)),
        "mean_uncertainty_adjusted_edge": float(np.mean(adjusted)),
        "mean_required_edge": float(np.mean(required)),
        "fraction_eligible": float(eligible.mean()),
    }


def simulate_fixed_horizon_segments(
    timestamps: np.ndarray,
    p_up: np.ndarray,
    p_flat: np.ndarray,
    p_dn: np.ndarray,
    predicted_return: np.ndarray,
    actual_execution_return: np.ndarray,
    funding_rate: np.ndarray,
    uncertainty: np.ndarray,
    **kwargs,
) -> dict:
    """Run fixed-horizon simulation independently across feature-null gaps."""
    timestamps = np.asarray(timestamps, dtype=np.int64)
    if not len(timestamps):
        raise ValueError("cannot simulate an empty timestamp vector")
    starts = np.r_[0, np.flatnonzero(np.diff(timestamps) != 60_000) + 1]
    ends = np.r_[starts[1:], len(timestamps)]
    parts = []
    for start, end in zip(starts, ends):
        sl = slice(start, end)
        parts.append(simulate_fixed_horizon(
            timestamps[sl], p_up[sl], p_flat[sl], p_dn[sl], predicted_return[sl],
            actual_execution_return[sl], funding_rate[sl], uncertainty[sl], **kwargs,
        ))
    trades = [trade for part in parts for trade in part["trades"]]
    realized = np.asarray([t["realized_net"] for t in trades], dtype=float)
    costs = np.asarray([t["cost"] for t in trades], dtype=float)
    wins = realized[realized > 0]
    losses = realized[realized < 0]
    return {
        "scenario": parts[0]["scenario"],
        "segment_count": len(parts),
        "candidate_bars": sum(part["candidate_bars"] for part in parts),
        "trade_count": len(trades),
        "selection_rate": len(trades) / len(timestamps),
        "net_return_sum": float(realized.sum()) if len(realized) else 0.0,
        "gross_return_sum": float(sum(t["side"] * t["actual_execution_return"] for t in trades)),
        "cost_sum": float(costs.sum()) if len(costs) else 0.0,
        "expectancy": float(realized.mean()) if len(realized) else None,
        "hit_rate": float(len(wins) / len(realized)) if len(realized) else None,
        "profit_factor": float(wins.sum() / abs(losses.sum())) if len(losses) and losses.sum() != 0 else None,
        "sharpe": float(realized.mean() / realized.std(ddof=1)) if len(realized) > 1 and realized.std(ddof=1) else None,
        "sortino": float(realized.mean() / realized[realized < 0].std(ddof=1)) if len(realized) > 1 and len(realized[realized < 0]) > 1 else None,
        "max_drawdown": _max_drawdown(realized),
        "trades": trades,
    }


def simulate_fixed_horizon(
    timestamps: np.ndarray,
    p_up: np.ndarray,
    p_flat: np.ndarray,
    p_down: np.ndarray,
    predicted_return: np.ndarray,
    actual_execution_return: np.ndarray,
    funding_rate: np.ndarray,
    uncertainty: np.ndarray,
    *,
    horizon_minutes: int = 15,
    fee_mult: float = 1.0,
    slippage_extra_pct: float = 0.0,
    delay_penalty_pct: float = 0.0,
    min_edge_mult: float | None = None,
) -> dict:
    """Simulate non-overlapping fixed-horizon trades without future features.

    A side is selected from explicit multiclass probabilities. A trade is taken
    only when its uncertainty-adjusted predicted net edge clears the configured
    multiple of scenario costs. Realized open-to-open returns are attached only
    after selection.
    """
    if min_edge_mult is None:
        policy = json.loads(POLICY_PATH.read_text())
        min_edge_mult = float(policy["enter_long"]["min_edge_over_cost"])
    n = len(timestamps)
    if n > 1 and not np.all(np.diff(np.asarray(timestamps, dtype=np.int64)) == 60_000):
        raise ValueError("fixed-horizon simulation requires contiguous one-minute bars")
    arrays = [p_up, p_flat, p_down, predicted_return, actual_execution_return, funding_rate, uncertainty]
    if any(len(values) != n for values in arrays):
        raise ValueError("economic simulation arrays must be aligned")
    costs = _costs(fee_mult, slippage_extra_pct, delay_penalty_pct)
    fixed_cost = costs["fees"] + costs["slippage"] + costs["delay"]
    trades = []
    i = 0
    while i < n:
        values = [values[i] for values in arrays]
        if any(value is None or not np.isfinite(value) for value in values):
            i += 1
            continue
        if p_up[i] >= p_flat[i] and p_up[i] >= p_down[i]:
            side = 1
        elif p_down[i] >= p_flat[i] and p_down[i] >= p_up[i]:
            side = -1
        else:
            side = 0
        predicted_gross = side * predicted_return[i]
        predicted_net = predicted_gross - fixed_cost - side * funding_rate[i] * horizon_minutes / 480.0
        uncertainty_penalty = uncertainty[i] * abs(predicted_gross)
        adjusted_edge = predicted_net - uncertainty_penalty
        required_edge = min_edge_mult * (fixed_cost + abs(funding_rate[i]) * horizon_minutes / 480.0)
        if side != 0 and adjusted_edge >= required_edge:
            funding = side * funding_rate[i] * horizon_minutes / 480.0
            realized_net = side * actual_execution_return[i] - fixed_cost - funding
            trades.append({
                "timestamp": int(timestamps[i]),
                "side": side,
                "predicted_return": float(predicted_return[i]),
                "actual_execution_return": float(actual_execution_return[i]),
                "predicted_net_edge": float(predicted_net),
                "uncertainty_adjusted_edge": float(adjusted_edge),
                "realized_net": float(realized_net),
                "cost": float(fixed_cost + funding),
            })
            i += horizon_minutes
        else:
            i += 1
    realized = np.asarray([trade["realized_net"] for trade in trades], dtype=float)
    gross = np.asarray([
        trade["side"] * trade["actual_execution_return"] for trade in trades
    ], dtype=float)
    costs_paid = np.asarray([trade["cost"] for trade in trades], dtype=float)
    wins = realized[realized > 0]
    losses = realized[realized < 0]
    return {
        "scenario": {
            "fee_mult": fee_mult,
            "slippage_extra_pct": slippage_extra_pct,
            "delay_penalty_pct": delay_penalty_pct,
            "horizon_minutes": horizon_minutes,
            "min_edge_mult": min_edge_mult,
        },
        "candidate_bars": int(n),
        "trade_count": int(len(trades)),
        "selection_rate": float(len(trades) / n) if n else 0.0,
        "net_return_sum": float(realized.sum()) if len(realized) else 0.0,
        "gross_return_sum": float(gross.sum()) if len(gross) else 0.0,
        "cost_sum": float(costs_paid.sum()) if len(costs_paid) else 0.0,
        "expectancy": float(realized.mean()) if len(realized) else None,
        "hit_rate": float(len(wins) / len(realized)) if len(realized) else None,
        "profit_factor": float(wins.sum() / abs(losses.sum())) if len(losses) and losses.sum() != 0 else None,
        "sharpe": float(realized.mean() / realized.std(ddof=1)) if len(realized) > 1 and realized.std(ddof=1) else None,
        "sortino": float(realized.mean() / realized[realized < 0].std(ddof=1)) if len(realized) > 1 and len(realized[realized < 0]) > 1 else None,
        "max_drawdown": _max_drawdown(realized),
        "trades": trades,
    }

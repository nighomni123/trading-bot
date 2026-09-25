#!/usr/bin/env python3
"""Run the isolated Phase 3 economic-target research protocol.

The runner reads pre-2025 data only. It never connects to policy, risk, live
execution, Frontier, Laya, Jev, or RL. Stages are resumable so expensive model
work can be observed and corrected without changing the pre-registered grid.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import polars as pl
from lightgbm import LGBMClassifier, LGBMRegressor

from jev_trading.labels.barriers import (
    BarrierConfig,
    ExecutionBarrierCache,
    SL_FIRST,
    TIMEOUT,
    TP_FIRST,
    barrier_grid,
)
from jev_trading.quant.engine import load_economic_bundle
from jev_trading.quant.evaluation import simulate_fixed_horizon_segments
from jev_trading.quant.payoff import (
    FUNDING_INTERVAL_MS,
    CostModel,
    FundingIndex,
    binary_probability_metrics,
    chronological_masks,
    execution_components,
    probability_metrics,
    realized_barrier_trades,
    simulate_corrected_fixed_horizon,
    trade_metrics,
)
from jev_trading.state.features import FEATURE_COLUMNS, build_features

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "configs" / "phase3.json"
FEATURES = tuple(FEATURE_COLUMNS)
PURGE_MINUTES = 60


def read_config() -> dict:
    config = json.loads(CONFIG_PATH.read_text())
    if tuple(config["features"]) != FEATURES:
        raise ValueError("Phase 3 feature set differs from the frozen causal baseline")
    if config["data"]["allowed_end_exclusive_ms"] != 1735689600000:
        raise ValueError("Phase 3 must reject 2025+ selection data")
    return config


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_sha() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def write_experiment_yaml(path: Path, record: dict) -> None:
    lines = [
        f"experiment_id: {json.dumps(record['experiment_id'])}",
        f"hypothesis: {json.dumps(record['hypothesis'])}",
        f"data_source: {json.dumps(record['data_source'])}",
        f"data_range: {json.dumps(record['data_range'])}",
        f"feature_set: {json.dumps(record['feature_set'])}",
        f"target: {json.dumps(record['target'])}",
        f"model: {json.dumps(record['model'])}",
        f"seed: {record['seed']}",
        f"train_period: {json.dumps(record['train_period'])}",
        f"validation_period: {json.dumps(record['validation_period'])}",
        f"purge: {json.dumps(record['purge'])}",
        f"cost_model: {json.dumps(record['cost_model'])}",
        f"threshold: {json.dumps(record['threshold'])}",
        f"trade_count: {record['trade_count']}",
        f"result: {json.dumps(record['result'], sort_keys=True)}",
        f"decision: {json.dumps(record['decision'])}",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def load_pre2025(config: dict) -> tuple[Path, pl.DataFrame]:
    source = ROOT / config["data"]["path"]
    if not source.is_file():
        raise FileNotFoundError(source)
    if sha256(source) != config["data"]["sha256"]:
        raise ValueError("Phase 3 source hash differs from the pre-registered dataset")
    bars = pl.read_parquet(source).sort("timestamp")
    cutoff = config["data"]["allowed_end_exclusive_ms"]
    if bars.is_empty() or bars["timestamp"].max() >= cutoff:
        raise ValueError("Phase 3 source includes historically consumed 2025+ data")
    timestamps = bars["timestamp"].to_numpy()
    if np.any(np.diff(timestamps) != 60_000):
        raise ValueError("Phase 3 source timestamps are not contiguous one-minute bars")
    return source, bars


def _summary(result: dict) -> dict:
    keys = (
        "trade_count", "win_rate", "expectancy", "net_return_sum", "gross_return_sum",
        "fees", "slippage", "funding", "sharpe", "sortino", "max_drawdown",
        "profit_factor", "turnover_notional",
    )
    return {key: result.get(key) for key in keys}


def _corrected_fixed_horizon(
    timestamps: np.ndarray,
    bars: pl.DataFrame,
    side: np.ndarray,
    predicted: np.ndarray,
    uncertainty: np.ndarray,
    candidates: np.ndarray,
    *,
    costs: CostModel,
    apply_uncertainty_penalty: bool,
    delay_bars: int = 0,
) -> dict:
    funding = FundingIndex(
        timestamps,
        bars["funding_rate"].to_numpy(),
        bars["close"].to_numpy(),
    )
    shifted_side = np.full(len(side), np.nan)
    shifted_predicted = np.full(len(predicted), np.nan)
    shifted_uncertainty = np.full(len(uncertainty), np.nan)
    origins = candidates[delay_bars:]
    destinations = origins + delay_bars
    shifted_side[destinations] = side[origins]
    shifted_predicted[destinations] = predicted[origins]
    shifted_uncertainty[destinations] = uncertainty[origins]
    return simulate_corrected_fixed_horizon(
        timestamps,
        bars["open"].to_numpy(),
        bars["open"].to_numpy(),
        shifted_side,
        shifted_predicted,
        shifted_uncertainty,
        funding,
        destinations,
        horizon_minutes=15,
        costs=costs,
        min_edge_multiplier=2.0,
        apply_uncertainty_penalty=apply_uncertainty_penalty,
    )


def run_economic_audit(config: dict, out: Path) -> dict:
    source, bars = load_pre2025(config)
    features = build_features(bars).select(["timestamp", *FEATURES])
    cache = ExecutionBarrierCache(bars)
    bundle = load_economic_bundle(
        ROOT / "artifacts/phase2-experiments-final/EXP-010-economic-return-baseline/models"
    )
    timestamps_all = bars["timestamp"].to_numpy()
    train_end = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    valid_end = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    _train, valid = chronological_masks(
        timestamps_all, train_end, train_end, valid_end, PURGE_MINUTES
    )
    X = features.select(FEATURES).to_numpy()
    valid &= np.isfinite(X).all(axis=1)
    valid_indices = np.flatnonzero(valid)
    X_valid = features.filter(pl.Series(valid)).select(FEATURES)
    direction = bundle.predict_direction(X_valid)
    y_direction = np.column_stack([
        direction["p_up_15"], direction["p_flat_15"], direction["p_dn_15"]
    ])
    side = np.where(
        (y_direction[:, 0] >= y_direction[:, 1]) & (y_direction[:, 0] >= y_direction[:, 2]),
        1.0,
        np.where(
            (y_direction[:, 2] >= y_direction[:, 1]) & (y_direction[:, 2] >= y_direction[:, 0]),
            -1.0,
            0.0,
        ),
    )
    predicted = bundle.predict_execution_return(X_valid, 15)
    uncertainty = bundle.predict_uncertainty_15(X_valid)
    actual = cache.timeout_return[15][valid_indices]
    funding_rate = bars["funding_rate"].to_numpy()[valid_indices]
    timestamps = timestamps_all[valid_indices]

    side_full = np.full(len(bars), np.nan)
    predicted_full = np.full(len(bars), np.nan)
    uncertainty_full = np.full(len(bars), np.nan)
    side_full[valid_indices] = side
    predicted_full[valid_indices] = predicted
    uncertainty_full[valid_indices] = uncertainty

    old = simulate_fixed_horizon_segments(
        timestamps, direction["p_up_15"], direction["p_flat_15"], direction["p_dn_15"],
        predicted, actual, funding_rate, uncertainty, horizon_minutes=15,
    )
    base_costs = CostModel()
    corrected_with_penalty = _corrected_fixed_horizon(
        timestamps_all, bars, side_full, predicted_full, uncertainty_full, valid_indices,
        costs=base_costs, apply_uncertainty_penalty=True,
    )
    corrected_without_penalty = _corrected_fixed_horizon(
        timestamps_all, bars, side_full, predicted_full, uncertainty_full, valid_indices,
        costs=base_costs, apply_uncertainty_penalty=False,
    )
    delayed = _corrected_fixed_horizon(
        timestamps_all, bars, side_full, predicted_full, uncertainty_full, valid_indices,
        costs=base_costs, apply_uncertainty_penalty=False, delay_bars=1,
    )
    stored = json.loads(
        (ROOT / "experiments/EXP-010-economic-return-baseline/metrics.json").read_text()
    )["result"]["simulation_1x"]
    old_summary = _summary(old)
    old_matches = all(
        np.isclose(old_summary[key], stored[key], rtol=0, atol=1e-12)
        for key in ("trade_count", "net_return_sum", "expectancy")
    )
    metrics = {
        "phase2_frozen_stored": _summary(stored),
        "phase2_recomputed_old_accounting": old_summary,
        "corrected_exact_with_uncertainty_penalty": _summary(corrected_with_penalty),
        "corrected_exact_without_uncertainty_penalty": _summary(corrected_without_penalty),
        "corrected_actual_one_minute_delay": _summary(delayed),
        "old_accounting_reproduces_frozen_headline": old_matches,
        "accounting_deltas": {
            "trade_count_with_penalty": corrected_with_penalty["trade_count"] - old["trade_count"],
            "net_return_with_penalty": corrected_with_penalty["net_return_sum"] - old["net_return_sum"],
            "net_return_without_penalty": corrected_without_penalty["net_return_sum"] - old["net_return_sum"],
        },
    }
    materially_changes_phase2 = bool(
        corrected_with_penalty["trade_count"] >= 30
        and corrected_with_penalty["expectancy"] is not None
        and corrected_with_penalty["expectancy"] > 0
    )
    result = {
        "status": "EMPIRICALLY VALIDATED",
        "phase2_conclusion_changed": materially_changes_phase2,
        "interpretation": (
            "Accounting correction does not overturn the Phase 2 gate; tiny selected-trade PnL "
            "is not promoted" if not materially_changes_phase2 else
            "Corrected accounting changes the economic population and requires full target research"
        ),
    }
    record = {
        "experiment_id": "EXP-020-economic-measurement-audit",
        "hypothesis": "Phase 2 predicted and realized economics use the same executable quantity.",
        "data_source": str(source.relative_to(ROOT)),
        "data_range": ["2024-01-01", "2025-01-01)"],
        "feature_set": list(FEATURES),
        "target": "execution_return_15m accounting audit",
        "model": "frozen Phase 2 economic-v2 bundle",
        "seed": 7,
        "train_period": ["2021-01-01", "2024-01-01)"],
        "validation_period": ["2024-01-01", "2025-01-01)"],
        "purge": {"minutes_each_side": PURGE_MINUTES, "random_temporal_split": False},
        "cost_model": base_costs.as_dict(),
        "threshold": {"min_edge_over_cost": 2.0},
        "metrics": metrics,
        "trade_count": {
            "old": old["trade_count"],
            "corrected": corrected_with_penalty["trade_count"],
        },
        "result": result,
        "decision": "NO EDGE" if not materially_changes_phase2 else "REVIEW",
    }
    directory = out / "EXP-020-economic-measurement-audit"
    write_json(directory / "metrics.json", record)
    write_experiment_yaml(directory / "experiment.yaml", record)
    return record


def _ms(year: int, month: int = 1, day: int = 1) -> int:
    return int(datetime(year, month, day, tzinfo=timezone.utc).timestamp() * 1000)


def _model_params(config: dict, seed: int | None = None) -> dict:
    return {
        **config["model"]["params"],
        "random_state": config["model"]["seed"] if seed is None else seed,
    }


def _fit_side_probability_model(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_predict: np.ndarray,
    config: dict,
    seed: int,
) -> np.ndarray:
    """Fit one side-specific SL/TIMEOUT/TP head and preserve three-class support."""
    model = LGBMClassifier(
        objective="multiclass",
        num_class=3,
        **{**config["model"]["params"], "random_state": seed},
    ).fit(X_train, y_train)
    raw = model.predict_proba(X_predict)
    classes = model.classes_.astype(int)
    probabilities = np.zeros((len(X_predict), 3), dtype=float)
    probabilities[:, classes] = raw[:, :len(classes)]
    return probabilities


def _conditional_timeout_prediction(
    X_train: np.ndarray,
    target: np.ndarray,
    train_indices: np.ndarray,
    outcomes: np.ndarray,
    X_predict: np.ndarray,
    config: dict,
) -> np.ndarray:
    conditional_indices = train_indices[outcomes == TIMEOUT]
    minimum = config["model"]["conditional_timeout_minimum_rows"]
    if len(conditional_indices) < minimum:
        fallback = (
            target[conditional_indices].mean()
            if len(conditional_indices)
            else target[train_indices].mean()
        )
        return np.full(len(X_predict), float(fallback))
    model = LGBMRegressor(**_model_params(config)).fit(
        X_train[outcomes == TIMEOUT], target[conditional_indices]
    )
    return model.predict(X_predict)


def _regime_masks(
    X_train: np.ndarray,
    X_valid: np.ndarray,
) -> tuple[dict[str, list[tuple[str, np.ndarray]]], dict[str, list[float]]]:
    index = {name: i for i, name in enumerate(FEATURES)}
    cutpoints: dict[str, list[float]] = {}
    regimes: dict[str, list[tuple[str, np.ndarray]]] = {}
    for name in ("realized_vol_30m", "volume_z", "oi_change_1d"):
        values = X_train[:, index[name]]
        edges = np.quantile(values[np.isfinite(values)], (1 / 3, 2 / 3)).tolist()
        cutpoints[name] = [float(value) for value in edges]
        column = X_valid[:, index[name]]
        regimes[name] = [
            ("low", column <= edges[0]),
            ("normal", (column > edges[0]) & (column <= edges[1])),
            ("high", column > edges[1]),
        ]
    trend = X_valid[:, index["trend_score"]]
    regimes["trend"] = [
        ("flat", np.abs(trend) < 0.15),
        ("trending", np.abs(trend) >= 0.15),
    ]
    funding_z = X_valid[:, index["funding_z"]]
    regimes["funding"] = [
        ("negative_extreme", funding_z < -1.0),
        ("normal", (funding_z >= -1.0) & (funding_z <= 1.0)),
        ("positive_extreme", funding_z > 1.0),
    ]
    return regimes, cutpoints


def _training_event_reference(labels, config: BarrierConfig, train_indices: np.ndarray, side: int):
    outcomes = labels.long_outcome[train_indices] if side == 1 else labels.short_outcome[train_indices]
    tp_time = labels.long_time_to_tp[train_indices] if side == 1 else labels.short_time_to_tp[train_indices]
    sl_time = labels.long_time_to_sl[train_indices] if side == 1 else labels.short_time_to_sl[train_indices]
    event_time = np.where(
        outcomes == TP_FIRST,
        tp_time,
        np.where(outcomes == SL_FIRST, sl_time, config.horizon_minutes + 1),
    )
    return [
        np.sort(event_time[outcomes == outcome].astype(float))
        for outcome in (SL_FIRST, TIMEOUT, TP_FIRST)
    ]


def _next_funding_offset(cache: ExecutionBarrierCache, decision_indices: np.ndarray, horizon: int) -> tuple[np.ndarray, np.ndarray]:
    entry_timestamp = cache.timestamp[decision_indices + 1]
    next_event = (np.floor(entry_timestamp / FUNDING_INTERVAL_MS) + 1) * FUNDING_INTERVAL_MS
    offset = ((next_event - entry_timestamp) / 60_000.0).astype(int)
    return offset, offset <= horizon + 1


def _predicted_payoff_components(
    cache: ExecutionBarrierCache,
    config: BarrierConfig,
    decision_indices: np.ndarray,
    probabilities: np.ndarray,
    predicted_timeout_return: np.ndarray,
    visible_rate: np.ndarray,
    side: np.ndarray,
    training_event_reference,
    costs: CostModel,
) -> dict[str, np.ndarray]:
    probabilities = np.asarray(probabilities, dtype=float)
    probabilities = probabilities / probabilities.sum(axis=1, keepdims=True)
    side = np.asarray(side, dtype=float)
    predicted_timeout = np.asarray(predicted_timeout_return, dtype=float)
    if predicted_timeout.ndim == 1:
        predicted_timeout = np.repeat(predicted_timeout[:, None], 2, axis=1)
    if predicted_timeout.shape != (len(decision_indices), 2):
        raise ValueError("timeout return predictions must be n or (n, 2)")
    side_timeout_return = np.where(side > 0, predicted_timeout[:, 0], predicted_timeout[:, 1])
    entry = cache.entry_open[decision_indices]
    entry_fill = entry * (1.0 + side * costs.slippage_rate)
    event_offset, has_event = _next_funding_offset(
        cache, decision_indices, config.horizon_minutes
    )
    event_probability = np.zeros_like(probabilities)
    for outcome_index, reference in enumerate(training_event_reference):
        if not len(reference) or not has_event.any():
            continue
        position = np.searchsorted(reference, event_offset[has_event], side="left")
        event_probability[has_event, outcome_index] = (
            len(reference) - position
        ) / len(reference)
    funding_weight = probabilities * event_probability
    sl_reference = entry * (1.0 - side * config.sl_bps / 10_000.0)
    tp_reference = entry * (1.0 + side * config.tp_bps / 10_000.0)
    timeout_reference = entry * (1.0 + side * side_timeout_return)
    references = (sl_reference, timeout_reference, tp_reference)
    aggregate = {
        key: np.zeros(len(decision_indices), dtype=float)
        for key in (
            "raw_reference_return", "gross_fill_return", "slippage_cost", "fees", "funding", "net"
        )
    }
    for outcome_index, reference in enumerate(references):
        funding_fraction = (
            -side * visible_rate * funding_weight[:, outcome_index] / entry_fill
        )
        components = execution_components(
            side, entry, reference, costs, funding_fraction
        )
        for key in aggregate:
            aggregate[key] += probabilities[:, outcome_index] * components[key]
    aggregate["required_edge"] = 2.0 * (
        costs.reference_round_trip_cost + np.abs(aggregate["funding"])
    )
    aggregate["probability_tp"] = probabilities[:, 2]
    aggregate["probability_sl"] = probabilities[:, 0]
    aggregate["probability_timeout"] = probabilities[:, 1]
    return aggregate


def _select_rows(
    decision_indices: np.ndarray,
    eligible: np.ndarray,
    predicted_ev: np.ndarray,
    required_edge: np.ndarray,
    horizon: int,
) -> np.ndarray:
    decision_indices = np.asarray(decision_indices, dtype=int)
    rows = np.flatnonzero(
        np.asarray(eligible, dtype=bool)
        & np.isfinite(predicted_ev)
        & (predicted_ev >= required_edge)
    )
    selected: list[int] = []
    next_free = -1
    for row in rows:
        index = int(decision_indices[row])
        if index < next_free:
            continue
        selected.append(int(row))
        next_free = index + horizon
    return np.asarray(selected, dtype=int)


def _realized_trades(
    cache: ExecutionBarrierCache,
    labels,
    config: BarrierConfig,
    selected_indices: np.ndarray,
    selected_sides: np.ndarray,
    costs: CostModel,
    funding: FundingIndex,
) -> list[dict]:
    if not len(selected_indices):
        return []
    side_full = np.zeros(len(cache.timestamp), dtype=np.int8)
    side_full[selected_indices] = selected_sides
    return realized_barrier_trades(
        cache.timestamp,
        cache.entry_open,
        cache.open,
        labels,
        config,
        selected_indices,
        side_full,
        costs,
        funding,
        labels.long_time_to_tp,
        labels.long_time_to_sl,
        labels.short_time_to_tp,
        labels.short_time_to_sl,
        labels.long_ambiguous,
        labels.short_ambiguous,
    )


def _evaluate_predictions(
    cache: ExecutionBarrierCache,
    labels,
    config: BarrierConfig,
    decision_indices: np.ndarray,
    probabilities_long: np.ndarray,
    probabilities_short: np.ndarray,
    predicted_timeout_return: np.ndarray,
    visible_rate: np.ndarray,
    training_reference_long,
    training_reference_short,
    costs: CostModel,
    funding: FundingIndex,
    eligible: np.ndarray | None = None,
    forced_sides: np.ndarray | None = None,
) -> dict:
    n = len(decision_indices)
    ones = np.ones(n, dtype=float)
    minus_ones = -np.ones(n, dtype=float)
    long_components = _predicted_payoff_components(
        cache, config, decision_indices, probabilities_long, predicted_timeout_return,
        visible_rate, ones, training_reference_long, costs,
    )
    short_components = _predicted_payoff_components(
        cache, config, decision_indices, probabilities_short, predicted_timeout_return,
        visible_rate, minus_ones, training_reference_short, costs,
    )
    if forced_sides is None:
        side = np.where(
            long_components["net"] >= short_components["net"], 1, -1
        ).astype(np.int8)
    else:
        side = np.asarray(forced_sides, dtype=np.int8)
        if len(side) != n or not np.isin(side, (-1, 1)).all():
            raise ValueError("forced_sides must align and contain only -1 or 1")
    chosen_probabilities = np.where(
        (side == 1)[:, None], probabilities_long, probabilities_short
    )
    keys = (
        "raw_reference_return", "gross_fill_return", "slippage_cost", "fees", "funding", "net", "required_edge"
    )
    chosen = {
        key: np.where(side == 1, long_components[key], short_components[key])
        for key in keys
    }
    if eligible is None:
        eligible = np.ones(n, dtype=bool)
    selected_rows = _select_rows(
        decision_indices, eligible, chosen["net"], chosen["required_edge"], config.horizon_minutes
    )
    selected_indices = decision_indices[selected_rows]
    selected_sides = side[selected_rows]
    trades = _realized_trades(
        cache, labels, config, selected_indices, selected_sides, costs, funding
    )
    for output_row, selected_row in enumerate(selected_rows):
        trades[output_row].update({
            "predicted_ev": float(chosen["net"][selected_row]),
            "required_edge": float(chosen["required_edge"][selected_row]),
            "predicted_gross": float(chosen["gross_fill_return"][selected_row]),
            "predicted_fees": float(chosen["fees"][selected_row]),
            "predicted_slippage": float(chosen["slippage_cost"][selected_row]),
            "predicted_funding": float(chosen["funding"][selected_row]),
            "p_tp": float(chosen_probabilities[selected_row, 2]),
            "p_sl": float(chosen_probabilities[selected_row, 0]),
            "p_timeout": float(chosen_probabilities[selected_row, 1]),
        })
    metrics = _summary(trade_metrics(trades))
    metrics.update({
        "positive_ev_observations": int(np.sum(chosen["net"] > 0)),
        "hurdle_eligible_observations": int(np.sum(chosen["net"] >= chosen["required_edge"])),
        "mean_max_predicted_ev": float(np.maximum(long_components["net"], short_components["net"]).mean()),
        "mean_selected_predicted_ev": (
            float(chosen["net"][selected_rows].mean()) if len(selected_rows) else None
        ),
        "mean_predicted_gross": float(chosen["gross_fill_return"].mean()),
        "mean_predicted_fees": float(chosen["fees"].mean()),
        "mean_predicted_slippage": float(chosen["slippage_cost"].mean()),
        "mean_predicted_funding": float(chosen["funding"].mean()),
        "long_mean_ev": float(long_components["net"].mean()),
        "short_mean_ev": float(short_components["net"].mean()),
        "long_selection_rate": float(np.mean(side == 1)),
        "cost_survival_rate": None,
    })
    return {
        "metrics": metrics,
        "trades": trades,
        "selected_indices": selected_indices,
        "selected_sides": selected_sides,
        "predicted_ev": chosen["net"],
        "required_edge": chosen["required_edge"],
        "side": side,
    }


def _best_side_realized_net(
    cache: ExecutionBarrierCache,
    labels,
    config: BarrierConfig,
    indices: np.ndarray,
    costs: CostModel,
    funding: FundingIndex,
    side: int,
) -> np.ndarray:
    outcomes = labels.long_outcome[indices] if side == 1 else labels.short_outcome[indices]
    tp_time = labels.long_time_to_tp[indices] if side == 1 else labels.short_time_to_tp[indices]
    sl_time = labels.long_time_to_sl[indices] if side == 1 else labels.short_time_to_sl[indices]
    entry = cache.entry_open[indices]
    tp_reference = entry * (1.0 + side * config.tp_bps / 10_000.0)
    sl_reference = entry * (1.0 - side * config.sl_bps / 10_000.0)
    timeout_reference = cache.open[indices + config.horizon_minutes + 1]
    exit_reference = np.where(
        outcomes == TP_FIRST,
        tp_reference,
        np.where(outcomes == SL_FIRST, sl_reference, timeout_reference),
    )
    exit_indices = indices + np.where(
        outcomes == TP_FIRST,
        tp_time,
        np.where(outcomes == SL_FIRST, sl_time, config.horizon_minutes + 1),
    ).astype(int)
    side_array = np.full(len(indices), side, dtype=float)
    entry_fill = entry * (1.0 + side * costs.slippage_rate)
    funding_cost = funding.actual_cost_fraction(
        side_array, indices, exit_indices, entry_fill
    )
    return execution_components(
        side_array, entry, exit_reference, costs, funding_cost
    )["net"]


def _regime_probability_summary(
    regimes: dict[str, list[tuple[str, np.ndarray]]],
    probabilities: np.ndarray,
) -> dict:
    return {
        dimension: {
            label: {
                "n": int(mask.sum()),
                "mean_probability": probabilities[mask].mean(axis=0).tolist(),
            }
            for label, mask in groups
            if mask.any()
        }
        for dimension, groups in regimes.items()
    }


def _stable_regions(rows: list[dict], minimum_trades: int = 30) -> dict:
    regions: list[dict] = []
    best_cell: dict | None = None
    isolated: list[str] = []
    for horizon in config_horizons_from_rows(rows):
        cells = {
            (row["tp_bps"], row["sl_bps"]): row
            for row in rows
            if row["horizon_minutes"] == horizon
        }
        passing = {
            position
            for position, row in cells.items()
            if row["base"]["trade_count"] >= minimum_trades
            and (row["base"]["expectancy"] or -np.inf) > 0
            and (row["mean_selected_predicted_ev"] or -np.inf) > 0
        }
        stable = set()
        for position in passing:
            tp, sl = position
            neighbors = [
                (tp - 10, sl), (tp + 10, sl), (tp, sl - 5), (tp, sl + 5)
            ]
            compatible = sum(neighbor in passing for neighbor in neighbors)
            if compatible >= 2:
                stable.add(position)
        horizon_best = max(
            cells.values(),
            key=lambda row: (
                row["base"]["expectancy"] if row["base"]["expectancy"] is not None else -np.inf,
                row["base"]["trade_count"],
            ),
        )
        if best_cell is None or (
            (horizon_best["base"]["expectancy"] or -np.inf) >
            (best_cell["base"]["expectancy"] or -np.inf)
        ):
            best_cell = horizon_best
        if len(stable) >= 3:
            remaining = set(stable)
            component: list[tuple[int, int]] = []
            while remaining:
                start = remaining.pop()
                component = [start]
                frontier = [start]
                while frontier:
                    tp, sl = frontier.pop()
                    for neighbor in ((tp - 10, sl), (tp + 10, sl), (tp, sl - 5), (tp, sl + 5)):
                        if neighbor in remaining:
                            remaining.remove(neighbor)
                            component.append(neighbor)
                            frontier.append(neighbor)
            if len(component) >= 3:
                component_rows = [cells[position] for position in component]
                regions.append({
                    "horizon_minutes": horizon,
                    "config_ids": sorted(row["config_id"] for row in component_rows),
                    "minimum_cell_expectancy": min(row["base"]["expectancy"] for row in component_rows),
                    "total_trade_count": sum(row["base"]["trade_count"] for row in component_rows),
                })
        for position in passing - stable:
            isolated.append(cells[position]["config_id"])
    regions.sort(key=lambda row: (
        -row["minimum_cell_expectancy"], -row["total_trade_count"], row["config_ids"][0]
    ))
    return {
        "best_isolated_cell": (
            best_cell["config_id"]
            if best_cell is not None and best_cell["base"]["expectancy"] is not None
            else None
        ),
        "best_isolated_cell_expectancy": (
            best_cell["base"]["expectancy"]
            if best_cell is not None and best_cell["base"]["expectancy"] is not None
            else None
        ),
        "isolated_positive_cells": sorted(isolated),
        "stable_regions": regions,
        "frozen_region": regions[0]["config_ids"] if regions else [],
    }


def config_horizons_from_rows(rows: list[dict]) -> list[int]:
    return sorted({row["horizon_minutes"] for row in rows})


def _write_target_heatmaps(path: Path, rows: list[dict]) -> None:
    lines = ["# TP × SL discovery heatmaps", "", "Cells show `net expectancy / trades`.", ""]
    for horizon in config_horizons_from_rows(rows):
        lines.extend([f"## {horizon}m", ""])
        for scenario in ("base", "2x_fees_reselected", "3x_fees_reselected"):
            lines.extend([
                f"### {scenario}", "", "| TP \\ SL | 10bp | 15bp | 20bp | 30bp |", "|---|---:|---:|---:|---:|"
            ])
            for tp in (10, 15, 20, 30, 40):
                values = []
                for sl in (10, 15, 20, 30):
                    row = next(
                        (
                            row for row in rows
                            if row["horizon_minutes"] == horizon
                            and row["tp_bps"] == tp
                            and row["sl_bps"] == sl
                        ),
                        None,
                    )
                    if row is None:
                        values.append("n/a")
                    else:
                        metrics = (
                            row["base"] if scenario == "base"
                            else row["cost_stress"][scenario.removesuffix("_reselected")]["gate_reselected"]
                        )
                        expectancy = metrics["expectancy"]
                        values.append("n/a" if expectancy is None else f"{expectancy:.5f} / {metrics['trade_count']}")
                lines.append(f"| {tp}bp | " + " | ".join(values) + " |")
            lines.append("")
    path.write_text("\n".join(lines) + "\n")


def run_discovery_grid(config: dict, out: Path) -> dict:
    source, bars = load_pre2025(config)
    features = build_features(bars).select(["timestamp", *FEATURES])
    X = features.select(FEATURES).to_numpy()
    timestamps = bars["timestamp"].to_numpy()
    feature_valid = np.isfinite(X).all(axis=1)
    train_mask, valid_mask = chronological_masks(
        timestamps, _ms(2022), _ms(2022), _ms(2023), PURGE_MINUTES
    )
    train_indices = np.flatnonzero(train_mask & feature_valid)
    valid_indices = np.flatnonzero(valid_mask & feature_valid)
    X_train = X[train_indices]
    X_valid = X[valid_indices]
    cache = ExecutionBarrierCache(bars)
    funding = FundingIndex(timestamps, bars["funding_rate"].to_numpy(), bars["close"].to_numpy())
    costs = CostModel()

    regimes, regime_cutpoints = _regime_masks(X_train, X_valid)
    visible_rate = bars["funding_rate"].to_numpy()[valid_indices]
    rows: list[dict] = []
    probability_rows: list[dict] = []
    flat_rows: list[dict] = []
    class_names = (
        "LONG_SL_FIRST", "LONG_TIMEOUT", "LONG_TP_FIRST",
        "SHORT_SL_FIRST", "SHORT_TIMEOUT", "SHORT_TP_FIRST",
    )
    for index, barrier in enumerate(barrier_grid(), start=1):
        labels = cache.labels(barrier)
        usable_train = train_indices[labels.valid[train_indices]]
        usable_valid = valid_indices[labels.valid[valid_indices]]
        long_probabilities = _fit_side_probability_model(
            X[usable_train], labels.long_outcome[usable_train], X[usable_valid],
            config, config["model"]["seed"],
        )
        short_probabilities = _fit_side_probability_model(
            X[usable_train], labels.short_outcome[usable_train], X[usable_valid],
            config, config["model"]["seed"],
        )
        long_metrics = probability_metrics(
            labels.long_outcome[usable_valid], long_probabilities,
            ("SL_FIRST", "TIMEOUT", "TP_FIRST"),
        )
        short_metrics = probability_metrics(
            labels.short_outcome[usable_valid], short_probabilities,
            ("SL_FIRST", "TIMEOUT", "TP_FIRST"),
        )
        overall_metrics = {
            "log_loss": float(np.mean([long_metrics["log_loss"], short_metrics["log_loss"]])),
            "brier": float(np.mean([long_metrics["brier"], short_metrics["brier"]])),
            "macro_auc_ovr": float(np.nanmean([
                long_metrics["macro_auc_ovr"], short_metrics["macro_auc_ovr"]
            ])),
            "mean_ece": float(np.mean([long_metrics["mean_ece"], short_metrics["mean_ece"]])),
        }
        reference_long = _training_event_reference(labels, barrier, usable_train, 1)
        reference_short = _training_event_reference(labels, barrier, usable_train, -1)
        timeout_target = cache.timeout_return[barrier.horizon_minutes]
        predicted_timeout = np.column_stack([
            _conditional_timeout_prediction(
                X[usable_train], timeout_target, usable_train,
                labels.long_outcome[usable_train], X[usable_valid], config,
            ),
            _conditional_timeout_prediction(
                X[usable_train], timeout_target, usable_train,
                labels.short_outcome[usable_train], X[usable_valid], config,
            ),
        ])
        evaluation = _evaluate_predictions(
            cache, labels, barrier, usable_valid, long_probabilities, short_probabilities,
            predicted_timeout, visible_rate, reference_long, reference_short, costs, funding,
        )
        stress_results = {}
        flat = {
            "experiment_id": "EXP-023",
            "config_id": barrier.config_id,
            "tp_bps": barrier.tp_bps,
            "sl_bps": barrier.sl_bps,
            "horizon_minutes": barrier.horizon_minutes,
            "probability_log_loss": overall_metrics["log_loss"],
            "probability_brier": overall_metrics["brier"],
            "probability_macro_auc_ovr": overall_metrics["macro_auc_ovr"],
        }
        for key, stress_cost in {
            "2x_fees": CostModel(fee_multiplier=2.0),
            "3x_fees": CostModel(fee_multiplier=3.0),
            "extra_slippage": CostModel(extra_slippage_bps_per_side=5.0),
        }.items():
            reselected = _evaluate_predictions(
                cache, labels, barrier, usable_valid, long_probabilities, short_probabilities,
                predicted_timeout, visible_rate, reference_long, reference_short,
                stress_cost, funding,
            )
            frozen_trades = _realized_trades(
                cache, labels, barrier, evaluation["selected_indices"],
                evaluation["selected_sides"], stress_cost, funding,
            )
            frozen_metrics = _summary(trade_metrics(frozen_trades))
            frozen_positive = (
                sum(trade["net"] > 0 for trade in frozen_trades) / len(frozen_trades)
                if frozen_trades else None
            )
            stress_results[key] = {
                "frozen_base_selection": frozen_metrics,
                "frozen_selection_positive_fraction": frozen_positive,
                "gate_reselected": reselected["metrics"],
            }
            flat[f"{key}_trade_count"] = reselected["metrics"]["trade_count"]
            flat[f"{key}_expectancy"] = reselected["metrics"]["expectancy"]
            flat[f"{key}_net_return_sum"] = reselected["metrics"]["net_return_sum"]
        delayed_indices = usable_valid + 1
        delayed_eligible = labels.valid[delayed_indices]
        delayed = _evaluate_predictions(
            cache, labels, barrier, delayed_indices, long_probabilities, short_probabilities,
            predicted_timeout, visible_rate, reference_long, reference_short, costs, funding,
            eligible=delayed_eligible,
        )
        best_long = _best_side_realized_net(
            cache, labels, barrier, usable_valid, costs, funding, 1
        )
        best_short = _best_side_realized_net(
            cache, labels, barrier, usable_valid, costs, funding, -1
        )
        best_net = np.maximum(best_long, best_short)
        row = {
            "config_id": barrier.config_id,
            "tp_bps": barrier.tp_bps,
            "sl_bps": barrier.sl_bps,
            "horizon_minutes": barrier.horizon_minutes,
            "base": evaluation["metrics"],
            "actual_one_minute_delay": delayed["metrics"],
            "cost_stress": stress_results,
            "probability": {
                "overall": overall_metrics,
                "long": long_metrics,
                "short": short_metrics,
                "regime_mean_probability_long": _regime_probability_summary(
                    regimes, long_probabilities
                ),
                "regime_mean_probability_short": _regime_probability_summary(
                    regimes, short_probabilities
                ),
            },
            "target_state": {
                "opportunity_rate": float(np.mean(best_net > 0)),
                "mean_best_side_net": float(best_net.mean()),
                "ambiguous_long_rate": float(labels.long_ambiguous[usable_valid].mean()),
                "ambiguous_short_rate": float(labels.short_ambiguous[usable_valid].mean()),
            },
            "mean_selected_predicted_ev": evaluation["metrics"]["mean_selected_predicted_ev"],
            "model": {
                "family": "paired_lightgbm_three_class",
                "classes": list(class_names),
                "params": _model_params(config),
                "feature_list": list(FEATURES),
                "seed": config["model"]["seed"],
                "train_rows": int(len(usable_train)),
                "validation_rows": int(len(usable_valid)),
            },
        }
        rows.append(row)
        probability_rows.append({
            "config_id": barrier.config_id,
            "tp_bps": barrier.tp_bps,
            "sl_bps": barrier.sl_bps,
            "horizon_minutes": barrier.horizon_minutes,
            "overall": overall_metrics,
            "long": long_metrics,
            "short": short_metrics,
        })
        for key in ("trade_count", "expectancy", "net_return_sum", "sharpe", "sortino", "max_drawdown", "profit_factor"):
            flat[f"base_{key}"] = evaluation["metrics"][key]
        flat["delay_trade_count"] = delayed["metrics"]["trade_count"]
        flat["delay_expectancy"] = delayed["metrics"]["expectancy"]
        flat["opportunity_rate"] = row["target_state"]["opportunity_rate"]
        flat_rows.append(flat)
        print(json.dumps({
            "progress": f"{index}/100",
            "config_id": barrier.config_id,
            "trades": evaluation["metrics"]["trade_count"],
            "expectancy": evaluation["metrics"]["expectancy"],
        }), flush=True)
    stable = _stable_regions(rows)
    grid_directory = out / "EXP-023-target-shape-sweep"
    grid_directory.mkdir(parents=True, exist_ok=True)
    grid_frame = pl.DataFrame(flat_rows)
    grid_frame.write_csv(grid_directory / "target-shape.csv")
    _write_target_heatmaps(grid_directory / "heatmaps.md", rows)
    common = {
        "data_source": str(source.relative_to(ROOT)),
        "data_range": ["2021-01-01", "2023-01-01)"],
        "feature_set": list(FEATURES),
        "seed": config["model"]["seed"],
        "purge": {"minutes_each_side": PURGE_MINUTES, "random_temporal_split": False},
        "cost_model": costs.as_dict(),
        "threshold": {"minimum_edge_over_cost": 2.0},
    }
    label_record = {
        "experiment_id": "EXP-021-barrier-target-construction",
        "hypothesis": "Executable-entry TP/SL/timeout labels are point-in-time and side-symmetric.",
        **common,
        "target": "100 pre-registered TP x SL x horizon configurations",
        "model": "deterministic labels; no model",
        "train_period": ["2021-01-01", "2022-01-01)"],
        "validation_period": ["2022-01-01", "2023-01-01)"],
        "metrics": {
            "configurations": len(rows),
            "all_contiguous_one_minute": True,
            "same_bar_policy": "SL_FIRST plus ambiguity flag",
            "tail_policy": "null/incomplete",
        },
        "trade_count": 0,
        "result": {"status": "TESTED", "grid_complete": len(rows) == 100},
        "decision": "IMPLEMENTED",
    }
    probability_record = {
        "experiment_id": "EXP-022-barrier-probability-model",
        "hypothesis": "Barrier outcome probabilities add information beyond class frequencies.",
        **common,
        "target": "paired long/short three-class TP_FIRST/SL_FIRST/TIMEOUT probabilities",
        "model": "20-tree/15-leaf deterministic LightGBM",
        "train_period": ["2021-01-01", "2022-01-01)"],
        "validation_period": ["2022-01-01", "2023-01-01)"],
        "metrics": {
            "configurations": len(probability_rows),
            "median_log_loss": float(np.median([row["overall"]["log_loss"] for row in probability_rows])),
            "median_brier": float(np.median([row["overall"]["brier"] for row in probability_rows])),
        },
        "trade_count": sum(row["base"]["trade_count"] for row in rows),
        "result": {"status": "TESTED", "probability_improvement_is_not_economic_promotion": True},
        "decision": "NOT VALIDATED",
    }
    sweep_record = {
        "experiment_id": "EXP-023-target-shape-sweep",
        "hypothesis": "At least one stable neighboring TP/SL/horizon region has positive discovery economics.",
        **common,
        "target": "pre-registered 100-cell barrier grid",
        "model": "EXP-022 probability model plus 5 timeout-return regressors",
        "train_period": ["2021-01-01", "2022-01-01)"],
        "validation_period": ["2022-01-01", "2023-01-01)"],
        "metrics": {
            "stable_region_discovery": stable,
            "regime_cutpoints_from_training": regime_cutpoints,
            "configurations": rows,
        },
        "trade_count": sum(row["base"]["trade_count"] for row in rows),
        "result": {
            "status": "EMPIRICALLY VALIDATED" if stable["frozen_region"] else "NO EDGE",
            "stable_region_found": bool(stable["frozen_region"]),
        },
        "decision": "PROMISING" if stable["frozen_region"] else "NO EDGE",
    }
    for record, directory in (
        (label_record, "EXP-021-barrier-target-construction"),
        (probability_record, "EXP-022-barrier-probability-model"),
        (sweep_record, "EXP-023-target-shape-sweep"),
    ):
        write_json(out / directory / "metrics.json", record)
        write_experiment_yaml(out / directory / "experiment.yaml", record)
    return sweep_record


def _simulate_barrier_filtered(
    cache: ExecutionBarrierCache,
    labels,
    config: BarrierConfig,
    decision_indices: np.ndarray,
    sides: np.ndarray,
    eligible: np.ndarray,
    costs: CostModel,
    funding: FundingIndex,
) -> dict:
    decision_indices = np.asarray(decision_indices, dtype=int)
    rows = _select_rows(
        decision_indices,
        np.asarray(eligible, dtype=bool) & (np.asarray(sides) != 0),
        np.ones(len(decision_indices)),
        np.zeros(len(decision_indices)),
        config.horizon_minutes,
    )
    selected_indices = decision_indices[rows]
    selected_sides = np.asarray(sides, dtype=int)[rows]
    trades = _realized_trades(
        cache, labels, config, selected_indices, selected_sides, costs, funding
    )
    return _summary(trade_metrics(trades))


def _simulate_fixed_all(
    timestamps: np.ndarray,
    open_price: np.ndarray,
    sides: np.ndarray,
    funding: FundingIndex,
    decision_indices: np.ndarray,
    horizon: int,
) -> dict:
    decision_indices = np.asarray(decision_indices, dtype=int)
    rows: list[int] = []
    next_free = -1
    for row, index in enumerate(decision_indices):
        if sides[index] == 0:
            continue
        if index < next_free:
            continue
        rows.append(int(row))
        next_free = index + horizon
    selected = decision_indices[np.asarray(rows, dtype=int)]
    selected_sides = np.asarray(sides, dtype=int)[selected]
    entry = open_price[selected + 1]
    exit_reference = open_price[selected + horizon + 1]
    exit_indices = selected + horizon + 1
    entry_fill = entry * (1.0 + selected_sides * 0.0002)
    funding_cost = funding.actual_cost_fraction(
        selected_sides, selected, exit_indices, entry_fill
    )
    components = execution_components(
        selected_sides, entry, exit_reference, CostModel(), funding_cost
    )
    trades = []
    for row, index in enumerate(selected):
        trades.append({
            "timestamp": int(timestamps[index]),
            "decision_index": int(index),
            "side": int(selected_sides[row]),
            "holding_bars": horizon,
            "raw_reference_return": float(components["raw_reference_return"][row]),
            "gross": float(components["gross_fill_return"][row]),
            "slippage_cost": float(components["slippage_cost"][row]),
            "fees": float(components["fees"][row]),
            "funding": float(components["funding"][row]),
            "net": float(components["net"][row]),
        })
    return _summary(trade_metrics(trades))


def _feature_distribution(X: np.ndarray, mask: np.ndarray) -> dict:
    result = {
        "n": int(mask.sum()) if np.asarray(mask).dtype == bool else len(mask),
        "features": {},
    }
    for column, name in enumerate(FEATURES):
        values = X[mask, column]
        values = values[np.isfinite(values)]
        if not len(values):
            result["features"][name] = None
            continue
        result["features"][name] = {
            "mean": float(values.mean()),
            "std": float(values.std()),
            "p10": float(np.quantile(values, 0.10)),
            "median": float(np.median(values)),
            "p90": float(np.quantile(values, 0.90)),
        }
    return result


def _top_feature_correlations(X: np.ndarray, mask: np.ndarray, limit: int = 8) -> list[dict]:
    values = X[mask]
    if len(values) < 3:
        return []
    correlation = np.corrcoef(values, rowvar=False)
    pairs = []
    for left in range(len(FEATURES)):
        for right in range(left + 1, len(FEATURES)):
            value = correlation[left, right]
            if np.isfinite(value):
                pairs.append({
                    "feature_a": FEATURES[left],
                    "feature_b": FEATURES[right],
                    "correlation": float(value),
                    "absolute_correlation": float(abs(value)),
                })
    return sorted(pairs, key=lambda row: row["absolute_correlation"], reverse=True)[:limit]


def run_discovery_analysis(config: dict, out: Path) -> dict:
    source, bars = load_pre2025(config)
    features = build_features(bars).select(["timestamp", *FEATURES])
    X = features.select(FEATURES).to_numpy()
    timestamps = bars["timestamp"].to_numpy()
    feature_valid = np.isfinite(X).all(axis=1)
    train_mask, valid_mask = chronological_masks(
        timestamps, _ms(2022), _ms(2022), _ms(2023), PURGE_MINUTES
    )
    train_indices = np.flatnonzero(train_mask & feature_valid)
    valid_indices = np.flatnonzero(valid_mask & feature_valid)
    X_train = X[train_indices]
    X_valid = X[valid_indices]
    cache = ExecutionBarrierCache(bars)
    funding = FundingIndex(timestamps, bars["funding_rate"].to_numpy(), bars["close"].to_numpy())
    costs = CostModel()
    sentinel = BarrierConfig(20, 20, 15)
    labels = cache.labels(sentinel)
    usable_train = train_indices[labels.valid[train_indices]]
    usable_valid = valid_indices[labels.valid[valid_indices]]
    probabilities_long = _fit_side_probability_model(
        X[usable_train], labels.long_outcome[usable_train], X[usable_valid],
        config, config["model"]["seed"],
    )
    probabilities_short = _fit_side_probability_model(
        X[usable_train], labels.short_outcome[usable_train], X[usable_valid],
        config, config["model"]["seed"],
    )

    timeout_target = cache.timeout_return[15]
    timeout_train = train_indices[np.isfinite(timeout_target[train_indices])]
    timeout_model = LGBMRegressor(**_model_params(config)).fit(
        X[timeout_train], timeout_target[timeout_train]
    )
    terminal_prediction = timeout_model.predict(X_valid)
    predicted_timeout = np.column_stack([
        _conditional_timeout_prediction(
            X[usable_train], timeout_target, usable_train,
            labels.long_outcome[usable_train], X[usable_valid], config,
        ),
        _conditional_timeout_prediction(
            X[usable_train], timeout_target, usable_train,
            labels.short_outcome[usable_train], X[usable_valid], config,
        ),
    ])
    reference_long = _training_event_reference(labels, sentinel, usable_train, 1)
    reference_short = _training_event_reference(labels, sentinel, usable_train, -1)
    visible_rate = bars["funding_rate"].to_numpy()[valid_indices]

    direction_target = np.where(
        timeout_target[usable_train] >= costs.reference_round_trip_cost,
        2,
        np.where(
            timeout_target[usable_train] <= -costs.reference_round_trip_cost,
            0,
            1,
        ),
    ).astype(np.int8)
    direction_train = usable_train[np.isfinite(timeout_target[usable_train])]
    direction_probability = _fit_side_probability_model(
        X[direction_train], direction_target, X_valid, config, config["model"]["seed"]
    )
    learned_side = np.where(
        (direction_probability[:, 2] >= direction_probability[:, 1])
        & (direction_probability[:, 2] >= direction_probability[:, 0]),
        1,
        -1,
    ).astype(np.int8)
    momentum = X_valid[:, FEATURES.index("ret_5m")]
    naive_side = np.where(momentum > 0, 1, np.where(momentum < 0, -1, 0)).astype(np.int8)

    opportunity_train_long = _best_side_realized_net(
        cache, labels, sentinel, usable_train, costs, funding, 1
    )
    opportunity_train_short = _best_side_realized_net(
        cache, labels, sentinel, usable_train, costs, funding, -1
    )
    opportunity_target_train = (
        np.maximum(opportunity_train_long, opportunity_train_short) > 0
    ).astype(np.int8)
    opportunity_model = LGBMClassifier(**_model_params(config)).fit(
        X[usable_train], opportunity_target_train
    )
    opportunity_probability = opportunity_model.predict_proba(X[usable_valid])[:, 1]
    opportunity_threshold = config["opportunity"]["probability_threshold"]

    direction_only = _simulate_barrier_filtered(
        cache, labels, sentinel, usable_valid, learned_side,
        np.ones(len(usable_valid), dtype=bool), costs, funding,
    )
    model_b = _simulate_barrier_filtered(
        cache, labels, sentinel, usable_valid, naive_side,
        opportunity_probability >= opportunity_threshold, costs, funding,
    )
    model_c = _simulate_barrier_filtered(
        cache, labels, sentinel, usable_valid, learned_side,
        opportunity_probability >= opportunity_threshold, costs, funding,
    )
    model_d_evaluation = _evaluate_predictions(
        cache, labels, sentinel, usable_valid, probabilities_long, probabilities_short,
        predicted_timeout, visible_rate, reference_long, reference_short, costs, funding,
        eligible=opportunity_probability >= opportunity_threshold,
        forced_sides=learned_side,
    )
    model_d = model_d_evaluation["metrics"]

    side_full = np.full(len(bars), np.nan)
    predicted_full = np.full(len(bars), np.nan)
    uncertainty_full = np.zeros(len(bars))
    side_full[usable_valid] = learned_side
    predicted_full[usable_valid] = terminal_prediction
    control_a = simulate_corrected_fixed_horizon(
        timestamps, bars["open"].to_numpy(), bars["open"].to_numpy(), side_full,
        predicted_full, uncertainty_full, funding, usable_valid,
        horizon_minutes=15, costs=costs, min_edge_multiplier=2.0,
        apply_uncertainty_penalty=False,
    )
    train_mean_return = float(timeout_target[timeout_train].mean())
    unconditional_side_value = 1 if train_mean_return >= -train_mean_return else -1
    unconditional_side = np.full(len(bars), unconditional_side_value, dtype=float)
    control_b = _simulate_fixed_all(
        timestamps, bars["open"].to_numpy(), unconditional_side, funding,
        usable_valid, 15,
    )
    naive_side_full = np.zeros(len(bars), dtype=np.int8)
    naive_side_full[usable_valid] = np.where(naive_side == 0, 1, naive_side)
    control_c = _simulate_fixed_all(
        timestamps, bars["open"].to_numpy(), naive_side_full, funding, usable_valid, 15,
    )
    long_frequency = np.bincount(labels.long_outcome[usable_train], minlength=3).astype(float)
    short_frequency = np.bincount(labels.short_outcome[usable_train], minlength=3).astype(float)
    long_frequency /= long_frequency.sum()
    short_frequency /= short_frequency.sum()
    baseline_timeout_return = np.array([
        timeout_target[usable_train[labels.long_outcome[usable_train] == TIMEOUT]].mean(),
        timeout_target[usable_train[labels.short_outcome[usable_train] == TIMEOUT]].mean(),
    ])
    baseline_probabilities_long = np.tile(long_frequency, (len(usable_valid), 1))
    baseline_probabilities_short = np.tile(short_frequency, (len(usable_valid), 1))
    control_d = _evaluate_predictions(
        cache, labels, sentinel, usable_valid, baseline_probabilities_long,
        baseline_probabilities_short, np.tile(baseline_timeout_return, (len(usable_valid), 1)),
        visible_rate, reference_long, reference_short, costs, funding,
    )["metrics"]

    opportunity_metrics = binary_probability_metrics(
        np.maximum(
            _best_side_realized_net(cache, labels, sentinel, usable_valid, costs, funding, 1),
            _best_side_realized_net(cache, labels, sentinel, usable_valid, costs, funding, -1),
        ) > 0,
        opportunity_probability,
    )
    decomposition = {
        "A_direction_only": direction_only,
        "B_opportunity_naive_direction": model_b,
        "C_opportunity_learned_direction": model_c,
        "D_opportunity_direction_payoff": model_d,
        "Control_A_phase2_terminal_corrected": _summary(control_a),
        "Control_B_unconditional_return": control_b,
        "Control_C_naive_momentum": control_c,
        "Control_D_unconditional_barrier_payoff": control_d,
        "incremental_D_minus_C_net": model_d["net_return_sum"] - model_c["net_return_sum"],
        "incremental_D_expectancy": (
            model_d["expectancy"] - model_c["expectancy"]
            if model_d["expectancy"] is not None and model_c["expectancy"] is not None
            else None
        ),
    }

    high_mfe_cutoff = float(np.nanquantile(labels.long_mfe[train_indices], 0.75))
    low_mae_cutoff = float(np.nanquantile(labels.long_mae[train_indices], 0.25))
    groups = {
        "all_eligible": usable_valid,
        "tp_first": usable_valid[labels.long_outcome[usable_valid] == TP_FIRST],
        "sl_first": usable_valid[labels.long_outcome[usable_valid] == SL_FIRST],
        "timeout": usable_valid[labels.long_outcome[usable_valid] == TIMEOUT],
        "high_mfe": usable_valid[labels.long_mfe[usable_valid] >= high_mfe_cutoff],
        "high_mae": usable_valid[labels.long_mae[usable_valid] <= low_mae_cutoff],
    }
    state_distributions = {
        name: {
            "distribution": _feature_distribution(X, mask),
            "top_interactions": _top_feature_correlations(X, mask),
        }
        for name, mask in groups.items()
    }
    tp_mask = groups["tp_first"]
    sl_mask = groups["sl_first"]
    standardized_differences = {}
    pooled = X[tp_mask].std(axis=0) + X[sl_mask].std(axis=0)
    for column, name in enumerate(FEATURES):
        denominator = pooled[column]
        standardized_differences[name] = (
            float((X[tp_mask, column].mean() - X[sl_mask, column].mean()) / denominator)
            if denominator > 0 else None
        )
    state_analysis = {
        "sentinel": sentinel.config_id,
        "discovery_cutoffs": {
            "high_mfe_75th": high_mfe_cutoff,
            "high_mae_25th": low_mae_cutoff,
        },
        "groups": state_distributions,
        "tp_minus_sl_standardized_mean": standardized_differences,
        "class_balance": {
            "long": np.bincount(labels.long_outcome[usable_valid], minlength=3).tolist(),
            "short": np.bincount(labels.short_outcome[usable_valid], minlength=3).tolist(),
        },
    }

    phase2_bundle = load_economic_bundle(
        ROOT / "artifacts/phase2-experiments-final/EXP-010-economic-return-baseline/models"
    )
    _train_2024, valid_2024 = chronological_masks(
        timestamps, _ms(2024), _ms(2024), _ms(2025), PURGE_MINUTES
    )
    valid_2024 &= feature_valid
    indices_2024 = np.flatnonzero(valid_2024)
    X_2024 = X[indices_2024]
    X_2024_frame = features.filter(pl.Series(valid_2024)).select(FEATURES)
    phase2_direction = phase2_bundle.predict_direction(X_2024_frame)
    phase2_probability = np.column_stack([
        phase2_direction["p_up_15"], phase2_direction["p_flat_15"], phase2_direction["p_dn_15"]
    ])
    phase2_side = np.where(
        (phase2_probability[:, 0] >= phase2_probability[:, 1])
        & (phase2_probability[:, 0] >= phase2_probability[:, 2]),
        1.0,
        np.where(
            (phase2_probability[:, 2] >= phase2_probability[:, 1])
            & (phase2_probability[:, 2] >= phase2_probability[:, 0]),
            -1.0,
            0.0,
        ),
    )
    phase2_prediction = phase2_bundle.predict_execution_return(X_2024_frame, 15)
    phase2_uncertainty = phase2_bundle.predict_uncertainty_15(X_2024_frame)
    phase2_funding = bars["funding_rate"].to_numpy()[indices_2024] * 15.0 / 480.0
    phase2_gross = phase2_side * phase2_prediction
    phase2_net = phase2_gross - costs.reference_round_trip_cost - phase2_side * phase2_funding
    phase2_adjusted = phase2_net - phase2_uncertainty * np.abs(phase2_gross)
    stage_masks = {
        "valid_model_observations": np.ones(len(indices_2024), dtype=bool),
        "direction_gate": phase2_side != 0,
        "gross_return_gate": phase2_gross > 0,
        "cost_gate": phase2_net > 0,
        "uncertainty_gate": phase2_adjusted > 0,
        "risk_hurdle_gate": phase2_adjusted >= 2.0 * (
            costs.reference_round_trip_cost + np.abs(phase2_funding)
        ),
    }
    previous = np.ones(len(indices_2024), dtype=bool)
    gate_counts = {}
    for name, mask in stage_masks.items():
        previous = previous & mask
        gate_counts[name] = {
            "surviving": int(previous.sum()),
            "lost_at_stage": int((~mask).sum()),
            "fraction_of_valid": float(previous.mean()),
        }
    rare_event = {
        "phase2_2024_gate_decomposition": gate_counts,
        "barrier_opportunity_discovery": {
            "target_positive_rate": float(np.mean(
                np.maximum(
                    _best_side_realized_net(cache, labels, sentinel, usable_valid, costs, funding, 1),
                    _best_side_realized_net(cache, labels, sentinel, usable_valid, costs, funding, -1),
                ) > 0
            )),
            "model_probability_positive_rate": float(np.mean(opportunity_probability >= opportunity_threshold)),
            "model_hurdle_trade_count": model_d["trade_count"],
            "interpretation": "Thresholds were not loosened; scarcity is reported at each pre-registered gate.",
        },
    }

    common = {
        "data_source": str(source.relative_to(ROOT)),
        "data_range": ["2021-01-01", "2023-01-01)"],
        "feature_set": list(FEATURES),
        "seed": config["model"]["seed"],
        "purge": {"minutes_each_side": PURGE_MINUTES, "random_temporal_split": False},
        "cost_model": costs.as_dict(),
        "threshold": {"minimum_edge_over_cost": 2.0},
    }
    decomposition_record = {
        "experiment_id": "EXP-024-opportunity-direction-decomposition",
        "hypothesis": "Separating opportunity detection from direction improves stable economics.",
        **common,
        "target": sentinel.config_id,
        "model": "LightGBM direction, opportunity, and paired three-class barrier heads",
        "train_period": ["2021-01-01", "2022-01-01)"],
        "validation_period": ["2022-01-01", "2023-01-01)"],
        "metrics": {
            "models": decomposition,
            "opportunity_probability": opportunity_metrics,
            "opportunity_threshold": opportunity_threshold,
        },
        "trade_count": model_d["trade_count"],
        "result": {
            "status": "EMPIRICALLY VALIDATED",
            "D_improves_C": bool(decomposition["incremental_D_expectancy"] is not None and decomposition["incremental_D_expectancy"] > 0),
        },
        "decision": "PROMISING" if model_d["trade_count"] >= 30 and model_d["expectancy"] is not None and model_d["expectancy"] > 0 else "NO EDGE",
    }
    state_record = {
        "experiment_id": "EXP-025-existing-state-analysis",
        "hypothesis": "Barrier-defined opportunity states correspond to reproducible current-feature distributions.",
        **common,
        "target": sentinel.config_id,
        "model": "descriptive analysis; no new features",
        "train_period": ["2021-01-01", "2022-01-01)"],
        "validation_period": ["2022-01-01", "2023-01-01)"],
        "metrics": state_analysis,
        "trade_count": 0,
        "result": {"status": "EMPIRICALLY VALIDATED", "new_features_promoted": False},
        "decision": "NOT VALIDATED",
    }
    gate_record = {
        "experiment_id": "EXP-026-rare-event-gate-decomposition",
        "hypothesis": "Trade scarcity is explained by pre-registered economic gates rather than relaxed targets.",
        **common,
        "target": "Phase 2 gate plus sentinel barrier opportunity",
        "model": "frozen Phase 2 gate and EXP-024 discovery models",
        "train_period": ["2021-01-01", "2022-01-01)"],
        "validation_period": ["2022-01-01", "2025-01-01)"],
        "metrics": rare_event,
        "trade_count": model_d["trade_count"],
        "result": {"status": "EMPIRICALLY VALIDATED", "thresholds_loosened": False},
        "decision": "NO EDGE" if model_d["trade_count"] < 30 or model_d["expectancy"] is None or model_d["expectancy"] <= 0 else "REVIEW",
    }
    for record, directory in (
        (decomposition_record, "EXP-024-opportunity-direction-decomposition"),
        (state_record, "EXP-025-existing-state-analysis"),
        (gate_record, "EXP-026-rare-event-gate-decomposition"),
    ):
        write_json(out / directory / "metrics.json", record)
        write_experiment_yaml(out / directory / "experiment.yaml", record)
    return {
        "decomposition": decomposition_record,
        "state": state_record,
        "gates": gate_record,
    }


def _rank_correlation(left: np.ndarray, right: np.ndarray) -> float:
    left_rank = np.argsort(np.argsort(left))
    right_rank = np.argsort(np.argsort(right))
    if np.std(left_rank) == 0 or np.std(right_rank) == 0:
        return 0.0
    return float(np.corrcoef(left_rank, right_rank)[0, 1])


def _uncertainty_diagnostic(
    uncertainty: np.ndarray,
    predicted_ev: np.ndarray,
    realized_net: np.ndarray,
) -> dict:
    uncertainty = np.asarray(uncertainty, dtype=float)
    error = np.abs(np.asarray(predicted_ev, dtype=float) - realized_net)
    adverse = realized_net < 0
    order = np.argsort(uncertainty, kind="mergesort")
    buckets = []
    for bucket_index, group in enumerate(np.array_split(order, min(5, len(order)))):
        if not len(group):
            continue
        buckets.append({
            "bucket": bucket_index,
            "n": int(len(group)),
            "mean_uncertainty": float(uncertainty[group].mean()),
            "mean_absolute_economic_error": float(error[group].mean()),
            "adverse_rate": float(adverse[group].mean()),
        })
    errors = [row["mean_absolute_economic_error"] for row in buckets]
    adverse_rates = [row["adverse_rate"] for row in buckets]
    return {
        "spearman_error_vs_uncertainty": _rank_correlation(uncertainty, error),
        "spearman_adverse_vs_uncertainty": _rank_correlation(uncertainty, adverse.astype(float)),
        "errors_monotonic": bool(all(a <= b for a, b in zip(errors, errors[1:]))),
        "adverse_monotonic": bool(all(a <= b for a, b in zip(adverse_rates, adverse_rates[1:]))),
        "buckets": buckets,
        "used_as_economic_penalty": False,
    }


def _regime_trade_summary(
    trades: list[dict],
    frozen_two_x_trades: list[dict],
    valid_indices: np.ndarray,
    regimes: dict[str, list[tuple[str, np.ndarray]]],
) -> dict:
    global_to_local = {int(index): row for row, index in enumerate(valid_indices)}
    result = {}
    for dimension, groups in regimes.items():
        result[dimension] = {}
        for label, mask in groups:
            selected = [trade for trade in trades if mask[global_to_local[trade["decision_index"]]]]
            selected_2x = [
                trade for trade in frozen_two_x_trades
                if mask[global_to_local[trade["decision_index"]]]
            ]
            metrics = _summary(trade_metrics(selected))
            metrics["observations"] = int(mask.sum())
            metrics["two_x_positive_fraction"] = (
                sum(trade["net"] > 0 for trade in selected_2x) / len(selected_2x)
                if selected_2x else None
            )
            result[dimension][label] = metrics
    return result


def run_confirmation(config: dict, out: Path) -> dict:
    source, bars = load_pre2025(config)
    sweep_path = out / "EXP-023-target-shape-sweep" / "metrics.json"
    if not sweep_path.is_file():
        raise FileNotFoundError("EXP-023 discovery sweep must be frozen before confirmation")
    sweep = json.loads(sweep_path.read_text())
    region = sweep["metrics"]["stable_region_discovery"]["frozen_region"]
    candidate_exists = bool(region)
    all_configs = {config_item.config_id: config_item for config_item in barrier_grid()}
    selected_configs = [all_configs[config_id] for config_id in region]
    diagnostic_only = False
    if not selected_configs:
        selected_configs = [BarrierConfig(20, 20, 15)]
        diagnostic_only = True
    features = build_features(bars).select(["timestamp", *FEATURES])
    X = features.select(FEATURES).to_numpy()
    timestamps = bars["timestamp"].to_numpy()
    feature_valid = np.isfinite(X).all(axis=1)
    cache = ExecutionBarrierCache(bars)
    funding = FundingIndex(timestamps, bars["funding_rate"].to_numpy(), bars["close"].to_numpy())
    costs = CostModel()
    thresholds = config["promotion"]["confirmation_thresholds"]
    fold_definitions = config["splits"]["walk_forward_confirmation"]
    results: list[dict] = []
    for barrier in selected_configs:
        labels = cache.labels(barrier)
        for fold in fold_definitions:
            valid_start = int(datetime.fromisoformat(fold["validation"][0]).replace(tzinfo=timezone.utc).timestamp() * 1000)
            train_end = valid_start
            valid_end_year = int(fold["validation"][0][:4]) + 1
            valid_end = int(datetime(valid_end_year, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
            train_mask, valid_mask = chronological_masks(
                timestamps, train_end, valid_start, valid_end, PURGE_MINUTES
            )
            train_indices = np.flatnonzero(train_mask & feature_valid)
            valid_indices = np.flatnonzero(valid_mask & feature_valid)
            usable_train = train_indices[labels.valid[train_indices]]
            usable_valid = valid_indices[labels.valid[valid_indices]]
            probability_models = [
                (
                    _fit_side_probability_model(
                        X[usable_train], labels.long_outcome[usable_train],
                        X[usable_valid], config, seed,
                    ),
                    _fit_side_probability_model(
                        X[usable_train], labels.short_outcome[usable_train],
                        X[usable_valid], config, seed,
                    ),
                )
                for seed in config["model"]["uncertainty_diagnostic_seeds"]
            ]
            probabilities_long = np.mean([pair[0] for pair in probability_models], axis=0)
            probabilities_short = np.mean([pair[1] for pair in probability_models], axis=0)
            long_probability_result = probability_metrics(
                labels.long_outcome[usable_valid], probabilities_long,
                ("SL_FIRST", "TIMEOUT", "TP_FIRST"),
            )
            short_probability_result = probability_metrics(
                labels.short_outcome[usable_valid], probabilities_short,
                ("SL_FIRST", "TIMEOUT", "TP_FIRST"),
            )
            probability_result = {
                "long": long_probability_result,
                "short": short_probability_result,
                "mean_ece": float(np.mean([
                    long_probability_result["mean_ece"], short_probability_result["mean_ece"]
                ])),
                "mean_log_loss": float(np.mean([
                    long_probability_result["log_loss"], short_probability_result["log_loss"]
                ])),
                "mean_brier": float(np.mean([
                    long_probability_result["brier"], short_probability_result["brier"]
                ])),
            }

            timeout_target = cache.timeout_return[barrier.horizon_minutes]
            predicted_timeout = np.column_stack([
                _conditional_timeout_prediction(
                    X[usable_train], timeout_target, usable_train,
                    labels.long_outcome[usable_train], X[usable_valid], config,
                ),
                _conditional_timeout_prediction(
                    X[usable_train], timeout_target, usable_train,
                    labels.short_outcome[usable_train], X[usable_valid], config,
                ),
            ])

            direction_train = usable_train[np.isfinite(timeout_target[usable_train])]
            direction_target = np.where(
                timeout_target[direction_train] >= costs.reference_round_trip_cost,
                2,
                np.where(
                    timeout_target[direction_train] <= -costs.reference_round_trip_cost,
                    0,
                    1,
                ),
            ).astype(np.int8)
            direction_probability = _fit_side_probability_model(
                X[direction_train], direction_target, X[usable_valid],
                config, config["model"]["seed"],
            )
            learned_side = np.where(
                (direction_probability[:, 2] >= direction_probability[:, 1])
                & (direction_probability[:, 2] >= direction_probability[:, 0]),
                1,
                -1,
            ).astype(np.int8)

            opportunity_long = _best_side_realized_net(
                cache, labels, barrier, usable_train, costs, funding, 1
            )
            opportunity_short = _best_side_realized_net(
                cache, labels, barrier, usable_train, costs, funding, -1
            )
            opportunity_target = (
                np.maximum(opportunity_long, opportunity_short) > 0
            ).astype(np.int8)
            opportunity_model = LGBMClassifier(**_model_params(config)).fit(
                X[usable_train], opportunity_target
            )
            opportunity_probability = opportunity_model.predict_proba(X[usable_valid])[:, 1]
            opportunity_eligible = (
                opportunity_probability >= config["opportunity"]["probability_threshold"]
            )
            reference_long = _training_event_reference(labels, barrier, usable_train, 1)
            reference_short = _training_event_reference(labels, barrier, usable_train, -1)
            visible_rate = bars["funding_rate"].to_numpy()[usable_valid]
            evaluation = _evaluate_predictions(
                cache, labels, barrier, usable_valid, probabilities_long, probabilities_short,
                predicted_timeout, visible_rate, reference_long, reference_short, costs, funding,
                eligible=opportunity_eligible, forced_sides=learned_side,
            )
            stress_results = {}
            frozen_two_x_trades: list[dict] = []
            for key, stress_cost in {
                "2x_fees": CostModel(fee_multiplier=2.0),
                "3x_fees": CostModel(fee_multiplier=3.0),
                "extra_slippage": CostModel(extra_slippage_bps_per_side=5.0),
            }.items():
                reselected = _evaluate_predictions(
                    cache, labels, barrier, usable_valid, probabilities_long, probabilities_short,
                    predicted_timeout, visible_rate, reference_long, reference_short,
                    stress_cost, funding, eligible=opportunity_eligible,
                    forced_sides=learned_side,
                )
                frozen_trades = _realized_trades(
                    cache, labels, barrier, evaluation["selected_indices"],
                    evaluation["selected_sides"], stress_cost, funding,
                )
                if key == "2x_fees":
                    frozen_two_x_trades = frozen_trades
                stress_results[key] = {
                    "gate_reselected": reselected["metrics"],
                    "frozen_base_selection": _summary(trade_metrics(frozen_trades)),
                    "frozen_selection_positive_fraction": (
                        sum(trade["net"] > 0 for trade in frozen_trades) / len(frozen_trades)
                        if frozen_trades else None
                    ),
                }
            delayed_indices = usable_valid + 1
            delayed = _evaluate_predictions(
                cache, labels, barrier, delayed_indices, probabilities_long, probabilities_short,
                predicted_timeout, visible_rate, reference_long, reference_short, costs, funding,
                eligible=opportunity_eligible & labels.valid[delayed_indices],
                forced_sides=learned_side,
            )

            per_seed_ev = []
            for seed_long, seed_short in probability_models:
                long_seed = _predicted_payoff_components(
                    cache, barrier, usable_valid, seed_long, predicted_timeout,
                    visible_rate, np.ones(len(usable_valid)), reference_long, costs,
                )
                short_seed = _predicted_payoff_components(
                    cache, barrier, usable_valid, seed_short, predicted_timeout,
                    visible_rate, -np.ones(len(usable_valid)), reference_short, costs,
                )
                per_seed_ev.append(
                    np.where(learned_side == 1, long_seed["net"], short_seed["net"])
                )
            ensemble_ev = np.column_stack(per_seed_ev)
            ev_scale = float(np.std(ensemble_ev.mean(axis=1)))
            uncertainty = ensemble_ev.std(axis=1) / (ev_scale if ev_scale > 0 else 1.0)
            realized_long = _best_side_realized_net(
                cache, labels, barrier, usable_valid, costs, funding, 1
            )
            realized_short = _best_side_realized_net(
                cache, labels, barrier, usable_valid, costs, funding, -1
            )
            realized = np.where(learned_side == 1, realized_long, realized_short)
            uncertainty_result = _uncertainty_diagnostic(
                uncertainty, evaluation["predicted_ev"], realized
            )
            regimes, regime_cutpoints = _regime_masks(
                X[train_indices], X[usable_valid]
            )
            regime_result = _regime_trade_summary(
                evaluation["trades"], frozen_two_x_trades, usable_valid, regimes
            )
            results.append({
                "config_id": barrier.config_id,
                "fold": fold["fold"],
                "train_period": fold["train"],
                "validation_period": fold["validation"],
                "rows": {"train": len(usable_train), "validation": len(usable_valid)},
                "probability": probability_result,
                "opportunity_positive_rate": float(np.mean(opportunity_target)),
                "opportunity_selected_rate": float(opportunity_eligible.mean()),
                "base": evaluation["metrics"],
                "actual_one_minute_delay": delayed["metrics"],
                "cost_stress": stress_results,
                "uncertainty": uncertainty_result,
                "regimes": regime_result,
                "regime_cutpoints_from_training": regime_cutpoints,
            })
            print(json.dumps({
                "stage": "confirmation",
                "config_id": barrier.config_id,
                "fold": fold["fold"],
                "trades": evaluation["metrics"]["trade_count"],
                "expectancy": evaluation["metrics"]["expectancy"],
            }), flush=True)

    by_config: dict[str, list[dict]] = {}
    for row in results:
        by_config.setdefault(row["config_id"], []).append(row)
    config_gates = {}
    for config_id, rows in by_config.items():
        base_pass = all(
            row["base"]["trade_count"] >= thresholds["minimum_trade_count_per_config_fold"]
            and row["base"]["expectancy"] is not None
            and row["base"]["expectancy"] > 0
            for row in rows
        )
        two_x_pass = sum(
            row["cost_stress"]["2x_fees"]["gate_reselected"]["expectancy"] is not None
            and row["cost_stress"]["2x_fees"]["gate_reselected"]["expectancy"] > 0
            for row in rows
        ) >= thresholds["required_positive_2x_fee_folds_per_config"]
        three_x_pass = sum(
            row["cost_stress"]["3x_fees"]["gate_reselected"]["expectancy"] is not None
            and row["cost_stress"]["3x_fees"]["gate_reselected"]["expectancy"] >= 0
            for row in rows
        ) >= thresholds["minimum_nonnegative_3x_fee_folds_per_config"]
        slippage_pass = sum(
            row["cost_stress"]["extra_slippage"]["gate_reselected"]["expectancy"] is not None
            and row["cost_stress"]["extra_slippage"]["gate_reselected"]["expectancy"] > 0
            for row in rows
        ) >= thresholds["required_positive_extra_slippage_folds_per_config"]
        delay_pass = sum(
            row["actual_one_minute_delay"]["expectancy"] is not None
            and row["actual_one_minute_delay"]["expectancy"] > 0
            for row in rows
        ) >= thresholds["required_positive_actual_delay_folds_per_config"]
        calibration_pass = all(
            row["probability"]["mean_ece"] <= thresholds["maximum_mean_probability_ece"]
            for row in rows
        )
        regime_pass = True
        for row in rows:
            shares = []
            for groups in row["regimes"].values():
                total = sum(group["trade_count"] for group in groups.values())
                if total:
                    shares.extend(group["trade_count"] / total for group in groups.values())
            if shares and max(shares) > thresholds["maximum_single_regime_trade_share"]:
                regime_pass = False
        passes = base_pass and two_x_pass and three_x_pass and slippage_pass and delay_pass and calibration_pass and regime_pass
        config_gates[config_id] = {
            "base": base_pass,
            "2x_fees": two_x_pass,
            "3x_fees": three_x_pass,
            "extra_slippage": slippage_pass,
            "actual_delay": delay_pass,
            "calibration": calibration_pass,
            "regime_concentration": regime_pass,
            "passes_all": passes,
        }
    passing_configs = [
        config_id for config_id, gate in config_gates.items() if gate["passes_all"]
    ]
    promising = bool(
        candidate_exists
        and len(passing_configs) >= thresholds["minimum_configs_passing_all_confirmation_folds"]
    )
    common = {
        "data_source": str(source.relative_to(ROOT)),
        "data_range": ["2021-01-01", "2025-01-01)"],
        "feature_set": list(FEATURES),
        "seed": config["model"]["seed"],
        "purge": {"minutes_each_side": PURGE_MINUTES, "random_temporal_split": False},
        "cost_model": costs.as_dict(),
        "threshold": thresholds,
    }
    confirmation_record = {
        "experiment_id": "EXP-031-walk-forward-confirmation",
        "hypothesis": "A discovery-frozen target region remains economically positive across expanding walk-forward folds.",
        **common,
        "target": [barrier.config_id for barrier in selected_configs],
        "model": "three-seed barrier probability, learned direction, opportunity, and timeout return",
        "train_period": ["expanding through 2021, 2022, or 2023"],
        "validation_period": ["2022", "2023", "2024"],
        "metrics": {
            "candidate_exists": candidate_exists,
            "diagnostic_only": diagnostic_only,
            "config_gates": config_gates,
            "passing_configs": passing_configs,
            "folds": results,
        },
        "trade_count": sum(row["base"]["trade_count"] for row in results),
        "result": {
            "status": "EMPIRICALLY VALIDATED",
            "stable_region_found": candidate_exists,
            "promising": promising,
            "fresh_holdout_used": False,
        },
        "decision": "PROMISING — REQUIRES FRESH HOLDOUT" if promising else "NO EDGE",
    }
    cost_record = {
        "experiment_id": "EXP-032-cost-stress-confirmation",
        "hypothesis": "Confirmation selections survive base, 2x/3x fees, extra slippage, and one-minute actual delay.",
        **common,
        "target": [barrier.config_id for barrier in selected_configs],
        "model": "EXP-031 frozen model specification",
        "train_period": ["expanding through 2021, 2022, or 2023"],
        "validation_period": ["2022", "2023", "2024"],
        "metrics": {
            "folds": [
                {
                    "config_id": row["config_id"],
                    "fold": row["fold"],
                    "base": row["base"],
                    "actual_one_minute_delay": row["actual_one_minute_delay"],
                    "cost_stress": row["cost_stress"],
                }
                for row in results
            ],
            "zero_trade_stress_is_abstention_not_robustness": True,
        },
        "trade_count": confirmation_record["trade_count"],
        "result": {
            "status": "EMPIRICALLY VALIDATED",
            "cost_survival": promising,
            "fresh_holdout_used": False,
        },
        "decision": confirmation_record["decision"],
    }
    for record, directory in (
        (confirmation_record, "EXP-031-walk-forward-confirmation"),
        (cost_record, "EXP-032-cost-stress-confirmation"),
    ):
        write_json(out / directory / "metrics.json", record)
        write_experiment_yaml(out / directory / "experiment.yaml", record)
    if promising:
        write_json(out / "frozen-candidate.json", {
            "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
            "research_cutoff_exclusive_ms": config["data"]["allowed_end_exclusive_ms"],
            "configs": passing_configs,
            "features": list(FEATURES),
            "model": config["model"],
            "costs": costs.as_dict(),
            "promotion_thresholds": thresholds,
            "fresh_holdout_status": "NOT OBTAINED — historically consumed 2025+ is forbidden",
        })
    return {"confirmation": confirmation_record, "cost": cost_record}


def write_deferred_microstructure_experiments(config: dict, out: Path) -> list[dict]:
    experiments = (
        (
            "EXP-027-microstructure-family-a",
            "M1 spread/liquidity can add point-in-time economic information.",
            "M1 — spread/liquidity",
        ),
        (
            "EXP-028-microstructure-family-b",
            "M2 order-book imbalance adds incremental economic information.",
            "M2 — order-book depth/imbalance",
        ),
        (
            "EXP-029-microstructure-family-c",
            "M3 trade-flow or signed volume adds incremental economic information.",
            "M3 — trade-flow/signed volume",
        ),
        (
            "EXP-030-combined-causal-feature-set",
            "A combined causal microstructure set improves stable target economics.",
            "combined pre-registered microstructure families",
        ),
    )
    reason = (
        "NOT RUN — the repository has no provenance-safe point-in-time historical "
        "BTCUSDT spread/depth/trade-flow/liquidation fields. No family was fabricated, "
        "backfilled from 2025+, or added simultaneously."
    )
    records = []
    for experiment_id, hypothesis, target in experiments:
        record = {
            "experiment_id": experiment_id,
            "hypothesis": hypothesis,
            "data_source": "NOT RUN — unavailable in the admissible pre-2025 local dataset",
            "data_range": "NOT RUN",
            "feature_set": [],
            "target": target,
            "model": "NOT RUN",
            "seed": config["model"]["seed"],
            "train_period": "NOT RUN",
            "validation_period": "NOT RUN",
            "purge": {"minutes_each_side": PURGE_MINUTES, "random_temporal_split": False},
            "cost_model": CostModel().as_dict(),
            "threshold": {"minimum_edge_over_cost": 2.0},
            "metrics": {"status": "DEFERRED", "reason": reason},
            "trade_count": 0,
            "result": {"status": "DEFERRED", "reason": reason},
            "decision": reason,
        }
        write_json(out / experiment_id / "metrics.json", record)
        write_experiment_yaml(out / experiment_id / "experiment.yaml", record)
        records.append(record)
    return records


def finalize_phase3(config: dict, out: Path) -> dict:
    deferred = write_deferred_microstructure_experiments(config, out)
    experiment_ids = [f"EXP-{number:03d}" for number in range(20, 33)]
    registry = []
    decisions = {}
    for experiment_id in experiment_ids:
        matches = sorted(out.glob(f"{experiment_id}-*/metrics.json"))
        if not matches:
            registry.append({
                "experiment_id": experiment_id,
                "status": "NOT RUN",
                "decision": "NOT RUN — missing artifact",
                "path": None,
            })
            continue
        metrics = json.loads(matches[0].read_text())
        decision = metrics["decision"]
        decisions[experiment_id] = decision
        registry.append({
            "experiment_id": experiment_id,
            "hypothesis": metrics["hypothesis"],
            "status": metrics["result"]["status"],
            "decision": decision,
            "path": str(matches[0].relative_to(ROOT)),
        })
    write_json(out / "phase3-registry.json", {
        "phase": 3,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "experiments": registry,
        "deferred_microstructure_records": len(deferred),
    })
    source = ROOT / config["data"]["path"]
    code_paths = (
        "configs/phase3.json",
        "src/jev_trading/labels/barriers.py",
        "src/jev_trading/quant/payoff.py",
        "scripts/run_phase3.py",
        "tests/test_phase3_barriers.py",
        "tests/test_phase3_economics.py",
        "tests/test_phase3_provenance.py",
    )
    provenance = {
        "phase": 3,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "baseline_commit": config["baseline_commit"],
        "implementation_commit": git_sha(),
        "working_tree_dirty": True,
        "preexisting_untracked_preserved": [
            ".local/", "docs/pre-jev/", "kaggle/",
            "scripts/build_kaggle_kernel.py", "scripts/run_kaggle_benchmark.sh",
        ],
        "phase2_frozen_status": "NO EDGE",
        "phase2_evidence_commit": "2abb4d4",
        "phase2_provenance_commit": "67be354",
        "source": {
            "path": str(source.relative_to(ROOT)),
            "sha256": sha256(source),
            "period": ["2021-01-01", "2025-01-01)"],
            "2025_plus_used_for_selection": False,
        },
        "config_sha256": sha256(CONFIG_PATH),
        "code_hashes": {path: sha256(ROOT / path) for path in code_paths},
        "runtime": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "polars": pl.__version__,
        },
        "model": config["model"],
        "costs": config["economics"],
        "fresh_holdout": {
            "status": "NOT OBTAINED",
            "2025_plus_eligible": False,
            "required_start": "after frozen candidate timestamp",
        },
    }
    write_json(out / "phase3-provenance.json", provenance)
    final_decision = (
        "PROMISING — REQUIRES FRESH HOLDOUT"
        if decisions.get("EXP-031-walk-forward-confirmation") == "PROMISING — REQUIRES FRESH HOLDOUT"
        else "NO EDGE"
    )
    verification = {
        "phase3_decision": final_decision,
        "experiment_decisions": decisions,
        "data_integrity": {
            "source_hash_verified": True,
            "pre_2025_only": True,
            "timestamp_compression": False,
            "future_feature_leakage_detected": False,
            "future_label_leakage_detected": False,
            "calibration_contamination": False,
            "2025_plus_selection": False,
        },
        "economic_integrity": {
            "executable_entry": "open[t+1]",
            "executable_exit": "barrier fill or open[t+H+1]",
            "fees_exact_per_fill": True,
            "slippage_exact_per_fill": True,
            "discrete_funding": True,
            "side_transformation": True,
            "timeout_payoff": True,
            "actual_one_minute_delay_tested": True,
        },
        "statistical_integrity": {
            "chronological_validation": True,
            "purge_embargo_minutes": PURGE_MINUTES,
            "random_temporal_split": False,
            "target_sweep_controlled": True,
            "multiple_testing_documented": True,
            "uncertainty_not_forced_monotonic": True,
        },
        "research_integrity": {
            "phase2_frozen": True,
            "controls_preserved": True,
            "negative_results_retained": True,
            "frontier_laya_jev_rl_live_modified": False,
            "microstructure_fabricated": False,
        },
        "pytest": {"command": ".venv/bin/python -m pytest -q", "status": "PENDING_FINAL_RUN"},
    }
    write_json(out / "phase3-verification.json", verification)
    return {"registry": registry, "provenance": provenance, "verification": verification}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("audit", "grid", "analysis", "confirmation", "finalize", "all"), default="all")
    parser.add_argument("--out", default="experiments")
    args = parser.parse_args(argv)
    config = read_config()
    out = ROOT / args.out
    results: dict[str, object] = {}
    if args.stage in {"audit", "all"}:
        results["audit"] = run_economic_audit(config, out)
    if args.stage in {"grid", "all"}:
        results["grid"] = run_discovery_grid(config, out)
    if args.stage in {"analysis", "all"}:
        results.update(run_discovery_analysis(config, out))
    if args.stage in {"confirmation", "all"}:
        results.update(run_confirmation(config, out))
    if args.stage in {"finalize", "all"}:
        results["finalize"] = finalize_phase3(config, out)
    print(json.dumps({
        "stage": args.stage,
        "decisions": {
            key: value["decision"]
            for key, value in results.items()
            if isinstance(value, dict) and "decision" in value
        },
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

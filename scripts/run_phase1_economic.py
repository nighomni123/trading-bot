#!/usr/bin/env python3
"""Phase 1 economic-target experiment on pre-OOS data only.

This is a measurement gate, not a promotion run. It compares zero, historical
mean, linear, and deterministic LightGBM predictions for explicit return and
excursion targets. It never accepts an OOS date and never persists an unvalidated
model as a deployable artifact.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import polars as pl
from lightgbm import LGBMRegressor
from sklearn.linear_model import LinearRegression

from jev_trading.labels.engine import compute_labels, compute_path_labels
from jev_trading.quant.train import purge_ms_for_horizon
from jev_trading.state.features import FEATURE_COLUMNS, build_features

OOS_START = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
TRAIN_START = int(datetime(2021, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
TRAIN_END = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
VALID_START = TRAIN_END
VALID_END = OOS_START
TARGETS = (
    "future_return_5m",
    "future_return_15m",
    "future_return_30m",
    "future_return_60m",
    "mfe_15m",
    "mae_15m",
    "mfe_60m",
    "mae_60m",
)
PATH_TP = 0.0020
PATH_SL = 0.0020
PATH_HORIZON = 60


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def versions() -> dict[str, str]:
    result = {"python": platform.python_version()}
    for name in ("lightgbm", "scikit-learn", "polars", "numpy"):
        result[name] = importlib.metadata.version(name)
    return result


def regression_metrics(y: np.ndarray, prediction: np.ndarray) -> dict:
    error = prediction - y
    corr = float(np.corrcoef(prediction, y)[0, 1]) if np.std(prediction) > 0 and np.std(y) > 0 else 0.0
    return {
        "n": int(len(y)),
        "mae": float(np.mean(np.abs(error))),
        "rmse": float(np.sqrt(np.mean(error * error))),
        "corr": corr,
        "direction_accuracy": None,
        "prediction_mean": float(np.mean(prediction)),
        "prediction_std": float(np.std(prediction)),
        "target_mean": float(np.mean(y)),
        "target_std": float(np.std(y)),
    }


def target_metrics(y: np.ndarray, prediction: np.ndarray, target_name: str) -> dict:
    metrics = regression_metrics(y, prediction)
    metrics["direction_accuracy"] = (
        float(np.mean((prediction >= 0) == (y >= 0))) if "future_return" in target_name else None
    )
    return metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", default="data/btcusdt_1m.parquet")
    parser.add_argument("--out", default="artifacts/phase1-economic")
    args = parser.parse_args(argv)

    source = Path(args.bars)
    if not source.is_file():
        parser.error(f"source parquet not found: {source}")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # Slice before feature/label construction; this experiment has no OOS mode.
    bars = pl.read_parquet(source).filter(
        (pl.col("timestamp") >= TRAIN_START) & (pl.col("timestamp") < OOS_START)
    ).sort("timestamp")
    if bars.is_empty():
        raise ValueError("pre-OOS research slice is empty")
    features = build_features(bars).drop_nulls(subset=list(FEATURE_COLUMNS))
    labels = compute_labels(bars)
    joined = features.join(labels, on="timestamp", how="inner").sort("timestamp")
    x = joined.select(list(FEATURE_COLUMNS)).to_numpy()
    timestamps = joined["timestamp"].to_numpy()
    purge = purge_ms_for_horizon(60)
    train_mask = (timestamps >= TRAIN_START) & (timestamps < TRAIN_END - purge)
    valid_mask = (timestamps >= VALID_START + purge) & (timestamps < VALID_END - purge)

    metrics: dict[str, dict] = {}
    useful_targets: list[str] = []
    for target in TARGETS:
        target_values = joined[target].to_numpy()
        usable_train = train_mask & np.isfinite(target_values)
        usable_valid = valid_mask & np.isfinite(target_values)
        ytr = target_values[usable_train]
        yva = target_values[usable_valid]
        xtr, xva = x[usable_train], x[usable_valid]
        mean = float(np.mean(ytr))
        linear = LinearRegression().fit(xtr, ytr)
        lgb = LGBMRegressor(
            n_estimators=100,
            num_leaves=31,
            learning_rate=0.05,
            random_state=7,
            deterministic=True,
            force_col_wise=True,
            verbosity=-1,
        ).fit(xtr, ytr)
        predictions = {
            "zero": np.zeros_like(yva),
            "historical_mean": np.full_like(yva, mean),
            "linear": linear.predict(xva),
            "lightgbm": lgb.predict(xva),
        }
        target_result = {name: target_metrics(yva, pred, target) for name, pred in predictions.items()}
        mean_mae = target_result["historical_mean"]["mae"]
        lgb_metrics = target_result["lightgbm"]
        if "future_return" in target:
            predicted_net = predictions["lightgbm"] - 0.0014
            target_result["economic"] = {
                "base_round_trip_cost": 0.0014,
                "mean_predicted_net_edge": float(np.mean(predicted_net)),
                "fraction_predicted_net_positive": float(np.mean(predicted_net > 0)),
            }
        else:
            target_result["economic"] = None
        target_result["useful_validation_signal"] = bool(
            "future_return" in target
            and lgb_metrics["corr"] >= 0.02
            and lgb_metrics["mae"] < mean_mae * 0.99
            and target_result["economic"]["mean_predicted_net_edge"] > 0
        )
        if target_result["useful_validation_signal"]:
            useful_targets.append(target)
        metrics[target] = target_result
        print(json.dumps({"target": target, **target_result}, sort_keys=True))

    path = compute_path_labels(bars, PATH_TP, PATH_SL, PATH_HORIZON)
    path_joined = joined.select("timestamp").join(path, on="timestamp", how="inner")
    path_valid = path_joined.filter(
        (pl.col("timestamp") >= VALID_START + purge) & (pl.col("timestamp") < VALID_END - purge)
    )
    path_metrics = {
        "tp": PATH_TP,
        "sl": PATH_SL,
        "horizon_bars": PATH_HORIZON,
        "valid_rows": path_valid.height,
        "tp_before_sl_rate": path_valid["tp_before_sl"].drop_nulls().mean(),
        "timeout_rate": path_valid["timeout"].drop_nulls().mean(),
        "time_to_tp_median": path_valid["time_to_tp"].drop_nulls().median(),
        "time_to_sl_median": path_valid["time_to_sl"].drop_nulls().median(),
    }
    decision = "STOP" if not useful_targets else "DEFER_NEW_OOS"
    (out / "metrics.json").write_text(json.dumps({
        "targets": metrics,
        "path_labels": {k: (float(v) if isinstance(v, (float, np.floating)) else v) for k, v in path_metrics.items()},
        "decision": decision,
        "useful_validation_targets": useful_targets,
    }, indent=2, sort_keys=True))
    provenance = {
        "experiment_id": "PHASE1-ECONOMIC-TARGETS",
        "git_commit": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip(),
        "source": {"path": str(source), "sha256": sha256(source)},
        "periods": {"train": ["2021-01-01", "2024-01-01"], "validation": ["2024-01-01", "2025-01-01"], "oos_used": False},
        "purge_ms": purge,
        "targets": list(TARGETS),
        "path_pair": {"tp": PATH_TP, "sl": PATH_SL, "horizon": PATH_HORIZON},
        "runtime": versions(),
        "code_hashes": {f: sha256(Path(f)) for f in (
            "src/jev_trading/labels/engine.py",
            "src/jev_trading/quant/train.py",
            "src/jev_trading/state/features.py",
            "scripts/run_phase1_economic.py",
        )},
        "oos_contamination": "2025+ was historically consumed; no fresh OOS is claimed",
    }
    (out / "provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True))
    (out / "summary.json").write_text(json.dumps({
        "decision": decision,
        "useful_validation_targets": useful_targets,
        "metrics_file": "metrics.json",
        "provenance_file": "provenance.json",
        "oos_used": False,
    }, indent=2))
    (out / "notes.md").write_text(
        "# Phase 1 economic target gate\n\n"
        f"Decision: **{decision}**. The experiment uses pre-2025 data only. "
        "A validation signal is not a frozen-OOS promotion. If no target beats the "
        "historical-mean baseline with useful correlation, stop and revisit features/targets; "
        "do not add Frontier or Jev.\n"
    )
    print(json.dumps({"decision": decision, "useful_validation_targets": useful_targets}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

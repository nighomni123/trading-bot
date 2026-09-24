#!/usr/bin/env python3
"""Chronological pre-2025 walk-forward stability for the Phase 2 return/direction core."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import polars as pl
from lightgbm import LGBMClassifier, LGBMRegressor

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jev_trading.quant.engine import FEATURE_SETS, build_execution_training_frame, chronological_economic_split
from jev_trading.quant.evaluation import direction_metrics, predicted_edge_summary, regression_metrics, simulate_fixed_horizon_segments

OOS_START = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
FOLDS = (
    ("2021", "2022", "train:[2021-01-01,2022-01-01)", "valid:[2022-01-01,2023-01-01)"),
    ("2021-2022", "2023", "train:[2021-01-01,2023-01-01)", "valid:[2023-01-01,2024-01-01)"),
    ("2021-2023", "2024", "train:[2021-01-01,2024-01-01)", "valid:[2024-01-01,2025-01-01)"),
)


def ms(year: int) -> int:
    return int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", default="artifacts/phase0-baseline-final-d/pre_oos_2021_2024.parquet")
    parser.add_argument("--out", default="artifacts/phase2-experiments-final/EXP-018-walk-forward/metrics.json")
    args = parser.parse_args(argv)
    bars = pl.read_parquet(args.bars).filter(pl.col("timestamp") < OOS_START).sort("timestamp")
    features = FEATURE_SETS["base"]
    frame = build_execution_training_frame(bars, features)
    results = []
    for train_name, valid_name, train_label, valid_label in FOLDS:
        start_year = int(train_name.split("-")[-1])
        valid_year = int(valid_name)
        train, valid = chronological_economic_split(frame, ms(start_year + 1), ms(valid_year), ms(valid_year + 1), 60)
        direction_train = train.drop_nulls(subset=[*features, "direction_class_exec_15"])
        direction = LGBMClassifier(
            num_leaves=15, n_estimators=20, learning_rate=0.05, verbosity=-1,
            random_state=7, deterministic=True, force_col_wise=True,
            objective="multiclass", num_class=3,
        ).fit(direction_train.select(list(features)).to_numpy(), direction_train["direction_class_exec_15"].to_numpy().astype(int))
        target_train = train.drop_nulls(subset=[*features, "execution_return_15m"])
        Xtr = target_train.select(list(features)).to_numpy()
        ytr = target_train["execution_return_15m"].to_numpy()
        regressors = [
            LGBMRegressor(num_leaves=15, n_estimators=20, learning_rate=0.05, verbosity=-1,
                          random_state=seed, deterministic=True, force_col_wise=True).fit(Xtr, ytr)
            for seed in (7, 17)
        ]
        valid = valid.drop_nulls(subset=[*features, "direction_class_exec_15", "execution_return_15m", "funding_rate"])
        Xva = valid.select(list(features)).to_numpy()
        class_probs = direction.predict_proba(Xva)
        classes = {int(c): i for i, c in enumerate(direction.classes_)}
        p_dn, p_flat, p_up = class_probs[:, classes[0]], class_probs[:, classes[1]], class_probs[:, classes[2]]
        ensemble = np.column_stack([model.predict(Xva) for model in regressors])
        prediction = ensemble.mean(axis=1)
        uncertainty = np.clip(ensemble.std(axis=1) / np.std(ytr), 0.0, 1.0)
        actual = valid["execution_return_15m"].to_numpy()
        funding = valid["funding_rate"].to_numpy()
        timestamps = valid["timestamp"].to_numpy()
        edge = predicted_edge_summary(p_up, p_flat, p_dn, prediction, uncertainty, funding)
        simulation = simulate_fixed_horizon_segments(
            timestamps, p_up, p_flat, p_dn, prediction, actual, funding, uncertainty,
            horizon_minutes=15,
        )
        results.append({
            "fold": f"{train_name}->{valid_name}",
            "train": train_label,
            "validation": valid_label,
            "rows": {"train": len(train), "validation": len(valid)},
            "direction": direction_metrics(valid["direction_class_exec_15"].to_numpy().astype(int), np.column_stack([p_up, p_flat, p_dn])),
            "return": {
                "historical_mean": regression_metrics(actual, np.full_like(actual, ytr.mean())),
                "lightgbm_ensemble": regression_metrics(actual, prediction),
            },
            "economic_gate": edge,
            "simulation_1x": simulation,
        })
    all_positive = all(row["economic_gate"]["mean_uncertainty_adjusted_edge"] > 0 and row["simulation_1x"]["trade_count"] > 0 for row in results)
    output = {
        "experiment_id": "EXP-018",
        "question": "Is the Phase 2 return/direction economic edge stable across chronological folds?",
        "decision": "PASS" if all_positive else "NO_EDGE",
        "reason": "all folds clear the economic gate" if all_positive else "at least one chronological fold has non-positive adjusted edge or no selected trade",
        "oos_used": False,
        "purge_minutes": 60,
        "folds": results,
    }
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, indent=2, sort_keys=True))
    print(json.dumps({"decision": output["decision"], "folds": len(results)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

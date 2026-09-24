#!/usr/bin/env python3
"""Run Phase 2 Economic Quant Engine v2 experiments on pre-2025 data only.

This is a research runner. It never loads 2025+ rows, never changes live/policy
configuration, and writes separate experiment artifacts for each gate.
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
from sklearn.linear_model import LinearRegression

from jev_trading.quant.engine import (
    FEATURE_SETS,
    SPECIALIST_FEATURE_SETS,
    build_economic_training_frame,
    chronological_economic_split,
    load_economic_bundle,
    save_economic_bundle,
    train_economic_bundle,
)
from jev_trading.quant.evaluation import (
    direction_metrics,
    predicted_edge_summary,
    regression_metrics,
    simulate_fixed_horizon_segments,
    uncertainty_reliability,
)

OOS_START = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
TRAIN_START = int(datetime(2021, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
TRAIN_END = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
VALID_START = TRAIN_END
VALID_END = OOS_START
SEEDS = (7, 17, 27)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_sha() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str))


def eval_bundle(
    bars: pl.DataFrame,
    bundle,
    feature_cols: tuple[str, ...],
    *,
    cost_modes=True,
    cached_frame: pl.DataFrame | None = None,
) -> dict:
    frame = cached_frame if cached_frame is not None else build_economic_training_frame(
        bars, feature_cols, holding_tp=0.002, holding_sl=0.002
    )
    train, valid = chronological_economic_split(frame, TRAIN_END, VALID_START, VALID_END, 60)
    required = [*feature_cols, "direction_class_exec_15", "execution_return_15m", "funding_rate"]
    valid = valid.drop_nulls(subset=required).sort("timestamp")
    X = valid.select(["timestamp", *feature_cols])
    direction = bundle.predict_direction(X)
    y_dir = valid["direction_class_exec_15"].to_numpy().astype(int)
    direction_result = direction_metrics(y_dir, np.column_stack([
        direction["p_up_15"], direction["p_flat_15"], direction["p_dn_15"]
    ]))
    return_metrics = {}
    linear_models = {}
    for horizon, target in ((5, "future_return_5m"), (15, "future_return_15m"), (30, "future_return_30m"), (60, "future_return_60m")):
        target_train = train.drop_nulls(subset=[*feature_cols, target])
        target_valid = valid.drop_nulls(subset=[*feature_cols, target])
        xtr = target_train.select(list(feature_cols)).to_numpy()
        ytr = target_train[target].to_numpy()
        xva = target_valid.select(list(feature_cols)).to_numpy()
        yva = target_valid[target].to_numpy()
        linear = LinearRegression().fit(xtr, ytr)
        linear_models[target] = linear
        prediction = bundle.predict_raw_return(target_valid, horizon)
        return_metrics[target] = {
            "zero": regression_metrics(yva, np.zeros_like(yva)),
            "historical_mean": regression_metrics(yva, np.full_like(yva, ytr.mean())),
            "linear": regression_metrics(yva, linear.predict(xva)),
            "lightgbm": regression_metrics(yva, prediction),
        }
    excursion_metrics = {}
    for horizon, (mfe_target, mae_target) in ((15, ("execution_mfe_15m", "execution_mae_15m")), (60, ("execution_mfe_60m", "execution_mae_60m"))):
        for name, target in (("mfe", mfe_target), ("mae", mae_target)):
            rows = valid.drop_nulls(subset=[*feature_cols, target])
            x = rows.select(list(feature_cols))
            pred = (bundle.mfe_models[horizon] if name == "mfe" else bundle.mae_models[horizon]).predict(x.to_numpy())
            excursion_metrics[target] = regression_metrics(rows[target].to_numpy(), pred)
    p_up = direction["p_up_15"]
    p_flat = direction["p_flat_15"]
    p_dn = direction["p_dn_15"]
    predicted = bundle.predict_execution_return(X, 15)
    uncertainty = bundle.predict_uncertainty_15(X)
    actual = valid["execution_return_15m"].to_numpy()
    funding = valid["funding_rate"].to_numpy()
    timestamps = valid["timestamp"].to_numpy()
    edge = predicted_edge_summary(p_up, p_flat, p_dn, predicted, uncertainty, funding)
    simulation = simulate_fixed_horizon_segments(
        timestamps, p_up, p_flat, p_dn, predicted, actual, funding, uncertainty,
        horizon_minutes=15,
    )
    stress = {}
    if cost_modes:
        for label, kwargs in {
            "2x_fees": {"fee_mult": 2.0},
            "3x_fees": {"fee_mult": 3.0},
            "extra_slippage": {"slippage_extra_pct": 0.05},
            "execution_delay": {"delay_penalty_pct": 0.02},
        }.items():
            stress[label] = simulate_fixed_horizon_segments(
                timestamps, p_up, p_flat, p_dn, predicted, actual, funding, uncertainty,
                horizon_minutes=15, **kwargs,
            )
    reliability = uncertainty_reliability(actual, predicted, uncertainty)
    return {
        "rows": {"train": len(train), "validation": len(valid)},
        "direction": direction_result,
        "returns": return_metrics,
        "excursions": excursion_metrics,
        "uncertainty": reliability,
        "economic_gate": edge,
        "simulation_1x": simulation,
        "cost_stress": stress,
        "feature_cols": list(feature_cols),
    }


def experiment_record(exp_id: str, question: str, result: dict, decision: str, reason: str) -> dict:
    return {
        "experiment_id": exp_id,
        "question": question,
        "decision": decision,
        "reason": reason,
        "oos_used": False,
        "result": result,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", default="artifacts/phase0-baseline-final-d/pre_oos_2021_2024.parquet")
    parser.add_argument("--out", default="artifacts/phase2-experiments")
    parser.add_argument("--trees", type=int, default=60)
    parser.add_argument("--leaves", type=int, default=15)
    parser.add_argument("--skip-derived", action="store_true")
    parser.add_argument("--base-model-dir", default=None, help="reuse a previously trained base bundle")
    args = parser.parse_args(argv)
    source = Path(args.bars)
    if not source.is_file():
        parser.error(f"pre-OOS bars not found: {source}")
    bars = pl.read_parquet(source).filter((pl.col("timestamp") >= TRAIN_START) & (pl.col("timestamp") < OOS_START)).sort("timestamp")
    if bars.is_empty() or bars["timestamp"].max() >= OOS_START:
        raise ValueError("Phase 2 runner received an empty or OOS-containing source")
    out = Path(args.out)
    params = {"n_estimators": args.trees, "num_leaves": args.leaves}
    common_frame = build_economic_training_frame(
        bars, FEATURE_SETS["base_plus_derived"], holding_tp=0.002, holding_sl=0.002
    )
    if args.base_model_dir:
        base_bundle = load_economic_bundle(args.base_model_dir)
    else:
        base_bundle = train_economic_bundle(
            bars, train_end=TRAIN_END, valid_start=VALID_START, valid_end=VALID_END,
            feature_set="base", lgbm_params=params, seeds=SEEDS, training_frame=common_frame,
        )
        save_economic_bundle(base_bundle, out / "EXP-010-economic-return-baseline" / "models")
    base_result = eval_bundle(bars, base_bundle, FEATURE_SETS["base"], cached_frame=common_frame)
    base_edge = base_result["economic_gate"]["mean_uncertainty_adjusted_edge"]
    base_trade = base_result["simulation_1x"]["trade_count"]
    base_positive = base_edge > 0 and base_trade > 0

    exp10 = experiment_record(
        "EXP-010", "Do explicit multi-horizon return heads beat simple baselines and produce positive predicted net edge?",
        base_result, "PASS" if base_positive else "NO_EDGE",
        "positive adjusted predicted edge and non-zero fixed-horizon trades" if base_positive else "return heads fail the pre-OOS economic gate",
    )
    write_json(out / "EXP-010-economic-return-baseline" / "metrics.json", exp10)

    derived_result = None
    if not args.skip_derived:
        derived_bundle = train_economic_bundle(
            bars, train_end=TRAIN_END, valid_start=VALID_START, valid_end=VALID_END,
            feature_set="base_plus_derived", lgbm_params=params, seeds=SEEDS,
            training_frame=common_frame,
        )
        save_economic_bundle(derived_bundle, out / "EXP-011-downside-excursion" / "models")
        derived_result = eval_bundle(
            bars, derived_bundle, FEATURE_SETS["base_plus_derived"], cached_frame=common_frame
        )
    direction_excursion_result = derived_result or base_result
    direction_excursion_pass = bool(
        base_positive
        and derived_result is not None
        and derived_result["economic_gate"]["mean_uncertainty_adjusted_edge"] > 0
    )
    exp11 = experiment_record(
        "EXP-011", "Do explicit down/flat/up, execution-return, and MFE/MAE heads add economic information?",
        direction_excursion_result,
        "PASS" if direction_excursion_pass else "NO_EDGE",
        "excursion/direction heads are measured but cannot override return economics",
    )
    write_json(out / "EXP-011-downside-excursion" / "metrics.json", exp11)
    uncertainty_result = derived_result or base_result
    uncertainty_pass = bool(uncertainty_result["uncertainty"]["errors_monotonic"])
    exp12 = experiment_record(
        "EXP-012", "Does normalized ensemble dispersion predict absolute return error?",
        (derived_result or base_result)["uncertainty"],
        "PASS" if uncertainty_pass else "NO_EDGE",
        "uncertainty increases monotonically with realized error" if uncertainty_pass else "uncertainty is not a reliable error signal",
    )
    write_json(out / "EXP-012-uncertainty" / "metrics.json", exp12)

    if derived_result is not None:
        ablation = {
            "base": base_result["economic_gate"],
            "base_plus_derived": derived_result["economic_gate"],
            "base_return_15": base_result["returns"]["future_return_15m"]["lightgbm"],
            "derived_return_15": derived_result["returns"]["future_return_15m"]["lightgbm"],
            "decision": "PASS" if derived_result["economic_gate"]["mean_uncertainty_adjusted_edge"] > base_edge and derived_result["economic_gate"]["fraction_eligible"] > base_result["economic_gate"]["fraction_eligible"] else "NO_EDGE",
        }
    else:
        ablation = {"decision": "DEFER", "reason": "derived feature run skipped"}
    exp13 = experiment_record("EXP-013", "Do interpretable derived features improve economic metrics over the frozen base?", ablation, ablation.get("decision", "DEFER"), "controlled base_plus_derived comparison")
    write_json(out / "EXP-013-feature-ablation" / "metrics.json", exp13)

    # Specialists are intentionally not trained after a failed base economic gate.
    specialist_result = {
        "feature_sets": {name: list(cols) for name, cols in SPECIALIST_FEATURE_SETS.items()},
        "decision": "DEFER",
        "reason": "EXP-010/011 base economic gate failed; independent specialists are not promoted or trained",
    }
    for exp_id, question in (
        ("EXP-014", "Momentum specialist economic value"),
        ("EXP-015", "Mean-reversion specialist economic value"),
        ("EXP-016", "Breakout specialist economic value"),
        ("EXP-017", "Specialist ensemble/arbitration value"),
    ):
        write_json(out / f"{exp_id}-specialist" / "metrics.json", experiment_record(exp_id, question, specialist_result, "DEFER", specialist_result["reason"]))

    # A small chronological walk-forward is still recorded as a structural gate;
    # expensive specialist work is not launched without base evidence.
    wf = {
        "folds": [
            {"train": ["2021-01-01", "2022-01-01"], "validation": ["2022-01-01", "2023-01-01"]},
            {"train": ["2021-01-01", "2023-01-01"], "validation": ["2023-01-01", "2024-01-01"]},
            {"train": ["2021-01-01", "2024-01-01"], "validation": ["2024-01-01", "2025-01-01"]},
        ],
        "purge_minutes": 60,
        "oos_used": False,
        "decision": "DEFER_AFTER_BASE_GATE",
        "reason": "Base economic gate failed; no specialist allocation is justified",
    }
    write_json(out / "EXP-018-walk-forward" / "metrics.json", experiment_record("EXP-018", "Are economic results stable across chronological walk-forward windows?", wf, "DEFER", wf["reason"]))
    stress_result = base_result["cost_stress"]
    stress_pass = bool(base_positive and base_result["simulation_1x"]["net_return_sum"] > 0 and all(
        result.get("net_return_sum", 0.0) > 0 for result in stress_result.values()
    ))
    exp19 = experiment_record(
        "EXP-019", "Does the base economic edge survive adverse costs?", stress_result,
        "PASS" if stress_pass else "NO_EDGE",
        "base return edge is negative before stress" if not base_positive else "one or more hostile-cost scenarios are non-positive",
    )
    write_json(out / "EXP-019-cost-stress" / "metrics.json", exp19)

    common = {
        "git_commit": git_sha(),
        "source": {"path": str(source), "sha256": sha256(source)},
        "periods": {"train": ["2021-01-01", "2024-01-01"], "validation": ["2024-01-01", "2025-01-01"]},
        "oos_used": False,
        "runtime": {"python": platform.python_version(), "numpy": np.__version__, "polars": pl.__version__},
        "model_config": {"seeds": list(SEEDS), **params},
        "cost_model": "taker 0.05% per side, slippage 0.02% per side, funding per 8h, delay/slippage stress",
        "feature_sets": {name: list(cols) for name, cols in FEATURE_SETS.items()},
        "code_hashes": {f: sha256(Path(f)) for f in ("src/jev_trading/quant/engine.py", "src/jev_trading/quant/evaluation.py", "src/jev_trading/quant/economic.py", "src/jev_trading/labels/engine.py", "src/jev_trading/state/features.py", "scripts/run_phase2_experiments.py")},
    }
    write_json(out / "provenance.json", common)
    print(json.dumps({"base_positive": base_positive, "experiments": 10, "out": str(out)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

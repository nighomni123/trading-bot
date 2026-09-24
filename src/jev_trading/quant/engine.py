"""Phase 2 multi-output economic quant engine.

All supervised targets are point-in-time. Return and excursion heads use
open[t+1] entry and open[t+H+1] exit so their economics match next-bar execution.
Uncertainty is normalized dispersion from deterministic return-model seeds.
"""
from __future__ import annotations

import hashlib
import json
import pickle
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from lightgbm import LGBMClassifier, LGBMRegressor

from jev_trading.labels.engine import (
    DEFAULT_THRESHOLD,
    compute_execution_labels,
    compute_labels,
    compute_path_labels,
)
from jev_trading.quant.economic import EconomicQuantOutput
from jev_trading.state.features import (
    FEATURE_COLUMNS,
    PHASE2_DERIVED_COLUMNS,
    build_phase2_features,
)

RAW_RETURN_TARGETS = {h: f"future_return_{h}m" for h in (5, 15, 30, 60)}
EXECUTION_RETURN_TARGETS = {h: f"execution_return_{h}m" for h in (5, 15, 30, 60)}
EXCURSION_TARGETS = {
    15: ("execution_mfe_15m", "execution_mae_15m"),
    60: ("execution_mfe_60m", "execution_mae_60m"),
}
BASE_FEATURE_SET = tuple(FEATURE_COLUMNS)
DERIVED_FEATURE_SET = PHASE2_DERIVED_COLUMNS
BASE_PLUS_DERIVED_FEATURE_SET = BASE_FEATURE_SET + DERIVED_FEATURE_SET
INTERPRETABLE_FEATURE_SET = tuple(c for c in FEATURE_COLUMNS if c != "trend_score") + DERIVED_FEATURE_SET
FEATURE_SETS = {
    "base": BASE_FEATURE_SET,
    "base_plus_derived": BASE_PLUS_DERIVED_FEATURE_SET,
    "interpretable": INTERPRETABLE_FEATURE_SET,
}
SPECIALIST_FEATURE_SETS = {
    "momentum": tuple(c for c in BASE_PLUS_DERIVED_FEATURE_SET if c in {
        "ret_1m", "ret_5m", "ret_15m", "ret_60m", "atr_14", "ema20", "ema50",
        "ema200", "ema20_ema50_gap", "ema50_ema200_gap", "ema20_ema200_gap",
        "trend_accel_15", "trend_duration_60", "ret_15_atr", "ret_15_rv",
        "funding", "funding_z", "oi_change_1d", "funding_oi_interaction", "volume_z",
    }),
    "mean_reversion": tuple(c for c in BASE_PLUS_DERIVED_FEATURE_SET if c in {
        "ret_1m", "ret_5m", "ret_15m", "realized_vol_5m", "realized_vol_30m",
        "atr_14", "ema20", "ema50", "ema20_ema50_gap", "ema50_ema200_gap",
        "ret_15_atr", "ret_15_rv", "funding", "funding_z", "volume_z",
    }),
    "breakout": tuple(c for c in BASE_PLUS_DERIVED_FEATURE_SET if c in {
        "ret_15m", "ret_60m", "realized_vol_5m", "realized_vol_30m", "atr_14",
        "ema20", "ema50", "ema200", "ema20_ema50_gap", "ema50_ema200_gap",
        "ema20_ema200_gap", "trend_accel_15", "trend_duration_60", "ret_15_rv",
        "volume_z", "oi_change_1d",
    }),
}
DEFAULT_LGBM_PARAMS = {
    "num_leaves": 15,
    "n_estimators": 80,
    "learning_rate": 0.05,
    "verbosity": -1,
    "deterministic": True,
    "force_col_wise": True,
}


@dataclass
class EconomicModelBundle:
    feature_set: str
    feature_cols: tuple[str, ...]
    direction_model: Any
    direction_classes: tuple[int, ...]
    raw_return_models: dict[int, Any]
    execution_return_models: dict[int, Any]
    execution_return_ensemble_15: list[Any]
    mfe_models: dict[int, Any]
    mae_models: dict[int, Any]
    holding_model: Any
    holding_tp: float
    holding_sl: float
    holding_horizon_minutes: int
    uncertainty_scale: float
    seeds: tuple[int, ...]
    config: dict[str, Any]

    def _matrix(self, X: pl.DataFrame) -> np.ndarray:
        missing = set(self.feature_cols) - set(X.columns)
        if missing:
            raise ValueError(f"missing economic features: {sorted(missing)}")
        return X.select(list(self.feature_cols)).to_numpy()

    def predict_direction(self, X: pl.DataFrame) -> dict[str, np.ndarray]:
        raw = np.asarray(self.direction_model.predict_proba(self._matrix(X)), dtype=float)
        classes = {int(c): i for i, c in enumerate(self.direction_classes)}
        return {
            "p_dn_15": raw[:, classes[0]],
            "p_flat_15": raw[:, classes[1]],
            "p_up_15": raw[:, classes[2]],
        }

    def predict_raw_return(self, X: pl.DataFrame, horizon: int) -> np.ndarray:
        if horizon not in self.raw_return_models:
            raise KeyError(f"no raw return head for {horizon}m")
        return np.asarray(self.raw_return_models[horizon].predict(self._matrix(X)), dtype=float)

    def predict_execution_return(self, X: pl.DataFrame, horizon: int) -> np.ndarray:
        if horizon not in self.execution_return_models:
            raise KeyError(f"no execution return head for {horizon}m")
        if horizon == 15 and self.execution_return_ensemble_15:
            matrix = self._matrix(X)
            return np.column_stack([
                model.predict(matrix) for model in self.execution_return_ensemble_15
            ]).mean(axis=1)
        return np.asarray(self.execution_return_models[horizon].predict(self._matrix(X)), dtype=float)

    def predict_uncertainty_15(self, X: pl.DataFrame) -> np.ndarray:
        matrix = self._matrix(X)
        predictions = np.column_stack([m.predict(matrix) for m in self.execution_return_ensemble_15])
        return np.clip(predictions.std(axis=1) / self.uncertainty_scale, 0.0, 1.0)

    def predict_economic_output(self, X: pl.DataFrame) -> dict[str, Any]:
        direction = self.predict_direction(X)
        expected_return = self.predict_execution_return(X, 15)
        uncertainty = self.predict_uncertainty_15(X)
        matrix = self._matrix(X)
        mfe = np.asarray(self.mfe_models[15].predict(matrix), dtype=float)
        mae = np.asarray(self.mae_models[15].predict(matrix), dtype=float)
        holding = np.clip(
            np.asarray(self.holding_model.predict(matrix), dtype=float),
            1.0,
            float(self.holding_horizon_minutes),
        )
        rows = []
        for i in range(len(X)):
            side = "LONG" if direction["p_up_15"][i] >= direction["p_dn_15"][i] else "SHORT"
            rows.append(EconomicQuantOutput(
                p_up_15=float(direction["p_up_15"][i]),
                p_flat_15=float(direction["p_flat_15"][i]),
                p_dn_15=float(direction["p_dn_15"][i]),
                expected_return_15=float(expected_return[i]),
                expected_downside_15=float(mae[i]),
                expected_favorable_excursion_15=float(mfe[i]),
                expected_adverse_excursion_15=float(mae[i]),
                uncertainty=float(uncertainty[i]),
                holding_time_minutes=float(holding[i]),
                horizon_min=15,
                side=side,
            ))
        return {
            "rows": rows,
            "p_up_15": direction["p_up_15"],
            "p_flat_15": direction["p_flat_15"],
            "p_dn_15": direction["p_dn_15"],
            "expected_return_15": expected_return,
            "expected_mfe_15": mfe,
            "expected_mae_15": mae,
            "uncertainty_15": uncertainty,
            "holding_minutes_15": holding,
        }


def _model_params(params: dict | None, seed: int) -> dict:
    return {**DEFAULT_LGBM_PARAMS, **(params or {}), "random_state": seed}


def _regressor(X: np.ndarray, y: np.ndarray, params: dict | None, seed: int) -> LGBMRegressor:
    model = LGBMRegressor(**_model_params(params, seed))
    return model.fit(X, y)


def build_execution_training_frame(
    bars: pl.DataFrame,
    feature_cols: tuple[str, ...],
    threshold: float = DEFAULT_THRESHOLD,
) -> pl.DataFrame:
    """Build the label/feature frame without barrier holding-time columns."""
    features = build_phase2_features(bars).select(["timestamp", "funding_rate", *feature_cols])
    execution = compute_execution_labels(bars, threshold=threshold)
    return features.join(execution, on="timestamp", how="inner").sort("timestamp")


def build_economic_training_frame(
    bars: pl.DataFrame,
    feature_cols: tuple[str, ...],
    threshold: float = DEFAULT_THRESHOLD,
    holding_tp: float = 0.002,
    holding_sl: float = 0.002,
    holding_horizon: int = 60,
) -> pl.DataFrame:
    features = build_phase2_features(bars).select(["timestamp", "funding_rate", *feature_cols])
    raw = compute_labels(bars, threshold=threshold)
    execution = compute_execution_labels(bars, threshold=threshold)
    path = compute_path_labels(bars, holding_tp, holding_sl, holding_horizon).with_columns(
        pl.min_horizontal("time_to_tp", "time_to_sl")
        .fill_null(pl.lit(holding_horizon))
        .alias("time_to_first_barrier")
    ).select("timestamp", "time_to_first_barrier", "timeout")
    return features.join(raw, on="timestamp", how="inner").join(
        execution, on="timestamp", how="inner"
    ).join(path, on="timestamp", how="inner").filter(pl.col("timeout").is_not_null()).sort("timestamp")


def chronological_economic_split(
    frame: pl.DataFrame,
    train_end: int,
    valid_start: int,
    valid_end: int,
    purge_bars: int = 60,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    if train_end > valid_start:
        raise ValueError("train_end must not exceed valid_start")
    purge_ms = purge_bars * 60_000
    train = frame.filter(pl.col("timestamp") < train_end - purge_ms)
    valid = frame.filter(
        (pl.col("timestamp") >= valid_start + purge_ms)
        & (pl.col("timestamp") < valid_end - purge_ms)
    )
    if not len(train) or not len(valid):
        raise ValueError("empty economic train or validation split")
    if train["timestamp"].max() >= valid["timestamp"].min():
        raise ValueError("economic split leakage across chronological boundary")
    return train, valid


def _fit_target(frame: pl.DataFrame, target: str, feature_cols: tuple[str, ...], params: dict | None, seed: int):
    usable = frame.drop_nulls(subset=[*feature_cols, target])
    if usable.is_empty():
        raise ValueError(f"no usable rows for target {target}")
    X = usable.select(list(feature_cols)).to_numpy()
    y = usable[target].to_numpy()
    return _regressor(X, y, params, seed), len(usable), float(np.std(y))


def train_economic_bundle(
    bars: pl.DataFrame,
    *,
    train_end: int,
    valid_start: int,
    valid_end: int,
    feature_set: str = "base_plus_derived",
    lgbm_params: dict | None = None,
    seeds: tuple[int, ...] = (7, 17, 27),
    threshold: float = DEFAULT_THRESHOLD,
    holding_tp: float = 0.002,
    holding_sl: float = 0.002,
    holding_horizon: int = 60,
    training_frame: pl.DataFrame | None = None,
) -> EconomicModelBundle:
    if feature_set not in FEATURE_SETS:
        raise KeyError(f"unknown feature set: {feature_set}")
    if valid_end > int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000):
        raise ValueError("Phase 2 economic training cannot consume 2025+ OOS")
    if len(set(seeds)) < 2:
        raise ValueError("at least two seeds are required for real ensemble uncertainty")
    feature_cols = FEATURE_SETS[feature_set]
    frame = training_frame if training_frame is not None else build_economic_training_frame(
        bars, feature_cols, threshold, holding_tp, holding_sl, holding_horizon
    )
    missing_features = set(feature_cols) - set(frame.columns)
    if missing_features:
        raise ValueError(f"cached training frame missing features: {sorted(missing_features)}")
    train, _valid = chronological_economic_split(frame, train_end, valid_start, valid_end, 60)

    direction = train.drop_nulls(subset=[*feature_cols, "direction_class_exec_15"])
    if direction["direction_class_exec_15"].n_unique() < 3:
        raise ValueError("direction training split must contain up, flat, and down classes")
    direction_model = LGBMClassifier(
        **_model_params(lgbm_params, seeds[0]), objective="multiclass", num_class=3
    ).fit(
        direction.select(list(feature_cols)).to_numpy(),
        direction["direction_class_exec_15"].to_numpy().astype(int),
    )

    raw_models: dict[int, Any] = {}
    execution_models: dict[int, Any] = {}
    mfe_models: dict[int, Any] = {}
    mae_models: dict[int, Any] = {}
    target_rows: dict[str, int] = {}
    target_scales: dict[str, float] = {}
    execution_ensemble: list[Any] = []
    uncertainty_scale = 1.0

    for horizon, target in RAW_RETURN_TARGETS.items():
        model, n_rows, scale = _fit_target(train, target, feature_cols, lgbm_params, seeds[0])
        raw_models[horizon] = model
        target_rows[target] = n_rows
        target_scales[target] = scale
    for horizon, target in EXECUTION_RETURN_TARGETS.items():
        if horizon == 15:
            fitted = [_fit_target(train, target, feature_cols, lgbm_params, seed) for seed in seeds]
            models = [item[0] for item in fitted]
            execution_ensemble = models
            execution_models[horizon] = models[0]
            n_rows, scale = fitted[0][1], fitted[0][2]
            uncertainty_scale = scale
        else:
            model, n_rows, scale = _fit_target(train, target, feature_cols, lgbm_params, seeds[0])
            execution_models[horizon] = model
        target_rows[target] = n_rows
        target_scales[target] = scale
    for horizon, (mfe_target, mae_target) in EXCURSION_TARGETS.items():
        mfe_models[horizon], n_mfe, mfe_scale = _fit_target(
            train, mfe_target, feature_cols, lgbm_params, seeds[0]
        )
        mae_models[horizon], n_mae, mae_scale = _fit_target(
            train, mae_target, feature_cols, lgbm_params, seeds[0]
        )
        target_rows[mfe_target] = n_mfe
        target_rows[mae_target] = n_mae
        target_scales[mfe_target] = mfe_scale
        target_scales[mae_target] = mae_scale
    holding_model, n_holding, holding_scale = _fit_target(
        train, "time_to_first_barrier", feature_cols, lgbm_params, seeds[0]
    )
    target_rows["time_to_first_barrier"] = n_holding
    target_scales["time_to_first_barrier"] = holding_scale
    if uncertainty_scale <= 0:
        raise ValueError("execution return target has zero variance; uncertainty is undefined")

    return EconomicModelBundle(
        feature_set=feature_set,
        feature_cols=feature_cols,
        direction_model=direction_model,
        direction_classes=tuple(int(c) for c in direction_model.classes_),
        raw_return_models=raw_models,
        execution_return_models=execution_models,
        execution_return_ensemble_15=execution_ensemble,
        mfe_models=mfe_models,
        mae_models=mae_models,
        holding_model=holding_model,
        holding_tp=holding_tp,
        holding_sl=holding_sl,
        holding_horizon_minutes=holding_horizon,
        uncertainty_scale=uncertainty_scale,
        seeds=tuple(seeds),
        config={
            "train_end": train_end,
            "valid_start": valid_start,
            "valid_end": valid_end,
            "purge_bars": 60,
            "threshold": threshold,
            "feature_cols": list(feature_cols),
            "seeds": list(seeds),
            "lgbm_params": _model_params(lgbm_params, seeds[0]),
            "target_rows": target_rows,
            "target_scales": target_scales,
            "holding_pair": [holding_tp, holding_sl],
            "holding_horizon_minutes": holding_horizon,
            "uncertainty_method": "normalized_ensemble_dispersion",
        },
    )


def save_economic_bundle(bundle: EconomicModelBundle, out_dir: str | Path) -> dict[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    model_path = out / "economic_bundle.pkl"
    model_path.write_bytes(pickle.dumps(bundle))
    metadata = {
        "feature_set": bundle.feature_set,
        "feature_cols": list(bundle.feature_cols),
        "direction_classes": list(bundle.direction_classes),
        "seeds": list(bundle.seeds),
        "config": bundle.config,
    }
    (out / "economic_metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True))
    (out / "metrics.json").write_text(json.dumps({
        "metrics": {"model_version": "economic-v2"},
        "feature_cols": list(bundle.feature_cols),
    }, indent=2, sort_keys=True))
    return {
        "economic_bundle.pkl": hashlib.sha256(model_path.read_bytes()).hexdigest(),
        "economic_metadata.json": hashlib.sha256((out / "economic_metadata.json").read_bytes()).hexdigest(),
        "metrics.json": hashlib.sha256((out / "metrics.json").read_bytes()).hexdigest(),
    }


def load_economic_bundle(model_dir: str | Path) -> EconomicModelBundle:
    path = Path(model_dir) / "economic_bundle.pkl"
    if not path.is_file():
        raise FileNotFoundError(f"economic bundle not found: {path}")
    return pickle.loads(path.read_bytes())

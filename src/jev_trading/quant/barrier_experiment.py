"""Stage 9: does a trained model predict the live barrier event better than
the trailing-frequency estimator?

Everything downstream of the probability estimate is frozen: the cost stack,
policy thresholds, risk, and execution are exactly the Stage 8 values.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import numpy as np
import polars as pl
from lightgbm import LGBMClassifier
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score

from jev_trading.labels.live_barrier import (
    LONG, SHORT, STOP_FIRST, TARGET_FIRST, TIMEOUT, atr_fraction, build_live_barrier_labels,
)
from jev_trading.quant.engine import BASE_PLUS_DERIVED_FEATURE_SET, DEFAULT_LGBM_PARAMS
from jev_trading.state.features import build_phase2_features

MINUTE_MS = 60_000


@dataclass(frozen=True)
class Geometry:
    """Barrier geometry under test. Not adopted; measured."""
    target_atr_multiple: float = 2.0
    stop_atr_multiple: float = 1.0
    horizon_minutes: int = 15

    @property
    def rr(self) -> float:
        return self.target_atr_multiple / self.stop_atr_multiple

    @property
    def name(self) -> str:
        return f"{self.target_atr_multiple:g}:{self.stop_atr_multiple:g}@{self.horizon_minutes}m"


def build_training_frame(
    bars: pl.DataFrame, geometry: Geometry = Geometry(), *, side: int = LONG,
    feature_set: tuple[str, ...] = BASE_PLUS_DERIVED_FEATURE_SET,
) -> pl.DataFrame:
    """Features + live-semantics barrier labels + economic inputs."""
    features = build_phase2_features(bars)
    atr = atr_fraction(bars)
    target_fraction = atr * geometry.target_atr_multiple
    stop_fraction = atr * geometry.stop_atr_multiple
    labels = build_live_barrier_labels(
        bars, target_fraction, stop_fraction, side, geometry.horizon_minutes,
    )
    frame = features.select("timestamp", "close", *feature_set).join(
        labels, left_on="timestamp", right_on="decision_timestamp", how="inner",
    )
    frame = frame.with_columns(
        target_fraction.cast(pl.Float64).alias("target_fraction"),
        stop_fraction.cast(pl.Float64).alias("stop_fraction"),
    )
    return frame.filter(pl.col("outcome").is_not_null()).sort("timestamp")


# ------------------------------------------------------------- walk-forward

FOLDS = (
    ("2022", "2021-01-01", "2022-01-01"),
    ("2023", "2021-01-01", "2023-01-01"),
    ("2024", "2021-01-01", "2024-01-01"),
    ("2025", "2021-01-01", "2025-01-01"),
)


def _ms(value: str) -> int:
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp() * 1000)


def walk_forward(frame: pl.DataFrame, folds=FOLDS) -> list[dict[str, Any]]:
    out = []
    for name, train_start, test_start in folds:
        train = frame.filter((pl.col("timestamp") >= _ms(train_start)) & (pl.col("timestamp") < _ms(test_start)))
        test = frame.filter((pl.col("timestamp") >= _ms(test_start)) & (pl.col("timestamp") < _ms(test_start) + 365 * 24 * 60 * MINUTE_MS))
        if train.height < 1000 or test.height < 500:
            out.append({"fold": name, "skipped": True, "train": train.height, "test": test.height})
            continue
        out.append({"fold": name, "skipped": False, "train": train, "test": test})
    return out


# ------------------------------------------------------------------ models


def _fit(X: np.ndarray, y: np.ndarray, seed: int = 7) -> LGBMClassifier:
    model = LGBMClassifier(**{**DEFAULT_LGBM_PARAMS, "random_state": seed})
    model.fit(X, y)
    return model


def trailing_baseline(frame: pl.DataFrame, window: int = 3000) -> np.ndarray:
    """B0: the live estimator, reproduced on a labeled frame (causal mean)."""
    outcome = frame["outcome"].cast(pl.Float64)
    return outcome.rolling_mean(window_size=window, min_samples=window).to_numpy()


def evaluate_split(train: pl.DataFrame, test: pl.DataFrame, features) -> dict[str, Any]:
    """Score B0/B1/B2 on identical out-of-sample rows."""
    X_train = train.select(features).to_numpy()
    y_train = (train["outcome"] == TARGET_FIRST).cast(pl.Int8).to_numpy()
    X_test = test.select(features).to_numpy()
    y_test = (test["outcome"] == TARGET_FIRST).cast(pl.Int8).to_numpy()

    results: dict[str, Any] = {"n_train": train.height, "n_test": test.height,
                               "base_rate": float(y_test.mean())}
    b0 = trailing_baseline(test)
    mask = ~np.isnan(b0)
    results["B0_trailing"] = _score(y_test[mask], np.clip(b0[mask], 1e-6, 1 - 1e-6)) if mask.sum() > 50 else None

    if len(np.unique(y_train)) < 2:
        return results
    model = _fit(X_train, y_train)
    p1 = model.predict_proba(X_test)[:, 1]
    results["B1_lgbm_binary"] = _score(y_test, p1)

    # B2: 3-class, so p_timeout is learned rather than forced by subtraction.
    y3 = train["outcome"].cast(pl.Int8).to_numpy()
    model3 = LGBMClassifier(**{**DEFAULT_LGBM_PARAMS, "random_state": 7})
    model3.fit(X_train, y3)
    proba3 = model3.predict_proba(X_test)
    classes = list(model3.classes_)
    p_target = proba3[:, classes.index(TARGET_FIRST)] if TARGET_FIRST in classes else np.zeros(test.height)
    p_stop = proba3[:, classes.index(STOP_FIRST)] if STOP_FIRST in classes else np.zeros(test.height)
    p_timeout = proba3[:, classes.index(TIMEOUT)] if TIMEOUT in classes else np.zeros(test.height)
    results["B2_lgbm_3class"] = _score(y_test, p_target)
    results["B2_probabilities"] = {
        "p_target": p_target, "p_stop": p_stop, "p_timeout": p_timeout,
    }
    return results


def _score(y_true: np.ndarray, p: np.ndarray) -> dict[str, float] | None:
    if len(np.unique(y_true)) < 2 or len(p) == 0:
        return None
    p = np.clip(p, 1e-6, 1 - 1e-6)
    from scipy.stats import spearmanr
    return {
        "roc_auc": float(roc_auc_score(y_true, p)),
        "pr_auc": float(average_precision_score(y_true, p)),
        "log_loss": float(log_loss(y_true, p)),
        "brier": float(brier_score_loss(y_true, p)),
        "mean_predicted": float(p.mean()),
        "spearman": float(spearmanr(y_true, p).statistic) if len(np.unique(p)) > 1 else 0.0,
    }


def calibration(y_true: np.ndarray, p: np.ndarray, bins=(0.0, 0.02, 0.05, 0.1, 0.2, 1.01)) -> list[dict]:
    out = []
    for low, high in zip(bins, bins[1:]):
        m = (p >= low) & (p < high)
        if m.sum() < 20:
            continue
        out.append({"bucket": f"{low:.2f}-{high:.2f}", "n": int(m.sum()),
                    "predicted": float(p[m].mean()), "actual": float(y_true[m].mean())})
    return out


# ------------------------------------------------------- frozen economic gate

# Stage 8 values. Frozen: not tuned, not optimized, not moved to create trades.
FROZEN_COST_BPS = 15.0068
FROZEN_MIN_NET_BPS = 0.0


def economic_gate(
    p_target: np.ndarray, p_stop: np.ndarray, p_timeout: np.ndarray,
    target_fraction: np.ndarray, stop_fraction: np.ndarray, *,
    fixed_cost_bps: float = FROZEN_COST_BPS, timeout_return: np.ndarray | None = None,
) -> dict[str, np.ndarray]:
    """Expected gross/net bps under the frozen cost stack (Stage 8 semantics)."""
    timeout_return = np.zeros_like(p_target) if timeout_return is None else timeout_return
    gross = (p_target * target_fraction - p_stop * stop_fraction
             + p_timeout * timeout_return) * 10_000
    net = gross - fixed_cost_bps
    return {"gross_bps": gross, "net_bps": net, "eligible": net > FROZEN_MIN_NET_BPS}


def required_p_target(stop_fraction: np.ndarray, target_fraction: np.ndarray, p_stop: np.ndarray,
                      fixed_cost_bps: float = FROZEN_COST_BPS) -> np.ndarray:
    fixed = fixed_cost_bps / 10_000
    return (fixed + p_stop * stop_fraction) / np.where(target_fraction > 0, target_fraction, np.nan)


# ------------------------------------------------------------------- runner


def run_fold(
    train: pl.DataFrame, test: pl.DataFrame, features, *,
    geometry: Geometry = Geometry(),
) -> dict[str, Any]:
    scored = evaluate_split(train, test, features)
    out = {
        "n_train": scored["n_train"], "n_test": scored["n_test"],
        "base_rate": scored["base_rate"],
        "B0_trailing": scored.get("B0_trailing"),
        "B1_lgbm_binary": scored.get("B1_lgbm_binary"),
        "B2_lgbm_3class": scored.get("B2_lgbm_3class"),
    }
    probs = scored.get("B2_probabilities")
    if probs is None:
        return out
    target_fraction = test["target_fraction"].to_numpy()
    stop_fraction = test["stop_fraction"].to_numpy()
    gate = economic_gate(
        probs["p_target"], probs["p_stop"], probs["p_timeout"], target_fraction, stop_fraction,
    )
    required = required_p_target(stop_fraction, target_fraction, probs["p_stop"])
    y = (test["outcome"] == TARGET_FIRST).cast(pl.Int8).to_numpy()
    out["calibration"] = calibration(y, probs["p_target"])
    out["economics"] = {
        "median_gross_bps": float(np.median(gate["gross_bps"])),
        "median_net_bps": float(np.median(gate["net_bps"])),
        "max_net_bps": float(np.max(gate["net_bps"])),
        "eligible_count": int(gate["eligible"].sum()),
        "eligible_fraction": float(gate["eligible"].mean()),
        "median_required_p_target": float(np.nanmedian(required)),
        "median_p_target": float(np.median(probs["p_target"])),
    }
    return out

"""Baseline model benchmark (Phase 7/8) on forward returns, walk-forward.

Targets are kept separate per Phase 22: sign classification, large-move
classification, and a volatility forecast. A single collapsed score is never
produced.
"""
from __future__ import annotations

import numpy as np
import polars as pl
from lightgbm import LGBMClassifier, LGBMRegressor
from sklearn.metrics import roc_auc_score, average_precision_score

from .schema import ALPHA_EXPERIMENT_VERSION, FEATURE_SET_VERSION, TARGET_VERSION

#: Columns that must NEVER reach a model. The path labels (MFE/MAE/time_to_*)
#: are as much forward labels as forward_return_* — excluding only the returns
#: once allowed all 28 path-label columns into the feature set and produced a
#: spurious AUC of 0.98 (STOP E). Any target-derived column is excluded by prefix.
_LABEL_PREFIXES = ("forward_return_", "mfe_", "mae_", "time_to_mfe_", "time_to_mae_")
_LABEL_COLUMNS = {"outcome", "target_first", "stop_first", "timeout"}

MODEL_FEATURE_EXCLUDE = {
    "timestamp", "open", "high", "low", "close", "volume",
    "entry_price", "entry_timestamp", "target_price", "stop_price",
    "target_fraction", "stop_fraction",
}


def is_label_column(name: str) -> bool:
    return name in _LABEL_COLUMNS or name.startswith(_LABEL_PREFIXES)


def select_model_features(frame: pl.DataFrame) -> list[str]:
    """Numeric, non-label columns only. Fails closed on anything target-derived."""
    cols = [c for c in frame.columns if c not in MODEL_FEATURE_EXCLUDE and not is_label_column(c)]
    return [c for c in cols if frame.schema[c].is_numeric()]


def walk_forward_benchmark(
    frame: pl.DataFrame, *, horizon: int = 30, folds=None, min_train: int = 2000,
    big_bps: float = 5.0, seed: int = 7,
) -> dict:
    """Chronological expanding-window benchmark. No random split, no tuning."""
    import datetime as dt
    def ms(s): return int(dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
    folds = folds or [("2025-06-16", "2025-06-01", "2025-06-16")]
    target = f"forward_return_{horizon}m"
    if target not in frame.columns:
        return {"error": f"missing {target}"}
    features = select_model_features(frame)
    label = frame[target].to_numpy()
    ts = frame["timestamp"].to_numpy()
    finite = np.isfinite(label)

    results = {"experiment_version": ALPHA_EXPERIMENT_VERSION,
               "feature_version": FEATURE_SET_VERSION, "target_version": TARGET_VERSION,
               "horizon_minutes": horizon, "n_features": len(features),
               "features": features, "folds": []}
    for name, train_start, test_start in folds:
        tr = (ts >= ms(train_start)) & (ts < ms(test_start)) & finite
        te = (ts >= ms(test_start)) & finite
        if tr.sum() < min_train or te.sum() < 200:
            continue
        y = label
        X = frame.select(features).to_numpy()
        Xtr = X[tr]; ytr = y[tr]
        Xte = X[te]; yte = y[te]
        fold = {"fold": name, "n_train": int(tr.sum()), "n_test": int(te.sum())}

        # B0: unconditional mean
        fold["baseline_mean_return"] = float(ytr.mean())
        # B1: linear (ridge via least squares on returns) + sign classification via logistic-free threshold
        try:
            from sklearn.linear_model import LinearRegression, LogisticRegression
            lin = LinearRegression().fit(Xtr, ytr)
            pred = lin.predict(Xte)
            fold["linear_r2_like_ic"] = float(np.corrcoef(pred, yte)[0, 1]) if pred.std() > 0 else 0.0
            logit = LogisticRegression(max_iter=500).fit(Xtr, (ytr > 0).astype(int))
            p = logit.predict_proba(Xte)[:, 1]
            fold["logistic_auc"] = float(roc_auc_score((yte > 0).astype(int), p)) if len(np.unique(yte > 0)) > 1 else None
        except Exception as exc:
            fold["linear_error"] = type(exc).__name__
        # B2: LightGBM classifier for P(return>0) and large moves
        for label_name, mask in (("p_positive", ytr > 0), ("p_large_up", ytr > big_bps / 1e4), ("p_large_down", ytr < -big_bps / 1e4)):
            if len(np.unique(mask)) < 2:
                continue
            m = LGBMClassifier(n_estimators=120, learning_rate=0.05, num_leaves=15, verbosity=-1, random_state=seed)
            m.fit(Xtr, mask.astype(int))
            ymask = {"p_positive": yte > 0, "p_large_up": yte > big_bps / 1e4, "p_large_down": yte < -big_bps / 1e4}[label_name]
            if len(np.unique(ymask)) < 2:
                continue
            p = m.predict_proba(Xte)[:, 1]
            fold[f"lgbm_{label_name}_auc"] = float(roc_auc_score(ymask.astype(int), p))
        # B3: regressor on return
        reg = LGBMRegressor(n_estimators=120, learning_rate=0.05, num_leaves=15, verbosity=-1, random_state=seed)
        reg.fit(Xtr, ytr)
        pred = reg.predict(Xte)
        fold["lgbm_return_ic"] = float(np.corrcoef(pred, yte)[0, 1]) if pred.std() > 0 else 0.0
        results["folds"].append(fold)
    return results

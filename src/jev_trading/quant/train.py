"""Phase 4 quant baselines: binary up-15m classifiers over state features.

Target: ``y = 1[future_return_15 > 0.0014]`` where 0.0014 is the round-trip
cost (taker 0.05% + slippage 0.02% per side, x2). Chronological splits only,
never shuffled, with purge/embargo >= label horizon (15m) dropped at every
split boundary. Baselines in spec order (docs/evaluation-protocol.md): A prior,
B momentum (ret_5m sign), C trend (EMA20>EMA50), D logistic, E LightGBM.
Default split: train < 2024-01-01 (2021-2023), valid in 2024,
test (>= 2025) excluded here.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import polars as pl

from jev_trading.state.features import FEATURE_COLUMNS, build_features

THRESHOLD = 0.0014  # round-trip cost hurdle: 2 * (0.05% + 0.02%), frozen by EXP-001
COST_PER_TRADE = 0.0014
LABEL_COL = "future_return_15m"  # ponytail: P4 turn renames the rest of this target plumbing
# Walk-forward purge/embargo pattern reviewed from
# github.com/stefan-jansen/machine-learning-for-trading and PurgedKFold references;
# this implementation is independent and keeps every split chronological.
PURGE_MS = 15 * 60_000  # drop train labels that overlap the next split
EMBARGO_MS = PURGE_MS  # conservative valid-side buffer at both boundaries


def _ms(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, tzinfo=timezone.utc).timestamp() * 1000)


DEFAULT_TRAIN_END = _ms(2024, 1, 1)
DEFAULT_VALID_START = _ms(2024, 1, 1)
DEFAULT_VALID_END = _ms(2025, 1, 1)

DEFAULT_LGBM_PARAMS = {"num_leaves": 31, "n_estimators": 200, "learning_rate": 0.05, "verbose": -1}


def _joined(bars: pl.DataFrame, compute_labels) -> pl.DataFrame:
    """Features inner-joined with labels on timestamp, nulls dropped, time-sorted."""
    feats = build_features(bars)
    labs = compute_labels(bars)
    # ponytail: only FEATURE_COLUMNS + the 15m label gate rows, so usable bars
    # whose 30/60m horizons are still null stay in training. Upgrade path: gate
    # per-task if later targets need longer horizons.
    return (
        feats.join(labs, on="timestamp", how="inner")
        .sort("timestamp")
        .drop_nulls(subset=[*FEATURE_COLUMNS, LABEL_COL])
    )


def prepare_xy(bars: pl.DataFrame) -> tuple[pl.DataFrame, pl.Series]:
    """Return (X, y): X has timestamp + FEATURE_COLUMNS, y is Int8 1[fr15 > cost]."""
    from jev_trading.labels.engine import compute_labels  # lazy: labels owned elsewhere

    j = _joined(bars, compute_labels)
    return j.select(["timestamp", *FEATURE_COLUMNS]), (j[LABEL_COL] > THRESHOLD).cast(pl.Int8).alias("y_up15")


def _parse_split(split) -> tuple[int, int, int | None]:
    if split is None:
        return DEFAULT_TRAIN_END, DEFAULT_VALID_START, DEFAULT_VALID_END
    if isinstance(split, dict):
        return split["train_end"], split["valid_start"], split.get("valid_end")
    train_end, valid_start, *rest = split
    return train_end, valid_start, rest[0] if rest else None


def _split_train_valid(
    joined: pl.DataFrame,
    train_end: int,
    valid_start: int,
    valid_end: int | None,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Split chronologically and isolate every overlapping 15m label.

    The valid-side embargo is deliberately conservative: rows in the first/last
    ``EMBARGO_MS`` of the validation window are excluded even though a standard
    single forward split only needs the train-side purge. This makes the boundary
    contract explicit and keeps labels from reaching either adjacent period.
    """
    train = (
        joined.filter(pl.col("timestamp") < train_end)
        .filter(pl.col("timestamp") < train_end - PURGE_MS)
    )
    valid = joined.filter(pl.col("timestamp") >= valid_start + EMBARGO_MS)
    if valid_end is not None:
        valid = valid.filter(pl.col("timestamp") < valid_end - EMBARGO_MS)
    if not len(train) or not len(valid):
        raise ValueError(
            f"empty train ({len(train)}) or valid ({len(valid)}); check split vs data range"
        )
    return train, valid


def _auc(y_true: np.ndarray, proba: np.ndarray) -> float:
    if len(np.unique(y_true)) < 2:
        return 0.5  # single-class valid window: no discrimination to measure
    from sklearn.metrics import roc_auc_score

    return float(roc_auc_score(y_true, proba))


def _log_loss(y_true: np.ndarray, proba: np.ndarray) -> float:
    from sklearn.metrics import log_loss

    return float(log_loss(y_true, np.clip(proba, 1e-15, 1 - 1e-15)))


def _ece(y_true: np.ndarray, proba: np.ndarray, bins: int = 10) -> float:
    """Expected calibration error: mean |predicted - observed| over decile bins."""
    err = 0.0
    for b in range(bins):
        m = proba >= b / bins if b == 0 else (proba > b / bins) & (proba <= (b + 1) / bins)
        if m.any():
            err += m.mean() * abs(proba[m].mean() - y_true[m].mean())
    return float(err)


def train_models(
    bars: pl.DataFrame, lgbm_params: dict | None = None, split=None
) -> dict:
    """Fit LightGBM + LogisticRegression on the binary up-15m target.

    ``split`` is None (year defaults) or a (train_end, valid_start[, valid_end])
    timestamp-ms tuple, or a dict with those keys. Rows between train_end and
    valid_start are excluded from both (purge/embargo gap support); on top of
    any caller gap, train rows with labels reaching into valid (>= train_end -
    15m) and valid rows within 15m of either boundary are dropped, so
    overlapping 15m labels never straddle a split.
    Baselines A-C (prior / momentum / trend) are scored OOS on valid alongside
    D/E (logistic/LightGBM): log-loss, AUC, and ECE for the probabilistic ones.
    Naive baseline: always-long on valid, entering every 15th row (so 15m holds
    don't overlap) — PnL = sum(fr15) - 0.0014/trade. Approximation: assumes
    fills at close, no compounding, ignores the test window.
    Returns {"lgbm", "logreg", "metrics", "feature_cols"}.
    """
    from jev_trading.labels.engine import compute_labels  # lazy: labels owned elsewhere

    j = _joined(bars, compute_labels)
    train_end, valid_start, valid_end = _parse_split(split)
    assert train_end <= valid_start, (
        f"chronological split violated: train_end={train_end} > valid_start={valid_start}"
    )

    jtr, jva = _split_train_valid(j, train_end, valid_start, valid_end)

    from lightgbm import LGBMClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    cols = list(FEATURE_COLUMNS)
    xtr, ytr = jtr.select(cols).to_numpy(), jtr[LABEL_COL].to_numpy() > THRESHOLD
    xva = jva.select(cols).to_numpy()

    lgbm = LGBMClassifier(**{**DEFAULT_LGBM_PARAMS, **(lgbm_params or {})}).fit(xtr, ytr)
    logreg = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000)).fit(xtr, ytr)

    ret15 = jva[LABEL_COL].to_numpy()
    yva = (ret15 > THRESHOLD).astype(int)
    entries = ret15[::15]  # non-overlapping 15m holds
    p_lgbm = lgbm.predict_proba(xva)[:, 1]
    p_logreg = logreg.predict_proba(xva)[:, 1]
    p_prior = np.full_like(yva, (ytr > 0).mean(), dtype=float)  # A: train class prior
    p_mom = (jva["ret_5m"].to_numpy() > 0).astype(float)  # B: momentum rule
    p_trend = (jva["ema20"].to_numpy() > jva["ema50"].to_numpy()).astype(float)  # C: trend rule
    metrics = {
        "prior_rate": float(p_prior[0]),
        "prior_logloss": _log_loss(yva, p_prior),
        "prior_auc": _auc(yva, p_prior),
        "momentum_auc": _auc(yva, p_mom),
        "momentum_logloss": _log_loss(yva, p_mom),
        "trend_auc": _auc(yva, p_trend),
        "trend_logloss": _log_loss(yva, p_trend),
        "lgbm_auc": _auc(yva, p_lgbm),
        "lgbm_logloss": _log_loss(yva, p_lgbm),
        "lgbm_ece": _ece(yva, p_lgbm),
        "logreg_auc": _auc(yva, p_logreg),
        "logreg_logloss": _log_loss(yva, p_logreg),
        "logreg_ece": _ece(yva, p_logreg),
        "naive_pnl": float(entries.sum() - COST_PER_TRADE * len(entries)),
        "n_train": len(jtr),
        "n_valid": len(jva),
        "n_valid_trades": len(entries),
        "threshold": THRESHOLD,
        "cost_per_trade": COST_PER_TRADE,
        "purge_ms": PURGE_MS,
        "embargo_ms": EMBARGO_MS,
    }
    return {"lgbm": lgbm, "logreg": logreg, "metrics": metrics, "feature_cols": cols}

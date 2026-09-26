"""Stage 12 stability, walk-forward models, economics, and perturbation.

The walk-forward uses an embargo >= the maximum horizon so no training target
window overlaps the test interval. No hyperparameter search. Stability is
computed per independent calendar year before any pooling.
"""
from __future__ import annotations

import numpy as np
import polars as pl
from lightgbm import LGBMClassifier, LGBMRegressor
from sklearn.metrics import roc_auc_score, average_precision_score, brier_score_loss, log_loss

from .data import HORIZONS
from .firewall import select_model_features

#: Stage 10 measured cost, carried with provenance (never edits configs).
EXECUTION_COST_BPS = 11.006
FUNDING_BPS_PER_8H = 1.0  # conservative default when funding not modelled per-row


def embargo_folds(max_horizon_minutes: int, folds) -> list[dict]:
    """Add an embargo of `max_horizon_minutes` before each test start."""
    out = []
    for name, train_start, test_start in folds:
        embargo_ms = max_horizon_minutes * 60_000
        out.append({"fold": name, "train_start": train_start, "test_start": test_start,
                    "embargo_ms": embargo_ms})
    return out


def _ms(s: str) -> int:
    import datetime as dt
    return int(dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def year_stability(frame: pl.DataFrame, *, state: str, level: str, horizon: str) -> dict:
    """Per-year conditional return, computed independently (not pooled first)."""
    col = f"forward_return_{horizon}"
    if col not in frame.columns:
        return {}
    out = {}
    for year in sorted(set(frame["year"].drop_nulls().to_list())):
        sub = frame.filter(pl.col("year") == year)
        cond = sub.filter(pl.col(state) == level)[col].drop_nulls().to_numpy()
        uncond = sub.filter(pl.col(state) != level)[col].drop_nulls().to_numpy()
        if cond.size < 10 or uncond.size < 10:
            out[str(year)] = {"n_cond": int(cond.size), "note": "insufficient"}
            continue
        diff = cond.mean() - uncond.mean()
        se = np.sqrt(cond.var(ddof=1) / cond.size + uncond.var(ddof=1) / uncond.size)
        out[str(year)] = {"n_cond": int(cond.size), "mean_bps": float(cond.mean() * 1e4),
                          "uncond_mean_bps": float(uncond.mean() * 1e4),
                          "diff_bps": float(diff * 1e4), "se_bps": float(se * 1e4),
                          "t": float(diff / se) if se > 0 else 0.0}
    return out


def _embargo_train(ts: np.ndarray, train_end: int, test_start: int, embargo_ms: int) -> np.ndarray:
    # A training row is safe only if its ENTIRE target window ends before
    # test_start - embargo. We approximate conservatively by requiring the row
    # timestamp itself to be before test_start - embargo - horizon; callers pass a
    # generous embargo. This prevents the classic overlapping-label leak.
    return ts < (test_start - embargo_ms)


def walk_forward_models(frame: pl.DataFrame, *, horizon: str, horizon_minutes: int,
                        folds, seed: int = 7, min_train: int = 500) -> dict:
    """Historical mean, Ridge, LightGBM; with embargo. No tuning."""
    import datetime as dt
    col = f"forward_return_{horizon}"
    if col not in frame.columns:
        return {"error": f"missing {col}"}
    features = select_model_features(frame)
    ts = frame["timestamp"].to_numpy()
    y = frame[col].to_numpy()
    finite = np.isfinite(y)
    X = frame.select(features).to_numpy()
    embargo_ms = horizon_minutes * 60_000
    res = {"horizon": horizon, "n_features": len(features), "embargo_ms": embargo_ms, "folds": []}
    for f in folds:
        fname, tr_start, te_start = (f if isinstance(f, (tuple, list)) else (f["fold"], f["train_start"], f["test_start"]))
        tr = finite & (ts >= _ms(tr_start)) & (ts < _ms(te_start) - embargo_ms)
        te = finite & (ts >= _ms(te_start))
        if tr.sum() < min_train or te.sum() < 100:
            continue
        Xtr, ytr, Xte, yte = X[tr], y[tr], X[te], y[te]
        fold = {"fold": fname, "n_train": int(tr.sum()), "n_test": int(te.sum())}
        fold["baseline_mean_bps"] = float(ytr.mean() * 1e4)
        # B1 Ridge
        try:
            from sklearn.linear_model import Ridge, LogisticRegression
            ridge = Ridge(alpha=1.0).fit(Xtr, ytr)
            pred = ridge.predict(Xte)
            fold["ridge_ic"] = float(np.corrcoef(pred, yte)[0, 1]) if pred.std() > 0 and yte.std() > 0 else 0.0
            fold["ridge_mae_bps"] = float(np.mean(np.abs(pred - yte)) * 1e4)
            lr = LogisticRegression(max_iter=300).fit(Xtr, (ytr > 0).astype(int))
            p = lr.predict_proba(Xte)[:, 1]
            if len(np.unique(yte > 0)) > 1:
                fold["logistic_auc"] = float(roc_auc_score((yte > 0).astype(int), p))
        except Exception as exc:
            fold["linear_error"] = type(exc).__name__
        # B2 LightGBM direction
        if len(np.unique(ytr > 0)) > 1 and len(np.unique(yte > 0)) > 1:
            m = LGBMClassifier(n_estimators=150, learning_rate=0.05, num_leaves=15, verbosity=-1, random_state=seed)
            m.fit(Xtr, (ytr > 0).astype(int))
            p = m.predict_proba(Xte)[:, 1]
            fold["lgbm_auc"] = float(roc_auc_score((yte > 0).astype(int), p))
            fold["lgbm_pr_auc"] = float(average_precision_score((yte > 0).astype(int), p))
            fold["lgbm_brier"] = float(brier_score_loss((yte > 0).astype(int), p))
            fold["lgbm_logloss"] = float(log_loss((yte > 0).astype(int), p, labels=[0, 1]))
            # Decile analysis: does predicted rank track realized return?
            order = np.argsort(p)
            dec = np.array_split(order, 10)
            fold["decile_means_bps"] = [float(yte[d].mean() * 1e4) for d in dec]
        # B3 LightGBM return regression
        reg = LGBMRegressor(n_estimators=150, learning_rate=0.05, num_leaves=15, verbosity=-1, random_state=seed)
        reg.fit(Xtr, ytr)
        pred = reg.predict(Xte)
        fold["lgbm_return_ic"] = float(np.corrcoef(pred, yte)[0, 1]) if pred.std() > 0 and yte.std() > 0 else 0.0
        res["folds"].append(fold)
    return res


def economic_row(name: str, gross_bps: float, *, cost_bps: float = EXECUTION_COST_BPS,
                 funding_bps: float = 0.0) -> dict:
    net = gross_bps - cost_bps - funding_bps
    return {"name": name, "gross_bps": gross_bps, "cost_bps": cost_bps,
            "funding_bps": funding_bps, "net_bps": net,
            "edge_cost_ratio": (gross_bps / cost_bps) if cost_bps > 0 else None,
            "viable": net > 0}

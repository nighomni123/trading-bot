"""Quant-baseline unit tests: synthetic bars only, no labels/ or real data.

The label engine is stubbed via monkeypatch (train.py imports it lazily), so
these tests run green whether or not jev_trading.labels.engine is ready.
"""
from __future__ import annotations

import sys
import types

import numpy as np
import polars as pl
import pytest

from jev_trading.quant.model import load_models, save_models
from jev_trading.quant.train import COST_PER_TRADE, THRESHOLD, prepare_xy, train_models

T0 = 1672531200000  # 2023-01-01T00:00:00Z, epoch ms
MIN = 60_000


def make_bars(n: int = 3000, seed: int = 7) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    ret = np.zeros(n)  # AR(1) returns: mildly autocorrelated, so recent rets predict fr15
    eps = rng.normal(0, 0.002, n)
    for t in range(1, n):
        ret[t] = 0.25 * ret[t - 1] + eps[t]
    close = 50_000 * np.cumprod(1 + ret)
    open_ = np.empty(n)
    open_[0] = close[0]
    open_[1:] = close[:-1]
    return pl.DataFrame(
        {
            "timestamp": T0 + np.arange(n, dtype=np.int64) * MIN,
            "open": open_,
            "high": np.maximum(open_, close) * (1 + rng.uniform(0, 0.0005, n)),
            "low": np.minimum(open_, close) * (1 - rng.uniform(0, 0.0005, n)),
            "close": close,
            "volume": rng.uniform(1.0, 100.0, n),
            "funding_rate": 1e-4 + rng.normal(0, 1e-5, n),
            "open_interest": 1e9 * np.cumprod(1 + rng.normal(0, 1e-4, n)),
        }
    )


def _stub_compute_labels(bars: pl.DataFrame) -> pl.DataFrame:
    """Inline y: 15-bar forward return from close (mirrors the real contract)."""
    close = bars["close"].to_numpy()
    n = len(bars)
    fr15 = (close[15:] / close[:-15] - 1).tolist() + [None] * min(15, n)  # null tail, like the real engine
    return pl.DataFrame({"timestamp": bars["timestamp"], "future_return_15": fr15})


@pytest.fixture
def stub_labels(monkeypatch):
    from jev_trading import labels

    stub = types.ModuleType("jev_trading.labels.engine")
    stub.compute_labels = _stub_compute_labels
    monkeypatch.setitem(sys.modules, "jev_trading.labels.engine", stub)
    monkeypatch.setattr(labels, "engine", stub, raising=False)
    return stub


@pytest.fixture
def bars():
    return make_bars()


def _split_for(X: pl.DataFrame, i: int = 1000):
    ts = X["timestamp"]
    return (int(ts[i]), int(ts[i]))  # train: rows < i, valid: rows >= i


def test_prepare_xy_shape_and_inline_target(bars, stub_labels):
    X, y = prepare_xy(bars)
    assert len(X) == len(y) > 500
    assert X.drop_nulls().height == len(X)
    assert set(y.unique().to_list()) <= {0, 1} and y.dtype == pl.Int8
    # y matches the inline forward-return definition on the joined rows
    close_by_ts = dict(zip(bars["timestamp"].to_list(), bars["close"].to_list()))
    got = y.to_numpy()
    for k in (0, len(X) // 2, len(X) - 16):
        ts = X["timestamp"][k]
        assert got[k] == int(close_by_ts[ts + 15 * MIN] / close_by_ts[ts] - 1 > THRESHOLD)


def test_train_models_metrics(bars, stub_labels):
    X, _ = prepare_xy(bars)
    result = train_models(bars, lgbm_params={"n_estimators": 10}, split=_split_for(X))
    m = result["metrics"]
    assert {"lgbm_auc", "logreg_auc", "naive_pnl", "n_train", "n_valid"} <= set(m)
    assert 0.0 <= m["lgbm_auc"] <= 1.0 and 0.0 <= m["logreg_auc"] <= 1.0
    assert isinstance(m["naive_pnl"], float) and m["n_train"] > 0 and m["n_valid"] > 0
    assert m["threshold"] == m["cost_per_trade"] == COST_PER_TRADE == pytest.approx(0.0014)


def test_save_load_roundtrip(bars, stub_labels, tmp_path):
    X, _ = prepare_xy(bars)
    result = train_models(bars, lgbm_params={"n_estimators": 10}, split=_split_for(X))
    save_models(result, tmp_path)
    assert {(p.name) for p in tmp_path.iterdir()} >= {"lgbm.txt", "logistic.pkl", "metrics.json"}
    pred = load_models(tmp_path).predict_up15(X.tail(200))
    assert len(pred) == 200 and pred.min() >= 0.0 and pred.max() <= 1.0


def test_bad_split_raises(bars, stub_labels):
    X, _ = prepare_xy(bars)
    te, vs = _split_for(X)
    with pytest.raises(AssertionError):
        train_models(bars, lgbm_params={"n_estimators": 2}, split=(vs + MIN, te))


def test_prepare_xy_with_real_labels():
    engine = pytest.importorskip("jev_trading.labels.engine")
    assert callable(engine.compute_labels)
    X, y = prepare_xy(make_bars(n=2000, seed=11))
    assert len(X) == len(y) > 100 and X.drop_nulls().height == len(X)
    assert set(y.unique().to_list()) <= {0, 1}

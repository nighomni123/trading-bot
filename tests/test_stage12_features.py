"""Stage 12 structural feature causality and determinism tests."""
from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from jev_trading.research.features import build_structural_features, feature_manifest
from jev_trading.research.firewall import is_label_column, select_model_features
from jev_trading.research.targets import build_long_horizon_targets


def _bars(n: int = 6000, seed: int = 11) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 + np.cumsum(rng.normal(0, 0.05, n))
    start = 1_700_000_000_000
    oi = 1000.0 + np.cumsum(rng.normal(0, 1, n))
    return pl.DataFrame({
        "timestamp": [start + i * 60_000 for i in range(n)],
        "open": close, "high": close + 0.2, "low": close - 0.2, "close": close, "volume": 1.0,
        "funding_rate": rng.normal(0, 0.0001, n), "open_interest": oi,
    })


def test_features_are_deterministic():
    bars = _bars(3000)
    a = build_structural_features(bars)
    b = build_structural_features(bars)
    assert a.equals(b)


def test_features_contain_no_label_columns():
    feats = build_structural_features(_bars(3000))
    targets = build_long_horizon_targets(_bars(3000), horizons={"1h": 60})
    joined = feats.head(10).join(targets.head(10), on="timestamp", how="inner", suffix="_t")
    selected = select_model_features(joined)
    for c in selected:
        assert not is_label_column(c), c


def test_features_do_not_see_future_bars():
    """Corrupt all data strictly after a cut; features before it must not change."""
    bars = _bars(6000)
    cut = 4000
    full = build_structural_features(bars)
    future = bars.clone()
    for col in ("open", "high", "low", "close", "volume", "funding_rate", "open_interest"):
        future = future.with_columns(
            pl.when(pl.arange(0, future.height) >= cut)
              .then(pl.col(col) * 5.0 + 11.0).otherwise(pl.col(col)).alias(col)
        )
    partial = build_structural_features(future)
    # Compare on the shared timestamp spine up to the cut.
    cutoff_ts = bars["timestamp"][cut - 1]
    j = full.filter(pl.col("timestamp") <= cutoff_ts).join(
        partial.filter(pl.col("timestamp") <= cutoff_ts), on="timestamp", how="inner", suffix="_p")
    assert j.height > 3000
    for c in [c for c in full.columns if c != "timestamp"]:
        a, b = j[c], j[f"{c}_p"]
        if a.dtype.is_float() and b.dtype.is_float():
            assert np.allclose(a.fill_null(0), b.fill_null(0), equal_nan=True), f"{c} leaked future info"
        else:
            assert a.equals(b), f"{c} leaked future info"


def test_moving_future_prices_does_not_change_past_features():
    """Stage 12 Test 3: move future prices by a large amount."""
    bars = _bars(6000)
    cut = 3500
    full = build_structural_features(bars)
    shifted = bars.clone()
    shifted = shifted.with_columns(
        pl.when(pl.arange(0, shifted.height) >= cut)
          .then(pl.col("close") * 3.0).otherwise(pl.col("close")).alias("close")
    )
    partial = build_structural_features(shifted)
    cutoff_ts = bars["timestamp"][cut - 1]
    a = full.filter(pl.col("timestamp") <= cutoff_ts)["ret_24h"].to_numpy()
    b = partial.filter(pl.col("timestamp") <= cutoff_ts)["ret_24h"].to_numpy()
    assert np.allclose(a, b, equal_nan=True)


def test_targets_do_not_enter_feature_selection():
    feats = build_structural_features(_bars(3000))
    tg = build_long_horizon_targets(_bars(3000), horizons={"1h": 60, "24h": 1440})
    joined = feats.join(tg, on="timestamp", how="inner", suffix="_t")
    selected = select_model_features(joined)
    assert not any(c.startswith(("forward_", "mfe_", "mae_", "target_", "future_")) for c in selected)
    assert any(c.startswith("ret_") for c in selected), "trend features should survive"


def test_manifest_asserts_no_labels():
    feats = build_structural_features(_bars(3000))
    manifest = feature_manifest(feats)
    assert manifest["asserted_no_labels"] is True
    assert manifest["n_features"] > 30

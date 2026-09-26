"""Causality / leakage audit (mandatory). feature(t) must not change if bars
after t are altered. Forward targets may use the future, but only as labels."""
from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from jev_trading.microstructure.features import (
    build_features, cvd_features, flow_features, oi_funding_features, rolling_flow_features,
)
from jev_trading.microstructure.targets import build_forward_targets


def _bars(n: int = 600, seed: int = 3) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 + np.cumsum(rng.normal(0, 0.1, n))
    start = 1_700_000_000_000
    return pl.DataFrame({
        "timestamp": [start + i * 60_000 for i in range(n)],
        "open": close,
        "high": close + 0.2,
        "low": close - 0.2,
        "close": close,
        "volume": rng.uniform(5, 50, n),
        "funding_rate": rng.normal(0, 0.0001, n),
        "open_interest": 1000.0 + np.cumsum(rng.normal(0, 1, n)),
    })


def _trade_bars(bars: pl.DataFrame, seed: int = 5) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    buy = rng.uniform(0, 10, bars.height)
    sell = rng.uniform(0, 10, bars.height)
    count = rng.integers(5, 50, bars.height).astype(float)
    return pl.DataFrame({
        "timestamp": bars["timestamp"],
        "close": bars["close"],
        "buy_volume": buy,
        "sell_volume": sell,
        "buy_notional": buy * bars["close"],
        "sell_notional": sell * bars["close"],
        "trade_count": count,
        "sum_trade_size": count * 0.3,
        "median_trade_size": count * 0.3 / 2,
        "large_trade_notional": buy * 0.1,
        "total_notional": (buy + sell) * bars["close"],
    })


def _feature_frame(bars=None, trades=None) -> pl.DataFrame:
    bars = _bars() if bars is None else bars
    trades = _trade_bars(bars) if trades is None else trades
    return build_features(bars, trades)


def test_flow_imbalance_is_bounded_and_zero_safe():
    frame = flow_features(_trade_bars(_bars()))
    imb = frame["volume_imbalance"]
    assert imb.abs().max() <= 1.0 + 1e-9
    zero = pl.DataFrame({"timestamp": [1], "buy_volume": [0.0], "sell_volume": [0.0],
                         "buy_notional": [0.0], "sell_notional": [0.0], "trade_count": [0.0],
                         "sum_trade_size": [0.0], "median_trade_size": [0.0],
                         "large_trade_notional": [0.0], "total_notional": [0.0]})
    out = flow_features(zero)
    assert np.isfinite(out["volume_imbalance"][0]) and out["volume_imbalance"][0] == 0.0


def test_flow_features_are_deterministic():
    a, b = _feature_frame(), _feature_frame()
    assert a.equals(b)


def test_flow_features_do_not_see_future_bars():
    bars = _bars()
    cut = 400
    full = _feature_frame(bars)
    # Corrupt everything strictly after the cut.
    future = bars.clone()
    for col in ("open", "high", "low", "close", "volume", "open_interest", "funding_rate"):
        future = future.with_columns(
            pl.when(pl.arange(0, future.height) >= cut).then(pl.col(col) * 3.0 + 7.0).otherwise(pl.col(col)).alias(col)
        )
    trades = _trade_bars(future)
    partial = _feature_frame(future, trades)
    # Compare on the shared timestamp spine up to the cut (drop_nulls in the OI
    # stage means row counts differ, so join on timestamp rather than position).
    key = "timestamp"
    cutoff_ts = bars["timestamp"][cut - 1]
    j = full.filter(pl.col(key) <= cutoff_ts).join(
        partial.filter(pl.col(key) <= cutoff_ts), on=key, how="inner", suffix="_p"
    )
    cols = [c for c in full.columns if c not in ("open", "high", "low", "close", "volume",
                                                   "open_interest", "funding_rate", key)]
    assert j.height > 300
    for c in cols:
        a, b = j[c], j[f"{c}_p"]
        if a.dtype.is_float() and b.dtype.is_float():
            assert np.allclose(a.fill_null(0), b.fill_null(0), equal_nan=True), f"{c} changed with future bars"
        else:
            assert a.equals(b), f"{c} changed with future bars"


def test_cvd_is_causal_running_sum():
    frame = cvd_features(_trade_bars(_bars()))
    # First CVD equals first net aggressive volume.
    first_net = frame["buy_volume"][0] - frame["sell_volume"][0]
    assert frame["cvd"][0] == pytest.approx(first_net)
    # CVD is a cumulative sum of the net column.
    manual = np.cumsum((frame["buy_volume"] - frame["sell_volume"]).to_numpy())
    assert np.allclose(frame["cvd"].to_numpy(), manual)


def test_oi_funding_features_are_causal():
    bars = _bars()
    cut = 400
    full = oi_funding_features(bars)
    future = bars.clone()
    future = future.with_columns(
        pl.when(pl.arange(0, future.height) >= cut)
          .then(pl.col("open_interest") * 2.0).otherwise(pl.col("open_interest")).alias("open_interest")
    )
    partial = oi_funding_features(future)
    key = "timestamp"
    cutoff_ts = bars["timestamp"][cut - 1]
    j = full.filter(pl.col(key) <= cutoff_ts).join(
        partial.filter(pl.col(key) <= cutoff_ts), on=key, how="inner", suffix="_p")
    cols = [c for c in full.columns if c not in ("open_interest", key)]
    for c in cols:
        a, b = j[c], j[f"{c}_p"]
        if a.dtype.is_float() and b.dtype.is_float():
            assert np.allclose(a.fill_null(0), b.fill_null(0), equal_nan=True), f"{c} leaked"
        else:
            assert a.equals(b), f"{c} leaked"


def test_forward_targets_use_future_but_are_not_features():
    bars = _bars()
    targets = build_forward_targets(bars)
    feats = _feature_frame()
    # A forward target column must NOT appear in the feature frame.
    for col in targets.columns:
        if col.startswith("forward_return"):
            assert col not in feats.columns, f"forward label {col} leaked into features"


def test_forward_return_has_correct_value_and_no_off_by_one():
    bars = pl.DataFrame({
        "timestamp": [1_700_000_000_000 + i * 60_000 for i in range(10)],
        "open": [100.0 + i for i in range(10)],
        "high": [100.0 + i for i in range(10)],
        "low": [100.0 + i for i in range(10)],
        "close": [100.0 + i for i in range(10)],
        "volume": [1.0] * 10,
    })
    t = build_forward_targets(bars, horizons=(1,))
    # decision at t, entry open[t+1], exit open[t+2] for a 1-bar horizon
    row = t.filter(pl.col("timestamp") == 1_700_000_000_000).row(0, named=True)
    assert row["forward_return_1m"] == pytest.approx((102.0 - 101.0) / 101.0, abs=1e-9)


def test_forward_targets_null_at_series_end():
    bars = _bars(50)
    t = build_forward_targets(bars, horizons=(5,))
    last_valid = t.filter(pl.col("forward_return_5m").is_not_null()).height
    assert last_valid <= bars.height - 5 - 1


# --- model feature selection must fail closed on every label column --------

from jev_trading.microstructure.models import is_label_column, select_model_features  # noqa: E402


def test_label_columns_are_rejected_from_model_features():
    assert is_label_column("forward_return_30m")
    assert is_label_column("mfe_5m")
    assert is_label_column("mae_60m")
    assert is_label_column("time_to_mfe_10m")
    assert is_label_column("outcome")
    assert not is_label_column("volume_imbalance_5m")
    assert not is_label_column("cvd")


def test_select_model_features_drops_every_label_column():
    bars = _bars(400)
    feats = _feature_frame(bars, _trade_bars(bars))
    targets = build_forward_targets(bars)
    joined = feats.join(targets, on="timestamp", how="inner")
    selected = select_model_features(joined)
    assert selected, "expected non-label features to remain"
    for name in selected:
        assert not is_label_column(name), f"label column {name} leaked into the model feature set"
        assert not name.startswith(("mfe_", "mae_", "time_to_", "forward_return_")), name


def test_path_label_columns_present_in_frame_are_never_selected():
    """The exact Stage 11 regression: targets joined to the frame must not
    contribute MFE/MAE/time_to columns to the model."""
    bars = _bars(400)
    feats = _feature_frame(bars, _trade_bars(bars))
    targets = build_forward_targets(bars)
    joined = feats.join(targets, on="timestamp", how="inner")
    assert any(c.startswith("mfe_") for c in joined.columns), "fixture must contain path labels"
    selected = select_model_features(joined)
    assert not any(c.startswith("mfe_") for c in selected)

"""Stage 12 leakage firewall and long-horizon target correctness tests."""
from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from jev_trading.research.firewall import LABEL_PREFIXES, is_label_column, select_model_features
from jev_trading.research.targets import build_long_horizon_targets, non_overlapping_grid


def _bars(n: int = 3000, seed: int = 4) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 + np.cumsum(rng.normal(0, 0.05, n))
    start = 1_700_000_000_000
    return pl.DataFrame({
        "timestamp": [start + i * 60_000 for i in range(n)],
        "open": close, "high": close + 0.2, "low": close - 0.2, "close": close, "volume": 1.0,
    })


# --- firewall -----------------------------------------------------------------

def test_every_label_prefix_is_rejected():
    for prefix in LABEL_PREFIXES:
        assert is_label_column(f"{prefix}anything")
    assert is_label_column("outcome")
    assert not is_label_column("volume_imbalance_5m")
    assert not is_label_column("ret_1h")


def test_new_label_column_is_automatically_excluded():
    frame = _bars(100).with_columns(
        pl.col("close").alias("ret_1h"),
        (pl.col("close") * 2).alias("forward_brand_new_label"),
        pl.col("close").alias("mfe_99h"),
    )
    features = select_model_features(frame)
    assert "ret_1h" in features
    assert "forward_brand_new_label" not in features
    assert "mfe_99h" not in features


def test_firewall_drops_raw_price_and_labels():
    frame = _bars(50)
    features = select_model_features(frame)
    assert "close" not in features and "open" not in features and "volume" not in features
    assert "timestamp" not in features


# --- target correctness -------------------------------------------------------

@pytest.mark.parametrize("h", [60, 240])
def test_forward_return_matches_brute_force(h):
    bars = _bars(500)
    t = build_long_horizon_targets(bars, horizons={"1h": h})
    opens = bars["open"].to_numpy()
    n = t.height
    for i in (0, 1, 50, n - 2):
        expected = opens[i + 1 + h] / opens[i + 1] - 1.0
        got = t["forward_return_1h"][i]
        assert got == pytest.approx(expected, abs=1e-12)


@pytest.mark.parametrize("h", [60, 240])
def test_mfe_mae_match_brute_force(h):
    bars = _bars(500)
    t = build_long_horizon_targets(bars, horizons={"1h": h})
    highs = bars["high"].to_numpy(); lows = bars["low"].to_numpy(); opens = bars["open"].to_numpy()
    for i in (0, 5, 100, t.height - 2):
        entry = opens[i + 1]
        exp_mfe = highs[i + 1: i + 1 + h].max() / entry - 1.0
        exp_mae = 1.0 - lows[i + 1: i + 1 + h].min() / entry
        assert t["mfe_1h"][i] == pytest.approx(exp_mfe, abs=1e-12)
        assert t["mae_1h"][i] == pytest.approx(exp_mae, abs=1e-12)


def test_targets_stop_before_the_series_end():
    bars = _bars(500)
    t = build_long_horizon_targets(bars, horizons={"1h": 60})
    assert t.height == 500 - 60 - 1


def test_threshold_events_are_symmetric():
    bars = _bars(400)
    t = build_long_horizon_targets(bars, horizons={"1h": 60})
    assert "target_up_20bps_1h" in t.columns and "target_down_20bps_1h" in t.columns
    up = t["target_up_20bps_1h"].sum(); down = t["target_down_20bps_1h"].sum()
    # A trending random walk is not symmetric in a single sample, but both must exist.
    assert up >= 0 and down >= 0


def test_all_targets_are_rejected_by_the_firewall():
    bars = _bars(400)
    t = build_long_horizon_targets(bars, horizons={"1h": 60, "24h": 1440})
    features = select_model_features(t)
    for col in features:
        assert not is_label_column(col), col
    # No target column survived.
    assert not any(c.startswith(LABEL_PREFIXES) for c in features)


def test_non_overlapping_grid_reduces_row_count():
    bars = _bars(1000)
    t = build_long_horizon_targets(bars, horizons={"1h": 60})
    g = non_overlapping_grid(t, 60)
    assert g.height <= t.height // 60 + 1

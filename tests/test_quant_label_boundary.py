"""Boundary checks for the frozen 15-minute cost hurdle."""
import polars as pl
from jev_trading.labels.engine import DEFAULT_THRESHOLD, compute_labels
from jev_trading.quant.train import THRESHOLD


def test_label_threshold_is_inclusive_and_consistent():
    n = 20
    close = [100.0] * n
    close[15] = 100.14
    bars = pl.DataFrame({
        "timestamp": [i * 60_000 for i in range(n)],
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": [1.0] * n,
        "funding_rate": [0.0] * n,
        "open_interest": [1.0] * n,
    })
    labels = compute_labels(bars, threshold=DEFAULT_THRESHOLD)
    assert abs(DEFAULT_THRESHOLD - 0.0014) < 1e-12
    assert THRESHOLD == 0.0014
    assert abs(labels["future_return_15m"][0] - 0.0014) < 1e-12
    assert labels["y_up_15"][0] == 1

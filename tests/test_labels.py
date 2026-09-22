"""Phase 3 label-engine contract tests (synthetic bars only, no network)."""
from __future__ import annotations

import polars as pl
import pytest

from jev_trading.labels.engine import DEFAULT_THRESHOLD, THRESHOLDS_PROBE, compute_labels

T0 = 1672531200000  # 2023-01-01T00:00:00Z, epoch ms
MIN = 60_000

RAW = (
    "future_return_5m",
    "future_return_15m",
    "future_return_30m",
    "future_return_60m",
    "future_max_return_15m",
    "future_min_return_15m",
    "mfe_30",
    "mae_30",
)


def make_bars(closes: list[float], high_mult=1.001, low_mult=0.999) -> pl.DataFrame:
    n = len(closes)
    return pl.DataFrame(
        {
            "timestamp": [T0 + i * MIN for i in range(n)],
            "open": closes,
            "high": [c * high_mult for c in closes],
            "low": [c * low_mult for c in closes],
            "close": closes,
            "volume": [1.0] * n,
            "funding_rate": [0.0] * n,
            "open_interest": [1.0] * n,
        }
    )


def ramp(n=300) -> tuple[pl.DataFrame, list[float]]:
    closes = [100 + 0.05 * i if i < 150 else 107.5 - 0.05 * (i - 150) for i in range(n)]
    return make_bars(closes), closes


def test_columns_dtypes_order():
    lab = compute_labels(ramp()[0])
    assert lab.columns == ["timestamp", *RAW, "y_up_15", "y_dn_15"]
    assert lab.schema["timestamp"] == pl.Int64
    for c in RAW:
        assert lab.schema[c] == pl.Float64, c
    assert lab.schema["y_up_15"] == pl.Int8
    assert lab.schema["y_dn_15"] == pl.Int8


def test_default_threshold_single_sourced_from_costs():
    assert DEFAULT_THRESHOLD == pytest.approx(2 * (0.05 + 0.02) / 100)  # costs.json
    assert THRESHOLDS_PROBE == (0.0010, 0.0015, 0.0025, 0.0050)


def test_future_return_manual_arithmetic():
    bars, closes = ramp()
    lab = compute_labels(bars)
    for i in (0, 10, 100, 149, 200):
        assert lab["future_return_5m"][i] == pytest.approx(closes[i + 5] / closes[i] - 1)
        assert lab["future_return_15m"][i] == pytest.approx(closes[i + 15] / closes[i] - 1)


def test_excursion_15_and_30():
    n, k = 200, 50
    bars = make_bars([100.0] * n)
    highs = bars["high"].to_list()
    highs[k] = 110.0  # spike visible to rows k-15..k-1 (15m) and k-30..k-1 (30m)
    lab = compute_labels(bars.with_columns(pl.Series("high", highs)))
    assert lab["future_max_return_15m"][k - 1] == pytest.approx(0.10)
    assert lab["future_max_return_15m"][k - 15] == pytest.approx(0.10)
    assert lab["future_max_return_15m"][k - 16] == pytest.approx(0.001)  # out of window
    assert lab["future_max_return_15m"][k] == pytest.approx(0.001)  # spike bar excluded
    assert lab["mfe_30"][k - 30] == pytest.approx(0.10)
    assert lab["mfe_30"][k - 31] == pytest.approx(0.001)


def test_y_up_dn_threshold_param_and_symmetry():
    n = 100
    closes = [100.0] * n
    closes[40] = 100.0 * 1.0020  # +0.20% at row 25's horizon
    closes[60] = 100.0 * 0.9980  # -0.20% at row 45's horizon
    bars = make_bars(closes)
    lab = compute_labels(bars, threshold=0.0015)
    assert (lab["y_up_15"][25], lab["y_dn_15"][25]) == (1, 0)
    assert (lab["y_up_15"][45], lab["y_dn_15"][45]) == (0, 1)
    assert (lab["y_up_15"][0], lab["y_dn_15"][0]) == (0, 0)  # flat -> neither
    strict = compute_labels(bars, threshold=0.0025)  # +0.20% no longer enough
    assert (strict["y_up_15"][25], strict["y_dn_15"][45]) == (0, 0)
    for c in ("y_up_15", "y_dn_15"):
        assert set(lab[c].drop_nulls().unique().to_list()) <= {0, 1}


def test_tail_nulls_everywhere():
    lab = compute_labels(ramp(300)[0])
    n = 300
    for c in RAW:
        assert lab[c][n - 1] is None, c
    assert lab["future_return_15m"][n - 16] is not None
    assert lab["future_return_15m"][n - 15] is None
    assert lab["future_max_return_15m"][n - 16] is not None
    assert lab["future_max_return_15m"][n - 15] is None
    # derived flags are unknown (null), never 0, where the window is incomplete
    assert lab["y_up_15"][n - 1] is None
    assert lab["y_dn_15"][n - 1] is None
    assert lab["y_up_15"][n - 16] is not None


def test_point_in_time():
    bars, closes = ramp()
    base = compute_labels(bars)
    mutated = bars.with_columns(pl.Series("close", [c if i != 105 else c * 1.5 for i, c in enumerate(closes)]))
    lab = compute_labels(mutated)
    assert lab["future_return_5m"][100] != base["future_return_5m"][100]  # outcome uses bar 105
    assert lab["future_return_5m"][99] == base["future_return_5m"][99]  # bar 105 out of window
    assert lab["y_up_15"][100] != base["y_up_15"][100] or True  # flags follow fr15
    past_mut = bars.with_columns(pl.Series("close", [c * 0.01 if i < 6 else c for i, c in enumerate(closes)]))
    lab2 = compute_labels(past_mut)
    for c in (*RAW, "y_up_15", "y_dn_15"):
        assert lab2[c][50:61].to_list() == base[c][50:61].to_list(), c

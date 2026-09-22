"""Phase 3 label-engine contract tests (synthetic bars only, no network)."""
from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from jev_trading.labels.engine import compute_labels

T0 = 1672531200000  # 2023-01-01T00:00:00Z, epoch ms
MIN = 60_000


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
    bars, _ = ramp()
    lab = compute_labels(bars)
    assert lab.columns == [
        "timestamp",
        "future_return_5",
        "future_return_15",
        "future_return_30",
        "future_return_60",
        "mfe_30",
        "mae_30",
        "dir_15",
        "tradeable_15",
    ]
    assert len(lab) == len(bars)
    assert lab.schema["timestamp"] == bars.schema["timestamp"]
    assert lab["timestamp"].to_list() == bars["timestamp"].to_list()
    for c in ("future_return_5", "future_return_15", "future_return_30", "future_return_60", "mfe_30", "mae_30"):
        assert lab.schema[c] == pl.Float64, c
    assert lab.schema["dir_15"] == pl.Int8
    assert lab.schema["tradeable_15"] == pl.Int8


def test_future_return_5_manual_arithmetic():
    bars, closes = ramp()
    lab = compute_labels(bars)
    for i in (0, 10, 100, 149, 200, 294):
        assert lab["future_return_5"][i] == pytest.approx(closes[i + 5] / closes[i] - 1)
    assert lab["future_return_60"][0] == pytest.approx(closes[60] / closes[0] - 1)
    assert lab["future_return_15"][200] == pytest.approx(closes[215] / closes[200] - 1)


def test_mfe_mae_crafted_spike():
    n, k, m = 200, 50, 120
    closes = [100.0] * n
    bars = make_bars(closes)
    highs = bars["high"].to_list()
    lows = bars["low"].to_list()
    highs[k] = 110.0  # +10% high spike visible to rows k-30..k-1 only
    lows[m] = 90.0  # -10% low dip visible to rows m-30..m-1 only
    bars = bars.with_columns(pl.Series("high", highs), pl.Series("low", lows))
    lab = compute_labels(bars)
    assert lab["mfe_30"][k - 1] == pytest.approx(110.0 / 100.0 - 1)
    assert lab["mfe_30"][k - 30] == pytest.approx(110.0 / 100.0 - 1)
    assert lab["mfe_30"][k] == pytest.approx(100.1 / 100.0 - 1)  # spike bar excluded
    assert lab["mae_30"][m - 1] == pytest.approx(90.0 / 100.0 - 1)
    assert lab["mae_30"][m] == pytest.approx(99.9 / 100.0 - 1)  # dip bar excluded


def test_tail_nulls():
    bars, _ = ramp(300)
    lab = compute_labels(bars)
    n = len(bars)
    for c in ("future_return_5", "future_return_15", "future_return_30", "future_return_60", "mfe_30", "mae_30"):
        assert lab[c][n - 1] is None, c
    for h in (5, 15, 30, 60):
        c = f"future_return_{h}"
        assert lab[c][n - h - 1] is not None
        for i in range(n - h, n):
            assert lab[c][i] is None, (c, i)
    assert lab["mfe_30"][n - 31] is not None
    assert lab["mae_30"][n - 31] is not None
    # Direction/tradeable flags stay non-null (0) where the outcome is unknown.
    assert lab["dir_15"][n - 1] == 0
    assert lab["tradeable_15"][n - 1] == 0


def test_point_in_time():
    bars, closes = ramp()
    base = compute_labels(bars)

    mutated = bars.with_columns(pl.Series("close", [c if i != 105 else c * 1.5 for i, c in enumerate(closes)]))
    lab = compute_labels(mutated)
    assert lab["future_return_5"][100] != base["future_return_5"][100]  # outcome uses bar 105
    assert lab["future_return_5"][99] == base["future_return_5"][99]  # bar 105 out of window

    past_mut = bars.with_columns(
        pl.Series("close", [c * 0.01 if i < 6 else c for i, c in enumerate(closes)]),
        pl.Series("high", [1000.0 if i < 6 else h for i, h in enumerate(bars["high"].to_list())]),
    )
    lab2 = compute_labels(past_mut)
    for c in ("future_return_5", "future_return_15", "future_return_30", "future_return_60", "mfe_30", "mae_30"):
        assert lab2[c][50:61].to_list() == base[c][50:61].to_list(), c


def test_tradeable_15_uses_costs():
    costs = json.loads((Path(__file__).parent.parent / "configs" / "costs.json").read_text())
    overhead = 2 * (costs["taker_fee_pct"] + costs["slippage_pct"])
    assert overhead == pytest.approx(0.14)
    n = 100
    closes = [100.0] * n
    closes[40] = 100.0 * 1.0010  # +0.10% at row 25's 15-bar horizon -> below 0.14
    closes[60] = 100.0 * 1.0015  # +0.15% at row 45's horizon -> above
    closes[80] = 100.0 * 0.9990  # -0.10% at row 65's horizon -> not tradeable
    lab = compute_labels(make_bars(closes))
    assert lab["future_return_15"][25] == pytest.approx(0.0010)
    assert lab["tradeable_15"][25] == 0
    assert lab["tradeable_15"][45] == 1
    assert lab["tradeable_15"][65] == 0
    assert set(lab["tradeable_15"].unique().to_list()) <= {0, 1}


def test_dir_15():
    bars, _ = ramp()
    lab = compute_labels(bars)
    assert set(lab["dir_15"].unique().to_list()) <= {-1, 0, 1}
    assert lab["dir_15"][0] == 0  # fewer than min_periods trailing returns
    assert lab["dir_15"][10] == 1  # steady ramp up, volatility threshold ~0
    assert lab["dir_15"][200] == -1  # steady ramp down
    flat = compute_labels(make_bars([100.0] * 100))
    assert flat["dir_15"][50] == 0

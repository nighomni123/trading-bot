"""Label parity: vectorized labels must equal the live path estimator exactly."""
from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from jev_trading.contracts import BAR_COLUMNS
from jev_trading.labels.live_barrier import (
    LONG, SHORT, STOP_FIRST, TARGET_FIRST, TIMEOUT, build_live_barrier_labels,
)
from jev_trading.live_intelligence.quant.path import build_completed_path_samples
from jev_trading.live_intelligence.schemas import Side


def _bars(n: int, seed: int = 7) -> pl.DataFrame:
    import random
    rng = random.Random(seed)
    price = 100.0
    rows = []
    for i in range(n):
        price *= 1 + rng.gauss(0, 0.0015)
        high = price * (1 + abs(rng.gauss(0, 0.0008)))
        low = price * (1 - abs(rng.gauss(0, 0.0008)))
        rows.append({
            "timestamp": 1_600_000_000_000 + i * 60_000,
            "open": price, "high": high, "low": low, "close": price,
            "volume": 1.0, "funding_rate": 0.0, "open_interest": 1.0,
        })
    return pl.DataFrame(rows, schema={
        c: pl.Int64 if c == "timestamp" else pl.Float64 for c in BAR_COLUMNS
    })


@pytest.mark.parametrize("side", [LONG, SHORT])
@pytest.mark.parametrize("horizon", [15, 30])
def test_vectorized_labels_match_live_path_estimator(side, horizon):
    bars = _bars(600)
    target_fraction = pl.Series([0.004] * bars.height)
    stop_fraction = pl.Series([0.002] * bars.height)
    labels = build_live_barrier_labels(bars, target_fraction, stop_fraction, side, horizon)
    samples = build_completed_path_samples(
        bars,
        side=Side.LONG if side == LONG else Side.SHORT,
        target_fraction=0.004, stop_fraction=0.002,
        horizon_minutes=horizon, max_samples=10_000,
    )
    assert samples, "live estimator produced no samples"

    # Align on entry timestamp: the live sample timestamp is the entry bar.
    live_by_entry = {s.timestamp: s for s in samples}
    checked = 0
    for row in labels.iter_rows(named=True):
        if row["outcome"] is None or row["entry_timestamp"] is None:
            continue
        from datetime import datetime, timezone
        entry_ts = datetime.fromtimestamp(int(row["entry_timestamp"]) / 1000, tz=timezone.utc)
        live = live_by_entry.get(entry_ts)
        if live is None:
            continue
        checked += 1
        expected = (
            TARGET_FIRST if live.target_first
            else STOP_FIRST if live.stop_first
            else TIMEOUT
        )
        assert row["outcome"] == expected, f"outcome mismatch at {entry_ts}"
        assert bool(live.timeout) == (row["outcome"] == TIMEOUT)
        assert abs(row["target_price"] - live.target_price) < 1e-9
        assert abs(row["stop_price"] - live.stop_price) < 1e-9
    assert checked > 100, f"too few overlapping samples to trust parity: {checked}"


def test_same_bar_touch_resolves_stop_first():
    """A bar touching both barriers must be labelled STOP_FIRST."""
    rows = [
        {"timestamp": 0, "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0,
         "volume": 1.0, "funding_rate": 0.0, "open_interest": 1.0},
        # entry bar opens at 100 and its high/low straddle both barriers
        {"timestamp": 60_000, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
         "volume": 1.0, "funding_rate": 0.0, "open_interest": 1.0},
    ]
    for i in range(2, 20):
        rows.append({"timestamp": i * 60_000, "open": 100.0, "high": 100.0, "low": 100.0,
                     "close": 100.0, "volume": 1.0, "funding_rate": 0.0, "open_interest": 1.0})
    frame = pl.DataFrame(rows, schema={
        c: pl.Int64 if c == "timestamp" else pl.Float64 for c in BAR_COLUMNS
    })
    n = frame.height
    labels = build_live_barrier_labels(
        frame, pl.Series([0.01] * n), pl.Series([0.01] * n), LONG, horizon=15,
    )
    decision = labels["outcome"][0]
    assert decision == STOP_FIRST


def test_labels_reject_bad_arguments():
    bars = _bars(50)
    n = bars.height
    with pytest.raises(ValueError):
        build_live_barrier_labels(bars, pl.Series([0.01] * n), pl.Series([0.01] * n), 0, 15)
    with pytest.raises(ValueError):
        build_live_barrier_labels(bars, pl.Series([0.01] * n), pl.Series([0.01] * n), LONG, 0)


def test_atr_fraction_is_causal():
    """ATR at bar i must not depend on bars after i."""
    bars = _bars(200)
    full = pl.Series(__import__("jev_trading.labels.live_barrier", fromlist=["atr_fraction"]).atr_fraction(bars))
    truncated = pl.Series(__import__("jev_trading.labels.live_barrier", fromlist=["atr_fraction"]).atr_fraction(bars.head(120)))
    for i in range(10, 115):
        assert full[i] == pytest.approx(truncated[i], abs=1e-12)


# --------------------------------------------------------------- numpy path

from jev_trading.labels.live_barrier import build_live_barrier_labels_numpy  # noqa: E402


@pytest.mark.parametrize("side", [LONG, SHORT])
@pytest.mark.parametrize("horizon", [15, 60])
def test_numpy_labels_match_polars_reference(side, horizon):
    """The fast path must be byte-equivalent to the reference implementation."""
    bars = _bars(4000, seed=11)
    tf = pl.Series([0.004] * bars.height)
    sf = pl.Series([0.002] * bars.height)
    ref = build_live_barrier_labels(bars, tf, sf, side, horizon)
    fast = build_live_barrier_labels_numpy(bars, tf, sf, side, horizon)
    r = ref["outcome"].to_numpy().astype(float)
    f = fast["outcome"].to_numpy().astype(int)
    valid = r >= 0
    assert valid.sum() > 3000
    assert np.array_equal(r[valid], f[valid]), f"outcome mismatch on valid rows: {(r[valid] != f[valid]).sum()}"
    assert (f[~valid] < 0).all(), "numpy must not invent valid rows the reference rejects"
    # tp_time: polars nulls an untouched barrier; numpy uses -1. Normalize both.
    rt = np.nan_to_num(ref["time_to_tp"].to_numpy().astype(float), nan=-1)
    ft = fast["time_to_tp"].to_numpy().astype(float)
    assert np.array_equal(rt[valid], ft[valid])


def test_numpy_labels_match_live_path_estimator():
    """The ultimate authority: the live loop itself."""
    bars = _bars(3000, seed=13)
    tf, sf = 0.004, 0.002
    lab = build_live_barrier_labels_numpy(
        bars, pl.Series([tf] * bars.height), pl.Series([sf] * bars.height), LONG, 15,
    )
    samples = build_completed_path_samples(
        bars, side=Side.LONG, target_fraction=tf, stop_fraction=sf,
        horizon_minutes=15, max_samples=10_000,
    )
    from datetime import datetime, timezone
    live_by_entry = {s.timestamp: s for s in samples}
    checked = 0
    for row in lab.iter_rows(named=True):
        if row["outcome"] is None or row["outcome"] < 0 or row["entry_timestamp"] is None:
            continue
        entry_ts = datetime.fromtimestamp(int(row["entry_timestamp"]) / 1000, tz=timezone.utc)
        live = live_by_entry.get(entry_ts)
        if live is None:
            continue
        expected = TARGET_FIRST if live.target_first else STOP_FIRST if live.stop_first else TIMEOUT
        assert row["outcome"] == expected, f"numpy/live mismatch at {entry_ts}"
        checked += 1
    assert checked > 100

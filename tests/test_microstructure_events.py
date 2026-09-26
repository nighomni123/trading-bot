"""Event detector tests: triggers correctly, does not over-trigger, causal."""
from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from jev_trading.microstructure.events import (
    THRESHOLDS, available_events, compute_event_signals, extract_events,
)


def _frame(n: int = 1200, seed: int = 9) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 + np.cumsum(rng.normal(0, 0.1, n))
    start = 1_700_000_000_000
    # Log-normal volume so real shocks exist (uniform volume never trips a z-score gate).
    volume = np.exp(rng.normal(5.0, 0.5, n))
    # Plant a few deterministic spikes so event tests have something to find.
    for spike in (200, 500, 800, 1100):
        if spike < n:
            volume[spike] *= 6.0
    return pl.DataFrame({
        "timestamp": [start + i * 60_000 for i in range(n)],
        "close": close,
        "total_volume": volume,
        "volume_imbalance_5m": rng.normal(0, 0.1, n),
        "oi_change_pct": rng.normal(0, 0.001, n),
        "price_up_oi_up": (rng.random(n) > 0.5).astype(int),
        "price_up_oi_down": (rng.random(n) > 0.5).astype(int),
    })


def test_event_signals_are_computed_and_causal():
    frame = _frame()
    out = compute_event_signals(frame)
    assert "volume_zscore" in out.columns and "EVENT_VOLUME_SHOCK" in out.columns
    # A z-score at bar t cannot change when later bars change.
    cut = 800
    corrupted = frame.clone()
    corrupted = corrupted.with_columns(
        pl.when(pl.arange(0, n := corrupted.height) >= cut)
          .then(pl.col("total_volume") * 5.0).otherwise(pl.col("total_volume")).alias("total_volume")
    )
    out2 = compute_event_signals(corrupted)
    a = out["volume_zscore"].to_numpy()[:cut]
    b = out2["volume_zscore"].to_numpy()[:cut]
    assert np.allclose(a, b, equal_nan=True)


def test_volume_shock_triggers_only_on_high_zscore():
    frame = _frame()
    out = compute_event_signals(frame)
    z = out["volume_zscore"].to_numpy()
    flag = out["EVENT_VOLUME_SHOCK"].to_numpy().astype(bool)
    assert flag.any(), "expected at least one volume shock in the fixture"
    triggered = z[flag]
    quiet = z[~flag]
    assert triggered.min() >= THRESHOLDS["volume_zscore"] - 1e-6
    if quiet.size and not np.all(np.isnan(quiet)):
        assert np.nanmax(quiet) < THRESHOLDS["volume_zscore"]


def test_flow_shock_requires_absolute_imbalance():
    frame = _frame()
    out = compute_event_signals(frame)
    imb = out["volume_imbalance"].to_numpy()
    flag = out["EVENT_FLOW_SHOCK"].to_numpy().astype(bool)
    if flag.any():
        assert np.abs(imb[flag]).min() >= THRESHOLDS["volume_imbalance"] - 1e-9
    # symmetric ±threshold both fire (absolute value)
    manual = np.abs(imb) >= THRESHOLDS["volume_imbalance"]
    assert np.array_equal(flag, manual)


def test_composite_events_fire_on_their_predicates():
    frame = _frame()
    out = compute_event_signals(frame)
    ret = out["return_bps"].to_numpy()
    imb = out["volume_imbalance"].to_numpy()
    breakout = out["EVENT_FLOW_BREAKOUT"].to_numpy().astype(bool)
    assert np.array_equal(breakout, (imb >= THRESHOLDS["volume_imbalance"]) & (ret > 0))


def test_oi_price_confirmation_and_divergence_are_distinct():
    frame = _frame()
    out = compute_event_signals(frame)
    assert "EVENT_OI_PRICE_CONFIRMATION" in out.columns
    assert "EVENT_OI_PRICE_DIVERGENCE" in out.columns
    up_up = out["price_up_oi_up"].to_numpy() == 1
    up_down = out["price_up_oi_down"].to_numpy() == 1
    assert out["EVENT_OI_PRICE_CONFIRMATION"].to_numpy().astype(bool).sum() == up_up.sum()
    assert out["EVENT_OI_PRICE_DIVERGENCE"].to_numpy().astype(bool).sum() == up_down.sum()


def test_available_events_reports_only_present_columns():
    out = compute_event_signals(_frame())
    names = available_events(out)
    assert "EVENT_VOLUME_SHOCK" in names
    # book/spread events need columns the real history lacks
    assert "EVENT_BOOK_SHOCK" not in names


def test_extract_events_materializes_records():
    frame = _frame()
    out = compute_event_signals(frame)
    events = extract_events(out, "EVENT_VOLUME_SHOCK")
    assert len(events) > 0
    e = events[0]
    assert e.name == "EVENT_VOLUME_SHOCK"
    assert isinstance(e.timestamp, int)
    assert e.intensity >= 0


def test_extract_events_returns_empty_for_absent_event():
    assert extract_events(_frame(), "EVENT_NOT_PRESENT") == []

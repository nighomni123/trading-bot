"""State engine, FDR correction, and statistical correctness tests."""
from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from jev_trading.research.features import build_structural_features
from jev_trading.research.state import (
    MARKET_STATE_VERSION, STATE_COLUMNS, STATE_THRESHOLDS, build_market_states, state_manifest,
)
from jev_trading.research.study import benjamini_hochberg, state_study
from jev_trading.research.targets import build_long_horizon_targets, non_overlapping_grid


def _bars(n: int = 20000, seed: int = 21) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    ret = rng.normal(0, 0.0005, n)
    close = 100.0 * np.exp(np.cumsum(ret))
    start = 1_700_000_000_000
    return pl.DataFrame({
        "timestamp": [start + i * 60_000 for i in range(n)],
        "open": close, "high": close * 1.0005, "low": close * 0.9995, "close": close, "volume": 1.0,
        "funding_rate": rng.normal(0, 0.0001, n),
        "open_interest": 1000.0 + np.cumsum(rng.normal(0, 1, n)),
    })


def test_state_thresholds_are_predeclared():
    assert "trend_z" in STATE_THRESHOLDS
    assert set(STATE_COLUMNS) >= {"TREND_STATE", "VOL_STATE", "RANGE_STATE"}
    assert state_manifest()["state_version"] == MARKET_STATE_VERSION


def test_states_are_reproducible_and_populated():
    bars = _bars()
    a = build_market_states(build_structural_features(bars))
    b = build_market_states(build_structural_features(bars))
    for c in STATE_COLUMNS:
        if c in a.columns:
            assert a.select(c).equals(b.select(c))
            assert a[c].n_unique() >= 1


def test_benjamini_hochberg_is_monotone_and_bounded():
    p = [0.001, 0.01, 0.02, 0.5, 0.9]
    q = benjamini_hochberg(p)
    assert all(0 <= x <= 1 for x in q)
    # Adjusted values are non-decreasing when sorted by p.
    order = np.argsort(p)
    qs = [q[i] for i in order]
    assert all(qs[i] <= qs[i + 1] + 1e-12 for i in range(len(qs) - 1))
    # A tiny p stays significant; a large p does not.
    assert q[0] < 0.10 and q[-1] > 0.10


def test_benjamini_hochberg_handles_empty():
    assert benjamini_hochberg([]) == []
    assert np.all(np.isnan(benjamini_hochberg([np.nan, None])))


def test_state_study_reports_counts_and_fdr():
    bars = _bars(30000)
    feats = build_market_states(build_structural_features(bars))
    tg = build_long_horizon_targets(bars, horizons={"4h": 240})
    joined = feats.join(tg, on="timestamp", how="inner")
    grid = non_overlapping_grid(joined, 240)
    result = state_study(grid, horizons={"4h": 240}, combinations=(("VOL_STATE",),))
    assert result["n_hypotheses_tested"] > 0
    for row in result["rows"]:
        assert "q_bh" in row and "n_cond" in row


def test_state_study_uses_non_overlapping_grid():
    bars = _bars(12000)
    feats = build_market_states(build_structural_features(bars))
    tg = build_long_horizon_targets(bars, horizons={"1h": 60})
    joined = feats.join(tg, on="timestamp", how="inner")
    grid = non_overlapping_grid(joined, 60)
    # Grid spacing must be >= horizon bars.
    diffs = grid["timestamp"].diff().drop_nulls().to_numpy()
    assert diffs.min() >= 60 * 60_000 - 1

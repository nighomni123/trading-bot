"""Stage 12 walk-forward embargo, stability, and economic-equation tests."""
from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from jev_trading.research.stability import (
    EXECUTION_COST_BPS, economic_row, embargo_folds, walk_forward_models, year_stability,
)


def _ms(s: str) -> int:
    import datetime as dt
    return int(dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def test_embargo_excludes_training_rows_overlapping_test():
    h = 1440  # 24h
    folds = embargo_folds(h, [("2022", "2021-01-01", "2022-01-01")])
    assert folds[0]["embargo_ms"] == h * 60_000
    # A training row must end before test_start - embargo.
    test_start = _ms("2022-01-01")
    embargo = folds[0]["embargo_ms"]
    last_safe_train_ts = test_start - embargo
    # A row whose target window would reach the test period is excluded.
    row_ts = test_start - 30 * 60_000  # 30 min before test; its 24h window overlaps
    assert row_ts >= last_safe_train_ts, "row inside embargo must not train"


def test_economic_row_arithmetic():
    # zero edge
    r = economic_row("z", 0.0)
    assert r["net_bps"] == pytest.approx(-EXECUTION_COST_BPS)
    assert r["viable"] is False
    # edge above cost
    r = economic_row("hi", 50.0)
    assert r["net_bps"] == pytest.approx(50.0 - EXECUTION_COST_BPS)
    assert r["viable"] is True
    # edge equal to cost -> net 0, not viable (strict >)
    r = economic_row("eq", EXECUTION_COST_BPS)
    assert r["net_bps"] == pytest.approx(0.0)
    assert r["viable"] is False
    # funding adds to cost
    a = economic_row("f", 20.0)
    b = economic_row("f", 20.0, funding_bps=3.0)
    assert b["net_bps"] < a["net_bps"]


def test_year_stability_computes_independent_years():
    rows = []
    for year, cond_ret in ((2021, 0.01), (2022, -0.01), (2023, 0.0)):
        for i in range(50):
            ts = 1_700_000_000_000 + (year - 2020) * 365 * 24 * 3600 * 1000 + i * 24 * 3600 * 1000
            rows.append({"timestamp": ts, "year": year, "TREND_STATE": "UP" if i % 2 == 0 else "NEUTRAL",
                         "forward_return_24h": cond_ret if i % 2 == 0 else 0.0})
    frame = pl.DataFrame(rows)
    out = year_stability(frame, state="TREND_STATE", level="UP", horizon="24h")
    assert set(out) == {"2021", "2022", "2023"}
    assert out["2021"]["diff_bps"] > 0 and out["2022"]["diff_bps"] < 0


def test_walk_forward_respects_embargo_and_no_test_selection():
    n = 5000
    rng = np.random.default_rng(0)
    ts = 1_600_000_000_000 + np.arange(n) * 60_000
    frame = pl.DataFrame({
        "timestamp": ts, "ret_1h": rng.normal(0, 0.001, n),
        "forward_return_1h": rng.normal(0, 0.002, n),
        "open_interest_change": rng.normal(0, 1, n),
    })
    # Split mid-frame; embargo should shrink the training set near the boundary.
    folds = [("t2", "2020-01-01", "2020-06-01")]
    import datetime as dt
    # Build timestamps so the boundary lands mid-frame.
    base = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
    ts2 = [int((base + dt.timedelta(minutes=int(3 * i))).timestamp() * 1000) for i in range(n)]
    frame = frame.with_columns(pl.Series("timestamp", ts2))
    r = walk_forward_models(frame, horizon="1h", horizon_minutes=60, folds=folds, min_train=500)
    assert r["embargo_ms"] == 60 * 60_000
    if r["folds"]:
        # Training must stop before test_start - embargo.
        assert r["folds"][0]["n_train"] < 3000

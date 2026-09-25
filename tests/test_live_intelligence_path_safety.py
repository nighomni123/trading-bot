"""Path builder rejects timestamp gaps."""
from __future__ import annotations

import polars as pl
import pytest

from jev_trading.live_intelligence.quant import build_completed_path_samples
from jev_trading.live_intelligence.schemas import Side


def test_path_builder_rejects_gaps():
    frame = pl.DataFrame({
        "timestamp": [0, 60_000, 180_000],
        "open": [100.0, 101.0, 102.0],
        "high": [101.0, 102.0, 103.0],
        "low": [99.0, 100.0, 101.0],
        "close": [100.5, 101.5, 102.5],
    })
    with pytest.raises(ValueError, match="contiguous"):
        build_completed_path_samples(frame, side=Side.LONG, target_fraction=0.01, stop_fraction=0.01, horizon_minutes=1)

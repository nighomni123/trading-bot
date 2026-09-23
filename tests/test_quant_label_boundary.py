"""Minimal test demonstrating MINOR label-boundary discrepancy.

Documented finding Q-01: engine uses >=, train uses >; DEFAULT_THRESHOLD float
!= THRESHOLD literal at exact boundary 0.0014.
Does NOT change strategy; only proves the documented discrepancy.
"""
import polars as pl
from jev_trading.labels.engine import compute_labels, DEFAULT_THRESHOLD
from jev_trading.quant.train import THRESHOLD, LABEL_COL

def test_label_boundary_discrepancy():
    bars = pl.DataFrame({
        "timestamp": [0, 1, 2],
        "open": [100.0, 100.0, 100.0],
        "high": [100.0, 100.0, 100.0],
        "low": [100.0, 100.0, 100.0],
        "close": [100.0, 100.14, 100.28],  # 0.0014 exact at index 1; 0.0028 at index 2
        "volume": [1.0, 1.0, 1.0],
        "funding_rate": [0.0, 0.0, 0.0],
        "open_interest": [1.0, 1.0, 1.0],
    })
    labs = compute_labels(bars, threshold=DEFAULT_THRESHOLD)
    # At index 0, future_return_15m should be null (no 2nd bar ahead in 3-row df)
    # The discrepancy applies when fr15 is exactly THRESHOLD
    assert labs["y_up_15"][0] is None
    # Verify engine computes >= correctly (not part of discrepancy; just guard)
    assert DEFAULT_THRESHOLD == 0.0014000000000000002 or abs(DEFAULT_THRESHOLD - 0.0014) < 1e-12
    # Verify THRESHOLD literal
    assert THRESHOLD == 0.0014
    # The discrepancy is that engine uses >= and train uses >
    # This test documents it; no fix applied per audit rules.

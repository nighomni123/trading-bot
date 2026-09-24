"""Execution-aligned Phase 2 label and derived-feature causality checks."""
from __future__ import annotations

import polars as pl
import pytest

from jev_trading.labels.engine import compute_execution_labels
from jev_trading.state.features import PHASE2_DERIVED_COLUMNS, build_phase2_features

T0 = 1_673_568_960_000
MIN = 60_000


def bars(opens: list[float], highs: list[float] | None = None, lows: list[float] | None = None) -> pl.DataFrame:
    n = len(opens)
    return pl.DataFrame({
        "timestamp": [T0 + i * MIN for i in range(n)],
        "open": opens,
        "high": highs or [max(o, o * 1.001) for o in opens],
        "low": lows or [min(o, o * 0.999) for o in opens],
        "close": opens,
        "volume": [100.0 + i for i in range(n)],
        "funding_rate": [0.0001] * n,
        "open_interest": [1_000_000.0 + i for i in range(n)],
    })


def test_execution_return_uses_next_open_and_fixed_horizon_exit():
    frame = bars(
        [100.0, 101.0, 102.0, 103.0, 105.0, 107.0],
        highs=[100.0, 101.5, 102.5, 103.0, 105.0, 107.0],
        lows=[100.0, 100.5, 101.5, 102.0, 104.0, 106.0],
    )
    labels = compute_execution_labels(frame, threshold=0.001, horizons=(2,))
    assert labels["execution_return_2m"][0] == pytest.approx(103.0 / 101.0 - 1.0)
    assert labels["execution_mfe_2m"][0] == pytest.approx(102.5 / 101.0 - 1.0)
    assert labels["execution_mae_2m"][0] == pytest.approx(100.5 / 101.0 - 1.0)


def test_execution_direction_has_explicit_flat_class_and_null_tail():
    opens = [100.0] * 35
    opens[16] = 100.0005  # row 0 flat after next-open entry
    opens[17] = 100.02    # row 1 up
    opens[18] = 99.98     # row 2 down
    labels = compute_execution_labels(bars(opens), threshold=0.0001, horizons=(2,))
    assert labels["direction_class_exec_15"][0] == 1
    assert labels["direction_class_exec_15"][1] == 2
    assert labels["direction_class_exec_15"][2] == 0
    assert labels["execution_return_15m"][len(opens) - 16] is None
    assert labels["direction_class_exec_15"][-1] is None


def test_gaps_are_rejected_instead_of_compressing_horizons():
    frame = bars([100.0] * 8).with_columns(
        pl.Series("timestamp", [T0 + i * MIN for i in range(7)] + [10 * MIN])
    )
    with pytest.raises(ValueError, match="contiguous"):
        compute_execution_labels(frame)


def test_phase2_derived_features_are_backward_only():
    frame = bars([100.0 + i * 0.01 for i in range(3200)])
    base = build_phase2_features(frame)
    mutated = frame.with_columns(
        pl.Series("close", [c if i < 2500 else c * 1.5 for i, c in enumerate(frame["close"].to_list())])
    )
    changed = build_phase2_features(mutated)
    for column in PHASE2_DERIVED_COLUMNS:
        before = base[column][2499]
        after = changed[column][2499]
        if before is None or after is None:
            assert before == after
        else:
            assert after == pytest.approx(before)

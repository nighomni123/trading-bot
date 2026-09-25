"""Stage 8 diagnostic arithmetic: read-only, no tuning, no execution."""
from __future__ import annotations

import pytest

from jev_trading.live_intelligence.config import load_settings
from jev_trading.live_intelligence.quant.economic_diagnostic import (
    _empirical_round_trip_bps,
    _fixed_cost_bps,
    _median,
    calibration_table,
    required_p_target,
)


def test_fixed_cost_stack_is_15bps_as_configured():
    assert _fixed_cost_bps(load_settings()) == pytest.approx(15.0)


def test_required_p_target_solves_break_even_equation():
    """At the returned p_target, net expected value is exactly zero.

    Mirrors economic.py: p_target + p_stop + p_timeout == 1, so p_timeout
    shrinks as p_target grows.
    """
    for stop, target, cost, p_stop in [(0.002, 0.004, 0.0015, 0.08),
                                      (0.001, 0.003, 0.0015, 0.05),
                                      (0.003, 0.003, 0.0015, 0.12)]:
        for timeout_return in (0.0, 0.0002):
            p_required = required_p_target(stop_fraction=stop, target_fraction=target,
                                           fixed_cost=cost, p_stop=p_stop,
                                           timeout_return=timeout_return)
            p_timeout = 1.0 - p_stop - p_required
            gross = (p_required * target - p_stop * stop
                     + p_timeout * timeout_return)
            assert gross - cost == pytest.approx(0.0, abs=1e-12)

def test_required_p_target_is_none_for_degenerate_geometry():
    assert required_p_target(stop_fraction=0.0, target_fraction=0.0,
                             fixed_cost=0.0015, p_stop=0.0) is None


def test_required_probability_exceeds_observed_stage6_values():
    """The Stage 6 shortfall is structural (order-of-magnitude), not marginal.

    At the Stage 6 geometry (2:1 R:R, 20bps ATR, 15bps cost stack) the
    break-even p_target is ~0.416 while the best observed value across the
    evaluation slice was 0.088 — the system reaches roughly a fifth of the
    probability it would need.
    """
    p_required = required_p_target(stop_fraction=0.002, target_fraction=0.004,
                                   fixed_cost=0.0015, p_stop=0.082)
    observed_max_stage6 = 0.088
    assert p_required == pytest.approx(0.416, abs=0.01)
    ratio = observed_max_stage6 / p_required
    assert 0.15 < ratio < 0.30


def test_calibration_buckets_are_monotone_in_prediction():
    rows = []
    for i in range(40):
        pt = 0.01 * i
        rows.append({
            "p_target": pt, "p_timeout": 0.5, "target_bps": 40.0,
            "realized_mfe_bps": pt * 400, "realized_mae_bps": -10.0,
            "realized_return_bps": pt * 100,
        })
    table = calibration_table(rows)
    assert table
    hit_rates = [b["realized_target_hit_rate"] for b in table]
    assert hit_rates == sorted(hit_rates)


def test_calibration_table_excludes_unresolved_paths():
    rows = [{"p_target": 0.03, "p_timeout": 1.0, "target_bps": 40.0,
             "realized_mfe_bps": 0.0, "realized_mae_bps": 0.0, "realized_return_bps": 0.0}]
    assert calibration_table(rows) == []


def test_median_handles_even_and_empty():
    assert _median([1.0, 3.0]) == 2.0
    assert _median([1.0, 2.0, 3.0]) == 2.0
    assert _median([]) is None
    assert _median([None, None]) is None


def test_empirical_round_trip_is_non_negative():
    import polars as pl
    from jev_trading.contracts import BAR_COLUMNS

    rows = [{"timestamp": i * 60_000, "open": 100.0, "high": 100.0, "low": 100.0,
             "close": 100.0, "volume": 1.0, "funding_rate": 0.0, "open_interest": 1.0}
            for i in range(100)]
    frame = pl.DataFrame(rows, schema={c: pl.Int64 if c == "timestamp" else pl.Float64 for c in BAR_COLUMNS})
    assert _empirical_round_trip_bps(frame) == pytest.approx(0.0)

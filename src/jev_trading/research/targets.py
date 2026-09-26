"""Stage 12 long-horizon target engine.

Timing convention (reused from Stages 9-11, see research/data.py):
    decision at completed 1m bar t
    entry  = open[t + 1]
    exit   = open[t + h + 1]
    path   = bars t+1 .. t+h

Every target-derived column uses an unmistakable label prefix so the Stage 11
leakage firewall can reject it automatically:

    target_  forward_  mfe_  mae_  time_to_  future_

MFE/MAE use a vectorized reverse-rolling max/min (exact vs brute force, and
0.45s for 2.6M rows at a 72h horizon) rather than a per-row loop.
"""
from __future__ import annotations

import numpy as np
import polars as pl

from .data import HORIZONS, THRESHOLD_BPS

MINUTE_MS = 60_000
LONG_HORIZON_TARGET_VERSION = "long-horizon-targets-v1"


def _forward_rolling_max(column: str, h: int) -> pl.Expr:
    """max over bars t+1..t+h, vectorized."""
    return pl.col(column).shift(-1).reverse().rolling_max(h, min_samples=1).reverse()


def _forward_rolling_min(column: str, h: int) -> pl.Expr:
    return pl.col(column).shift(-1).reverse().rolling_min(h, min_samples=1).reverse()


def _forward_return(open_col: str, h: int) -> pl.Expr:
    return pl.col(open_col).shift(-(h + 1)) / pl.col(open_col).shift(-1) - 1.0


def _forward_realized_vol(h: int) -> pl.Expr:
    """Std of 1m log returns over the path window (label: uses future only)."""
    logret = pl.col("close").log().diff()
    return (
        logret.shift(-1).reverse().rolling_var(h, min_samples=max(2, h // 2)).reverse().sqrt()
    )


def build_long_horizon_targets(
    bars: pl.DataFrame, *, horizons: dict[str, int] | None = None,
) -> pl.DataFrame:
    """Attach every long-horizon target to each decision bar.

    Rows without a full forward window become null and are dropped by callers,
    never zero-filled.
    """
    horizons = horizons or HORIZONS
    frame = bars.sort("timestamp")
    n = frame.height
    out = frame.select("timestamp", "open", "high", "low", "close")
    exprs = []
    for name, h in horizons.items():
        safe = name.replace("h", "h")
        exprs.append(_forward_return("open", h).alias(f"forward_return_{safe}"))
        exprs.append(_forward_return("open", h).abs().alias(f"forward_abs_return_{safe}"))
        exprs.append((_forward_return("open", h) > 0).cast(pl.Int8).alias(f"forward_positive_{safe}"))
        # Threshold events (symmetric).
        fwd = _forward_return("open", h)
        for bps in THRESHOLD_BPS:
            exprs.append((fwd > bps / 10_000).cast(pl.Int8).alias(f"target_up_{bps}bps_{safe}"))
            exprs.append((fwd < -bps / 10_000).cast(pl.Int8).alias(f"target_down_{bps}bps_{safe}"))
        # Path extremes (long direction: favorable is max high, adverse is min low).
        entry = pl.col("open").shift(-1)
        hi = _forward_rolling_max("high", h)
        lo = _forward_rolling_min("low", h)
        exprs.append((hi / entry - 1.0).alias(f"mfe_{safe}"))
        exprs.append((1.0 - lo / entry).alias(f"mae_{safe}"))
        exprs.append(_forward_realized_vol(h).alias(f"future_realized_vol_{safe}"))
    out = out.with_columns(exprs)

    # Truncate to rows with a full window for the LONGEST horizon so every label
    # is defined; shorter horizons then also have a full window.
    max_h = max(horizons.values())
    out = out.head(max(n - max_h - 1, 0))
    return out


def non_overlapping_grid(frame: pl.DataFrame, horizon_minutes: int) -> pl.DataFrame:
    """Decision times spaced >= horizon apart, for unbiased inference.

    Long horizons overlap heavily at 1m sampling; inference on overlapping rows
    inflates t-statistics. This returns the same frame sampled every
    `horizon_minutes` bars.
    """
    return frame.filter((pl.int_range(pl.len()) % horizon_minutes) == 0)


def target_manifest() -> dict:
    return {
        "target_version": LONG_HORIZON_TARGET_VERSION,
        "horizons_minutes": HORIZONS,
        "threshold_bps": list(THRESHOLD_BPS),
        "columns_per_horizon": [
            "forward_return_{h}", "forward_abs_return_{h}", "forward_positive_{h}",
            "mfe_{h}", "mae_{h}", "future_realized_vol_{h}",
            "target_up_{bps}bps_{h}", "target_down_{bps}bps_{h}",
        ],
        "label_prefixes": ["target_", "forward_", "mfe_", "mae_", "time_to_", "future_"],
    }

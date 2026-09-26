"""Forward-return and path target engine (labels only, never features).

Timing convention matches the repository's existing convention used by
`labels/live_barrier.py`, so Stage 11 stays comparable with Stages 9-10:

    decision at bar t
    entry  = open[t + 1]
    exit   = open[t + horizon + 1]        (for forward_return_h)
    path   = bars t+1 .. t+horizon          (MFE/MAE, time-to-extreme)

These are LABELS. They may use the future. They must never enter a feature.
"""
from __future__ import annotations

import polars as pl

from .schema import TARGET_VERSION

MINUTE_MS = 60_000
FORWARD_HORIZONS = (1, 5, 10, 20, 30, 60, 240)


def build_forward_targets(bars: pl.DataFrame, horizons=FORWARD_HORIZONS) -> pl.DataFrame:
    """Forward returns and path statistics for every decision bar.

    forward_return_h = exit_open / entry_open - 1
    entry_open = open[t+1]; exit_open = open[t+h+1]
    MFE/MAE measured over highs/lows of bars t+1..t+h.
    Rows lacking a full window are null, never zero-filled.
    """
    frame = bars.sort("timestamp")
    n = frame.height
    out = frame.select("timestamp")
    opens = frame["open"].to_numpy()
    highs = frame["high"].to_numpy()
    lows = frame["low"].to_numpy()

    import numpy as np

    def _nullable(arr: np.ndarray) -> pl.Series:
        """NaN -> null so `is_not_null()` is meaningful for label boundaries."""
        return pl.Series(np.where(np.isfinite(arr), arr, None))

    for h in horizons:
        entry_idx = np.arange(n) + 1
        exit_idx = np.arange(n) + h + 1
        valid = exit_idx < n
        fwd = np.full(n, np.nan)
        fwd[valid] = opens[exit_idx[valid]] / opens[entry_idx[valid]] - 1.0
        out = out.with_columns(_nullable(fwd).alias(f"forward_return_{h}m"))

        # Path extremes over bars t+1..t+h
        mfe = np.full(n, np.nan)
        mae = np.full(n, np.nan)
        t_mfe = np.full(n, np.nan)
        t_mae = np.full(n, np.nan)
        for i in range(n):
            if not valid[i]:
                continue
            seg_high = highs[i + 1: i + h + 1]
            seg_low = lows[i + 1: i + h + 1]
            entry = opens[i + 1]
            mfe[i] = np.max(seg_high / entry - 1.0)
            mae[i] = -np.min(seg_low / entry - 1.0)  # positive magnitude
            t_mfe[i] = int(np.argmax(seg_high) + 1)
            t_mae[i] = int(np.argmin(seg_low) + 1)
        out = out.with_columns(
            _nullable(mfe).alias(f"mfe_{h}m"),
            _nullable(mae).alias(f"mae_{h}m"),
            _nullable(t_mfe).alias(f"time_to_mfe_{h}m"),
            _nullable(t_mae).alias(f"time_to_mae_{h}m"),
        )
    return out


def forward_distribution_stats(returns: pl.Series) -> dict:
    """Distribution summary used by the event study (Phase 6/18)."""
    clean = returns.drop_nulls().to_numpy()
    clean = clean[np.isfinite(clean)]
    if clean.size == 0:
        return {"n": 0, "mean": None, "median": None, "std": None,
                "p_positive": None, "p_negative": None,
                "p05": None, "p25": None, "p75": None, "p95": None}
    return {
        "n": int(clean.size),
        "mean": float(clean.mean()),
        "median": float(np.median(clean)),
        "std": float(clean.std(ddof=1)) if clean.size > 1 else 0.0,
        "p_positive": float((clean > 0).mean()),
        "p_negative": float((clean < 0).mean()),
        "p05": float(np.percentile(clean, 5)),
        "p25": float(np.percentile(clean, 25)),
        "p75": float(np.percentile(clean, 75)),
        "p95": float(np.percentile(clean, 95)),
    }

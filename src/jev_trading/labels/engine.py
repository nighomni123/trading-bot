"""Phase 3 label engine: point-in-time outcome labels for 1m bars.

ponytail: pure polars shifts/rolling windows — no loops, no new deps.
Contract: the outcome for row i uses only bars with index > i (close[i]
is the known reference price); the dir_15 threshold is causal trailing
volatility. Full 30-bar excursion window required, else null.
"""
from __future__ import annotations

import polars as pl

HORIZONS = (5, 15, 30, 60)
MFE_WINDOW = 30
DIR_HORIZON = 15
DIR_WINDOW = 15
DIR_MIN_PERIODS = 5
# configs/costs.json: round trip = 2 * (taker_fee_pct + slippage_pct).
ROUND_TRIP_OVERHEAD_PCT = 2 * (0.05 + 0.02)  # = 0.14 percent points


def compute_labels(bars: pl.DataFrame) -> pl.DataFrame:
    close = pl.col("close")
    out = [pl.col("timestamp")]
    for h in HORIZONS:
        out.append((close.shift(-h) / close - 1).alias(f"future_return_{h}"))
    # Max/min over next MFE_WINDOW bars (i+1..i+30): trailing rolling
    # extremum on the reversed series, reversed back, shifted one bar
    # forward so bar i itself is excluded; null unless full window fits.
    fwd_high = (
        pl.col("high")
        .reverse()
        .rolling_max(window_size=MFE_WINDOW, min_samples=MFE_WINDOW)
        .reverse()
        .shift(-1)
    )
    fwd_low = (
        pl.col("low")
        .reverse()
        .rolling_min(window_size=MFE_WINDOW, min_samples=MFE_WINDOW)
        .reverse()
        .shift(-1)
    )
    out += [
        (fwd_high / close - 1).alias("mfe_30"),
        (fwd_low / close - 1).alias("mae_30"),
    ]
    ret1 = close / close.shift(1) - 1
    # Causal trailing-15-bar sample std (ddof=1, polars default) of 1m returns.
    thr = 0.5 * ret1.rolling_std(window_size=DIR_WINDOW, min_samples=DIR_MIN_PERIODS)
    fr15 = close.shift(-DIR_HORIZON) / close - 1
    out += [
        (
            pl.when(fr15 > thr)
            .then(pl.lit(1))
            .when(fr15 < -thr)
            .then(pl.lit(-1))
            .otherwise(pl.lit(0))  # also covers null fr15/thr at head/tail
            .cast(pl.Int8)
            .alias("dir_15")
        ),
        (
            (fr15 > ROUND_TRIP_OVERHEAD_PCT / 100)
            .fill_null(False)  # null fr15 at tail -> not tradeable
            .cast(pl.Int8)
            .alias("tradeable_15")
        ),
    ]
    return bars.select(out)

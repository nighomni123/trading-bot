"""Data compatibility layer: canonical dataset -> LEAN format.

Only adapter knows LEAN-specific format; canonical schema preserved.
Point-in-time: LEAN receives bars in chronological order; execution at open[t+1]
is enforced by LEAN strategy, not by data format.
"""
from __future__ import annotations

import polars as pl
from jev_trading.contracts import BAR_COLUMNS


def to_lean_csv(df: pl.DataFrame, out_path: str | None = None) -> pl.DataFrame:
    """Convert canonical bar DataFrame to LEAN-compatible representation.

    Keeps timestamp (epoch ms -> LEAN expects format depending on usage),
    OHLCV, funding_rate (mapped to LEAN's interest/ funding fields if used),
    open_interest.
    Does NOT drop point-in-time semantics.
    """
    # Ensure canonical columns present
    for col in BAR_COLUMNS:
        if col not in df.columns:
            df = df.with_columns(pl.lit(0.0).alias(col))
    # LEAN CSV typically expects: Time,Open,High,Low,Close,Volume
    # We keep funding_rate/open_interest as extra columns for LEAN custom model
    lean_df = df.select([
        pl.col("timestamp").alias("Time"),
        pl.col("open").alias("Open"),
        pl.col("high").alias("High"),
        pl.col("low").alias("Low"),
        pl.col("close").alias("Close"),
        pl.col("volume").alias("Volume"),
        pl.col("funding_rate").alias("FundingRate"),
        pl.col("open_interest").alias("OpenInterest"),
    ])
    if out_path:
        lean_df.write_csv(out_path)
    return lean_df


def to_lean_format_dict(df: pl.DataFrame) -> dict:
    """Return dict representation (e.g., for JSON strategy config)."""
    return {
        "columns": ["Time","Open","High","Low","Close","Volume","FundingRate","OpenInterest"],
        "rows": len(df),
        "instrument": "BTCUSDT_PERP",
        "point_in_time": True,
    }

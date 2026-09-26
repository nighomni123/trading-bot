"""Archive ingestion and trade-side normalization.

The public Binance archive supplies aggTrades only for this symbol; book history
is genuinely unavailable and is reported as unpopulated rather than imputed.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

from .schema import MicrostructureSchema

TRADE_DIR = Path("data/microstructure/trades")
BAR_FILE = Path("data/btcusdt_1m.parquet")
MINUTE_MS = 60_000


def split_aggressive_volume(frame: pl.DataFrame) -> tuple[float, float]:
    """Return (aggressive_buy_volume, aggressive_sell_volume).

    `is_buyer_maker == True` means the buyer was passive, so the aggressor sold.
    """
    qty = frame["quantity"]
    buy = qty.filter(~frame["is_buyer_maker"]).sum() or 0.0
    sell = qty.filter(frame["is_buyer_maker"]).sum() or 0.0
    return float(buy), float(sell)


def normalize_trades(frame: pl.DataFrame) -> pl.DataFrame:
    """Canonical trade frame with notional derived and a stable column set."""
    return frame.select(
        pl.col("agg_trade_id").cast(pl.Int64),
        pl.col("trade_timestamp").cast(pl.Int64),
        pl.col("price").cast(pl.Float64),
        pl.col("quantity").cast(pl.Float64),
        (pl.col("price") * pl.col("quantity")).alias("notional"),
        pl.col("is_buyer_maker").cast(pl.Boolean),
    ).sort("trade_timestamp").select(list(MicrostructureSchema.trades()))


def trades_to_bars(trades: pl.DataFrame) -> pl.DataFrame:
    """Aggregate normalized trades to 1-minute bars aligned to the kline grid.

    Volume is base-asset quantity so it is comparable with the kline `volume`.
    """
    minute = (pl.col("trade_timestamp") // MINUTE_MS) * MINUTE_MS
    return (
        trades.with_columns(minute.alias("timestamp"))
        .group_by("timestamp")
        .agg(
            pl.col("price").first().alias("open"),
            pl.col("price").max().alias("high"),
            pl.col("price").min().alias("low"),
            pl.col("price").last().alias("close"),
            pl.col("quantity").sum().alias("volume"),
        )
        .sort("timestamp")
    )


def _to_canonical(frame: pl.DataFrame) -> pl.DataFrame:
    """Accept either already-canonical columns or raw archive column names."""
    rename = {}
    if "transact_time" in frame.columns:
        rename["transact_time"] = "trade_timestamp"
    if rename:
        frame = frame.rename(rename)
    return frame


def load_trade_range(start: str, end: str) -> pl.DataFrame:
    """Load and concatenate cached daily trade parquet files for a date range."""
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    frames = []
    day = first
    while day <= last:
        path = TRADE_DIR / f"{day.isoformat()}.parquet"
        if path.exists():
            frames.append(_to_canonical(pl.read_parquet(path)))
        day = date.fromordinal(day.toordinal() + 1)
    if not frames:
        return pl.DataFrame(schema=MicrostructureSchema.trades())
    return normalize_trades(pl.concat(frames, how="vertical_relaxed"))


def load_bars(path: Path | str = BAR_FILE) -> pl.DataFrame:
    """Klines with the microstructure columns the history does carry."""
    frame = pl.read_parquet(path)
    return frame.with_columns(
        (pl.col("open_interest") - pl.col("open_interest").shift(1)).alias("open_interest_change"),
    ).sort("timestamp")


def dataset_coverage(trades: pl.DataFrame) -> dict:
    if trades.is_empty():
        return {"days": 0, "trades": 0, "start": None, "end": None}
    start, end = trades["trade_timestamp"].min(), trades["trade_timestamp"].max()
    return {
        "days": int((end - start) // (24 * 60 * MINUTE_MS)) + 1,
        "trades": trades.height,
        "start": int(start),
        "end": int(end),
    }

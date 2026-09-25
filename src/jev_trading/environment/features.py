"""Causal feature helpers for the active market environment."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

import numpy as np
import polars as pl

from jev_trading.contracts import BAR_COLUMNS

MINUTE_MS = 60_000
TIMEFRAME_MS = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240}


def utc_from_ms(value: int) -> datetime:
    return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)


def _ema(values: Iterable[float], span: int) -> float | None:
    values = [float(value) for value in values]
    if len(values) < span:
        return None
    alpha = 2.0 / (span + 1.0)
    result = values[0]
    for value in values[1:]:
        result += alpha * (value - result)
    return float(result)


def _std(values: Iterable[float]) -> float | None:
    values = [float(value) for value in values]
    return float(np.std(values, ddof=0)) if values else None


def aggregate_timeframe(bars: pl.DataFrame, timeframe: str) -> pl.DataFrame:
    """Aggregate closed 1m bars without looking beyond the supplied frame."""
    if timeframe not in TIMEFRAME_MS:
        raise ValueError(f"unsupported timeframe: {timeframe}")
    missing = [column for column in BAR_COLUMNS if column not in bars.columns]
    if missing:
        raise ValueError(f"missing bar columns: {missing}")
    if bars.is_empty():
        return pl.DataFrame()
    frame = bars.select(BAR_COLUMNS).sort("timestamp")
    interval = TIMEFRAME_MS[timeframe] * MINUTE_MS
    return (
        frame.with_columns((pl.col("timestamp") // interval * interval).alias("bucket"))
        .group_by("bucket", maintain_order=True)
        .agg(
            pl.col("open").first().alias("open"),
            pl.col("high").max().alias("high"),
            pl.col("low").min().alias("low"),
            pl.col("close").last().alias("close"),
            pl.col("volume").sum().alias("volume"),
            pl.col("funding_rate").last().alias("funding_rate"),
            pl.col("open_interest").last().alias("open_interest"),
        )
        .sort("bucket")
    )


def timeframe_features(frame: pl.DataFrame) -> dict[str, float | None]:
    """Return component measurements for one already-aggregated timeframe."""
    if frame.is_empty():
        return {}
    closes = frame["close"].to_list()
    highs = frame["high"].to_list()
    lows = frame["low"].to_list()
    volumes = frame["volume"].to_list()
    close = float(closes[-1])
    previous = float(closes[-2]) if len(closes) > 1 else None
    returns = [float(closes[i] / closes[i - 1] - 1.0) for i in range(1, len(closes)) if closes[i - 1] > 0]
    log_returns = [float(np.log(closes[i] / closes[i - 1])) for i in range(1, len(closes)) if closes[i - 1] > 0]
    true_ranges = []
    for i, high in enumerate(highs):
        prior = float(closes[i - 1]) if i else float(high)
        true_ranges.append(max(float(high) - float(lows[i]), abs(float(high) - prior), abs(float(lows[i]) - prior)))
    typical = [(float(highs[i]) + float(lows[i]) + close) / 3.0 for i in range(len(closes))]
    total_volume = sum(float(value) for value in volumes)
    vwap = sum(price * float(volume) for price, volume in zip(typical, volumes)) / total_volume if total_volume > 0 else None
    high = float(highs[-1])
    low = float(lows[-1])
    range_position = (close - low) / (high - low) if high > low else 0.5
    mean_volume = sum(float(value) for value in volumes[:-1]) / max(1, len(volumes) - 1)
    std_volume = _std(volumes[:-1]) or 0.0
    volume_z = (float(volumes[-1]) - mean_volume) / std_volume if std_volume > 1e-12 else None
    ema20, ema50, ema200 = (_ema(closes, span) for span in (20, 50, 200))
    direction = "UP" if close > previous else "DOWN" if previous is not None and close < previous else "FLAT"
    if ema20 is not None and ema50 is not None:
        direction = "UP" if ema20 >= ema50 else "DOWN"
    persistence = sum(1 for value in returns[-20:] if (value > 0 and direction == "UP") or (value < 0 and direction == "DOWN")) / min(20, len(returns)) if returns else None
    acceleration = (returns[-1] - returns[-2]) if len(returns) > 1 else None
    return {
        "return_fraction": returns[-1] if returns else None,
        "realized_volatility": _std(log_returns),
        "atr_fraction": (sum(true_ranges[-14:]) / min(14, len(true_ranges))) / close if close > 0 else None,
        "ema20": ema20,
        "ema50": ema50,
        "ema200": ema200,
        "vwap": vwap,
        "distance_from_vwap": (close - vwap) / vwap if vwap else None,
        "range_position": range_position,
        "volume_z": volume_z,
        "trend_persistence": persistence,
        "trend_acceleration": acceleration,
    }

"""Deterministic event detector.

An Event is a deterministic predicate over observable state. Names describe
research hypotheses, NOT truths: the engine identifies conditions, and the
event study determines what follows. No event asserts a future direction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import polars as pl

from .schema import EVENT_DEFINITION_VERSION

EPS = 1e-12


@dataclass(frozen=True)
class Event:
    name: str
    timestamp: int
    direction: int          # +1 long, -1 short, 0 neutral
    intensity: float
    features: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "timestamp": self.timestamp, "direction": self.direction,
            "intensity": self.intensity, "features": self.features,
        }


#: Pre-registered thresholds. Declared before any event study is run.
THRESHOLDS = {
    "volume_zscore": 2.0,
    "volume_imbalance": 0.30,
    "oi_change_zscore": 2.0,
    "spread_zscore": 2.0,
    "depth_imbalance": 0.30,
    "return_zscore": 2.5,
}


def _zscore(expr: pl.Expr, window: int = 288) -> pl.Expr:
    mean = expr.rolling_mean(window_size=window, min_samples=60)
    std = expr.rolling_std(window_size=window, min_samples=60)
    return (expr - mean) / (std + EPS)


def compute_event_signals(frame: pl.DataFrame) -> pl.DataFrame:
    """Add rolling z-scores and boolean event flags. Causal: all rolling windows
    are trailing."""
    out = frame
    exprs = []
    for col, key in (("total_volume", "volume_zscore"),
                     ("oi_change_pct", "oi_change_zscore"),
                     ("bid_ask_spread_bps", "spread_zscore"),
                     ("close", "return_zscore")):
        if col in frame.columns:
            exprs.append(_zscore(pl.col(col)).alias(key))
    if "volume_imbalance_5m" in frame.columns:
        exprs.append(pl.col("volume_imbalance_5m").alias("volume_imbalance"))
    if "top_level_imbalance" in frame.columns:
        exprs.append(pl.col("top_level_imbalance").alias("depth_imbalance"))
    if exprs:
        out = frame.with_columns(exprs)
        out = out.with_columns(
            (pl.col("close").pct_change() * 10000).alias("return_bps"),
        )
    t = THRESHOLDS
    flags = []
    if "volume_zscore" in out.columns:
        flags.append((pl.col("volume_zscore") >= t["volume_zscore"]).alias("EVENT_VOLUME_SHOCK"))
    if "volume_imbalance" in out.columns:
        flags.append((pl.col("volume_imbalance").abs() >= t["volume_imbalance"]).alias("EVENT_FLOW_SHOCK"))
    if "oi_change_zscore" in out.columns:
        flags.append((pl.col("oi_change_zscore").abs() >= t["oi_change_zscore"]).alias("EVENT_OI_SHOCK"))
    if "spread_zscore" in out.columns:
        flags.append((pl.col("spread_zscore").abs() >= t["spread_zscore"]).alias("EVENT_SPREAD_SHOCK"))
    if "depth_imbalance" in out.columns:
        flags.append((pl.col("depth_imbalance").abs() >= t["depth_imbalance"]).alias("EVENT_BOOK_SHOCK"))
    if "return_zscore" in out.columns:
        flags.append((pl.col("return_zscore").abs() >= t["return_zscore"]).alias("EVENT_PRICE_SHOCK"))
    # Composite research hypotheses
    if "volume_imbalance" in out.columns and "return_bps" in out.columns:
        flags.append(
            ((pl.col("volume_imbalance") >= t["volume_imbalance"]) & (pl.col("return_bps") > 0))
            .alias("EVENT_FLOW_BREAKOUT")
        )
        flags.append(
            ((pl.col("volume_imbalance") <= -t["volume_imbalance"]) & (pl.col("return_bps") > 0))
            .alias("EVENT_FLOW_REVERSAL")
        )
    if "price_up_oi_up" in out.columns:
        flags.append((pl.col("price_up_oi_up") == 1).alias("EVENT_OI_PRICE_CONFIRMATION"))
        flags.append((pl.col("price_up_oi_down") == 1).alias("EVENT_OI_PRICE_DIVERGENCE"))
    if flags:
        out = out.with_columns(flags)
    return out


EVENT_COLUMNS = (
    "EVENT_VOLUME_SHOCK", "EVENT_FLOW_SHOCK", "EVENT_OI_SHOCK", "EVENT_SPREAD_SHOCK",
    "EVENT_BOOK_SHOCK", "EVENT_PRICE_SHOCK", "EVENT_FLOW_BREAKOUT", "EVENT_FLOW_REVERSAL",
    "EVENT_OI_PRICE_CONFIRMATION", "EVENT_OI_PRICE_DIVERGENCE",
)


def available_events(frame: pl.DataFrame) -> list[str]:
    return [c for c in EVENT_COLUMNS if c in frame.columns]


def extract_events(frame: pl.DataFrame, name: str, *, direction_column: str | None = None) -> list[Event]:
    """Materialize a boolean event column into Event records."""
    if name not in frame.columns:
        return []
    sub = frame.filter(pl.col(name))
    events: list[Event] = []
    for row in sub.iter_rows(named=True):
        direction = 0
        if direction_column and direction_column in row and row[direction_column] is not None:
            direction = int(row[direction_column])
        intensity = 0.0
        for key in ("volume_imbalance", "depth_imbalance", "return_zscore", "oi_change_zscore"):
            if row.get(key) is not None:
                try:
                    intensity = abs(float(row[key]))
                except (TypeError, ValueError):
                    intensity = 0.0
                break
        events.append(Event(
            name=name, timestamp=int(row["timestamp"]), direction=direction,
            intensity=intensity,
            features={k: row[k] for k in ("volume_imbalance_5m", "oi_change_pct", "return_bps") if row.get(k) is not None},
        ))
    return events

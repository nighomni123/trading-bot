"""Executable-entry barrier labels for Phase 3 target research.

A decision on completed bar ``t`` enters at ``open[t+1]``.  Barriers are checked
on bars ``t+1..t+horizon`` and a timeout exits at ``open[t+horizon+1]``.
One-minute OHLC cannot order two touches inside the same bar, so simultaneous
TP/SL touches are conservatively labelled ``SL_FIRST`` and flagged ambiguous.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

MINUTE_MS = 60_000
TP_BPS = (10, 15, 20, 30, 40)
SL_BPS = (10, 15, 20, 30)
HORIZONS = (5, 10, 15, 30, 60)

SL_FIRST = 0
TIMEOUT = 1
TP_FIRST = 2


@dataclass(frozen=True)
class BarrierConfig:
    tp_bps: int
    sl_bps: int
    horizon_minutes: int

    def __post_init__(self) -> None:
        if self.tp_bps not in TP_BPS or self.sl_bps not in SL_BPS:
            raise ValueError("barrier must be in the pre-registered TP/SL grid")
        if self.horizon_minutes not in HORIZONS:
            raise ValueError("horizon must be in the pre-registered grid")

    @property
    def config_id(self) -> str:
        return f"tp{self.tp_bps}bp_sl{self.sl_bps}bph{self.horizon_minutes}m"


def barrier_grid() -> tuple[BarrierConfig, ...]:
    return tuple(
        BarrierConfig(tp, sl, horizon)
        for horizon in HORIZONS
        for tp in TP_BPS
        for sl in SL_BPS
    )


@dataclass(frozen=True)
class BarrierLabels:
    valid: np.ndarray
    long_outcome: np.ndarray
    short_outcome: np.ndarray
    long_time_to_tp: np.ndarray
    long_time_to_sl: np.ndarray
    short_time_to_tp: np.ndarray
    short_time_to_sl: np.ndarray
    long_ambiguous: np.ndarray
    short_ambiguous: np.ndarray
    long_mfe: np.ndarray
    long_mae: np.ndarray
    short_mfe: np.ndarray
    short_mae: np.ndarray
    timeout_return: np.ndarray


def _require_bars(bars: pl.DataFrame) -> None:
    required = {"timestamp", "open", "high", "low", "close", "funding_rate"}
    missing = sorted(required - set(bars.columns))
    if missing:
        raise ValueError(f"missing bar columns: {missing}")
    if bars.is_empty():
        raise ValueError("barrier labels require non-empty bars")
    ts = bars["timestamp"].to_numpy()
    if not np.all(np.diff(ts) == MINUTE_MS):
        raise ValueError("bar timestamps must be contiguous one-minute UTC bars")


def _first_touch(
    future: np.ndarray,
    entry: np.ndarray,
    levels: tuple[int, ...],
    *,
    long: bool,
) -> dict[int, np.ndarray]:
    """First offset at which each pre-registered level is touched."""
    n = len(entry)
    result: dict[int, np.ndarray] = {}
    for bps in levels:
        times = np.full(n, -1, dtype=np.int16)
        level = bps / 10_000.0
        for offset in range(1, HORIZONS[-1] + 1):
            size = n - offset
            if size <= 0:
                break
            target = entry[:size] * (1.0 + level if long else 1.0 - level)
            observed = future[offset:]
            if long:
                hit = (observed >= target) | np.isclose(observed, target, rtol=1e-12, atol=0.0)
            else:
                hit = (observed <= target) | np.isclose(observed, target, rtol=1e-12, atol=0.0)
            view = times[:size]
            view[(view < 0) & hit] = offset
        result[bps] = times
    return result


def _outcomes(
    tp_time: np.ndarray,
    sl_time: np.ndarray,
    valid: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    outcome = np.full(len(valid), -1, dtype=np.int8)
    timeout = valid & (tp_time < 0) & (sl_time < 0)
    tp_only = valid & (tp_time >= 0) & (sl_time < 0)
    sl_only = valid & (sl_time >= 0) & (tp_time < 0)
    tp_first = valid & (tp_time >= 0) & (sl_time >= 0) & (tp_time < sl_time)
    # Equal offsets are the OHLC ambiguity case: conservative SL-first.
    outcome[timeout] = TIMEOUT
    outcome[tp_only | tp_first] = TP_FIRST
    outcome[sl_only | (valid & (sl_time >= 0) & ~(tp_time < sl_time))] = SL_FIRST
    ambiguous = valid & (tp_time >= 0) & (sl_time >= 0) & (tp_time == sl_time)
    return outcome, ambiguous


class ExecutionBarrierCache:
    """Precompute the 100 configurations without recomputing shared excursions."""

    def __init__(self, bars: pl.DataFrame):
        _require_bars(bars)
        self.n = bars.height
        self.timestamp = bars["timestamp"].to_numpy()
        self.open = bars["open"].to_numpy().astype(float)
        high = bars["high"].to_numpy().astype(float)
        low = bars["low"].to_numpy().astype(float)
        self.close = bars["close"].to_numpy().astype(float)
        self.funding_rate = bars["funding_rate"].to_numpy().astype(float)
        self.entry_open = np.full(self.n, np.nan, dtype=float)
        self.entry_open[:-1] = self.open[1:]

        future_high = np.full(self.n, -np.inf, dtype=float)
        future_low = np.full(self.n, np.inf, dtype=float)
        self.high_by_horizon: dict[int, np.ndarray] = {}
        self.low_by_horizon: dict[int, np.ndarray] = {}
        self.timeout_return: dict[int, np.ndarray] = {}
        for offset in range(1, HORIZONS[-1] + 1):
            size = self.n - offset
            if size <= 0:
                break
            future_high[:size] = np.maximum(future_high[:size], high[offset:])
            future_low[:size] = np.minimum(future_low[:size], low[offset:])
            if offset in HORIZONS:
                self.high_by_horizon[offset] = future_high[:size].copy()
                self.low_by_horizon[offset] = future_low[:size].copy()
                returns = np.full(self.n, np.nan, dtype=float)
                exit_size = self.n - offset - 1
                returns[:exit_size] = self.open[offset + 1:] / self.entry_open[:exit_size] - 1.0
                self.timeout_return[offset] = returns

        self.long_tp_time = _first_touch(high, self.entry_open, TP_BPS, long=True)
        self.long_sl_time = _first_touch(low, self.entry_open, SL_BPS, long=False)
        self.short_tp_time = _first_touch(low, self.entry_open, TP_BPS, long=False)
        self.short_sl_time = _first_touch(high, self.entry_open, SL_BPS, long=True)

    def labels(self, config: BarrierConfig) -> BarrierLabels:
        valid = np.isfinite(self.timeout_return[config.horizon_minutes])
        long_tp = self.long_tp_time[config.tp_bps]
        long_sl = self.long_sl_time[config.sl_bps]
        short_tp = self.short_tp_time[config.tp_bps]
        short_sl = self.short_sl_time[config.sl_bps]
        long_outcome, long_ambiguous = _outcomes(long_tp, long_sl, valid)
        short_outcome, short_ambiguous = _outcomes(short_tp, short_sl, valid)

        size = self.n - config.horizon_minutes
        entry = self.entry_open[:size]
        long_high = self.high_by_horizon[config.horizon_minutes] / entry - 1.0
        long_low = self.low_by_horizon[config.horizon_minutes] / entry - 1.0
        short_high = long_high
        short_low = long_low
        long_mfe = np.full(self.n, np.nan, dtype=float)
        long_mae = np.full(self.n, np.nan, dtype=float)
        short_mfe = np.full(self.n, np.nan, dtype=float)
        short_mae = np.full(self.n, np.nan, dtype=float)
        long_mfe[:size], long_mae[:size] = long_high, long_low
        short_mfe[:size], short_mae[:size] = -short_low, -short_high
        return BarrierLabels(
            valid=valid,
            long_outcome=long_outcome,
            short_outcome=short_outcome,
            long_time_to_tp=long_tp,
            long_time_to_sl=long_sl,
            short_time_to_tp=short_tp,
            short_time_to_sl=short_sl,
            long_ambiguous=long_ambiguous,
            short_ambiguous=short_ambiguous,
            long_mfe=long_mfe,
            long_mae=long_mae,
            short_mfe=short_mfe,
            short_mae=short_mae,
            timeout_return=self.timeout_return[config.horizon_minutes].copy(),
        )


def labels_to_frame(labels: BarrierLabels, timestamps: np.ndarray) -> pl.DataFrame:
    """Convert cache arrays to nullable Polars label columns."""
    frame = pl.DataFrame({
        "timestamp": timestamps,
        "valid": labels.valid,
        "long_outcome": labels.long_outcome,
        "short_outcome": labels.short_outcome,
        "long_time_to_tp": labels.long_time_to_tp,
        "long_time_to_sl": labels.long_time_to_sl,
        "short_time_to_tp": labels.short_time_to_tp,
        "short_time_to_sl": labels.short_time_to_sl,
        "long_ambiguous": labels.long_ambiguous,
        "short_ambiguous": labels.short_ambiguous,
        "long_mfe": labels.long_mfe,
        "long_mae": labels.long_mae,
        "short_mfe": labels.short_mfe,
        "short_mae": labels.short_mae,
        "timeout_return": labels.timeout_return,
    })
    integer_time_columns = (
        "long_time_to_tp", "long_time_to_sl", "short_time_to_tp", "short_time_to_sl"
    )
    float_columns = ("long_mfe", "long_mae", "short_mfe", "short_mae", "timeout_return")
    return frame.with_columns(
        *[
            pl.when(pl.col(column) >= 0).then(pl.col(column)).otherwise(None).cast(pl.Int16)
            for column in integer_time_columns
        ],
        *[
            pl.when(pl.col(column).is_finite()).then(pl.col(column)).otherwise(None)
            for column in float_columns
        ],
        *[
            pl.when(pl.col(column) >= 0).then(pl.col(column)).otherwise(None).cast(pl.Int8)
            for column in ("long_outcome", "short_outcome")
        ],
    )


def compute_execution_barrier_labels(
    bars: pl.DataFrame,
    config: BarrierConfig,
) -> pl.DataFrame:
    cache = ExecutionBarrierCache(bars)
    return labels_to_frame(cache.labels(config), cache.timestamp)

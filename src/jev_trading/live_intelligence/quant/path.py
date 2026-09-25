"""Empirical path analysis from explicitly supplied or causally-built samples.

The builder only uses bars whose complete outcome window is already present in
the input frame. It never uses the current decision's future bars.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

import polars as pl

from ..schemas import MarketEnvironment, PathSample, QuantAnalysisResult, Side


def build_completed_path_samples(
    bars: pl.DataFrame,
    *,
    side: Side,
    target_fraction: float,
    stop_fraction: float,
    horizon_minutes: int,
    max_samples: int = 500,
) -> list[PathSample]:
    """Build completed next-open barrier observations from a closed-bar frame.

    Entry is the next bar open after the decision bar. High/low checks run from
    that entry bar through the configured horizon. A same-bar target/stop tie is
    conservatively treated as stop-first. The final incomplete horizon is
    excluded.
    """
    if target_fraction <= 0 or stop_fraction <= 0 or horizon_minutes < 1 or max_samples < 1:
        raise ValueError("path sample parameters must be positive")
    required = {"timestamp", "open", "high", "low", "close"}
    missing = required - set(bars.columns)
    if missing:
        raise ValueError(f"missing path columns: {sorted(missing)}")
    frame = bars.select(list(required)).sort("timestamp")
    if frame.is_empty():
        return []
    timestamps = frame["timestamp"].to_list()
    opens = frame["open"].to_list()
    highs = frame["high"].to_list()
    lows = frame["low"].to_list()
    n = len(frame)
    samples: list[PathSample] = []
    # Decision row i, entry row i+1, final checked row i+horizon.
    last_start = n - horizon_minutes - 1
    for i in range(max(0, last_start + 1)):
        entry_index = i + 1
        entry = float(opens[entry_index])
        if entry <= 0:
            continue
        target = entry * (1 + target_fraction) if side == Side.LONG else entry * (1 - target_fraction)
        stop = entry * (1 - stop_fraction) if side == Side.LONG else entry * (1 + stop_fraction)
        target_hit = False
        stop_hit = False
        first_hit: int | None = None
        for offset in range(horizon_minutes):
            index = entry_index + offset
            high = float(highs[index])
            low = float(lows[index])
            hit_target = high >= target if side == Side.LONG else low <= target
            hit_stop = low <= stop if side == Side.LONG else high >= stop
            if hit_target or hit_stop:
                # Stop wins simultaneous touches because OHLC cannot order them.
                stop_hit = hit_stop
                target_hit = hit_target and not hit_stop
                first_hit = offset + 1
                break
        if first_hit is None:
            target_first = stop_first = False
            timeout = True
        else:
            timeout = False
            target_first = target_hit
            stop_first = stop_hit
        if side == Side.LONG:
            favorable = max(float(highs[j]) / entry - 1 for j in range(entry_index, entry_index + horizon_minutes))
            adverse = max(1 - float(lows[j]) / entry for j in range(entry_index, entry_index + horizon_minutes))
        else:
            favorable = max(1 - float(lows[j]) / entry for j in range(entry_index, entry_index + horizon_minutes))
            adverse = max(float(highs[j]) / entry - 1 for j in range(entry_index, entry_index + horizon_minutes))
        samples.append(PathSample(
            timestamp=datetime.fromtimestamp(int(timestamps[entry_index]) / 1000, tz=timezone.utc),
            side=side, target_first=target_first, stop_first=stop_first, timeout=timeout,
            favorable_excursion=favorable, adverse_excursion=adverse,
            duration_seconds=(first_hit or horizon_minutes) * 60,
        ))
    return samples[-max_samples:]


def analyze_path(environment: MarketEnvironment, samples: Sequence[PathSample], *, side: Side, horizon_seconds: int) -> QuantAnalysisResult:
    selected = [sample for sample in samples if sample.side == side]
    n = len(selected)
    if n == 0:
        return QuantAnalysisResult(
            analysis_name="path", analyzer_version="path-v1", timestamp=environment.decision_timestamp,
            strategy_context=side.value, horizon_seconds=horizon_seconds,
            evidence={"sample_size": 0}, limitations=("no completed empirical path samples",),
        )
    probabilities = {
        "target": sum(sample.target_first for sample in selected) / n,
        "stop": sum(sample.stop_first for sample in selected) / n,
        "timeout": sum(sample.timeout for sample in selected) / n,
    }
    return QuantAnalysisResult(
        analysis_name="path", analyzer_version="path-v1", timestamp=environment.decision_timestamp,
        strategy_context=side.value, sample_size=n, estimated_probability=probabilities["target"],
        expected_payoff=sum(sample.favorable_excursion for sample in selected) / n,
        expected_downside=sum(sample.adverse_excursion for sample in selected) / n,
        uncertainty=1.0 / (n ** 0.5), horizon_seconds=horizon_seconds,
        evidence={"outcomes": probabilities}, limitations=("sample selection must be documented by caller",),
        path_probabilities=probabilities, empirical_sample_size=n,
    )

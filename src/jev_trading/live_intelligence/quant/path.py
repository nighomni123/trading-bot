"""Causal, barrier-homogeneous path construction and analysis."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
import math

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
    """Build completed next-open barrier observations with explicit identity."""
    if side == Side.FLAT:
        raise ValueError("path side cannot be FLAT")
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
    if any(int(right) - int(left) != 60_000 for left, right in zip(timestamps, timestamps[1:])):
        raise ValueError("path samples require contiguous one-minute bars")
    opens = frame["open"].to_list()
    highs = frame["high"].to_list()
    lows = frame["low"].to_list()
    closes = frame["close"].to_list()
    n = len(frame)
    samples: list[PathSample] = []
    # Decision row i, entry open at i+1, checks i+1..i+H, timeout open at i+H+1.
    last_start = n - horizon_minutes - 2
    for i in range(max(0, last_start + 1)):
        entry_index = i + 1
        entry = float(opens[entry_index])
        if entry <= 0:
            continue
        target = entry * (1 + target_fraction) if side == Side.LONG else entry * (1 - target_fraction)
        stop = entry * (1 - stop_fraction) if side == Side.LONG else entry * (1 + stop_fraction)
        first_hit: int | None = None
        target_first = False
        stop_first = False
        for offset in range(horizon_minutes):
            index = entry_index + offset
            hit_target = highs[index] >= target if side == Side.LONG else lows[index] <= target
            hit_stop = lows[index] <= stop if side == Side.LONG else highs[index] >= stop
            if hit_target or hit_stop:
                first_hit = offset + 1
                stop_first = hit_stop
                target_first = hit_target and not hit_stop
                break
        timeout = first_hit is None
        if timeout:
            timeout_index = entry_index + horizon_minutes
            return_fraction = (float(opens[timeout_index]) / entry - 1.0) * (1.0 if side == Side.LONG else -1.0)
            excursion_end = entry_index + horizon_minutes - 1
            duration_seconds = horizon_minutes * 60
        else:
            return_fraction = target_fraction if target_first else -stop_fraction
            excursion_end = entry_index + first_hit - 1
            duration_seconds = first_hit * 60
        if side == Side.LONG:
            favorable = max(float(highs[index]) / entry - 1 for index in range(entry_index, excursion_end + 1))
            adverse = max(1 - float(lows[index]) / entry for index in range(entry_index, excursion_end + 1))
        else:
            favorable = max(1 - float(lows[index]) / entry for index in range(entry_index, excursion_end + 1))
            adverse = max(float(highs[index]) / entry - 1 for index in range(entry_index, excursion_end + 1))
        samples.append(PathSample(
            timestamp=datetime.fromtimestamp(int(timestamps[entry_index]) / 1000, tz=timezone.utc),
            side=side, entry_price=entry, target_price=target, stop_price=stop,
            target_fraction=target_fraction, stop_fraction=stop_fraction,
            horizon_minutes=horizon_minutes, target_first=target_first,
            stop_first=stop_first, timeout=timeout, return_fraction=return_fraction,
            favorable_excursion=max(0.0, favorable), adverse_excursion=max(0.0, adverse),
            duration_seconds=duration_seconds,
        ))
    return samples[-max_samples:]


def analyze_path(
    environment: MarketEnvironment,
    samples: Sequence[PathSample],
    *,
    side: Side,
    target_fraction: float,
    stop_fraction: float,
    horizon_minutes: int,
    horizon_seconds: int,
) -> QuantAnalysisResult:
    selected = [
        sample for sample in samples
        if sample.side == side
        and math.isclose(sample.target_fraction, target_fraction, rel_tol=1e-12, abs_tol=1e-12)
        and math.isclose(sample.stop_fraction, stop_fraction, rel_tol=1e-12, abs_tol=1e-12)
        and sample.horizon_minutes == horizon_minutes
    ]
    identity = {
        "side": side.value,
        "target_fraction": target_fraction,
        "stop_fraction": stop_fraction,
        "horizon_minutes": horizon_minutes,
    }
    n = len(selected)
    if n == 0:
        return QuantAnalysisResult(
            analysis_name="path", analyzer_version="path-v2", timestamp=environment.decision_timestamp,
            strategy_context=side.value, horizon_seconds=horizon_seconds,
            evidence={"sample_size": 0, "barrier_identity": identity},
            limitations=("no completed empirical path samples for this barrier identity",),
        )
    probabilities = {
        "target": sum(sample.target_first for sample in selected) / n,
        "stop": sum(sample.stop_first for sample in selected) / n,
        "timeout": sum(sample.timeout for sample in selected) / n,
    }
    timeout_returns = [sample.return_fraction for sample in selected if sample.timeout]
    expected_mfe = sum(sample.favorable_excursion for sample in selected) / n
    expected_mae = sum(sample.adverse_excursion for sample in selected) / n
    return QuantAnalysisResult(
        analysis_name="path", analyzer_version="path-v2", timestamp=environment.decision_timestamp,
        strategy_context=side.value, sample_size=n, estimated_probability=probabilities["target"],
        expected_payoff=expected_mfe, expected_downside=expected_mae,
        expected_return=sum(sample.return_fraction for sample in selected) / n,
        expected_mfe=expected_mfe, expected_mae=expected_mae,
        expected_timeout_return=(sum(timeout_returns) / len(timeout_returns) if timeout_returns else None),
        expected_duration_seconds=sum(sample.duration_seconds for sample in selected) / n,
        uncertainty=1.0 / (n ** 0.5), horizon_seconds=horizon_seconds,
        evidence={"outcomes": probabilities, "barrier_identity": identity},
        limitations=("samples overlap and are not conditioned on the current regime or strategy state",),
        path_probabilities=probabilities, empirical_sample_size=n,
    )

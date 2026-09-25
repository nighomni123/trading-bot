"""Empirical path analysis from explicitly supplied completed samples.

This module does not manufacture a path distribution. A caller must provide
samples whose outcomes are already observed and point-in-time safe.
"""
from __future__ import annotations

from collections.abc import Sequence

from ..schemas import PathSample, QuantAnalysisResult, Side, MarketEnvironment


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

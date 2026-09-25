"""Deterministic interface benchmark for free provider profiles; no execution."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any

from jev_trading.live_intelligence.config import LiveSettings, ProviderConfig
from jev_trading.live_intelligence.frontier.client import FrontierClientFactory
from jev_trading.live_intelligence.frontier.strategist import FrontierStrategist, load_prompt
from jev_trading.live_intelligence.jev.client import JevClientFactory
from jev_trading.live_intelligence.jev.evaluator import JevEvaluator
from jev_trading.live_intelligence.policy import make_candidate
from jev_trading.live_intelligence.provider_errors import ProviderFailure
from jev_trading.live_intelligence.provider_smoke import _smoke_environment
from jev_trading.live_intelligence.schemas import (
    JevRequest,
    QuantAnalysisResult,
    QuantEvidence,
    StrategyHypothesis,
)


def _variant_environment(base, index: int):
    trend = ("UP", "DOWN", "FLAT")[index % 3]
    volatility = ("EXPANSION", "COMPRESSION")[index % 2]
    timeframes = {
        name: state.model_copy(update={"trend_direction": trend})
        for name, state in base.timeframes.items()
    }
    return base.model_copy(update={
        "timeframes": timeframes,
        "structure": base.structure.model_copy(update={"breakout_state": "UP" if trend == "UP" else "DOWN"}),
        "volatility": base.volatility.model_copy(update={"regime": volatility}),
    })


def _quant_evidence(environment, index: int) -> QuantEvidence:
    probabilities = {"target": 0.55 + (index % 3) * 0.05, "stop": 0.2, "timeout": 0.25 - (index % 3) * 0.05}
    if abs(sum(probabilities.values()) - 1.0) > 1e-8:
        probabilities["timeout"] = 1.0 - probabilities["target"] - probabilities["stop"]
    path = QuantAnalysisResult(
        analysis_name="path", analyzer_version="path-v2", timestamp=environment.decision_timestamp,
        sample_size=200, estimated_probability=probabilities["target"],
        path_probabilities=probabilities, evidence={"case": index},
    )
    trend = QuantAnalysisResult(
        analysis_name="trend", analyzer_version="trend-v1", timestamp=environment.decision_timestamp,
        evidence={"case": index, "direction": environment.timeframes["1h"].trend_direction},
    )
    return QuantEvidence(
        timestamp=environment.decision_timestamp, regime=environment.timeframes["1h"].trend_direction,
        regime_confidence=0.5, analyzer_results=(trend, path), path=path,
        probabilities=probabilities, sample_size=200,
        analyzer_versions={"trend": "trend-v1", "path": "path-v2"},
    )


def _hypothesis(environment, index: int) -> StrategyHypothesis:
    direction = "LONG" if index % 2 == 0 else "SHORT"
    return StrategyHypothesis(
        hypothesis_id=f"benchmark-hypothesis-{index}", timestamp=environment.decision_timestamp,
        regime=environment.timeframes["1h"].trend_direction, regime_confidence=0.5,
        primary_strategy="momentum", direction=direction, horizon_seconds=900,
        thesis="Deterministic benchmark candidate", entry_conditions=["trend_alignment"],
        invalidation_conditions=["trend_transition"], abstain=False, reason="benchmark",
        conviction=0.5, model_version="benchmark", prompt_version="frontier-strategist-v1",
    )


def _openai(config: ProviderConfig) -> ProviderConfig:
    return config.model_copy(update={"provider": "openai_compatible"})


def _run_frontier(settings: LiveSettings, index: int) -> dict[str, Any]:
    base = _smoke_environment(datetime.now(timezone.utc))
    environment = _variant_environment(base, index)
    evidence = _quant_evidence(environment, index)
    client = FrontierClientFactory.create(_openai(settings.frontier.provider))
    strategist = FrontierStrategist(
        client, prompt=load_prompt(Path(__file__).parent / settings.frontier.prompt_file),
        prompt_version=settings.frontier.prompt_version, settings=settings,
    )
    started = time.perf_counter()
    result = strategist.generate(environment, request_id=f"benchmark-frontier-{index}", quant_evidence=evidence)
    return {"component": "frontier", "case": index, "ok": True, "interface_mode": result.interface_mode, "tool_name": result.tool_name, "retry_count": client.last_retry_count, "latency_ms": (time.perf_counter() - started) * 1000}


def _run_jev(settings: LiveSettings, index: int) -> dict[str, Any]:
    base = _smoke_environment(datetime.now(timezone.utc))
    environment = _variant_environment(base, index)
    evidence = _quant_evidence(environment, index)
    hypothesis = _hypothesis(environment, index)
    candidate = make_candidate(environment, hypothesis, settings=settings)
    request = JevRequest(
        request_id=f"benchmark-jev-{index}", timestamp=environment.decision_timestamp,
        environment=environment, frontier_hypothesis=hypothesis, quant_evidence=evidence,
        candidate_trade=candidate, questions=(), prompt_version=settings.jev.prompt_version,
    )
    client = JevClientFactory.create(_openai(settings.jev.provider), prompt=load_prompt(Path(__file__).parent / settings.jev.prompt_file))
    evaluator = JevEvaluator(
        client, prompt=load_prompt(Path(__file__).parent / settings.jev.prompt_file),
        settings=settings, prompt_version=settings.jev.prompt_version,
        max_validity_seconds=settings.jev.validity_seconds,
    )
    started = time.perf_counter()
    result = evaluator.evaluate(request)
    return {"component": "jev", "case": index, "ok": True, "interface_mode": result.interface_mode, "tool_name": result.tool_name, "retry_count": client.last_retry_count, "latency_ms": (time.perf_counter() - started) * 1000}


_throttle_lock = __import__("threading").Lock()
_last_start = 0.0


def _throttle(min_interval_seconds: float) -> None:
    """Space every request start so a free endpoint is not deliberately bursted."""
    global _last_start
    if min_interval_seconds <= 0:
        return
    with _throttle_lock:
        wait = _last_start + min_interval_seconds - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_start = time.monotonic()


def _run_gated(fn, settings: LiveSettings, index: int, min_interval_seconds: float) -> dict[str, Any]:
    _throttle(min_interval_seconds)
    return fn(settings, index)


def run_benchmark(settings: LiveSettings, *, cases: int = 24, workers: int = 4, delay: float = 0.0) -> dict[str, Any]:
    tasks = []
    for index in range(cases):
        tasks.append(("frontier", index))
        tasks.append(("jev", index))
    results: list[dict[str, Any]] = []
    started_at = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(
                _run_gated,
                _run_frontier if component == "frontier" else _run_jev,
                settings, index, delay,
            ): (component, index)
            for component, index in tasks
        }
        for future in as_completed(futures):
            component, index = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:
                results.append({
                    "component": component, "case": index, "ok": False,
                    "interface_mode": None, "tool_name": None,
                    "latency_ms": None,
                    "retry_count": getattr(exc, "retry_count", 0),
                    "retry_after": getattr(exc, "retry_after", None),
                    "error_category": getattr(exc, "category", type(exc).__name__),
                    "http_status": getattr(exc, "http_status", None),
                })
    elapsed_minutes = max((time.perf_counter() - started_at) / 60.0, 1e-9)
    latencies = sorted(result["latency_ms"] for result in results if result.get("latency_ms") is not None)
    def percentile(value):
        if not latencies:
            return None
        return latencies[min(len(latencies) - 1, int(len(latencies) * value))]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cases": cases,
        "workers": max(1, workers),
        "delay_seconds": delay,
        "results": sorted(results, key=lambda item: (item["component"], item["case"])),
        "summary": {
            "total": len(results),
            "valid": sum(result["ok"] for result in results),
            "final_failures": sum(not result["ok"] for result in results),
            "validation_failures": sum(result.get("error_category") == "validation" for result in results),
            "rate_limited": sum(result.get("http_status") == 429 for result in results),
            "retries": sum(result.get("retry_count", 0) or 0 for result in results),
            "tool_call_successes": sum(result.get("interface_mode") == "tool_call" for result in results),
            "latency_p50_ms": percentile(0.50),
            "latency_p95_ms": percentile(0.95),
            "latency_p99_ms": percentile(0.99),
            "requests_per_minute": round(len(results) / elapsed_minutes, 2),
            "trading_execution": "NOT_INVOKED",
        },
    }

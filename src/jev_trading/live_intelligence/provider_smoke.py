"""Provider-only smoke checks; never starts the paper execution loop."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import polars as pl

from jev_trading.contracts import BAR_COLUMNS
from jev_trading.environment import build_market_environment
from jev_trading.live_intelligence.config import LiveSettings, ProviderConfig
from jev_trading.live_intelligence.frontier.client import FrontierClientFactory
from jev_trading.live_intelligence.frontier.strategist import FrontierStrategist, load_prompt
from jev_trading.live_intelligence.jev.client import JevClientFactory
from jev_trading.live_intelligence.jev.evaluator import JevEvaluator
from jev_trading.live_intelligence.policy import make_candidate
from jev_trading.live_intelligence.schemas import (
    DataEventType,
    DataQuality,
    JevRequest,
    MarketTick,
    MarketType,
    QuantEvidence,
    StrategyHypothesis,
)


def _smoke_environment(now: datetime):
    """A synthetic environment long enough to contain a whole completed 4h bucket."""
    minutes = 600
    start = (int(now.timestamp() * 1000) - minutes * 60_000) // (240 * 60_000) * (240 * 60_000)
    count = (int(now.timestamp() * 1000) - start) // 60_000
    rows = []
    for index in range(count):
        price = 100.0 + index * 0.01
        rows.append({
            "timestamp": start + index * 60_000,
            "open": price, "high": price + 0.05, "low": price - 0.05, "close": price,
            "volume": 10.0, "funding_rate": 0.0, "open_interest": 1000.0,
        })
    bars = pl.DataFrame(rows, schema={column: pl.Int64 if column == "timestamp" else pl.Float64 for column in BAR_COLUMNS})
    tick = MarketTick(
        source="primary", source_role="primary", instrument="BTCUSDT_PERP", venue="binance-futures",
        market_type=MarketType.PERPETUAL, event_timestamp=now, received_timestamp=now,
        event_type=DataEventType.BAR, last=float(bars["close"][-1]), bid=99.99, ask=100.01,
        mid=100.0, bid_depth=10.0, ask_depth=10.0, volume=10.0, open_interest=1000.0,
    )
    return build_market_environment(
        bars, ticks=[tick], quality=DataQuality(safe_for_trading=True, stale=False), decision_timestamp=now,
    )


def _smoke_hypothesis(environment, request_id: str) -> StrategyHypothesis:
    return StrategyHypothesis(
        hypothesis_id=request_id, timestamp=environment.decision_timestamp, regime="MIXED",
        regime_confidence=0.5, primary_strategy="momentum", direction="LONG", horizon_seconds=900,
        thesis="Provider smoke request; not an execution decision", entry_conditions=["smoke_only"],
        invalidation_conditions=["schema_failure"], abstain=False, reason="provider_smoke",
        conviction=0.0, model_version="provider-smoke", prompt_version="frontier-strategist-v1",
    )


def _openai_provider(config: ProviderConfig) -> ProviderConfig:
    return config.model_copy(update={"provider": "openai_compatible"})


def _failure_fields(exc: Exception) -> dict[str, Any]:
    return {
        "error_category": getattr(exc, "category", type(exc).__name__),
        "http_status": getattr(exc, "http_status", None),
        "retry_count": getattr(exc, "retry_count", 0),
        "request_id": getattr(exc, "request_id", None),
    }


def smoke_frontier(settings: LiveSettings) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    environment = _smoke_environment(now)
    hypothesis_request_id = "provider-smoke-frontier"
    client = FrontierClientFactory.create(_openai_provider(settings.frontier.provider))
    prompt = load_prompt(Path(__file__).parent / settings.frontier.prompt_file)
    strategist = FrontierStrategist(client, prompt=prompt, prompt_version=settings.frontier.prompt_version)
    result = {
        "component": "frontier", "provider": getattr(client, "provider", settings.frontier.provider.provider),
        "model": getattr(client, "model", settings.frontier.provider.model),
        "connectivity": "PASS", "request": "PASS", "response_parsing": "PASS", "schema_validation": "PASS",
        "trading_execution": "NOT_INVOKED",
    }
    try:
        hypothesis = strategist.generate(environment, request_id=hypothesis_request_id, regime="MIXED")
        result["abstain"] = hypothesis.abstain
    except Exception as exc:
        fields = _failure_fields(exc)
        result.update({"request": "FAIL", **fields})
        if fields["error_category"] == "validation":
            result["schema_validation"] = "FAIL"
    return result


def smoke_jev(settings: LiveSettings) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    environment = _smoke_environment(now)
    hypothesis = _smoke_hypothesis(environment, "provider-smoke-jev")
    candidate = make_candidate(environment, hypothesis, settings=settings)
    evidence = QuantEvidence(
        timestamp=now, regime="MIXED", regime_confidence=0.5,
        analyzer_versions={"smoke": "provider-smoke-v1"},
    )
    request = JevRequest(
        request_id="provider-smoke-jev", timestamp=now, environment=environment,
        frontier_hypothesis=hypothesis, quant_evidence=evidence, candidate_trade=candidate,
        questions=(), prompt_version=settings.jev.prompt_version,
    )
    client = JevClientFactory.create(_openai_provider(settings.jev.provider), prompt=load_prompt(Path(__file__).parent / settings.jev.prompt_file))
    evaluator = JevEvaluator(
        client, prompt=load_prompt(Path(__file__).parent / settings.jev.prompt_file),
        max_validity_seconds=settings.jev.validity_seconds, prompt_version=settings.jev.prompt_version,
        minimum_target_probability=settings.jev.minimum_target_probability,
        maximum_stop_probability=settings.jev.maximum_stop_probability,
        minimum_entry_quality=settings.jev.minimum_entry_quality,
        maximum_failure_probability=settings.jev.maximum_failure_probability,
        minimum_liquidity_quality=settings.jev.minimum_liquidity_quality,
    )
    result = {
        "component": "jev", "provider": getattr(client, "provider", settings.jev.provider.provider),
        "model": getattr(client, "model", settings.jev.provider.model),
        "connectivity": "PASS", "request": "PASS", "response_parsing": "PASS", "schema_validation": "PASS",
        "trading_execution": "NOT_INVOKED",
    }
    try:
        evaluation = evaluator.evaluate(request)
        result["recommended_state"] = evaluation.recommended_state.value
    except Exception as exc:
        fields = _failure_fields(exc)
        result.update({"request": "FAIL", **fields})
        if fields["error_category"] == "validation":
            result["schema_validation"] = "FAIL"
    return result


def run_provider_smoke(settings: LiveSettings, component: str = "both") -> list[dict[str, Any]]:
    if component == "frontier":
        return [smoke_frontier(settings)]
    if component == "jev":
        return [smoke_jev(settings)]
    return [smoke_frontier(settings), smoke_jev(settings)]

"""Jev factory, transport, expiry, thresholds, and cadence."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

from jev_trading.data.normalization import ReplayAdapter
from jev_trading.live_intelligence.config import ProviderConfig, load_settings
from jev_trading.live_intelligence.frontier.client import DisabledFrontierClient
from jev_trading.live_intelligence.jev.client import (
    DisabledJevClient,
    JevClientFactory,
    JevUnavailable,
    OpenAICompatibleJevClient,
    ReplayJevClient,
)
from jev_trading.live_intelligence.jev.evaluator import JevEvaluator
from jev_trading.live_intelligence.policy import make_candidate
from jev_trading.live_intelligence.runner import ShadowRunner
from jev_trading.live_intelligence.schemas import (
    JevEvaluation,
    JevRequest,
    JevState,
    PositionState,
    QuantEvidence,
)
from tests.test_live_intelligence import bars, environment, hypothesis

UTC = timezone.utc


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": __import__("json").dumps(self.payload)}}]}


def _request(now: datetime | None = None):
    settings = load_settings()
    timestamp = now or datetime.now(UTC)
    env = environment(now=timestamp)
    hyp = hypothesis().model_copy(update={"timestamp": timestamp})
    candidate = make_candidate(env, hyp, settings=settings)
    evidence = QuantEvidence(
        timestamp=timestamp, regime="MIXED", regime_confidence=0.5,
        analyzer_versions={"trend": "trend-v1"},
    )
    return JevRequest(
        request_id="request-1", timestamp=timestamp, environment=env,
        frontier_hypothesis=hyp, quant_evidence=evidence, candidate_trade=candidate,
        questions=(), prompt_version="jev-evaluator-v1",
    ), settings


def _enter_response(request: JevRequest, **overrides):
    values = {
        "decision_id": request.request_id,
        "request_id": request.request_id,
        "timestamp": request.timestamp,
        "valid_until": request.timestamp + timedelta(seconds=30),
        "probabilities": {"target": 0.6, "stop": 0.2, "timeout": 0.2},
        "target_probability": 0.6,
        "entry_quality": 0.8,
        "failure_risk": 0.1,
        "liquidity_quality": 0.8,
        "ratings": {},
        "answers": {},
        "confidence": 0.8,
        "recommended_state": "ENTER",
        "reason": "fixture",
        "model_version": "test",
        "prompt_version": request.prompt_version,
    }
    values.update(overrides)
    return JevEvaluation(**values)


class StaticClient:
    model_version = "test"

    def __init__(self, response):
        self.response = response

    def evaluate(self, request):
        return self.response


def _evaluator(client, settings):
    return JevEvaluator(
        client, prompt="versioned jev prompt", prompt_version="jev-evaluator-v1",
        max_validity_seconds=settings.jev.validity_seconds,
        minimum_target_probability=settings.jev.minimum_target_probability,
        maximum_stop_probability=settings.jev.maximum_stop_probability,
        minimum_entry_quality=settings.jev.minimum_entry_quality,
        maximum_failure_probability=settings.jev.maximum_failure_probability,
        minimum_liquidity_quality=settings.jev.minimum_liquidity_quality,
    )


def test_jev_factory_respects_provider_configuration(monkeypatch):
    assert isinstance(JevClientFactory.create(ProviderConfig(), prompt="p"), DisabledJevClient)
    assert isinstance(JevClientFactory.create(ProviderConfig(provider="replay"), prompt="p"), ReplayJevClient)
    monkeypatch.setenv("JEV_MODEL", "jev/model")
    client = JevClientFactory.create(ProviderConfig(provider="openai_compatible"), prompt="p")
    assert isinstance(client, OpenAICompatibleJevClient)
    assert client.model == "jev/model"
    assert client.prompt == "p"


def test_jev_transport_retries_and_parses(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret")
    request, _ = _request()
    client = OpenAICompatibleJevClient(
        base_url="https://example.invalid/v1", model="test/model", prompt="system",
        max_retries=2, retry_backoff_seconds=0,
    )
    calls = []

    def post(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) < 3:
            raise requests.ConnectionError("temporary")
        return _Response(_enter_response(request).model_dump(mode="json"))

    monkeypatch.setattr("jev_trading.live_intelligence.jev.client.requests.post", post)
    result = client.evaluate(request)
    assert result.request_id == request.request_id
    assert len(calls) == 3


def test_jev_transport_fails_closed_after_retries(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret")
    request, _ = _request()
    client = OpenAICompatibleJevClient(
        base_url="https://example.invalid/v1", model="test/model", prompt="system",
        max_retries=1, retry_backoff_seconds=0,
    )
    calls = []

    def post(*args, **kwargs):
        calls.append(1)
        raise requests.ConnectionError("temporary")

    monkeypatch.setattr("jev_trading.live_intelligence.jev.client.requests.post", post)
    with pytest.raises(JevUnavailable):
        client.evaluate(request)
    assert len(calls) == 2


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("target_probability", 0.1),
        ("entry_quality", 0.0),
        ("failure_risk", 1.0),
    ],
)
def test_enter_thresholds_fail_closed(field, value):
    request, settings = _request()
    response = _enter_response(request, **{field: value})
    with pytest.raises(ValueError):
        _evaluator(StaticClient(response), settings).evaluate(request)


def test_expired_jev_is_rejected_and_prompt_hash_is_stamped():
    timestamp = datetime.now(UTC) - timedelta(seconds=2)
    request, settings = _request(timestamp)
    response = _enter_response(request, valid_until=timestamp + timedelta(seconds=1))
    evaluator = _evaluator(StaticClient(response), settings)
    with pytest.raises(ValueError, match="expired"):
        evaluator.evaluate(request)
    valid = _evaluator(StaticClient(_enter_response(request)), settings).evaluate(request)
    assert len(valid.prompt_hash) == 64


def test_jev_cadence_and_rate_limit(tmp_path: Path):
    settings = load_settings().model_copy(update={
        "jev": load_settings().jev.model_copy(update={
            "min_call_interval_seconds": 60, "max_calls_per_hour": 1,
        }),
    })
    runner = ShadowRunner(
        settings, ReplayAdapter("primary", "binance-futures", "BTCUSDT_PERP", bars(), []),
        DisabledFrontierClient(), DisabledJevClient(), ledger_path=tmp_path / "ledger.jsonl",
    )
    now = datetime.now(UTC)
    assert runner.should_call_jev(now=now) is True
    runner._record_jev_call(now)
    assert runner.should_call_jev(now=now + timedelta(seconds=1)) is False
    assert runner.should_call_jev(now=now + timedelta(seconds=61)) is False
    assert runner.should_call_jev(now=now + timedelta(hours=1, seconds=1)) is True

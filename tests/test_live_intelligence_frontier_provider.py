"""Frontier factory, OpenRouter transport, prompt, and cadence tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

from jev_trading.data.normalization import ReplayAdapter
from jev_trading.live_intelligence.config import ProviderConfig, load_settings
from jev_trading.live_intelligence.frontier.client import (
    DisabledFrontierClient,
    FrontierClientFactory,
    FrontierUnavailable,
    OpenAICompatibleFrontierClient,
    ReplayFrontierClient,
)
from jev_trading.live_intelligence.frontier.strategist import FrontierStrategist
from jev_trading.live_intelligence.jev.client import DisabledJevClient
from jev_trading.live_intelligence.runner import ShadowRunner
from jev_trading.live_intelligence.schemas import QuantEvidence
from tests.test_live_intelligence import bars, environment


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": __import__("json").dumps(self.payload)}}]}


def _valid_payload(environment_value, request_id="request-1"):
    return {
        "hypothesis_id": request_id,
        "timestamp": environment_value.decision_timestamp.isoformat(),
        "regime": "MIXED",
        "regime_confidence": 0.5,
        "primary_strategy": "momentum",
        "direction": "LONG",
        "horizon_seconds": 900,
        "thesis": "test",
        "entry_conditions": ["test"],
        "invalidation_conditions": ["test"],
        "target_logic": "test",
        "stop_logic": "test",
        "maximum_holding_seconds": 900,
        "abstain": False,
        "reason": "test",
        "conviction": 0.5,
        "model_version": "test",
        "prompt_version": "frontier-strategist-v1",
    }


def test_factory_respects_disabled_replay_and_openai_configuration(monkeypatch):
    assert isinstance(FrontierClientFactory.create(ProviderConfig()), DisabledFrontierClient)
    assert isinstance(FrontierClientFactory.create(ProviderConfig(provider="replay")), ReplayFrontierClient)
    monkeypatch.setenv("FRONTIER_MODEL", "provider/model")
    client = FrontierClientFactory.create(ProviderConfig(provider="openai_compatible"))
    assert isinstance(client, OpenAICompatibleFrontierClient)
    assert client.model == "provider/model"
    assert client.base_url == "https://openrouter.ai/api/v1"


def test_openai_transport_retries_and_does_not_expose_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-test-value")
    client = OpenAICompatibleFrontierClient(
        base_url="https://example.invalid/v1", model="test/model", max_retries=2,
        retry_backoff_seconds=0,
    )
    env = environment()
    calls = []

    def fail_twice(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) < 3:
            raise requests.ConnectionError("temporary")
        return _Response(_valid_payload(env))

    monkeypatch.setattr("jev_trading.live_intelligence.frontier.client.requests.post", fail_twice)
    result = client.complete(system_prompt="system", payload={"environment": env.model_dump(mode="json")})
    assert result["hypothesis_id"] == "request-1"
    assert len(calls) == 3
    assert all(call["headers"]["Authorization"] == "Bearer secret-test-value" for call in calls)


def test_openai_transport_accepts_fenced_json_content(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-test-value")
    env = environment()
    payload = _valid_payload(env)
    response = _Response(payload)
    response.json = lambda: {"choices": [{"message": {"content": "```json\n" + __import__("json").dumps(payload) + "\n```"}}]}
    monkeypatch.setattr("jev_trading.live_intelligence.frontier.client.requests.post", lambda *args, **kwargs: response)
    client = OpenAICompatibleFrontierClient(base_url="https://example.invalid/v1", model="test/model")
    assert client.complete(system_prompt="system", payload={})["hypothesis_id"] == "request-1"


def test_openai_transport_fails_closed_after_bounded_retries(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-test-value")
    client = OpenAICompatibleFrontierClient(
        base_url="https://example.invalid/v1", model="test/model", max_retries=1,
        retry_backoff_seconds=0,
    )
    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise requests.ConnectionError("temporary")

    monkeypatch.setattr("jev_trading.live_intelligence.frontier.client.requests.post", fail)
    with pytest.raises(FrontierUnavailable):
        client.complete(system_prompt="system", payload={})
    assert len(calls) == 2


def test_prompt_hash_and_quant_context_are_recorded():
    env = environment()
    evidence = QuantEvidence(
        timestamp=env.decision_timestamp, regime="MIXED", regime_confidence=0.5,
        analyzer_versions={"trend": "trend-v1"},
    )
    captured = {}

    class Capturing(ReplayFrontierClient):
        def complete(self, *, system_prompt, payload):
            captured.update(payload)
            return _valid_payload(env, payload["request_id"])

    strategist = FrontierStrategist(
        Capturing(allow_trade=True), prompt="versioned prompt", prompt_version="frontier-strategist-v1",
    )
    result = strategist.generate(env, request_id="request-1", quant_evidence=evidence)
    assert len(result.prompt_hash) == 64
    assert captured["quant_evidence"]["analyzer_versions"] == {"trend": "trend-v1"}


def test_frontier_cadence_is_enforced_independently_of_rate_limit(tmp_path: Path):
    settings = load_settings().model_copy(update={
        "frontier": load_settings().frontier.model_copy(update={
            "min_call_interval_seconds": 900, "max_calls_per_hour": 12,
        }),
    })
    runner = ShadowRunner(
        settings, ReplayAdapter("primary", "binance-futures", "BTCUSDT_PERP", bars(), []),
        DisabledFrontierClient(), DisabledJevClient(), ledger_path=tmp_path / "ledger.jsonl",
    )
    now = datetime.now(timezone.utc)
    assert runner.should_call_frontier([], now=now) is True
    runner._record_frontier_call(now)
    assert runner.should_call_frontier([], now=now + timedelta(seconds=1)) is False
    assert runner.should_call_frontier([], now=now + timedelta(seconds=901)) is True


def test_frontier_hourly_rate_limit(tmp_path: Path):
    settings = load_settings().model_copy(update={
        "frontier": load_settings().frontier.model_copy(update={
            "min_call_interval_seconds": 0, "max_calls_per_hour": 1,
        }),
    })
    runner = ShadowRunner(
        settings, ReplayAdapter("primary", "binance-futures", "BTCUSDT_PERP", bars(), []),
        DisabledFrontierClient(), DisabledJevClient(), ledger_path=tmp_path / "ledger.jsonl",
    )
    now = datetime.now(timezone.utc)
    assert runner.should_call_frontier([], now=now) is True
    runner._record_frontier_call(now)
    assert runner.should_call_frontier([], now=now + timedelta(seconds=1)) is False
    assert runner.should_call_frontier([], now=now + timedelta(hours=1, seconds=1)) is True


def test_mismatched_frontier_id_is_rejected():
    env = environment()

    class WrongId(ReplayFrontierClient):
        def complete(self, *, system_prompt, payload):
            raw = super().complete(system_prompt=system_prompt, payload=payload)
            raw["hypothesis_id"] = "wrong-id"
            return raw

    strategist = FrontierStrategist(
        WrongId(allow_trade=True), prompt="prompt", prompt_version="frontier-strategist-v1",
    )
    with pytest.raises(ValueError, match="hypothesis_id"):
        strategist.generate(env, request_id="request-1")

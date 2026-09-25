"""Explicit provider capability and independent Frontier/Jev configuration tests."""
from __future__ import annotations

import json

from jev_trading.live_intelligence.config import ProviderConfig
from jev_trading.live_intelligence.frontier.client import FrontierClientFactory, OpenAICompatibleFrontierClient
from jev_trading.live_intelligence.jev.client import JevClientFactory, OpenAICompatibleJevClient


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": json.dumps(self.payload)}}]}


def test_frontier_request_omits_response_format_when_unsupported(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    payload = {"ok": True}
    calls = []
    monkeypatch.setattr(
        "jev_trading.live_intelligence.frontier.client.requests.post",
        lambda *args, **kwargs: calls.append(kwargs) or Response(payload),
    )
    client = OpenAICompatibleFrontierClient(
        base_url="https://example.invalid/v1", model="test/model", supports_response_format=False,
    )
    assert client.complete(system_prompt="system", payload={}) == payload
    assert "response_format" not in calls[0]["json"]


def test_jev_request_includes_response_format_when_supported(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    calls = []
    monkeypatch.setattr(
        "jev_trading.live_intelligence.jev.client.requests.post",
        lambda *args, **kwargs: calls.append(kwargs) or Response({
            "decision_id": "r", "request_id": "r", "timestamp": "2026-01-01T00:00:00Z",
            "valid_until": "2026-01-01T00:00:30Z", "recommended_state": "NO_TRADE",
            "reason": "test", "model_version": "m", "prompt_version": "jev-evaluator-v1",
        }),
    )
    client = OpenAICompatibleJevClient(
        base_url="https://example.invalid/v1", model="test/model", prompt="system",
        supports_response_format=True,
    )
    class Request:
        request_id = "r"
        def model_dump(self, **kwargs):
            return {"request_id": "r"}
    try:
        client.evaluate(Request())
    except Exception:
        pass
    assert calls and calls[0]["json"]["response_format"] == {"type": "json_object"}


def test_non_retryable_provider_error_is_not_retried(monkeypatch):
    import requests
    from jev_trading.live_intelligence.frontier.client import FrontierUnavailable

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    calls = []
    response = requests.Response()
    response.status_code = 400
    def fail(*args, **kwargs):
        calls.append(1)
        raise requests.HTTPError(response=response)
    monkeypatch.setattr("jev_trading.live_intelligence.frontier.client.requests.post", fail)
    client = OpenAICompatibleFrontierClient(base_url="https://example.invalid/v1", model="test/model", max_retries=2, retry_backoff_seconds=0)
    try:
        client.complete(system_prompt="system", payload={"request_id": "r"})
    except FrontierUnavailable as exc:
        assert exc.category == "invalid_request"
        assert exc.http_status == 400
        assert exc.retry_count == 1
    else:
        raise AssertionError("provider error should fail closed")
    assert len(calls) == 1


def test_frontier_and_jev_factories_use_independent_environment(monkeypatch):
    monkeypatch.setenv("FRONTIER_BASE_URL", "https://frontier.example/v1")
    monkeypatch.setenv("JEV_BASE_URL", "https://jev.example/v1")
    monkeypatch.setenv("FRONTIER_MODEL", "frontier/model")
    monkeypatch.setenv("JEV_MODEL", "jev/model")
    monkeypatch.setenv("FRONTIER_SUPPORTS_RESPONSE_FORMAT", "true")
    monkeypatch.setenv("JEV_SUPPORTS_RESPONSE_FORMAT", "false")
    config = ProviderConfig(provider="openai_compatible", model="configured-at-deployment")
    frontier = FrontierClientFactory.create(config)
    jev = JevClientFactory.create(config, prompt="system")
    assert isinstance(frontier, OpenAICompatibleFrontierClient)
    assert isinstance(jev, OpenAICompatibleJevClient)
    assert frontier.base_url == "https://frontier.example/v1"
    assert jev.base_url == "https://jev.example/v1"
    assert frontier.supports_response_format is True
    assert jev.supports_response_format is False
    assert frontier.model == "frontier/model"
    assert jev.model == "jev/model"

"""Typed tool-call contracts, adapters, and authority-boundary tests."""
from __future__ import annotations

import json

import pytest

from jev_trading.live_intelligence.config import load_settings
from jev_trading.live_intelligence.frontier.client import FrontierUnavailable, OpenAICompatibleFrontierClient
from jev_trading.live_intelligence.frontier.strategist import FrontierStrategist
from jev_trading.live_intelligence.jev.client import JevUnavailable, OpenAICompatibleJevClient
from jev_trading.live_intelligence.jev.evaluator import JevEvaluator
from jev_trading.live_intelligence.model_contracts import FRONTIER_TOOL, JEV_TOOL
from tests.test_live_intelligence import environment
from tests.test_live_intelligence_jev_provider import _request


class Response:
    status_code = 200

    def __init__(self, message):
        self.message = message

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": self.message}]}


def _frontier_args(**overrides):
    value = {
        "regime": "BULLISH", "strategy_family": "momentum", "direction": "LONG",
        "confidence": 0.7, "abstain": False, "thesis": "Trend evidence",
        "entry_conditions": ["trend_alignment"],
        "invalidation_conditions": ["trend_transition"],
    }
    value.update(overrides)
    return value


def _jev_args(**overrides):
    value = {
        "recommended_state": "WAIT", "abstain": False, "confidence": 0.6,
        "entry_quality": 0.7, "failure_risk": 0.2, "liquidity_quality": 0.8,
        "reason": "Evidence is not strong enough",
    }
    value.update(overrides)
    return value


def test_tool_schemas_are_inlined_and_typed():
    assert "$defs" not in FRONTIER_TOOL["function"]["parameters"]
    assert "$defs" not in JEV_TOOL["function"]["parameters"]
    assert FRONTIER_TOOL["function"]["name"] == "submit_strategy_hypothesis"
    assert JEV_TOOL["function"]["name"] == "submit_jev_evaluation"
    assert "quantity" not in json.dumps(FRONTIER_TOOL)
    assert "leverage" not in json.dumps(JEV_TOOL)


def test_frontier_tool_call_adapts_to_domain(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    message = {"tool_calls": [{"function": {"name": "submit_strategy_hypothesis", "arguments": json.dumps(_frontier_args())}}]}
    monkeypatch.setattr("jev_trading.live_intelligence.frontier.client.requests.post", lambda *a, **k: Response(message))
    settings = load_settings()
    client = OpenAICompatibleFrontierClient(
        base_url="https://example.invalid/v1", model="test/model", provider="openai_compatible",
        supports_tool_calling=True, tool=FRONTIER_TOOL,
    )
    strategist = FrontierStrategist(client, prompt="p", prompt_version="frontier-strategist-v1", settings=settings)
    result = strategist.generate(environment(), request_id="tool-frontier")
    assert result.interface_mode == "tool_call"
    assert result.tool_name == "submit_strategy_hypothesis"
    assert result.primary_strategy == "momentum"
    assert result.direction.value == "LONG"


def test_frontier_tool_authority_field_is_rejected(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    args = _frontier_args(quantity=10)
    message = {"tool_calls": [{"function": {"name": "submit_strategy_hypothesis", "arguments": json.dumps(args)}}]}
    monkeypatch.setattr("jev_trading.live_intelligence.frontier.client.requests.post", lambda *a, **k: Response(message))
    settings = load_settings()
    client = OpenAICompatibleFrontierClient(
        base_url="https://example.invalid/v1", model="test/model",
        supports_tool_calling=True, tool=FRONTIER_TOOL,
    )
    strategist = FrontierStrategist(client, prompt="p", prompt_version="frontier-strategist-v1", settings=settings)
    with pytest.raises(FrontierUnavailable, match="validation"):
        strategist.generate(environment(), request_id="tool-authority")


def test_frontier_missing_or_multiple_tool_calls_fail_closed(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    settings = load_settings()
    client = OpenAICompatibleFrontierClient(
        base_url="https://example.invalid/v1", model="test/model",
        supports_tool_calling=True, tool=FRONTIER_TOOL,
    )
    strategist = FrontierStrategist(client, prompt="p", prompt_version="frontier-strategist-v1", settings=settings)
    monkeypatch.setattr("jev_trading.live_intelligence.frontier.client.requests.post", lambda *a, **k: Response({"content": "{}"}))
    with pytest.raises(FrontierUnavailable):
        strategist.generate(environment(), request_id="no-tool")
    multiple = {"tool_calls": [
        {"function": {"name": "submit_strategy_hypothesis", "arguments": json.dumps(_frontier_args())}},
        {"function": {"name": "submit_strategy_hypothesis", "arguments": json.dumps(_frontier_args())}},
    ]}
    monkeypatch.setattr("jev_trading.live_intelligence.frontier.client.requests.post", lambda *a, **k: Response(multiple))
    with pytest.raises(FrontierUnavailable):
        strategist.generate(environment(), request_id="multiple-tools")


def test_jev_tool_call_adapts_deterministic_probabilities(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    message = {"tool_calls": [{"function": {"name": "submit_jev_evaluation", "arguments": json.dumps(_jev_args())}}]}
    monkeypatch.setattr("jev_trading.live_intelligence.jev.client.requests.post", lambda *a, **k: Response(message))
    request, settings = _request()
    client = OpenAICompatibleJevClient(
        base_url="https://example.invalid/v1", model="test/model", prompt="p",
        supports_tool_calling=True, tool=JEV_TOOL,
    )
    evaluator = JevEvaluator(
        client, prompt="p", settings=settings, prompt_version=settings.jev.prompt_version,
    )
    result = evaluator.evaluate(request)
    assert result.interface_mode == "tool_call"
    assert result.tool_name == "submit_jev_evaluation"
    assert result.recommended_state.value == "WAIT"
    assert result.entry_quality == 0.7

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
    # The v2 mandate: action, a machine-checkable entry rule, an invalidation
    # and a relative expiry. No size, ever.
    value = {
        "regime": "BULLISH", "strategy_family": "momentum", "action": "LONG",
        "confidence": 0.7, "abstain": False, "thesis": "Trend evidence",
        "entry_trigger": "ON_CONDITION",
        "entry_condition": {"kind": "price_above", "value": 85000.0, "timeframe": "15m"},
        "invalidation_conditions": [{"kind": "trend_down", "timeframe": "15m"}],
        "expires_in_seconds": 3600,
        "intended_holding_seconds": 14400,
        "proposed_stop_bps": 120.0,
        "proposed_target_bps": 300.0,
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


def _tool_field_names(tool):
    """Every property name anywhere in a tool's parameter schema."""
    found, stack = set(), [tool["function"]["parameters"]]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            found |= set(node.get("properties", {}))
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return found


def test_tool_schemas_are_inlined_and_typed():
    assert "$defs" not in FRONTIER_TOOL["function"]["parameters"]
    assert "$defs" not in JEV_TOOL["function"]["parameters"]
    assert FRONTIER_TOOL["function"]["name"] == "submit_trade_proposal"
    assert JEV_TOOL["function"]["name"] == "submit_jev_evaluation"
    # The invariant is that no *field* grants sizing authority -- not that the
    # words never appear, since the descriptions must state the prohibition.
    for tool in (FRONTIER_TOOL, JEV_TOOL):
        assert not _tool_field_names(tool) & {
            "size", "quantity", "notional", "leverage", "order", "buy", "sell", "size_pct",
        }


def test_proposal_tool_exposes_the_bounded_mandate():
    names = _tool_field_names(FRONTIER_TOOL)
    # The model may decide these...
    assert {
        "action", "entry_trigger", "entry_condition", "invalidation_conditions",
        "expires_in_seconds", "intended_holding_seconds",
        "proposed_stop_bps", "proposed_target_bps",
    } <= names
    # ...and the exit / stay-flat choices must be expressible.
    action = FRONTIER_TOOL["function"]["parameters"]["properties"]["action"]
    assert set(action["enum"]) == {"LONG", "SHORT", "EXIT", "HOLD", "FLAT"}


def test_frontier_tool_call_adapts_to_domain(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    message = {"tool_calls": [{"function": {"name": "submit_trade_proposal", "arguments": json.dumps(_frontier_args())}}]}
    monkeypatch.setattr("jev_trading.live_intelligence.frontier.client.requests.post", lambda *a, **k: Response(message))
    settings = load_settings()
    client = OpenAICompatibleFrontierClient(
        base_url="https://example.invalid/v1", model="test/model", provider="openai_compatible",
        supports_tool_calling=True, tool=FRONTIER_TOOL,
    )
    strategist = FrontierStrategist(client, prompt="p", prompt_version="frontier-strategist-v1", settings=settings)
    result = strategist.generate(environment(), request_id="tool-frontier")
    assert result.interface_mode == "tool_call"
    assert result.tool_name == "submit_trade_proposal"
    assert result.primary_strategy == "momentum"
    assert result.direction.value == "LONG"
    # The domain object carries the bounded proposal, with an absolute expiry
    # the application stamped and a direction derived from the action.
    assert result.trade_proposal is not None
    assert result.trade_proposal.action == "LONG"
    assert result.trade_proposal.entry_condition.kind == "price_above"
    assert result.trade_proposal.expires_at > result.trade_proposal.timestamp


def test_frontier_tool_authority_field_is_rejected(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    args = _frontier_args(quantity=10)
    message = {"tool_calls": [{"function": {"name": "submit_trade_proposal", "arguments": json.dumps(args)}}]}
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
        {"function": {"name": "submit_trade_proposal", "arguments": json.dumps(_frontier_args())}},
        {"function": {"name": "submit_trade_proposal", "arguments": json.dumps(_frontier_args())}},
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

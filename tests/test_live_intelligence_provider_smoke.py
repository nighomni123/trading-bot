"""Provider JSON normalization and non-trading smoke command tests."""
from __future__ import annotations

import json

import pytest

from jev_trading.live_intelligence import cli
from jev_trading.live_intelligence.frontier.client import _parse_json_object


def test_json_normalization_accepts_safe_shapes():
    payload = {"ok": True, "nested": {"value": 1}}
    encoded = json.dumps(payload)
    assert _parse_json_object(encoded) == payload
    assert _parse_json_object(f"  \n{encoded}\n  ") == payload
    assert _parse_json_object(f"```json\n{encoded}\n```") == payload
    assert _parse_json_object(f"Here is the requested object:\n{encoded}") == payload
    assert _parse_json_object(f"{encoded}\nEnd of response.") == payload


def test_json_normalization_rejects_malformed_or_non_object():
    with pytest.raises(ValueError):
        _parse_json_object("{not-json}")
    with pytest.raises(ValueError):
        _parse_json_object("plain text only")
    with pytest.raises(ValueError):
        _parse_json_object("[1, 2, 3]")


def test_provider_smoke_cli_reports_not_invoked(monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_settings", lambda path: object())
    monkeypatch.setattr(cli, "run_provider_smoke", lambda settings, component: [{
        "component": component, "request": "PASS", "trading_execution": "NOT_INVOKED",
    }])
    assert cli.main(["provider-smoke", "--component", "frontier"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["trading_execution"] == "NOT_INVOKED"
    assert output["provider_smoke"][0]["component"] == "frontier"

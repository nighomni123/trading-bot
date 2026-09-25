"""Deterministic interface-only benchmark harness tests."""
from __future__ import annotations

from jev_trading.live_intelligence import provider_benchmark
from jev_trading.live_intelligence.config import load_settings


def test_benchmark_aggregates_mock_cases_without_execution(monkeypatch):
    def frontier(settings, index):
        return {"component": "frontier", "case": index, "ok": True, "interface_mode": "tool_call", "tool_name": "submit_strategy_hypothesis", "latency_ms": 10 + index}
    def jev(settings, index):
        return {"component": "jev", "case": index, "ok": True, "interface_mode": "tool_call", "tool_name": "submit_jev_evaluation", "latency_ms": 20 + index}
    monkeypatch.setattr(provider_benchmark, "_run_frontier", frontier)
    monkeypatch.setattr(provider_benchmark, "_run_jev", jev)
    result = provider_benchmark.run_benchmark(load_settings(), cases=2, workers=2)
    assert result["summary"]["total"] == 4
    assert result["summary"]["valid"] == 4
    assert result["summary"]["tool_call_successes"] == 4
    assert result["summary"]["trading_execution"] == "NOT_INVOKED"

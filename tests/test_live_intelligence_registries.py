"""Active strategy and hypothesis registry invariants."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from jev_trading.research import HypothesisRegistry, StrategyRegistry
from jev_trading.live_intelligence.schemas import ResearchHypothesis


def test_static_strategy_registry_validates_market_regime_and_version():
    registry = StrategyRegistry("research/strategies/registry.json")
    definition = registry.validate("momentum", market="BTCUSDT_PERP", regime="BULLISH")
    assert definition.version == "v1"
    assert registry.is_allowed("momentum", market="BTCUSDT_PERP", regime="MIXED")
    assert not registry.is_allowed("unknown", market="BTCUSDT_PERP", regime="MIXED")
    assert not registry.is_allowed("momentum", market="BTCUSD_SPOT", regime="MIXED")


def test_hypothesis_transition_graph_and_evidence_are_enforced(tmp_path: Path):
    registry = HypothesisRegistry(tmp_path)
    hypothesis = ResearchHypothesis(
        hypothesis_id="H-1", date_created=datetime.now(timezone.utc), author="human",
        market="BTCUSDT_PERP", regime="MIXED", strategy="momentum", rationale="test",
        required_evidence=(), success_criteria=(), failure_criteria=(),
    )
    registry.create(hypothesis)
    with pytest.raises(ValueError):
        registry.transition("H-1", "SUPPORTED", evidence="not allowed from proposed")
    with pytest.raises(ValueError):
        registry.transition("H-1", "TESTING", evidence="")
    path = registry.transition("H-1", "TESTING", evidence="frozen test window opened")
    updated = ResearchHypothesis.model_validate(__import__("json").loads(path.read_text()))
    assert updated.status == "TESTING"
    assert updated.transition_evidence == ("frozen test window opened",)

"""Operational strategy and human-controlled hypothesis registries."""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from ..live_intelligence.schemas import ResearchHypothesis

VALID = {"PROPOSED", "TESTING", "SUPPORTED", "REJECTED", "DEFERRED", "PROMOTED"}
ALLOWED_TRANSITIONS = {
    "PROPOSED": {"TESTING", "DEFERRED", "REJECTED"},
    "TESTING": {"SUPPORTED", "REJECTED", "DEFERRED", "TESTING"},
    "SUPPORTED": {"PROMOTED", "REJECTED", "DEFERRED"},
    "REJECTED": {"TESTING", "DEFERRED"},
    "DEFERRED": {"TESTING", "REJECTED"},
    "PROMOTED": set(),
}


class StrategyDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    strategy_id: str
    version: str
    family: str
    market: str = "BTCUSDT_PERP"
    allowed_regimes: tuple[str, ...] = ("BULLISH", "BEARISH", "MIXED")
    description: str
    required_market_conditions: tuple[str, ...] = ()
    entry_conditions: tuple[str, ...] = ()
    entry_logic: str = "validated_policy_entry"
    exit_conditions: tuple[str, ...] = ()
    exit_logic: str = "stop_target_timeout_or_jev_exit"
    invalidations: tuple[str, ...] = ()
    allowed_horizons: tuple[str, ...] = ()
    quant_requirements: tuple[str, ...] = ()
    jev_requirements: tuple[str, ...] = ()
    risk_profile: str
    risk_assumptions: tuple[str, ...] = ()
    status: str

    @classmethod
    def from_mapping(cls, value: dict) -> "StrategyDefinition":
        return cls.model_validate({**value, "allowed_regimes": tuple(value.get("allowed_regimes", ("BULLISH", "BEARISH", "MIXED")))})


class StrategyRegistry:
    def __init__(self, path: str | Path = "research/strategies/registry.json"):
        self.path = Path(path)
        payload = json.loads(self.path.read_text())
        self.registry_version = payload.get("registry_version", "unknown")
        self.status = payload.get("status", "RESEARCH_ONLY")
        self._strategies = {
            definition.strategy_id: definition
            for definition in (StrategyDefinition.from_mapping(item) for item in payload.get("strategies", []))
        }

    def get(self, strategy_id: str) -> StrategyDefinition | None:
        return self._strategies.get(strategy_id)

    def is_allowed(self, strategy_id: str, *, market: str, regime: str) -> bool:
        definition = self.get(strategy_id)
        return bool(
            definition
            and definition.market == market
            and regime in definition.allowed_regimes
            and definition.status in {"PROPOSED", "TESTING", "SUPPORTED", "PROMOTED"}
        )

    def validate(self, strategy_id: str, *, market: str, regime: str) -> StrategyDefinition:
        definition = self.get(strategy_id)
        if definition is None or not self.is_allowed(strategy_id, market=market, regime=regime):
            raise ValueError(f"strategy is not registered/allowed: {strategy_id}")
        return definition


class HypothesisRegistry:
    def __init__(self, root: str | Path = "research/hypotheses"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, hypothesis: ResearchHypothesis) -> Path:
        if hypothesis.status not in VALID:
            raise ValueError("invalid hypothesis status")
        path = self.root / f"{hypothesis.hypothesis_id}.json"
        if path.exists():
            raise FileExistsError(path)
        path.write_text(hypothesis.model_dump_json(indent=2) + "\n")
        return path

    def transition(self, hypothesis_id: str, status: str, *, evidence: str) -> Path:
        if status not in VALID:
            raise ValueError("invalid hypothesis status")
        if not evidence.strip():
            raise ValueError("hypothesis transition requires evidence")
        source = self.root / f"{hypothesis_id}.json"
        if not source.exists():
            raise FileNotFoundError(source)
        old = ResearchHypothesis.model_validate(json.loads(source.read_text()))
        if status not in ALLOWED_TRANSITIONS.get(old.status, set()):
            raise ValueError(f"invalid hypothesis transition: {old.status}->{status}")
        updated = old.model_copy(update={
            "status": status,
            "revision": old.revision + 1,
            "transition_evidence": old.transition_evidence + (evidence,),
            "supersedes": hypothesis_id,
        })
        target = self.root / f"{hypothesis_id}_r{updated.revision}.json"
        if target.exists():
            raise FileExistsError(target)
        target.write_text(updated.model_dump_json(indent=2) + "\n")
        return target

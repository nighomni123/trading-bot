"""Replay recorded decisions with state reconstruction and no provider calls."""
from __future__ import annotations

from pathlib import Path

from ..ledger import DecisionLedger
from ..live_intelligence.schemas import DecisionRecord


class ReplayEngine:
    def __init__(self, path: str | Path):
        self.ledger = DecisionLedger(path)
        self.ledger.verify()

    def records(self) -> tuple[DecisionRecord, ...]:
        return self.ledger.records()

    def replay(self) -> tuple[DecisionRecord, ...]:
        """Verify the complete hash chain and return validated records."""
        return self.records()

    def reconstruct(self, decision_id: str) -> dict:
        record = next((item for item in self.records() if item.decision_id == decision_id), None)
        if record is None:
            raise KeyError(decision_id)
        if record.market_environment.decision_timestamp != record.timestamp:
            raise ValueError("replay timestamp mismatch")
        if record.policy_decision.timestamp != record.timestamp:
            raise ValueError("replay policy timestamp mismatch")
        if record.risk_decision.timestamp != record.timestamp:
            raise ValueError("replay risk timestamp mismatch")
        if record.quant_evidence is not None and tuple(record.quant_analyses) != tuple(record.quant_evidence.analyzer_results):
            raise ValueError("replay Quant evidence does not match analyzer records")
        if record.jev_request is not None and record.quant_evidence is not None and record.jev_request.quant_evidence != record.quant_evidence:
            raise ValueError("replay Jev evidence does not match decision evidence")
        intent = record.execution_intent
        fills = tuple(fill for fill in self.ledger.fills() if fill.decision_id == record.decision_id)
        if intent is not None:
            if not any(fill.intent_id == intent.intent_id for fill in fills):
                raise ValueError("replay execution intent has no matching fill")
        elif fills:
            raise ValueError("replay decision has fill without execution intent")
        trades = tuple(
            trade for trade in self.ledger.trades()
            if record.decision_id in {trade.entry_decision_id, trade.exit_decision_id}
        )
        return {
            "decision_id": record.decision_id,
            "timestamp": record.timestamp.isoformat(),
            "known_state": {
                "market_environment": record.market_environment.model_dump(mode="json"),
                "frontier_hypothesis": record.frontier_hypothesis.model_dump(mode="json") if record.frontier_hypothesis else None,
                "quant_evidence": record.quant_evidence.model_dump(mode="json") if record.quant_evidence else None,
                "jev_evaluation": record.jev_evaluation.model_dump(mode="json") if record.jev_evaluation else None,
            },
            "deterministic_decision": {
                "policy": record.policy_decision.model_dump(mode="json"),
                "risk": record.risk_decision.model_dump(mode="json"),
                "execution_intent": intent.model_dump(mode="json") if intent else None,
            },
            "execution": {
                "fills": [fill.model_dump(mode="json") for fill in fills],
                "trades": [trade.model_dump(mode="json") for trade in trades],
            },
            "versions": record.versions.model_dump(mode="json"),
        }

    def reconstruct_all(self) -> list[dict]:
        return [self.reconstruct(record.decision_id) for record in self.records()]

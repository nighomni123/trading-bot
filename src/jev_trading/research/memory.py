"""Immutable research memory and report generation."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from ..ledger import DecisionLedger
from ..live_intelligence.schemas import DecisionRecord, ResearchHypothesis, ResearchObservation


class ResearchMemory:
    def __init__(self, root: str | Path = "research"):
        self.root = Path(root)
        self.observations_path = self.root / "observations.jsonl"
        self.hypotheses_path = self.root / "hypotheses"
        self.postmortems_path = self.root / "postmortems"
        for path in (self.root / "hourly", self.root / "daily", self.root / "weekly", self.hypotheses_path, self.postmortems_path):
            path.mkdir(parents=True, exist_ok=True)

    def record(self, observation: ResearchObservation) -> None:
        with self.observations_path.open("a", encoding="utf-8") as handle:
            handle.write(observation.model_dump_json() + "\n")

    def add_hypothesis(self, hypothesis: ResearchHypothesis) -> Path:
        target = self.hypotheses_path / f"{hypothesis.hypothesis_id}.json"
        if target.exists():
            raise FileExistsError(f"hypothesis already exists: {target}")
        target.write_text(hypothesis.model_dump_json(indent=2) + "\n")
        return target

    def generate(self, ledger: DecisionLedger, period: str, timestamp: datetime | None = None) -> Path:
        if period not in {"hourly", "daily", "weekly"}:
            raise ValueError("period must be hourly, daily, or weekly")
        now = timestamp or datetime.now(tz=timezone.utc)
        records = ledger.records()
        actions = Counter(record.policy_decision.action.value for record in records)
        fills = ledger.fills()
        trades = ledger.trades()
        lines = [
            f"# {period.title()} Research — {now.isoformat()}",
            "",
            "## Environment",
            f"Decisions recorded: {len(records)}",
            "",
            "## Decisions",
            *[f"- {action}: {count}" for action, count in sorted(actions.items())],
            "",
            "## Execution",
            f"Paper fills: {len(fills)}",
            f"Closed paper trades: {len(trades)}",
            "",
            "## Safety",
            "- This report is generated from structured ledger data.",
            "- It never changes prompts, thresholds, risk, strategies, or execution permissions.",
        ]
        directory = self.root / period
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{now.strftime('%Y%m%dT%H%M%SZ')}.md"
        with target.open("x", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        return target

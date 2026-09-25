"""Research hypothesis registry with explicit, human-controlled transitions."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..live_intelligence.schemas import ResearchHypothesis

VALID = {"PROPOSED", "TESTING", "SUPPORTED", "REJECTED", "DEFERRED", "PROMOTED"}


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
        source = self.root / f"{hypothesis_id}.json"
        if not source.exists():
            raise FileNotFoundError(source)
        old = ResearchHypothesis.model_validate(json.loads(source.read_text()))
        if old.status == "PROMOTED":
            raise ValueError("promoted hypotheses require a new version/experiment")
        updated = old.model_copy(update={"status": status, "revision": old.revision + 1, "supersedes": hypothesis_id})
        target = self.root / f"{hypothesis_id}_r{updated.revision}.json"
        target.write_text(updated.model_dump_json(indent=2) + "\n")
        return target

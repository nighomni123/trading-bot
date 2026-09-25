"""Replay a recorded decision ledger without calling external providers."""
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
        """Return recorded decisions after verifying the complete hash chain."""
        return self.records()

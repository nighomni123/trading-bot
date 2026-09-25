"""Append-only structured decision ledger for the active loop."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable

from ..live_intelligence.schemas import DecisionRecord, PaperFill, PositionState, TradeRecord


def _canonical(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _hash(value: dict, previous: str | None) -> str:
    payload = _canonical({key: item for key, item in value.items() if key != "record_hash"}) + (previous or "")
    return hashlib.sha256(payload.encode()).hexdigest()


class DecisionLedger:
    """Hash-chained JSONL ledger; records cannot be silently overwritten."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._records: dict[str, DecisionRecord] = {}
        self._fills: dict[str, PaperFill] = {}
        self._trades: list[TradeRecord] = []
        self._positions: dict[str, PositionState] = {}
        self._last_hash: str | None = None
        if self.path.exists():
            self.load()

    def _append(self, kind: str, value: dict, decision_id: str | None = None) -> dict:
        entry = {"type": kind, **value}
        entry["previous_hash"] = self._last_hash
        entry["record_hash"] = _hash(entry, self._last_hash)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(_canonical(entry) + "\n")
        self._last_hash = entry["record_hash"]
        return entry

    def append_decision(self, record: DecisionRecord) -> None:
        if record.decision_id in self._records:
            raise ValueError(f"duplicate decision_id: {record.decision_id}")
        self._append("decision", record.model_dump(mode="json"), record.decision_id)
        self._records[record.decision_id] = record
        self._positions[record.decision_id] = record.position_after or record.position_before

    def append_fill(self, fill: PaperFill) -> None:
        if fill.decision_id not in self._records:
            raise ValueError("fill requires an existing decision record")
        if fill.fill_id in self._fills:
            raise ValueError(f"duplicate fill_id: {fill.fill_id}")
        self._append("fill", fill.model_dump(mode="json"), fill.decision_id)
        self._fills[fill.fill_id] = fill

    def append_trade(self, trade: TradeRecord) -> None:
        if trade.entry_decision_id not in self._records or trade.exit_decision_id not in self._records:
            raise ValueError("trade requires existing entry and exit decision records")
        self._append("trade", trade.model_dump(mode="json"), trade.exit_decision_id)
        self._trades.append(trade)

    def load(self) -> None:
        self._records.clear(); self._fills.clear(); self._trades.clear(); self._positions.clear(); self._last_hash = None
        previous = None
        for line_no, raw in enumerate(self.path.read_text().splitlines(), 1):
            if not raw:
                raise ValueError(f"blank ledger line {line_no}")
            entry = json.loads(raw)
            if entry.get("previous_hash") != previous:
                raise ValueError(f"ledger chain break at line {line_no}")
            expected = _hash(entry, previous)
            if entry.get("record_hash") != expected:
                raise ValueError(f"ledger hash mismatch at line {line_no}")
            kind = entry.get("type")
            if kind == "decision":
                payload = {key: value for key, value in entry.items() if key not in {"type", "previous_hash", "record_hash"}}
                record = DecisionRecord.model_validate(payload)
                self._records[record.decision_id] = record
                self._positions[record.decision_id] = record.position_after or record.position_before
            elif kind == "fill":
                payload = {key: value for key, value in entry.items() if key not in {"type", "previous_hash", "record_hash"}}
                fill = PaperFill.model_validate(payload)
                if fill.decision_id not in self._records:
                    raise ValueError("fill without decision during load")
                self._fills[fill.fill_id] = fill
            elif kind == "trade":
                payload = {key: value for key, value in entry.items() if key not in {"type", "previous_hash", "record_hash"}}
                trade = TradeRecord.model_validate(payload)
                if trade.entry_decision_id not in self._records or trade.exit_decision_id not in self._records:
                    raise ValueError("trade without decisions during load")
                self._trades.append(trade)
            else:
                raise ValueError(f"unknown ledger record type: {kind}")
            previous = entry["record_hash"]
        self._last_hash = previous

    def verify(self) -> None:
        self.load()

    def records(self) -> tuple[DecisionRecord, ...]:
        return tuple(self._records.values())

    def fills(self) -> tuple[PaperFill, ...]:
        return tuple(self._fills.values())

    def trades(self) -> tuple[TradeRecord, ...]:
        return tuple(self._trades)

    def has_decision(self, decision_id: str) -> bool:
        return decision_id in self._records

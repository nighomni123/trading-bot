"""Append-only structured decision ledger for the active loop."""
from __future__ import annotations

import hashlib
import json
import os
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
        self._trade_fill_ids: set[str] = set()
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
            handle.flush()
            os.fsync(handle.fileno())
        self._last_hash = entry["record_hash"]
        return entry

    def append_decision(self, record: DecisionRecord) -> None:
        if record.decision_id in self._records:
            raise ValueError(f"duplicate decision_id: {record.decision_id}")
        self._append("decision", record.model_dump(mode="json"), record.decision_id)
        self._records[record.decision_id] = record
        self._positions[record.decision_id] = record.position_after or record.position_before

    def _validate_fill(self, fill: PaperFill) -> None:
        if fill.decision_id not in self._records:
            raise ValueError("fill requires an existing decision record")
        record = self._records[fill.decision_id]
        intent = record.execution_intent
        if intent is None or fill.intent_id != intent.intent_id:
            raise ValueError("fill must reference the decision execution intent")
        if fill.status != "FILLED" or fill.mode.value != "PAPER":
            raise ValueError("only filled PAPER orders may be recorded")
        if fill.execution_timestamp < intent.earliest_execution_at:
            raise ValueError("fill precedes intent eligibility")
        if fill.quantity > intent.quantity or fill.quantity > record.risk_decision.approved_quantity:
            raise ValueError("fill quantity exceeds approved quantity")
        if fill.fill_id in self._fills:
            raise ValueError(f"duplicate fill_id: {fill.fill_id}")

    def _validate_trade(self, trade: TradeRecord) -> None:
        if trade.entry_decision_id not in self._records or trade.exit_decision_id not in self._records:
            raise ValueError("trade requires existing entry and exit decision records")
        if trade.entry_fill_id is None and trade.exit_fill_id is None:
            return
        if trade.entry_fill_id is None or trade.exit_fill_id is None:
            raise ValueError("trade fill links must be both present or both absent")
        if trade.entry_fill_id not in self._fills or trade.exit_fill_id not in self._fills:
            raise ValueError("trade requires existing entry and exit fills")
        entry_fill = self._fills[trade.entry_fill_id]
        exit_fill = self._fills[trade.exit_fill_id]
        if entry_fill.decision_id != trade.entry_decision_id or exit_fill.decision_id != trade.exit_decision_id:
            raise ValueError("trade fill decision links do not match")
        if entry_fill.execution_timestamp >= exit_fill.execution_timestamp:
            raise ValueError("entry fill must precede exit fill")
        if trade.entry_fill_id in self._trade_fill_ids or trade.exit_fill_id in self._trade_fill_ids:
            raise ValueError("fill cannot be reused by multiple trades")
        if entry_fill.quantity < trade.quantity or exit_fill.quantity < trade.quantity:
            raise ValueError("trade quantity exceeds linked fill quantity")

    def append_fill(self, fill: PaperFill) -> None:
        self._validate_fill(fill)
        self._append("fill", fill.model_dump(mode="json"), fill.decision_id)
        self._fills[fill.fill_id] = fill

    def append_trade(self, trade: TradeRecord) -> None:
        self._validate_trade(trade)
        self._append("trade", trade.model_dump(mode="json"), trade.exit_decision_id)
        self._trades.append(trade)
        if trade.entry_fill_id is not None:
            self._trade_fill_ids.update((trade.entry_fill_id, trade.exit_fill_id))

    def load(self) -> None:
        self._records.clear(); self._fills.clear(); self._trades.clear(); self._trade_fill_ids.clear(); self._positions.clear(); self._last_hash = None
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
                self._validate_fill(fill)
                self._fills[fill.fill_id] = fill
            elif kind == "trade":
                payload = {key: value for key, value in entry.items() if key not in {"type", "previous_hash", "record_hash"}}
                trade = TradeRecord.model_validate(payload)
                self._validate_trade(trade)
                self._trades.append(trade)
                if trade.entry_fill_id is not None:
                    self._trade_fill_ids.update((trade.entry_fill_id, trade.exit_fill_id))
            else:
                raise ValueError(f"unknown ledger record type: {kind}")
            previous = entry["record_hash"]
        self._last_hash = previous

    def verify(self) -> None:
        self.load()

    @property
    def last_hash(self) -> str | None:
        return self._last_hash

    def records(self) -> tuple[DecisionRecord, ...]:
        return tuple(self._records.values())

    def fills(self) -> tuple[PaperFill, ...]:
        return tuple(self._fills.values())

    def trades(self) -> tuple[TradeRecord, ...]:
        return tuple(self._trades)

    def has_decision(self, decision_id: str) -> bool:
        return decision_id in self._records

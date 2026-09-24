"""Canonical append-only JSONL event schema shared by backtest and shadow execution.

``exec_ts`` is the earliest eligible next-bar timestamp for the current
decision; an actual fill is represented by ``executed`` on a later record whose
``ts`` is the real fill bar.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
from pathlib import Path
from typing import TextIO

SCHEMA_VERSION = 3
_ACTIONS = {"NO_ACTION", "ENTER_LONG", "ENTER_SHORT", "EXIT", "REDUCE", "REVERSE"}
_REQUIRED = {
    "seq", "ts", "exec_ts", "decision", "executed", "fill_decision_ts",
    "fill_px", "fill_qty", "fee", "fund_rate", "p_up", "pos",
    "cum_fee", "cum_fund", "equity", "entry_hash",
}


def canonical_json(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def entry_hash(entry: dict) -> str:
    return hashlib.sha256(canonical_json({k: v for k, v in entry.items() if k != "entry_hash"}).encode()).hexdigest()


def write_header(handle: TextIO, *, arm: str, metadata: dict) -> None:
    handle.write(json.dumps({"type": "header", "schema_version": SCHEMA_VERSION, "arm": arm, **metadata}) + "\n")


def write_record(handle: TextIO, record: dict) -> dict:
    record = dict(record)
    record["entry_hash"] = entry_hash(record)
    handle.write(json.dumps(record, allow_nan=False) + "\n")
    return record


def write_footer(
    handle: TextIO,
    records: list[dict] | None = None,
    *,
    record_count: int | None = None,
    last_entry_hash: str | None = None,
) -> None:
    if records is not None:
        record_count = len(records)
        last_entry_hash = records[-1]["entry_hash"] if records else None
    count = 0 if record_count is None else record_count
    footer = {
        "type": "footer",
        "schema_version": SCHEMA_VERSION,
        "record_count": count,
        "last_seq": count,
        "last_entry_hash": last_entry_hash,
    }
    handle.write(json.dumps(footer) + "\n")


def _finite_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"event field {field} must be a finite number")
    return float(value)


def _validate_record(entry: dict, line_no: int, previous_ts: int | None) -> None:
    missing = _REQUIRED - entry.keys()
    if missing:
        raise ValueError(f"event-log entry missing fields at line {line_no}: {sorted(missing)}")
    ts = entry["ts"]
    if isinstance(ts, bool) or not isinstance(ts, int):
        raise ValueError(f"event ts must be an integer at line {line_no}")
    if previous_ts is not None and ts <= previous_ts:
        raise ValueError(f"event timestamps are not strictly increasing at line {line_no}")
    decision, executed = entry["decision"], entry["executed"]
    if decision not in _ACTIONS or executed not in _ACTIONS:
        raise ValueError(f"invalid action at line {line_no}")
    exec_ts = entry["exec_ts"]
    if decision == "NO_ACTION":
        if exec_ts is not None:
            raise ValueError(f"NO_ACTION has an execution timestamp at line {line_no}")
    elif isinstance(exec_ts, bool) or not isinstance(exec_ts, int) or exec_ts <= ts:
        raise ValueError(f"decision execution must be later than its timestamp at line {line_no}")

    if executed == "NO_ACTION":
        if entry["fill_decision_ts"] is not None or entry["fill_px"] != 0 or entry["fill_qty"] != 0 or entry["fee"] != 0:
            raise ValueError(f"NO_ACTION execution has fill fields at line {line_no}")
    else:
        origin = entry["fill_decision_ts"]
        if isinstance(origin, bool) or not isinstance(origin, int) or origin >= ts:
            raise ValueError(f"fill origin must precede fill bar at line {line_no}")
        if _finite_number(entry["fill_px"], "fill_px") <= 0 or _finite_number(entry["fill_qty"], "fill_qty") <= 0:
            raise ValueError(f"executed fill must have positive price and quantity at line {line_no}")
        if _finite_number(entry["fee"], "fee") < 0:
            raise ValueError(f"executed fee must be non-negative at line {line_no}")

    pos = entry["pos"]
    if not isinstance(pos, dict) or not {"side", "quantity", "entry_price", "unrealized_pnl", "realized_pnl", "time_in_position"} <= pos.keys():
        raise ValueError(f"invalid position snapshot at line {line_no}")
    side, qty = pos["side"], _finite_number(pos["quantity"], "pos.quantity")
    if side not in (-1, 0, 1) or (side == 0 and qty != 0) or (side != 0 and qty <= 0):
        raise ValueError(f"invalid position side/quantity at line {line_no}")
    _finite_number(entry["cum_fee"], "cum_fee")
    _finite_number(entry["cum_fund"], "cum_fund")
    _finite_number(entry["equity"], "equity")
    _finite_number(entry["fund_rate"], "fund_rate")
    proba = _finite_number(entry["p_up"], "p_up")
    if not 0 <= proba <= 1:
        raise ValueError(f"p_up outside [0,1] at line {line_no}")


def verify_log(path: str | Path) -> list[dict]:
    """Verify schema, ordering, fills, hashes, and complete-log footer."""
    lines = Path(path).read_text().splitlines()
    if len(lines) < 2:
        raise ValueError("event log is incomplete")
    try:
        header, footer = json.loads(lines[0]), json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid event-log JSON: {exc.msg}") from exc
    if not isinstance(header, dict) or header.get("type") != "header" or header.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("event-log header must use schema_version 3")
    if not isinstance(footer, dict) or footer.get("type") != "footer" or footer.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("event log is incomplete or has no valid footer")

    records: list[dict] = []
    previous_ts: int | None = None
    for line_no, raw in enumerate(lines[1:-1], start=2):
        if not raw:
            raise ValueError(f"blank event-log line at line {line_no}")
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid event-log JSON at line {line_no}: {exc.msg}") from exc
        if not isinstance(entry, dict):
            raise ValueError(f"event-log entry must be an object at line {line_no}")
        expected_seq = len(records) + 1
        if entry.get("seq") != expected_seq or isinstance(entry.get("seq"), bool):
            raise ValueError(f"event-log sequence gap at line {line_no}: expected {expected_seq}, got {entry.get('seq')}")
        _validate_record(entry, line_no, previous_ts)
        expected_hash = entry_hash(entry)
        if not isinstance(entry.get("entry_hash"), str) or not hmac.compare_digest(entry["entry_hash"], expected_hash):
            raise ValueError(f"event-log entry hash mismatch at line {line_no}")
        records.append(entry)
        previous_ts = entry["ts"]

    if not records:
        raise ValueError("event log contains no records")
    if footer.get("record_count") != len(records) or footer.get("last_seq") != records[-1]["seq"]:
        raise ValueError("event-log footer count mismatch")
    expected_last = records[-1]["entry_hash"]
    if not isinstance(footer.get("last_entry_hash"), str) or not hmac.compare_digest(footer["last_entry_hash"], expected_last):
        raise ValueError("event-log footer hash mismatch")
    return records

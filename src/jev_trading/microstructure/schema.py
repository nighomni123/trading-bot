"""Stage 11 microstructure schema, versions, and data-quality contract.

Trade-side convention (canonical, documented once here):
    Binance `is_buyer_maker == True` means the BUYER was the passive side,
    therefore the aggressor was a SELLER.

        aggressive_buy_volume  = volume where is_buyer_maker == False
        aggressive_sell_volume = volume where is_buyer_maker == True

Book fields are part of the schema and are exercised by fixtures, but the
Binance public archive has no historical bookDepth/bookTicker for BTCUSDT. They
are therefore optional and MUST remain null on real history rather than being
imputed (STOP A).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import polars as pl

MICROSTRUCTURE_SCHEMA_VERSION = "microstructure-v1"
FEATURE_SET_VERSION = "features-v1"
EVENT_DEFINITION_VERSION = "events-v1"
TARGET_VERSION = "targets-v1"
ALPHA_EXPERIMENT_VERSION = "alpha-experiment-v1"

#: Columns every normalized frame must expose.
TRADE_COLUMNS = (
    "agg_trade_id", "trade_timestamp", "price", "quantity", "notional", "is_buyer_maker",
)
BOOK_COLUMNS = (
    "book_timestamp", "bid_price", "ask_price", "bid_qty", "ask_qty", "spread", "mid_price",
)
BAR_COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    count: int
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "count": self.count, "detail": self.detail}


class MicrostructureSchema:
    """Dtypes for the normalized research frame."""

    @staticmethod
    def trades() -> dict[str, Any]:
        return {
            "agg_trade_id": pl.Int64,
            "trade_timestamp": pl.Int64,   # epoch ms, UTC
            "price": pl.Float64,
            "quantity": pl.Float64,
            "notional": pl.Float64,
            "is_buyer_maker": pl.Boolean,
        }

    @staticmethod
    def book() -> dict[str, Any]:
        return {
            "book_timestamp": pl.Int64,
            "bid_price": pl.Float64,
            "ask_price": pl.Float64,
            "bid_qty": pl.Float64,
            "ask_qty": pl.Float64,
            "spread": pl.Float64,
            "mid_price": pl.Float64,
        }


def validate_trades(frame: pl.DataFrame) -> list[ValidationIssue]:
    """Data-quality contract for normalized trades. Never mutates, never imputes."""
    issues: list[ValidationIssue] = []
    missing = [c for c in TRADE_COLUMNS if c not in frame.columns]
    if missing:
        return [ValidationIssue("MISSING_COLUMNS", len(missing), f"missing: {missing}")]
    if frame.is_empty():
        return [ValidationIssue("EMPTY", 0, "trade frame is empty")]

    ts = frame["trade_timestamp"]
    if ts.null_count():
        issues.append(ValidationIssue("NULL_TIMESTAMPS", ts.null_count(), "null trade_timestamp"))
    if (ts < 1_000_000_000_000).any():
        # A plausible 1m bar timestamp is ~1.6e12; seconds are ~1.6e9.
        bad = ((ts < 1_000_000_000_000) & ts.is_not_null()).sum()
        issues.append(ValidationIssue("UNIT_SUSPECT", bad, "timestamps look like seconds, not ms"))
    n = frame.height
    monotonic = ts.drop_nulls().is_sorted()
    if not monotonic:
        inversions = (ts.drop_nulls().diff() < 0).sum()
        issues.append(ValidationIssue("NON_MONOTONIC_TS", inversions, "timestamps not sorted"))
    dupes = frame.height - frame.select(pl.struct(TRADE_COLUMNS).n_unique()).item()
    if dupes:
        issues.append(ValidationIssue("DUPLICATE_TRADES", dupes, "identical trade rows"))
    for column, code in (("price", "NONPOSITIVE_PRICE"), ("quantity", "NONPOSITIVE_QUANTITY")):
        bad = ((frame[column] <= 0) | frame[column].is_nan() | frame[column].is_infinite()).sum()
        if bad:
            issues.append(ValidationIssue(code, bad, f"{column} <= 0 or non-finite"))
    return issues


def validate_book(frame: pl.DataFrame) -> list[ValidationIssue]:
    """Book contract: bid < ask, quantities non-negative."""
    issues: list[ValidationIssue] = []
    missing = [c for c in ("bid_price", "ask_price", "bid_qty", "ask_qty") if c not in frame.columns]
    if missing:
        return [ValidationIssue("MISSING_COLUMNS", len(missing), f"missing: {missing}")]
    if frame.is_empty():
        return [ValidationIssue("EMPTY", 0, "book frame is empty")]
    crossed = (frame["bid_price"] >= frame["ask_price"]).sum()
    if crossed:
        issues.append(ValidationIssue("CROSSED_BOOK", crossed, "bid_price >= ask_price"))
    for column in ("bid_qty", "ask_qty"):
        bad = ((frame[column] < 0) | frame[column].is_nan()).sum()
        if bad:
            issues.append(ValidationIssue("NEGATIVE_DEPTH", bad, f"{column} < 0"))
    bad = (frame["bid_price"] <= 0).sum() + (frame["ask_price"] <= 0).sum()
    if bad:
        issues.append(ValidationIssue("NONPOSITIVE_PRICE", bad, "book price <= 0"))
    return issues


def validate_bars(frame: pl.DataFrame) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    missing = [c for c in BAR_COLUMNS if c not in frame.columns]
    if missing:
        return [ValidationIssue("MISSING_COLUMNS", len(missing), f"missing: {missing}")]
    if frame.is_empty():
        return [ValidationIssue("EMPTY", 0, "bar frame is empty")]
    if not frame["timestamp"].is_sorted():
        issues.append(ValidationIssue("NON_MONOTONIC_TS", 1, "bar timestamps not sorted"))
    dupes = frame.height - frame["timestamp"].n_unique()
    if dupes:
        issues.append(ValidationIssue("DUPLICATE_TIMESTAMPS", dupes, "repeated bar timestamps"))
    gaps = (frame["timestamp"].diff().drop_nulls() != 60_000).sum()
    if gaps:
        issues.append(ValidationIssue("BAR_GAPS", gaps, "bars not contiguous 1-minute"))
    body_max = pl.max_horizontal(pl.col("open"), pl.col("close"))
    body_min = pl.min_horizontal(pl.col("open"), pl.col("close"))
    incoherent = (pl.col("high") < pl.col("low")) | (pl.col("high") < body_max) | (pl.col("low") > body_min)
    bad_ohlc = frame.select(incoherent.sum()).item()
    if bad_ohlc:
        issues.append(ValidationIssue("OHLC_INCOHERENT", bad_ohlc, "high/low inconsistent with open/close"))
    neg_vol = frame.select((pl.col("volume") < 0).sum()).item()
    if neg_vol:
        issues.append(ValidationIssue("NEGATIVE_VOLUME", neg_vol, "negative volume"))
    return issues


def validation_report(frames: dict[str, pl.DataFrame]) -> dict[str, Any]:
    """Unified report. `populated=False` marks data that exists in the schema but
    is unavailable in the real history (STOP A), which is reported, not hidden."""
    validators = {"trades": validate_trades, "book": validate_book, "bars": validate_bars}
    report: dict[str, Any] = {"schema_version": MICROSTRUCTURE_SCHEMA_VERSION, "frames": {}}
    for name, frame in frames.items():
        if frame is None or (hasattr(frame, "is_empty") and frame.is_empty()):
            report["frames"][name] = {"populated": False, "rows": 0, "issues": [
                ValidationIssue("UNPOPULATED", 0, f"{name} unavailable in this dataset").as_dict()]}
            continue
        issues = validators[name](frame)
        report["frames"][name] = {
            "populated": True,
            "rows": frame.height,
            "columns": frame.columns,
            "ok": not issues,
            "issues": [i.as_dict() for i in issues],
        }
    return report

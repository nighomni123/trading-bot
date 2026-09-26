"""Microstructure schema and data-quality contract tests (synthetic fixtures)."""
from __future__ import annotations

import polars as pl
import pytest

from jev_trading.microstructure.schema import (
    MicrostructureSchema, validation_report, validate_bars, validate_book, validate_trades,
)


def _trades(n: int = 10, *, maker: bool | None = None) -> pl.DataFrame:
    rows = []
    for i in range(n):
        rows.append({
            "agg_trade_id": 1000 + i,
            "trade_timestamp": 1_700_000_000_000 + i * 1000,
            "price": 100.0 + i,
            "quantity": 0.5,
            "notional": (100.0 + i) * 0.5,
            "is_buyer_maker": (i % 2 == 0) if maker is None else maker,
        })
    return pl.DataFrame(rows, schema=MicrostructureSchema.trades())


def _book(n: int = 5) -> pl.DataFrame:
    return pl.DataFrame({
        "book_timestamp": [1_700_000_000_000 + i * 1000 for i in range(n)],
        "bid_price": [100.0 + i for i in range(n)],
        "ask_price": [100.5 + i for i in range(n)],
        "bid_qty": [1.0] * n,
        "ask_qty": [2.0] * n,
        "spread": [0.5] * n,
        "mid_price": [100.25 + i for i in range(n)],
    }, schema=MicrostructureSchema.book())


def _bars(n: int = 10) -> pl.DataFrame:
    rows = []
    for i in range(n):
        p = 100.0 + i
        rows.append({"timestamp": 1_700_000_000_000 + i * 60_000, "open": p, "high": p + 0.5,
                     "low": p - 0.5, "close": p + 0.1, "volume": 10.0})
    return pl.DataFrame(rows)


def test_valid_trades_pass():
    assert validate_trades(_trades()) == []


def test_trade_side_semantics_are_documented_and_honoured():
    """is_buyer_maker=True means an aggressive SELLER (buyer was passive)."""
    frame = _trades(4, maker=True)
    assert frame["is_buyer_maker"].all()
    from jev_trading.microstructure.ingest import split_aggressive_volume
    buy, sell = split_aggressive_volume(frame)
    assert buy == pytest.approx(0.0)
    assert sell == pytest.approx(2.0)


def test_non_positive_price_is_rejected():
    frame = _trades(3).with_columns(pl.when(pl.arange(0, 3) == 1).then(pl.lit(-1.0))
                                     .otherwise(pl.col("price")).alias("price"))
    codes = {i.code for i in validate_trades(frame)}
    assert "NONPOSITIVE_PRICE" in codes


def test_duplicate_trades_are_detected():
    frame = pl.concat([_trades(3), _trades(3).head(1)])
    codes = {i.code for i in validate_trades(frame)}
    assert "DUPLICATE_TRADES" in codes


def test_second_unit_timestamps_are_flagged():
    frame = _trades(3).with_columns((pl.col("trade_timestamp") // 1000).alias("trade_timestamp"))
    codes = {i.code for i in validate_trades(frame)}
    assert "UNIT_SUSPECT" in codes


def test_non_monotonic_timestamps_are_flagged():
    frame = _trades(4)
    reordered = pl.concat([frame.head(1), frame.tail(1), frame.slice(1, 2)])
    codes = {i.code for i in validate_trades(reordered)}
    assert "NON_MONOTONIC_TS" in codes


def test_missing_columns_are_reported():
    codes = {i.code for i in validate_trades(pl.DataFrame({"a": [1]}))}
    assert codes == {"MISSING_COLUMNS"}


def test_valid_book_passes_and_crossed_book_fails():
    assert validate_book(_book()) == []
    crossed = _book().with_columns(pl.lit(0.5).alias("ask_price"))
    codes = {i.code for i in validate_book(crossed)}
    assert "CROSSED_BOOK" in codes


def test_negative_depth_fails():
    bad = _book().with_columns(pl.lit(-1.0).alias("bid_qty"))
    codes = {i.code for i in validate_book(bad)}
    assert "NEGATIVE_DEPTH" in codes


def test_bars_validate_and_flag_gaps_and_coherence():
    assert validate_bars(_bars()) == []
    gapped = pl.concat([_bars(3), _bars(3).with_columns((pl.col("timestamp") + 600_000))])
    assert "BAR_GAPS" in {i.code for i in validate_bars(gapped)}
    incoherent = _bars(3).with_columns(pl.lit(1.0).alias("high"))
    assert "OHLC_INCOHERENT" in {i.code for i in validate_bars(incoherent)}


def test_unpopulated_frame_is_reported_not_imputed():
    report = validation_report({"trades": _trades(), "book": None})
    assert report["frames"]["trades"]["ok"] is True
    assert report["frames"]["book"]["populated"] is False
    assert report["frames"]["book"]["issues"][0]["code"] == "UNPOPULATED"


def test_report_records_schema_version():
    report = validation_report({"bars": _bars()})
    assert report["schema_version"] == "microstructure-v1"

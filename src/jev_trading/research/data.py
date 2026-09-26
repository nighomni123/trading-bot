"""Stage 12 data coverage and the canonical timestamp contract.

Coverage is reported PER FIELD, because fields have different availability.
A single blended coverage number would be misleading.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import polars as pl

MINUTE_MS = 60_000
BAR_FILE = Path("data/btcusdt_1m.parquet")
TRADE_DIR = Path("data/microstructure/trades")

#: Pre-registered long horizons in minutes.
HORIZONS: dict[str, int] = {
    "1h": 60, "2h": 120, "4h": 240, "8h": 480,
    "12h": 720, "24h": 1440, "48h": 2880, "72h": 4320,
}

#: Pre-registered threshold events in bps (symmetric up/down).
THRESHOLD_BPS = (20, 50, 100, 200, 500)

TIMESTAMP_CONTRACT = {
    "decision_timestamp": "completed 1m bar t (epoch ms, UTC)",
    "entry_timestamp": "open[t+1]",
    "exit_timestamp": "open[t+h+1] where h is the horizon in minutes",
    "path_window": "bars t+1 .. t+h inclusive",
    "reused_from": "labels/live_barrier.py and microstructure/targets.py (Stages 9-11)",
    "note": "No close-to-close mixing. Features use information at or before t only.",
}


def file_hash(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _field_coverage(frame: pl.DataFrame, column: str, start: int, end: int) -> dict[str, Any]:
    if column not in frame.columns:
        return {"available_from": None, "available_to": None, "coverage_percentage": 0.0,
                "missing_count": frame.height, "present": False}
    series = frame[column]
    non_null = series.drop_nulls()
    if non_null.is_empty():
        return {"available_from": None, "available_to": None, "coverage_percentage": 0.0,
                "missing_count": frame.height, "present": False}
    total_span = max(end - start, 1)
    return {
        "available_from": _iso(int(non_null.min())),
        "available_to": _iso(int(non_null.max())),
        "coverage_percentage": round(100.0 * non_null.len() / frame.height, 4) if frame.height else 0.0,
        "missing_count": frame.height - non_null.len(),
        "present": True,
    }


def data_coverage(bar_file: str | Path = BAR_FILE) -> dict[str, Any]:
    frame = pl.scan_parquet(bar_file)
    schema = frame.collect_schema()
    n_rows = frame.select(pl.len()).collect().item()
    start, end = frame.select(
        pl.col("timestamp").min().alias("lo"), pl.col("timestamp").max().alias("hi")
    ).collect().row(0)
    span_minutes = (end - start) / MINUTE_MS + 1

    ts = frame.select("timestamp").collect()["timestamp"]
    expected = span_minutes
    gaps = int((ts.diff().drop_nulls() != MINUTE_MS).sum())
    duplicates = int(n_rows - ts.n_unique())
    monotonic = bool(ts.is_sorted())

    oi = pl.scan_parquet(bar_file).select("open_interest").collect()["open_interest"]
    invalid_oi = int(((oi <= 0) | oi.is_nan()).sum())

    trade_days = sorted(p.stem for p in TRADE_DIR.glob("*.parquet")) if TRADE_DIR.exists() else []
    # Load once; per-field coverage is computed from the same frame (no reload).
    core = pl.read_parquet(bar_file, columns=[
        c for c in ("open", "high", "low", "close", "volume", "funding_rate", "open_interest")
        if c in schema.names()
    ])
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset_hash": file_hash(bar_file),
        "instrument": "BTCUSDT",
        "venue": "Binance USD-M futures",
        "contract_type": "PERPETUAL",
        "frequency": "1m",
        "first_timestamp": _iso(int(start)),
        "last_timestamp": _iso(int(end)),
        "rows": n_rows,
        "expected_rows": int(expected),
        "missing_bars": int(expected - n_rows),
        "duplicate_timestamps": duplicates,
        "timestamp_gaps": gaps,
        "monotonic": monotonic,
        "invalid_open_interest": invalid_oi,
        "columns": list(schema.names()),
        "field_coverage": {
            name: _field_coverage(core, name, start, end)
            for name in ("open", "high", "low", "close", "volume", "funding_rate", "open_interest")
        },
        "trade_flow_coverage": {
            "available": bool(trade_days),
            "days": len(trade_days),
            "first_day": trade_days[0] if trade_days else None,
            "last_day": trade_days[-1] if trade_days else None,
            "note": "Trade flow is NOT multi-year. Long-horizon trade features are unavailable.",
        },
        "order_book_coverage": {
            "available": False,
            "note": "Binance public archive has no BTCUSDT bookDepth/bookTicker. UNTESTED, never imputed.",
        },
        "horizons_minutes": HORIZONS,
        "timestamp_contract": TIMESTAMP_CONTRACT,
    }


def load_core(bar_file: str | Path = BAR_FILE) -> pl.DataFrame:
    return pl.read_parquet(bar_file).sort("timestamp")

#!/usr/bin/env python3
"""Fetch Kalshi + Polymarket history (YES-probability OHLCV) into a parquet log.

Range is half-open [start, end) in UTC dates: --end is exclusive. Idempotent:
an existing output keeps its rows outside the requested gap; the gap is
replaced by this fetch, then the file is rewritten sorted and de-duplicated.

Mirrors scripts/fetch_data.py (Binance) onto the prediction-market feed.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time, timezone
from pathlib import Path

import polars as pl

from jev_trading.data.pm import PM_BAR_COLUMNS, fetch_pm_bars


def _utc_ms(d: date) -> int:
    return int(datetime.combine(d, time(), tzinfo=timezone.utc).timestamp() * 1000)


def merge_gap(existing: pl.DataFrame | None, new: pl.DataFrame,
              start_ms: int, end_ms: int) -> pl.DataFrame:
    if existing is None or existing.is_empty():
        merged = new
    else:
        keep = existing.filter(
            (pl.col("timestamp") < start_ms) | (pl.col("timestamp") >= end_ms)
        )
        merged = pl.concat([keep, new], how="diagonal_relaxed")
    return (
        merged.select(list(PM_BAR_COLUMNS))
        .with_columns(pl.col("timestamp").cast(pl.Int64))
        .unique(subset=["platform", "market_id", "timestamp"], keep="last")
        .sort(["platform", "market_id", "timestamp"])
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", type=date.fromisoformat, required=True,
                   help="YYYY-MM-DD (UTC, inclusive)")
    p.add_argument("--end", type=date.fromisoformat, required=True,
                   help="YYYY-MM-DD (UTC, exclusive)")
    p.add_argument("--out", default="data/pm_history.parquet", help="output parquet path")
    p.add_argument("--interval", default="1h", choices=["1m", "1h", "1d", "6h", "1w", "max"])
    p.add_argument("--platforms", nargs="*", default=["polymarket", "kalshi"])
    args = p.parse_args(argv)

    start_ms, end_ms = _utc_ms(args.start), _utc_ms(args.end)
    if start_ms >= end_ms:
        p.error("--end must be after --start")

    print(f"fetching PM history [{args.start}, {args.end}) "
          f"({', '.join(args.platforms)}) interval={args.interval} ...")
    new = fetch_pm_bars(start_ms, end_ms, platforms=args.platforms, interval=args.interval)

    out = Path(args.out)
    existing = pl.read_parquet(out) if out.exists() else None
    if existing is not None:
        missing = [c for c in PM_BAR_COLUMNS if c not in existing.columns]
        if missing:
            print(f"WARN: {out} missing {missing}; ignoring existing rows")
            existing = None

    merged = merge_gap(existing, new, start_ms, end_ms)
    out.parent.mkdir(parents=True, exist_ok=True)
    merged.write_parquet(out)
    print(f"wrote {out}: {merged.height} rows "
          f"[{merged['timestamp'][0]}, {merged['timestamp'][-1]}] ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())

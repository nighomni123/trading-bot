#!/usr/bin/env python3
"""Fetch BTCUSDT 1m bars + funding + OI into a BAR_COLUMNS Parquet file.

Range is half-open [start, end) in UTC dates: --end is exclusive.
Idempotent: an existing output keeps its rows outside the requested gap; the gap
is replaced by this fetch, then the file is rewritten sorted and de-duplicated.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time, timezone
from pathlib import Path

import polars as pl

from jev_trading.contracts import BAR_COLUMNS
from jev_trading.data.fetch import fetch_bars, merge_gap


def _utc_ms(d: date) -> int:
    return int(datetime.combine(d, time(), tzinfo=timezone.utc).timestamp() * 1000)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", type=date.fromisoformat, required=True, help="YYYY-MM-DD (UTC, inclusive)")
    p.add_argument("--end", type=date.fromisoformat, required=True, help="YYYY-MM-DD (UTC, exclusive)")
    p.add_argument("--out", default="data/btcusdt_1m.parquet", help="output parquet path")
    args = p.parse_args(argv)

    start_ms, end_ms = _utc_ms(args.start), _utc_ms(args.end)
    if start_ms >= end_ms:
        p.error("--end must be after --start")

    print(f"fetching [{args.start}, {args.end}) ...")
    new = fetch_bars(start_ms, end_ms)

    out = Path(args.out)
    existing = pl.read_parquet(out) if out.exists() else None
    if existing is not None:
        missing = [c for c in BAR_COLUMNS if c not in existing.columns]
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

"""CLI entry: python -m jev_trading.backtest ..."""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time, timezone
from pathlib import Path

import polars as pl

from jev_trading.backtest.local import LocalBacktestEngine


def _utc_ms(value: str) -> int:
    d = date.fromisoformat(value)
    return int(datetime.combine(d, time(), tzinfo=timezone.utc).timestamp() * 1000)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local deterministic backtest runner")
    parser.add_argument("--engine", choices=["local"], default="local")
    parser.add_argument("--strategy", default="threshold")
    parser.add_argument("--start", default=None, help="UTC inclusive date")
    parser.add_argument("--end", default=None, help="UTC exclusive date")
    parser.add_argument("--experiment-id", default="EXP-LOCAL-001")
    parser.add_argument("--bars", default="data/btcusdt_1m.parquet")
    args = parser.parse_args(argv)

    bars_path = Path(args.bars)
    if not bars_path.is_file():
        parser.error(f"bars file not found: {bars_path}")
    data = pl.read_parquet(bars_path)
    if args.start:
        data = data.filter(pl.col("timestamp") >= _utc_ms(args.start))
    if args.end:
        data = data.filter(pl.col("timestamp") < _utc_ms(args.end))
    if data.is_empty():
        parser.error("selected date window is empty")

    result = LocalBacktestEngine().run(
        strategy=None,
        data=data,
        config={"experiment_id": args.experiment_id, "arm": args.strategy},
    )
    print(f"Backtest complete: engine={args.engine} experiment={args.experiment_id} bars={len(data)}")
    print(f"net_pnl={result.net_pnl:.4f} trades={result.trade_count} max_dd={result.max_drawdown:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

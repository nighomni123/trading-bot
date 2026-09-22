#!/usr/bin/env python3
"""Train quant baselines (LightGBM + logistic) from 1m bars; persist to --out."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import polars as pl

from jev_trading.quant.model import save_models
from jev_trading.quant.train import train_models


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bars", default="data/btcusdt_1m.parquet", help="input 1m bars parquet")
    p.add_argument("--out", default="models/", help="output model dir")
    args = p.parse_args(argv)

    if not Path(args.bars).exists():
        p.error(f"bars file not found: {args.bars}")
        return 2
    result = train_models(pl.read_parquet(args.bars))
    save_models(result, args.out)
    print(json.dumps(result["metrics"], indent=2))
    print(f"saved to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

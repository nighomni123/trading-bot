#!/usr/bin/env python3
"""Train the real Phase 2 economic-v2 heads on pre-2025 bars only."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jev_trading.quant.engine import save_economic_bundle, train_economic_bundle

OOS_START = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
TRAIN_END = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
VALID_START = TRAIN_END
VALID_END = OOS_START


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", default="data/btcusdt_1m.parquet")
    parser.add_argument("--out", default="artifacts/phase2-economic-v2-model")
    parser.add_argument("--feature-set", default="base_plus_derived", choices=("base", "base_plus_derived", "interpretable"))
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--trees", type=int, default=80)
    parser.add_argument("--leaves", type=int, default=15)
    args = parser.parse_args(argv)

    source = Path(args.bars)
    if not source.is_file():
        parser.error(f"bars file not found: {source}")
    bars = pl.read_parquet(source).filter(pl.col("timestamp") < OOS_START).sort("timestamp")
    if bars.is_empty():
        raise ValueError("pre-OOS source is empty")
    bundle = train_economic_bundle(
        bars,
        train_end=TRAIN_END,
        valid_start=VALID_START,
        valid_end=VALID_END,
        feature_set=args.feature_set,
        seeds=(args.seed, args.seed + 10, args.seed + 20),
        lgbm_params={"n_estimators": args.trees, "num_leaves": args.leaves},
    )
    out = Path(args.out)
    hashes = save_economic_bundle(bundle, out)
    (out / "metrics.json").write_text(json.dumps({
        "metrics": {"model_version": "economic-v2", "n_train": bundle.config["target_rows"]},
        "feature_cols": list(bundle.feature_cols),
    }, indent=2, sort_keys=True))
    print(json.dumps({
        "out": str(out),
        "feature_set": args.feature_set,
        "hashes": hashes,
        "oos_used": False,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

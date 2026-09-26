"""Stage 11 research CLI (Phase 35). Research-only; never trades."""
from __future__ import annotations

import argparse
import json
import sys

import polars as pl

from .economics import STAGE10_MEASURED_COST_BPS, scorecard_from_study
from .events import available_events, compute_event_signals
from .features import build_features
from .ingest import dataset_coverage, load_bars, load_trade_range, trades_to_bars
from .models import walk_forward_benchmark
from .schema import (
    ALPHA_EXPERIMENT_VERSION, EVENT_DEFINITION_VERSION, FEATURE_SET_VERSION,
    MICROSTRUCTURE_SCHEMA_VERSION, TARGET_VERSION, validation_report,
)
from .study import add_regimes, event_study, regime_conditional_study
from .targets import build_forward_targets


def _load(start: str, end: str):
    trades = load_trade_range(start, end)
    coverage = dataset_coverage(trades)
    if trades.is_empty():
        return None, None, coverage
    q99 = trades["quantity"].quantile(0.99)
    agg = (
        trades.with_columns(((pl.col("trade_timestamp") // 60_000) * 60_000).alias("timestamp"))
        .group_by("timestamp")
        .agg(
            pl.col("quantity").filter(~pl.col("is_buyer_maker")).sum().alias("buy_volume"),
            pl.col("quantity").filter(pl.col("is_buyer_maker")).sum().alias("sell_volume"),
            pl.col("notional").filter(~pl.col("is_buyer_maker")).sum().alias("buy_notional"),
            pl.col("notional").filter(pl.col("is_buyer_maker")).sum().alias("sell_notional"),
            pl.len().cast(pl.Float64).alias("trade_count"),
            pl.col("quantity").sum().alias("sum_trade_size"),
            pl.col("quantity").median().alias("median_trade_size"),
            pl.col("notional").filter(pl.col("quantity") > q99).sum().alias("large_trade_notional"),
        )
        .sort("timestamp")
    )
    lo, hi = trades["trade_timestamp"].min(), trades["trade_timestamp"].max()
    bars = load_bars().filter(pl.col("timestamp").is_between(lo, hi + 86_400_000))
    return bars, agg, coverage


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jev-research")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "event-study", "alpha-benchmark", "report"):
        p = sub.add_parser(name)
        p.add_argument("--start", default="2025-06-01")
        p.add_argument("--end", default="2025-06-30")
    args = parser.parse_args(argv)
    bars, agg, coverage = _load(args.start, args.end)
    if bars is None:
        print(json.dumps({"error": "no trade data", "coverage": coverage}, indent=2))
        return 1

    if args.command == "validate":
        trades = load_trade_range(args.start, args.end)
        print(json.dumps(validation_report({"trades": trades, "book": None, "bars": bars}), indent=2, default=str))
        return 0

    feats = build_features(bars, agg).filter(pl.col("close").is_not_null())
    feats = feats.join(build_forward_targets(bars), on="timestamp", how="inner")
    events = compute_event_signals(feats)
    names = available_events(events)

    if args.command == "event-study":
        events = add_regimes(events)
        study = event_study(events, names)
        study["coverage"] = coverage
        study["versions"] = {
            "schema": MICROSTRUCTURE_SCHEMA_VERSION, "features": FEATURE_SET_VERSION,
            "events": EVENT_DEFINITION_VERSION, "targets": TARGET_VERSION,
            "experiment": ALPHA_EXPERIMENT_VERSION,
        }
        print(json.dumps(study, indent=2, default=str))
        return 0

    if args.command == "alpha-benchmark":
        mid = "2025-06-16"
        results = {"coverage": coverage, "n_features_frame": feats.shape, "benchmarks": []}
        for h in (10, 30, 60):
            sub_frame = feats.drop_nulls(subset=[f"forward_return_{h}m"])
            r = walk_forward_benchmark(sub_frame, horizon=h,
                                       folds=[(f"{mid}..{args.end}", args.start, mid)])
            results["benchmarks"].append(r)
        print(json.dumps(results, indent=2, default=str))
        return 0

    # report
    events = add_regimes(events)
    study = event_study(events, names)
    scorecard = scorecard_from_study(study, cost_bps=STAGE10_MEASURED_COST_BPS)
    regime = regime_conditional_study(events, names)
    print(json.dumps({"coverage": coverage, "study": study, "economics": scorecard,
                      "regime": regime}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

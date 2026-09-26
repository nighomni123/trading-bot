"""Stage 12 research CLI. Extends the Stage 11 convention; research-only."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import polars as pl

from .data import HORIZONS, data_coverage, load_core
from .features import build_structural_features, feature_manifest
from .firewall import select_model_features
from .stability import walk_forward_models, year_stability
from .state import build_market_states, state_manifest
from .study import state_study
from .targets import build_long_horizon_targets, non_overlapping_grid, target_manifest

OUT = Path("research/stage12")
FOLDS = [("2022", "2021-01-01", "2022-01-01"), ("2023", "2021-01-01", "2023-01-01"),
         ("2024", "2021-01-01", "2024-01-01"), ("2025", "2021-01-01", "2025-01-01")]


def _write(name: str, payload) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(payload, indent=2, default=str) + "\n")


def _frame(horizons=None):
    bars = load_core()
    feats = build_market_states(build_structural_features(bars))
    targets = build_long_horizon_targets(bars, horizons=horizons)
    j = feats.join(targets, on="timestamp", how="inner", suffix="_t")
    j = j.with_columns((((pl.col("timestamp") // (365.25 * 24 * 3600 * 1000)).cast(pl.Int32)) + 1970).alias("year"))
    return j


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jev-research-long")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("validate-data", "build-features", "build-targets", "state-study",
                 "benchmark", "economics", "stability", "report"):
        sub.add_parser(name)

    args = parser.parse_args(argv)

    if args.command == "validate-data":
        cov = data_coverage()
        _write("data_coverage.json", cov)
        print(json.dumps({k: cov[k] for k in
                          ("rows", "first_timestamp", "last_timestamp", "duplicate_timestamps",
                           "monotonic", "trade_flow_coverage", "order_book_coverage")}, indent=2))
        return 0

    if args.command == "build-features":
        f = build_structural_features(load_core())
        print(json.dumps(feature_manifest(f), indent=2, default=str))
        return 0

    if args.command == "build-targets":
        t = build_long_horizon_targets(load_core())
        _write("target_manifest.json", {**target_manifest(), "n_rows": t.height})
        print(json.dumps({**target_manifest(), "n_rows": t.height}, indent=2, default=str))
        return 0

    if args.command == "state-study":
        j = _frame()
        results = {}
        for hname, hmins in HORIZONS.items():
            grid = non_overlapping_grid(j, hmins)
            results[hname] = state_study(grid, horizons={hname: hmins})
        _write("state_study.json", results)
        print(json.dumps({h: {"tested": r["n_hypotheses_tested"], "sig": r["n_significant_fdr10"]}
                          for h, r in results.items()}, indent=2))
        return 0

    if args.command == "benchmark":
        results = {}
        for hname in ("4h", "24h", "72h"):
            sub_frame = _frame({hname: HORIZONS[hname]}).drop_nulls(subset=[f"forward_return_{hname}"])
            results[hname] = walk_forward_models(
                sub_frame, horizon=hname, horizon_minutes=HORIZONS[hname], folds=FOLDS)
        _write("model_results.json", results)
        print(json.dumps({h: [f.get("lgbm_auc") for f in r.get("folds", [])]
                          for h, r in results.items()}, indent=2))
        return 0

    if args.command in ("stability", "economics", "report"):
        j = _frame()
        cands = [("TREND_STATE", "UP", "24h"), ("TREND_STATE", "NEUTRAL", "24h"),
                 ("FUNDING_STATE", "LOW", "4h"), ("FUNDING_STATE", "NORMAL", "4h"),
                 ("PRICE_LOCATION_STATE", "LOW", "4h")]
        stab = {}
        for state, level, hor in cands:
            grid = non_overlapping_grid(j, HORIZONS[hor])
            stab[f"{state}={level}@{hor}"] = year_stability(grid, state=state, level=level, horizon=hor)
        _write("stability.json", stab)
        if args.command == "stability":
            print(json.dumps(stab, indent=2, default=str))
            return 0
        results = {}
        for hname, hmins in HORIZONS.items():
            grid = non_overlapping_grid(j, hmins)
            results[hname] = state_study(grid, horizons={hname: hmins})
        _write("state_study.json", results)
        print(json.dumps({"hypotheses_tested": sum(r["n_hypotheses_tested"] for r in results.values()),
                          "significant_fdr10": sum(r["n_significant_fdr10"] for r in results.values())},
                         indent=2))
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())

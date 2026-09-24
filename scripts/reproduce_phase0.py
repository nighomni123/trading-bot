#!/usr/bin/env python3
"""Freeze and reproduce the current pre-OOS baseline without touching 2025+.

The historical EXP-002/EXP-003 files are references only: they were produced
from source/data versions that are not fully pinned. This script creates a new
current-code baseline from a pre-2025 slice, records hashes, and replays a small
real event log through the canonical verifier.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from jev_trading.backtest.simulator import SimConfig, prepare, run, verify_log
from jev_trading.quant.model import load_models, save_models
from jev_trading.quant.train import train_models

OOS_START = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
TRAIN_END = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
VALID_START = TRAIN_END
VALID_END = OOS_START


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def git_sha() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


def git_state() -> tuple[str, str, bool, dict[str, str]]:
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        capture_output=True, text=True, check=True,
    ).stdout
    diff = subprocess.run(
        ["git", "diff", "HEAD", "--binary"], capture_output=True, check=True
    ).stdout
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    relevant = [p for p in untracked if p.startswith(("src/", "tests/", "scripts/", "configs/", "experiments/EXP-008-phase0-baseline/")) or p == "docs/phase0-baseline.md"]
    untracked_hashes = {p: sha256(Path(p)) for p in relevant if Path(p).is_file()}
    return hashlib.sha256(diff).hexdigest(), status, bool(status or diff), untracked_hashes


def package_versions() -> dict[str, str]:
    names = ("lightgbm", "scikit-learn", "polars", "pyarrow", "numpy", "pandas", "pytest")
    out = {"python": platform.python_version()}
    for name in names:
        try:
            out[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            out[name] = "not-installed"
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="data/btcusdt_1m.parquet")
    parser.add_argument("--out", default="artifacts/phase0-baseline")
    args = parser.parse_args(argv)

    source = Path(args.source)
    if not source.is_file():
        parser.error(f"source parquet not found: {source}")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # Materialize the permitted research slice before any feature/label/model call.
    bars = pl.read_parquet(source).filter(pl.col("timestamp") < OOS_START)
    if bars.is_empty():
        raise ValueError("pre-OOS source slice is empty")
    pre_oos = out / "pre_oos_2021_2024.parquet"
    bars.write_parquet(pre_oos)

    result = train_models(
        bars,
        lgbm_params={"random_state": 7, "deterministic": True, "force_col_wise": True},
        split=(TRAIN_END, VALID_START, VALID_END),
    )
    model_dir = out / "models"
    save_models(result, model_dir)
    quant = load_models(model_dir)
    feats, p_up = prepare(bars, quant)
    validation = run(
        feats,
        p_up,
        None,
        "threshold",
        SimConfig(p_thr=0.40, seed=7),
        start_ms=VALID_START,
    )

    # A bounded real-bar replay sample exercises the same event schema without
    # creating a multi-hundred-megabyte validation log.
    sample_start = VALID_START
    sample_end = sample_start + 2 * 86_400_000
    sample_mask = (feats["timestamp"] >= sample_start - 2_000 * 60_000) & (feats["timestamp"] < sample_end)
    sample_feats = feats.filter(sample_mask)
    sample_p = p_up[sample_mask.to_numpy()]
    sample_log = out / "validation_sample.jsonl"
    sample = run(sample_feats, sample_p, None, "threshold", SimConfig(p_thr=0.40), log_path=sample_log)
    verified = verify_log(sample_log)

    metrics = {
        "train_validation": result["metrics"],
        "threshold_validation": validation["metrics"],
        "sample_replay": {
            "metrics": sample["metrics"],
            "verified_records": len(verified),
            "log": str(sample_log),
        },
    }
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2, sort_keys=True))

    files = [
        "src/jev_trading/labels/engine.py",
        "src/jev_trading/quant/train.py",
        "src/jev_trading/quant/model.py",
        "src/jev_trading/quant/economic.py",
        "src/jev_trading/backtest/simulator.py",
        "src/jev_trading/backtest/local.py",
        "src/jev_trading/backtest/__main__.py",
        "src/jev_trading/policy/engine.py",
        "src/jev_trading/risk/kernel.py",
        "src/jev_trading/events.py",
        "src/jev_trading/execution/__init__.py",
        "configs/policy.json",
        "configs/risk.json",
        "configs/costs.json",
        "scripts/paper_trade.py",
        "scripts/run_ablation.py",
        "scripts/reproduce_phase0.py",
        "docs/phase0-baseline.md",
        "experiments/EXP-008-phase0-baseline/experiment.yaml",
    ]
    diff_sha, git_status, dirty, untracked_hashes = git_state()
    provenance = {
        "experiment_id": "PHASE0-CURRENT-BASELINE",
        "git_commit": git_sha(),
        "git_diff_sha256": diff_sha,
        "git_status": git_status,
        "working_tree_dirty": dirty,
        "untracked_relevant_hashes": untracked_hashes,
        "source": {"path": str(source), "sha256": sha256(source)},
        "pre_oos_dataset": {
            "path": "pre_oos_2021_2024.parquet",
            "sha256": sha256(pre_oos),
            "rows": bars.height,
            "period": ["2021-01-01", "2025-01-01"],
        },
        "periods": {
            "train": ["2021-01-01", "2024-01-01"],
            "validation": ["2024-01-01", "2025-01-01"],
            "frozen_oos": ["2025-01-01", "2026-01-01"],
            "oos_used_by_this_run": False,
        },
        "model": {
            "directory": "models",
            "files": {p.name: sha256(p) for p in sorted(model_dir.iterdir()) if p.is_file()},
            "parameters": {"random_state": 7, "deterministic": True, "force_col_wise": True},
        },
        "code_hashes": {f: sha256(Path(f)) for f in files},
        "runtime": package_versions(),
        "historical_reference": {
            "EXP-002": {"lgbm_auc": 0.6372191006946994, "lgbm_logloss": 0.5170694467023773},
            "status": "reference_only; historical source contained uncommitted drift",
        },
        "oos_contamination": {
            "status": "historical_2025_already_consumed",
            "action": "do not use 2025+ for selection; acquire a new untouched holdout before promotion",
        },
    }
    (out / "provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True))
    (out / "locked_config.json").write_text(json.dumps({
        "locked": True,
        "experiment_id": "PHASE0-CURRENT-BASELINE",
        "source_period": ["2021-01-01", "2025-01-01"],
        "train_period": ["2021-01-01", "2024-01-01"],
        "validation_period": ["2024-01-01", "2025-01-01"],
        "frozen_oos": ["2025-01-01", "2026-01-01"],
        "label_threshold": 0.0014,
        "p_thr": 0.40,
        "seed": 7,
        "execution": "decision[t] -> open[t+1] + side slippage; funding on held rate prints",
        "oos_consumed_by_run": False,
    }, indent=2))
    (out / "summary.json").write_text(json.dumps({
        "status": "PASS_CURRENT_BASELINE_ONLY",
        "decision": "REPRODUCIBLE_CURRENT_BASELINE",
        "historical_reproduction": "FAIL",
        "historical_baseline": "NOT_EXACTLY_REPRODUCIBLE",
        "metrics_file": "metrics.json",
        "provenance_file": "provenance.json",
        "replay_verified": True,
    }, indent=2))
    (out / "notes.md").write_text(
        "# Phase 0 current baseline\n\n"
        "This is a new reproducible freeze of the current code, not a claim that the historical "
        "EXP-002/EXP-003 artifacts can be regenerated byte-for-byte. The source slice ends before "
        "2025-01-01. Historical 2025 OOS was already consumed by earlier experiments and is not used "
        "for selection here.\n\n"
        "Run twice with separate `--out` directories and compare `metrics.json`; LightGBM is pinned to "
        "deterministic parameters and all relevant code/config/data/model hashes are in `provenance.json`. "
        "The current economic layer also corrects the historical double-counted slippage and per-8h funding "
        "units, so old economic metrics are reference-only rather than expected to match exactly.\n"
    )
    print(json.dumps({"status": "PASS", "out": str(out), "records": len(verified)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

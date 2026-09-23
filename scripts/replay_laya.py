#!/usr/bin/env python3
"""Replay/offline harness: historical state → LayaDecision artifacts.

Usage:
    python scripts/replay_laya.py --input results/state_window.jsonl --output results/laya_decisions.jsonl

Produces auditable artifacts for measuring incremental value:
Quant only vs Quant+Policy vs Quant+Policy+Laya.

ponytail: no real Laya call; stub heuristic produces replay artifacts.
Upgrade when Phase 5 specialist evidence justifies real inference.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Self-contained replay logic (lazy: avoids package init / pydantic rebuild)
# References: src/jev_trading/frontier/laya.py (stub) and contracts

def replay_laya_decision(ts: int) -> dict:
    return {
        "ts": ts,
        "strategy_profile": {"strategy": "TREND", "confidence": 0.6, "abstain": 0.15, "selectivity": 0.5},
        "market_quality": "MODERATE",
        "cost_environment": "NORMAL",
        "stub_source": "replay_heuristic",
    }


def run_replay(input_path: Path, output_path: Path) -> None:
    lines = input_path.read_text().strip().splitlines()
    decisions: list[dict] = []
    for line in lines:
        if not line:
            continue
        record = json.loads(line)
        # Build minimal FrontierState from replay record
        decision = replay_laya_decision(record.get("timestamp", 0))
        decisions.append(decision)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        for d in decisions:
            f.write(json.dumps(d) + "\n")

    # Self-check: fail if output is empty or not JSONL
    assert output_path.exists(), "replay did not write output"
    count = sum(1 for _ in open(output_path))
    assert count > 0, "replay produced zero decisions"
    print(f"replay_laya: wrote {count} decisions to {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Offline Laya replay harness")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run_replay(args.input, args.output)

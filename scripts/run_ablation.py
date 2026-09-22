#!/usr/bin/env python3
"""Run P7 ablation arms over a [start, end) window (test period stays frozen).

Example: valid-year base costs + hostile multipliers for the full stack:
  .venv/bin/python scripts/run_ablation.py --start 2024-01-01 --end 2025-01-01 \\
      --arms random,threshold,policy,full,nojev --fee-mult 1.0
  .venv/bin/python scripts/run_ablation.py --start 2024-01-01 --end 2025-01-01 \\
      --arms threshold,full --fee-mult 2.0,3.0
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import date, datetime, time, timezone
from pathlib import Path

import polars as pl

from jev_trading.backtest.simulator import ARMS, SimConfig, prepare, run, summarize_by_year
from jev_trading.jev.mock import MockJev
from jev_trading.quant.model import load_models


def _utc_ms(d: date) -> int:
    return int(datetime.combine(d, time(), tzinfo=timezone.utc).timestamp() * 1000)


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bars", default="data/btcusdt_1m.parquet")
    ap.add_argument("--models", default="models/")
    ap.add_argument("--events", default="events/")
    ap.add_argument("--start", type=date.fromisoformat, default=date(2024, 1, 1))
    ap.add_argument("--end", type=date.fromisoformat, default=date(2025, 1, 1))
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--fee-mult", default="1.0")
    ap.add_argument("--p-thr", type=float, default=0.40)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--no-logs", action="store_true",
                    help="skip JSONL logs (metrics only; replay covered by base-cost logs)")
    ap.add_argument("--stride", type=int, default=1,
                    help="DEV ONLY: iterate every Nth bar. Never for gate runs.")
    args = ap.parse_args(argv)

    bars = pl.read_parquet(args.bars).filter(pl.col("timestamp") < _utc_ms(args.end))
    quant = load_models(args.models)
    jev = MockJev()
    try:
        git = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                             text=True, check=True).stdout.strip()
    except Exception:  # noqa: BLE001 — sha is provenance, not a gate
        git = "unknown"
    versions = {"data": _sha(Path(args.bars)), "git": git,
                "quant_metrics": json.loads((Path(args.models) / "metrics.json").read_text())["metrics"],
                "policy": _sha(Path("configs/policy.json")), "risk": _sha(Path("configs/risk.json")),
                "costs": _sha(Path("configs/costs.json"))}

    out_dir = Path(args.events)
    out_dir.mkdir(parents=True, exist_ok=True)
    feats, p_up = prepare(bars, quant)  # once: features + inference shared by all arms
    summary = []
    for arm in args.arms.split(","):
        for mult in [float(m) for m in args.fee_mult.split(",")]:
            tag = f"{args.start}_{arm}_x{mult}_p{args.p_thr}"
            cfg = SimConfig(fee_mult=mult, p_thr=args.p_thr, seed=args.seed, stride=args.stride)
            log = None if args.no_logs else out_dir / f"p7_{tag}.jsonl"
            res = run(feats, p_up, jev, arm, cfg, log_path=log,
                      versions=versions, start_ms=_utc_ms(args.start))
            m = res["metrics"] | {"by_year": summarize_by_year(res)}
            summary.append(m)
            print(json.dumps(m))
    (out_dir / "p7_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"wrote {len(summary)} arm-runs to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""CLI entry: python -m jev_trading.backtest ..."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from jev_trading.backtest.local import LocalBacktestEngine
from jev_trading.backtest.lean import LeanBacktestEngine
from jev_trading.backtest.comparison import compare_backtests, reconcile_trades


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Jev pluggable backtest runner")
    parser.add_argument("--engine", choices=["local", "lean", "both"], default="local")
    parser.add_argument("--strategy", default="threshold")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--experiment-id", default="EXP-LEAN-001")
    parser.add_argument("--output-dir", default="results/experiments")
    args = parser.parse_args(argv)
    # Load canonical data from data/ (simplified)
    data = None
    try:
        from jev_trading.data.fetch import load_btc_bars
        data = load_btc_bars()
    except Exception:
        pass
    # Minimal run
    results = {}
    if args.engine in ("local", "both"):
        engine = LocalBacktestEngine()
        results["local"] = engine.run(strategy=None, data=data, config={"experiment_id": args.experiment_id, "arm": args.strategy})
    if args.engine in ("lean", "both"):
        engine = LeanBacktestEngine()
        results["lean"] = engine.run(strategy=None, data=data, config={"experiment_id": args.experiment_id, "arm": args.strategy})
    if args.engine == "both":
        comp = compare_backtests(results["local"], results["lean"])
        rec = reconcile_trades(results["local"].trades, results["lean"].trades)
        out_dir = Path(args.output_dir) / args.experiment_id
        out_dir.mkdir(parents=True, exist_ok=True)
        import json
        (out_dir / "comparison.json").write_text(json.dumps(comp, indent=2, default=str))
        (out_dir / "reconciliation.json").write_text(json.dumps(rec, indent=2, default=str))
    print(f"Backtest complete: engine={args.engine} experiment={args.experiment_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

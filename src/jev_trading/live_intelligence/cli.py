"""CLI for active paper-only status, smoke, and replay inspection."""
from __future__ import annotations

import argparse
import json
import sys

from .config import load_settings
from .frontier.client import DisabledFrontierClient
from .jev.client import DisabledJevClient
from .runner import ShadowRunner
from jev_trading.data.normalization import BinancePerpAdapter
from jev_trading.replay import ReplayEngine


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jev")
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status", help="show paper-mode status")
    status.add_argument("--config", default="configs/live.json")
    run = sub.add_parser("paper", help="run the paper shadow loop")
    run.add_argument("--config", default="configs/live.json")
    run.add_argument("--iterations", type=int, default=1)
    run.add_argument("--ledger", default="research/runtime/ledger/decisions.jsonl")
    replay = sub.add_parser("replay", help="verify and summarize a recorded ledger")
    replay.add_argument("ledger")
    args = parser.parse_args(argv)
    settings = load_settings(args.config)
    if args.command == "status":
        print(json.dumps({"execution_mode": settings.execution_mode, "live_orders": "DISABLED", "instrument": settings.market.instrument, "experiment_id": settings.experiment_id, "frontier_provider": settings.frontier.provider.provider, "jev_provider": settings.jev.provider.provider}, indent=2))
        return 0
    if args.command == "replay":
        records = ReplayEngine(args.ledger).records()
        print(json.dumps({"records": len(records), "decisions": [record.decision_id for record in records]}, indent=2))
        return 0
    runner = ShadowRunner(settings, BinancePerpAdapter(), DisabledFrontierClient(), DisabledJevClient(), ledger_path=args.ledger)
    runner.run_forever(iterations=args.iterations)
    print(f"EXECUTION MODE: PAPER\nLIVE ORDERS: DISABLED\nLEDGER: {args.ledger}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

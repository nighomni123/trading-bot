"""CLI for active paper-only status, smoke, and replay inspection."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import load_project_env, load_settings
from .decision_quality import run_decision_quality
from .quant.economic_diagnostic import run_quant_diagnostic
from .frontier.client import FrontierClientFactory
from .frontier.strategist import load_prompt
from .jev.client import JevClientFactory
from .provider_benchmark import run_benchmark
from .provider_smoke import run_provider_smoke
from .runner import ShadowRunner
from .shadow import inspect_run, run_doctor
from jev_trading.data.venue_adapters import (
    BinanceLivePerpAdapter,
    BybitPerpAdapter,
    CompositeMarketDataAdapter,
)
from jev_trading.replay import ReplayEngine
from jev_trading.research import ResearchMemory


def _build_live_components(settings, arm: str):
    """Construct exactly what the configuration asks for; no hidden defaults."""
    frontier_client = FrontierClientFactory.create(settings.frontier.provider, replay_allow_trade=True)
    jev_prompt = load_prompt(Path(__file__).parent / settings.jev.prompt_file)
    jev_client = JevClientFactory.create(settings.jev.provider, prompt=jev_prompt)
    primary = BinanceLivePerpAdapter(source_role=settings.market.primary_source_role)
    secondary = (BybitPerpAdapter(),) if settings.market.secondary_source == "bybit" else ()
    adapter = CompositeMarketDataAdapter(primary, secondary)
    del arm  # arms are selected by the runner, not by transport construction
    return adapter, frontier_client, jev_client


def _shadow_status(settings) -> dict:
    return {
        "experiment_id": settings.experiment_id,
        "execution_mode": settings.execution_mode,
        "live_orders": "DISABLED",
        "run_mode": "LIVE_DATA_PAPER",
        "instrument": settings.market.instrument,
        "market_type": settings.market.market_type,
        "primary_source": settings.market.primary_source,
        "secondary_source": settings.market.secondary_source,
        "poll_seconds": settings.market.poll_seconds,
        "warmup_bars": settings.market.warmup_bars,
        "frontier": {
            "provider": settings.frontier.provider.provider,
            "model": settings.frontier.provider.model,
            "periodic_seconds": settings.frontier.periodic_seconds,
            "min_call_interval_seconds": settings.frontier.min_call_interval_seconds,
            "max_calls_per_hour": settings.frontier.max_calls_per_hour,
        },
        "jev": {
            "provider": settings.jev.provider.provider,
            "model": settings.jev.provider.model,
            "validity_seconds": settings.jev.validity_seconds,
            "max_calls_per_hour": settings.jev.max_calls_per_hour,
        },
        "paper": settings.paper.model_dump(mode="json"),
        "costs": settings.costs.model_dump(mode="json"),
        "kill_switch": settings.risk.kill_switch,
        "stale_data_ms": settings.risk.stale_data_ms,
    }


def _run_paper(args, *, demo: bool) -> int:
    settings = load_settings(args.config)
    ledger_path = args.ledger or f"{settings.ledger_root}/decisions.jsonl"
    runtime_root = getattr(args, "runtime_root", None) or (
        f"{settings.ledger_root}" if demo else None
    )
    adapter, frontier_client, jev_client = _build_live_components(settings, args.arm)
    telemetry = None
    if runtime_root:
        from .shadow import ShadowTelemetry
        telemetry = ShadowTelemetry(runtime_root, settings, arm=args.arm, console=not getattr(args, "no_console", False))
    adapter.start()
    try:
        runner = ShadowRunner(
            settings, adapter, frontier_client, jev_client,
            arm=args.arm, ledger_path=ledger_path,
            checkpoint_path=(f"{runtime_root}/runtime-state.json" if runtime_root else None),
            telemetry=telemetry, run_mode="LIVE_DATA_PAPER",
        )
        if telemetry is not None:
            telemetry.write_manifest(
                frontier_model=runner.frontier.client.model_version,
                jev_model=runner.jev.client.model_version,
                quant_versions={name: "pending" for name in runner.quant.names()},
            )
        iterations = args.iterations if args.iterations is not None else (1 if not demo else None)
        summary = runner.run_forever(iterations=iterations)
        print(json.dumps({
            **summary,
            "latency": runner.latency_report(),
            "execution_mode": "PAPER",
            "live_orders": "DISABLED",
        }, indent=2, default=str))
    finally:
        adapter.close()
    return 0


def _shadow(args) -> int:
    if args.shadow_command == "status":
        print(json.dumps(_shadow_status(load_settings(args.config)), indent=2))
        return 0
    if args.shadow_command == "inspect":
        print(json.dumps(inspect_run(args.ledger), indent=2, default=str))
        return 0
    settings = load_settings(args.config)
    ledger_path = args.ledger or f"{settings.ledger_root}/decisions.jsonl"
    runtime_root = getattr(args, "runtime_root", None) or settings.ledger_root
    adapter, frontier_client, jev_client = _build_live_components(settings, args.arm)
    adapter.start()
    try:
        if args.shadow_command == "doctor":
            report = run_doctor(
                settings, adapter, arm=args.arm,
                frontier_client=frontier_client, jev_client=jev_client,
                ledger_path=ledger_path,
                checkpoint_path=f"{runtime_root}/runtime-state.json",
            )
            print(json.dumps(report, indent=2, default=str))
            return 0 if report["ready"] else 1
        from .shadow import ShadowTelemetry
        telemetry = ShadowTelemetry(runtime_root, settings, arm=args.arm, console=not args.no_console)
        runner = ShadowRunner(
            settings, adapter, frontier_client, jev_client,
            arm=args.arm, ledger_path=ledger_path,
            checkpoint_path=f"{runtime_root}/runtime-state.json",
            telemetry=telemetry, run_mode="LIVE_DATA_PAPER",
        )
        telemetry.write_manifest(
            frontier_model=runner.frontier.client.model_version,
            jev_model=runner.jev.client.model_version,
            quant_versions={name: "pending" for name in runner.quant.names()},
        )
        summary = runner.run_forever(iterations=args.iterations)
        print(json.dumps({**summary, "latency": runner.latency_report()}, indent=2, default=str))
    finally:
        adapter.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    load_project_env()
    parser = argparse.ArgumentParser(prog="jev")
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status", help="show paper-mode status")
    status.add_argument("--config", default="configs/live.json")
    run = sub.add_parser("paper", help="run the paper shadow loop")
    run.add_argument("--config", default="configs/live.json")
    run.add_argument("--iterations", type=int, default=1)
    run.add_argument("--arm", choices=("A", "B", "C", "QUANT_ONLY", "QUANT_POLICY", "QUANT_FRONTIER", "QUANT_FRONTIER_JEV"), default="C")
    run.add_argument("--ledger", default=None, help="default: <ledger_root>/decisions.jsonl from the config")
    run.add_argument("--demo", action="store_true", help="stream heartbeat/dashboard telemetry for a shadow demo")
    run.add_argument("--runtime-root", default=None, help="telemetry/manifest root for a shadow demo")
    shadow = sub.add_parser("shadow", help="shadow-demo operations")
    shadow_sub = shadow.add_subparsers(dest="shadow_command", required=True)
    shadow_status = shadow_sub.add_parser("status", help="show resolved shadow configuration and safety state")
    shadow_status.add_argument("--config", default="configs/shadow-demo.json")
    shadow_doctor = shadow_sub.add_parser("doctor", help="run pre-flight checks before a shadow run")
    shadow_doctor.add_argument("--config", default="configs/shadow-demo.json")
    shadow_doctor.add_argument("--arm", choices=("A", "B", "C"), default="C")
    shadow_doctor.add_argument("--ledger", default=None)
    shadow_run = shadow_sub.add_parser("run", help="run a continuous shadow demo against live public data")
    shadow_run.add_argument("--config", default="configs/shadow-demo.json")
    shadow_run.add_argument("--arm", choices=("A", "B", "C"), default="C")
    shadow_run.add_argument("--iterations", type=int, default=None, help="stop after N polls (default: run until interrupted)")
    shadow_run.add_argument("--ledger", default=None)
    shadow_run.add_argument("--runtime-root", default=None)
    shadow_run.add_argument("--no-console", action="store_true", help="write telemetry only")
    shadow_inspect = shadow_sub.add_parser("inspect", help="verify and summarize a finished shadow run")
    shadow_inspect.add_argument("--ledger", default="research/runtime/shadow-demo/decisions.jsonl")
    replay = sub.add_parser("replay", help="verify and summarize a recorded ledger")
    replay.add_argument("ledger")
    research = sub.add_parser("research", help="generate immutable research memory")
    research.add_argument("period", choices=("hourly", "daily", "weekly"))
    research.add_argument("--ledger", default="research/runtime/ledger/decisions.jsonl")
    research.add_argument("--root", default="research")
    research.add_argument("--config", default="configs/live.json")
    smoke = sub.add_parser("provider-smoke", help="test configured AI providers without trading")
    smoke.add_argument("--component", choices=("frontier", "jev", "both"), default="both")
    smoke.add_argument("--config", default="configs/live.json")
    benchmark = sub.add_parser("provider-benchmark", help="run interface-only provider fixtures; never trades")
    benchmark.add_argument("--config", default="configs/live.json")
    benchmark.add_argument("--cases", type=int, default=12)
    benchmark.add_argument("--workers", type=int, default=4)
    benchmark.add_argument("--delay", type=float, default=0.0, help="minimum seconds between provider request starts")
    quality = sub.add_parser("decision-quality", help="historical candidate-level selection quality; never executes")
    quality.add_argument("--config", default="configs/live.json")
    quality.add_argument("--candidates", type=int, default=12)
    quality.add_argument("--horizon", type=int, default=15, help="outcome horizon in minutes")
    quality.add_argument("--quant-only", action="store_true", help="skip all LLM calls (harness self-check)")
    diagnostic = sub.add_parser("quant-diagnostic", help="Stage 8 read-only economic diagnostic; never executes")
    diagnostic.add_argument("--config", default="configs/live.json")
    diagnostic.add_argument("--candidates", type=int, default=60)
    diagnostic.add_argument("--calibration-rows", type=int, default=200)
    economics = sub.add_parser("execution-costs", help="measure live execution costs; never trades")
    economics.add_argument("--symbol", default="BTCUSDT")
    economics.add_argument("--samples", type=int, default=40)
    economics.add_argument("--notional", type=float, default=1000.0)
    args = parser.parse_args(argv)
    if args.command == "shadow":
        return _shadow(args)
    if args.command == "execution-costs":
        from jev_trading.quant.execution_economics import build_profiles, measure_book, profile_total_bps
        measurement = measure_book(symbol=args.symbol, samples=args.samples, notional_usd=args.notional)
        print(json.dumps({
            "measurement": measurement.summary(),
            "profiles": [
                {"name": p.name, "mode": p.execution_mode, "slippage_source": p.slippage_source,
                 "achievable": p.achievable, "total_rt_bps": profile_total_bps(p, measurement),
                 "note": p.note}
                for p in build_profiles(measurement)
            ],
            "execution": "NOT_INVOKED",
        }, indent=2))
        return 0
    if args.command == "quant-diagnostic":
        payload = run_quant_diagnostic(
            load_settings(args.config), count=args.candidates,
            calibration_rows=args.calibration_rows,
        )
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "decision-quality":
        payload = run_decision_quality(
            load_settings(args.config), count=args.candidates,
            horizon_minutes=args.horizon, with_intelligence=not args.quant_only,
        )
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "provider-benchmark":
        payload = run_benchmark(load_settings(args.config), cases=args.cases, workers=args.workers, delay=args.delay)
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "provider-smoke":
        results = run_provider_smoke(load_settings(args.config), args.component)
        print(json.dumps({"provider_smoke": results, "trading_execution": "NOT_INVOKED"}, indent=2))
        return 0 if all(item["request"] == "PASS" for item in results) else 1
    if args.command == "replay":
        engine = ReplayEngine(args.ledger)
        records = engine.records()
        print(json.dumps({
            "records": len(records),
            "decisions": [record.decision_id for record in records],
            "reconstructions": engine.reconstruct_all(),
        }, indent=2))
        return 0
    if args.command == "research":
        load_settings(args.config)
        report = ResearchMemory(args.root).generate(ReplayEngine(args.ledger).ledger, args.period)
        print(report)
        return 0
    settings = load_settings(args.config)
    if args.command == "status":
        print(json.dumps({
            "execution_mode": settings.execution_mode, "live_orders": "DISABLED",
            "instrument": settings.market.instrument, "primary_source": settings.market.primary_source,
            "secondary_source": settings.market.secondary_source, "experiment_id": settings.experiment_id,
            "frontier_provider": settings.frontier.provider.provider, "jev_provider": settings.jev.provider.provider,
            "run_mode": "LIVE_DATA_PAPER", "funding_model": settings.paper.funding_model,
        }, indent=2))
        return 0
    if args.command == "paper":
        return _run_paper(args, demo=args.demo)
    raise SystemExit(f"unhandled command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())

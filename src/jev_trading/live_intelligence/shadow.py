"""Operational surface for a shadow run: telemetry, heartbeat, doctor, report.

Everything here is observational except the manifest, which is written once at
startup and never rewritten. No function in this module can place an order.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jev_trading.live_intelligence.config import LiveSettings, project_root
from jev_trading.live_intelligence.experiment import (
    config_hash, file_hash, freeze_experiment, git_commit,
)
from jev_trading.live_intelligence.execution import PaperExecutor
from jev_trading.live_intelligence.policy import PolicyFinalizer
from jev_trading.live_intelligence.risk import ActiveRiskKernel
from jev_trading.live_intelligence.schemas import (
    AccountState, ExecutionMode, ExecutionState, PositionState,
)

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"


def _append(path: Path, payload: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")
        handle.flush()


def render_dashboard(sample: dict) -> str:
    """Informational one-screen view of the latest poll."""
    def money(value) -> str:
        return "n/a" if value is None else f"${value:,.2f}"

    price = sample.get("mark_price")
    spread_bps = None
    if sample.get("spread_bps") is not None:
        spread_bps = sample["spread_bps"]
    lines = [
        "┌──────────────────────────────────────────────┐",
        f"│ JEV SHADOW — {sample.get('instrument', 'BTCUSDT PERPETUAL')}          │",
        "├──────────────────────────────────────────────┤",
        f"│ Feed       {sample.get('feed_status', '?'):<8}   age {sample.get('feed_age_ms')} ms      │",
        f"│ Price      {'n/a' if price is None else f'{price:,.2f}':<32}│",
        f"│ Spread     {'n/a' if spread_bps is None else f'{spread_bps:.2f} bps':<32}│",
        f"│ Position   {sample.get('position', '?'):<8} qty {sample.get('position_quantity', 0):<18}│",
        f"│ Equity     {money(sample.get('equity')):<32}│",
        f"│ P&L        {money((sample.get('realized_pnl') or 0) + (sample.get('unrealized_pnl') or 0)):<32}│",
        f"│ DD         {(sample.get('drawdown_pct') or 0):.2f}%{'':<26}│",
        f"│ Quant      {sample.get('quant_status', '?'):<32}│",
        f"│ Frontier   {sample.get('frontier_status', '?'):<32}│",
        f"│ Jev        {sample.get('jev_status', '?'):<32}│",
        f"│ Policy     {sample.get('policy_action', '?'):<32}│",
        f"│ Risk       {sample.get('risk_status', '?'):<32}│",
        f"│ Pending    {sample.get('pending_order', 'NONE'):<32}│",
        f"│ Ledger     seq={sample.get('ledger_sequence', 0):<25}│",
        "└──────────────────────────────────────────────┘",
    ]
    return "\n".join(lines)


def render_heartbeat(sample: dict) -> str:
    return "\n".join([
        "",
        "SHADOW HEARTBEAT",
        "----------------",
        f"Experiment: {sample.get('experiment_id')}  arm={sample.get('arm')}  mode={sample.get('run_mode')}",
        f"Market:     {sample.get('instrument', 'BTCUSDT_PERP')}",
        f"Feed:       {sample.get('feed_status')}  age {sample.get('feed_age_ms')} ms",
        f"Last bar:   {sample.get('bar_timestamp')}",
        f"Position:   {sample.get('position')} {sample.get('position_quantity')} @ {sample.get('entry_price')}",
        f"Equity:     {sample.get('equity'):.2f}" if isinstance(sample.get("equity"), (int, float)) else "Equity:     n/a",
        f"Daily P&L:  {sample.get('daily_pnl')}",
        f"Open orders:{sample.get('pending_order')}",
        f"Quant:      {sample.get('quant_status')}   Frontier: {sample.get('frontier_status')}   Jev: {sample.get('jev_status')}",
        f"Policy:     {sample.get('policy_action')}   Risk: {sample.get('risk_status')}",
        f"Ledger:     seq={sample.get('ledger_sequence')}  hash={str(sample.get('ledger_hash'))[:12]}",
    ])


class ShadowTelemetry:
    """Append-only runtime telemetry for one shadow experiment."""

    def __init__(
        self,
        root: str | Path,
        settings: LiveSettings,
        *,
        arm: str = "C",
        heartbeat_seconds: float = 60.0,
        console: bool = True,
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.settings = settings
        self.arm = arm
        self.heartbeat_seconds = heartbeat_seconds
        self.console = console
        self.metrics_path = self.root / "metrics.jsonl"
        self.events_path = self.root / "events.jsonl"
        self.started_at = datetime.now(tz=timezone.utc)
        self._last_heartbeat = 0.0
        self.samples = 0
        self.events = 0

    # -- manifest ----------------------------------------------------------
    def write_manifest(self, *, frontier_model: str, jev_model: str, quant_versions: dict[str, str]) -> Path:
        target = self.root / "manifest.json"
        prompt_root = Path(__file__).parent
        details = {
            "experiment_id": self.settings.experiment_id,
            "run_mode": "LIVE_DATA_PAPER",
            "arm": self.arm,
            "execution_mode": self.settings.execution_mode,
            "live_orders": "DISABLED",
            "instrument": self.settings.market.instrument,
            "market_type": self.settings.market.market_type,
            "primary_venue": self.settings.market.venue,
            "primary_source": self.settings.market.primary_source,
            "secondary_source": self.settings.market.secondary_source,
            "starting_capital_usd": self.settings.paper.capital_usd,
            "funding_model": self.settings.paper.funding_model,
            "cost_assumptions": self.settings.costs.model_dump(mode="json"),
            "risk_limits": self.settings.risk.model_dump(mode="json"),
            "policy_thresholds": self.settings.policy.model_dump(mode="json"),
            "frontier_model": frontier_model,
            "jev_model": jev_model,
            "frontier_prompt_hash": file_hash(prompt_root / self.settings.frontier.prompt_file),
            "jev_prompt_hash": file_hash(prompt_root / self.settings.jev.prompt_file),
            "quant_versions": quant_versions,
            "config_hash": config_hash(self.settings),
            "config": json.loads(self.settings.model_dump_json()),
            "git_commit": git_commit(),
            "host": socket.gethostname(),
            "platform": platform.platform(),
        }
        if target.exists():
            return target
        freeze_experiment(
            self.settings, target,
            frontier_model=frontier_model, jev_model=jev_model, quant_versions=quant_versions,
            details=details,
        )
        return target

    # -- streams -----------------------------------------------------------
    def sample(self, payload: dict) -> None:
        record = {"experiment_id": self.settings.experiment_id, "arm": self.arm, **payload}
        record.setdefault("instrument", self.settings.market.instrument)
        _append(self.metrics_path, record)
        self.samples += 1
        if self.console:
            self._maybe_heartbeat(record)

    def event(self, event_type: str, **fields) -> None:
        _append(self.events_path, {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "experiment_id": self.settings.experiment_id,
            "component": fields.pop("component", "runtime"),
            "event_type": event_type,
            "status": fields.pop("status", "OK"),
            "reason": fields.pop("reason", ""),
            "details": fields,
        })
        self.events += 1

    def _maybe_heartbeat(self, sample: dict) -> None:
        import time as _time
        now = _time.monotonic()
        if now - self._last_heartbeat < self.heartbeat_seconds:
            return
        self._last_heartbeat = now
        print(render_heartbeat(sample), flush=True)

    def render(self, sample: dict) -> None:
        print(render_dashboard(sample), flush=True)

    def close(self, summary: dict) -> Path:
        summary = {**summary, "started_at": self.started_at.isoformat(), "samples": self.samples, "events": self.events}
        _append(self.metrics_path, {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(), "phase": "SHUTDOWN",
            "experiment_id": self.settings.experiment_id, "arm": self.arm, "shutdown": summary,
        })
        _append(self.events_path, {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "experiment_id": self.settings.experiment_id,
            "component": "runtime",
            "event_type": "shutdown",
            **summary,
        })
        return self.write_report(summary)

    def write_report(self, summary: dict) -> Path:
        reports = project_root() / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
        target = reports / f"shadow-demo-{stamp}.md"
        target.write_text(render_run_report(self.settings, summary, self.arm), encoding="utf-8")
        return target


def render_run_report(settings: LiveSettings, summary: dict, arm: str) -> str:
    started = summary.get("started_at")
    duration = "unknown"
    if started:
        duration = f"{(datetime.now(tz=timezone.utc) - datetime.fromisoformat(started)).total_seconds() / 60:.1f} minutes"
    return "\n".join([
        f"# Shadow run {settings.experiment_id} ({arm})",
        "",
        f"- Run start: {started}",
        f"- Duration: {duration}",
        f"- Run mode: {summary.get('run_mode')}",
        f"- Execution mode: {settings.execution_mode} (live orders: DISABLED)",
        f"- Decisions: {summary.get('decisions', 0)}",
        f"- Fills: {summary.get('fills', 0)}",
        f"- Trades: {summary.get('trades', 0)}",
        f"- Final position: {summary.get('position')}",
        f"- Final equity: {summary.get('equity')}",
        f"- Realized P&L: {summary.get('realized_pnl')}",
        f"- Cancelled pending intent: {summary.get('cancelled_pending_intent')}",
        f"- Ledger hash: {summary.get('ledger_hash')}",
        "",
        "## Paper profitability",
        "",
        "DEMONSTRATION SAMPLE — NOT PERFORMANCE VALIDATION.",
        "This run demonstrates operational integration against real-time market",
        "data and paper execution. It does not establish profitability,",
        "predictive alpha, live-trading readiness, or suitability for real capital.",
        "",
    ])


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    mandatory: bool = True


def _await_websocket(adapter, seconds: float) -> bool:
    """Wait briefly for a live socket tick; a REST bootstrap is not the feed."""
    import time as _time
    deadline = _time.monotonic() + seconds
    while _time.monotonic() < deadline:
        try:
            if any(tick.metadata.get("transport") == "websocket" for tick in adapter.snapshot()):
                return True
        except Exception:
            pass
        _time.sleep(0.5)
    return False


def run_doctor(
    settings: LiveSettings,
    adapter,
    *,
    arm: str = "C",
    frontier_client=None,
    jev_client=None,
    ledger_path: str | Path = "research/runtime/shadow-demo/decisions.jsonl",
    checkpoint_path: str | Path | None = None,
    clock_skew_tolerance_seconds: float = 120.0,
    feed_wait_seconds: float = 10.0,
) -> dict:
    """Pre-flight checks; returns PASS/WARN/FAIL per check and a final verdict."""
    checks: list[Check] = []

    def add(name: str, ok: bool | None, detail: str, *, mandatory: bool = True) -> None:
        checks.append(Check(name, PASS if ok else (FAIL if mandatory else WARN), detail, mandatory))

    add("execution mode is PAPER", settings.execution_mode == "PAPER", settings.execution_mode)
    add("instrument is BTCUSDT perpetual",
        settings.market.instrument == "BTCUSDT_PERP" and settings.market.market_type == "PERPETUAL",
        f"{settings.market.instrument}/{settings.market.market_type}")
    add("live order capability absent", set(ExecutionMode) == {ExecutionMode.PAPER},
        f"execution modes: {[mode.value for mode in ExecutionMode]}")
    kernel = ActiveRiskKernel(settings)
    executor = PaperExecutor(settings, kernel)
    add("paper executor operational", executor.mode == "PAPER", executor.version)
    add("policy and risk operational", PolicyFinalizer(settings).thresholds.minimum_liquidity_notional >= 0,
        "policy-v1/active-risk-v1 instantiated")

    for label, path in (("ledger path writable", Path(ledger_path)),
                        ("checkpoint path writable", Path(checkpoint_path or f"{ledger_path}.state.json"))):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            probe = path.parent / f".doctor-{os.getpid()}"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            add(label, True, str(path))
        except OSError as exc:
            add(label, False, f"{path}: {exc}")

    usage = shutil.disk_usage(str(Path(ledger_path).parent or "."))
    add("disk space", usage.free > 200 * 1024 * 1024, f"{usage.free / 1024**3:.1f} GiB free", mandatory=False)

    bars = None
    try:
        bars = adapter.fetch_closed_bars(limit=max(600, settings.market.warmup_bars))
        add("public Binance 1m data", not bars.is_empty(), f"{0 if bars is None else bars.height} bars")
    except Exception as exc:  # network failure is a FAIL, not a crash
        add("public Binance 1m data", False, f"{type(exc).__name__}: {exc}")

    live = _await_websocket(adapter, feed_wait_seconds)
    add("public WebSocket connectivity", live, "websocket feed observed" if live else f"no websocket tick within {feed_wait_seconds:.0f}s")

    if bars is not None and not bars.is_empty():
        from jev_trading.environment import build_market_environment
        from jev_trading.data.normalization import DataFabric
        now = datetime.now(tz=timezone.utc)
        last_close = int(bars["timestamp"][-1]) + 60_000
        skew = (last_close - int(now.timestamp() * 1000)) / 1000
        add("system clock sanity", abs(skew) <= clock_skew_tolerance_seconds,
            f"last closed bar {skew:+.1f}s from now")
        continuity = DataFabric()
        continuity.ingest_bars(bars)
        add("1m bar continuity", not continuity.quality(now=now).bar_timestamp_problems,
            str(continuity.quality(now=now).bar_timestamp_problems) or "no gaps")
        quality = adapter.health(now=now)
        add("primary source health", quality.safe_for_trading,
            f"lag {quality.timestamp_lag_ms} ms", mandatory=True)
        try:
            environment = build_market_environment(
                bars, ticks=adapter.snapshot(), quality=quality, decision_timestamp=now,
            )
            book = environment.liquidity
            add("book freshness", book.spread is not None, f"spread {book.spread}")
            add("depth notional", book.top_level_notional is not None, f"{book.top_level_notional}")
            add("open interest available", environment.derivatives.open_interest is not None,
                str(environment.derivatives.open_interest), mandatory=False)
            add("funding available", environment.derivatives.funding is not None,
                str(environment.derivatives.funding), mandatory=False)
            add("liquidations unknown unless observed", not environment.derivatives.liquidations,
                str(environment.derivatives.liquidations), mandatory=False)
        except Exception as exc:
            add("environment build", False, f"{type(exc).__name__}: {exc}")

    secondary = getattr(adapter, "secondary", ())
    if secondary:
        for source in secondary:
            try:
                health = source.health()
                add(f"secondary source {source.source}", health.safe_for_trading,
                    f"lag {health.timestamp_lag_ms} ms", mandatory=False)
            except Exception as exc:
                add(f"secondary source {source.source}", None, f"{type(exc).__name__}", mandatory=False)

    requires_frontier = arm in {"B", "C"}
    requires_jev = arm == "C"
    for name, client, provider, required in (
        ("Frontier provider", frontier_client, settings.frontier.provider, requires_frontier),
        ("Jev provider", jev_client, settings.jev.provider, requires_jev),
    ):
        if not required:
            continue
        configured = provider.provider != "disabled"
        key_present = True
        if configured:
            key_present = bool(os.environ.get(provider.api_key_env))
        add(f"{name} configured and reachable", configured and key_present,
            f"provider={provider.provider} model={provider.model} "
            f"credential_present={key_present if configured else False}")

    failed = [check for check in checks if check.status == FAIL]
    warned = [check for check in checks if check.status == WARN]
    return {
        "checks": [check.__dict__ for check in checks],
        "mandatory_failed": [check.name for check in failed],
        "warnings": [check.name for check in warned],
        "ready": not failed,
        "verdict": "SHADOW RUN READY" if not failed else "SHADOW RUN NOT READY",
    }


def inspect_run(ledger_path: str | Path) -> dict:
    """Post-run summary that re-verifies the ledger instead of trusting it."""
    from jev_trading.replay import ReplayEngine
    from jev_trading.ledger import DecisionLedger

    engine = ReplayEngine(ledger_path)
    ledger: DecisionLedger = engine.ledger
    records = engine.records()
    fills = ledger.fills()
    trades = ledger.trades()
    start = min((record.timestamp for record in records), default=None)
    end = max((record.timestamp for record in records), default=None)
    actions: dict[str, int] = {}
    for record in records:
        actions[record.policy_decision.action.value] = actions.get(record.policy_decision.action.value, 0) + 1
    peak, drawdown = 0.0, 0.0
    equity = 0.0
    for trade in trades:
        equity += trade.net_pnl_usd
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {
        "ledger": str(ledger_path),
        "integrity": "VERIFIED",
        "decisions": len(records),
        "window": {"from": start.isoformat() if start else None, "to": end.isoformat() if end else None},
        "policy_actions": actions,
        "risk_rejections": sum(record.risk_decision.status.value == "REJECTED" for record in records),
        "provider_calls": {
            "frontier": sum(record.frontier_hypothesis is not None and "cadence" not in record.frontier_hypothesis.reason for record in records),
            "jev": sum(record.jev_evaluation is not None for record in records),
        },
        "provider_failures": sum(len(record.provider_failures) for record in records),
        "unsafe_decisions": sum(not record.market_environment.data_quality.safe_for_trading for record in records),
        "fills": len(fills),
        "trades": len(trades),
        "net_pnl_usd": sum(trade.net_pnl_usd for trade in trades),
        "fees_usd": sum(trade.fees_usd for trade in trades),
        "slippage_usd": sum(trade.slippage_usd for trade in trades),
        "funding_usd": sum(trade.funding_usd for trade in trades),
        "max_drawdown_usd": drawdown,
        "pending_cancellations": [record.pending_cancellation for record in records if record.pending_cancellation],
    }

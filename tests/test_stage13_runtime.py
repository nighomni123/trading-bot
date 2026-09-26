"""Stage 13: runtime loop, cadence, provider failure, recovery, and telemetry."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import polars as pl
import pytest

from jev_trading.contracts import BAR_COLUMNS
from jev_trading.data.normalization import ReplayAdapter
from jev_trading.live_intelligence.config import load_settings
from jev_trading.live_intelligence.frontier.client import ReplayFrontierClient
from jev_trading.live_intelligence.jev.client import ReplayJevClient
from jev_trading.live_intelligence.runner import ShadowRunner, infer_run_mode
from jev_trading.live_intelligence.schemas import (
    DataEventType, MarketTick, MarketType, PolicyAction, Side,
)
from tests.test_live_intelligence import aligned_start_ms
from tests.test_live_intelligence_ablations import CountingFrontier, CountingJev

UTC = timezone.utc


class FailingFrontier(ReplayFrontierClient):
    """Provider outage: 429, timeout, and malformed output all abstain."""

    def __init__(self, mode: str = "error"):
        super().__init__(allow_trade=True)
        self.mode = mode
        self.calls = 0

    def complete(self, **kwargs):
        self.calls += 1
        from jev_trading.live_intelligence.frontier.client import FrontierUnavailable
        if self.mode == "error":
            raise FrontierUnavailable("boom", category="rate_limit", http_status=429, retry_count=2)
        if self.mode == "timeout":
            raise FrontierUnavailable("slow", category="timeout")
        return {"hypothesis_id": "wrong-id"}


def _frame(base: datetime, minutes: int = 400) -> pl.DataFrame:
    """Bars ending one minute before ``base``, starting on a 4h boundary."""
    start = aligned_start_ms(base, minutes)
    count = (int(base.timestamp() * 1000) - start) // 60_000
    rows = []
    for index in range(count):
        price = 100 + index * 0.01
        rows.append({
            "timestamp": start + index * 60_000,
            "open": price, "high": price + 0.1, "low": price - 0.1, "close": price,
            "volume": 10.0, "funding_rate": 0.0001, "open_interest": 1000.0,
        })
    return pl.DataFrame(rows, schema={c: pl.Int64 if c == "timestamp" else pl.Float64 for c in BAR_COLUMNS})


def _recent() -> datetime:
    """A minute-aligned instant just in the past, so wall-clock checks agree."""
    return (datetime.now(UTC) - timedelta(seconds=61)).replace(second=0, microsecond=0)


_BASE = load_settings()
# The synthetic frame has no real microstructure, so cost assumptions are
# removed rather than loosened: the subject here is loop behaviour, not edge.
_CHEAP = {
    "costs": _BASE.costs.model_copy(update={"fee_bps_per_side": 0.0, "slippage_bps_per_side": 0.0, "latency_bps": 0.0}),
    "jev": _BASE.jev.model_copy(update={"min_call_interval_seconds": 0, "validity_seconds": 300}),
    "frontier": _BASE.frontier.model_copy(update={"min_call_interval_seconds": 0}),
}


def _runner(tmp_path, clock, *, arm="C", frontier=None, jev=None, settings_update=None, frame=None, ticks=None):
    """clock is a one-element list so tests can advance the injected clock."""
    base_settings = load_settings()
    settings = base_settings.model_copy(update={
        "experiment_id": "STAGE13-EXP",
        "research": base_settings.research.model_copy(update={"root": str(tmp_path / "research")}),
        "observability": base_settings.observability.model_copy(update={"metrics_file": str(tmp_path / "metrics.json")}),
        **(settings_update or {}),
    })
    frame = frame if frame is not None else _frame(clock[0], 400)
    adapter = ReplayAdapter(
        "primary", "binance-futures", "BTCUSDT_PERP", frame, ticks if ticks is not None else [_tick(clock[0])],
    )
    return ShadowRunner(
        settings, adapter, frontier or ReplayFrontierClient(allow_trade=True), jev or ReplayJevClient(),
        arm=arm, ledger_path=tmp_path / "ledger.jsonl",
        checkpoint_path=tmp_path / "runtime-state.json", clock=lambda: clock[0],
    )


class EnteringJev(ReplayJevClient):
    """A fake provider that actually says ENTER, so an intent can exist."""

    model_version = "fake-jev-v1"

    def evaluate(self, request):
        from jev_trading.live_intelligence.schemas import JevEvaluation
        return JevEvaluation(
            decision_id=request.request_id, request_id=request.request_id, timestamp=request.timestamp,
            valid_until=request.timestamp + timedelta(seconds=240),
            probabilities={"target": 0.8, "stop": 0.1, "timeout": 0.1},
            target_probability=0.8, entry_quality=0.8, failure_risk=0.1, liquidity_quality=0.8,
            ratings={}, answers={}, confidence=0.8, recommended_state="ENTER", reason="fake",
            model_version=self.model_version, prompt_version=request.prompt_version,
        )


def _tick(now: datetime) -> MarketTick:
    return MarketTick(
        source="primary", source_role="primary", instrument="BTCUSDT_PERP", venue="binance-futures",
        market_type=MarketType.PERPETUAL, event_timestamp=now, received_timestamp=now,
        event_type=DataEventType.BAR, last=100 + 400 * 0.01, bid=100, ask=100.02, mid=100.01,
        bid_depth=10, ask_depth=10, volume=10, open_interest=1000, funding_rate=0.0001,
    )


def test_decision_deduplication():
    """Four polls inside one closed bar produce one decision, not four."""
    import tempfile
    root = Path(tempfile.mkdtemp())
    clock = [_recent()]
    frontier, jev = CountingFrontier(), CountingJev()
    runner = _runner(root, clock, frontier=frontier, jev=jev)
    first = runner.run_once()
    for _ in range(3):
        clock[0] = clock[0] + timedelta(seconds=15)
        runner.adapter.ticks = [_tick(clock[0])]
        assert runner.run_once().decision_id == first.decision_id
    assert len(runner.ledger.records()) == 1
    assert (frontier.calls, jev.calls) == (1, 1)
    assert runner.run_mode == "REPLAY"
    assert infer_run_mode(runner.adapter) == "REPLAY"


def test_frontier_cadence():
    from jev_trading.live_intelligence.schemas import MarketEvent
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    base_settings = load_settings()
    settings = base_settings.model_copy(update={
        "frontier": base_settings.frontier.model_copy(update={
            "periodic_seconds": 900, "min_call_interval_seconds": 900,
            "max_calls_per_hour": 4, "event_severity_threshold": 0.6,
        }),
    })
    runner = ShadowRunner(
        settings, ReplayAdapter("primary", "binance-futures", "BTCUSDT_PERP", _frame(datetime.now(UTC), 400), []),
        ReplayFrontierClient(allow_trade=True), ReplayJevClient(), ledger_path=tmp / "l.jsonl",
    )
    start = datetime.now(UTC)
    event = MarketEvent(
        event_type="volatility_expansion", event_timestamp=start, detection_timestamp=start,
        instrument="BTCUSDT_PERP", source="binance", severity=0.9,
    )
    assert runner.should_call_frontier([event], now=start) is True
    runner._mark_frontier_events([event])
    runner._record_frontier_call(start)
    # Same event, 15s later: no second call (dedup + minimum interval).
    assert runner.should_call_frontier([event], now=start + timedelta(seconds=15)) is False
    # A different qualifying event cannot bypass the minimum interval either.
    later_event = event.model_copy(update={"event_timestamp": start + timedelta(minutes=1)})
    assert runner.should_call_frontier([later_event], now=start + timedelta(seconds=30)) is False
    # After the floor, the periodic cadence allows the next call.
    assert runner.should_call_frontier([], now=start + timedelta(seconds=901)) is True
    for index in range(settings.frontier.max_calls_per_hour):
        runner._record_frontier_call(start + timedelta(minutes=index))
    assert runner.should_call_frontier([], now=start + timedelta(minutes=30)) is False


def test_frontier_rate_limit_abstains_and_keeps_running():
    import tempfile
    root = Path(tempfile.mkdtemp())
    clock = [_recent()]
    frontier = FailingFrontier("error")
    runner = _runner(root, clock, frontier=frontier)
    record = runner.run_once()
    assert frontier.calls == 1
    assert record.frontier_hypothesis is not None and record.frontier_hypothesis.abstain is True
    assert "rate_limit" in record.frontier_hypothesis.reason
    assert record.provider_failures[0].http_status == 429
    assert record.policy_decision.action in {PolicyAction.NO_TRADE, PolicyAction.DATA_UNSAFE}
    # The loop keeps running: a later poll is still processed.
    clock[0] += timedelta(minutes=1)
    runner.adapter.ticks = [_tick(clock[0])]
    runner.adapter.bars = pl.concat([runner.adapter.bars, _frame(clock[0], 400).tail(1)])
    assert runner.run_once() is not None


def test_provider_failure_abstention():
    import tempfile
    root = Path(tempfile.mkdtemp())
    clock = [_recent()]
    runner = _runner(root, clock, frontier=FailingFrontier("malformed"))
    record = runner.run_once()
    assert record.frontier_hypothesis.abstain is True
    assert "validation" in record.frontier_hypothesis.reason
    assert record.execution_intent is None
    assert record.risk_decision.status.value in {"REJECTED", "APPROVED"}


def test_jev_expiry():
    from jev_trading.live_intelligence.policy import PolicyFinalizer
    from jev_trading.live_intelligence.schemas import JevEvaluation, JevState
    from tests.test_live_intelligence import environment, hypothesis
    settings = load_settings()
    env = environment().model_copy(update={
        "liquidity": environment().liquidity.model_copy(update={"top_level_notional": 1_000_000.0}),
    })
    jev = JevEvaluation(
        decision_id="d", request_id="r", timestamp=env.decision_timestamp,
        valid_until=env.decision_timestamp + timedelta(seconds=30),
        probabilities={"target": 0.6, "stop": 0.2, "timeout": 0.2},
        target_probability=0.6, entry_quality=0.8, failure_risk=0.1,
        ratings={}, answers={}, confidence=0.8, recommended_state=JevState.ENTER,
        reason="fixture", model_version="m", prompt_version="jev-evaluator-v1",
    )
    from jev_trading.live_intelligence.policy import make_candidate
    candidate = make_candidate(env, hypothesis(), settings=settings)
    value = _value(settings, env, candidate)
    fresh = PolicyFinalizer(settings).finalize(
        env, hypothesis(), value, jev, candidate=candidate, decision_id="d",
    )
    assert fresh.action == PolicyAction.ENTER_LONG
    expired = jev.model_copy(update={"valid_until": env.decision_timestamp - timedelta(seconds=1)})
    stale = PolicyFinalizer(settings).finalize(
        env, hypothesis(), value, expired, candidate=candidate, decision_id="d",
    )
    assert stale.action == PolicyAction.NO_TRADE
    assert "jev_expired" in stale.reasons


def _value(settings, env, candidate=None):
    from jev_trading.live_intelligence.policy import make_candidate
    from jev_trading.live_intelligence.quant import calculate_economic_value
    from tests.test_live_intelligence import hypothesis
    candidate = candidate or make_candidate(env, hypothesis(), settings=settings)
    return calculate_economic_value(
        candidate, {"target": 0.5, "stop": 0.2, "timeout": 0.3},
        settings.costs.assumptions(900), sample_size=30,
    )


def test_kill_switch():
    from jev_trading.live_intelligence.policy import make_candidate
    from jev_trading.live_intelligence.risk import ActiveRiskKernel
    from jev_trading.live_intelligence.schemas import (
        AccountState, ExecutionState, PolicyDecision, PositionState,
    )
    from tests.test_live_intelligence import environment, hypothesis
    base = load_settings()
    settings = base.model_copy(update={"risk": base.risk.model_copy(update={"kill_switch": True})})
    env = environment().model_copy(update={
        "liquidity": environment().liquidity.model_copy(update={"top_level_notional": 1_000_000.0}),
    })
    kernel = ActiveRiskKernel(settings)
    entry = PolicyDecision(
        decision_id="d", timestamp=env.decision_timestamp, action=PolicyAction.ENTER_LONG,
        reasons=("fixture",), candidate=make_candidate(env, hypothesis(), settings=settings), policy_version="fixture",
    )
    blocked = kernel.evaluate(entry, env, AccountState(capital_usd=10_000), ExecutionState(), position=PositionState())
    assert blocked.status.value == "REJECTED"
    assert "configured_kill_switch" in blocked.reasons

    # A kill switch stops new risk; it must not trap an open position.
    position = PositionState(side=Side.LONG, quantity=1, entry_price=100.0, opened_at=env.decision_timestamp)
    exit_policy = PolicyDecision(
        decision_id="d", timestamp=env.decision_timestamp, action=PolicyAction.EXIT,
        reasons=("fixture",), policy_version="fixture",
    )
    allowed = kernel.evaluate(
        exit_policy, env, AccountState(capital_usd=10_000), ExecutionState(), position=position,
    )
    assert allowed.status.value == "APPROVED"
    assert "configured_kill_switch" in allowed.reasons


def test_pending_order_revalidation():
    """A pending intent is re-judged against current data between boundaries."""
    import tempfile
    root = Path(tempfile.mkdtemp())
    clock = [_recent()]
    runner = _runner(root, clock, jev=EnteringJev(), settings_update=_CHEAP)
    record = runner.run_once()
    assert record.execution_intent is not None
    assert runner._pending_state is not None
    # Same bar, but the data went unsafe: the order is dropped with a reason.
    unsafe = record.market_environment.model_copy(update={
        "data_quality": record.market_environment.data_quality.model_copy(update={"safe_for_trading": False, "stale": True}),
    })
    runner._revalidate_pending(unsafe, runner._pending_state.policy, runner._pending_state.risk_decision, record.execution_intent and None)
    assert runner._pending_intent is None
    assert runner._last_cancellation == "CURRENT_DATA_UNSAFE"
    assert runner.ledger.fills() == ()


def test_shutdown_checkpoint_and_restart_recovery():
    import tempfile
    root = Path(tempfile.mkdtemp())
    clock = [_recent()]
    runner = _runner(root, clock, jev=EnteringJev(), settings_update=_CHEAP)
    runner.run_once()
    pending = runner._pending_state
    assert pending is not None
    summary = runner.shutdown()
    assert summary["cancelled_pending_intent"] == pending.intent.intent_id
    state = json.loads((root / "runtime-state.json").read_text())
    assert state["pending"] is None
    assert state["ledger_hash"] == runner.ledger.last_hash

    frontier, jev = CountingFrontier(), EnteringJev()
    resumed = _runner(root, clock, frontier=frontier, jev=jev, settings_update=_CHEAP)
    assert resumed._pending_state is None
    assert len(resumed.ledger.records()) == len(runner.ledger.records())
    resumed.run_once()
    assert frontier.calls == 0, "committed intelligence is not re-queried"
    assert len(resumed.ledger.records()) == 1
    assert resumed.ledger.fills() == ()


def test_telemetry_streams_are_append_only():
    import tempfile
    from jev_trading.live_intelligence.shadow import ShadowTelemetry
    root = Path(tempfile.mkdtemp())
    clock = [_recent()]
    settings_update = {
        "research": load_settings().research.model_copy(update={"root": str(root / "research")}),
    }
    telemetry = ShadowTelemetry(root, load_settings(), console=False)
    runner = _runner(root, clock, settings_update=settings_update)
    runner.telemetry = telemetry
    telemetry.write_manifest(frontier_model="m", jev_model="j", quant_versions={"trend": "trend-v1"})
    record = runner.run_once()
    telemetry.close(runner.shutdown())
    metrics = [json.loads(line) for line in (root / "metrics.jsonl").read_text().splitlines()]
    events = [json.loads(line) for line in (root / "events.jsonl").read_text().splitlines()]
    assert any(row.get("phase") == "DECIDED" for row in metrics)
    sample = next(row for row in metrics if row.get("phase") == "DECIDED")
    for field in ("feed_age_ms", "feed_status", "bar_timestamp", "position", "equity",
                  "realized_pnl", "unrealized_pnl", "daily_pnl", "drawdown",
                  "quant_status", "frontier_status", "jev_status", "policy_action",
                  "risk_status", "pending_order", "latency_ms", "ledger_sequence", "drawdown_pct"):
        assert field in sample, field
    assert events, "market events must be emitted"
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["execution_mode"] == "PAPER"
    assert manifest["live_orders"] == "DISABLED"
    assert manifest["config_hash"]
    assert manifest["funding_model"] == "DISABLED"
    # A manifest is frozen for the run.
    assert telemetry.write_manifest(frontier_model="m", jev_model="j", quant_versions={}) == root / "manifest.json"

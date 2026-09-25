"""Crash/restart equivalence and intelligence-replay tests."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import polars as pl
import pytest

from jev_trading.contracts import BAR_COLUMNS
from jev_trading.data.normalization import ReplayAdapter
from jev_trading.live_intelligence.config import load_settings
from jev_trading.live_intelligence.frontier.client import ReplayFrontierClient
from jev_trading.live_intelligence.jev.client import ReplayJevClient
from jev_trading.live_intelligence.runner import ShadowRunner
from jev_trading.live_intelligence.schemas import DataEventType, MarketTick, MarketType
from tests.test_live_intelligence_ablations import CountingFrontier, CountingJev


def _adapter(now=None):
    count = 300
    now = now or datetime.now(timezone.utc)
    start = int(now.timestamp() * 1000) - count * 60_000
    rows = []
    for index in range(count):
        close = 100 + index * 0.1
        rows.append({
            "timestamp": start + index * 60_000, "open": close, "high": close + 0.05,
            "low": close - 0.05, "close": close, "volume": 10.0,
            "funding_rate": 0.0001, "open_interest": 1000.0,
        })
    frame = pl.DataFrame(rows, schema={column: pl.Int64 if column == "timestamp" else pl.Float64 for column in BAR_COLUMNS})
    tick = MarketTick(
        source="primary", source_role="primary", instrument="BTCUSDT_PERP", venue="binance-futures",
        market_type=MarketType.PERPETUAL, event_timestamp=now, received_timestamp=now,
        event_type=DataEventType.BAR, last=100, volume=10, open_interest=1000, funding_rate=0.0001,
    )
    return ReplayAdapter("primary", "binance-futures", "BTCUSDT_PERP", frame, [tick])


def _runner(tmp_path, name, adapter, *, now=None, **kwargs):
    """Build a runner whose adapter and clock agree on one decision instant."""
    now = now or datetime.now(timezone.utc)
    base = load_settings()
    settings = base.model_copy(update={
        "experiment_id": "RECOVERY-EXP",
        "research": base.research.model_copy(update={"root": str(tmp_path / name)}),
        "observability": base.observability.model_copy(update={"metrics_file": str(tmp_path / f"{name}.json")}),
    })
    return ShadowRunner(
        settings, adapter, ReplayFrontierClient(allow_trade=True), ReplayJevClient(),
        arm=kwargs.pop("arm", "C"), ledger_path=tmp_path / name / "ledger.jsonl",
        clock=kwargs.pop("clock", lambda: now), **kwargs,
    )


def _shared_now():
    return datetime.now(timezone.utc)


def _semantic(records):
    return [
        {
            "decision_id": r.decision_id,
            "hypothesis_id": r.frontier_hypothesis.hypothesis_id if r.frontier_hypothesis else None,
            "frontier": r.frontier_hypothesis.model_dump(mode="json") if r.frontier_hypothesis else None,
            "jev": r.jev_evaluation.model_dump(mode="json") if r.jev_evaluation else None,
            "policy": r.policy_decision.model_dump(mode="json"),
            "risk": r.risk_decision.model_dump(mode="json"),
        }
        for r in records
    ]


def test_continuous_run_is_deterministic(tmp_path):
    now = _shared_now()
    a = _runner(tmp_path, "a", _adapter(now), now=now)
    a.run_once(); a.run_once()
    b = _runner(tmp_path, "b", _adapter(now), now=now)
    b.run_once(); b.run_once()
    assert _semantic(a.ledger.records()) == _semantic(b.ledger.records())


def test_crash_after_ledger_commit_but_before_checkpoint_recovers(tmp_path):
    """Simulates a crash in the append->checkpoint window by truncating the checkpoint."""
    run = _runner(tmp_path, "crash", _adapter())
    run.run_once(); run.run_once()
    full = _semantic(run.ledger.records())
    assert full

    stale = dict(json.loads(run.checkpoint_path.read_text()))
    stale["ledger_hash"] = "stale"
    run.checkpoint_path.write_text(json.dumps(stale))

    resumed = _runner(tmp_path, "crash", _adapter())
    # State is rebuilt from the ledger suffix, not guessed.
    assert resumed.position == run.position
    assert resumed.account == run.account
    assert resumed.execution == run.execution
    assert [f.fill_id for f in resumed.ledger.fills()] == [f.fill_id for f in run.ledger.fills()]


def test_crash_with_missing_checkpoint_rebuilds_from_ledger(tmp_path):
    run = _runner(tmp_path, "missing", _adapter())
    run.run_once(); run.run_once()
    expected = _semantic(run.ledger.records())
    run.checkpoint_path.unlink()

    resumed = _runner(tmp_path, "missing", _adapter())
    assert resumed.position == run.position
    assert _semantic(resumed.ledger.records()) == expected


def test_checkpoint_is_atomically_valid_and_anchored(tmp_path):
    run = _runner(tmp_path, "anchor", _adapter())
    run.run_once()
    payload = json.loads(run.checkpoint_path.read_text())
    assert payload["schema_version"] == 1
    assert payload["experiment_id"] == "RECOVERY-EXP"
    assert payload["ledger_hash"] == run.ledger.last_hash
    assert not run.checkpoint_path.with_suffix(run.checkpoint_path.suffix + ".tmp").exists()


def test_checkpoint_from_another_experiment_is_rejected(tmp_path):
    run = _runner(tmp_path, "wrong", _adapter())
    run.run_once()
    payload = json.loads(run.checkpoint_path.read_text())
    payload["experiment_id"] = "SOME-OTHER-EXP"
    run.checkpoint_path.write_text(json.dumps(payload))
    with pytest.raises(RuntimeError, match="another experiment"):
        _runner(tmp_path, "wrong", _adapter())


def test_resumed_run_replays_intelligence_without_calling_provider(tmp_path):
    """The ledger is the intelligence cache: no fresh provider call on resume."""
    calls = {"frontier": 0, "jev": 0}
    now = _shared_now()

    settings = load_settings().model_copy(update={"experiment_id": "REPLAY-EXP"})
    ledger = tmp_path / "replay" / "ledger.jsonl"
    frontier, jev = CountingFrontier(), CountingJev()
    first = ShadowRunner(settings, _adapter(now), frontier, jev, arm="C", ledger_path=ledger, clock=lambda: now)
    first.run_once()
    calls["frontier"], calls["jev"] = frontier.calls, jev.calls
    assert (calls["frontier"], calls["jev"]) == (1, 1)

    # Same experiment + same decision timestamp => same deterministic request id.
    frontier2, jev2 = CountingFrontier(), CountingJev()
    second = ShadowRunner(settings, _adapter(now), frontier2, jev2, arm="C", ledger_path=ledger, clock=lambda: now)
    second.run_once()
    calls["frontier"], calls["jev"] = frontier2.calls, jev2.calls
    assert (calls["frontier"], calls["jev"]) == (0, 0), "resume must not re-query the provider"
    assert _semantic(second.ledger.records()) == _semantic(first.ledger.records())

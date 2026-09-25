"""Deterministic fake-provider end-to-end shadow path."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import polars as pl

from jev_trading.contracts import BAR_COLUMNS
from jev_trading.data.normalization import ReplayAdapter
from jev_trading.live_intelligence.config import load_settings
from jev_trading.live_intelligence.frontier.client import ReplayFrontierClient
from jev_trading.live_intelligence.jev.client import JevClient
from jev_trading.live_intelligence.runner import ShadowRunner
from jev_trading.live_intelligence.schemas import DataEventType, JevEvaluation, MarketTick, MarketType
from jev_trading.replay import ReplayEngine

UTC = timezone.utc


class FakeJev(JevClient):
    model_version = "fake-jev-v1"

    def evaluate(self, request):
        return JevEvaluation(
            decision_id=request.request_id, request_id=request.request_id, timestamp=request.timestamp,
            valid_until=request.timestamp + timedelta(seconds=120),
            probabilities={"target": 0.8, "stop": 0.1, "timeout": 0.1},
            target_probability=0.8, entry_quality=0.8, failure_risk=0.1, liquidity_quality=0.8,
            ratings={}, answers={}, confidence=0.8, recommended_state="ENTER", reason="fake provider",
            model_version=self.model_version, prompt_version=request.prompt_version,
        )


def test_fake_providers_execute_full_shadow_path_to_paper_ledger(tmp_path: Path):
    base = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=61)
    count = 300
    rows = []
    for index in range(count):
        price = 100 + index * 0.01 + (2 if index % 20 == 0 else 0)
        timestamp = base - timedelta(minutes=count - index)
        rows.append({
            "timestamp": int(timestamp.timestamp() * 1000), "open": price,
            "high": price + 0.2, "low": price - 0.2, "close": price,
            "volume": 10.0, "funding_rate": 0.0, "open_interest": 1000.0,
        })
    frame = pl.DataFrame(rows, schema={column: pl.Int64 if column == "timestamp" else pl.Float64 for column in BAR_COLUMNS})
    last = float(frame["close"][-1])
    tick = MarketTick(
        source="primary", source_role="primary", instrument="BTCUSDT_PERP", venue="binance-futures",
        market_type=MarketType.PERPETUAL, event_timestamp=base, received_timestamp=base,
        event_type=DataEventType.BAR, last=last, bid=last - 0.01, ask=last + 0.01, mid=last,
        bid_depth=10, ask_depth=10, volume=10, open_interest=1000,
    )
    settings = load_settings()
    settings = settings.model_copy(update={
        "frontier": settings.frontier.model_copy(update={"min_call_interval_seconds": 0}),
        "jev": settings.jev.model_copy(update={"min_call_interval_seconds": 0, "validity_seconds": 300}),
        "costs": settings.costs.model_copy(update={"fee_bps_per_side": 0.0, "slippage_bps_per_side": 0.0, "latency_bps": 0.0}),
        "risk": settings.risk.model_copy(update={"stale_data_ms": 10_000_000, "maximum_slippage_bps": 0.0}),
        "research": settings.research.model_copy(update={"root": str(tmp_path / "research")}),
        "observability": settings.observability.model_copy(update={"metrics_file": str(tmp_path / "metrics.json")}),
    })
    adapter = ReplayAdapter("primary", "binance-futures", "BTCUSDT_PERP", frame, [tick])
    clock = [base]
    ledger_path = tmp_path / "ledger.jsonl"
    runner = ShadowRunner(
        settings, adapter, ReplayFrontierClient(allow_trade=True), FakeJev(),
        ledger_path=ledger_path, clock=lambda: clock[0],
    )
    first = runner.run_once()
    assert first.frontier_hypothesis is not None and not first.frontier_hypothesis.abstain
    assert first.jev_request is not None and first.jev_evaluation is not None
    assert first.execution_intent is not None
    assert not runner.ledger.fills()

    next_open = float(frame["close"][-1])
    next_row = {
        "timestamp": int(base.timestamp() * 1000), "open": next_open, "high": next_open + 0.2,
        "low": next_open - 0.2, "close": next_open, "volume": 10.0,
        "funding_rate": 0.0, "open_interest": 1000.0,
    }
    adapter.bars = pl.concat([adapter.bars, pl.DataFrame([next_row], schema={column: pl.Int64 if column == "timestamp" else pl.Float64 for column in BAR_COLUMNS})])
    received = base + timedelta(minutes=1)
    adapter.ticks = [tick.model_copy(update={"event_timestamp": received - timedelta(microseconds=1), "received_timestamp": received})]
    clock[0] = base + timedelta(minutes=1)
    second = runner.run_once()
    assert second.market_environment.data_quality.safe_for_trading is True
    assert len(runner.ledger.fills()) == 1
    assert runner.position.side.value == "LONG"
    reconstruction = ReplayEngine(ledger_path).reconstruct(first.decision_id)
    assert reconstruction["known_state"]["quant_evidence"]
    assert len(reconstruction["execution"]["fills"]) == 1

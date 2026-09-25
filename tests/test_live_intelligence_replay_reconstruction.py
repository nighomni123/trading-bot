"""Decision reconstruction replay tests."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from jev_trading.contracts import BAR_COLUMNS
from jev_trading.data.normalization import ReplayAdapter
from jev_trading.live_intelligence.config import load_settings
from jev_trading.live_intelligence.frontier.client import DisabledFrontierClient
from jev_trading.live_intelligence.jev.client import DisabledJevClient
from jev_trading.live_intelligence.runner import ShadowRunner
from jev_trading.live_intelligence.schemas import DataEventType, MarketTick, MarketType
from jev_trading.replay import ReplayEngine


def test_replay_reconstructs_recorded_state_and_decision(tmp_path: Path):
    count = 300
    now = datetime.now(timezone.utc)
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
    settings = load_settings().model_copy(update={
        "research": load_settings().research.model_copy(update={"root": str(tmp_path / "research")}),
        "observability": load_settings().observability.model_copy(update={"metrics_file": str(tmp_path / "metrics.json")}),
    })
    ledger_path = tmp_path / "ledger.jsonl"
    runner = ShadowRunner(
        settings, ReplayAdapter("primary", "binance-futures", "BTCUSDT_PERP", frame, [tick]),
        DisabledFrontierClient(), DisabledJevClient(), ledger_path=ledger_path,
    )
    record = runner.run_once()
    engine = ReplayEngine(ledger_path)
    reconstructed = engine.reconstruct(record.decision_id)
    assert reconstructed["decision_id"] == record.decision_id
    assert reconstructed["known_state"]["quant_evidence"]["version"] == "quant-evidence-v1"
    assert reconstructed["deterministic_decision"]["policy"]["action"] == record.policy_decision.action.value
    assert reconstructed["deterministic_decision"]["risk"]["status"] == record.risk_decision.status.value
    assert reconstructed["versions"]["frontier_prompt_hash"]

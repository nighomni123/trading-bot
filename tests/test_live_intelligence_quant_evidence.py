"""Canonical QuantEvidence integration."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from jev_trading.contracts import BAR_COLUMNS
from jev_trading.data.normalization import ReplayAdapter
from jev_trading.live_intelligence.config import load_settings
from jev_trading.live_intelligence.frontier.client import ReplayFrontierClient
from jev_trading.live_intelligence.jev.client import ReplayJevClient
from jev_trading.live_intelligence.runner import ShadowRunner
from jev_trading.live_intelligence.schemas import DataEventType, MarketTick, MarketType


def test_runner_ledger_and_jev_share_one_coherent_quant_evidence(tmp_path: Path):
    count = 300
    now = datetime.now(timezone.utc)
    start_ms = int(now.timestamp() * 1000) - count * 60_000
    rows = []
    for index in range(count):
        close = 100 + index * 0.1
        rows.append({
            "timestamp": start_ms + index * 60_000,
            "open": close, "high": close + 0.05, "low": close - 0.05,
            "close": close, "volume": 10.0, "funding_rate": 0.0001, "open_interest": 1000.0,
        })
    frame = pl.DataFrame(rows, schema={column: pl.Int64 if column == "timestamp" else pl.Float64 for column in BAR_COLUMNS})
    observation = MarketTick(
        source="primary", source_role="primary", instrument="BTCUSDT_PERP",
        venue="binance-futures", market_type=MarketType.PERPETUAL,
        event_timestamp=now, received_timestamp=now, event_type=DataEventType.BAR,
        last=100, volume=10, open_interest=1000, funding_rate=0.0001,
    )
    runner = ShadowRunner(
        load_settings(),
        ReplayAdapter("primary", "binance-futures", "BTCUSDT_PERP", frame, [observation]),
        ReplayFrontierClient(allow_trade=True), ReplayJevClient(),
        ledger_path=tmp_path / "ledger.jsonl",
    )
    record = runner.run_once()
    evidence = record.quant_evidence
    assert evidence is not None
    names = [result.analysis_name for result in evidence.analyzer_results]
    assert len(names) == len(set(names))
    assert names.count("path") <= 1
    assert names.count("opportunity") <= 1
    assert evidence.analyzer_versions["trend"] == "trend-v1"
    if evidence.economic_value is not None:
        opportunity = next(result for result in evidence.analyzer_results if result.analysis_name == "opportunity")
        assert opportunity.evidence["economic_value"] is not None
        assert evidence.probabilities == evidence.economic_value.probabilities
    if record.jev_request is not None:
        assert record.jev_request.quant_evidence == evidence

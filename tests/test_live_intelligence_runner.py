"""Runner and empirical path integration tests."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from jev_trading.contracts import BAR_COLUMNS
from jev_trading.data.normalization import ReplayAdapter
from jev_trading.environment import build_market_environment
from jev_trading.live_intelligence.frontier.client import ReplayFrontierClient
from jev_trading.live_intelligence.jev.client import ReplayJevClient
from jev_trading.live_intelligence.quant import build_completed_path_samples
from jev_trading.live_intelligence.runner import ShadowRunner
from jev_trading.live_intelligence.config import load_settings
from jev_trading.live_intelligence.schemas import DataQuality, DataEventType, MarketTick, MarketType, Side

UTC = timezone.utc
T0 = int(datetime.now(UTC).timestamp() * 1000) - 300 * 60_000


def test_completed_path_builder_excludes_incomplete_tail_and_is_causal():
    n = 40
    rows = []
    for i in range(n):
        close = 100 + i * 0.1
        rows.append({"timestamp": T0 + i * 60_000, "open": close, "high": close + 0.05, "low": close - 0.05, "close": close})
    frame = pl.DataFrame(rows)
    samples = build_completed_path_samples(frame, side=Side.LONG, target_fraction=0.001, stop_fraction=0.001, horizon_minutes=5)
    assert samples
    assert all(sample.timestamp < datetime.fromtimestamp((T0 + (n - 1) * 60_000) / 1000, tz=UTC) for sample in samples)
    changed = frame.with_columns(pl.when(pl.arange(0, n) >= 30).then(pl.col("high") * 100).otherwise(pl.col("high")).alias("high"))
    changed_samples = build_completed_path_samples(changed, side=Side.LONG, target_fraction=0.001, stop_fraction=0.001, horizon_minutes=5)
    assert samples != changed_samples


def test_runner_records_request_and_fails_closed_with_disabled_providers(tmp_path: Path):
    settings = load_settings()
    n = 300
    base = int(datetime.now(UTC).timestamp() * 1000) - n * 60_000
    rows = []
    for i in range(n):
        close = 100 + i * 0.01
        rows.append({"timestamp": base + i * 60_000, "open": close, "high": close + 0.1, "low": close - 0.1, "close": close, "volume": 10.0, "funding_rate": 0.0001, "open_interest": 1000.0})
    frame = pl.DataFrame(rows, schema={c: pl.Int64 if c == "timestamp" else pl.Float64 for c in BAR_COLUMNS})
    now = datetime.now(UTC)
    observation = MarketTick(source="primary", source_role="primary", instrument="BTCUSDT_PERP", venue="binance-futures", market_type=MarketType.PERPETUAL, event_timestamp=now, received_timestamp=now, event_type=DataEventType.BAR, last=100, mark=100, volume=10, open_interest=1000, funding_rate=0.0001)
    adapter = ReplayAdapter("primary", "binance-futures", "BTCUSDT_PERP", frame, [observation])
    runner = ShadowRunner(settings, adapter, ReplayFrontierClient(allow_trade=True), ReplayJevClient(), ledger_path=tmp_path / "ledger.jsonl")
    record = runner.run_once()
    assert record.frontier_hypothesis is not None
    assert record.policy_decision.action.value == "NO_TRADE"
    assert record.risk_decision.status.value == "REJECTED"
    assert record.execution_intent is None
    assert runner.ledger.has_decision(record.decision_id)

"""Independent A/B/C ablation-arm wiring."""
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
from tests.test_live_intelligence import aligned_start_ms


class CountingFrontier(ReplayFrontierClient):
    def __init__(self):
        super().__init__(allow_trade=True)
        self.calls = 0

    def complete(self, **kwargs):
        self.calls += 1
        return super().complete(**kwargs)


class CountingJev(ReplayJevClient):
    def __init__(self):
        self.calls = 0

    def evaluate(self, request):
        self.calls += 1
        return super().evaluate(request)


def _adapter():
    count = 300
    now = datetime.now(timezone.utc)
    start = aligned_start_ms(now, count)
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


def test_arms_use_expected_components(tmp_path: Path):
    results = {}
    for arm in ("A", "B", "C"):
        frontier = CountingFrontier()
        jev = CountingJev()
        settings = load_settings().model_copy(update={
            "research": load_settings().research.model_copy(update={"root": str(tmp_path / arm)}),
            "observability": load_settings().observability.model_copy(update={"metrics_file": str(tmp_path / f"{arm}.json")}),
        })
        runner = ShadowRunner(settings, _adapter(), frontier, jev, arm=arm, ledger_path=tmp_path / f"{arm}.jsonl")
        record = runner.run_once()
        results[arm] = (record, frontier.calls, jev.calls)
    assert results["A"][1:] == (0, 0)
    assert results["B"][1:] == (1, 0)
    assert results["C"][1:] == (1, 1)
    assert {record.versions.experiment_arm for record, _, _ in results.values()} == {"A", "B", "C"}

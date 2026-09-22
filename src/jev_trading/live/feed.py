"""Live bar feed adapter: Binance klines -> BAR_COLUMNS, feeds WorldModel + Jev + Policy."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Awaitable, TYPE_CHECKING

import polars as pl

from jev_trading.contracts import BAR_COLUMNS
from jev_trading.data.fetch import (
    fetch_klines,
    fetch_funding,
    fetch_open_interest,
    merge_enrichment,
    MINUTE_MS,
    LOOKBACK_MS,
)

if TYPE_CHECKING:
    from jev_trading.frontier.world_model import WorldDigest
    from jev_trading.jev.client import JevClient
    from jev_trading.policy.engine import PolicyProposal


@dataclass
class BarFeed:
    """Streaming 1m bar feed from Binance USDT perp.

    Note: Requires network access to Binance fapi. No API key needed for public endpoints.
    The feed uses the same fetch/merge logic as data/fetch.py but adapted for streaming.

    Usage:
        feed = BarFeed(symbol="BTCUSDT", interval="1m", lookback=2640)
        async for bar in feed.poll():
            # process bar
        # or callback style:
        feed.on_bar(my_callback)
        await feed.run()
    """
    symbol: str = "BTCUSDT"
    interval: str = "1m"
    lookback: int = 2640  # 44 hours = enough for all feature warmup (ema200 + 1d z-scores)

    def __post_init__(self) -> None:
        self._callback: Callable[[dict], Awaitable[None]] | None = None
        self._running = False
        self._bars: pl.DataFrame | None = None
        self._last_ts: int | None = None

    def on_bar(self, callback: Callable[[dict], Awaitable[None]]) -> None:
        """Register async callback for new bars. Called with BAR_COLUMNS dict."""
        self._callback = callback

    async def poll(self) -> dict | None:
        """Fetch latest bar. Returns BAR_COLUMNS dict or None if no new bar."""
        now_ms = int(time.time() * 1000)
        end_ms = now_ms - (now_ms % MINUTE_MS)  # align to closed minute
        start_ms = end_ms - self.lookback * MINUTE_MS - LOOKBACK_MS

        # Fetch fresh data (reuses fetch.py patterns)
        klines = fetch_klines(start_ms, end_ms + MINUTE_MS, self.symbol)
        if klines.is_empty():
            return None

        funding = fetch_funding(start_ms, end_ms + MINUTE_MS, self.symbol)
        oi = fetch_open_interest(start_ms, end_ms + MINUTE_MS, self.symbol)

        bars = merge_enrichment(klines, funding, oi)
        if bars.is_empty():
            return None

        # Keep only the latest bar
        latest = bars.tail(1)
        if latest.is_empty():
            return None

        ts = latest["timestamp"][0]
        if self._last_ts is not None and ts <= self._last_ts:
            return None  # no new bar

        self._last_ts = ts
        self._bars = bars  # cache for to_bars()

        # Convert to dict
        row = latest.row(0, named=True)
        bar_dict = {c: row[c] for c in BAR_COLUMNS}
        return bar_dict

    def to_bars(self) -> pl.DataFrame:
        """Return full BAR_COLUMNS DataFrame (cached from last poll)."""
        if self._bars is None:
            # Initial fetch
            now_ms = int(time.time() * 1000)
            end_ms = now_ms - (now_ms % MINUTE_MS)
            start_ms = end_ms - self.lookback * MINUTE_MS - LOOKBACK_MS
            klines = fetch_klines(start_ms, end_ms + MINUTE_MS, self.symbol)
            funding = fetch_funding(start_ms, end_ms + MINUTE_MS, self.symbol)
            oi = fetch_open_interest(start_ms, end_ms + MINUTE_MS, self.symbol)
            self._bars = merge_enrichment(klines, funding, oi)
        return self._bars

    async def run(self, interval_sec: float = 60.0) -> None:
        """Run feed loop, calling callback on each new bar.

        Note: This is a blocking async loop. Run in a task.
        """
        self._running = True
        while self._running:
            bar = await self.poll()
            if bar is not None and self._callback is not None:
                await self._callback(bar)
            await asyncio.sleep(interval_sec)

    def stop(self) -> None:
        self._running = False


async def _demo_callback(bar: dict) -> None:
    """Example callback: print bar and feed WorldModel + Jev + Policy."""
    print(f"New bar: {bar['timestamp']} close={bar['close']:.2f}")
    # TODO: integrate with WorldModel, JevClient, PolicyEngine
    # state = build_state_dict(bar)
    # jev_answers = await jev_client.ask(state, questions)
    # proposal = decide(quant_predict(state), jev_answers)
    # risk_decision = risk_kernel.evaluate(proposal, portfolio, market, now_ms, bar['close'])


# Example usage (not run on import):
# if __name__ == "__main__":
#     feed = BarFeed()
#     feed.on_bar(_demo_callback)
#     asyncio.run(feed.run())

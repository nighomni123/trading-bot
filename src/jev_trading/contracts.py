"""Shared contracts: schemas every phase codes against.

ponytail: only what cross-phase consumers need — bar schema + action space.
Module-specific types live with their owning modules.
"""
from __future__ import annotations

from enum import Enum

# P1 writes Parquet with exactly these columns; P2/P3/P5/P7 consume them.
# timestamp = epoch ms UTC, strictly increasing, one row per 1m bar (resample for 5m+).
BAR_COLUMNS: tuple[str, ...] = (
    "timestamp",       # int64 epoch ms UTC
    "open",            # float64
    "high",            # float64
    "low",             # float64
    "close",           # float64
    "volume",          # float64 base-asset volume
    "funding_rate",    # float64, per-8h rate; forward-filled if funding updates less often
    "open_interest",   # float64 base-asset OI; forward-filled
)

INSTRUMENT = "BTCUSDT_PERP"  # Binance USDT-denominated BTC futures (BTCUSDT)

PM_SOURCES: tuple[str, ...] = ("polymarket", "kalshi")  # order = fetch priority

# Kalshi / Polymarket binary markets: a YES contract pays $1 at settlement, so its
# price is the implied P(YES) in [0, 1]. Normalized snapshot consumed by the live
# feed as sentiment / order-book input alongside BAR_COLUMNS.
PM_COLUMNS: tuple[str, ...] = (
    "timestamp",       # int64 epoch ms UTC, quote observation time (server-stamped, never the poll time)
    "platform",        # "polymarket" | "kalshi"
    "market_id",       # str, platform-native id (Polymarket condition id / Kalshi ticker)
    "event_id",        # str, parent event id
    "question",        # str
    "yes_bid",         # float64, best YES bid in [0,1]
    "yes_ask",         # float64, best YES ask in [0,1]
    "last_price",      # float64, last trade YES price in [0,1]
    "spread",          # float64, ask - bid
    "mid_price",       # float64, (bid + ask) / 2
    "best_bid_size",   # float64, contracts at best bid
    "best_ask_size",   # float64, contracts at best ask
    "depth_bids",      # float64, contracts in top 10 bids
    "depth_asks",      # float64, contracts in top 10 asks
    "liquidity",       # float64, USD
    "volume_24h",      # float64, USD
    "volume_total",    # float64, USD
    "open_interest",   # float64, USD (null when a platform omits it)
    "close_date",      # int64 epoch ms UTC, market resolution time
    "status",          # str, "open" | "closed" | raw platform status
)

# Normalized YES-probability OHLC history (for replay / training).
PM_BAR_COLUMNS: tuple[str, ...] = (
    "timestamp",       # int64 epoch ms UTC, candle end (inclusive)
    "platform",        # "polymarket" | "kalshi"
    "market_id",       # str
    "question",        # str
    "open",            # float64 YES probability in [0,1]
    "high",
    "low",
    "close",
    "volume",          # float64 contracts
    "open_interest",   # float64 contracts
)


class Action(str, Enum):
    """Constrained action space (Conversation 1). Policy engine emits; risk kernel gates."""

    NO_ACTION = "NO_ACTION"
    ENTER_LONG = "ENTER_LONG"
    ENTER_SHORT = "ENTER_SHORT"
    REDUCE = "REDUCE"
    EXIT = "EXIT"

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


class Action(str, Enum):
    """Constrained action space (Conversation 1). Policy engine emits; risk kernel gates."""

    NO_ACTION = "NO_ACTION"
    ENTER_LONG = "ENTER_LONG"
    ENTER_SHORT = "ENTER_SHORT"
    REDUCE = "REDUCE"
    EXIT = "EXIT"

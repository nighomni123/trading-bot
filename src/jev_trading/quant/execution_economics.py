"""Stage 10: measured execution economics.

Public market data only. No credentials, no order endpoints, no execution.
The goal is to replace an assumed cost stack with a measured one, keeping
provenance on every component so an unmeasured term can never masquerade as
a measured one.
"""
from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from typing import Any

import requests

BINANCE_BOOK = "https://fapi.binance.com/fapi/v1/ticker/bookTicker"
BINANCE_DEPTH = "https://fapi.binance.com/fapi/v1/depth"

# Documented Binance USD-M base tier. VIP/discount tiers are NOT assumed
# achievable for this account; they must be confirmed by the operator.
DOCUMENTED_FEES = {
    "taker_bps_per_side": 5.0,   # 0.05%
    "maker_bps_per_side": 2.0,   # 0.02%
    "source": "Binance USD-M futures base tier; VIP/discount tiers excluded pending operator confirmation",
}


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(len(ordered) * q))
    return ordered[index]


@dataclass
class CostMeasurement:
    """Raw observations. Nothing here is an assumption."""
    spread_bps: list[float] = field(default_factory=list)
    taker_slippage_bps: list[float] = field(default_factory=list)
    maker_slippage_bps: list[float] = field(default_factory=list)
    book_depth_levels: list[int] = field(default_factory=list)
    top_notional_usd: list[float] = field(default_factory=list)
    samples: int = 0
    venue_reachable: bool = True
    error: str | None = None

    def summary(self) -> dict[str, Any]:
        def stats(values):
            return {
                "n": len(values),
                "median": statistics.median(values) if values else None,
                "p90": _percentile(values, 0.90),
                "max": max(values) if values else None,
            }
        return {
            "venue_reachable": self.venue_reachable,
            "error": self.error,
            "samples": self.samples,
            "spread_bps": stats(self.spread_bps),
            "taker_slippage_bps": stats(self.taker_slippage_bps),
            "maker_slippage_bps": stats(self.maker_slippage_bps),
            "book_depth_levels": stats(self.book_depth_levels),
            "top_level_notional_usd": stats(self.top_notional_usd),
        }


def measure_book(
    symbol: str = "BTCUSDT", *, samples: int = 60, interval: float = 0.25,
    notional_usd: float = 1000.0, timeout: float = 10.0,
    session: requests.Session | None = None,
) -> CostMeasurement:
    """Sample live spread and book-walked slippage. Read-only.

    Taker slippage walks the ask side (a buy lifts the offer). Maker slippage
    is measured as the bid-side fill a resting order would receive.
    """
    http = session or requests
    out = CostMeasurement()
    for _ in range(samples):
        try:
            book = http.get(BINANCE_BOOK, params={"symbol": symbol}, timeout=timeout).json()
            depth = http.get(BINANCE_DEPTH, params={"symbol": symbol, "limit": 20}, timeout=timeout).json()
        except Exception as exc:
            out.venue_reachable = False
            out.error = type(exc).__name__
            return out

        bid, ask = float(book["bidPrice"]), float(book["askPrice"])
        if bid <= 0 or ask <= 0 or ask < bid:
            continue
        mid = (bid + ask) / 2
        out.spread_bps.append((ask - bid) / mid * 1e4)
        out.samples += 1

        # Walk the book to fill `notional_usd`; slippage = |avg_fill - mid| / mid.
        for levels, bucket in (
            (depth["asks"], out.taker_slippage_bps),   # taker buys: cross the ask
            (depth["bids"], out.maker_slippage_bps),   # maker would rest on the bid
        ):
            remaining_qty = notional_usd / mid
            cost = 0.0
            for price, size in levels:
                price, size = float(price), float(size)
                take = min(size, remaining_qty)
                cost += take * price
                remaining_qty -= take
                if remaining_qty <= 1e-9:
                    break
            if remaining_qty > 1e-6:
                continue
            avg = cost / (notional_usd / mid)
            bucket.append(abs(avg - mid) / mid * 1e4)
        if depth["bids"] and depth["asks"]:
            top_bid, top_ask = float(depth["bids"][0][0]), float(depth["asks"][0][0])
            out.book_depth_levels.append(len(depth["bids"]) + len(depth["asks"]))
            out.top_notional_usd.append(
                (top_bid * float(depth["bids"][0][1]) + top_ask * float(depth["asks"][0][1]))
            )
        time.sleep(interval)
    return out


@dataclass(frozen=True)
class CostProfile:
    """A declared execution scenario. Feasible flags matter more than price."""
    name: str
    execution_mode: str          # taker/taker | taker/maker | maker/maker
    fee_bps_per_side: float
    slippage_source: str         # "measured" | "assumed"
    latency_bps: float
    latency_source: str
    achievable: bool             # may this mode actually be executed?
    note: str = ""


def build_profiles(measurement: CostMeasurement | None) -> list[CostProfile]:
    """Declare scenarios. Measured slippage is used only where it was measured."""
    measured_taker = None
    measured_maker = None
    if measurement and measurement.samples:
        if measurement.taker_slippage_bps:
            measured_taker = statistics.median(measurement.taker_slippage_bps)
        if measurement.maker_slippage_bps:
            measured_maker = statistics.median(measurement.maker_slippage_bps)

    taker_slip = measured_taker if measured_taker is not None else 2.0
    maker_slip = measured_maker if measured_maker is not None else 0.0
    src = "measured" if measured_taker is not None else "assumed"

    return [
        CostProfile(
            name="configured_taker_taker", execution_mode="taker/taker",
            fee_bps_per_side=DOCUMENTED_FEES["taker_bps_per_side"],
            slippage_source="assumed", latency_bps=1.0, latency_source="assumed",
            achievable=True,
            note="The current configs/live.json assumption, retained for comparison.",
        ),
        CostProfile(
            name="measured_taker_taker", execution_mode="taker/taker",
            fee_bps_per_side=DOCUMENTED_FEES["taker_bps_per_side"],
            slippage_source=src, latency_bps=1.0, latency_source="assumed",
            achievable=True,
            note="Documented base-tier taker fee with book-walked slippage.",
        ),
        CostProfile(
            name="measured_taker_maker", execution_mode="taker/maker",
            fee_bps_per_side=DOCUMENTED_FEES["taker_bps_per_side"] + DOCUMENTED_FEES["maker_bps_per_side"],
            slippage_source=src, latency_bps=1.0, latency_source="assumed",
            achievable=True,
            note="Enter taker, exit maker. Requires the bot to rest a passive exit.",
        ),
        CostProfile(
            name="measured_maker_maker", execution_mode="maker/maker",
            fee_bps_per_side=DOCUMENTED_FEES["maker_bps_per_side"] * 2,
            slippage_source="measured" if measured_maker is not None else "assumed",
            latency_bps=1.0, latency_source="assumed",
            achievable=False,
            note="Both legs passive. Fill probability is NOT modelled here, so this "
                 "profile is reported but never used to declare viability.",
        ),
    ]


def profile_total_bps(profile: CostProfile, measurement: CostMeasurement | None) -> float:
    if profile.slippage_source == "measured" and measurement:
        bucket = (measurement.taker_slippage_bps if "taker" in profile.execution_mode.split("/")[0]
                  else measurement.maker_slippage_bps)
        if bucket:
            return 2 * profile.fee_bps_per_side + statistics.median(bucket) + profile.latency_bps
    return 2 * profile.fee_bps_per_side + 2 * 2.0 + profile.latency_bps

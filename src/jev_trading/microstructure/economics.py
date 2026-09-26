"""Economic scorecard (Phase 25/26).

Applies the Stage 10 measured cost profile with provenance preserved.
`configs/live.json` is never read or modified here.
"""
from __future__ import annotations

import numpy as np
import polars as pl

from .schema import ALPHA_EXPERIMENT_VERSION

#: Stage 10 measured profile (median measured slippage + documented 5bps/side
#: taker fee + 1bps latency allowance), carried forward with provenance.
STAGE10_MEASURED_COST_BPS = 11.006
STAGE10_COST_PROVENANCE = {
    "source": "stage-10 live measurement, Binance BTCUSDT public endpoints",
    "taker_fee_bps_per_side": 5.0,
    "slippage_bps_median_measured": 0.0060,
    "latency_bps": 1.0,
    "measured_at_cost_bps": STAGE10_MEASURED_COST_BPS,
    "note": "Research reference only. configs/live.json remains 15.0 bps and is unmodified.",
}


def economic_scorecard(
    event_name: str, conditional_mean_bps: float, n: int,
    *, cost_bps: float = STAGE10_MEASURED_COST_BPS, direction: str = "long",
) -> dict:
    """gross -> cost -> net for a single signal. No ranking, no promotion."""
    net = conditional_mean_bps - cost_bps
    return {
        "event": event_name,
        "direction": direction,
        "n": n,
        "gross_expected_bps": conditional_mean_bps,
        "cost_bps": cost_bps,
        "net_expected_bps": net,
        "economically_viable": net > 0,
        "cost_multiple": (cost_bps / conditional_mean_bps) if conditional_mean_bps > 0 else None,
    }


def scorecard_from_study(study: dict, *, cost_bps: float = STAGE10_MEASURED_COST_BPS) -> dict:
    """Build the economic view for every event/horizon in a study result."""
    rows = []
    for name, entry in study.get("events", {}).items():
        n = entry.get("n_events", 0)
        for horizon, block in entry.get("by_horizon", {}).items():
            test = block.get("test") or {}
            diff = test.get("diff")
            if diff is None or n < 30:
                continue
            rows.append(economic_scorecard(
                f"{name}:{horizon}", diff * 1e4, n, cost_bps=cost_bps,
            ))
    viable = [r for r in rows if r["economically_viable"]]
    return {
        "experiment_version": ALPHA_EXPERIMENT_VERSION,
        "cost_provenance": STAGE10_COST_PROVENANCE,
        "rows": rows,
        "n_rows": len(rows),
        "n_economically_viable": len(viable),
        "viable": viable,
    }

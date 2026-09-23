"""Comparison and reconciliation between engines.

Not for automated optimization; for investigating differences.
"""
from __future__ import annotations

from jev_trading.backtest.base import BacktestResult, Trade


def compare_backtests(local_result: BacktestResult, lean_result: BacktestResult) -> dict:
    """Compare headline metrics; do not interpret small diffs as errors."""
    return {
        "delta_pnl": lean_result.net_pnl - local_result.net_pnl,
        "delta_return": lean_result.return_pct - local_result.return_pct,
        "delta_drawdown": lean_result.max_drawdown - local_result.max_drawdown,
        "delta_trade_count": lean_result.trade_count - local_result.trade_count,
        "delta_win_rate": lean_result.win_rate - local_result.win_rate,
        "local_engine": local_result.engine,
        "lean_engine": lean_result.engine,
        "local_experiment": local_result.experiment_id,
        "lean_experiment": lean_result.experiment_id,
        "note": "Agreement increases confidence; disagreement is a debugging/research signal, not an error.",
    }


def reconcile_trades(local_trades: list[Trade], lean_trades: list[Trade]) -> dict:
    """Trade-level reconciliation."""
    # Simplified: pair by trade_id if present; else report missing/extra.
    local_ids = {t.trade_id for t in local_trades}
    lean_ids = {t.trade_id for t in lean_trades}
    return {
        "local_count": len(local_trades),
        "lean_count": len(lean_trades),
        "missing_in_lean": sorted(local_ids - lean_ids),
        "extra_in_lean": sorted(lean_ids - local_ids),
        "common": sorted(local_ids & lean_ids),
        "note": "Compare timestamp, price, quantity, fees, slippage for common trades.",
    }

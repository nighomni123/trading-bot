"""LocalBacktestEngine: adapter around existing simulator.

Ponytail: wraps simulator.run; does not duplicate logic.
Preserves next-bar execution semantics (docs/execution-semantics.md).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from jev_trading.backtest.base import BacktestEngine, BacktestResult, Trade, DecisionEvent
from jev_trading.backtest.simulator import SimConfig, prepare, run, ARMS


class LocalBacktestEngine(BacktestEngine):
    def __init__(self, cfg: SimConfig | None = None):
        self.cfg = cfg or SimConfig()

    @property
    def name(self) -> str:
        return "local"

    def run(
        self,
        strategy: object = None,
        data: pl.DataFrame | None = None,
        config: dict | None = None,
    ) -> BacktestResult:
        cfg = self.cfg
        if config:
            for k, v in config.items():
                if hasattr(cfg, k):
                    setattr(cfg, k, v)
        # Accept data as DataFrame; if strategy provides quant/jev, use them
        # Minimal adapter: require data (bars) and either strategy or defaults
        bars = data
        if bars is None:
            raise ValueError("LocalBacktestEngine requires data (polars DataFrame)")
        # For MVP, use default quant mock if not provided by strategy
        quant = getattr(strategy, "quant", None)
        if quant is None:
            # Import lazily to avoid circular; use simple rule-based for baseline
            class MockQuant:
                def predict_up15(self, X):
                    import polars as pl, numpy as np
                    return pl.Series("p_up15", np.full(len(X), 0.5))
            quant = MockQuant()
        feats, p_up = prepare(bars, quant)
        # Default arm for comparison: "threshold" (simplest baseline)
        arm = config.get("arm", "threshold") if config else "threshold"
        # Jev replay: if strategy has cached answers, inject via mock client
        jev_client = getattr(strategy, "jev_client", None) if strategy else None
        result = run(
            feats, p_up, jev_client,
            arm=arm, cfg=cfg,
            log_path=config.get("log_path") if config else None,
        )
        metrics = result["metrics"]
        records = result["records"]
        # Build canonical result from metrics
        initial = cfg.capital_usd
        final = records[-1]["equity"] if records else initial
        net = final - initial
        trades = []
        # Extract entry/exit events from records (simplified)
        for r in records:
            if r.get("decision") in ("ENTER_LONG", "EXIT") and r.get("fill_px") is not None:
                trade = Trade(
                    trade_id=f"T-{r['seq']}",
                    instrument="BTCUSDT_PERP",
                    side=1 if r["decision"] == "ENTER_LONG" else -1,
                    signal_timestamp=datetime.fromtimestamp(r["ts"]/1000, tz=timezone.utc) if r.get("ts") else None,
                    decision_timestamp=datetime.fromtimestamp(r.get("exec_ts", r["ts"])/1000, tz=timezone.utc) if r.get("exec_ts") else None,
                    order_timestamp=datetime.fromtimestamp(r.get("exec_ts", r["ts"])/1000, tz=timezone.utc) if r.get("exec_ts") else None,
                    fill_timestamp=datetime.fromtimestamp(r.get("exec_ts", r["ts"])/1000, tz=timezone.utc) if r.get("exec_ts") else None,
                    quantity=r.get("fill_qty", 0.0),
                    requested_price=r.get("fill_px"),
                    fill_price=r.get("fill_px"),
                    fees=r.get("fee", 0.0),
                    engine=self.name,
                    unavailable=["entry_price", "exit_price", "gross_pnl", "net_pnl", "holding_time", "position_before", "position_after"] if r["decision"]=="ENTER_LONG" else ["entry_price"],
                )
                trades.append(trade)
        # Build equity curve from records
        equity_curve = [{"ts": r["ts"], "equity": r["equity"]} for r in records[:500]]  # cap for size
        # Decisions from records (simplified)
        decisions = []
        for r in records:
            decisions.append(DecisionEvent(
                timestamp=datetime.fromtimestamp(r["ts"]/1000, tz=timezone.utc) if r.get("ts") else None,
                quant_prediction=r.get("p_up"),
                final_action=r.get("decision"),
                engine=self.name,
            ))
        return BacktestResult(
            engine=self.name,
            engine_version="simulator-v2",
            experiment_id=config.get("experiment_id", "EXP-LOCAL-001") if config else "EXP-LOCAL-001",
            start_time=datetime.fromtimestamp(records[0]["ts"]/1000, tz=timezone.utc) if records else datetime.now(timezone.utc),
            end_time=datetime.fromtimestamp(records[-1]["ts"]/1000, tz=timezone.utc) if records else datetime.now(timezone.utc),
            initial_capital=initial,
            final_equity=final,
            net_pnl=net,
            gross_pnl=net + metrics.get("fees", 0.0),
            fees=metrics.get("fees", 0.0),
            slippage=0.0,  # detailed slippage not tracked separately in simulator; exposed in metadata
            return_pct=metrics.get("return_pct", 0.0) / 100.0,
            max_drawdown=metrics.get("max_dd_pct", 0.0) / 100.0,
            trade_count=metrics.get("n_entries", 0) + metrics.get("n_exits", 0),
            winning_trades=metrics.get("n_entries", 0),  # approximate; full reconciliation needs trade pairing
            losing_trades=0,
            win_rate=metrics.get("win_rate") or 0.0,
            profit_factor=1.0,  # not computed by simulator directly
            sharpe=None,
            sortino=None,
            average_trade=metrics.get("avg_per_exit", 0.0) or 0.0,
            average_holding_period=0.0,
            turnover=metrics.get("exposure", 0.0),
            equity_curve=equity_curve,
            trades=trades,
            decisions=decisions,
            metadata={
                "arm": arm,
                "simulator_version": 2,
                "execution_assumption": "next-bar open[t+1] + slippage",
                "fees_model": f"taker_fee_pct={cfg.taker_fee_pct} fee_mult={cfg.fee_mult}",
                "slippage_model": f"slippage_pct={cfg.slippage_pct}",
                "data_source": "canonical_bar_df",
                "point_in_time": True,
                "lookahead_forbidden": True,
            },
        )

"""Canonical pluggable backtest interface.

Ponytail: minimal ABC; no LEAN-specific types here.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Trade:
    trade_id: str
    strategy_id: str | None = None
    instrument: str = "BTCUSDT_PERP"
    side: int = 1  # +1 long, -1 short, 0 flat (entry/exit context)
    signal_timestamp: datetime | None = None
    decision_timestamp: datetime | None = None
    order_timestamp: datetime | None = None
    fill_timestamp: datetime | None = None
    quantity: float = 0.0
    requested_price: float | None = None
    fill_price: float | None = None
    entry_price: float | None = None
    exit_price: float | None = None
    gross_pnl: float = 0.0
    fees: float = 0.0
    slippage: float = 0.0
    net_pnl: float = 0.0
    holding_time: float = 0.0  # bars or seconds; documented in metadata
    position_before: float = 0.0
    position_after: float = 0.0
    risk_decision: str | None = None
    jev_decision: str | None = None
    policy_version: str | None = None
    engine: str = "local"
    unavailable: list[str] = field(default_factory=list)


@dataclass
class DecisionEvent:
    timestamp: datetime | None = None
    market_state_id: str | None = None
    quant_prediction: float | None = None
    policy_version: str | None = None
    jev_questions: list[str] = field(default_factory=list)
    jev_answers: list[str] = field(default_factory=list)
    jev_score: float | None = None
    candidate_action: str | None = None
    final_action: str | None = None
    risk_decision: str | None = None
    risk_rejection_reason: str | None = None
    target_position: float | None = None
    actual_position: float | None = None
    engine: str = "local"


@dataclass
class BacktestResult:
    engine: str
    engine_version: str
    experiment_id: str
    start_time: datetime
    end_time: datetime
    initial_capital: float
    final_equity: float
    net_pnl: float
    gross_pnl: float
    fees: float
    slippage: float
    return_pct: float
    max_drawdown: float
    trade_count: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    profit_factor: float
    sharpe: float | None = None
    sortino: float | None = None
    average_trade: float = 0.0
    average_holding_period: float = 0.0
    turnover: float = 0.0
    equity_curve: list[dict] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    decisions: list[DecisionEvent] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


class BacktestEngine(ABC):
    @abstractmethod
    def run(
        self,
        strategy: Any,
        data: Any,
        config: dict | None = None,
    ) -> BacktestResult:
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        ...

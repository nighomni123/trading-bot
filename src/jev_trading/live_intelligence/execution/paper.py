"""Paper-only execution adapter with long/short, fees, slippage, and funding."""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from ..config import LiveSettings
from ..risk import ActiveRiskKernel
from ..schemas import (
    ExecutionIntent,
    MarketEnvironment,
    PaperFill,
    PolicyAction,
    PositionState,
    RiskDecision,
    RiskStatus,
    Side,
    TradeRecord,
)


class PaperExecutor:
    """Simulates fills only; there is intentionally no broker adapter."""

    def __init__(self, settings: LiveSettings, risk: ActiveRiskKernel):
        if settings.execution_mode != "PAPER":
            raise ValueError("PaperExecutor requires PAPER mode")
        self.settings = settings
        self.risk = risk
        self.position = PositionState()
        self._entry: dict | None = None
        self._last_trade: TradeRecord | None = None
        self._cumulative_fees = 0.0
        self._cumulative_slippage = 0.0

    @property
    def mode(self) -> str:
        return "PAPER"

    @property
    def last_trade(self) -> TradeRecord | None:
        return self._last_trade

    @property
    def cumulative_fees(self) -> float:
        return self._cumulative_fees

    @property
    def cumulative_slippage(self) -> float:
        return self._cumulative_slippage

    def _fill_price(self, reference: float, order_side: Side) -> tuple[float, float]:
        slip = reference * self.settings.costs.slippage_bps_per_side / 10_000
        if order_side == Side.LONG:
            return reference + slip, slip
        if order_side == Side.SHORT:
            return reference - slip, slip
        return reference, 0.0

    def execute(self, intent: ExecutionIntent, risk_decision: RiskDecision, environment: MarketEnvironment) -> PaperFill:
        if intent.mode.value != "PAPER" or risk_decision.status != RiskStatus.APPROVED:
            raise ValueError("paper execution requires an approved PAPER intent")
        if intent.decision_id != risk_decision.decision_id:
            raise ValueError("risk decision does not match execution intent")
        if environment.timestamp < intent.earliest_execution_at:
            raise ValueError("execution is not yet eligible")
        quantity = min(intent.quantity, risk_decision.approved_quantity)
        if quantity <= 0:
            raise ValueError("approved quantity must be positive")

        is_entry = intent.action in {PolicyAction.ENTER_LONG, PolicyAction.ENTER_SHORT}
        if is_entry and self.position.side != Side.FLAT:
            raise ValueError("cannot enter over an existing position")
        if not is_entry and self.position.side == Side.FLAT:
            raise ValueError("cannot exit a flat position")
        if not is_entry and intent.side != self.position.side:
            raise ValueError("exit/reduce side must match the open position")

        order_side = intent.side if is_entry else (Side.SHORT if self.position.side == Side.LONG else Side.LONG)
        reference = environment.price.last or intent.reference_price
        fill_price, slip = self._fill_price(reference, order_side)
        fee = quantity * fill_price * self.settings.costs.fee_bps_per_side * self.settings.costs.fee_multiplier / 10_000
        self._cumulative_fees += fee
        self._cumulative_slippage += quantity * slip
        self._last_trade = None

        if is_entry:
            self.position = PositionState(
                side=intent.side, quantity=quantity, entry_price=fill_price,
                current_stop=intent.stop, current_target=intent.target,
                strategy_id=intent.strategy_id, opened_at=intent.created_at,
            )
            self._entry = {
                "strategy_id": intent.strategy_id,
                "strategy_version": intent.strategy_version,
                "entry_decision_id": intent.decision_id,
                "opened_at": intent.created_at,
                "entry_price": fill_price,
                "quantity": quantity,
            }
        else:
            position_side = self.position.side
            entry = self._entry or {}
            close_quantity = min(quantity, self.position.quantity)
            position_sign = 1 if position_side == Side.LONG else -1
            gross = position_sign * close_quantity * (fill_price - self.position.entry_price)
            net = gross - fee
            remaining = self.position.quantity - close_quantity
            realized = self.position.realized_pnl + net
            if remaining <= 1e-12:
                self.position = PositionState(realized_pnl=realized)
            else:
                self.position = self.position.model_copy(update={"quantity": remaining, "realized_pnl": realized})
            self._last_trade = TradeRecord(
                trade_id=str(uuid4()), strategy_id=position_side.value,
                strategy_version=entry.get("strategy_version", "unknown"), side=position_side,
                quantity=close_quantity, entry_price=float(entry.get("entry_price", self.position.entry_price)),
                exit_price=fill_price, opened_at=entry.get("opened_at", intent.created_at), closed_at=environment.timestamp,
                gross_pnl_usd=gross, fees_usd=fee, slippage_usd=close_quantity * slip,
                funding_usd=0.0, net_pnl_usd=net,
                entry_decision_id=entry.get("entry_decision_id", intent.decision_id), exit_decision_id=intent.decision_id,
            )
            if remaining <= 1e-12:
                self._entry = None
        return PaperFill(
            fill_id=str(uuid4()), intent_id=intent.intent_id, decision_id=intent.decision_id,
            execution_timestamp=environment.timestamp, intended_price=reference,
            fill_price=fill_price, quantity=quantity, fee_usd=fee,
            slippage_usd=quantity * slip, status="FILLED", mode="PAPER",
        )

    def mark(self, mark_price: float, timestamp: datetime) -> PositionState:
        if self.position.side != Side.FLAT:
            sign = 1 if self.position.side == Side.LONG else -1
            self.position = self.position.model_copy(update={
                "unrealized_pnl": sign * self.position.quantity * (mark_price - self.position.entry_price),
                "time_in_position_seconds": int((timestamp - (self.position.opened_at or timestamp)).total_seconds()),
            })
        return self.position

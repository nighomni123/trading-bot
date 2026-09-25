"""Paper-only execution adapter with long/short, fees, slippage, and funding."""
from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

from ..config import LiveSettings
from ..risk import ActiveRiskKernel
from ..schemas import (
    AccountState,
    ExecutionIntent,
    ExecutionState,
    MarketEnvironment,
    PaperFill,
    PolicyAction,
    PolicyDecision,
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
        self._open: dict | None = None
        self._last_funding: datetime | None = None

    @property
    def mode(self) -> str:
        return "PAPER"

    def _fill_price(self, reference: float, side: Side) -> tuple[float, float]:
        slip = reference * self.settings.costs.slippage_bps_per_side / 10_000
        if side == Side.LONG:
            return reference + slip, slip
        if side == Side.SHORT:
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
        reference = intent.reference_price
        fill_price, slip = self._fill_price(reference, intent.side)
        fee = quantity * fill_price * self.settings.costs.fee_bps_per_side * self.settings.costs.fee_multiplier / 10_000
        if intent.action in {PolicyAction.ENTER_LONG, PolicyAction.ENTER_SHORT} and self.position.side != Side.FLAT:
            raise ValueError("cannot enter over an existing position")
        if intent.action in {PolicyAction.EXIT, PolicyAction.REDUCE} and self.position.side == Side.FLAT:
            raise ValueError("cannot exit a flat position")
        signed = 1 if intent.side == Side.LONG else -1
        if intent.action in {PolicyAction.ENTER_LONG, PolicyAction.ENTER_SHORT}:
            self.position = PositionState(side=intent.side, quantity=quantity, entry_price=fill_price, current_stop=intent.reference_price * (0.99 if intent.side == Side.LONG else 1.01), current_target=intent.reference_price * (1.01 if intent.side == Side.LONG else 0.99), strategy_id=intent.strategy_id, opened_at=intent.created_at)
            self._open = {"strategy_id": intent.strategy_id, "opened_at": intent.created_at, "entry_decision_id": intent.decision_id, "quantity": quantity}
        else:
            close_qty = min(quantity, self.position.quantity)
            self.position = PositionState(realized_pnl=self.position.realized_pnl)
        return PaperFill(fill_id=str(uuid4()), intent_id=intent.intent_id, decision_id=intent.decision_id, execution_timestamp=environment.timestamp, intended_price=reference, fill_price=fill_price, quantity=quantity, fee_usd=fee, slippage_usd=quantity * slip, status="FILLED", mode="PAPER")

    def mark(self, mark_price: float, timestamp: datetime) -> PositionState:
        if self.position.side != Side.FLAT:
            sign = 1 if self.position.side == Side.LONG else -1
            self.position = self.position.model_copy(update={"unrealized_pnl": sign * self.position.quantity * (mark_price - self.position.entry_price), "time_in_position_seconds": int((timestamp - (self.position.opened_at or timestamp)).total_seconds())})
        return self.position

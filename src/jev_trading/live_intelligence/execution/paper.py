"""Paper-only next-open execution with explicit fees and funding."""
from __future__ import annotations

from datetime import datetime, timedelta
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
    """Full-fill paper executor. Venue partial fills are intentionally unsupported.

    Execution convention: a decision taken at the close of bucket ``t`` is
    filled at the open of the next 1m bucket, which is observable only once that
    bucket has closed. Nothing else may set a fill price.
    """

    version = "paper-next-open-v1"

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
        self._cumulative_funding = 0.0
        self._funding_events_charged = 0
        self._funding_accrued = 0.0
        self._last_funding_charged = 0.0

    @property
    def mode(self) -> str:
        return "PAPER"

    @property
    def funding_model(self) -> str:
        return self.settings.paper.funding_model

    @property
    def last_trade(self) -> TradeRecord | None:
        return self._last_trade

    @property
    def cumulative_fees(self) -> float:
        return self._cumulative_fees

    @property
    def cumulative_slippage(self) -> float:
        return self._cumulative_slippage

    @property
    def cumulative_funding(self) -> float:
        return self._cumulative_funding

    def _fill_price(self, reference: float, order_side: Side) -> tuple[float, float]:
        slip = reference * self.settings.costs.slippage_bps_per_side / 10_000
        if order_side == Side.LONG:
            return reference + slip, slip
        if order_side == Side.SHORT:
            return reference - slip, slip
        return reference, 0.0

    def _slippage_envelope_bps(self, environment: MarketEnvironment) -> float:
        """The worse of the configured cost and the measured market slippage."""
        configured = self.settings.costs.slippage_bps_per_side
        measured = environment.liquidity.estimated_slippage
        return max(configured, (measured * 10_000) if measured is not None else 0.0)

    def _next_open(self, environment: MarketEnvironment, intent: ExecutionIntent) -> float:
        """Return the open of the one-minute bucket that follows the decision."""
        one_minute = environment.timeframes.get("1m")
        if one_minute is None or one_minute.open is None:
            raise ValueError("next-open execution requires a completed 1m bucket")
        if one_minute.bucket_start != intent.created_at:
            raise ValueError(
                "next-open execution requires the 1m bucket that opens at the decision boundary"
            )
        return float(one_minute.open)

    def _accrue_funding(self, mark_price: float, timestamp: datetime) -> float:
        """Charge funding for whole funding intervals the position has spanned."""
        self._last_funding_charged = 0.0
        if self.settings.paper.funding_model != "LIVE":
            return 0.0
        if self.position.side == Side.FLAT or self.position.opened_at is None:
            return 0.0
        interval = timedelta(hours=self.settings.paper.funding_interval_hours)
        elapsed_intervals = int((timestamp - self.position.opened_at) // interval)
        new_intervals = elapsed_intervals - self._funding_events_charged
        if new_intervals <= 0:
            return 0.0
        self._funding_events_charged = elapsed_intervals
        rate = self._entry.get("funding_rate") if self._entry else None
        if rate is None:
            return 0.0
        sign = 1.0 if self.position.side == Side.LONG else -1.0
        funding_cost = sign * self.position.quantity * mark_price * float(rate) * new_intervals
        self._funding_accrued += funding_cost
        self._cumulative_funding += funding_cost
        self._last_funding_charged = funding_cost
        self.position = self.position.model_copy(update={
            "funding_pnl_usd": self.position.funding_pnl_usd - funding_cost,
        })
        return funding_cost

    def execute(self, intent: ExecutionIntent, risk_decision: RiskDecision, environment: MarketEnvironment) -> PaperFill:
        if intent.mode.value != "PAPER" or risk_decision.status != RiskStatus.APPROVED:
            raise ValueError("paper execution requires an approved PAPER intent")
        if intent.decision_id != risk_decision.decision_id:
            raise ValueError("risk decision does not match execution intent")
        if not environment.data_quality.safe_for_trading:
            raise ValueError("paper execution requires safe current data")
        if environment.decision_timestamp < intent.created_at:
            raise ValueError("execution decision time precedes intent")
        if intent.expires_at is not None and environment.decision_timestamp >= intent.expires_at:
            raise ValueError("execution intent expired")
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
        reference = self._next_open(environment, intent)
        # A fill outside the configured envelope is rejected, never capped into a
        # favourable price.
        envelope_bps = self._slippage_envelope_bps(environment)
        if envelope_bps > self.settings.risk.maximum_slippage_bps:
            raise ValueError(
                f"slippage {envelope_bps:.4f}bps exceeds the configured envelope "
                f"{self.settings.risk.maximum_slippage_bps:.4f}bps"
            )
        fill_price, slip = self._fill_price(reference, order_side)
        if is_entry:
            if intent.stop is None or intent.target is None:
                raise ValueError("entry intent requires stop and target")
            if intent.side == Side.LONG and not (intent.stop < fill_price < intent.target):
                raise ValueError("next-open entry price is outside planned barriers")
            if intent.side == Side.SHORT and not (intent.target < fill_price < intent.stop):
                raise ValueError("next-open entry price is outside planned barriers")
        else:
            self._accrue_funding(reference, environment.timestamp)

        fee = quantity * fill_price * self.settings.costs.fee_bps_per_side * self.settings.costs.fee_multiplier / 10_000
        slippage = quantity * slip
        self._cumulative_fees += fee
        self._cumulative_slippage += slippage
        self._last_trade = None
        fill_id = str(uuid4())

        if is_entry:
            self.position = PositionState(
                side=intent.side, quantity=quantity, entry_price=fill_price,
                current_stop=intent.stop, current_target=intent.target,
                strategy_id=intent.strategy_id, strategy_version=intent.strategy_version,
                opened_at=environment.timestamp,
            )
            self._entry = {
                "strategy_id": intent.strategy_id,
                "strategy_version": intent.strategy_version,
                "entry_decision_id": intent.decision_id,
                "opened_at": environment.timestamp,
                "entry_price": fill_price,
                "quantity": quantity,
                "entry_fee": fee,
                "entry_slippage": slippage,
                "fill_id": fill_id,
                "funding_rate": environment.derivatives.funding,
            }
            self._funding_events_charged = 0
            self._funding_accrued = 0.0
        else:
            position_side = self.position.side
            entry = self._entry or {}
            close_quantity = min(quantity, self.position.quantity)
            position_sign = 1 if position_side == Side.LONG else -1
            gross = position_sign * close_quantity * (fill_price - self.position.entry_price)
            entry_quantity = float(entry.get("quantity", self.position.quantity))
            entry_fee = float(entry.get("entry_fee", 0.0)) * close_quantity / entry_quantity
            entry_slippage = float(entry.get("entry_slippage", 0.0)) * close_quantity / entry_quantity
            funding = self._funding_accrued * close_quantity / self.position.quantity
            total_fees = entry_fee + fee
            total_slippage = entry_slippage + close_quantity * slip
            net = gross - total_fees - funding
            remaining = self.position.quantity - close_quantity
            realized = self.position.realized_pnl + net
            remaining_funding = self._funding_accrued - funding
            if remaining <= 1e-12:
                self.position = PositionState(realized_pnl=realized)
                self._entry = None
                self._funding_accrued = 0.0
            else:
                self.position = self.position.model_copy(update={
                    "quantity": remaining,
                    "realized_pnl": realized,
                    "funding_pnl_usd": -remaining_funding,
                })
                entry["quantity"] = remaining
                entry["entry_fee"] = float(entry.get("entry_fee", 0.0)) - entry_fee
                entry["entry_slippage"] = float(entry.get("entry_slippage", 0.0)) - entry_slippage
                self._funding_accrued = remaining_funding
            self._last_trade = TradeRecord(
                trade_id=str(uuid4()), strategy_id=position_side.value,
                strategy_version=entry.get("strategy_version", "unknown"), side=position_side,
                quantity=close_quantity, entry_price=float(entry.get("entry_price", self.position.entry_price)),
                exit_price=fill_price, opened_at=entry.get("opened_at", intent.created_at),
                closed_at=environment.timestamp, gross_pnl_usd=gross, fees_usd=total_fees,
                slippage_usd=total_slippage, funding_usd=funding, net_pnl_usd=net,
                entry_decision_id=entry.get("entry_decision_id", intent.decision_id),
                exit_decision_id=intent.decision_id,
                entry_fill_id=entry.get("fill_id"),
                exit_fill_id=fill_id,
            )
        return PaperFill(
            fill_id=fill_id, intent_id=intent.intent_id, decision_id=intent.decision_id,
            execution_timestamp=environment.timestamp, intended_price=reference,
            fill_price=fill_price, quantity=quantity, fee_usd=fee,
            slippage_usd=slippage, funding_usd=self._last_funding_charged, status="FILLED", mode="PAPER",
            experiment_id=self.settings.experiment_id, symbol=environment.instrument,
            side=order_side, price_source="primary_1m_open",
            bar_timestamp=environment.timeframes["1m"].bucket_start,
            execution_model_version=self.version, funding_model=self.funding_model,
        )

    def to_state(self) -> dict:
        entry = None
        if self._entry is not None:
            entry = dict(self._entry)
            if isinstance(entry.get("opened_at"), datetime):
                entry["opened_at"] = entry["opened_at"].isoformat()
        return {
            "position": self.position.model_dump(mode="json"),
            "entry": entry,
            "last_trade": self._last_trade.model_dump(mode="json") if self._last_trade else None,
            "cumulative_fees": self._cumulative_fees,
            "cumulative_slippage": self._cumulative_slippage,
            "cumulative_funding": self._cumulative_funding,
            "funding_events_charged": self._funding_events_charged,
            "funding_accrued": self._funding_accrued,
        }

    def restore_state(self, state: dict) -> None:
        self.position = PositionState.model_validate(state["position"])
        self._entry = state.get("entry")
        if self._entry is not None and isinstance(self._entry.get("opened_at"), str):
            self._entry["opened_at"] = datetime.fromisoformat(self._entry["opened_at"])
        self._last_trade = TradeRecord.model_validate(state["last_trade"]) if state.get("last_trade") else None
        self._cumulative_fees = float(state.get("cumulative_fees", 0.0))
        self._cumulative_slippage = float(state.get("cumulative_slippage", 0.0))
        self._cumulative_funding = float(state.get("cumulative_funding", 0.0))
        self._funding_events_charged = int(state.get("funding_events_charged", 0))
        self._funding_accrued = float(state.get("funding_accrued", 0.0))

    def mark(self, mark_price: float, timestamp: datetime) -> PositionState:
        self._accrue_funding(mark_price, timestamp)
        if self.position.side != Side.FLAT:
            sign = 1 if self.position.side == Side.LONG else -1
            self.position = self.position.model_copy(update={
                "unrealized_pnl": sign * self.position.quantity * (mark_price - self.position.entry_price),
                "time_in_position_seconds": int((timestamp - (self.position.opened_at or timestamp)).total_seconds()),
            })
        return self.position

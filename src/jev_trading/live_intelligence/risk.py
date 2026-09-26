"""Deterministic risk authority for the active paper loop.

This kernel is separate from the frozen legacy ``RiskKernel`` so historical
research behavior remains reproducible. It enforces the active architecture's
hard limits and can only approve a policy candidate; no intelligence object can
override it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .config import LiveSettings
from .schemas import (
    AccountState,
    ExecutionState,
    MarketEnvironment,
    PolicyAction,
    PolicyDecision,
    PositionState,
    RiskDecision,
    RiskStatus,
    Side,
)


class ActiveRiskKernel:
    version = "active-risk-v1"

    def __init__(self, settings: LiveSettings):
        self.settings = settings
        self.limits = settings.risk
        self._halt_reason: str | None = None
        self._last_orders: list[datetime] = []

    @property
    def halted(self) -> bool:
        return self._halt_reason is not None

    def halt(self, reason: str) -> None:
        self._halt_reason = reason

    def reset_manual(self) -> None:
        self._halt_reason = None
        self._last_orders.clear()

    def _prune_orders(self, now: datetime) -> None:
        cutoff = now - timedelta(seconds=60)
        self._last_orders = [timestamp for timestamp in self._last_orders if timestamp >= cutoff]

    def record_order(self, timestamp: datetime) -> None:
        self._prune_orders(timestamp)
        self._last_orders.append(timestamp)

    def orders_last_minute(self, now: datetime) -> int:
        self._prune_orders(now)
        return len(self._last_orders)

    def evaluate(
        self,
        policy: PolicyDecision,
        environment: MarketEnvironment,
        account: AccountState,
        execution: ExecutionState,
        *,
        position: PositionState,
        now: datetime | None = None,
        decision_id: str | None = None,
    ) -> RiskDecision:
        timestamp = now or environment.decision_timestamp
        ident = decision_id or policy.decision_id
        reasons: list[str] = []
        # Kill switches stop new risk; they never trap an open position.
        kill_reasons: list[str] = []
        self._prune_orders(timestamp)
        if self.limits.kill_switch:
            kill_reasons.append("configured_kill_switch")
        if self._halt_reason:
            kill_reasons.append(f"kill_switch:{self._halt_reason}")
        if account.kill_switch:
            kill_reasons.append("account_kill_switch")
        if execution.mode.value != "PAPER":
            reasons.append("execution_mode_not_paper")
        if not environment.data_quality.safe_for_trading:
            reasons.append("data_unsafe")
        if environment.data_quality.stale:
            reasons.append("stale_data")
        if execution.feed_healthy is False:
            reasons.append("feed_unhealthy")
        if policy.action == PolicyAction.DATA_UNSAFE:
            reasons.append("policy_data_unsafe")
        if policy.action == PolicyAction.NO_TRADE:
            reasons.append("no_trade")
        if account.daily_realized_pnl_usd <= -self.limits.maximum_daily_loss_usd:
            reasons.append("daily_loss_limit")
        if account.peak_equity_usd is not None:
            drawdown_pct = max(0.0, (account.peak_equity_usd - account.capital_usd) / account.peak_equity_usd * 100)
            if drawdown_pct > self.limits.maximum_drawdown_pct:
                reasons.append("maximum_drawdown")
        actual_orders = self.orders_last_minute(timestamp)
        if max(actual_orders, execution.orders_last_minute) > self.limits.maximum_orders_per_minute:
            reasons.append("order_rate_limit")
        if execution.cooldown_until and timestamp < execution.cooldown_until:
            reasons.append("cooldown")
        if account.open_positions > self.limits.maximum_open_positions:
            reasons.append("open_position_limit")
        if account.correlated_exposure_usd > self.limits.maximum_correlated_exposure_usd:
            reasons.append("correlated_exposure_limit")
        if environment.liquidity.spread is not None and environment.liquidity.spread * 10_000 > self.limits.maximum_spread_bps:
            reasons.append("spread_limit")
        estimated_slippage_bps = (
            environment.liquidity.estimated_slippage * 10_000
            if environment.liquidity.estimated_slippage is not None
            else self.settings.costs.slippage_bps_per_side
        )
        if estimated_slippage_bps > self.limits.maximum_slippage_bps:
            reasons.append("slippage_limit")
        if environment.liquidity.top_level_notional is None:
            reasons.append("missing_liquidity_data")
        elif environment.liquidity.top_level_notional < self.limits.minimum_liquidity_notional:
            reasons.append("minimum_liquidity")

        if policy.action in {PolicyAction.EXIT, PolicyAction.REDUCE}:
            if position.side == Side.FLAT:
                reasons.append("no_open_position")
            else:
                approved_quantity = position.quantity if policy.action == PolicyAction.EXIT else position.quantity / 2
                if approved_quantity <= 0:
                    reasons.append("exit_size_zero")
                elif not reasons:
                    # Reducing risk stays permitted under a kill switch; the
                    # switch is recorded so the run is auditable.
                    return RiskDecision(
                        decision_id=ident, timestamp=timestamp, status=RiskStatus.APPROVED,
                        approved_quantity=approved_quantity,
                        approved_notional=approved_quantity * (environment.price.last or position.entry_price),
                        maximum_loss_usd=0.0,
                        reasons=("risk_reducing_exit_approved", *kill_reasons),
                        risk_config_version=self.version,
                    )

        if policy.action in {PolicyAction.ENTER_LONG, PolicyAction.ENTER_SHORT} and policy.candidate is not None:
            entry_reasons = reasons + kill_reasons
            entry = policy.candidate.entry_reference
            stop_distance = abs(entry - policy.candidate.stop)
            if stop_distance <= 0:
                entry_reasons.append("invalid_stop_distance")
            notional_cap = min(self.limits.maximum_position_notional_usd, account.capital_usd * self.limits.maximum_leverage)
            risk_cap = min(self.limits.maximum_capital_at_risk_usd, account.capital_usd * self.limits.risk_per_trade_pct / 100)
            round_trip_cost = entry * (
                2 * self.settings.costs.fee_bps_per_side * self.settings.costs.fee_multiplier
                + 2 * self.settings.costs.slippage_bps_per_side
                + self.settings.costs.latency_bps
            ) / 10_000
            side = 1.0 if policy.candidate.side == Side.LONG else -1.0
            adverse_funding = max(0.0, side * (environment.derivatives.funding or 0.0))
            funding_cost = entry * adverse_funding * policy.candidate.max_holding_seconds / (8 * 60 * 60)
            loss_per_unit = stop_distance + round_trip_cost + funding_cost
            if loss_per_unit > 0:
                quantity = min(
                    risk_cap / loss_per_unit,
                    self.limits.maximum_trade_loss_usd / loss_per_unit,
                    notional_cap / entry,
                )
                maximum_loss = quantity * loss_per_unit
                if quantity <= 0:
                    entry_reasons.append("risk_size_zero")
                if maximum_loss > self.limits.maximum_trade_loss_usd + 1e-9:
                    entry_reasons.append("trade_loss_limit")
                if not entry_reasons:
                    return RiskDecision(
                        decision_id=ident, timestamp=timestamp, status=RiskStatus.APPROVED,
                        approved_quantity=quantity, approved_notional=quantity * entry,
                        maximum_loss_usd=maximum_loss, reasons=("approved",), risk_config_version=self.version,
                    )
        return self._reject(ident, timestamp, tuple(reasons + kill_reasons or ("no_execution_authority",)))

    def _reject(self, decision_id: str, timestamp: datetime, reasons: tuple[str, ...]) -> RiskDecision:
        return RiskDecision(decision_id=decision_id, timestamp=timestamp, status=RiskStatus.REJECTED, reasons=reasons, risk_config_version=self.version)

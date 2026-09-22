"""Deterministic risk kernel: absolute authority over every proposal; no model can override.

Check order (first failure wins): latched halt -> stale feed -> NO_ACTION allow ->
spread -> daily loss -> order-rate -> EXIT/REDUCE allow -> ENTER position/leverage/size.
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from jev_trading.contracts import Action


class Proposal(BaseModel):
    action: Action
    confidence: float
    suggested_size_btc: float = Field(default=0.0, ge=0.0)


class Portfolio(BaseModel):
    position_btc: float
    capital_usd: float
    daily_pnl_usd: float
    open_orders: int


class MarketCheck(BaseModel):
    spread_bps: float
    last_update_ms: int
    now_ms: int


class RiskDecision(BaseModel):
    allowed: bool
    reason: str
    approved_size_btc: float
    halted: bool


class RiskConfig(BaseModel):
    max_position_btc: float
    max_leverage: float
    daily_loss_limit_usd: float
    trade_loss_limit_usd: float
    max_spread_bps: float
    min_depth_usd: float
    stale_data_ms: int
    max_orders_per_min: int
    max_open_positions: int
    risk_per_trade_pct: float


class RiskKernel:
    """Deterministic gate with absolute authority — rejects even confidence=0.99 proposals.

    ponytail: halt/approval state is in-memory, lost on restart — acceptable for MVP
    paper trading; the persistent ceiling needs a small sqlite file for halt state.
    """

    def __init__(self, config_path: str = "configs/risk.json") -> None:
        path = Path(config_path)
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[3] / path
        self.cfg = RiskConfig.model_validate(json.loads(path.read_text()))
        self._halt_reason: str | None = None
        self._manual_only = False
        self._daily_pnl_usd = 0.0
        self._approvals: list[int] = []

    @property
    def halted(self) -> bool:
        return self._halt_reason is not None

    def register_fill(self, pnl_usd: float) -> None:
        self._daily_pnl_usd += pnl_usd
        if pnl_usd <= -self.cfg.trade_loss_limit_usd:
            self._latch("trade_loss_limit_usd", manual_only=True)
        elif self._daily_pnl_usd <= -self.cfg.daily_loss_limit_usd:
            self._latch("daily_loss_limit_usd")

    def reset_daily(self) -> None:
        """New day: clear pnl, order-rate window, and non-trade-loss halts."""
        self._daily_pnl_usd = 0.0
        self._approvals.clear()
        if not self._manual_only:
            self._halt_reason = None

    def reset_manual(self) -> None:
        """Ops reset: clears every halt, including trade_loss_limit_usd."""
        self._daily_pnl_usd = 0.0
        self._approvals.clear()
        self._halt_reason = None
        self._manual_only = False

    def evaluate(
        self,
        proposal: Proposal | dict,
        portfolio: Portfolio | dict,
        market: MarketCheck | dict,
        now_ms: int,
        ref_price_usd: float,
    ) -> RiskDecision:
        p = proposal if isinstance(proposal, Proposal) else Proposal.model_validate(proposal)
        port = portfolio if isinstance(portfolio, Portfolio) else Portfolio.model_validate(portfolio)
        mkt = market if isinstance(market, MarketCheck) else MarketCheck.model_validate(market)
        if ref_price_usd <= 0:
            raise ValueError("ref_price_usd must be positive")
        if self._halt_reason is not None:
            return self._decide(False, f"halted:{self._halt_reason}", 0.0)
        if now_ms - mkt.last_update_ms > self.cfg.stale_data_ms:
            self._latch("stale_data_ms")
            return self._decide(False, "stale_data_ms", 0.0)
        if p.action == Action.NO_ACTION:
            return self._decide(True, "ok", 0.0)
        if mkt.spread_bps > self.cfg.max_spread_bps:
            return self._decide(False, "max_spread_bps", 0.0)
        if port.daily_pnl_usd <= -self.cfg.daily_loss_limit_usd:
            self._latch("daily_loss_limit_usd")
            return self._decide(False, "daily_loss_limit_usd", 0.0)
        self._approvals = [t for t in self._approvals if now_ms - t <= 60_000]
        if len(self._approvals) >= self.cfg.max_orders_per_min:
            return self._decide(False, "max_orders_per_min", 0.0)
        if p.action in (Action.EXIT, Action.REDUCE):
            self._approvals.append(now_ms)
            return self._decide(True, "ok", 0.0)
        pos = port.position_btc
        if (p.action == Action.ENTER_LONG and pos >= self.cfg.max_position_btc) or (
            p.action == Action.ENTER_SHORT and pos <= -self.cfg.max_position_btc
        ):
            return self._decide(False, "max_position_btc", 0.0)
        risk_cap = port.capital_usd * self.cfg.risk_per_trade_pct / 100.0 / ref_price_usd
        room = (
            self.cfg.max_position_btc - pos
            if p.action == Action.ENTER_LONG
            else self.cfg.max_position_btc + pos
        )
        size = max(0.0, min(p.suggested_size_btc, room, risk_cap))
        new_pos = pos + size if p.action == Action.ENTER_LONG else pos - size
        if abs(new_pos) * ref_price_usd > port.capital_usd * self.cfg.max_leverage:
            return self._decide(False, "max_leverage", 0.0)
        self._approvals.append(now_ms)
        return self._decide(True, "ok", size)

    def _latch(self, reason: str, manual_only: bool = False) -> None:
        if self._halt_reason is None or (manual_only and not self._manual_only):
            self._halt_reason = reason
            self._manual_only = manual_only

    def _decide(self, allowed: bool, reason: str, approved_size_btc: float) -> RiskDecision:
        return RiskDecision(
            allowed=allowed,
            reason=reason,
            approved_size_btc=approved_size_btc,
            halted=self.halted,
        )

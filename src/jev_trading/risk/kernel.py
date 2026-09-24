"""Deterministic risk kernel: absolute authority over every proposal; no model can override.

Check order (first failure wins): latched halt -> stale feed -> NO_ACTION allow ->
spread -> daily loss -> order-rate -> EXIT/REDUCE allow -> ENTER position/leverage/size.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from jev_trading.contracts import Action


class Proposal(BaseModel):
    action: Action
    confidence: float = Field(ge=0.0, le=1.0)
    suggested_size_btc: float = Field(default=0.0, ge=0.0)


class Portfolio(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    position_btc: float
    capital_usd: float = Field(gt=0.0)
    daily_pnl_usd: float
    open_orders: int = Field(ge=0)


class MarketCheck(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    spread_bps: float = Field(ge=0.0)
    last_update_ms: int
    now_ms: int


class RiskDecision(BaseModel):
    allowed: bool
    reason: str
    approved_size_btc: float = Field(ge=0.0)
    halted: bool


class RiskConfig(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    """Every field here is enforced in evaluate(); no aspirational keys.

    ponytail: depth/position-count limits were removed — unenforceable without
    book data / PositionState, both of which arrive with P7/P8. Re-add with
    wiring, not as silent config.
    """

    max_position_btc: float = Field(gt=0.0)
    max_leverage: float = Field(gt=0.0)
    daily_loss_limit_usd: float = Field(gt=0.0)
    trade_loss_limit_usd: float = Field(gt=0.0)
    max_spread_bps: float = Field(gt=0.0)
    stale_data_ms: int = Field(gt=0)
    max_orders_per_min: int = Field(gt=0)
    risk_per_trade_pct: float = Field(gt=0.0)
    # Borrowed from freqtrade/plugins/protections (cloned /tmp/oss/freqtrade):
    # - cooldown_ms mirrors CooldownPeriod._cooldown_period: after a closed
    #   trade, lock ENTERs until close_time + stop_duration (per-pair lock).
    #   0 = off (MVP default; set >0 in P7 hostile-cost arms if needed).
    # - max_drawdown_pct mirrors MaxDrawdownProtection._max_drawdown
    #   (equity mode): halt when peak-to-current equity drawdown exceeds X%
    #   of capital. 0 = off. Rejected alternative: freqtrade's legacy
    #   ratios mode (cumulative close_profit drop) — equity mode is the
    #   edge-case-correct one for a USD-capital paper account.
    cooldown_ms: int = Field(default=0, ge=0)
    max_drawdown_pct: float = Field(default=0.0, ge=0.0, le=100.0)


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
        self._cooldown_until_ms = 0  # freqtrade CooldownPeriod: ENTER lock after an EXIT
        self._peak_pnl_usd = 0.0  # freqtrade MaxDrawdown (equity mode): peak daily pnl

    @property
    def halted(self) -> bool:
        return self._halt_reason is not None

    def register_fill(self, pnl_usd: float, close_time_ms: int | None = None) -> None:
        """Record a fill and optionally arm cooldown at the actual close time."""
        if not isinstance(pnl_usd, (int, float)) or not math.isfinite(pnl_usd):
            raise ValueError("pnl_usd must be finite")
        self._daily_pnl_usd += pnl_usd
        self._peak_pnl_usd = max(self._peak_pnl_usd, self._daily_pnl_usd)
        if pnl_usd <= -self.cfg.trade_loss_limit_usd:
            self._latch("trade_loss_limit_usd", manual_only=True)
        elif self._daily_pnl_usd <= -self.cfg.daily_loss_limit_usd:
            self._latch("daily_loss_limit_usd")
        if close_time_ms is not None and self.cfg.cooldown_ms > 0:
            self._cooldown_until_ms = max(
                self._cooldown_until_ms, close_time_ms + self.cfg.cooldown_ms
            )

    def reset_daily(self) -> None:
        """New day: clear pnl, order-rate window, and non-trade-loss halts."""
        self._daily_pnl_usd = 0.0
        self._peak_pnl_usd = 0.0
        self._approvals.clear()
        if not self._manual_only:
            self._halt_reason = None

    def reset_manual(self) -> None:
        """Ops reset: clears every halt, including trade_loss_limit_usd."""
        self._daily_pnl_usd = 0.0
        self._peak_pnl_usd = 0.0
        self._approvals.clear()
        self._halt_reason = None
        self._manual_only = False
        self._cooldown_until_ms = 0

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
        if not isinstance(ref_price_usd, (int, float)) or not math.isfinite(ref_price_usd) or ref_price_usd <= 0:
            raise ValueError("ref_price_usd must be positive and finite")
        if self._halt_reason is not None:
            return self._decide(False, f"halted:{self._halt_reason}", 0.0)
        if now_ms - mkt.last_update_ms > self.cfg.stale_data_ms:
            self._latch("stale_data_ms")
            return self._decide(False, "stale_data_ms", 0.0)
        if p.action == Action.NO_ACTION:
            return self._decide(True, "ok", 0.0)
        if p.action in (Action.ENTER_LONG, Action.ENTER_SHORT) and port.open_orders > 0:
            return self._decide(False, "open_orders", 0.0)
        if mkt.spread_bps > self.cfg.max_spread_bps:
            return self._decide(False, "max_spread_bps", 0.0)
        if port.daily_pnl_usd <= -self.cfg.daily_loss_limit_usd:
            self._latch("daily_loss_limit_usd")
            return self._decide(False, "daily_loss_limit_usd", 0.0)
        # Borrowed from freqtrade MaxDrawdownProtection (equity mode,
        # max_drawdown_protection.py::_max_drawdown): halt when the
        # peak-to-current drawdown exceeds max_allowed_drawdown. Ours is
        # per-day (peak tracked in register_fill/reset_daily) as % of the
        # portfolio capital passed in — exact denominator, unlike the
        # fill-time path which lacks capital context.
        if self.cfg.max_drawdown_pct > 0 and port.capital_usd > 0:
            self._peak_pnl_usd = max(self._peak_pnl_usd, port.daily_pnl_usd)
            dd_pct = (self._peak_pnl_usd - port.daily_pnl_usd) / port.capital_usd * 100.0
            if dd_pct > self.cfg.max_drawdown_pct:
                self._latch("max_drawdown_pct")
                return self._decide(False, "max_drawdown_pct", 0.0)
        # Borrowed from freqtrade CooldownPeriod._cooldown_period
        # (cooldown_period.py): after a closed trade, lock new ENTERs until
        # close_time + stop_duration. Ours arms on every approved EXIT.
        if p.action in (Action.ENTER_LONG, Action.ENTER_SHORT) and now_ms < self._cooldown_until_ms:
            return self._decide(False, "cooldown_ms", 0.0)
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

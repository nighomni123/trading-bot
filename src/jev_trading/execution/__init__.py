"""Paper execution engine for P8 shadow trading — zero real money.

Accepts risk-approved actions, fills at next-open (buffered, lookahead-free per
docs/execution-semantics.md: features from bar t -> decision at close[t] ->
execution at open[t+1]), tracks paper PnL, and writes replayable JSONL using the
same seq/entry_hash schema as backtest/simulator.py.

Single position, full exits only — mirrors simulator.P7 MVP constraints.
Upgrade path: PositionState-aware policy exits, partial fills, short arm.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from jev_trading.contracts import Action
from jev_trading.risk.kernel import RiskDecision, RiskKernel

DAY_MS = 86_400_000


@dataclass
class PositionState:
    """Paper position snapshot — same shape as backtest.simulator.PositionState."""

    side: int  # +1 long, -1 short, 0 flat
    quantity: float
    entry_price: float
    unrealized_pnl: float
    realized_pnl: float
    time_in_position: int


@dataclass
class PaperCosts:
    """Round-trip costs (per side unless noted). Mirrors configs/costs.json."""

    taker_fee_pct: float = 0.05
    slippage_pct: float = 0.02
    fee_mult: float = 1.0  # P7 hostile-cost gate: 2-3x


def _fill_price(open_next: float, side: int, costs: PaperCosts) -> tuple[float, float]:
    """(fill price, slippage cost per BTC). Buys fill higher, sells lower."""
    slip = open_next * costs.slippage_pct / 100
    return (open_next + slip, slip) if side > 0 else (open_next - slip, slip)


def _fee(notional: float, costs: PaperCosts) -> float:
    return abs(notional) * costs.taker_fee_pct / 100 * costs.fee_mult


def _canonical_json(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _entry_hash(entry: dict) -> str:
    content = {k: v for k, v in entry.items() if k != "entry_hash"}
    return hashlib.sha256(_canonical_json(content).encode()).hexdigest()


class PaperTrader:
    """Live paper trading engine: execution state + JSONL logging.

    The caller (shadow trader loop) is responsible for the decision pipeline
    (state -> quant -> jev -> policy -> risk). This class only handles:
    - next-open fill simulation (buffered, no same-bar lookahead)
    - position / realized-unrealized PnL tracking
    - funding accrual
    - JSONL event logging (replayable via backtest.simulator.verify_log)
    """

    def __init__(
        self,
        capital_usd: float = 10_000.0,
        risk: RiskKernel | None = None,
        costs: PaperCosts | None = None,
        size_btc: float = 0.01,
    ) -> None:
        self._capital = capital_usd
        self._risk = risk or RiskKernel()
        self._costs = costs or PaperCosts()
        self._size_btc = size_btc
        self._pos = PositionState(0, 0.0, 0.0, 0.0, 0.0, 0)
        self._cum_fee = 0.0
        self._cum_fund = 0.0
        self._prev_fund_rate: float | None = None
        self._daily_pnl = 0.0  # realized (gross - fee) per UTC calendar day; feeds risk daily loss limit
        self._prev_ts: int | None = None  # for day-change detection
        self._pending: tuple[Action, float] | None = None
        self._entries = 0
        self._exits = 0
        self._wins = 0
        self._seq = 0
        self._log_handle = None

    # -- log management -------------------------------------------------

    def open_log(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self._log_path = p
        self._log_handle = open(p, "a")
        if p.stat().st_size == 0:
            self._write_header()

    def _write_header(self) -> None:
        self._log_handle.write(json.dumps({
            "type": "header",
            "schema_version": 2,
            "arm": "shadow_paper",
            "capital_usd": self._capital,
            "size_btc": self._size_btc,
        }) + "\n")
        self._log_handle.flush()

    def close_log(self) -> None:
        if self._log_handle:
            self._log_handle.close()
            self._log_handle = None

    # -- state accessors ------------------------------------------------

    @property
    def position(self) -> PositionState:
        return self._pos

    @property
    def capital(self) -> float:
        return self._capital

    @property
    def cum_fee(self) -> float:
        return self._cum_fee

    @property
    def cum_fund(self) -> float:
        return self._cum_fund

    @property
    def daily_pnl(self) -> float:
        return self._daily_pnl

    @property
    def risk(self) -> RiskKernel:
        return self._risk

    def equity(self, mark_price: float) -> float:
        upnl = self._pos.side * self._pos.quantity * (mark_price - self._pos.entry_price) if self._pos.side else 0.0
        return self._capital + self._pos.realized_pnl + upnl - self._cum_fee - self._cum_fund

    @property
    def metrics(self) -> dict:
        net = self.equity(self._pos.entry_price if self._pos.side else 0.0) - self._capital
        return {
            "capital_usd": self._capital,
            "equity": round(self.equity(self._pos.entry_price if self._pos.side else 0.0), 4),
            "net_pnl": round(net, 4),
            "return_pct": round(100 * net / self._capital, 4),
            "n_entries": self._entries,
            "n_exits": self._exits,
            "win_rate": self._wins / self._exits if self._exits else None,
            "cum_fee": round(self._cum_fee, 4),
            "cum_fund": round(self._cum_fund, 4),
            "position": asdict(self._pos),
            "pending": self._pending[0].value if self._pending else None,
            "halted": self._risk.halted,
        }

    # -- core bar processing ---------------------------------------------

    def on_bar(
        self,
        bar: dict,
        *,
        p_up: float = 0.0,
        jev: dict | None = None,
        proposal=None,
        risk_decision: RiskDecision | None = None,
        state_hash: str | None = None,
        latency_ms: float = 0.0,
    ) -> dict:
        """Process one bar.

        1. Execute any buffered decision at this bar's open (next-open fill).
        2. Update funding + unrealized PnL.
        3. Buffer the new decision for the NEXT bar's open.
        4. Write JSONL record.

        ``proposal`` and ``risk_decision`` come from the caller's policy+risk
        evaluation. If both are absent the bar is logged as a no-op pass-through.
        """
        ts = int(bar["timestamp"])
        open_px = float(bar["open"])
        close_px = float(bar["close"])
        fund = float(bar.get("funding_rate", 0.0) or 0.0)
        jev = jev or {}

        # --- day-change: reset daily PnL + risk kernel ----
        if self._prev_ts is not None and ts // DAY_MS != self._prev_ts // DAY_MS:
            self._daily_pnl = 0.0
            self._risk.reset_daily()
        self._prev_ts = ts

        # --- Step 1: execute pending decision at this bar's open ----------
        exec_action = Action.NO_ACTION
        exec_ts: int | None = None
        fill_px = 0.0
        fill_qty = 0.0
        fee = 0.0

        if self._pending is not None:
            action, size_btc = self._pending
            exec_ts = ts  # fills at this bar's open
            exec_action = action
            costs = self._costs

            if action == Action.ENTER_LONG:
                fill_px, _ = _fill_price(open_px, 1, costs)
                fill_qty = size_btc
                fee = _fee(fill_qty * fill_px, costs)
                self._pos = PositionState(1, fill_qty, fill_px, 0.0, self._pos.realized_pnl, 0)
                self._entries += 1
                self._cum_fee += fee
            elif action == Action.EXIT and self._pos.side != 0:
                side = self._pos.side
                fill_px, _ = _fill_price(open_px, -side, costs)
                fill_qty = self._pos.quantity
                gross = side * fill_qty * (fill_px - self._pos.entry_price)
                fee = _fee(fill_qty * fill_px, costs)
                self._wins += int(gross > 0)
                self._pos = PositionState(0, 0.0, 0.0, 0.0, self._pos.realized_pnl + gross, 0)
                self._cum_fee += fee
                self._daily_pnl += gross - fee
                self._exits += 1
                self._risk.register_fill(gross - fee, ts)

            self._pending = None

        # --- Step 2: funding accrual while holding -----------------------
        if self._prev_fund_rate is not None and fund != self._prev_fund_rate and self._pos.side:
            delta_rate = fund - self._prev_fund_rate
            self._cum_fund += -self._pos.side * self._pos.quantity * close_px * delta_rate
        if fund != 0.0 or self._pos.side:
            self._prev_fund_rate = fund

        # --- Step 3: unrealized PnL -------------------------------------
        if self._pos.side:
            self._pos.time_in_position += 1
            self._pos.unrealized_pnl = self._pos.side * self._pos.quantity * (close_px - self._pos.entry_price)

        # --- Step 4: buffer new decision for next bar -------------------
        decision_proposal = proposal
        decision_action = decision_proposal.action if decision_proposal else Action.NO_ACTION
        risk_allowed = risk_decision.allowed if risk_decision else True
        risk_reason = risk_decision.reason if risk_decision else "skipped"
        approved_size = risk_decision.approved_size_btc if risk_decision else 0.0

        if risk_allowed and decision_proposal is not None:
            if decision_action == Action.ENTER_LONG and self._pos.side == 0:
                self._pending = (Action.ENTER_LONG, approved_size)
            elif decision_action == Action.EXIT and self._pos.side != 0:
                self._pending = (Action.EXIT, 0.0)
            # ENTER_SHORT / REDUCE not yet wired (no p_dn producer — see P5 gap)

        eq = self.equity(close_px)

        record = {
            "seq": self._seq + 1,
            "ts": ts,
            "exec_ts": exec_ts,
            "latency_ms": round(latency_ms, 1),
            "state_hash": state_hash,
            "p_up": round(float(p_up), 6),
            **{k: round(float(v), 6) for k, v in jev.items()},
            "decision": decision_action.value,
            "executed": exec_action.value,
            "fill_px": round(fill_px, 4),
            "fill_qty": fill_qty,
            "fee": round(fee, 6),
            "fund_rate": fund,
            "pos": asdict(self._pos),
            "cum_fee": round(self._cum_fee, 4),
            "cum_fund": round(self._cum_fund, 4),
            "equity": round(eq, 4),
            "risk_allowed": risk_allowed,
            "risk_reason": risk_reason,
            "pending": self._pending[0].value if self._pending else None,
        }
        record["entry_hash"] = _entry_hash(record)
        self._seq += 1
        self._write_record(record)
        return record

    def _write_record(self, record: dict) -> None:
        if self._log_handle:
            self._log_handle.write(json.dumps(record) + "\n")
            self._log_handle.flush()

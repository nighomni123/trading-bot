"""Small live-paper contract checks for risk, next-open fills, and replay."""
from __future__ import annotations

from pathlib import Path

import pytest

from jev_trading.backtest.simulator import verify_log
from jev_trading.contracts import Action
from jev_trading.execution import PaperTrader
from jev_trading.policy.engine import decide
from jev_trading.risk.kernel import Proposal, RiskKernel

T0 = 1_700_000_000_000
MINUTE = 60_000


def _risk(risk: RiskKernel, trader: PaperTrader, proposal, ts: int, price: float):
    portfolio = {
        "position_btc": trader.position.side * trader.position.quantity,
        "capital_usd": trader.capital,
        "daily_pnl_usd": trader.daily_pnl,
        "open_orders": 0,
    }
    return risk.evaluate(
        proposal,
        portfolio,
        {"spread_bps": 1.0, "last_update_ms": ts, "now_ms": ts},
        ts,
        price,
    )


def test_paper_requires_risk_decision_for_non_no_action():
    trader = PaperTrader()
    proposal = Proposal(action=Action.ENTER_LONG, confidence=0.9, suggested_size_btc=0.01)
    with pytest.raises(ValueError, match="RiskDecision"):
        trader.on_bar(
            {"timestamp": T0, "open": 100.0, "close": 100.0, "funding_rate": 0.0},
            proposal=proposal,
        )


def test_paper_entry_exit_is_next_open_and_replayable(tmp_path: Path):
    risk = RiskKernel()
    trader = PaperTrader(risk=risk, size_btc=0.01)
    path = tmp_path / "shadow.jsonl"
    trader.open_log(path)
    records = []
    for i, (open_px, close_px, p_up) in enumerate(
        [(100.0, 100.0, 0.8), (100.5, 100.5, 0.1), (101.0, 101.0, 0.1), (101.5, 101.5, 0.1)]
    ):
        ts = T0 + i * MINUTE
        proposal = decide(
            {"p_up_15": p_up, "p_dn_15": 1 - p_up, "expected_return_15": 0.003},
            {"trade_ok": 0.9, "failure_regime": 0.1},
            position_side=trader.position.side,
        ).model_copy(update={"suggested_size_btc": 0.01})
        decision = _risk(risk, trader, proposal, ts, close_px)
        records.append(
            trader.on_bar(
                {"timestamp": ts, "open": open_px, "close": close_px, "funding_rate": 0.0},
                p_up=p_up,
                proposal=proposal,
                risk_decision=decision,
            )
        )
    trader.close_log()

    assert records[0]["decision"] == "ENTER_LONG"
    assert records[0]["exec_ts"] == T0 + MINUTE
    assert records[1]["executed"] == "ENTER_LONG"
    assert records[1]["fill_decision_ts"] == T0
    assert records[1]["fill_px"] == pytest.approx(100.5 * 1.0002)
    assert records[1]["fill_qty"] > 0
    assert records[2]["decision"] == "EXIT"
    assert records[3]["executed"] == "EXIT"
    assert trader.position.side == 0
    assert trader.metrics["n_entries"] == 1
    assert trader.metrics["n_exits"] == 1
    assert verify_log(path) == records
    assert all(r["exec_ts"] is None or r["exec_ts"] > r["ts"] for r in records)



def test_exit_bar_funding_uses_position_held_before_fill(tmp_path: Path):
    risk = RiskKernel()
    trader = PaperTrader(risk=risk, size_btc=0.01)
    for i, (open_px, close_px, p_up, funding) in enumerate(
        [(100.0, 100.0, 0.8, 0.0), (100.5, 100.5, 0.1, 0.0), (101.0, 101.0, 0.1, 0.0), (101.5, 101.5, 0.1, 0.001)]
    ):
        ts = T0 + i * MINUTE
        proposal = decide(
            {"p_up_15": p_up, "p_dn_15": 1 - p_up, "expected_return_15": 0.003},
            {"trade_ok": 0.9, "failure_regime": 0.1},
            position_side=trader.position.side,
        ).model_copy(update={"suggested_size_btc": 0.01})
        decision = _risk(risk, trader, proposal, ts, close_px)
        trader.on_bar(
            {"timestamp": ts, "open": open_px, "close": close_px, "funding_rate": funding},
            p_up=p_up,
            proposal=proposal,
            risk_decision=decision,
        )
    assert trader.cum_fund < 0


def test_empty_shadow_log_is_not_verified(tmp_path: Path):
    trader = PaperTrader()
    path = tmp_path / "empty.jsonl"
    trader.open_log(path)
    trader.close_log()
    with pytest.raises(ValueError, match="no records"):
        verify_log(path)

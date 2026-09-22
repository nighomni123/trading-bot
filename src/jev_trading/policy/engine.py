"""Plain-threshold policy: quant probs + jev answers -> PolicyProposal for risk.

PolicyProposal subclasses risk's Proposal (adds `reasons: list[str]`), so it
passes isinstance checks and goes straight into RiskKernel.evaluate().
suggested_size_btc stays 0.0 — sizing is risk's job. confidence =
min(p_up_15, trade_ok) for ENTER_LONG, min(p_dn_15, trade_ok) for ENTER_SHORT,
0.0 for NO_ACTION.

Edge check runs only when quant provides expected_return_15 AND
configs/costs.json exists with round_trip_cost (fraction of price per round
trip); otherwise it is skipped. Missing quant probs default to 0.0 and missing
jev answers to neutral 0.5 — both fail the entry gates (safe).

ponytail: cfg["exit"] thresholds are unused here — decide() has no position
context, so EXIT/REDUCE belong to the position-aware loop; upgrade path: pass
open position into decide().
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import Field

from jev_trading.contracts import Action
from jev_trading.risk.kernel import Proposal


class PolicyProposal(Proposal):
    """risk.Proposal + human-readable audit trail (every checked threshold, pass/fail)."""

    reasons: list[str] = Field(default_factory=list)


def load_config(path: str = "configs/policy.json") -> dict:
    p = Path(path)
    if not p.is_absolute():
        p = Path(__file__).resolve().parents[3] / p
    return json.loads(p.read_text())


def _round_trip_cost() -> float | None:
    p = Path(__file__).resolve().parents[3] / "configs/costs.json"
    if not p.is_file():
        return None
    v = json.loads(p.read_text()).get("round_trip_cost")
    return float(v) if isinstance(v, (int, float)) else None


def _check(reasons: list[str], side: str, label: str, passed: bool) -> bool:
    reasons.append(f"{side}: {label} -> {'pass' if passed else 'fail'}")
    return passed


def _edge(reasons: list[str], side: str, quant: dict, thr: dict, cost: float | None) -> bool:
    exp_ret = quant.get("expected_return_15")
    if exp_ret is None or cost is None:
        return True  # edge check skipped: need both expected return and costs.json
    exp_ret = float(exp_ret)
    needed = thr["min_edge_over_cost"] * cost
    return _check(reasons, side, f"expected_return_15 {exp_ret} >= min_edge {needed}", exp_ret >= needed)


def decide(quant: dict, jev_answers: dict, cfg: dict | None = None) -> PolicyProposal:
    """ENTER_LONG iff p_up_15 >= thr AND trade_ok >= thr AND failure_regime <= max
    (AND edge check when applicable); ENTER_SHORT mirrors with p_dn_15; else
    NO_ACTION. Both sides are always evaluated so `reasons` is a full audit trail;
    long wins on ties. cfg=None loads configs/policy.json.
    """
    if cfg is None:
        cfg = load_config()
    t_long, t_short = cfg["enter_long"], cfg["enter_short"]
    reasons: list[str] = []
    p_up = float(quant.get("p_up_15", 0.0))
    p_dn = float(quant.get("p_dn_15", 0.0))
    trade_ok = float(jev_answers.get("trade_ok", 0.5))
    failure = float(jev_answers.get("failure_regime", 0.5))
    cost = _round_trip_cost()

    ok_long = _check(reasons, "enter_long", f"p_up_15 {p_up} >= {t_long['p_up_15']}", p_up >= t_long["p_up_15"])
    ok_long &= _check(reasons, "enter_long", f"trade_ok {trade_ok} >= {t_long['jev_trade_ok']}", trade_ok >= t_long["jev_trade_ok"])
    ok_long &= _check(reasons, "enter_long", f"failure_regime {failure} <= {t_long['jev_failure_max']}", failure <= t_long["jev_failure_max"])
    ok_long &= _edge(reasons, "enter_long", quant, t_long, cost)

    ok_short = _check(reasons, "enter_short", f"p_dn_15 {p_dn} >= {t_short['p_dn_15']}", p_dn >= t_short["p_dn_15"])
    ok_short &= _check(reasons, "enter_short", f"trade_ok {trade_ok} >= {t_short['jev_trade_ok']}", trade_ok >= t_short["jev_trade_ok"])
    ok_short &= _check(reasons, "enter_short", f"failure_regime {failure} <= {t_short['jev_failure_max']}", failure <= t_short["jev_failure_max"])
    ok_short &= _edge(reasons, "enter_short", quant, t_short, cost)

    if ok_long:
        return PolicyProposal(action=Action.ENTER_LONG, confidence=min(p_up, trade_ok), reasons=reasons + ["ENTER_LONG"])
    if ok_short:
        return PolicyProposal(action=Action.ENTER_SHORT, confidence=min(p_dn, trade_ok), reasons=reasons + ["ENTER_SHORT"])
    return PolicyProposal(action=Action.NO_ACTION, confidence=0.0, reasons=reasons + ["NO_ACTION"])

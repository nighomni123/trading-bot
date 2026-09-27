"""Cross-arm comparison for a forward experiment.

The question this answers is not "which arm traded the most" but "does adding
the model improve forward, after-cost outcomes enough to justify its complexity
and expense". Three things follow from that and are the reason this module
exists:

* Costs are reported gross *and* net, including inference spend, because a
  profitable-looking arm that spent more on tokens than it made is not a result.
* Every arm is stated against cash and buy-and-hold over the same window and the
  same bars, because a long-biased run during a rally is otherwise
  indistinguishable from skill.
* Drawdown is computed from the same equity series the run published per poll,
  not from a trade sequence restarted at zero, so the report cannot disagree with
  the run that produced it.

Nothing here trades, sizes, or consults a provider. It reads ledgers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from jev_trading.ledger import DecisionLedger
from jev_trading.live_intelligence.schemas import PolicyAction, RiskStatus, Side


@dataclass
class ArmReport:
    """Everything measured for one arm over one window."""

    arm: str
    ledger: str
    decisions: int = 0
    abstentions: int = 0
    window: tuple[datetime, datetime] | None = None
    trades: int = 0
    gross_pnl_usd: float = 0.0
    fees_usd: float = 0.0
    slippage_usd: float = 0.0
    funding_usd: float = 0.0
    net_pnl_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    usage_reported: bool = False
    model_cost_usd: float = 0.0
    max_drawdown_usd: float = 0.0
    peak_equity_usd: float = 0.0
    turnover_usd: float = 0.0
    peak_exposure_usd: float = 0.0
    worst_adverse_excursion_fraction: float | None = None
    risk_rejections: int = 0
    provider_failures: int = 0
    unsafe_decisions: int = 0
    missed_orders: int = 0
    fill_models: dict[str, int] = field(default_factory=dict)
    policy_actions: dict[str, int] = field(default_factory=dict)
    proposal_states: dict[str, int] = field(default_factory=dict)
    entry_conditions: dict[str, int] = field(default_factory=dict)
    latency_ms: dict[str, float] = field(default_factory=dict)

    @property
    def net_after_model_usd(self) -> float:
        """The number that decides whether the model earned its cost."""
        return self.net_pnl_usd - self.model_cost_usd

    def as_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "ledger": self.ledger,
            "decisions": self.decisions,
            "abstentions": self.abstentions,
            "window": (
                {"from": self.window[0].isoformat(), "to": self.window[1].isoformat()}
                if self.window else None
            ),
            "trades": self.trades,
            "pnl_usd": {
                "gross": round(self.gross_pnl_usd, 4),
                "fees": round(self.fees_usd, 4),
                "slippage": round(self.slippage_usd, 4),
                "funding": round(self.funding_usd, 4),
                "net": round(self.net_pnl_usd, 4),
                "model_cost": round(self.model_cost_usd, 4),
                "net_after_model": round(self.net_after_model_usd, 4),
            },
            "tokens": {
                "input": self.input_tokens,
                "output": self.output_tokens,
                "reported_by_provider": self.usage_reported,
            },
            "risk": {
                "max_drawdown_usd": round(self.max_drawdown_usd, 4),
                "peak_equity_usd": round(self.peak_equity_usd, 4),
                "turnover_usd": round(self.turnover_usd, 4),
                "peak_exposure_usd": round(self.peak_exposure_usd, 4),
                "worst_adverse_excursion_fraction": self.worst_adverse_excursion_fraction,
            },
            "decision_quality": {
                "policy_actions": self.policy_actions,
                "proposal_states": self.proposal_states,
                "entry_conditions": self.entry_conditions,
                "risk_rejections": self.risk_rejections,
                "abstentions": self.abstentions,
            },
            "execution": {
                "fill_models": self.fill_models,
                "missed_orders": self.missed_orders,
                "provider_failures": self.provider_failures,
                "unsafe_decisions": self.unsafe_decisions,
                "latency_ms": {k: round(v, 2) for k, v in self.latency_ms.items()},
            },
        }


def _bump(counter: dict[str, int], key: str | None) -> None:
    if key:
        counter[key] = counter.get(key, 0) + 1


def _proposal_state(record) -> str:
    """Where a proposal ended up. Mirrors the runner's telemetry label."""
    proposal = record.trade_proposal
    if proposal is None:
        return "none"
    if proposal.abstain:
        return "abstained"
    reasons = set(record.policy_decision.reasons)
    if reasons & {"proposal_expired", "conditional_proposal_expired"}:
        return "expired"
    if record.policy_decision.action in {PolicyAction.ENTER_LONG, PolicyAction.ENTER_SHORT}:
        return (
            "accepted"
            if record.risk_decision.status == RiskStatus.APPROVED
            else "risk_rejected"
        )
    if any(reason.startswith("conditional_entry_triggered") for reason in reasons):
        return "triggered"
    return "rejected"


def summarize_arm(
    arm: str,
    records: Iterable,
    trades: Iterable,
    *,
    ledger_path: str,
    capital_usd: float,
    model_cost_per_mtok_usd: float = 0.0,
) -> ArmReport:
    """Reduce one arm's ledger to a report. Drawdown uses the per-poll equity."""
    report = ArmReport(arm=arm, ledger=str(ledger_path), peak_equity_usd=capital_usd)
    trade_list = list(trades)
    report.trades = len(trade_list)
    for trade in trade_list:
        report.gross_pnl_usd += trade.gross_pnl_usd
        report.fees_usd += trade.fees_usd
        report.slippage_usd += trade.slippage_usd
        report.funding_usd += trade.funding_usd
        report.net_pnl_usd += trade.net_pnl_usd
        report.turnover_usd += abs(trade.quantity) * (
            trade.entry_price + trade.exit_price
        )

    equity = capital_usd
    peak = capital_usd
    stamps: list[datetime] = []
    maes: list[float] = []
    for record in records:
        report.decisions += 1
        stamps.append(record.timestamp)
        action = record.policy_decision.action.value
        _bump(report.policy_actions, action)
        if action == PolicyAction.NO_TRADE:
            report.abstentions += 1
        if record.risk_decision.status == RiskStatus.REJECTED:
            report.risk_rejections += 1
        if not record.market_environment.data_quality.safe_for_trading:
            report.unsafe_decisions += 1
        report.provider_failures += len(record.provider_failures)
        if record.pending_cancellation:
            report.missed_orders += 1
        _bump(report.proposal_states, _proposal_state(record))
        if record.execution_intent is not None:
            _bump(
                report.entry_conditions,
                record.execution_intent.strategy_version or None,
            )
        hypothesis = record.frontier_hypothesis
        if hypothesis is not None and hypothesis.provider is not None:
            report.input_tokens += hypothesis.input_tokens
            report.output_tokens += hypothesis.output_tokens
            report.usage_reported = report.usage_reported or hypothesis.usage_reported
        if record.jev_evaluation is not None:
            report.input_tokens += record.jev_evaluation.input_tokens
            report.output_tokens += record.jev_evaluation.output_tokens
            report.usage_reported = report.usage_reported or record.jev_evaluation.usage_reported
        if record.economic_value is not None:
            mae = record.economic_value.expected_downside
            if mae is not None:
                maes.append(abs(float(mae)))
        for analysis in record.quant_analyses:
            if analysis.expected_mae is not None:
                maes.append(abs(float(analysis.expected_mae)))
        # Equity as the run itself published it, so this cannot drift from the
        # run's own drawdown column.
        state = record.runtime_state or {}
        account = state.get("account") or {}
        position = state.get("position") or {}
        equity_now = account.get("capital_usd")
        if equity_now is None:
            equity_now = equity
        if position.get("side", Side.FLAT) != Side.FLAT and equity_now is not None:
            report.peak_exposure_usd = max(
                report.peak_exposure_usd, abs(float(position.get("quantity") or 0.0))
                * float(record.market_environment.price.last or 0.0),
            )
        equity = float(equity_now)
        peak = max(peak, equity)
        report.max_drawdown_usd = max(report.max_drawdown_usd, peak - equity)
    report.peak_equity_usd = peak
    report.worst_adverse_excursion_fraction = max(maes) if maes else None
    if stamps:
        report.window = (min(stamps), max(stamps))
    report.model_cost_usd = (
        (report.input_tokens + report.output_tokens) / 1_000_000 * model_cost_per_mtok_usd
    )
    return report


def load_arm(
    arm: str,
    ledger_path: str,
    *,
    capital_usd: float,
    model_cost_per_mtok_usd: float = 0.0,
) -> ArmReport:
    ledger = DecisionLedger(ledger_path)
    return summarize_arm(
        arm,
        ledger.records(),
        ledger.trades(),
        ledger_path=ledger_path,
        capital_usd=capital_usd,
        model_cost_per_mtok_usd=model_cost_per_mtok_usd,
    )


def buy_and_hold(
    bars,
    *,
    capital_usd: float,
    start_ms: int | None = None,
    end_ms: int | None = None,
    fee_bps_per_side: float = 5.0,
    slippage_bps_per_side: float = 2.0,
) -> dict[str, Any]:
    """Passive long exposure over the same window, charged the same costs.

    A profitable long-biased run is not evidence of timing until it is compared
    with simply having been long.
    """
    closes = bars["close"].to_list()
    stamps = bars["timestamp"].to_list()
    if not closes:
        return {"return_fraction": 0.0, "net_pnl_usd": 0.0, "entry": None, "exit": None}
    pairs = [
        (stamp, close)
        for stamp, close in zip(stamps, closes)
        if (start_ms is None or stamp >= start_ms) and (end_ms is None or stamp <= end_ms)
    ]
    if len(pairs) < 2:
        return {"return_fraction": 0.0, "net_pnl_usd": 0.0, "entry": None, "exit": None}
    entry, exit_ = pairs[0][1], pairs[-1][1]
    round_trip = 2 * (fee_bps_per_side + slippage_bps_per_side) / 10_000
    net_return = exit_ / entry - 1.0 - round_trip
    return {
        "return_fraction": round(net_return, 6),
        "net_pnl_usd": round(capital_usd * net_return, 4),
        "entry": entry,
        "exit": exit_,
        "round_trip_cost_fraction": round(round_trip, 6),
    }


def compare_arms(
    arms: dict[str, str],
    *,
    bars=None,
    capital_usd: float = 10_000.0,
    model_cost_per_mtok_usd: float = 0.0,
    fee_bps_per_side: float = 5.0,
    slippage_bps_per_side: float = 2.0,
) -> dict[str, Any]:
    """Report every arm on identical data, capital and costs, plus benchmarks."""
    reports = {
        arm: load_arm(
            arm, path, capital_usd=capital_usd,
            model_cost_per_mtok_usd=model_cost_per_mtok_usd,
        )
        for arm, path in arms.items()
    }
    baseline = "A" if "A" in reports else next(iter(reports), None)
    window = reports[baseline].window if baseline else None

    benchmarks: dict[str, Any] = {
        "cash": {"net_pnl_usd": 0.0, "return_fraction": 0.0,
                 "note": "no position, no cost, no edge"},
    }
    if bars is not None:
        benchmarks["buy_and_hold"] = buy_and_hold(
            bars,
            capital_usd=capital_usd,
            start_ms=int(window[0].timestamp() * 1000) if window else None,
            end_ms=int(window[1].timestamp() * 1000) if window else None,
            fee_bps_per_side=fee_bps_per_side,
            slippage_bps_per_side=slippage_bps_per_side,
        )

    incremental: dict[str, Any] = {}
    if baseline is not None:
        base_net = reports[baseline].net_after_model_usd
        for arm, report in reports.items():
            if arm == baseline:
                continue
            incremental[f"{arm}_minus_{baseline}"] = {
                "net_after_model_usd": round(
                    report.net_after_model_usd - base_net, 4
                ),
                "net_pnl_usd": round(report.net_pnl_usd - reports[baseline].net_pnl_usd, 4),
                "model_cost_usd": round(report.model_cost_usd, 4),
                "trades": report.trades - reports[baseline].trades,
            }

    return {
        "capital_usd": capital_usd,
        "model_cost_per_mtok_usd": model_cost_per_mtok_usd,
        "window": (
            {"from": window[0].isoformat(), "to": window[1].isoformat()}
            if window else None
        ),
        "arms": {arm: report.as_dict() for arm, report in reports.items()},
        "benchmarks": benchmarks,
        "incremental": incremental,
        "verdict": verdict(reports, benchmarks, baseline),
    }


def verdict(
    reports: dict[str, ArmReport], benchmarks: dict[str, Any], baseline: str | None
) -> dict[str, Any]:
    """A deliberately blunt reading, stated so it cannot be quoted selectively.

    A profitable arm that beats cash but not passive exposure has not shown
    timing, and an arm that earned less than it spent on inference has not earned
    its complexity. Both are reported as failures of the hypothesis, not wins.
    """
    passive = benchmarks.get("buy_and_hold", {}).get("net_pnl_usd", 0.0)
    out: dict[str, Any] = {"baseline": baseline, "arms": {}}
    for arm, report in reports.items():
        reasons: list[str] = []
        if report.trades < 2:
            reasons.append("too_few_trades_to_infer_anything")
        if report.net_after_model_usd <= 0:
            reasons.append("negative_after_model_cost")
        if passive and report.net_pnl_usd <= passive:
            reasons.append("does_not_beat_passive_exposure")
        if not report.usage_reported:
            reasons.append("model_cost_unmeasured")
        out["arms"][arm] = {
            "net_after_model_usd": round(report.net_after_model_usd, 4),
            "supported": not reasons,
            "reasons": reasons,
        }
    return out


def render_markdown(result: dict[str, Any]) -> str:
    """Human-readable summary of a `compare_arms` result."""
    lines = [
        "# Forward experiment: arm comparison",
        "",
        f"- capital: {result['capital_usd']} USD",
        f"- window: {(result['window'] or {}).get('from')} -> {(result['window'] or {}).get('to')}",
        f"- model cost: {result['model_cost_per_mtok_usd']} USD / Mtok",
        "",
        "| arm | trades | net P&L | model cost | net after model | max DD | tokens |",
        "|---|---|---|---|---|---|---|",
    ]
    for arm, data in result["arms"].items():
        pnl = data["pnl_usd"]
        tokens = data["tokens"]
        lines.append(
            f"| {arm} | {data['trades']} | {pnl['net']:.2f} | {pnl['model_cost']:.2f} | "
            f"{pnl['net_after_model']:.2f} | {data['risk']['max_drawdown_usd']:.2f} | "
            f"{tokens['input'] + tokens['output']} |"
        )
    lines += ["", "## Benchmarks", ""]
    for name, data in result["benchmarks"].items():
        lines.append(f"- **{name}**: {data.get('net_pnl_usd', 0.0):.2f} USD")
    if result["incremental"]:
        lines += ["", "## Incremental vs baseline", ""]
        for name, data in result["incremental"].items():
            lines.append(
                f"- **{name}**: {data['net_after_model_usd']:+.2f} USD after model cost"
            )
    lines += ["", "## Verdict", ""]
    for arm, data in result["verdict"]["arms"].items():
        state = "supported" if data["supported"] else "NOT supported"
        why = "" if data["supported"] else f" ({', '.join(data['reasons'])})"
        lines.append(f"- arm {arm}: **{state}**{why}")
    lines += [
        "",
        "A profitable arm is not evidence of an edge. This report exists to make",
        "that comparison unavoidable, not to declare a result.",
        "",
    ]
    return "\n".join(lines)

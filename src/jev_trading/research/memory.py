"""Structured runtime research memory and derived immutable reports."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..ledger import DecisionLedger
from ..live_intelligence.schemas import DecisionRecord, ResearchHypothesis, ResearchObservation


class ResearchMemory:
    def __init__(self, root: str | Path = "research"):
        self.root = Path(root)
        self.observations_path = self.root / "observations.jsonl"
        self.hypotheses_path = self.root / "hypotheses"
        self.postmortems_path = self.root / "postmortems"
        for path in (self.root / "hourly", self.root / "daily", self.root / "weekly", self.hypotheses_path, self.postmortems_path):
            path.mkdir(parents=True, exist_ok=True)

    def record(self, observation: ResearchObservation) -> None:
        with self.observations_path.open("a", encoding="utf-8") as handle:
            handle.write(observation.model_dump_json() + "\n")

    def record_decision(self, record: DecisionRecord) -> None:
        hypothesis = record.frontier_hypothesis
        self.record(ResearchObservation(
            observation_id=f"decision:{record.decision_id}",
            timestamp=record.timestamp,
            kind="decision",
            decision_id=record.decision_id,
            experiment_id=record.experiment_id,
            data={
                "policy_action": record.policy_decision.action.value,
                "policy_reasons": record.policy_decision.reasons,
                "risk_status": record.risk_decision.status.value,
                "risk_reasons": record.risk_decision.reasons,
                "frontier_strategy": hypothesis.primary_strategy if hypothesis else None,
                "frontier_abstain": hypothesis.abstain if hypothesis else True,
                "frontier_reason": hypothesis.reason if hypothesis else None,
                "jev_state": record.jev_evaluation.recommended_state.value if record.jev_evaluation else None,
                "economic_value": record.economic_value.net_expected_value if record.economic_value else None,
                "regime": record.quant_evidence.regime if record.quant_evidence else None,
                "events": [event.event_type for event in record.market_environment.events],
                "quant_versions": record.versions.quant_analyzers,
                "data_safe": record.market_environment.data_quality.safe_for_trading,
                "provider_failures": [failure.model_dump(mode="json") for failure in record.provider_failures],
            },
            source_versions={
                "frontier_model": record.versions.frontier_model,
                "jev_model": record.versions.jev_model,
                "policy": record.versions.policy,
                "risk": record.versions.risk,
            },
        ))

    def add_hypothesis(self, hypothesis: ResearchHypothesis) -> Path:
        target = self.hypotheses_path / f"{hypothesis.hypothesis_id}.json"
        if target.exists():
            raise FileExistsError(f"hypothesis already exists: {target}")
        target.write_text(hypothesis.model_dump_json(indent=2) + "\n")
        return target

    def frontier_context(self, ledger: DecisionLedger, *, limit: int = 20) -> dict:
        records = ledger.records()[-limit:]
        strategy_pnl: dict[str, float] = defaultdict(float)
        trade_counts: Counter[str] = Counter()
        for trade in ledger.trades():
            strategy_pnl[trade.strategy_id] += trade.net_pnl_usd
            trade_counts[trade.strategy_id] += 1
        return {
            "recent_decisions": len(records),
            "policy_actions": dict(Counter(record.policy_decision.action.value for record in records)),
            "risk_rejections": sum(record.risk_decision.status.value == "REJECTED" for record in records),
            "data_failures": sum(not record.market_environment.data_quality.safe_for_trading for record in records),
            "frontier_abstentions": sum(bool(record.frontier_hypothesis and record.frontier_hypothesis.abstain) for record in records),
            "strategy_performance": {
                strategy: {"trades": trade_counts[strategy], "net_pnl_usd": strategy_pnl[strategy]}
                for strategy in sorted(trade_counts)
            },
        }

    def maybe_generate(
        self, ledger: DecisionLedger, now: datetime, *, hourly: bool = True, daily: bool = True, weekly: bool = True,
    ) -> list[Path]:
        generated: list[Path] = []
        if hourly and now.minute == 0:
            generated.append(self.generate(ledger, "hourly", now))
        if daily and now.hour == 0 and now.minute == 0:
            generated.append(self.generate(ledger, "daily", now))
        if weekly and now.weekday() == 0 and now.hour == 0 and now.minute == 0:
            generated.append(self.generate(ledger, "weekly", now))
        return generated

    def generate(self, ledger: DecisionLedger, period: str, timestamp: datetime | None = None) -> Path:
        if period not in {"hourly", "daily", "weekly"}:
            raise ValueError("period must be hourly, daily, or weekly")
        now = timestamp or datetime.now(tz=timezone.utc)
        cutoff = {
            "hourly": now - timedelta(hours=1),
            "daily": now - timedelta(days=1),
            "weekly": now - timedelta(days=7),
        }[period]
        records = tuple(record for record in ledger.records() if record.timestamp >= cutoff)
        fills = tuple(fill for fill in ledger.fills() if fill.execution_timestamp >= cutoff)
        trades = tuple(trade for trade in ledger.trades() if trade.closed_at >= cutoff)
        actions = Counter(record.policy_decision.action.value for record in records)
        risk_rejections = sum(record.risk_decision.status.value == "REJECTED" for record in records)
        data_failures = sum(not record.market_environment.data_quality.safe_for_trading for record in records)
        regimes = Counter(record.quant_evidence.regime for record in records if record.quant_evidence)
        events = Counter(event.event_type for record in records for event in record.market_environment.events)
        strategies = Counter(record.frontier_hypothesis.primary_strategy for record in records if record.frontier_hypothesis and record.frontier_hypothesis.primary_strategy)
        hypotheses = sorted(path.name for path in self.hypotheses_path.glob("*.json"))
        lines = [
            f"# {period.title()} Research — {now.isoformat()}", "",
            "## Window", f"From: {cutoff.isoformat()}", f"To: {now.isoformat()}", "",
            "## Market Regimes and Events",
            f"Regime counts: {dict(regimes)}",
            f"Event counts: {dict(events)}", "",
            "## Frontier and Strategies",
            f"Strategy hypotheses: {dict(strategies)}",
            f"Hypotheses: {hypotheses or ['none']}", "",
            "## Quant and Jev",
            f"Quant evidence decisions: {sum(record.quant_evidence is not None for record in records)}",
            f"Jev evaluations: {sum(record.jev_evaluation is not None for record in records)}", "",
            "## Decisions and Failures",
            f"Decisions: {len(records)}",
            *[f"- {action}: {count}" for action, count in sorted(actions.items())],
            f"Risk rejections: {risk_rejections}",
            f"Data-quality failures: {data_failures}", "",
            "## Execution and Outcomes",
            f"Paper fills: {len(fills)}",
            f"Closed paper trades: {len(trades)}",
            f"Net realized PnL: {sum(trade.net_pnl_usd for trade in trades):.8f}", "",
            "## Safety",
            "- Reports are derived from structured ledger and observation data.",
            "- Research context is read-only and cannot modify prompts, thresholds, risk, configuration, or source.",
        ]
        directory = self.root / period
        directory.mkdir(parents=True, exist_ok=True)
        stamp = now.strftime("%Y%m%dT%H%M%SZ") if period == "hourly" else now.strftime("%Y%m%d") if period == "daily" else f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"
        target = directory / f"{stamp}.md"
        if target.exists():
            return target
        with target.open("x", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        return target

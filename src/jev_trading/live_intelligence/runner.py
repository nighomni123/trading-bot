"""Small explicit live-shadow orchestrator."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from jev_trading.data.normalization import DataFabric, MarketDataAdapter
from jev_trading.environment import assess_regime, build_market_environment, detect_events
from jev_trading.ledger import DecisionLedger
from jev_trading.live_intelligence.config import LiveSettings
from jev_trading.live_intelligence.frontier.client import FrontierClient, FrontierUnavailable
from jev_trading.live_intelligence.frontier.strategist import FrontierStrategist, load_prompt
from jev_trading.live_intelligence.jev.client import JevClient
from jev_trading.live_intelligence.jev.evaluator import JevEvaluator
from jev_trading.live_intelligence.policy import PolicyFinalizer, make_candidate
from jev_trading.live_intelligence.quant import QuantRegistry
from jev_trading.live_intelligence.risk import ActiveRiskKernel
from jev_trading.live_intelligence.schemas import (
    AccountState,
    DataQuality,
    DecisionRecord,
    ExecutionIntent,
    ExecutionState,
    JevRequest,
    PolicyAction,
    PositionState,
    RiskStatus,
    StrategyHypothesis,
    Versions,
)


class ShadowRunner:
    """One-process paper runner; every external dependency is injectable."""

    def __init__(self, settings: LiveSettings, adapter: MarketDataAdapter, frontier_client: FrontierClient, jev_client: JevClient, *, ledger_path: str | Path = "research/runtime/ledger/decisions.jsonl"):
        self.settings = settings
        self.adapter = adapter
        self.quant = QuantRegistry()
        self.frontier = FrontierStrategist(frontier_client, prompt=load_prompt(Path(__file__).parent / "frontier/prompts/strategist_v1.txt"), prompt_version=settings.frontier.prompt_version)
        self.jev = JevEvaluator(jev_client, max_validity_seconds=settings.jev.validity_seconds, prompt_version=settings.jev.prompt_version)
        self.policy = PolicyFinalizer(settings)
        self.risk = ActiveRiskKernel(settings)
        self.fabric = DataFabric(settings.market.required_source_roles, max_age_ms=settings.risk.stale_data_ms)
        self.ledger = DecisionLedger(ledger_path)
        self.position = PositionState()
        self.account = AccountState(capital_usd=settings.paper.capital_usd, peak_equity_usd=settings.paper.capital_usd)
        self.execution = ExecutionState()
        self._last_frontier_call: datetime | None = None

    def should_call_frontier(self, events) -> bool:
        now = datetime.now(tz=timezone.utc)
        if self._last_frontier_call is None or (now - self._last_frontier_call).total_seconds() >= self.settings.frontier.periodic_seconds:
            return True
        return any(event.severity >= self.settings.frontier.event_severity_threshold for event in events)

    def run_once(self) -> DecisionRecord:
        bars = self.adapter.fetch_closed_bars(limit=self.settings.market.warmup_bars)
        if bars.is_empty():
            raise RuntimeError("market adapter returned no closed bars")
        observations = self.adapter.snapshot()
        self.fabric.ingest(observations)
        quality = self.fabric.quality()
        environment = build_market_environment(bars, ticks=self.fabric.latest(), quality=quality, decision_timestamp=datetime.now(tz=timezone.utc))
        events = detect_events(environment, self.settings.quant)
        regime = assess_regime(environment)
        request_id = str(uuid4())
        hypothesis: StrategyHypothesis
        if not quality.safe_for_trading:
            hypothesis = StrategyHypothesis(
                hypothesis_id=request_id, timestamp=environment.decision_timestamp, regime=regime.trend,
                regime_confidence=regime.confidence, thesis="Data quality is unsafe", abstain=True,
                reason="data_quality_unsafe", model_version=self.frontier.client.model_version,
                prompt_version=self.settings.frontier.prompt_version,
            )
        else:
            try:
                hypothesis = self.frontier.generate(environment, request_id=request_id, regime=regime.trend)
            except FrontierUnavailable as exc:
                hypothesis = StrategyHypothesis(
                    hypothesis_id=request_id, timestamp=environment.decision_timestamp, regime=regime.trend,
                    regime_confidence=regime.confidence, thesis="Frontier unavailable", abstain=True,
                    reason=f"frontier_unavailable:{exc}", model_version=self.frontier.client.model_version,
                    prompt_version=self.settings.frontier.prompt_version,
                )
        analyses = self.quant.run_all(environment)
        candidate = make_candidate(environment, hypothesis, settings=self.settings)
        jev_evaluation = None
        if candidate is not None and not hypothesis.abstain and quality.safe_for_trading:
            questions = hypothesis.jev_questions or ()
            jev_request = JevRequest(request_id=request_id, timestamp=environment.decision_timestamp, environment=environment, frontier_hypothesis=hypothesis, quant_evidence=analyses, candidate_trade=candidate, questions=questions, prompt_version=self.settings.jev.prompt_version)
            try:
                jev_evaluation = self.jev.evaluate(jev_request)
            except Exception as exc:
                hypothesis = hypothesis.model_copy(update={"abstain": True, "reason": f"jev_unavailable:{type(exc).__name__}"})
                candidate = None
        policy = self.policy.finalize(environment, hypothesis, None, jev_evaluation, position=self.position, candidate=candidate)
        risk = self.risk.evaluate(policy, environment, self.account, self.execution, position=self.position)
        intent = None
        if risk.status == RiskStatus.APPROVED and policy.action in {PolicyAction.ENTER_LONG, PolicyAction.ENTER_SHORT}:
            intent = ExecutionIntent(intent_id=str(uuid4()), decision_id=policy.decision_id, mode="PAPER", action=policy.action, side=candidate.side, quantity=risk.approved_quantity, reference_price=environment.price.last, created_at=environment.decision_timestamp, earliest_execution_at=environment.decision_timestamp + timedelta(minutes=1), strategy_id=candidate.strategy_id)
        record = DecisionRecord(
            decision_id=policy.decision_id, experiment_id=self.settings.experiment_id, timestamp=environment.decision_timestamp,
            market_environment=environment, frontier_hypothesis=hypothesis, quant_analyses=analyses,
            jev_request=None, jev_evaluation=jev_evaluation, policy_decision=policy, risk_decision=risk,
            execution_intent=intent, position_before=self.position, position_after=self.position,
            versions=Versions(code_version=self.settings.code_version, experiment_id=self.settings.experiment_id, frontier_model=self.frontier.client.model_version, frontier_prompt=self.settings.frontier.prompt_version, jev_model=self.jev.client.model_version, jev_prompt=self.settings.jev.prompt_version, quant_analyzers={name: "quant-v1" for name in self.quant.names()}, policy="policy-v1", risk=self.risk.version, strategy_registry=self.settings.strategy_registry_version),
        )
        self.ledger.append_decision(record)
        self._last_frontier_call = environment.decision_timestamp
        return record

    def run_forever(self, *, iterations: int | None = None) -> None:
        count = 0
        while iterations is None or count < iterations:
            self.run_once(); count += 1
            if iterations is None:
                time.sleep(self.settings.market.poll_seconds)

"""Small explicit live-shadow orchestrator."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

from jev_trading.data.normalization import DataFabric, MarketDataAdapter
from jev_trading.environment import assess_regime, build_market_environment, detect_events
from jev_trading.ledger import DecisionLedger
from jev_trading.live_intelligence.config import LiveSettings
from jev_trading.live_intelligence.experiment import make_versions
from jev_trading.live_intelligence.execution import PaperExecutor
from jev_trading.live_intelligence.frontier.client import FrontierClient, FrontierUnavailable
from jev_trading.live_intelligence.frontier.strategist import FrontierStrategist, load_prompt
from jev_trading.live_intelligence.jev.client import JevClient
from jev_trading.live_intelligence.jev.evaluator import JevEvaluator
from jev_trading.live_intelligence.policy import PolicyFinalizer, make_candidate
from jev_trading.live_intelligence.quant import QuantRegistry, analyze_path, build_completed_path_samples, calculate_economic_value
from jev_trading.live_intelligence.risk import ActiveRiskKernel
from jev_trading.research import ResearchMemory, StrategyRegistry
from jev_trading.live_intelligence.provider_errors import ProviderFailure
from jev_trading.live_intelligence.schemas import (
    AccountState,
    DataQuality,
    DecisionRecord,
    ExecutionIntent,
    ExecutionState,
    JevRequest,
    PolicyAction,
    PositionState,
    QuantEvidence,
    RiskStatus,
    Side,
    StrategyHypothesis,
    PendingIntentState,
    ProviderFailureRecord,
)


def _provider_failure_label(exc: Exception) -> str:
    if isinstance(exc, ProviderFailure):
        status = f":{exc.http_status}" if exc.http_status is not None else ""
        return f"{exc.category}{status}:retries={exc.retry_count}"
    return type(exc).__name__


class ShadowRunner:
    """One-process paper runner; every external dependency is injectable."""

    def __init__(self, settings: LiveSettings, adapter: MarketDataAdapter, frontier_client: FrontierClient, jev_client: JevClient, *, arm: str = "C", ledger_path: str | Path = "research/runtime/ledger/decisions.jsonl", checkpoint_path: str | Path | None = None, clock: Callable[[], datetime] | None = None):
        aliases = {
            "QUANT_ONLY": "A", "QUANT_POLICY": "A",
            "QUANT_FRONTIER": "B", "QUANT_FRONTIER_JEV": "C",
        }
        requested_arm = arm
        canonical_arm = aliases.get(arm, arm)
        if canonical_arm not in {"A", "B", "C"}:
            raise ValueError("arm must be A/B/C or an explicit experiment mode")
        self.settings = settings
        self._clock = clock or (lambda: datetime.now(tz=timezone.utc))
        self.arm = canonical_arm
        self.experiment_mode = requested_arm
        self.require_jev = canonical_arm == "C"
        self.adapter = adapter
        self.quant = QuantRegistry()
        frontier_prompt = load_prompt(Path(__file__).parent / settings.frontier.prompt_file)
        self.frontier = FrontierStrategist(
            frontier_client, prompt=frontier_prompt, prompt_version=settings.frontier.prompt_version,
        )
        jev_prompt = load_prompt(Path(__file__).parent / settings.jev.prompt_file)
        self.jev = JevEvaluator(
            jev_client, prompt=jev_prompt,
            max_validity_seconds=settings.jev.validity_seconds,
            prompt_version=settings.jev.prompt_version,
            minimum_target_probability=settings.jev.minimum_target_probability,
            maximum_stop_probability=settings.jev.maximum_stop_probability,
            minimum_entry_quality=settings.jev.minimum_entry_quality,
            maximum_failure_probability=settings.jev.maximum_failure_probability,
            minimum_liquidity_quality=settings.jev.minimum_liquidity_quality,
        )
        self.policy = PolicyFinalizer(settings)
        self.risk = ActiveRiskKernel(settings)
        self.paper = PaperExecutor(settings, self.risk)
        self.fabric = DataFabric(settings.market.required_source_roles, max_age_ms=settings.risk.stale_data_ms)
        self.ledger = DecisionLedger(ledger_path)
        self.checkpoint_path = Path(checkpoint_path) if checkpoint_path is not None else Path(str(ledger_path) + ".state.json")
        self.research = ResearchMemory(settings.research.root)
        strategy_path = Path(settings.research.strategy_registry)
        if not strategy_path.exists():
            strategy_path = Path(__file__).resolve().parents[3] / strategy_path
        self.strategy_registry = StrategyRegistry(strategy_path)
        self.position = PositionState()
        self.account = AccountState(capital_usd=settings.paper.capital_usd, peak_equity_usd=settings.paper.capital_usd)
        self.execution = ExecutionState()
        self._last_frontier_call: datetime | None = None
        self._frontier_call_times: list[datetime] = []
        self._last_jev_call: datetime | None = None
        self._jev_call_times: list[datetime] = []
        self._pending_intent = None
        self._pending_risk = None
        self._pending_decision_id = None
        self._pending_state: PendingIntentState | None = None
        self._trading_day = self._clock().date()
        self._restore_checkpoint()

    def should_call_frontier(self, events, *, now: datetime | None = None) -> bool:
        current = now or self._clock()
        self._frontier_call_times = [
            timestamp for timestamp in self._frontier_call_times
            if timestamp >= current - timedelta(hours=1)
        ]
        if len(self._frontier_call_times) >= self.settings.frontier.max_calls_per_hour:
            return False
        if any(event.severity >= self.settings.frontier.event_severity_threshold for event in events):
            return True
        return (
            self._last_frontier_call is None
            or (current - self._last_frontier_call).total_seconds()
            >= self.settings.frontier.min_call_interval_seconds
        )

    def _record_frontier_call(self, timestamp: datetime) -> None:
        self._last_frontier_call = timestamp
        self._frontier_call_times.append(timestamp)

    def should_call_jev(self, *, now: datetime | None = None) -> bool:
        current = now or self._clock()
        self._jev_call_times = [
            timestamp for timestamp in self._jev_call_times
            if timestamp >= current - timedelta(hours=1)
        ]
        if len(self._jev_call_times) >= self.settings.jev.max_calls_per_hour:
            return False
        return (
            self._last_jev_call is None
            or (current - self._last_jev_call).total_seconds()
            >= self.settings.jev.min_call_interval_seconds
        )

    def _record_jev_call(self, timestamp: datetime) -> None:
        self._last_jev_call = timestamp
        self._jev_call_times.append(timestamp)

    def _restore_checkpoint(self) -> None:
        if not self.checkpoint_path.exists():
            if self.ledger.records() or self.ledger.fills() or self.ledger.trades():
                raise RuntimeError("durable runtime checkpoint is missing for a non-empty ledger")
            return
        try:
            payload = json.loads(self.checkpoint_path.read_text())
            if payload.get("schema_version") != 1:
                raise ValueError("unsupported runtime checkpoint schema")
            if payload.get("ledger_hash") != self.ledger.last_hash:
                raise ValueError("runtime checkpoint is not anchored to the ledger")
            self.position = PositionState.model_validate(payload["position"])
            self.account = AccountState.model_validate(payload["account"])
            self.execution = ExecutionState.model_validate(payload["execution"])
            self.paper.restore_state(payload["paper"])
            pending = payload.get("pending")
            self._pending_state = PendingIntentState.model_validate(pending) if pending else None
            self._pending_intent = self._pending_state.intent if self._pending_state else None
            self._pending_risk = self._pending_state.risk_decision if self._pending_state else None
            self._pending_decision_id = self._pending_intent.decision_id if self._pending_intent else None
            self._trading_day = datetime.fromisoformat(payload["trading_day"]).date()
        except Exception as exc:
            raise RuntimeError(f"runtime checkpoint restore failed: {exc}") from exc

    def _persist_checkpoint(self) -> None:
        payload = {
            "schema_version": 1,
            "ledger_hash": self.ledger.last_hash,
            "position": self.position.model_dump(mode="json"),
            "account": self.account.model_dump(mode="json"),
            "execution": self.execution.model_dump(mode="json"),
            "pending": self._pending_state.model_dump(mode="json") if self._pending_state else None,
            "trading_day": self._trading_day.isoformat(),
            "paper": self.paper.to_state(),
        }
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.checkpoint_path.with_suffix(self.checkpoint_path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.checkpoint_path)
        try:
            directory_fd = os.open(self.checkpoint_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass

    def _clear_pending(self) -> None:
        self._pending_intent = None
        self._pending_risk = None
        self._pending_decision_id = None
        self._pending_state = None

    def _roll_trading_day(self, now: datetime) -> None:
        if now.date() != self._trading_day:
            self._trading_day = now.date()
            self.account = self.account.model_copy(update={"daily_realized_pnl_usd": 0.0})

    def _sync_runtime_state(self, environment, trade=None) -> None:
        self._roll_trading_day(environment.decision_timestamp)
        realized_delta = trade.net_pnl_usd if trade is not None else 0.0
        equity = (
            self.settings.paper.capital_usd
            + self.paper.position.realized_pnl
            + self.paper.position.funding_pnl_usd
            + self.paper.position.unrealized_pnl
        )
        peak = max(self.account.peak_equity_usd or equity, equity)
        self.account = self.account.model_copy(update={
            "capital_usd": equity,
            "daily_realized_pnl_usd": self.account.daily_realized_pnl_usd + realized_delta,
            "peak_equity_usd": peak,
            "open_positions": 0 if self.paper.position.side == Side.FLAT else 1,
            "pending_orders": 0 if self._pending_state is None else 1,
        })
        self.execution = self.execution.model_copy(update={
            "orders_last_minute": self.risk.orders_last_minute(environment.decision_timestamp),
            "feed_healthy": environment.data_quality.safe_for_trading,
        })

    def _revalidate_pending(
        self,
        environment,
        policy,
        risk,
        candidate,
    ) -> tuple[bool, bool]:
        """Return (valid, executed) after checking current state before a fill."""
        intent = self._pending_intent
        if intent is None:
            return False, False
        now = environment.decision_timestamp
        if not environment.data_quality.safe_for_trading or environment.timestamp > now:
            self._clear_pending()
            return False, False
        if intent.expires_at is not None and now >= intent.expires_at:
            self._clear_pending()
            return False, False
        if now < intent.created_at or policy.action != intent.action or risk.status != RiskStatus.APPROVED:
            self._clear_pending()
            return False, False
        if intent.action in {PolicyAction.ENTER_LONG, PolicyAction.ENTER_SHORT}:
            if self.position.side != Side.FLAT or candidate is None or candidate.side != intent.side:
                self._clear_pending()
                return False, False
            if candidate.strategy_id != intent.strategy_id:
                self._clear_pending()
                return False, False
        else:
            if self.position.side == Side.FLAT or self.position.side != intent.side:
                self._clear_pending()
                return False, False
            if intent.quantity > self.position.quantity + 1e-12:
                self._clear_pending()
                return False, False
        if environment.timestamp < intent.earliest_execution_at:
            return True, False
        execution_risk = risk.model_copy(update={"decision_id": intent.decision_id})
        try:
            fill = self.paper.execute(intent, execution_risk, environment)
        except (TypeError, ValueError):
            self._clear_pending()
            return False, False
        self.ledger.append_fill(fill)
        self.position = self.paper.position
        trade = self.paper.last_trade
        if trade is not None:
            self.ledger.append_trade(trade)
        if environment.price.last is not None:
            self.position = self.paper.mark(environment.price.last, environment.timestamp)
        if self.settings.risk.cooldown_seconds > 0:
            self.execution = self.execution.model_copy(update={
                "cooldown_until": environment.decision_timestamp + timedelta(seconds=self.settings.risk.cooldown_seconds),
            })
        self._clear_pending()
        self._sync_runtime_state(environment, trade)
        self._persist_checkpoint()
        return True, True

    def _quant_hypothesis(self, environment, regime, request_id: str) -> StrategyHypothesis:
        trend = environment.timeframes["1h"].trend_direction
        if trend not in {"UP", "DOWN"}:
            return StrategyHypothesis(
                hypothesis_id=request_id, timestamp=environment.decision_timestamp,
                regime=regime.trend, regime_confidence=regime.confidence,
                thesis="Quant arm has no confirmed directional state", abstain=True,
                reason="quant_arm_abstains", model_version="quant-baseline-v1",
                prompt_version="quant-baseline-v1",
            )
        direction = "LONG" if trend == "UP" else "SHORT"
        return StrategyHypothesis(
            hypothesis_id=request_id, timestamp=environment.decision_timestamp,
            regime=regime.trend, regime_confidence=regime.confidence,
            primary_strategy="momentum", direction=direction, horizon_seconds=900,
            thesis="Deterministic Quant directional baseline",
            supporting_evidence=("quant_timeframe_alignment",),
            entry_conditions=("quant_timeframe_alignment",),
            invalidation_conditions=("trend_transition", "liquidity_deterioration"),
            target_logic="policy_atr_target", stop_logic="policy_atr_stop",
            maximum_holding_seconds=900, abandon_conditions=("data_becomes_unsafe",),
            abstain=False, reason="quant_arm", conviction=0.5,
            model_version="quant-baseline-v1", prompt_version="quant-baseline-v1",
        )

    def run_once(self) -> DecisionRecord:
        bars = self.adapter.fetch_closed_bars(limit=self.settings.market.warmup_bars)
        if bars.is_empty():
            raise RuntimeError("market adapter returned no closed bars")
        self.fabric.ingest_bars(bars)
        observations = self.adapter.snapshot()
        self.fabric.ingest(observations)
        quality = self.fabric.quality()
        environment = build_market_environment(
            bars, ticks=self.fabric.latest(), quality=quality,
            position=self.position, decision_timestamp=self._clock(),
        )
        position_before = self.position
        if environment.price.last is not None:
            self.position = self.paper.mark(environment.price.last, environment.timestamp)
        environment = environment.model_copy(update={
            "position": self.position,
            "liquidity": environment.liquidity.model_copy(update={
                "estimated_slippage": self.settings.costs.slippage_bps_per_side / 10_000,
            }),
        })
        self._sync_runtime_state(environment)
        events = detect_events(environment, self.settings.quant)
        environment = environment.model_copy(update={"events": events})
        regime = assess_regime(environment)
        analyses = [
            result for result in self.quant.run_all(environment)
            if result.analysis_name not in {"path", "opportunity"}
        ]
        preliminary_evidence = QuantEvidence(
            timestamp=environment.decision_timestamp,
            regime=regime.trend,
            regime_confidence=regime.confidence,
            analyzer_results=tuple(analyses),
            limitations=tuple(dict.fromkeys(
                limitation for result in analyses for limitation in result.limitations
            )),
            analyzer_versions={result.analysis_name: result.analyzer_version for result in analyses},
        )
        request_id = str(uuid4())
        provider_failures: list[ProviderFailureRecord] = []
        hypothesis: StrategyHypothesis
        if not quality.safe_for_trading:
            hypothesis = StrategyHypothesis(
                hypothesis_id=request_id, timestamp=environment.decision_timestamp, regime=regime.trend,
                regime_confidence=regime.confidence, thesis="Data quality is unsafe", abstain=True,
                reason="data_quality_unsafe", model_version=self.frontier.client.model_version,
                prompt_version=self.settings.frontier.prompt_version, prompt_hash=self.frontier.prompt_hash,
            )
        elif self.arm == "A":
            hypothesis = self._quant_hypothesis(environment, regime, request_id)
        elif not self.should_call_frontier(events, now=environment.decision_timestamp):
            hypothesis = StrategyHypothesis(
                hypothesis_id=request_id, timestamp=environment.decision_timestamp, regime=regime.trend,
                regime_confidence=regime.confidence, thesis="Frontier cadence gate", abstain=True,
                reason="frontier_not_due", model_version=self.frontier.client.model_version,
                prompt_version=self.settings.frontier.prompt_version, prompt_hash=self.frontier.prompt_hash,
            )
        else:
            self._record_frontier_call(environment.decision_timestamp)
            try:
                hypothesis = self.frontier.generate(
                    environment, request_id=request_id, regime=regime.trend,
                    quant_evidence=preliminary_evidence,
                    research_memory=self.research.frontier_context(self.ledger),
                    system_health={
                        "data_quality": environment.data_quality.model_dump(mode="json"),
                        "execution_mode": self.settings.execution_mode,
                    },
                )
            except Exception as exc:
                provider_failures.append(ProviderFailureRecord.from_exception(
                    component="frontier",
                    provider=getattr(self.frontier.client, "provider", self.settings.frontier.provider.provider),
                    model=getattr(self.frontier.client, "model", self.settings.frontier.provider.model),
                    exc=exc,
                ))
                hypothesis = StrategyHypothesis(
                    hypothesis_id=request_id, timestamp=environment.decision_timestamp, regime=regime.trend,
                    regime_confidence=regime.confidence, thesis="Frontier unavailable", abstain=True,
                    reason=f"frontier_unavailable:{_provider_failure_label(exc)}", model_version=self.frontier.client.model_version,
                    prompt_version=self.settings.frontier.prompt_version, prompt_hash=self.frontier.prompt_hash,
                )
        candidate = make_candidate(
            environment, hypothesis, settings=self.settings, strategy_registry=self.strategy_registry,
        )
        if candidate is None and not hypothesis.abstain and hypothesis.primary_strategy:
            hypothesis = hypothesis.model_copy(update={"abstain": True, "reason": "strategy_not_registered_or_allowed"})
        path_result = None
        economic_value = None
        if candidate is not None:
            target_fraction = abs(candidate.target / candidate.entry_reference - 1.0)
            stop_fraction = abs(candidate.stop / candidate.entry_reference - 1.0)
            horizon_minutes = max(1, candidate.max_holding_seconds // 60)
            generated = build_completed_path_samples(
                bars, side=candidate.side, target_fraction=target_fraction, stop_fraction=stop_fraction,
                horizon_minutes=horizon_minutes, max_samples=500,
            )
            if generated:
                path_result = analyze_path(
                    environment, generated, side=candidate.side,
                    target_fraction=target_fraction, stop_fraction=stop_fraction,
                    horizon_minutes=horizon_minutes, horizon_seconds=candidate.max_holding_seconds,
                )
                analyses.append(path_result)
                if path_result.path_probabilities is not None:
                    economic_value = calculate_economic_value(
                        candidate, path_result.path_probabilities,
                        self.settings.costs.assumptions(candidate.max_holding_seconds, environment.derivatives.funding or 0.0),
                        timeout_return_fraction=path_result.expected_timeout_return or 0.0,
                        expected_duration_seconds=path_result.expected_duration_seconds,
                        sample_size=path_result.empirical_sample_size or 0,
                    )
        if economic_value is not None:
            analyses.append(self.quant.run("opportunity_analyzer", environment, economic_value=economic_value))
        quant_evidence = QuantEvidence(
            timestamp=environment.decision_timestamp,
            regime=regime.trend,
            regime_confidence=regime.confidence,
            analyzer_results=tuple(analyses),
            path=path_result,
            probabilities=path_result.path_probabilities if path_result else None,
            economic_value=economic_value,
            cost_assumptions=economic_value.cost_assumptions if economic_value else None,
            uncertainty=path_result.uncertainty if path_result else None,
            sample_size=path_result.empirical_sample_size if path_result else 0,
            limitations=tuple(dict.fromkeys(
                limitation
                for result in analyses
                for limitation in result.limitations
            )),
            analyzer_versions={result.analysis_name: result.analyzer_version for result in analyses},
        )
        jev_request = None
        jev_evaluation = None
        if self.arm == "C" and candidate is not None and not hypothesis.abstain and quality.safe_for_trading:
            if not self.should_call_jev(now=environment.decision_timestamp):
                hypothesis = hypothesis.model_copy(update={"abstain": True, "reason": "jev_not_due"})
                candidate = None
            else:
                questions = hypothesis.jev_questions or ()
                jev_request = JevRequest(request_id=request_id, timestamp=environment.decision_timestamp, environment=environment, frontier_hypothesis=hypothesis, quant_evidence=quant_evidence, candidate_trade=candidate, questions=questions, prompt_version=self.settings.jev.prompt_version)
                self._record_jev_call(environment.decision_timestamp)
                try:
                    jev_evaluation = self.jev.evaluate(jev_request)
                except Exception as exc:
                    provider_failures.append(ProviderFailureRecord.from_exception(
                        component="jev",
                        provider=getattr(self.jev.client, "provider", self.settings.jev.provider.provider),
                        model=getattr(self.jev.client, "model", self.settings.jev.provider.model),
                        exc=exc,
                    ))
                    hypothesis = hypothesis.model_copy(update={"abstain": True, "reason": f"jev_unavailable:{_provider_failure_label(exc)}"})
                    candidate = None
        policy = self.policy.finalize(
            environment, hypothesis, economic_value, jev_evaluation,
            position=self.position, candidate=candidate, require_jev=self.require_jev,
        )
        risk = self.risk.evaluate(policy, environment, self.account, self.execution, position=self.position)
        _pending_valid, pending_executed = self._revalidate_pending(environment, policy, risk, candidate)
        intent = None
        if self._pending_intent is None and not pending_executed and risk.status == RiskStatus.APPROVED and policy.action in {PolicyAction.ENTER_LONG, PolicyAction.ENTER_SHORT}:
            intent = ExecutionIntent(
                intent_id=str(uuid4()), decision_id=policy.decision_id, mode="PAPER",
                action=policy.action, side=candidate.side, quantity=risk.approved_quantity,
                reference_price=environment.price.last, stop=candidate.stop, target=candidate.target,
                created_at=environment.decision_timestamp,
                earliest_execution_at=environment.decision_timestamp + timedelta(minutes=1),
                expires_at=environment.decision_timestamp + timedelta(seconds=self.settings.pending_intent_ttl_seconds),
                strategy_id=candidate.strategy_id, strategy_version=candidate.strategy_version,
            )
        elif self._pending_intent is None and not pending_executed and risk.status == RiskStatus.APPROVED and policy.action in {PolicyAction.EXIT, PolicyAction.REDUCE} and self.position.side != Side.FLAT:
            intent = ExecutionIntent(
                intent_id=str(uuid4()), decision_id=policy.decision_id, mode="PAPER",
                action=policy.action, side=self.position.side, quantity=risk.approved_quantity,
                reference_price=environment.price.last, created_at=environment.decision_timestamp,
                earliest_execution_at=environment.decision_timestamp + timedelta(minutes=1),
                expires_at=environment.decision_timestamp + timedelta(seconds=self.settings.pending_intent_ttl_seconds),
                strategy_id=self.position.strategy_id or "unknown", strategy_version=self.position.strategy_version or "unknown",
            )
        record = DecisionRecord(
            decision_id=policy.decision_id, experiment_id=self.settings.experiment_id, timestamp=environment.decision_timestamp,
            market_environment=environment, frontier_hypothesis=hypothesis, quant_analyses=analyses,
            quant_evidence=quant_evidence, jev_request=jev_request, jev_evaluation=jev_evaluation, economic_value=economic_value, policy_decision=policy, risk_decision=risk,
            provider_failures=tuple(provider_failures),
            execution_intent=intent, position_before=position_before, position_after=self.position,
            versions=make_versions(
                self.settings,
                frontier_model=self.frontier.client.model_version,
                frontier_prompt_hash=self.frontier.prompt_hash,
                jev_model=self.jev.client.model_version,
                jev_prompt_hash=self.jev.prompt_hash,
                quant_versions=quant_evidence.analyzer_versions,
                arm=self.experiment_mode,
                frontier_provider=getattr(self.frontier.client, "provider", self.settings.frontier.provider.provider),
                jev_provider=getattr(self.jev.client, "provider", self.settings.jev.provider.provider),
                frontier_capabilities={
                    "response_format": getattr(self.frontier.client, "supports_response_format", self.settings.frontier.provider.supports_response_format),
                    "tool_calling": getattr(self.frontier.client, "supports_tool_calling", self.settings.frontier.provider.supports_tool_calling),
                    "reasoning": getattr(self.frontier.client, "supports_reasoning", self.settings.frontier.provider.supports_reasoning),
                    "vision": getattr(self.frontier.client, "supports_vision", self.settings.frontier.provider.supports_vision),
                },
                jev_capabilities={
                    "response_format": getattr(self.jev.client, "supports_response_format", self.settings.jev.provider.supports_response_format),
                    "tool_calling": getattr(self.jev.client, "supports_tool_calling", self.settings.jev.provider.supports_tool_calling),
                    "reasoning": getattr(self.jev.client, "supports_reasoning", self.settings.jev.provider.supports_reasoning),
                    "vision": getattr(self.jev.client, "supports_vision", self.settings.jev.provider.supports_vision),
                },
            ),
        )
        self.ledger.append_decision(record)
        self.research.record_decision(record)
        self.research.maybe_generate(
            self.ledger, environment.decision_timestamp,
            hourly=self.settings.research.hourly,
            daily=self.settings.research.daily,
            weekly=self.settings.research.weekly,
        )
        if intent is not None:
            self._pending_intent = intent
            self._pending_risk = risk
            self._pending_decision_id = policy.decision_id
            self._pending_state = PendingIntentState(intent=intent, risk_decision=risk, created_at=intent.created_at)
            self.risk.record_order(environment.decision_timestamp)
        self._sync_runtime_state(environment)
        self._persist_checkpoint()
        self._persist_metrics(record)
        return record

    def _persist_metrics(self, record: DecisionRecord) -> None:
        from .metrics import Metrics
        metrics = getattr(self, "_metrics", None)
        if metrics is None:
            metrics = Metrics(self.settings.observability.metrics_file)
            self._metrics = metrics
        metrics.increment("decisions_total")
        metrics.increment(f"arm_{self.arm}_decisions")
        metrics.increment(f"policy_{record.policy_decision.action.value.lower()}")
        metrics.increment(f"risk_{record.risk_decision.status.value.lower()}")
        metrics.flush()

    def run_forever(self, *, iterations: int | None = None) -> None:
        count = 0
        while iterations is None or count < iterations:
            self.run_once(); count += 1
            if iterations is None:
                time.sleep(self.settings.market.poll_seconds)

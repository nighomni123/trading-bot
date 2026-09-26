"""Small explicit live-shadow orchestrator."""
from __future__ import annotations

import json
import hashlib
import os
import signal
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

from jev_trading.data.normalization import DataFabric, MarketDataAdapter, ReplayAdapter
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
    PolicyDecision,
    PositionState,
    QuantEvidence,
    RiskStatus,
    Side,
    StrategyHypothesis,
    PendingIntentState,
    ProviderFailureRecord,
)

RUN_MODES = ("LIVE_DATA_PAPER", "REPLAY")


def _provider_failure_label(exc: Exception) -> str:
    if isinstance(exc, ProviderFailure):
        status = f":{exc.http_status}" if exc.http_status is not None else ""
        return f"{exc.category}{status}:retries={exc.retry_count}"
    return type(exc).__name__


def infer_run_mode(adapter: MarketDataAdapter) -> str:
    """LIVE_DATA_PAPER drives a real public feed; REPLAY is fixture-driven."""
    current = adapter
    while True:
        if isinstance(current, ReplayAdapter):
            return "REPLAY"
        primary = getattr(current, "primary", None)
        if primary is None or primary is current:
            return "LIVE_DATA_PAPER"
        current = primary


class ShadowRunner:
    """One-process paper runner; every external dependency is injectable."""

    def __init__(self, settings: LiveSettings, adapter: MarketDataAdapter, frontier_client: FrontierClient, jev_client: JevClient, *, arm: str = "C", ledger_path: str | Path = "research/runtime/ledger/decisions.jsonl", checkpoint_path: str | Path | None = None, clock: Callable[[], datetime] | None = None, telemetry=None, run_mode: str | None = None):
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
        self.run_mode = run_mode or infer_run_mode(adapter)
        if self.run_mode not in RUN_MODES:
            raise ValueError(f"run_mode must be one of {RUN_MODES}")
        self.telemetry = telemetry
        self.quant = QuantRegistry()
        frontier_prompt = load_prompt(Path(__file__).parent / settings.frontier.prompt_file)
        self.frontier = FrontierStrategist(
            frontier_client, prompt=frontier_prompt, prompt_version=settings.frontier.prompt_version,
            settings=settings,
        )
        jev_prompt = load_prompt(Path(__file__).parent / settings.jev.prompt_file)
        self.jev = JevEvaluator(
            jev_client, prompt=jev_prompt, settings=settings,
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
        self._frontier_event_keys: set[tuple[str, str]] = set()
        self._last_jev_call: datetime | None = None
        self._jev_call_times: list[datetime] = []
        self._pending_intent = None
        self._pending_risk = None
        self._pending_decision_id = None
        self._pending_state: PendingIntentState | None = None
        self._last_boundary: datetime | None = None
        self._last_cancellation: str | None = None
        self._timings_ms: dict[str, float] = {}
        self._latency_samples: dict[str, list[float]] = {}
        self._stopping = False
        self._trading_day = self._clock().date()
        self._restore_checkpoint()

    # -- cadence -----------------------------------------------------------
    def _prune_hour(self, timestamps: list[datetime], current: datetime) -> list[datetime]:
        return [timestamp for timestamp in timestamps if timestamp >= current - timedelta(hours=1)]

    def should_call_frontier(self, events, *, now: datetime | None = None) -> bool:
        """Periodic cadence, with a new high-severity event as the exception.

        The minimum call interval is a hard floor for every call, and the
        hourly cap is authoritative; a repeated event never re-triggers.
        """
        current = now or self._clock()
        self._frontier_call_times = self._prune_hour(self._frontier_call_times, current)
        if len(self._frontier_call_times) >= self.settings.frontier.max_calls_per_hour:
            return False
        floor = self.settings.frontier.min_call_interval_seconds
        since_last = (
            None if self._last_frontier_call is None
            else (current - self._last_frontier_call).total_seconds()
        )
        if since_last is not None and since_last < floor:
            return False
        if any(
            event.severity >= self.settings.frontier.event_severity_threshold
            and self._event_key(event) not in self._frontier_event_keys
            for event in events
        ):
            return True
        if since_last is None:
            return True
        return since_last >= self.settings.frontier.periodic_seconds

    @staticmethod
    def _event_key(event) -> tuple[str, str]:
        return (event.event_type, event.event_timestamp.isoformat())

    def _mark_frontier_events(self, events) -> None:
        self._frontier_event_keys = {self._event_key(event) for event in events}

    def _record_frontier_call(self, timestamp: datetime) -> None:
        self._last_frontier_call = timestamp
        self._frontier_call_times.append(timestamp)

    def should_call_jev(self, *, now: datetime | None = None) -> bool:
        current = now or self._clock()
        self._jev_call_times = self._prune_hour(self._jev_call_times, current)
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

    def _runtime_state(self) -> dict:
        """Single source of truth for committable runtime state."""
        return {
            "position": self.position.model_dump(mode="json"),
            "account": self.account.model_dump(mode="json"),
            "execution": self.execution.model_dump(mode="json"),
            "pending": self._pending_state.model_dump(mode="json") if self._pending_state else None,
            "trading_day": self._trading_day.isoformat(),
            "paper": self.paper.to_state(),
        }

    def _apply_runtime_state(self, payload: dict) -> None:
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

    def _ledger_committed_state(self) -> dict | None:
        """Rebuild committed state from the ledger when the checkpoint is stale.

        ponytail: linear scan of the ledger suffix; fine at current run sizes.
        Upgrade to an index if a long-running experiment needs sublinear lookup.
        """
        for record in reversed(self.ledger.records()):
            if record.runtime_state is not None:
                return record.runtime_state
        return None

    def _restore_checkpoint(self) -> None:
        payload = None
        if self.checkpoint_path.exists():
            try:
                candidate = json.loads(self.checkpoint_path.read_text())
                if candidate.get("schema_version") != 1:
                    raise ValueError("unsupported runtime checkpoint schema")
                # An explicit mismatch is dangerous and must hard-fail; a missing
                # field means a legacy/stale checkpoint, so fall back to the ledger.
                stored_experiment = candidate.get("experiment_id")
                if stored_experiment is not None and stored_experiment != self.settings.experiment_id:
                    raise ValueError("runtime checkpoint belongs to another experiment")
                if candidate.get("ledger_hash") == self.ledger.last_hash:
                    payload = candidate
            except ValueError as exc:
                raise RuntimeError(f"runtime checkpoint restore failed: {exc}") from exc
        if payload is None:
            payload = self._ledger_committed_state()
            if payload is None and (self.ledger.records() or self.ledger.fills() or self.ledger.trades()):
                raise RuntimeError("durable runtime checkpoint is missing for a non-empty ledger")
        if payload is None:
            return
        try:
            self._apply_runtime_state(payload)
        except Exception as exc:
            raise RuntimeError(f"runtime checkpoint restore failed: {exc}") from exc

    def _persist_checkpoint(self) -> None:
        payload = {
            "schema_version": 1,
            "experiment_id": self.settings.experiment_id,
            "ledger_hash": self.ledger.last_hash,
            **self._runtime_state(),
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

    def _request_id(self, decision_timestamp: datetime) -> str:
        return hashlib.sha256(
            f"{self.settings.experiment_id}|{decision_timestamp.isoformat()}".encode()
        ).hexdigest()[:32]

    def _committed_decision(self, request_id: str) -> DecisionRecord | None:
        """Return an already-committed decision, so replay never re-decides.

        The ledger is the intelligence cache, so a resumed experiment replays
        provider decisions instead of issuing fresh calls.
        ponytail: linear scan; upgrade to an index only if run sizes demand it.
        """
        for record in reversed(self.ledger.records()):
            if record.decision_id == request_id:
                return record
        return None

    def _clear_pending(self, reason: str | None = None) -> None:
        if reason is not None:
            self._last_cancellation = reason
        self._pending_intent = None
        self._pending_risk = None
        self._pending_decision_id = None
        self._pending_state = None

    def _timed(self, name: str):
        return _Stopwatch(self, name)

    def _resolve_pending(self, environment) -> bool:
        """Revalidate and settle a pending paper intent outside a decision boundary.

        A 15s poll must still be able to fill an intent approved a minute ago,
        using the policy that authorised it and a *fresh* risk evaluation.
        """
        if self._pending_state is None:
            return False
        policy = self._pending_state.policy
        risk = self.risk.evaluate(
            policy, environment, self.account, self.execution,
            position=self.position, decision_id=self._pending_intent.decision_id,
        )
        _valid, executed = self._revalidate_pending(environment, policy, risk, policy.candidate)
        return executed

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
            self._clear_pending("CURRENT_DATA_UNSAFE")
            return False, False
        if intent.expires_at is not None and now >= intent.expires_at:
            self._clear_pending("INTENT_EXPIRED")
            return False, False
        if now < intent.created_at or policy.action != intent.action or risk.status != RiskStatus.APPROVED:
            self._clear_pending("POLICY_OR_RISK_NO_LONGER_COMPATIBLE")
            return False, False
        if intent.action in {PolicyAction.ENTER_LONG, PolicyAction.ENTER_SHORT}:
            if self.position.side != Side.FLAT or candidate is None or candidate.side != intent.side:
                self._clear_pending("POSITION_OR_CANDIDATE_CONFLICT")
                return False, False
            if candidate.strategy_id != intent.strategy_id:
                self._clear_pending("STRATEGY_CHANGED")
                return False, False
        else:
            if self.position.side == Side.FLAT or self.position.side != intent.side:
                self._clear_pending("NO_MATCHING_POSITION")
                return False, False
            if intent.quantity > self.position.quantity + 1e-12:
                self._clear_pending("POSITION_SIZE_CHANGED")
                return False, False
        if environment.timestamp < intent.earliest_execution_at:
            return True, False
        execution_risk = risk.model_copy(update={"decision_id": intent.decision_id})
        try:
            fill = self.paper.execute(intent, execution_risk, environment)
        except (TypeError, ValueError) as exc:
            self._clear_pending(f"EXECUTION_REJECTED:{exc}")
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

    def _observe(self):
        """Ingest the feed and build the environment for the current clock."""
        with self._timed("data_fetch"):
            bars = self.adapter.fetch_closed_bars(limit=self.settings.market.warmup_bars)
            if bars.is_empty():
                raise RuntimeError("market adapter returned no closed bars")
            self.fabric.ingest_bars(bars)
            self.fabric.ingest(self.adapter.snapshot())
        # The decision instant is read after ingestion: observations can only be
        # received at or before the moment they are judged.
        now = self._clock()
        quality = self.fabric.quality(now=now)
        with self._timed("environment"):
            environment = build_market_environment(
                bars, ticks=self.fabric.latest(), quality=quality,
                position=self.position, decision_timestamp=now,
            )
        position_before = self.position
        if environment.price.last is not None:
            self.position = self.paper.mark(environment.price.last, environment.timestamp)
        environment = environment.model_copy(update={
            "position": self.position,
            # Measured, never assumed: half the spread is the observable cost of
            # crossing to the touch. Unknown stays unknown.
            "liquidity": environment.liquidity.model_copy(update={
                "estimated_slippage": (
                    environment.liquidity.spread / 2
                    if environment.liquidity.spread is not None else None
                ),
            }),
        })
        self._sync_runtime_state(environment)
        environment = environment.model_copy(update={
            "events": detect_events(environment, self.settings.quant),
        })
        return environment, position_before, assess_regime(environment)

    def run_once(self) -> DecisionRecord | None:
        """One poll: observe, settle any pending intent, decide only on a new bar.

        The decision boundary is the close of the latest completed 1m bar, so a
        15-second poll loop produces one logically independent decision per
        minute and a resumed run re-uses the committed one.
        """
        self._timings_ms = {}
        try:
            environment, _position_before, _regime = self._observe()
        except ValueError as exc:
            # A data problem must not kill the loop: record it and retry.
            self._last_cancellation = f"ENVIRONMENT_UNUSABLE:{exc}"
            self._emit_event("environment_unusable", reason=str(exc))
            return None
        boundary = environment.timestamp
        request_id = self._request_id(boundary)
        committed = self._committed_decision(request_id)
        if committed is not None:
            self._last_boundary = boundary
            if self._pending_state is not None:
                self._resolve_pending(environment)
            self._publish(environment, committed, phase="REPLAYED")
            return committed
        self._last_boundary = boundary
        return self._decide(environment, request_id)

    def _decide(self, environment, request_id: str) -> DecisionRecord:
        position_before = self.position
        regime = assess_regime(environment)
        events = environment.events
        with self._timed("quant"):
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
        provider_failures: list[ProviderFailureRecord] = []
        committed = self._committed_decision(request_id)
        quality = environment.data_quality
        bars = self.adapter.fetch_closed_bars(limit=self.settings.market.warmup_bars)
        hypothesis: StrategyHypothesis
        if committed is not None and committed.frontier_hypothesis is not None:
            hypothesis = committed.frontier_hypothesis
        elif not quality.safe_for_trading:
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
            self._mark_frontier_events(events)
            try:
                with self._timed("frontier"):
                    hypothesis = self.frontier.generate(
                        environment, request_id=request_id, regime=regime.trend,
                        quant_evidence=preliminary_evidence,
                        research_memory=self.research.frontier_context(self.ledger),
                        system_health={
                            "data_quality": environment.data_quality.model_dump(mode="json"),
                            "execution_mode": self.settings.execution_mode,
                            "run_mode": self.run_mode,
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
                if committed is not None and committed.jev_evaluation is not None:
                    jev_evaluation = committed.jev_evaluation
                else:
                    try:
                        with self._timed("jev"):
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
        with self._timed("policy"):
            policy = self.policy.finalize(
                environment, hypothesis, economic_value, jev_evaluation,
                position=self.position, candidate=candidate, require_jev=self.require_jev,
                decision_id=request_id,
            )
        with self._timed("risk"):
            risk = self.risk.evaluate(policy, environment, self.account, self.execution, position=self.position)
        # The next-open contract: a decision taken at the close of this bucket is
        # filled at the open of the bucket that follows it.
        boundary = environment.timestamp
        _pending_valid, pending_executed = self._revalidate_pending(environment, policy, risk, candidate)
        intent = None
        if self._pending_intent is None and not pending_executed and risk.status == RiskStatus.APPROVED and policy.action in {PolicyAction.ENTER_LONG, PolicyAction.ENTER_SHORT}:
            intent = ExecutionIntent(
                intent_id=str(uuid4()), decision_id=policy.decision_id, mode="PAPER",
                action=policy.action, side=candidate.side, quantity=risk.approved_quantity,
                reference_price=environment.price.last, stop=candidate.stop, target=candidate.target,
                created_at=boundary,
                earliest_execution_at=boundary + timedelta(minutes=1),
                expires_at=environment.decision_timestamp + timedelta(seconds=self.settings.pending_intent_ttl_seconds),
                strategy_id=candidate.strategy_id, strategy_version=candidate.strategy_version,
            )
        elif self._pending_intent is None and not pending_executed and risk.status == RiskStatus.APPROVED and policy.action in {PolicyAction.EXIT, PolicyAction.REDUCE} and self.position.side != Side.FLAT:
            intent = ExecutionIntent(
                intent_id=str(uuid4()), decision_id=policy.decision_id, mode="PAPER",
                action=policy.action, side=self.position.side, quantity=risk.approved_quantity,
                reference_price=environment.price.last, created_at=boundary,
                earliest_execution_at=boundary + timedelta(minutes=1),
                expires_at=environment.decision_timestamp + timedelta(seconds=self.settings.pending_intent_ttl_seconds),
                strategy_id=self.position.strategy_id or "unknown", strategy_version=self.position.strategy_version or "unknown",
            )
        record = DecisionRecord(
            decision_id=policy.decision_id, experiment_id=self.settings.experiment_id, timestamp=environment.decision_timestamp,
            market_environment=environment, frontier_hypothesis=hypothesis, quant_analyses=analyses,
            quant_evidence=quant_evidence, jev_request=jev_request, jev_evaluation=jev_evaluation, economic_value=economic_value, policy_decision=policy, risk_decision=risk,
            provider_failures=tuple(provider_failures),
            pending_cancellation=self._last_cancellation,
            runtime_state=self._runtime_state(),
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
            self._pending_state = PendingIntentState(
                intent=intent, risk_decision=risk, policy=policy, created_at=intent.created_at,
            )
            self.risk.record_order(environment.decision_timestamp)
        self._sync_runtime_state(environment)
        with self._timed("ledger"):
            self._persist_checkpoint()
        self._persist_metrics(record)
        self._publish(environment, record, phase="DECIDED")
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

    def _emit_event(self, event_type: str, **fields) -> None:
        telemetry = self.telemetry
        if telemetry is not None:
            telemetry.event(event_type, **fields)

    def _publish(self, environment, record, *, phase: str) -> None:
        """Hand one poll's state to telemetry (append-only metrics/events)."""
        telemetry = self.telemetry
        if telemetry is None:
            return
        for name, value in self._timings_ms.items():
            self._latency_samples.setdefault(name, []).append(value)
        telemetry.sample(
            {
                "timestamp": environment.decision_timestamp.isoformat(),
                "phase": phase,
                "run_mode": self.run_mode,
                "arm": self.arm,
                "experiment_id": self.settings.experiment_id,
                "decision_id": record.decision_id,
                "feed_age_ms": environment.data_quality.timestamp_lag_ms,
                "feed_status": "HEALTHY" if environment.data_quality.safe_for_trading else "UNSAFE",
                "bar_timestamp": environment.timestamp.isoformat(),
                "position": self.position.side.value,
                "position_quantity": self.position.quantity,
                "entry_price": self.position.entry_price,
                "mark_price": environment.price.last,
                "equity": self.account.capital_usd,
                "realized_pnl": self.paper.position.realized_pnl,
                "unrealized_pnl": self.position.unrealized_pnl,
                "funding_pnl": self.position.funding_pnl_usd,
                "daily_pnl": self.account.daily_realized_pnl_usd,
                "drawdown": max(
                    0.0, (self.account.peak_equity_usd or self.account.capital_usd) - self.account.capital_usd,
                ),
                "drawdown_pct": max(
                    0.0,
                    ((self.account.peak_equity_usd or self.account.capital_usd) - self.account.capital_usd)
                    / (self.account.peak_equity_usd or self.account.capital_usd) * 100,
                ),
                "quant_status": "OK" if record.quant_evidence else "UNAVAILABLE",
                "frontier_status": _component_status(record.frontier_hypothesis),
                "jev_status": _component_status(record.jev_evaluation),
                "policy_action": record.policy_decision.action.value,
                "risk_status": record.risk_decision.status.value,
                "pending_order": self._pending_intent.action.value if self._pending_intent else "NONE",
                "pending_cancellation": record.pending_cancellation,
                "last_cancellation": self._last_cancellation,
                "provider_failures": len(record.provider_failures),
                "latency_ms": dict(self._timings_ms),
                "ledger_sequence": len(self.ledger.records()),
                "ledger_hash": self.ledger.last_hash,
                "cumulative_fees": self.paper.cumulative_fees,
                "cumulative_slippage": self.paper.cumulative_slippage,
                "cumulative_funding": self.paper.cumulative_funding,
                "funding_model": self.paper.funding_model,
                "event_count": len(environment.events),
            }
        )
        for event in environment.events:
            telemetry.event("market_event", event=event.model_dump(mode="json"))
        for failure in record.provider_failures:
            telemetry.event("provider_failure", failure=failure.model_dump(mode="json"))

    def latency_report(self) -> dict[str, dict[str, float]]:
        return {
            name: {
                "samples": len(values),
                "mean_ms": sum(values) / len(values),
                "max_ms": max(values),
            }
            for name, values in self._latency_samples.items() if values
        }

    def install_signal_handlers(self) -> bool:
        """Stop cleanly on SIGINT/SIGTERM; never leave an executable order."""
        def handler(signum, _frame):
            self._stopping = True
        installed = False
        for name in ("SIGINT", "SIGTERM"):
            try:
                signal.signal(getattr(signal, name), handler)
                installed = True
            except (AttributeError, ValueError, OSError):
                continue
        return installed

    def shutdown(self) -> dict:
        """Cancel any outstanding intent, persist, and report the final state."""
        cancelled = None
        if self._pending_state is not None:
            cancelled = self._pending_intent.intent_id
            self._clear_pending("SHUTDOWN")
        self._persist_checkpoint()
        return {
            "experiment_id": self.settings.experiment_id,
            "run_mode": self.run_mode,
            "arm": self.arm,
            "decisions": len(self.ledger.records()),
            "fills": len(self.ledger.fills()),
            "trades": len(self.ledger.trades()),
            "position": self.position.side.value,
            "equity": self.account.capital_usd,
            "realized_pnl": self.paper.position.realized_pnl,
            "cancelled_pending_intent": cancelled,
            "ledger_hash": self.ledger.last_hash,
        }

    def run_forever(self, *, iterations: int | None = None, poll_seconds: float | None = None) -> dict:
        """Poll until the iteration budget or a signal; always sleeps between polls."""
        self.install_signal_handlers()
        interval = self.settings.market.poll_seconds if poll_seconds is None else poll_seconds
        count = 0
        try:
            while (iterations is None or count < iterations) and not self._stopping:
                self.run_once()
                count += 1
                if self._stopping or (iterations is not None and count >= iterations):
                    break
                time.sleep(interval)
        finally:
            summary = self.shutdown()
            if self.telemetry is not None:
                self.telemetry.close(summary)
        return summary


class _Stopwatch:
    def __init__(self, runner: "ShadowRunner", name: str):
        self.runner = runner
        self.name = name

    def __enter__(self):
        self.started = time.perf_counter()
        return self

    def __exit__(self, *_):
        self.runner._timings_ms[self.name] = round(
            (time.perf_counter() - self.started) * 1000, 3
        )


def _component_status(component) -> str:
    if component is None:
        return "UNAVAILABLE"
    if getattr(component, "abstain", False):
        return "ABSTAIN"
    reason = getattr(component, "reason", "") or ""
    if "unavailable" in reason or "expired" in reason:
        return "ERROR"
    return "OK"

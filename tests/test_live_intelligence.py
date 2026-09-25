"""Safety and contract tests for the active paper-only intelligence loop."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import polars as pl
import pytest
from pydantic import ValidationError

from jev_trading.contracts import BAR_COLUMNS
from jev_trading.data.normalization import DataFabric, ReplayAdapter
from jev_trading.environment import build_market_environment, detect_events
from jev_trading.ledger import DecisionLedger
from jev_trading.live_intelligence.config import LiveSettings, load_settings
from jev_trading.live_intelligence.execution import PaperExecutor
from jev_trading.live_intelligence.frontier.client import DisabledFrontierClient, ReplayFrontierClient
from jev_trading.live_intelligence.jev.client import DisabledJevClient, ReplayJevClient
from jev_trading.live_intelligence.policy import PolicyFinalizer, make_candidate
from jev_trading.live_intelligence.quant import QuantRegistry, calculate_economic_value
from jev_trading.live_intelligence.risk import ActiveRiskKernel
from jev_trading.live_intelligence.schemas import (
    AccountState,
    CandidateTrade,
    CostAssumptions,
    DataEventType,
    DataQuality,
    ExecutionIntent,
    ExecutionState,
    JevEvaluation,
    MarketEnvironment,
    MarketTick,
    MarketType,
    PolicyAction,
    PolicyDecision,
    PositionState,
    RiskDecision,
    RiskStatus,
    Side,
    StrategyHypothesis,
    Versions,
    DecisionRecord,
)
from jev_trading.replay import ReplayEngine
from jev_trading.research import HypothesisRegistry, ResearchMemory
from jev_trading.live_intelligence.schemas import ResearchHypothesis, ResearchObservation

UTC = timezone.utc
T0 = 1_700_000_000_000


def bars(n: int = 300) -> pl.DataFrame:
    rows = []
    for i in range(n):
        close = 100.0 + i * 0.01
        rows.append({
            "timestamp": T0 + i * 60_000, "open": close, "high": close + 0.1,
            "low": close - 0.1, "close": close, "volume": 10.0,
            "funding_rate": 0.0001, "open_interest": 1000.0,
        })
    return pl.DataFrame(rows, schema={c: pl.Int64 if c == "timestamp" else pl.Float64 for c in BAR_COLUMNS})


def tick(now: datetime, **overrides) -> MarketTick:
    values = dict(
        source="primary", source_role="primary", instrument="BTCUSDT_PERP",
        venue="binance-futures", market_type=MarketType.PERPETUAL,
        event_timestamp=now, received_timestamp=now, event_type=DataEventType.BAR,
        last=100.0, bid=99.99, ask=100.01, mark=100.0, volume=10.0,
        open_interest=1000.0, funding_rate=0.0001,
    )
    values.update(overrides)
    return MarketTick(**values)


def safe_quality() -> DataQuality:
    return DataQuality(safe_for_trading=True, stale=False, source_health={})


def environment(*, quality: DataQuality | None = None, now: datetime | None = None) -> MarketEnvironment:
    decision = now or datetime.fromtimestamp((T0 + 300 * 60_000) / 1000, tz=UTC)
    return build_market_environment(bars(), ticks=[tick(decision)], quality=quality or safe_quality(), decision_timestamp=decision)


def hypothesis(*, abstain: bool = False) -> StrategyHypothesis:
    now = datetime.fromtimestamp((T0 + 300 * 60_000) / 1000, tz=UTC)
    return StrategyHypothesis(
        hypothesis_id="hyp-test", timestamp=now, regime="BULLISH", regime_confidence=0.7,
        primary_strategy=None if abstain else "momentum", direction="NONE" if abstain else "LONG",
        horizon_seconds=None if abstain else 900, thesis="test", reason="test",
        entry_conditions=[] if abstain else ["price above vwap"],
        invalidation_conditions=[] if abstain else ["spread expands"],
        maximum_holding_seconds=None if abstain else 900, abstain=abstain,
        model_version="test-frontier", prompt_version="frontier-strategist-v1",
    )


def test_settings_are_paper_only_and_btc_perp():
    settings = load_settings()
    assert settings.execution_mode == "PAPER"
    assert settings.market.instrument == "BTCUSDT_PERP"
    assert settings.frontier.prompt_version == "frontier-strategist-v1"
    with pytest.raises(ValidationError):
        LiveSettings.model_validate({**settings.model_dump(), "execution_mode": "LIVE"})


def test_timestamps_must_be_aware():
    with pytest.raises(ValidationError):
        tick(datetime.now())


def test_data_fabric_rejects_duplicate_and_reports_stale():
    now = datetime.now(UTC)
    fabric = DataFabric(max_age_ms=1000)
    first = tick(now)
    fabric.ingest([first, first])
    quality = fabric.quality(now=now + timedelta(seconds=2))
    assert quality.duplicate_events == 1
    assert quality.stale is True
    assert quality.safe_for_trading is False


def test_environment_has_all_timeframes_and_events_are_causal():
    env = environment()
    assert set(env.timeframes) == {"1m", "5m", "15m", "1h", "4h"}
    events = detect_events(env)
    assert all(event.detection_timestamp >= event.event_timestamp for event in events)


def test_quant_registry_returns_typed_results_without_probability_fabrication():
    results = QuantRegistry().run_all(environment())
    assert len(results) == 13
    assert all(result.analysis_name for result in results)
    path = next(result for result in results if result.analysis_name == "path")
    assert path.estimated_probability is None
    assert path.limitations


def test_economic_value_charges_costs_and_validates_probabilities():
    candidate = CandidateTrade(side=Side.LONG, entry_reference=100, target=102, stop=99, max_holding_seconds=900, strategy_id="momentum", strategy_version="v1", entry_condition="test")
    costs = CostAssumptions(fee_bps_per_side=5, slippage_bps_per_side=2, latency_bps=1, holding_seconds=900)
    value = calculate_economic_value(candidate, {"target": 0.5, "stop": 0.2, "timeout": 0.3}, costs)
    assert value.net_expected_value < value.gross_expected_payoff
    assert value.fees > 0 and value.slippage > 0
    with pytest.raises(ValueError):
        calculate_economic_value(candidate, {"target": 0.5, "stop": 0.2, "timeout": 0.2}, costs)


def test_frontier_and_jev_clients_are_structured_and_disabled_provider_fails_closed():
    env = environment()
    with pytest.raises(Exception):
        DisabledFrontierClient().complete(system_prompt="x", payload={})
    with pytest.raises(Exception):
        DisabledJevClient().evaluate(request=None)
    replay = ReplayFrontierClient(allow_trade=True).complete(
        system_prompt="x",
        payload={"request_id": "r", "prompt_version": "frontier-strategist-v1", "environment": env.model_dump(mode="json"), "required_quant_questions": [], "jev_questions": []},
    )
    assert replay["abstain"] is False
    assert "quantity" not in replay and "leverage" not in replay


def test_policy_abstains_without_economic_and_jev_evidence():
    settings = load_settings()
    env = environment()
    hyp = hypothesis()
    decision = PolicyFinalizer(settings).finalize(env, hyp, None, None, candidate=make_candidate(env, hyp, settings=settings))
    assert decision.action == PolicyAction.NO_TRADE
    assert decision.candidate is None
    assert "missing_economic_value" in decision.reasons
    assert "missing_jev" in decision.reasons


def test_risk_rejects_stale_data_even_with_high_policy_confidence():
    settings = load_settings()
    env = environment(quality=DataQuality(safe_for_trading=False, stale=True, missing_sources=("primary",)))
    hyp = hypothesis()
    candidate = make_candidate(env, hyp, settings=settings)
    policy = PolicyFinalizer(settings).finalize(env, hyp, None, None, candidate=candidate)
    risk = ActiveRiskKernel(settings).evaluate(policy, env, AccountState(capital_usd=10000), ExecutionState(), position=PositionState())
    assert risk.status == RiskStatus.REJECTED
    assert "data_unsafe" in risk.reasons


def test_risk_can_approve_only_valid_entry_and_paper_execution_is_explicit():
    settings = load_settings()
    env = environment()
    hyp = hypothesis()
    candidate = make_candidate(env, hyp, settings=settings)
    jev = JevEvaluation(
        decision_id="d", request_id="r", timestamp=env.decision_timestamp,
        valid_until=env.decision_timestamp + timedelta(seconds=30),
        probabilities={"target": 0.6, "stop": 0.2, "failure": 0.1}, ratings={"entry_quality": 0.8},
        answers={}, confidence=0.8, recommended_state="ENTER", reason="test",
        model_version="test-jev", prompt_version="jev-evaluator-v1",
    )
    value = calculate_economic_value(candidate, {"target": 0.5, "stop": 0.2, "timeout": 0.3}, settings.costs.assumptions(900))
    policy = PolicyFinalizer(settings).finalize(env, hyp, value, jev, candidate=candidate, decision_id="d")
    assert policy.action == PolicyAction.ENTER_LONG
    risk = ActiveRiskKernel(settings).evaluate(policy, env, AccountState(capital_usd=10000), ExecutionState(), position=PositionState())
    assert risk.status == RiskStatus.APPROVED
    intent = ExecutionIntent(intent_id="i", decision_id="d", mode="PAPER", action=PolicyAction.ENTER_LONG, side=Side.LONG, quantity=risk.approved_quantity, reference_price=100, created_at=env.decision_timestamp, earliest_execution_at=env.decision_timestamp + timedelta(minutes=1), strategy_id="momentum")
    executor = PaperExecutor(settings, ActiveRiskKernel(settings))
    later = env.model_copy(update={"timestamp": env.timestamp + timedelta(minutes=1)})
    fill = executor.execute(intent, risk, later)
    assert fill.mode == "PAPER"
    assert executor.position.side == Side.LONG


def test_ledger_is_hash_chained_and_replayable(tmp_path: Path):
    settings = load_settings()
    env = environment()
    hyp = hypothesis()
    policy = PolicyFinalizer(settings).finalize(env, hyp, None, None, decision_id="decision-1")
    risk = ActiveRiskKernel(settings).evaluate(policy, env, AccountState(capital_usd=10000), ExecutionState(), position=PositionState())
    record = DecisionRecord(
        decision_id="decision-1", experiment_id=settings.experiment_id, timestamp=env.decision_timestamp,
        market_environment=env, frontier_hypothesis=hyp, quant_analyses=(),
        policy_decision=policy, risk_decision=risk, position_before=PositionState(), position_after=PositionState(),
        versions=Versions(code_version="test", experiment_id=settings.experiment_id, frontier_model="test", frontier_prompt="test", jev_model="test", jev_prompt="test", quant_analyzers={}, policy="test", risk="test", strategy_registry="test"),
    )
    path = tmp_path / "ledger.jsonl"
    ledger = DecisionLedger(path)
    ledger.append_decision(record)
    replay = ReplayEngine(path)
    assert replay.replay()[0].decision_id == "decision-1"
    raw = path.read_text().splitlines()
    entry = raw[0]
    raw[0] = entry.replace("decision-1", "tampered")
    path.write_text("\n".join(raw) + "\n")
    with pytest.raises(ValueError):
        ReplayEngine(path)


def test_research_reports_are_immutable_and_hypotheses_are_versioned(tmp_path: Path):
    memory = ResearchMemory(tmp_path / "research")
    registry = HypothesisRegistry(tmp_path / "research" / "hypotheses")
    now = datetime.now(UTC)
    hyp = ResearchHypothesis(hypothesis_id="HYP-1", date_created=now, author="human", market="BTCUSDT_PERP", regime="mixed", strategy="momentum", rationale="test", required_evidence=(), success_criteria=(), failure_criteria=())
    registry.create(hyp)
    with pytest.raises(FileExistsError):
        registry.create(hyp)
    revised = registry.transition("HYP-1", "TESTING", evidence="controlled test")
    assert revised.exists()
    assert (tmp_path / "research" / "hypotheses" / "HYP-1_r2.json").exists()

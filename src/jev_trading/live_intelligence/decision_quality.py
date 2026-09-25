"""Stage 6/7: causal candidate-level decision quality and paired A/B harness.

Strictly non-executing: outcomes are observed from history AFTER a candidate
timestamp; they are never fed back into the model input.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import polars as pl

from jev_trading.contracts import BAR_COLUMNS
from jev_trading.data.normalization import DataFabric
from jev_trading.environment import build_market_environment
from jev_trading.live_intelligence.config import LiveSettings, ProviderConfig
from jev_trading.live_intelligence.frontier.client import FrontierClientFactory
from jev_trading.live_intelligence.frontier.strategist import FrontierStrategist, load_prompt
from jev_trading.live_intelligence.jev.client import JevClientFactory
from jev_trading.live_intelligence.jev.evaluator import JevEvaluator
from jev_trading.live_intelligence.policy import PolicyFinalizer, make_candidate
from jev_trading.live_intelligence.quant import QuantRegistry, analyze_path, build_completed_path_samples, calculate_economic_value
from jev_trading.live_intelligence.risk import ActiveRiskKernel
from jev_trading.live_intelligence.schemas import (
    AccountState,
    DataQuality,
    ExecutionState,
    JevRequest,
    MarketTick,
    PositionState,
    QuantEvidence,
    StrategyHypothesis,
)

HISTORY = "data/btcusdt_1m.parquet"


def _ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _dt(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


def load_history(path: str = HISTORY) -> pl.DataFrame:
    return pl.read_parquet(path)


def causal_window(frame: pl.DataFrame, at: datetime, warmup: int = 3000) -> pl.DataFrame:
    """Bars strictly at or before `at`. Future data cannot reach the model."""
    return frame.filter(pl.col("timestamp") <= _ms(at)).tail(warmup)


def future_outcome(
    frame: pl.DataFrame, at: datetime, side: str, horizon_minutes: int,
) -> dict[str, Any]:
    """Observed outcome AFTER `at`; evaluation-only, never model input."""
    ahead = frame.filter(
        (pl.col("timestamp") > _ms(at)) & (pl.col("timestamp") <= _ms(at + timedelta(minutes=horizon_minutes)))
    )
    if ahead.is_empty():
        return {"realized_return": None, "mfe": None, "mae": None, "bars_ahead": 0}
    entry = frame.filter(pl.col("timestamp") <= _ms(at))["close"][-1]
    forward = [(close - entry) / entry for close in ahead["close"].to_list()]
    if side == "SHORT":
        forward = [-value for value in forward]
    return {
        "realized_return": forward[-1],
        "mfe": max(forward),
        "mae": min(forward),
        "bars_ahead": ahead.height,
    }


@dataclass
class Candidate:
    candidate_id: str
    timestamp: datetime
    environment: Any
    quant_hypothesis: StrategyHypothesis
    candidate: Any
    evidence: QuantEvidence
    economic_value: Any
    split: str
    extras: dict[str, Any] = field(default_factory=dict)


def _quant_hypothesis(environment, regime, request_id) -> StrategyHypothesis:
    """Mirrors ShadowRunner._quant_hypothesis so the baseline arm is identical."""
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


def build_candidate_at(frame: pl.DataFrame, at: datetime, settings: LiveSettings) -> Candidate | None:
    """Generate a candidate from causal data only. Returns None if not tradeable."""
    bars = causal_window(frame, at, warmup=settings.market.warmup_bars)
    if bars.height < settings.market.warmup_bars:
        return None
    quality = DataQuality(safe_for_trading=True, stale=False)
    environment = build_market_environment(bars, ticks=[], quality=quality, position=PositionState(), decision_timestamp=at)
    if environment.price.last is None or not environment.price.last:
        return None
    registry = QuantRegistry()
    analyses = [r for r in registry.run_all(environment) if r.analysis_name not in {"path", "opportunity"}]
    request_id = f"q-{int(at.timestamp())}"
    hypothesis = _quant_hypothesis(environment, _regime(environment), request_id)
    candidate = make_candidate(environment, hypothesis, settings=settings)
    if candidate is None:
        return None
    target_fraction = abs(candidate.target / candidate.entry_reference - 1.0)
    stop_fraction = abs(candidate.stop / candidate.entry_reference - 1.0)
    horizon_minutes = max(1, candidate.max_holding_seconds // 60)
    generated = build_completed_path_samples(
        bars, side=candidate.side, target_fraction=target_fraction, stop_fraction=stop_fraction,
        horizon_minutes=horizon_minutes, max_samples=500,
    )
    economic_value = None
    path_result = None
    if generated:
        path_result = analyze_path(
            environment, generated, side=candidate.side, target_fraction=target_fraction,
            stop_fraction=stop_fraction, horizon_minutes=horizon_minutes,
            horizon_seconds=candidate.max_holding_seconds,
        )
        if path_result.path_probabilities is not None:
            economic_value = calculate_economic_value(
                candidate, path_result.path_probabilities,
                settings.costs.assumptions(candidate.max_holding_seconds, environment.derivatives.funding or 0.0),
                timeout_return_fraction=path_result.expected_timeout_return or 0.0,
                expected_duration_seconds=path_result.expected_duration_seconds,
                sample_size=path_result.empirical_sample_size or 0,
            )
    evidence = QuantEvidence(
        timestamp=environment.decision_timestamp, regime=environment.timeframes["1h"].trend_direction,
        regime_confidence=0.5, analyzer_results=tuple(analyses), path=path_result,
        probabilities=path_result.path_probabilities if path_result else None,
        economic_value=economic_value,
        cost_assumptions=economic_value.cost_assumptions if economic_value else None,
        uncertainty=path_result.uncertainty if path_result else None,
        sample_size=path_result.empirical_sample_size if path_result else 0,
        analyzer_versions={r.analysis_name: r.analyzer_version for r in analyses},
    )
    return Candidate(
        candidate_id=f"c-{int(at.timestamp())}", timestamp=at, environment=environment,
        quant_hypothesis=hypothesis, candidate=candidate, evidence=evidence,
        economic_value=economic_value, split=at.strftime("%Y"),
    )


def _regime(environment):
    from jev_trading.environment import assess_regime
    return assess_regime(environment)


def _openai(config: ProviderConfig) -> ProviderConfig:
    return config.model_copy(update={"provider": "openai_compatible"})


def evaluate_candidate(
    candidate: Candidate, settings: LiveSettings, *, with_intelligence: bool,
) -> dict[str, Any]:
    """Score one candidate under QUANT_ONLY or QUANT_FRONTIER_JEV."""
    policy = PolicyFinalizer(settings)
    risk_kernel = ActiveRiskKernel(settings)
    environment = candidate.environment

    frontier_hypothesis = candidate.quant_hypothesis
    jev_evaluation = None
    frontier_decision = "quant"
    jev_decision = "n/a"
    frontier_error = jev_error = None
    latency_ms = 0.0

    if with_intelligence:
        import time as _time
        started = _time.perf_counter()
        try:
            client = FrontierClientFactory.create(_openai(settings.frontier.provider))
            strategist = FrontierStrategist(
                client, prompt=load_prompt("src/jev_trading/live_intelligence/" + settings.frontier.prompt_file),
                prompt_version=settings.frontier.prompt_version, settings=settings,
            )
            frontier_hypothesis = strategist.generate(
                environment, request_id=candidate.candidate_id, quant_evidence=candidate.evidence,
            )
            frontier_decision = frontier_hypothesis.direction.value
        except Exception as exc:
            frontier_error = type(exc).__name__
        if candidate.candidate is not None and not frontier_hypothesis.abstain:
            try:
                request = JevRequest(
                    request_id=candidate.candidate_id, timestamp=environment.decision_timestamp,
                    environment=environment, frontier_hypothesis=frontier_hypothesis,
                    quant_evidence=candidate.evidence, candidate_trade=candidate.candidate,
                    questions=(), prompt_version=settings.jev.prompt_version,
                )
                jev_client = JevClientFactory.create(
                    _openai(settings.jev.provider),
                    prompt=load_prompt("src/jev_trading/live_intelligence/" + settings.jev.prompt_file),
                )
                evaluator = JevEvaluator(
                    jev_client, prompt=load_prompt("src/jev_trading/live_intelligence/" + settings.jev.prompt_file),
                    settings=settings, prompt_version=settings.jev.prompt_version,
                )
                jev_evaluation = evaluator.evaluate(request)
                jev_decision = jev_evaluation.recommended_state.value
            except Exception as exc:
                jev_error = type(exc).__name__
        latency_ms = (_time.perf_counter() - started) * 1000

    decision = policy.finalize(
        environment, frontier_hypothesis, candidate.economic_value, jev_evaluation,
        position=PositionState(), candidate=candidate.candidate,
        require_jev=with_intelligence,
    )
    risk = risk_kernel.evaluate(
        decision, environment, AccountState(capital_usd=settings.paper.capital_usd),
        ExecutionState(), position=PositionState(),
    )
    return {
        "candidate_id": candidate.candidate_id,
        "timestamp": candidate.timestamp.isoformat(),
        "split": candidate.split,
        "strategy": candidate.candidate.strategy_id if candidate.candidate else None,
        "side": candidate.candidate.side.value if candidate.candidate else None,
        "frontier_decision": frontier_decision,
        "frontier_abstain": bool(frontier_hypothesis.abstain),
        "jev_decision": jev_decision,
        "policy_action": decision.action.value,
        "policy_eligible": decision.action.value in {"ENTER_LONG", "ENTER_SHORT"},
        "risk_status": risk.status.value,
        "net_expected_value": candidate.economic_value.net_expected_value if candidate.economic_value else None,
        "frontier_error": frontier_error,
        "jev_error": jev_error,
        "latency_ms": latency_ms,
    }


def sample_timestamps(frame: pl.DataFrame, count: int, *, end: datetime, step_minutes: int) -> list[datetime]:
    """Deterministic, evenly spaced candidate timestamps before `end`."""
    if end > _dt(frame["timestamp"].max() or 0):
        end = _dt(frame["timestamp"].max() or 0)
    start = max(_dt(frame["timestamp"].min() or 0), end - timedelta(minutes=step_minutes * count))
    span = (end - start).total_seconds()
    return [start + timedelta(seconds=span * (i + 1) / count) for i in range(count)]


def attach_outcomes(frame: pl.DataFrame, rows: list[dict[str, Any]], horizon_minutes: int) -> list[dict[str, Any]]:
    """Join observed future outcomes; evaluation-only, never model input."""
    for row in rows:
        side = row.get("side") or "LONG"
        row["outcome"] = future_outcome(frame, _dt(int(datetime.fromisoformat(row["timestamp"]).timestamp() * 1000)), side, horizon_minutes)
    return rows


def summarize(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    """Selection-quality summary for a set of rows; not a profitability claim."""
    selected = [r for r in rows if r[key]]
    rejected = [r for r in rows if not r[key]]

    def stats(subset):
        returns = [r["outcome"]["realized_return"] for r in subset if r["outcome"]["realized_return"] is not None]
        if not returns:
            return {"n": 0}
        ordered = sorted(returns)
        return {
            "n": len(returns),
            "mean_return": sum(returns) / len(returns),
            "median_return": ordered[len(ordered) // 2],
            "mean_mfe": sum(r["outcome"]["mfe"] for r in subset if r["outcome"]["mfe"] is not None) / max(1, len(subset)),
            "mean_mae": sum(r["outcome"]["mae"] for r in subset if r["outcome"]["mae"] is not None) / max(1, len(subset)),
        }

    return {"total": len(rows), "selected": stats(selected), "rejected": stats(rejected)}


def run_decision_quality(
    settings: LiveSettings, *, count: int = 12, horizon_minutes: int = 15,
    eval_end: datetime | None = None, step_minutes: int = 4320,
    with_intelligence: bool = True, delay: float = 0.0, history: str = HISTORY,
) -> dict[str, Any]:
    """Paired baseline vs treatment over one shared candidate stream. No execution."""
    frame = load_history(history)
    eval_end = eval_end or _dt(frame["timestamp"].max() or 0)
    stamps = sample_timestamps(frame, count, end=eval_end, step_minutes=step_minutes)

    candidates = []
    for at in stamps:
        candidate = build_candidate_at(frame, at, settings)
        if candidate is not None:
            candidates.append(candidate)

    baseline = [evaluate_candidate(c, settings, with_intelligence=False) for c in candidates]
    treatment = [evaluate_candidate(c, settings, with_intelligence=with_intelligence) for c in candidates]

    attach_outcomes(frame, baseline, horizon_minutes)
    attach_outcomes(frame, treatment, horizon_minutes)

    paired = []
    for base, treat in zip(baseline, treatment):
        paired.append({
            "candidate_id": base["candidate_id"],
            "timestamp": base["timestamp"],
            "side": base["side"],
            "baseline_eligible": base["policy_eligible"],
            "treatment_eligible": treat["policy_eligible"],
            "frontier_decision": treat["frontier_decision"],
            "frontier_abstain": treat["frontier_abstain"],
            "jev_decision": treat["jev_decision"],
            "realized_return": treat["outcome"]["realized_return"],
            "mfe": treat["outcome"]["mfe"],
            "mae": treat["outcome"]["mae"],
            "changed": base["policy_eligible"] != treat["policy_eligible"],
            "frontier_error": treat["frontier_error"],
            "jev_error": treat["jev_error"],
        })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "experiment_id": settings.experiment_id,
        "horizon_minutes": horizon_minutes,
        "candidate_count": len(candidates),
        "with_intelligence": with_intelligence,
        "intelligence_errors": {
            "frontier": sum(1 for r in treatment if r["frontier_error"]),
            "jev": sum(1 for r in treatment if r["jev_error"]),
        },
        "baseline": summarize(baseline, "policy_eligible"),
        "treatment": summarize(treatment, "policy_eligible"),
        "paired": paired,
        "execution": "NOT_INVOKED",
    }

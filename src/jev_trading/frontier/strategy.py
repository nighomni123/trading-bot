"""Strategy generator: WorldDigest -> ParameterArtifact (versioned, experiment-gated)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from jev_trading.frontier.world_model import WorldDigest, Regime
from jev_trading.jev.client import Question

if TYPE_CHECKING:
    from jev_trading.frontier.world_model import PNLStats


class QuantParams(BaseModel):
    """Quant model parameters."""
    p_up_threshold: float = Field(..., ge=0.0, le=1.0)
    min_edge_over_cost: float = Field(..., ge=0.0)
    lgbm_params: dict = Field(default_factory=dict)


class JevParams(BaseModel):
    """Jev question set and answer weighting."""
    questions: list[Question] = Field(default_factory=list)
    answer_weights: dict[str, float] = Field(default_factory=dict)
    trade_ok_min: float = Field(..., ge=0.0, le=1.0)
    failure_max: float = Field(..., ge=0.0, le=1.0)


class PolicyParams(BaseModel):
    """Policy engine thresholds (mirrors configs/policy.json structure)."""
    enter_long: dict = Field(default_factory=dict)
    enter_short: dict = Field(default_factory=dict)
    exit: dict = Field(default_factory=dict)


class StrategyMetadata(BaseModel):
    """Audit trail for the generated artifact."""
    hypothesis: str
    regime_target: str
    expected_impact: str
    rationale: list[str] = Field(default_factory=list)
    generated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ParameterArtifact(BaseModel):
    """Versioned strategy artifact produced by the frontier layer."""
    quant: QuantParams
    jev: JevParams
    policy: PolicyParams
    metadata: StrategyMetadata


class StrategyGenerator:
    """Generates regime-aware parameter artifacts from WorldDigest."""

    def __init__(
        self,
        baseline_quant_path: str = "models/",
        baseline_policy_path: str = "configs/policy.json",
        baseline_questions_path: str = "configs/jev_questions.json",
    ) -> None:
        self.baseline_quant_path = Path(baseline_quant_path)
        self.baseline_policy_path = Path(baseline_policy_path)
        self.baseline_questions_path = Path(baseline_questions_path)
        self._baseline_policy: dict | None = None
        self._baseline_questions: list[Question] | None = None

    def _load_baselines(self) -> tuple[dict, list[Question]]:
        """Lazy-load baseline configs."""
        if self._baseline_policy is None:
            if self.baseline_policy_path.is_absolute():
                p = self.baseline_policy_path
            else:
                p = Path(__file__).resolve().parents[3] / self.baseline_policy_path
            self._baseline_policy = json.loads(p.read_text())

        if self._baseline_questions is None:
            if self.baseline_questions_path.is_absolute():
                p = self.baseline_questions_path
            else:
                p = Path(__file__).resolve().parents[3] / self.baseline_questions_path
            self._baseline_questions = [Question.model_validate(item) for item in json.loads(p.read_text())]

        return self._baseline_policy, self._baseline_questions

    def _regime_adjustments(self, digest: WorldDigest) -> dict:
        """Compute parameter adjustments based on regime and PnL."""
        reg = digest.regime
        pnl = digest.pnl
        cal_drift = digest.calibration_drift

        adjustments = {
            "p_up_threshold_delta": 0.0,
            "min_edge_over_cost_delta": 0.0,
            "trade_ok_min_delta": 0.0,
            "failure_max_delta": 0.0,
            "add_questions": [],
            "rationale": [],
        }

        # High volatility / trending: tighten thresholds, demand more confidence
        if reg.volatility == "high" or reg.trend == "trending":
            adjustments["p_up_threshold_delta"] += 0.05
            adjustments["min_edge_over_cost_delta"] += 0.5
            adjustments["rationale"].append("High vol/trending regime: require stronger signals")

        # Low volatility / flat: lower threshold but be more selective on Jev
        if reg.volatility == "low" and reg.trend == "flat":
            adjustments["p_up_threshold_delta"] -= 0.03
            adjustments["trade_ok_min_delta"] += 0.05  # demand more Jev confidence
            adjustments["rationale"].append("Low vol/flat regime: lower quant bar, raise Jev bar")

        # Funding extremes: add funding-aware questions, tighten failure
        if reg.funding in ("extreme_long", "extreme_short"):
            adjustments["failure_max_delta"] -= 0.1
            adjustments["add_questions"].append("funding_extreme")
            adjustments["rationale"].append(f"Funding {reg.funding}: tighten failure, add funding question")

        # Cost erosion: demand higher edge over cost
        if pnl.cost_ratio > 0.6:
            adjustments["min_edge_over_cost_delta"] += 1.0
            adjustments["rationale"].append(f"Cost ratio {pnl.cost_ratio:.1%}: demand higher edge")

        # Calibration drift: if model drifting, be more conservative
        if cal_drift > 0.1:
            adjustments["p_up_threshold_delta"] += 0.03
            adjustments["rationale"].append(f"Calibration drift {cal_drift:.2f}: raise quant threshold")

        # Negative edge: critical — halt via overseer, but here we tighten aggressively
        if pnl.net_per_trade < 0 and pnl.cost_ratio > 0.5:
            adjustments["p_up_threshold_delta"] += 0.1
            adjustments["min_edge_over_cost_delta"] += 2.0
            adjustments["rationale"].append("Negative net PnL with high costs: aggressive tightening")

        return adjustments

    def generate(self, digest: WorldDigest) -> ParameterArtifact:
        """Produce a regime-aware ParameterArtifact."""
        baseline_policy, baseline_questions = self._load_baselines()
        adj = self._regime_adjustments(digest)

        # Quant params
        base_p_up = baseline_policy.get("enter_long", {}).get("p_up_15", 0.60)
        base_edge = baseline_policy.get("enter_long", {}).get("min_edge_over_cost", 2.0)
        quant = QuantParams(
            p_up_threshold=max(0.0, min(1.0, base_p_up + adj["p_up_threshold_delta"])),
            min_edge_over_cost=max(0.0, base_edge + adj["min_edge_over_cost_delta"]),
            lgbm_params={"num_leaves": 31, "n_estimators": 200, "learning_rate": 0.05, "verbose": -1},
        )

        # Jev params
        base_trade_ok = baseline_policy.get("enter_long", {}).get("jev_trade_ok", 0.75)
        base_failure = baseline_policy.get("enter_long", {}).get("jev_failure_max", 0.35)

        # Build question set with optional additions
        questions = list(baseline_questions)
        if "funding_extreme" in adj["add_questions"]:
            questions.append(Question(
                name="funding_extreme",
                kind="yesno",
                instructions="Is funding rate at an extreme that typically precedes a reversal?",
            ))

        # Answer weights: default equal, could be learned
        answer_weights = {q.name: 1.0 for q in questions}

        jev = JevParams(
            questions=questions,
            answer_weights=answer_weights,
            trade_ok_min=max(0.0, min(1.0, base_trade_ok + adj["trade_ok_min_delta"])),
            failure_max=max(0.0, min(1.0, base_failure + adj["failure_max_delta"])),
        )

        # Policy params (mirror structure)
        policy = PolicyParams(
            enter_long={
                "p_up_15": quant.p_up_threshold,
                "jev_trade_ok": jev.trade_ok_min,
                "jev_failure_max": jev.failure_max,
                "min_edge_over_cost": quant.min_edge_over_cost,
            },
            enter_short={
                "p_dn_15": quant.p_up_threshold,  # symmetric for now
                "jev_trade_ok": jev.trade_ok_min,
                "jev_failure_max": jev.failure_max,
                "min_edge_over_cost": quant.min_edge_over_cost,
            },
            exit={
                "p_up_below": 0.45,
                "p_dn_below": 0.45,
            },
        )

        # Metadata
        regime_str = f"{digest.regime.volatility}_{digest.regime.trend}_{digest.regime.funding}"
        metadata = StrategyMetadata(
            hypothesis=f"Regime-adaptive parameters for {regime_str} improve risk-adjusted returns",
            regime_target=regime_str,
            expected_impact="Higher Sharpe via regime-aware threshold tuning",
            rationale=adj["rationale"],
        )

        return ParameterArtifact(
            quant=quant,
            jev=jev,
            policy=policy,
            metadata=metadata,
        )

    def save_artifact(self, artifact: ParameterArtifact, exp_dir: str) -> Path:
        """Write frontier_strategy.yaml to experiments/EXP-xxx/."""
        exp_path = Path(exp_dir)
        if not exp_path.is_absolute():
            exp_path = Path(__file__).resolve().parents[3] / exp_path
        exp_path.mkdir(parents=True, exist_ok=True)

        out_path = exp_path / "frontier_strategy.yaml"
        # Use model_dump for YAML-compatible dict
        out_path.write_text(json.dumps(artifact.model_dump(), indent=2))
        return out_path

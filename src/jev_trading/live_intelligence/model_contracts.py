"""Minimal model-facing contracts and adapters to domain objects."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .schemas import (
    CandidateTrade,
    Direction,
    JevEvaluation,
    JevRequest,
    JevState,
    StrategyHypothesis,
)


class ModelContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class FrontierModelDecision(ModelContract):
    regime: str = Field(min_length=1)
    strategy_family: str | None = None
    direction: Direction = Direction.NONE
    confidence: float = Field(ge=0, le=1)
    abstain: bool
    thesis: str = Field(min_length=1)
    entry_conditions: tuple[str, ...] = ()
    invalidation_conditions: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_direction(self) -> "FrontierModelDecision":
        if not self.abstain and (self.strategy_family is None or self.direction == Direction.NONE):
            raise ValueError("non-abstaining Frontier decision requires strategy_family and direction")
        if self.abstain and self.direction != Direction.NONE:
            raise ValueError("abstaining Frontier decision must use direction NONE")
        return self


class JevModelDecision(ModelContract):
    recommended_state: JevState
    abstain: bool
    confidence: float = Field(ge=0, le=1)
    entry_quality: float = Field(ge=0, le=1)
    failure_risk: float = Field(ge=0, le=1)
    liquidity_quality: float | None = Field(default=None, ge=0, le=1)
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_abstention(self) -> "JevModelDecision":
        if self.abstain and self.recommended_state != JevState.NO_TRADE:
            raise ValueError("abstaining Jev decision must be NO_TRADE")
        return self


def model_tool(name: str, description: str, model: type[ModelContract]) -> dict[str, Any]:
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})

    def inline(value):
        if isinstance(value, dict):
            ref = value.get("$ref")
            if ref and ref.startswith("#/$defs/"):
                target = dict(definitions[ref.split("/")[-1]])
                target.update({key: inline(item) for key, item in value.items() if key != "$ref"})
                return inline(target)
            return {key: inline(item) for key, item in value.items()}
        if isinstance(value, list):
            return [inline(item) for item in value]
        return value

    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": inline(schema),
        },
    }


FRONTIER_TOOL = model_tool(
    "submit_strategy_hypothesis",
    "Submit a strategic hypothesis for a downstream deterministic trading system. Use only supplied market and quant evidence. Do not determine size, leverage, orders, execution, or risk limits. Abstain when evidence is insufficient.",
    FrontierModelDecision,
)
JEV_TOOL = model_tool(
    "submit_jev_evaluation",
    "Submit a bounded evaluation of the supplied candidate for deterministic policy and risk. Do not place orders, determine size, or override risk. Abstain when evidence is insufficient.",
    JevModelDecision,
)


def frontier_to_domain(
    decision: FrontierModelDecision,
    *,
    environment,
    settings,
    request_id: str,
    provider: str,
    model: str,
    model_version: str,
    prompt_version: str,
    prompt_hash: str,
    interface_mode: str,
    tool_name: str | None,
) -> StrategyHypothesis:
    return StrategyHypothesis(
        hypothesis_id=request_id,
        timestamp=environment.decision_timestamp,
        regime=decision.regime,
        regime_confidence=decision.confidence,
        primary_strategy=decision.strategy_family,
        direction=decision.direction,
        horizon_seconds=None if decision.abstain else settings.policy.maximum_holding_seconds,
        thesis=decision.thesis,
        supporting_evidence=(),
        required_quant_questions=(),
        entry_conditions=decision.entry_conditions,
        invalidation_conditions=decision.invalidation_conditions,
        target_logic=None if decision.abstain else "deterministic_policy_atr_target",
        stop_logic=None if decision.abstain else "deterministic_policy_atr_stop",
        maximum_holding_seconds=None if decision.abstain else settings.policy.maximum_holding_seconds,
        abandon_conditions=("data_becomes_unsafe",),
        jev_questions=(),
        abstain=decision.abstain,
        reason=decision.thesis,
        conviction=decision.confidence,
        model_version=model_version,
        prompt_version=prompt_version,
        prompt_hash=prompt_hash,
        provider=provider,
        model=model,
        temperature=getattr(settings.frontier.provider, "temperature", None),
        max_output_tokens=getattr(settings.frontier.provider, "max_output_tokens", None),
        capabilities={
            "response_format": settings.frontier.provider.supports_response_format,
            "tool_calling": settings.frontier.provider.supports_tool_calling,
            "reasoning": settings.frontier.provider.supports_reasoning,
            "vision": settings.frontier.provider.supports_vision,
        },
        interface_mode=interface_mode,
        tool_name=tool_name,
    )


def jev_to_domain(
    decision: JevModelDecision,
    request: JevRequest,
    *,
    settings,
    provider: str,
    model: str,
    model_version: str,
    prompt_version: str,
    prompt_hash: str,
    interface_mode: str,
    tool_name: str | None,
) -> JevEvaluation:
    probabilities = dict(request.quant_evidence.probabilities or {})
    if decision.recommended_state == JevState.ENTER and not probabilities:
        raise ValueError("ENTER Jev decision requires deterministic quant probabilities")
    return JevEvaluation(
        decision_id=request.request_id,
        request_id=request.request_id,
        timestamp=request.timestamp,
        valid_until=request.timestamp + timedelta(seconds=min(30, settings.jev.validity_seconds)),
        probabilities=probabilities,
        target_probability=probabilities.get("target"),
        entry_quality=decision.entry_quality,
        failure_risk=decision.failure_risk,
        liquidity_quality=decision.liquidity_quality,
        ratings={},
        answers={},
        confidence=decision.confidence,
        recommended_state=decision.recommended_state,
        reason=decision.reason,
        model_version=model_version,
        prompt_version=prompt_version,
        prompt_hash=prompt_hash,
        provider=provider,
        model=model,
        temperature=settings.jev.provider.temperature,
        max_output_tokens=settings.jev.provider.max_output_tokens,
        capabilities={
            "response_format": settings.jev.provider.supports_response_format,
            "tool_calling": settings.jev.provider.supports_tool_calling,
            "reasoning": settings.jev.provider.supports_reasoning,
            "vision": settings.jev.provider.supports_vision,
        },
        interface_mode=interface_mode,
        tool_name=tool_name,
    )

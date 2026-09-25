"""Frontier strategist, context serialization, and output validation."""
from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import ValidationError

from .client import FrontierClient, FrontierUnavailable
from ..model_contracts import FrontierModelDecision, frontier_to_domain
from ..schemas import MarketEnvironment, QuantEvidence, StrategyHypothesis


def load_prompt(path: str | Path) -> str:
    return Path(path).read_text()


def _json_payload(
    environment: MarketEnvironment,
    *,
    request_id: str,
    required_quant_questions: list[dict],
    jev_questions: list[dict],
    prompt_version: str,
    regime: str,
    quant_evidence: QuantEvidence | None = None,
    strategy_performance: dict | None = None,
    research_memory: dict | None = None,
    system_health: dict | None = None,
) -> dict:
    return {
        "request_id": request_id,
        "prompt_version": prompt_version,
        "regime": regime,
        "environment": environment.model_dump(mode="json"),
        "quant_evidence": quant_evidence.model_dump(mode="json") if quant_evidence else None,
        "strategy_performance": strategy_performance or {},
        "research_memory": research_memory or {},
        "system_health": system_health or {},
        "required_quant_questions": required_quant_questions,
        "jev_questions": jev_questions,
    }


class FrontierStrategist:
    def __init__(self, client: FrontierClient, *, prompt: str, prompt_version: str, settings=None):
        self.client = client
        self.prompt = prompt
        self.prompt_version = prompt_version
        self.settings = settings
        self.prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    def generate(
        self,
        environment: MarketEnvironment,
        *,
        request_id: str,
        required_quant_questions=(),
        jev_questions=(),
        regime: str = "UNKNOWN",
        quant_evidence: QuantEvidence | None = None,
        strategy_performance: dict | None = None,
        research_memory: dict | None = None,
        system_health: dict | None = None,
    ) -> StrategyHypothesis:
        payload = _json_payload(
            environment, request_id=request_id,
            required_quant_questions=[item.model_dump(mode="json") for item in required_quant_questions],
            jev_questions=[item.model_dump(mode="json") for item in jev_questions],
            prompt_version=self.prompt_version, regime=regime,
            quant_evidence=quant_evidence,
            strategy_performance=strategy_performance,
            research_memory=research_memory,
            system_health=system_health,
        )
        try:
            raw = self.client.complete(system_prompt=self.prompt, payload=payload)
            if not isinstance(raw, dict):
                raise ValueError("Frontier response must be an object")
            interface = getattr(self.client, "last_interface", "json")
            if interface == "tool_call":
                from ..config import load_settings
                settings = self.settings or load_settings()
                decision = FrontierModelDecision.model_validate(raw)
                return frontier_to_domain(
                    decision, environment=environment, settings=settings, request_id=request_id,
                    provider=getattr(self.client, "provider", "openai_compatible"),
                    model=getattr(self.client, "model", self.client.model_version),
                    model_version=self.client.model_version, prompt_version=self.prompt_version,
                    prompt_hash=self.prompt_hash, interface_mode=interface,
                    tool_name=getattr(self.client, "last_tool_name", None),
                )
            if raw.get("hypothesis_id") != request_id:
                raise ValueError("Frontier hypothesis_id does not match request")
            if raw.get("prompt_version") not in (None, self.prompt_version):
                raise ValueError("Frontier prompt version mismatch")
            raw["prompt_version"] = self.prompt_version
            raw["prompt_hash"] = self.prompt_hash
            result = StrategyHypothesis.model_validate(raw)
            if result.timestamp != environment.decision_timestamp:
                raise ValueError("Frontier timestamp does not match decision time")
            return result.model_copy(update={
                "provider": getattr(self.client, "provider", "replay"),
                "model": getattr(self.client, "model", self.client.model_version),
                "temperature": getattr(self.client, "temperature", None),
                "max_output_tokens": getattr(self.client, "max_output_tokens", None),
                "capabilities": {
                    "response_format": getattr(self.client, "supports_response_format", False),
                    "tool_calling": getattr(self.client, "supports_tool_calling", False),
                    "reasoning": getattr(self.client, "supports_reasoning", False),
                    "vision": getattr(self.client, "supports_vision", False),
                },
                "interface_mode": interface,
                "tool_name": None,
            })
        except FrontierUnavailable:
            raise
        except (ValidationError, ValueError, TypeError, KeyError) as exc:
            raise FrontierUnavailable(
                f"invalid Frontier output: {exc}",
                provider=getattr(self.client, "provider", "openai_compatible"),
                model=getattr(self.client, "model", "unknown"),
                category="validation",
                request_id=request_id,
            ) from exc

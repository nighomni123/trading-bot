"""Jev request/response validation, short validity, and threshold enforcement."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from .client import JevClient
from ..schemas import JevEvaluation, JevRequest


class JevEvaluator:
    def __init__(
        self,
        client: JevClient,
        *,
        prompt: str,
        max_validity_seconds: int = 60,
        prompt_version: str = "jev-evaluator-v1",
        minimum_target_probability: float = 0.55,
        maximum_stop_probability: float = 0.45,
        minimum_entry_quality: float = 0.55,
        maximum_failure_probability: float = 0.45,
        minimum_liquidity_quality: float = 0.0,
    ):
        self.client = client
        self.max_validity_seconds = max_validity_seconds
        self.prompt_version = prompt_version
        self.prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        self.minimum_target_probability = minimum_target_probability
        self.maximum_stop_probability = maximum_stop_probability
        self.minimum_entry_quality = minimum_entry_quality
        self.maximum_failure_probability = maximum_failure_probability
        self.minimum_liquidity_quality = minimum_liquidity_quality

    def evaluate(self, request: JevRequest) -> JevEvaluation:
        if request.prompt_version != self.prompt_version:
            raise ValueError("Jev prompt version mismatch")
        if request.timestamp.tzinfo is None:
            raise ValueError("Jev request timestamp must be timezone-aware")
        response = self.client.evaluate(request)
        if response.request_id != request.request_id or response.decision_id != request.request_id:
            raise ValueError("Jev response identifiers do not match request")
        if response.prompt_version != self.prompt_version:
            raise ValueError("Jev response prompt version mismatch")
        if response.prompt_hash not in (None, self.prompt_hash):
            raise ValueError("Jev response prompt hash mismatch")
        if response.timestamp < request.timestamp:
            raise ValueError("Jev response timestamp precedes request")
        if response.valid_until > request.timestamp + timedelta(seconds=self.max_validity_seconds):
            raise ValueError("Jev validity window exceeds configured maximum")
        now = datetime.now(tz=timezone.utc)
        if response.valid_until <= max(now, request.timestamp):
            raise ValueError("Jev response is expired")
        response = response.model_copy(update={
            "prompt_hash": self.prompt_hash,
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
        })
        if response.recommended_state == "ENTER":
            required = {"target", "stop", "timeout"}
            if not required.issubset(response.probabilities):
                raise ValueError("ENTER Jev response requires target/stop/timeout probabilities")
            total = sum(response.probabilities[name] for name in required)
            if abs(total - 1.0) > 1e-8:
                raise ValueError("ENTER Jev path probabilities must sum to one")
            if response.target_probability is None or response.entry_quality is None or response.failure_risk is None:
                raise ValueError("ENTER Jev response requires target_probability, entry_quality, and failure_risk")
            if response.target_probability < self.minimum_target_probability:
                raise ValueError("Jev target probability is below configured minimum")
            if response.probabilities["stop"] > self.maximum_stop_probability:
                raise ValueError("Jev stop probability exceeds configured maximum")
            if response.entry_quality < self.minimum_entry_quality:
                raise ValueError("Jev entry quality is below configured minimum")
            if response.failure_risk > self.maximum_failure_probability:
                raise ValueError("Jev failure risk exceeds configured maximum")
            if response.liquidity_quality is not None and response.liquidity_quality < self.minimum_liquidity_quality:
                raise ValueError("Jev liquidity quality is below configured minimum")
        return response

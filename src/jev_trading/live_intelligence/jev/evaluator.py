"""Jev request/response validation and short validity enforcement."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol

from .client import JevClient
from ..schemas import JevEvaluation, JevRequest


class JevEvaluator:
    def __init__(self, client: JevClient, *, max_validity_seconds: int = 60, prompt_version: str = "jev-evaluator-v1"):
        self.client = client
        self.max_validity_seconds = max_validity_seconds
        self.prompt_version = prompt_version

    def evaluate(self, request: JevRequest) -> JevEvaluation:
        if request.prompt_version != self.prompt_version:
            raise ValueError("Jev prompt version mismatch")
        if request.timestamp.tzinfo is None:
            raise ValueError("Jev request timestamp must be timezone-aware")
        response = self.client.evaluate(request)
        if response.request_id != request.request_id:
            raise ValueError("Jev response request_id mismatch")
        if response.prompt_version != self.prompt_version:
            raise ValueError("Jev response prompt version mismatch")
        if response.valid_until > request.timestamp + timedelta(seconds=self.max_validity_seconds):
            raise ValueError("Jev validity window exceeds configured maximum")
        now = datetime.now(tz=timezone.utc)
        if response.valid_until <= max(now, request.timestamp):
            raise ValueError("Jev response is expired")
        if response.recommended_state == "ENTER":
            required = {"target", "stop", "timeout"}
            if not required.issubset(response.probabilities):
                raise ValueError("ENTER Jev response requires target/stop/timeout probabilities")
            total = sum(response.probabilities[name] for name in required)
            if abs(total - 1.0) > 1e-8:
                raise ValueError("ENTER Jev path probabilities must sum to one")
        return response

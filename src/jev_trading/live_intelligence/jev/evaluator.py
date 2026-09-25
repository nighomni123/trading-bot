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
        if response.valid_until <= datetime.now(tz=timezone.utc):
            raise ValueError("Jev response is expired")
        return response

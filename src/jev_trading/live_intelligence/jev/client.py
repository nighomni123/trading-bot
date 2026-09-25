"""Structured Jev provider boundary with bounded questions."""
from __future__ import annotations

import json
import os
from datetime import timedelta
from dataclasses import dataclass
from typing import Any, Protocol

import requests

from ..schemas import JevEvaluation, JevRequest


class JevClient(Protocol):
    model_version: str

    def evaluate(self, request: JevRequest) -> JevEvaluation: ...


class JevUnavailable(RuntimeError):
    pass


@dataclass
class DisabledJevClient:
    model_version: str = "disabled"

    def evaluate(self, request: JevRequest) -> JevEvaluation:
        raise JevUnavailable("Jev provider is disabled; opportunity is not tradeable")


@dataclass
class ReplayJevClient:
    model_version: str = "replay-jev-v1"

    def evaluate(self, request: JevRequest) -> JevEvaluation:
        # Explicit test double. It abstains unless the recorded evidence is
        # already sufficient; it is never a source of alpha evidence.
        economic = [item for item in request.quant_evidence if item.analysis_name == "opportunity"]
        ready = bool(economic and economic[0].evidence.get("economic_value"))
        timestamp = request.timestamp
        return JevEvaluation(
            decision_id=request.request_id, request_id=request.request_id, timestamp=timestamp,
            valid_until=timestamp + timedelta(seconds=60),
            probabilities={}, ratings={}, answers={"replay": "test-double"},
            confidence=0.0, recommended_state="NO_TRADE", reason="replay client abstains",
            model_version=self.model_version, prompt_version=request.prompt_version,
        ) if not ready else JevEvaluation(
            decision_id=request.request_id, request_id=request.request_id, timestamp=timestamp,
            valid_until=timestamp + timedelta(seconds=60),
            probabilities={}, ratings={}, answers={"replay": "test-double"},
            confidence=0.0, recommended_state="WAIT", reason="replay client has no empirical Jev calibration",
            model_version=self.model_version, prompt_version=request.prompt_version,
        )


@dataclass
class OpenAICompatibleJevClient:
    base_url: str
    model: str
    api_key_env: str = "JEV_LLM_API_KEY"
    model_version: str = "openai-compatible-jev-v1"
    timeout_seconds: float = 20.0
    max_output_tokens: int = 900

    def evaluate(self, request: JevRequest) -> JevEvaluation:
        key = os.environ.get(self.api_key_env)
        if not key:
            raise JevUnavailable(f"missing credential environment variable {self.api_key_env}")
        response = requests.post(
            self.base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": self.model, "temperature": 0,
                "response_format": {"type": "json_object"},
                "max_tokens": self.max_output_tokens,
                "messages": [
                    {"role": "system", "content": "You are the bounded Jev evaluator described in the request. Return JSON only."},
                    {"role": "user", "content": json.dumps(request.model_dump(mode="json"), sort_keys=True)},
                ],
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return JevEvaluation.model_validate(json.loads(response.json()["choices"][0]["message"]["content"]))

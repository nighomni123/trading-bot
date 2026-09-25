"""Jev provider boundary and OpenAI-compatible transport."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

import requests

from jev_trading.live_intelligence.config import ProviderConfig
from jev_trading.live_intelligence.schemas import JevEvaluation, JevRequest


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
        ready = request.quant_evidence.economic_value is not None
        state = "WAIT" if ready else "NO_TRADE"
        reason = "replay client has no empirical Jev calibration" if ready else "replay client abstains"
        return JevEvaluation(
            decision_id=request.request_id, request_id=request.request_id, timestamp=request.timestamp,
            valid_until=request.timestamp + timedelta(seconds=60), probabilities={}, ratings={},
            answers={"replay": "test-double"}, confidence=0.0, recommended_state=state,
            reason=reason, model_version=self.model_version, prompt_version=request.prompt_version,
        )


@dataclass
class OpenAICompatibleJevClient:
    base_url: str
    model: str
    prompt: str
    api_key_env: str = "OPENROUTER_API_KEY"
    model_version: str = "openai-compatible-jev-v1"
    timeout_seconds: float = 20.0
    max_output_tokens: int = 900
    temperature: float = 0.0
    max_retries: int = 2
    retry_backoff_seconds: float = 0.5

    def evaluate(self, request: JevRequest) -> JevEvaluation:
        key = os.environ.get(self.api_key_env)
        if not key:
            raise JevUnavailable(f"missing credential environment variable {self.api_key_env}")
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = requests.post(
                    self.base_url.rstrip("/") + "/chat/completions",
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json={
                        "model": self.model,
                        "temperature": self.temperature,
                        "response_format": {"type": "json_object"},
                        "max_tokens": self.max_output_tokens,
                        "messages": [
                            {"role": "system", "content": self.prompt},
                            {"role": "user", "content": json.dumps(request.model_dump(mode="json"), sort_keys=True)},
                        ],
                    },
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                return JevEvaluation.model_validate(json.loads(content))
            except (requests.RequestException, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(self.retry_backoff_seconds * (2 ** attempt))
        raise JevUnavailable(f"OpenAI-compatible Jev request failed: {type(last_error).__name__}") from last_error


class JevClientFactory:
    @staticmethod
    def create(config: ProviderConfig, *, prompt: str) -> JevClient:
        if config.provider == "disabled":
            return DisabledJevClient()
        if config.provider == "replay":
            return ReplayJevClient()
        base_url = config.base_url or os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        model = config.model
        if model == "configured-at-deployment":
            model = os.getenv("JEV_MODEL", "")
        if not model:
            raise JevUnavailable("JEV_MODEL is required for OpenAI-compatible Jev")
        return OpenAICompatibleJevClient(
            base_url=base_url, model=model, prompt=prompt,
            api_key_env=config.api_key_env,
            model_version=f"openai-compatible-jev:{model}",
            timeout_seconds=config.timeout_seconds,
            max_output_tokens=config.max_output_tokens,
            temperature=config.temperature,
            max_retries=config.max_retries,
            retry_backoff_seconds=config.retry_backoff_seconds,
        )

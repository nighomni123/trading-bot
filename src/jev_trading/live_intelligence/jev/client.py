"""Jev provider boundary and OpenAI-compatible transport."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

import requests

from jev_trading.live_intelligence.config import ProviderConfig, env_bool
from jev_trading.live_intelligence.provider_errors import (
    ProviderFailure,
    classify_request_error,
    http_status,
    retryable_request_error,
)
from jev_trading.live_intelligence.schemas import JevEvaluation, JevRequest


class JevClient(Protocol):
    model_version: str

    def evaluate(self, request: JevRequest) -> JevEvaluation: ...


class JevUnavailable(ProviderFailure):
    def __init__(self, message: str, **kwargs) -> None:
        kwargs.setdefault("component", "jev")
        super().__init__(message, **kwargs)


def _parse_json_object(content: Any) -> dict[str, Any]:
    if not isinstance(content, str):
        raise ValueError("provider response content is not text")
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(text[start:end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("provider response must be a JSON object")
    return parsed


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
    provider: str = "openai_compatible"
    model_version: str = "openai-compatible-jev-v1"
    timeout_seconds: float = 20.0
    max_output_tokens: int = 900
    temperature: float = 0.0
    max_retries: int = 2
    retry_backoff_seconds: float = 0.5
    supports_response_format: bool = False
    supports_tool_calling: bool = False
    supports_reasoning: bool = False
    supports_vision: bool = False

    def evaluate(self, request: JevRequest) -> JevEvaluation:
        key = os.environ.get(self.api_key_env)
        if not key:
            raise JevUnavailable(
                f"missing credential environment variable {self.api_key_env}",
                provider=self.provider, model=self.model, category="authentication",
            )
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                request_body = {
                    "model": self.model,
                    "temperature": self.temperature,
                    "max_tokens": self.max_output_tokens,
                    "messages": [
                        {"role": "system", "content": self.prompt},
                        {"role": "user", "content": json.dumps(request.model_dump(mode="json"), sort_keys=True)},
                    ],
                }
                if self.supports_response_format:
                    request_body["response_format"] = {"type": "json_object"}
                response = requests.post(
                    self.base_url.rstrip("/") + "/chat/completions",
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json=request_body,
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
            except requests.RequestException as exc:
                last_error = exc
                category = classify_request_error(exc)
                if not retryable_request_error(exc) or attempt >= self.max_retries:
                    raise JevUnavailable(
                        f"OpenAI-compatible Jev request failed: {category}",
                        provider=self.provider, model=self.model, category=category,
                        http_status=http_status(exc), retry_count=attempt + 1,
                        request_id=request.request_id,
                    ) from exc
                time.sleep(self.retry_backoff_seconds * (2 ** attempt))
                continue
            try:
                content = response.json()["choices"][0]["message"]["content"]
                return JevEvaluation.model_validate(_parse_json_object(content))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                raise JevUnavailable(
                    "OpenAI-compatible Jev response validation failed",
                    provider=self.provider, model=self.model, category="validation",
                    http_status=response.status_code, retry_count=attempt + 1,
                    request_id=request.request_id,
                ) from exc
        raise JevUnavailable(
            "OpenAI-compatible Jev request failed",
            provider=self.provider, model=self.model, category="unknown",
            retry_count=self.max_retries + 1, request_id=request.request_id,
        ) from last_error


class JevClientFactory:
    @staticmethod
    def create(config: ProviderConfig, *, prompt: str) -> JevClient:
        if config.provider == "disabled":
            return DisabledJevClient()
        if config.provider == "replay":
            return ReplayJevClient()
        base_url = (
            config.base_url
            or os.getenv("JEV_BASE_URL")
            or os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        )
        model = config.model
        if model == "configured-at-deployment":
            model = os.getenv("JEV_MODEL", "")
        if not model:
            raise JevUnavailable(
                "JEV_MODEL is required for OpenAI-compatible Jev",
                provider=config.provider, category="configuration",
            )
        return OpenAICompatibleJevClient(
            base_url=base_url, model=model, prompt=prompt,
            provider=config.provider,
            api_key_env=config.api_key_env,
            model_version=f"openai-compatible-jev:{model}",
            timeout_seconds=config.timeout_seconds,
            max_output_tokens=config.max_output_tokens,
            temperature=config.temperature,
            max_retries=config.max_retries,
            retry_backoff_seconds=config.retry_backoff_seconds,
            supports_response_format=env_bool("JEV_SUPPORTS_RESPONSE_FORMAT", config.supports_response_format),
            supports_tool_calling=env_bool("JEV_SUPPORTS_TOOL_CALLING", config.supports_tool_calling),
            supports_reasoning=env_bool("JEV_SUPPORTS_REASONING", config.supports_reasoning),
            supports_vision=env_bool("JEV_SUPPORTS_VISION", config.supports_vision),
        )

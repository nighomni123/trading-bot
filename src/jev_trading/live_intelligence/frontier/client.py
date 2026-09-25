"""Frontier provider boundary and OpenAI-compatible transport."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Protocol

import requests

from jev_trading.live_intelligence.config import ProviderConfig, env_bool
from jev_trading.live_intelligence.model_contracts import FRONTIER_TOOL
from jev_trading.live_intelligence.provider_errors import (
    ProviderFailure,
    classify_request_error,
    http_status,
    retryable_request_error,
)
from jev_trading.live_intelligence.schemas import Direction


class FrontierClient(Protocol):
    model_version: str

    def complete(self, *, system_prompt: str, payload: dict[str, Any]) -> dict[str, Any]: ...


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


class FrontierUnavailable(ProviderFailure):
    def __init__(self, message: str, **kwargs) -> None:
        kwargs.setdefault("component", "frontier")
        super().__init__(message, **kwargs)


@dataclass
class DisabledFrontierClient:
    model_version: str = "disabled"

    def complete(self, *, system_prompt: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise FrontierUnavailable("Frontier provider is disabled; no fallback hypothesis is authorized")


@dataclass
class ReplayFrontierClient:
    """Deterministic test/replay double; never evidence of AI value."""

    model_version: str = "replay-frontier-v1"
    allow_trade: bool = False

    def complete(self, *, system_prompt: str, payload: dict[str, Any]) -> dict[str, Any]:
        env = payload["environment"]
        timeframes = env.get("timeframes", {})
        direction = Direction.NONE
        primary = None
        if self.allow_trade:
            trend_4h = timeframes.get("4h", {}).get("trend_direction")
            trend_1h = timeframes.get("1h", {}).get("trend_direction")
            replay_trend = trend_4h if trend_4h in {"UP", "DOWN"} else trend_1h
            if replay_trend in {"UP", "DOWN"}:
                direction = Direction.LONG if replay_trend == "UP" else Direction.SHORT
                primary = "momentum"
        return {
            "hypothesis_id": payload["request_id"],
            "timestamp": env["decision_timestamp"],
            "regime": payload.get("regime", "UNKNOWN"),
            "regime_confidence": 0.5,
            "primary_strategy": primary,
            "alternative_strategies": ["mean_reversion"],
            "direction": direction.value,
            "horizon_seconds": 900 if primary else None,
            "thesis": "Deterministic replay hypothesis" if primary else "Insufficient evidence; abstain",
            "supporting_evidence": ["replay fixture only"] if primary else [],
            "required_quant_questions": payload.get("required_quant_questions", []),
            "entry_conditions": ["replay fixture condition"] if primary else [],
            "invalidation_conditions": ["replay fixture invalidation"] if primary else [],
            "target_logic": "target supplied by candidate builder" if primary else None,
            "stop_logic": "stop supplied by candidate builder" if primary else None,
            "maximum_holding_seconds": 900 if primary else None,
            "abandon_conditions": ["data becomes unsafe"],
            "jev_questions": payload.get("jev_questions", []),
            "abstain": not bool(primary),
            "reason": "replay client" if primary else "replay client abstains",
            "conviction": 0.5 if primary else 0.0,
            "model_version": self.model_version,
            "prompt_version": payload.get("prompt_version", "unknown"),
        }


@dataclass
class OpenAICompatibleFrontierClient:
    """OpenAI-compatible Chat Completions transport, including OpenRouter."""

    base_url: str
    model: str
    api_key_env: str = "OPENROUTER_API_KEY"
    provider: str = "openai_compatible"
    model_version: str = "openai-compatible-frontier-v1"
    timeout_seconds: float = 30.0
    max_output_tokens: int = 1800
    temperature: float = 0.2
    max_retries: int = 2
    retry_backoff_seconds: float = 0.5
    supports_response_format: bool = False
    supports_tool_calling: bool = False
    supports_reasoning: bool = False
    supports_vision: bool = False
    tool: dict[str, Any] | None = None
    last_interface: str = "json"
    last_tool_name: str | None = None

    def complete(self, *, system_prompt: str, payload: dict[str, Any]) -> dict[str, Any]:
        key = os.environ.get(self.api_key_env)
        if not key:
            raise FrontierUnavailable(
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
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": json.dumps(payload, sort_keys=True)},
                    ],
                }
                if self.supports_response_format:
                    request_body["response_format"] = {"type": "json_object"}
                if self.supports_tool_calling and self.tool is not None:
                    request_body["tools"] = [self.tool]
                    request_body["tool_choice"] = {
                        "type": "function",
                        "function": {"name": self.tool["function"]["name"]},
                    }
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
                    raise FrontierUnavailable(
                        f"OpenAI-compatible Frontier request failed: {category}",
                        provider=self.provider, model=self.model, category=category,
                        http_status=http_status(exc), retry_count=attempt + 1,
                        request_id=payload.get("request_id"),
                    ) from exc
                time.sleep(self.retry_backoff_seconds * (2 ** attempt))
                continue
            try:
                message = response.json()["choices"][0]["message"]
                tool_calls = message.get("tool_calls") or []
                if self.supports_tool_calling and self.tool is not None:
                    if len(tool_calls) != 1:
                        raise ValueError("Frontier provider did not return exactly one required tool call")
                    function = tool_calls[0].get("function") or {}
                    expected_name = self.tool["function"]["name"]
                    if function.get("name") != expected_name:
                        raise ValueError("Frontier provider returned the wrong tool")
                    self.last_interface = "tool_call"
                    self.last_tool_name = expected_name
                    return _parse_json_object(function.get("arguments"))
                self.last_interface = "json"
                self.last_tool_name = None
                return _parse_json_object(message.get("content"))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                raise FrontierUnavailable(
                    "OpenAI-compatible Frontier response validation failed",
                    provider=self.provider, model=self.model, category="validation",
                    http_status=response.status_code, retry_count=attempt + 1,
                    request_id=payload.get("request_id"),
                ) from exc
        raise FrontierUnavailable(
            "OpenAI-compatible Frontier request failed",
            provider=self.provider, model=self.model, category="unknown",
            retry_count=self.max_retries + 1, request_id=payload.get("request_id"),
        ) from last_error


class FrontierClientFactory:
    @staticmethod
    def create(config: ProviderConfig, *, replay_allow_trade: bool = False) -> FrontierClient:
        if config.provider == "disabled":
            return DisabledFrontierClient()
        if config.provider == "replay":
            return ReplayFrontierClient(allow_trade=replay_allow_trade)
        base_url = (
            config.base_url
            or os.getenv("FRONTIER_BASE_URL")
            or os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        )
        model = config.model
        if model == "configured-at-deployment":
            model = os.getenv("FRONTIER_MODEL", "")
        if not model:
            raise FrontierUnavailable(
                "FRONTIER_MODEL is required for OpenAI-compatible Frontier",
                provider=config.provider, category="configuration",
            )
        return OpenAICompatibleFrontierClient(
            base_url=base_url,
            model=model,
            provider=config.provider,
            api_key_env=config.api_key_env,
            model_version=f"openai-compatible-frontier:{model}",
            timeout_seconds=config.timeout_seconds,
            max_output_tokens=config.max_output_tokens,
            temperature=config.temperature,
            max_retries=config.max_retries,
            retry_backoff_seconds=config.retry_backoff_seconds,
            supports_response_format=env_bool("FRONTIER_SUPPORTS_RESPONSE_FORMAT", config.supports_response_format),
            supports_tool_calling=env_bool("FRONTIER_SUPPORTS_TOOL_CALLING", config.supports_tool_calling),
            supports_reasoning=env_bool("FRONTIER_SUPPORTS_REASONING", config.supports_reasoning),
            supports_vision=env_bool("FRONTIER_SUPPORTS_VISION", config.supports_vision),
            tool=FRONTIER_TOOL,
        )

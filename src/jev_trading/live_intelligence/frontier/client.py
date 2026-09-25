"""Frontier provider boundary and OpenAI-compatible transport."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Protocol

import requests

from jev_trading.live_intelligence.config import ProviderConfig
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


class FrontierUnavailable(RuntimeError):
    pass


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
    model_version: str = "openai-compatible-frontier-v1"
    timeout_seconds: float = 30.0
    max_output_tokens: int = 1800
    temperature: float = 0.2
    max_retries: int = 2
    retry_backoff_seconds: float = 0.5

    def complete(self, *, system_prompt: str, payload: dict[str, Any]) -> dict[str, Any]:
        key = os.environ.get(self.api_key_env)
        if not key:
            raise FrontierUnavailable(f"missing credential environment variable {self.api_key_env}")
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = requests.post(
                    self.base_url.rstrip("/") + "/chat/completions",
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json={
                        "model": self.model,
                        "temperature": self.temperature,
                        "max_tokens": self.max_output_tokens,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": json.dumps(payload, sort_keys=True)},
                        ],
                    },
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                return _parse_json_object(content)
            except (requests.RequestException, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(self.retry_backoff_seconds * (2 ** attempt))
        raise FrontierUnavailable(f"OpenAI-compatible Frontier request failed: {type(last_error).__name__}") from last_error


class FrontierClientFactory:
    @staticmethod
    def create(config: ProviderConfig, *, replay_allow_trade: bool = False) -> FrontierClient:
        if config.provider == "disabled":
            return DisabledFrontierClient()
        if config.provider == "replay":
            return ReplayFrontierClient(allow_trade=replay_allow_trade)
        base_url = config.base_url or os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        model = config.model
        if model == "configured-at-deployment":
            model = os.getenv("FRONTIER_MODEL", "")
        if not model:
            raise FrontierUnavailable("FRONTIER_MODEL is required for OpenAI-compatible Frontier")
        return OpenAICompatibleFrontierClient(
            base_url=base_url,
            model=model,
            api_key_env=config.api_key_env,
            model_version=f"openai-compatible-frontier:{model}",
            timeout_seconds=config.timeout_seconds,
            max_output_tokens=config.max_output_tokens,
            temperature=config.temperature,
            max_retries=config.max_retries,
            retry_backoff_seconds=config.retry_backoff_seconds,
        )

"""Frontier provider boundary.

Frontier is deliberately a strategist/research client. Its output is validated
as data; it has no order, sizing, leverage, or risk methods.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Protocol

import requests

from jev_trading.live_intelligence.schemas import (
    Direction,
    JevQuestion,
    MarketEnvironment,
    QuantQuestion,
    StrategyHypothesis,
)


class FrontierClient(Protocol):
    model_version: str

    def complete(self, *, system_prompt: str, payload: dict[str, Any]) -> dict[str, Any]: ...


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
        tf = env.get("timeframes", {})
        direction = Direction.NONE
        primary = None
        if self.allow_trade:
            trend_4h = tf.get("4h", {}).get("trend_direction")
            trend_1h = tf.get("1h", {}).get("trend_direction")
            if trend_4h == trend_1h and trend_4h in {"UP", "DOWN"}:
                direction = Direction.LONG if trend_4h == "UP" else Direction.SHORT
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
    """Small provider adapter; credentials stay in environment variables."""

    base_url: str
    model: str
    api_key_env: str = "JEV_LLM_API_KEY"
    model_version: str = "openai-compatible-frontier-v1"
    timeout_seconds: float = 30.0
    max_output_tokens: int = 1800

    def complete(self, *, system_prompt: str, payload: dict[str, Any]) -> dict[str, Any]:
        key = os.environ.get(self.api_key_env)
        if not key:
            raise FrontierUnavailable(f"missing credential environment variable {self.api_key_env}")
        response = requests.post(
            self.base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": self.model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
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
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise ValueError("Frontier response must be a JSON object")
        return parsed

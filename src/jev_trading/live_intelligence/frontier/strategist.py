"""Frontier strategist and structured-output validation."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .client import FrontierClient
from ..schemas import MarketEnvironment, StrategyHypothesis


def load_prompt(path: str | Path) -> str:
    return Path(path).read_text()


def _json_payload(environment: MarketEnvironment, *, request_id: str, required_quant_questions: list[dict], jev_questions: list[dict], prompt_version: str, regime: str) -> dict:
    return {
        "request_id": request_id,
        "prompt_version": prompt_version,
        "regime": regime,
        "environment": environment.model_dump(mode="json"),
        "required_quant_questions": required_quant_questions,
        "jev_questions": jev_questions,
    }


class FrontierStrategist:
    def __init__(self, client: FrontierClient, *, prompt: str, prompt_version: str):
        self.client = client
        self.prompt = prompt
        self.prompt_version = prompt_version

    def generate(self, environment: MarketEnvironment, *, request_id: str, required_quant_questions=(), jev_questions=(), regime: str = "UNKNOWN") -> StrategyHypothesis:
        payload = _json_payload(
            environment, request_id=request_id,
            required_quant_questions=[item.model_dump(mode="json") for item in required_quant_questions],
            jev_questions=[item.model_dump(mode="json") for item in jev_questions],
            prompt_version=self.prompt_version, regime=regime,
        )
        try:
            raw = self.client.complete(system_prompt=self.prompt, payload=payload)
            return StrategyHypothesis.model_validate(raw)
        except (ValidationError, ValueError, TypeError, KeyError) as exc:
            raise ValueError(f"invalid Frontier output: {exc}") from exc

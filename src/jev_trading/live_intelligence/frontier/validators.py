"""Frontier response validation helpers."""
from ..schemas import StrategyHypothesis


def validate_hypothesis(value: dict) -> StrategyHypothesis:
    return StrategyHypothesis.model_validate(value)

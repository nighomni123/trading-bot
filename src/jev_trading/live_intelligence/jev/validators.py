"""Jev output validation."""
from ..schemas import JevEvaluation


def validate_evaluation(value: dict) -> JevEvaluation:
    return JevEvaluation.model_validate(value)

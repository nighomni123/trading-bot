"""The 5 MVP jev questions, loaded from configs/jev_questions.json."""
from __future__ import annotations

import json
from pathlib import Path

from jev_trading.jev.client import Question


def load_questions(path: str = "configs/jev_questions.json") -> list[Question]:
    p = Path(path)
    if not p.is_absolute():
        p = Path(__file__).resolve().parents[3] / p
    return [Question.model_validate(item) for item in json.loads(p.read_text())]


MVP_QUESTIONS: list[Question] = load_questions()

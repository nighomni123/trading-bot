"""Jev interface: fast bounded judgments (yesno/choice/score) over shared state.

Jev never emits orders — answers only feed the policy engine. TypeSafe real API
is post-MVP; JevClient is the swappable contract.

Answer semantics by kind:
- yesno: value = P(true), in [0, 1]
- score: value in [0, 1]
- choice: value = P(first option in Question.options), in [0, 1]
  (single-float encoding instead of a dist dict — simpler, enough for MVP)
"""
from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, Field


class Question(BaseModel):
    name: str
    kind: Literal["yesno", "choice", "score"]
    instructions: str
    options: list[str] | None = None


class Answer(BaseModel):
    name: str
    value: float = Field(ge=0.0, le=1.0)


class JevClient(Protocol):
    """One ask() answers all questions in parallel over the same state dict."""

    def ask(self, state: dict, questions: list[Question]) -> list[Answer]: ...

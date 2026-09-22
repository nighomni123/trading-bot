"""Deterministic mock Jev + seeded random arm (stand-ins until TypeSafe, post-MVP).

MockJev is a pure function of (state, question name): no randomness, no clock.
Routing: breakout ~ sigmoid(trend_score*2); liquidity_ok ~ sigmoid(volume_z);
vol_risk ~ sigmoid(vol_regime*2); failure_regime high when |funding_z| > 2 or
|vol_regime| > 2 (max of the two logistic risks); trade_ok ~ blend of trend
strength and not-failure. Unknown names and fully-missing inputs answer neutral
0.5; when only some failure inputs are present the missing ones count as no
evidence (0). All outputs clipped to [0, 1].

RandomJev determinism contract: reseeds random.Random(seed) on every ask() and
draws in question order — same (seed, question sequence) always yields the same
answers regardless of state or call history (reproducible across processes).
"""
from __future__ import annotations

import math
import random

from jev_trading.jev.client import Answer, Question


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def _clip(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def _trend(state: dict) -> float:
    return _sigmoid(float(state.get("trend_score", 0.0)) * 2)


def _failure(state: dict) -> float:
    if "funding_z" not in state and "vol_regime" not in state:
        return 0.5  # neutral when nothing relevant is present
    funding_risk = _sigmoid((abs(float(state.get("funding_z", 0.0))) - 2.0) * 2)
    vol_risk = _sigmoid((abs(float(state.get("vol_regime", 0.0))) - 2.0) * 2)
    return max(funding_risk, vol_risk)


def _score(name: str, state: dict) -> float:
    if name == "breakout":
        return _trend(state)
    if name == "liquidity_ok":
        return _sigmoid(float(state.get("volume_z", 0.0)))
    if name == "vol_risk":
        return _sigmoid(float(state.get("vol_regime", 0.0)) * 2)
    if name == "failure_regime":
        return _failure(state)
    if name == "trade_ok":
        return 0.5 * _trend(state) + 0.5 * (1.0 - _failure(state))
    return 0.5  # unknown question -> neutral


class MockJev:
    """Deterministic JevClient: same (state, questions) always -> same answers."""

    def ask(self, state: dict, questions: list[Question]) -> list[Answer]:
        return [Answer(name=q.name, value=_clip(_score(q.name, state))) for q in questions]


class RandomJev:
    """Seeded random arm — see module docstring for the determinism contract."""

    def __init__(self, seed: int) -> None:
        self.seed = seed

    def ask(self, state: dict, questions: list[Question]) -> list[Answer]:
        rng = random.Random(self.seed)
        return [Answer(name=q.name, value=rng.random()) for q in questions]

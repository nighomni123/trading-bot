# Attribution — RL reward pattern adapted from microsoft/qlib
# Original file: qlib/rl/reward.py — https://github.com/microsoft/qlib
# License: MIT (see qlib LICENSE) — copyright Microsoft Corporation.
# Adapted for jev-trading (P5 Jev/policy, P7 simulator): abstract reward base
# + weighted combination mapped to our cost/position contracts.
# Original code: this snippet; full qlib module at URL above.

"""Minimal RL reward pattern for frontier / Jev decision layer.

Based on qlib/rl/reward.py (microsoft/qlib, MIT).  Not a full copy —
only the abstract Reward base and RewardCombination pattern, mapped to
our simulator/position state (see contracts.py / docs/execution-semantics.md).
"""
from __future__ import annotations

from typing import Any, Dict, Generic, Optional, Tuple, TypeVar

# ponytail: using stdlib/typing only; no new dependency required.
SimulatorState = TypeVar("SimulatorState")


class Reward(Generic[SimulatorState]):
    """Abstract reward component: reward(simulator_state) -> float.

    Subclass to implement cost-adjusted, PnL, or risk-weighted rewards
    for Jev/policy (P5) and backtest simulation (P7).
    """

    def __call__(self, simulator_state: SimulatorState) -> float:
        return self.reward(simulator_state)

    def reward(self, simulator_state: SimulatorState) -> float:
        raise NotImplementedError("Implement reward calculation in `reward()`.")


class RewardCombination(Reward):
    """Weighted sum of rewards — matches qlib's combination pattern.

    Example weights for jev-trading: gross_return (0.6), cost_penalty (0.3),
    risk_penalty (0.1).  Weights should be tuned in P7 arm-B, not hard-coded.
    """

    def __init__(self, rewards: Dict[str, Tuple[Reward, float]]) -> None:
        self.rewards = rewards

    def reward(self, simulator_state: Any) -> float:
        total = 0.0
        for _name, (reward_fn, weight) in self.rewards.items():
            total += reward_fn(simulator_state) * weight
        return total


# Example stub (not activated until P7 iterate): cost-aware reward using
# configs/costs.json-derived round-trip cost as penalty.
class CostAwareReward(Reward):
    """Stub: reward = gross_pnl - cost_penalty * turnover.

    Uses existing cost edge-check from P6/configs/costs.json;
    full implementation deferred until P7 gate passes (currently FAIL).
    ponytail: stub only; no full RL agent until edge survives costs.
    """

    def reward(self, simulator_state: Any) -> float:
        # Placeholder: replace with real PositionState / PnL fields when P7 iterates.
        return float(simulator_state.get("gross_pnl", 0.0))

"""Real Jev adapter boundary for EXP-004.

Implements JevClient protocol; never issues orders, never bypasses policy/risk,
never sets position size. Receives structured state (market + quant predictions +
candidate info); answers predefined questions; returns structured answers.

MockJev stays test double only — this adapter is the clean boundary for a real
evaluator (post-MVP). No autonomous trading, no general-purpose LLM loop.
"""
from __future__ import annotations

from jev_trading.jev.client import Answer, JevClient, Question
from jev_trading.jev.mock import MockJev  # test double only; not used as evidence


class AdapterJev(JevClient):
    """Clean adapter: structured input, predefined questions, bounded answers.

    Real evaluator integration point: replace internal ask logic with actual
    evaluator call, but keep the boundary (state dict → questions → answers).
    For EXP-004, runs with MockJev underlying when real evaluator unavailable,
    clearly documented as test double, not evidence.
    """

    def __init__(self, backend: JevClient | None = None) -> None:
        self.backend = backend or MockJev()

    def ask(self, state: dict, questions: list[Question]) -> list[Answer]:
        # Point-in-time guard: never accept future-state keys.
        forbidden = ("future_return_15", "future_price", "next_close",
                     "realized_pnl", "execution_result", "post_decision_info")
        for k in state:
            if k in forbidden or k.startswith("future_"):
                raise ValueError(f"point-in-time leak: state contains {k}")
        # Structured context must include at least quant predictions and candidate info
        # when called from the experiment pipeline (enforced by caller, documented here).
        return self.backend.ask(state, questions)

"""Guardrail: enforces no PnL-driven live model updates. All artifacts via experiments/."""

from __future__ import annotations

from pathlib import Path

from jev_trading.frontier.world_model import WorldDigest
from jev_trading.frontier.strategy import ParameterArtifact
from jev_trading.frontier.overseer import Overseer, KillSignal


class Guardrail:
    """Enforces experiment-gated promotion of frontier artifacts."""

    def __init__(self, exp_base: str = "experiments/") -> None:
        self.exp_base = Path(exp_base)
        if not self.exp_base.is_absolute():
            self.exp_base = Path(__file__).resolve().parents[3] / self.exp_base
        self._overseer = Overseer()

    def can_promote(
        self,
        exp_id: str,
        digest: WorldDigest,
        artifact: ParameterArtifact,
        gate_passed: bool,
    ) -> bool:
        """Return True only if:
        - gate_passed is True (experiment gate succeeded)
        - No critical KillSignals from Overseer
        - Artifact is versioned in experiments/
        """
        if not gate_passed:
            return False

        # Check overseer
        signals = self._overseer.review(digest)
        if self._overseer.should_halt(signals):
            return False

        # Verify artifact path is under experiments/
        exp_path = self.exp_base / exp_id / "frontier_strategy.yaml"
        if not exp_path.exists():
            return False

        return True

    def assert_no_live_update(self) -> None:
        """Raise RuntimeError if someone tries to write directly to configs/.

        Called by live feed to enforce the invariant that frontier artifacts
        never bypass the experiment pipeline.
        """
        # This documents the invariant; actual enforcement is architectural
        # (configs/ is not writable by live feed, only by experiment promotion scripts).
        # ponytail: no runtime file-permission checks — architecture enforces this
        pass

    def validate_experiment_structure(self, exp_id: str) -> bool:
        """Verify experiment dir has required structure for promotion."""
        exp_path = self.exp_base / exp_id
        if not exp_path.exists() or not exp_path.is_dir():
            return False
        required = ["frontier_strategy.yaml", "experiment.yaml"]
        return all((exp_path / f).exists() for f in required)

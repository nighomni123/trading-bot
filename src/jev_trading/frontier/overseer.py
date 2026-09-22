"""Overseer: adversarial review of WorldDigest + decision logs -> KillSignal."""

from __future__ import annotations

from collections import deque
from typing import NamedTuple

from jev_trading.frontier.world_model import WorldDigest, Regime


class KillSignal(NamedTuple):
    """Signal to halt, revert, or review the strategy."""
    trigger: str      # Reason for signal
    severity: str     # "warning" or "critical"
    action: str       # "halt", "revert", "review"


class Overseer:
    """Adversarial review of frontier digests. Tracks last N windows."""

    def __init__(self, n_windows: int = 6) -> None:
        self.n_windows = n_windows
        self._history: deque[WorldDigest] = deque(maxlen=n_windows)
        self._last_regime: Regime | None = None

    def review(self, digest: WorldDigest) -> list[KillSignal]:
        """Review a digest and return any kill signals."""
        signals = []

        # Track history
        self._history.append(digest)

        # 1. Regime change detection
        signals.extend(self._detect_regime_change(digest))

        # 2. Cost erosion
        signals.extend(self._detect_cost_erosion(digest))

        # 3. Calibration drift
        signals.extend(self._detect_calibration_drift(digest))

        # 4. Jev information collapse (answers near 0.5 = no info)
        signals.extend(self._detect_overfitting(digest))

        # 5. Negative edge with high costs
        signals.extend(self._detect_negative_edge(digest))

        return signals

    def _detect_regime_change(self, digest: WorldDigest) -> list[KillSignal]:
        """Warn if regime shifts from last window."""
        signals = []
        if self._last_regime is not None:
            if self._last_regime != digest.regime:
                signals.append(KillSignal(
                    trigger=f"regime_change: {self._last_regime} -> {digest.regime}",
                    severity="warning",
                    action="review",
                ))
        self._last_regime = digest.regime
        return signals

    def _detect_cost_erosion(self, digest: WorldDigest) -> list[KillSignal]:
        """Warn if costs eating >80% of gross."""
        signals = []
        if digest.pnl.cost_ratio > 0.8:
            signals.append(KillSignal(
                trigger=f"cost_erosion: cost_ratio={digest.pnl.cost_ratio:.1%}",
                severity="warning",
                action="review",
            ))
        return signals

    def _detect_calibration_drift(self, digest: WorldDigest) -> list[KillSignal]:
        """Warn if calibration drift > 0.15."""
        signals = []
        if digest.calibration_drift > 0.15:
            signals.append(KillSignal(
                trigger=f"calibration_drift: {digest.calibration_drift:.3f}",
                severity="warning",
                action="review",
            ))
        return signals

    def _detect_overfitting(self, digest: WorldDigest) -> list[KillSignal]:
        """Warn if Jev answers carry no information (all near 0.5)."""
        signals = []
        for q_name, hist in digest.jev_distributions.items():
            if not hist:
                continue
            # Check if mass is concentrated in middle bins (4-5 for 10 bins = 0.4-0.6)
            mid_mass = sum(hist[3:7])  # bins 3-6 cover 0.3-0.7
            if mid_mass > 0.8:  # 80% of answers in middle
                signals.append(KillSignal(
                    trigger=f"jev_no_info: {q_name} answers concentrated near 0.5 (mid_mass={mid_mass:.1%})",
                    severity="warning",
                    action="review",
                ))
        return signals

    def _detect_negative_edge(self, digest: WorldDigest) -> list[KillSignal]:
        """Critical if net PnL negative and costs > 50% of gross."""
        signals = []
        pnl = digest.pnl
        if pnl.net_per_trade < 0 and pnl.cost_ratio > 0.5:
            signals.append(KillSignal(
                trigger=f"negative_edge: net_per_trade={pnl.net_per_trade:.6f}, cost_ratio={pnl.cost_ratio:.1%}",
                severity="critical",
                action="halt",
            ))
        return signals

    def should_halt(self, signals: list[KillSignal]) -> bool:
        """Return True if any critical signal present."""
        return any(s.severity == "critical" for s in signals)

#!/usr/bin/env python3
"""Retired EXP-004 runner.

The historical runner used consumed 2025 OOS, a policy path without a real
economic forecast, and non-canonical event hashes. Its artifacts remain in Git
history for audit only and must not be rerun as current evidence. Use the
validation-only baseline and Phase 1 scripts instead.
"""
from __future__ import annotations

import sys


def main() -> int:
    print(
        "EXP-004 is retired: consumed OOS, invalid attribution, and legacy event hashes "
        "are not current evidence. Use scripts/reproduce_phase0.py or "
        "scripts/run_phase1_economic.py.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Retired EXP-007C/7E runner.

The historical files used consumed OOS, reused one full-feature prediction vector
instead of retraining feature families, and approximated economic returns from
binary probabilities. They remain historical artifacts only. Use the explicit
validation-only Phase 1/2 experiment scripts for new evidence.
"""
from __future__ import annotations

import sys


def main() -> int:
    print(
        "EXP-007C/7E is retired: it was not a real feature ablation and used consumed "
        "OOS plus heuristic economic fields. Use scripts/run_phase1_economic.py.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

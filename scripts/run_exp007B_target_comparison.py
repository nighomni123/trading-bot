#!/usr/bin/env python3
"""Retired EXP-007B runner.

The historical script sampled discontinuous rows before feature/label creation,
trained on data across the validation boundary, and constructed synthetic
excursion/return fields. Its artifacts remain historical only. Use the
validation-only ``scripts/run_phase1_economic.py`` gate instead.
"""
from __future__ import annotations

import sys


def main() -> int:
    print(
        "EXP-007B is retired: discontinuous sampling, boundary leakage, synthetic "
        "economic fields, and consumed OOS are not valid evidence. Use "
        "scripts/run_phase1_economic.py.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Retired EXP-006 runner.

The historical EXP-006 artifacts used probability-weighted and fixed excursion
approximations, then evaluated a consumed OOS window. That code is preserved in
Git history and the experiment directory, but is not a valid economic experiment.
Use ``scripts/run_phase1_economic.py`` for the replacement pre-OOS gate.
"""
from __future__ import annotations

import sys


def main() -> int:
    print(
        "EXP-006 is retired: its synthetic economic fields and consumed OOS window "
        "are not valid evidence. Use scripts/run_phase1_economic.py.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

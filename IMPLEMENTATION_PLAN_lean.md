# Implementation status — current research path

The earlier LEAN-pluggable plan is historical. The optional LEAN adapter and its
dependency were intentionally removed; the supported research engine is
`LocalBacktestEngine` over the deterministic event simulator.

## Completed gates

- **Phase 0 current baseline:** PASS for the new current-code freeze. Two final
  runs produced identical validation metrics and provenance hashes; 170 tests
  passed; a real 4,880-record event sample passed schema-v3 verification; the
  independent ablation runner matched the recorded economics.
- **Historical baseline reproduction:** FAIL. EXP-002/EXP-003 were produced from
  uncommitted/older source and execution semantics. They are reference-only.
- **OOS:** 2025+ was already consumed by historical experiments. No current run
  uses it for selection, and a fresh untouched holdout is required for promotion.

## Phase 1 economic target gate

`EXP-009-economic-targets` is **STOP**:

- real 5m/15m/30m/60m return, MFE, and MAE labels are point-in-time and tested;
- deterministic real heads were compared with zero, historical-mean, and linear
  baselines on 2024 validation using a 60-minute purge;
- excursion heads had measurable conditional predictability, but no return head
  produced positive mean predicted net edge after the 0.0014 round-trip cost;
- no model artifact was promoted and no specialist, Frontier, Jev, RL, or live
  change was introduced.

The next valid action is feature/target research or acquisition of a fresh
untouched holdout. Adding an allocator or reasoning layer is blocked by the
Phase 1 gate.

## Supported commands

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/reproduce_phase0.py --out artifacts/phase0-baseline-final-a
.venv/bin/python scripts/run_phase1_economic.py --bars artifacts/phase0-baseline-final-d/pre_oos_2021_2024.parquet --out artifacts/phase1-economic-run1
```

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

## Phase 2 Economic Quant Engine v2

`EXP-010` through `EXP-019` are complete or explicitly deferred. Final decision:
**NO EDGE**.

- execution-aligned up/flat/down, return, MFE/MAE, holding, and ensemble
  uncertainty heads are implemented and serialized;
- base validation mean uncertainty-adjusted edge is `-0.001402`;
- all three chronological folds have negative mean adjusted edge and only 5–12
  selected trades;
- uncertainty error is non-monotonic;
- derived features reduce eligibility; specialist and ensemble experiments are
  deferred because the prerequisite failed;
- base 2×/3× fee and extra-slippage scenarios select zero trades.

Frontier, Laya, Jev, RL, and live integration remain blocked. Full report:
`docs/phase2-economic-engine-v2.md`.

## Supported commands

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/reproduce_phase0.py --out artifacts/phase0-baseline-final-a
.venv/bin/python scripts/run_phase1_economic.py --bars artifacts/phase0-baseline-final-d/pre_oos_2021_2024.parquet --out artifacts/phase1-economic-run1
.venv/bin/python scripts/run_phase2_experiments.py --bars artifacts/phase0-baseline-final-d/pre_oos_2021_2024.parquet --out artifacts/phase2-experiments-final --trees 20 --leaves 15
.venv/bin/python scripts/run_phase2_walkforward.py
```

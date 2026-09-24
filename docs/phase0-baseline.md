# Phase 0 — Current baseline freeze

## Gate decision

**PASS for the current-code baseline; historical EXP-002/EXP-003 reproduction is not claimed.**

The old artifacts were produced from a source state with uncommitted drift and do
not provide a fully pinned, replayable baseline. The current code is now frozen and
reproduced twice from a pre-2025 slice. The 2025+ period was already consumed by
historical experiments, so it is not available as a fresh final OOS set for this
project. It must not be used for model, threshold, feature, or allocator selection.
A new untouched holdout is required before promotion.

## Reproduction

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/reproduce_phase0.py --out artifacts/phase0-baseline-final-a
.venv/bin/python scripts/reproduce_phase0.py --out artifacts/phase0-baseline-final-b
```

The command materializes `[2021-01-01, 2025-01-01)` before feature/label/model
work, trains with deterministic LightGBM parameters, evaluates the threshold arm
on 2024, and verifies a real 4,880-record event-log sample. It writes
`metrics.json`, `provenance.json`, `locked_config.json`, `summary.json`, and
`notes.md` under the output directory.

## Current validation baseline

| Metric | Value |
|---|---:|
| Train rows | 1,574,601 |
| Validation rows | 525,485 |
| LightGBM validation AUC | 0.6439145 |
| LightGBM validation log-loss | 0.5158275 |
| Validation entries / exits | 4,199 / 4,199 |
| Gross PnL | -$103.8223 |
| Fees | $419.9320 |
| Slippage | $167.9728 |
| Funding | $0.4408 cost |
| Net PnL | -$523.3135 |
| Return | -5.2331% |
| Maximum drawdown | 5.2344% |
| Exposure | 2.76% |
| Replay records verified | 4,880 |

This is a reproducible **negative** economic baseline, not a profitable strategy.
The historical EXP-002 AUC/log-loss values remain reference values only.

## Controls repaired before the freeze

- Simulator and paper execution now share schema-v3 event records, next-bar
  decision/fill linkage, sequence/hash/footer verification, and risk-required
  paper proposals.
- Position-aware policy exits and pending-order risk blocking prevent duplicate
  entries and live/backtest divergence.
- Economic fees and slippage are counted once; funding is scaled from the
  per-eight-hour quote to one-minute bars.
- Ordinary ablation runs reject 2025+ unless an explicit locked OOS config is
  supplied. The `run_ablation.py` stride sampling option was removed.
- The stale optional LEAN import and tests for nonexistent EXP-004R artifacts were
  removed rather than replaced with fabricated evidence.

## Historical limitations carried forward

The current baseline does not make the old experiments scientifically valid. In
particular, historical 2025 OOS was opened, EXP-004 attribution was invalid, and
Phase 6/7 economic measurements contained the cost/funding defects documented in
the Phase 0 audit. Those artifacts remain preserved as historical evidence only.

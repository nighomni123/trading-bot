# Stage 12 — Initial Audit (Phase 0)

Audit date: 2026-09-26. HEAD at audit: `76b6e71`. Read-only except this document.

## A. Existing reusable infrastructure

| Component | Location | Reuse |
|---|---|---|
| `load_bars` (1m OHLCV + funding + OI) | `microstructure/ingest.py` | core long history loader |
| `load_trade_range` (aggTrades) | `microstructure/ingest.py` | June-2025-only flow data |
| `build_forward_targets` | `microstructure/targets.py` | timing convention to extend, NOT loop at 72h |
| `select_model_features` / `is_label_column` | `microstructure/models.py` | **the leakage firewall** — reuse, extend prefixes |
| `walk_forward_benchmark` | `microstructure/models.py` | chronological split pattern, needs embargo |
| `economic_scorecard` / `STAGE10_MEASURED_COST_BPS` | `microstructure/economics.py` | 11.006 bps cost + provenance |
| `new_hypothesis` / `write_hypothesis` / `set_status` | `microstructure/registry.py` | hypothesis lifecycle |
| research CLI `main()` | `microstructure/cli.py` | extend with long-horizon commands |
| `build_phase2_features` (23 kline features) | `state/features.py` | baseline feature source |
| `chronological_economic_split` (purged) | `quant/engine.py` | embargo reference |
| `binary_probability_metrics` | `quant/evaluation.py` | AUC/PR-AUC/logloss/ECE |
| `build_live_barrier_labels_numpy` | `labels/live_barrier.py` | precedent for vectorized label engines |

## B. Existing limitations

| Dimension | Status |
|---|---|
| Periods | 2021-01-01 .. 2025-12-31 (klines); 2025-06-01..30 (trades) |
| Frequency | 1 minute only. No 1h/1d native frame — must resample or index by offset. |
| Instrument | BTCUSDT **perpetual** only (single instrument) |
| Funding | present, continuous across the full kline history |
| Open interest | present, continuous across the full kline history |
| Trade data | **June 2025 only** (30 days) — NOT multi-year |
| Order-book history | **unavailable** (archive 404) — Stage 11 recorded UNTESTED |
| Timestamps | epoch **milliseconds**, UTC, 1-minute contiguous bars |
| Entry convention | decision at bar `t`, entry `open[t+1]`, exit `open[t+h+1]` (Stage 9–11) |
| Leakage defenses | Stage 11 label-prefix firewall; causality tests corrupting future bars |

### Timestamp convention (reused, not redefined)

```text
decision at completed 1m bar t
entry  = open[t + 1]
exit   = open[t + h + 1]
path   = bars t+1 .. t+h
```

This matches `labels/live_barrier.py` and keeps Stages 9–12 comparable. Long horizons use `h` in **minutes** (`1h` -> 60, `72h` -> 4320).

### Critical feasibility constraint discovered

At 1-minute sampling, a 72h horizon overlaps **4320x**. Consequences:

1. **Effective sample size is far below nominal n.** Raw t-statistics are massively inflated. The state study MUST either subsample decision times to be non-overlapping, or apply an overlap correction. This stage uses **non-overlapping decision grids per horizon** for inference and reports the stride.
2. **MFE/MAE cannot be looped.** `build_forward_targets` uses a per-row Python loop (fine at 60 bars, impossible at 4320). Verified a vectorized replacement: `shift(-1).reverse().rolling_max(h).reverse()`, exact vs brute force and **0.45s for 2.6M rows x 4320 bars**.

## C. Stage 11 boundary

```text
Stage 11 tested short-horizon microstructure/flow information.
Stage 12 tests longer-horizon structural information.
Stage 12 is not a continuation of short-horizon optimization.
```

Stage 11's findings (largest effect ~+2 bps vs 11.01 bps cost; weak OOS AUC) are treated as fixed inputs. Stage 12 changes **only the horizon and the structural feature families**, and does not reinterpret them.

## D. Proposed new modules

```text
jev_trading/research/            (new package, additive)
  __init__.py
  __main__.py
  cli.py
  data.py          coverage report + timestamp contract
  targets.py       long-horizon targets, vectorized
  firewall.py      label-prefix detector (reuses Stage 11 is_label_column)
  features.py      families A-H
  state.py         market-state engine
  study.py         state-conditional study + BH-FDR
  models.py        walk-forward + embargo
  economics.py     cost application
  stability.py     year/regime/perturbation
  registry.py      thin wrapper over Stage 11 registry
  report.py        Stage 12 report + plots
research/stage12/  machine-readable artifacts
```

Stage 11 modules are **not modified** except where a shared helper is extended additively.

## E. Production files that must not change

`configs/live.json`, everything under `live_intelligence/`, `risk/`, `execution/`,
`policy/`, the barrier strategy, Frontier/Jev prompts.

## F. Verification before proceeding

- Audit document written (this file)
- Reusable components identified
- `git status` shows no production modification

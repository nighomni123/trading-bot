# Phase 3 pre-implementation audit — economic target discovery

Audit timestamp: 2026-09-25T04:28:56Z  
Repository commit: `2abb4d4` (`Add Economic Quant Engine v2 and record no-edge gate`)  
Baseline test command: `.venv/bin/python -m pytest -q`  
Baseline result: **192 passed, 0 failed**

This document is the required pre-implementation state record. It does not rewrite the
Phase 2 conclusion. Phase 2 remains frozen evidence with final status **NO EDGE**.

## 1. Repository and provenance state

- The tracked tree is rooted at commit `2abb4d4`, matching `origin/main`.
- The working tree was already dirty before Phase 3. Pre-existing untracked content is
  `.local/`, `docs/pre-jev/`, `kaggle/`, `scripts/build_kaggle_kernel.py`, and
  `scripts/run_kaggle_benchmark.sh`. Phase 3 must not alter or claim that content.
- The admissible pre-2025 research source is
  `artifacts/phase0-baseline-final-d/pre_oos_2021_2024.parquet`:
  - SHA-256 `bcd43b1e7a35cd1db11be3c074415051f64edaefa6c93b69c1eb1f4546d08a96`;
  - 2,103,840 rows;
  - `[2021-01-01, 2025-01-01)`;
  - unique, strictly increasing, contiguous one-minute timestamps.
- `data/btcusdt_1m.parquet` contains the historically consumed 2025+ data. It is not an
  eligible Phase 3 selection or confirmation source.
- Phase 2 tracked provenance names commit `67be354`, the commit immediately before the
  Phase 2 implementation commit. The generated artifact provenance and final tracked
  provenance also differ in code hashes for `quant/engine.py` and
  `run_phase2_experiments.py`. The committed Phase 2 metrics and saved model hash are
  preserved, but the generated-run provenance is not a byte-for-byte description of the
  final commit. Phase 3 must record this limitation rather than rewriting Phase 2.

## 2. DOCUMENTED STATE vs ACTUAL CODE STATE vs EMPIRICALLY VALIDATED STATE

| Area | Documented state | Actual code state | Empirically validated state | Status |
|---|---|---|---|---|
| Phase 2 architecture | 1m bars → causal 16 features → direction/return/MFE/MAE/holding/uncertainty → economic gate → fixed-horizon simulation | Present in `quant/engine.py` and `quant/evaluation.py`; not connected to live execution | Headline metrics and three walk-forward folds exist in tracked artifacts | `EMPIRICALLY VALIDATED` as a research path; `NO EDGE` |
| Features | 16 backward-only features | `state/features.py` uses current/past bars only; mutation and warm-up tests pass | Phase 0/2 experiments ran on the 16-feature set | `EMPIRICALLY VALIDATED` for causality; predictive value limited |
| Raw labels | Close-anchored 5m/15m/30m/60m returns and excursions | `compute_labels()` uses `t+1..t+H`, null tails, rejects gaps | EXP-009 reports the documented return and excursion metrics | `EMPIRICALLY VALIDATED`, no positive return edge |
| Phase 2 path labels | First TP/SL touch and timeout | `compute_path_labels()` is close-anchored, not executable-entry anchored | Phase 1/2 holding diagnostic used a 20bp/20bp, 60m path | `TESTED`, but not adequate as the Phase 3 economic target |
| Execution return | Entry `open[t+1]`, fixed-horizon exit `open[t+H+1]` | `compute_execution_labels()` implements this | Phase 2 direction and return heads used execution-aligned labels | `EMPIRICALLY VALIDATED` |
| Direction | Explicit down/flat/up | Three-class LightGBM with explicit probabilities | Accuracy 0.5561, macro OVR AUC 0.6593, log-loss 0.9502, Brier 0.5600 | `PREDICTION LIFT`; `NO EDGE` economically |
| Return prediction | Execution-return regressor | Three-seed 15m LightGBM ensemble; other horizons are single models | Mean uncertainty-adjusted predicted edge about `-0.001402`; raw 15m correlation 0.0189 | `REJECTED` as stable economic target |
| MFE/MAE | Conditional magnitude heads | Execution-aligned regressors | 15m MFE/MAE correlations 0.4324/0.3866 | `PREDICTION LIFT`; `NOT ECONOMICALLY VALIDATED` |
| Base costs | 5bp taker fee and 2bp slippage per side | Phase 2 fixed-horizon research subtracts 10bp fees + 4bp slippage from an unadjusted open-to-open return | Cost components are arithmetically consistent as an approximation | `TESTED` approximation, not exact fill accounting |
| Fill prices | Buys fill above reference; sells fill below | Event simulator and paper trader apply side slippage to fill price | Simulator/paper tests verify next-open and side slippage | `EMPIRICALLY VALIDATED` in event execution |
| Phase 2 realized gross return | Predicted and realized quantities are described as execution-aligned | Phase 2 simulation uses raw `open[t+1] → open[t+H+1]` return, then subtracts constant slippage instead of computing fill-to-fill return | The constant-cost approximation is close but not identical to executable fill accounting | `MISMATCH DOCUMENTED`; controlled Phase 3 correction required |
| Funding | Per-eight-hour funding quote; event simulator charges when a held rate print changes | Phase 2 fixed-horizon gate and PnL prorate the current rate by `horizon/480` on every candidate | No held funding print occurs on most 15m trades, so proration is not the simulated funding convention | `MISMATCH DOCUMENTED`; controlled Phase 3 correction required |
| Delay | Execution degradation must be tested | Phase 2 `execution_delay` adds a fixed 0.02% penalty; it does not delay entry or shift the outcome window | No actual signal-delay sensitivity is stored | `NOT VALIDATED`; Phase 3 must add a true one-minute delay sensitivity |
| Uncertainty | Ensemble dispersion is an economic penalty | Phase 2 subtracts `uncertainty × abs(predicted gross)` and requires a non-null estimate | Error buckets are non-monotonic; Phase 2 reports rank correlation 0.1 for the frozen derived-feature result and 0.4 for the base artifact | `REJECTED` as calibrated economic confidence |
| Minimum edge | Policy multiplier 2.0 | Phase 2 requires adjusted **net** edge ≥ 2 × scenario cost | Gate behavior is tested; it is intentionally stringent | `TESTED`, not a profitability claim |
| Economic layer vs experiment | One deterministic economic path | `quant/economic.py` and `quant/evaluation.py` duplicate related formulas; the Phase 2 runner uses the latter directly | Tests cover both separately, not one shared end-to-end quantity | `MISMATCH DOCUMENTED`; Phase 3 uses one corrected evaluator |
| Short-side funding in schema | Side-aware economic output | `quant/economic.py` accepts a signed `funding_rate` convention but does not itself apply `side`; the Phase 2 evaluator does | No single end-to-end test proves schema and experiment funding are identical | `NOT VALIDATED`; excluded from the Phase 3 path |
| Model persistence | Loadable economic bundle | Pickled `EconomicModelBundle` plus JSON metadata; current `QuantPredictor` loads it | Saved Phase 2 bundle hash matches `model_hashes.json` and loads in the current environment | `EMPIRICALLY VALIDATED` for Phase 2 bundle |
| Walk-forward | Chronological, 60-minute purge/embargo | Three expanding folds, no random temporal shuffle | Adjusted edges are negative in all folds; selected trades 12/5/9 | `NO EDGE` |
| Cost stress | Base, 2×, 3×, extra slippage, delay | Base/2×/3× and extra-slippage gates abstain; delay is penalty-only | Base selected-trade PnL is positive but tiny-sample and not cherry-picked | `NOT ROBUST`; abstention under stress is explicit |
| Regime | Volatility, volume, trend, funding, OI reporting | Phase 2 artifact covers volatility, volume, trend, funding; OI is not a reported regime | All 35 eligible bars and 9 selected trades are high-volatility/high-volume/trending | `INSUFFICIENT EVIDENCE` |
| 2025+ provenance | Fresh final holdout required | Current Phase 2 scripts reject/read only pre-2025 for selection | 2025+ was consumed by historical experiments | `REJECTED` as fresh OOS; `DEFERRED` pending genuinely future data |
| Higher-level systems | Blocked without Quant evidence | Frontier, Laya, Jev, RL, and live code remain isolated | No Phase 3 incremental-value test is authorized | `DEFERRED` |

## 3. End-to-end Phase 2 hypothetical trade trace

For a decision row `t`:

1. `build_phase2_features()` reads bars `0..t`; row `t` may use its completed close.
2. The economic-v2 bundle predicts explicit direction, raw return, execution return,
   MFE, MAE, holding time, and ensemble dispersion at `t`.
3. The side is the most probable non-flat direction class.
4. The execution target enters at `open[t+1]` and exits at `open[t+H+1]`.
5. Predicted gross is `side × predicted_execution_return`.
6. Predicted fixed cost is two fees plus two slippage increments.
7. Predicted funding is prorated as `side × current_rate × H / 480`.
8. Predicted net subtracts fixed cost and funding.
9. The uncertainty penalty is `dispersion × abs(predicted gross)`.
10. A trade is eligible only if adjusted net edge ≥ `2 × scenario cost`.
11. The Phase 2 fixed-horizon simulator walks chronologically, skips feature-null gaps,
    blocks overlapping `H`-minute trades, and realizes raw open-to-open return.
12. Realized PnL subtracts the same constant fee/slippage amount and prorated funding.
13. The uncertainty penalty affects selection but is not subtracted again from realized PnL.
14. The event simulator is a separate path: it applies actual side-adjusted fill prices,
    fees on each fill, and discrete funding rate changes. It does not consume the Phase 2
    economic bundle.

The central mismatch is therefore not the feature or direction timestamp. It is the
translation from an execution-aligned **reference return** to an executable **fill-to-fill
trade**, especially for slippage, fees, funding events, and delay.

## 4. Phase 3 correction boundary

Phase 3 will add an isolated research path and will not alter Phase 2 artifacts or connect
anything to live execution. The controlled correction will:

- start at `open[t+1]`;
- apply side-adjusted entry and exit slippage exactly once;
- charge entry and exit fees on their actual notionals;
- charge funding only on discrete held funding prints for realized trades;
- use the latest visible funding rate only as a point-in-time expectation for future
  discrete prints in predicted EV;
- test one additional minute of actual entry delay, not only a fixed penalty;
- keep the 2× minimum-edge multiplier;
- report the old Phase 2 accounting and corrected accounting side by side;
- treat non-monotonic ensemble dispersion as diagnostic only, not economic confidence.

A changed PnL number alone cannot create an edge. The correction is accepted only if its
ablations are fully disclosed and the Phase 2 decision remains unchanged unless the
corrected evidence independently clears the Phase 3 gate.

## 5. Phase 3 pre-registered controls

The following are mandatory and are not selected on validation PnL:

- **Control A:** frozen Phase 2 economic-v2 baseline.
- **Control B:** unconditional expected-return/EV baseline.
- **Control C:** naive momentum direction with identical execution and costs.
- **Control D:** barrier/payoff baseline using training outcome frequencies only.
- **Experimental model:** barrier outcome probabilities plus opportunity/direction
  decomposition and corrected cost-adjusted EV.

Pre-2025 data is partitioned into a discovery period and later validation folds. The full
100-cell TP × SL × horizon grid is evaluated on discovery. Any stable region must be frozen
before later-fold evaluation. All 2023–2024 results remain validation evidence, not fresh
OOS. The historically consumed 2025+ period is not read by Phase 3 selection code.

## 6. Historical YAML syntax repair

The pre-existing EXP-001, EXP-002, and EXP-003 `experiment.yaml` files contained
unquoted multiline/colon-bearing scalars and did not parse as YAML. Phase 3 repaired
their syntax only: values, verdict text, ranges, and conclusions were preserved, and a
syntax-only note was added to each file. No Phase 2 result or artifact was reinterpreted.
The repaired files are included in the final parse verification.

## 7. Audit decision

**IMPLEMENTED / TESTED:** Phase 2 research architecture and negative result are real.  
**NOT VALIDATED:** exact predicted-versus-realized economic quantity across all components.  
**EMPIRICALLY VALIDATED:** current baseline tests, data hash/range, and saved Phase 2 bundle.  
**PHASE 3 STATUS:** authorized to implement the isolated target/cost audit and barrier
research path. Frontier, Laya, Jev, RL, and live execution remain prohibited.

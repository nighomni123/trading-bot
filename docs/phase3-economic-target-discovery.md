# Phase 3 — Economic Target Discovery & Quant Edge Research

## Final status

**EMPIRICALLY VALIDATED AS A RESEARCH PROCESS; QUANT PROMOTION DECISION IS RECORDED IN
`experiments/phase3-verification.json`.**

Phase 2 remains frozen evidence and retains its original status: **NO EDGE**. Phase 3 does
not reinterpret the nine positive selected Phase 2 trades as an edge.

## 1. Research question

Can the current BTCUSDT perpetual 1-minute information set discover a stable, executable,
cost-adjusted representation of an economic opportunity suitable for later strategy
allocation?

The tested hypothesis is:

> The fixed-horizon terminal-return target may discard useful path/payoff information, so a
> side-specific barrier distribution plus separately learned opportunity and direction may
> be more economically useful.

The hypothesis is falsifiable. Prediction improvement alone is not economic evidence, and
no target, threshold, model, or feature family is selected from 2025+ data.

## 2. Frozen inputs and pre-registration

- Baseline code commit: `2abb4d4`.
- Admissible source:
  `artifacts/phase0-baseline-final-d/pre_oos_2021_2024.parquet`.
- Source SHA-256:
  `bcd43b1e7a35cd1db11be3c074415051f64edaefa6c93b69c1eb1f4546d08a96`.
- Source range: `[2021-01-01, 2025-01-01)`, 2,103,840 contiguous one-minute rows.
- Features: the existing 16 causal features only.
- Discovery: train 2021, selection-validation 2022.
- Confirmation: expanding folds 2021→2022, 2021–2022→2023, and 2021–2023→2024.
- Purge/embargo: 60 minutes on each side of every chronological boundary.
- Grid: 5 TP × 4 SL × 5 horizon = 100 cells.
- Costs: 5bp taker fee and 2bp slippage per side; minimum edge = 2× scenario cost.
- Stress: 2× fees, 3× fees, +5bp slippage per side, and one actual additional minute of
  entry delay.
- Model: paired long/short LightGBM three-class heads plus side-specific
  `E(timeout return | TIMEOUT)` regressors, 20 trees, 15 leaves, seed 7. A target cell
  with fewer than two timeout rows uses the side-specific training mean, recorded as a
  deliberate rare-class fallback. Three seeds `(7,17,27)` are used only for uncertainty
  diagnosis.
- Probability calibration transform: none. Raw probability calibration is measured, not
  fitted on validation.
- Full machine-readable lock: `configs/phase3.json`.

The existing 2025+ interval is historically consumed and is not a fresh holdout. A fresh
future period must begin only after any candidate freeze.

## 3. Phase 3 architecture

```text
1m causal 16-feature state
  ├─ terminal-return / explicit-direction control
  ├─ long TP/SL/TIMEOUT probability head
  ├─ short TP/SL/TIMEOUT probability head
  ├─ opportunity TRADE/NO_TRADE head
  └─ side-specific E(timeout return | TIMEOUT) head
          ↓
side-specific executable payoff distribution
          ↓
cost-adjusted expected value
          ↓
NO_TRADE / TRADE
          ↓
non-overlapping research simulation
          ↓
walk-forward, regime, uncertainty, and cost confirmation
```

No component is connected to policy, risk, live execution, Frontier, Laya, Jev, or RL.

## 4. P3.0 economic measurement correction

The frozen Phase 2 accounting used raw `open[t+1] → open[t+16]` return and then subtracted
constant fee/slippage, while the event simulator uses side-adjusted fill prices and discrete
funding prints. The corrected Phase 3 path:

1. enters at the raw executable `open[t+1]` reference and applies side slippage to the
   actual entry fill;
2. exits at the barrier reference or `open[t+H+1]` and applies side slippage to the actual
   exit fill;
3. charges entry and exit fees on their actual notionals;
4. charges realized funding only when a held visible rate print changes;
5. predicts future discrete funding events with the current visible rate and training-only
   event-time distributions;
6. tests a true extra one-minute entry delay;
7. retains the minimum-edge multiplier and reports the old Phase 2 result beside the
   corrected result.

The controlled EXP-020 result is stored under
`experiments/EXP-020-economic-measurement-audit/`. Historical artifacts are unchanged.

## 5. Barrier labels

For decision row `t`, the entry is `open[t+1]`. High/low barriers are checked on
`t+1..t+H`; timeout exits at `open[t+H+1]`.

- Long: high touches TP, low touches SL.
- Short: low touches TP, high touches SL.
- Exact touches are inclusive with floating-point-safe equality.
- Simultaneous TP/SL in one OHLC bar is `SL_FIRST` because OHLC cannot establish intrabar
  ordering; an explicit ambiguity flag is retained.
- Timestamp gaps are rejected, never compressed.
- Tail rows without `open[t+H+1]` are null.
- The same implementation supplies MFE, MAE, time-to-TP, time-to-SL, and timeout return.

## 6. Probability and economic-value model

Each side has an explicit three-class distribution:

```text
P(SL_FIRST), P(TIMEOUT), P(TP_FIRST)
```

The probability report includes log-loss, multiclass Brier score, accuracy, macro OVR AUC
when defined, confusion counts, per-class precision/recall/F1, class balance, expected
calibration error, and reliability bins. Probability means are also reported by volatility,
volume, OI, trend, and funding regimes.

Expected value uses side-specific deterministic TP/SL payoffs, a trained timeout-return
expectation, and point-in-time expected discrete funding:

```text
EV = P(TP) × TP_net
   + P(SL) × SL_net
   + P(TIMEOUT) × E(timeout_net)
```

The 2× cost hurdle is applied to net EV. A stress scenario with zero trades is recorded as
abstention, not robustness.

## 7. Controlled decomposition and baselines

- **Model A:** learned LONG/FLAT/SHORT direction.
- **Model B:** opportunity probability ≥ 0.5 plus naive momentum direction.
- **Model C:** opportunity probability ≥ 0.5 plus learned direction.
- **Model D:** opportunity probability ≥ 0.5 plus learned direction and side-specific
  barrier payoff EV.
- **Control A:** corrected Phase 2 terminal-return gate.
- **Control B:** unconditional training-mean return side.
- **Control C:** naive momentum direction.
- **Control D:** unconditional training-frequency barrier/payoff EV.

Thresholds are not relaxed to increase trade count.

## 8. Target-shape and multiple-testing control

The full grid is evaluated on discovery only. A cell passes only with at least 30 trades,
positive base expectancy, and positive selected predicted EV. A stable region requires at
least three connected passing cells, each with at least two passing orthogonal TP/SL
neighbors. An isolated optimum is explicitly labeled isolated and cannot be promoted.

Only the frozen region is eligible for later confirmation. If no region exists, the
pre-registered 20bp/20bp/15m sentinel is walked forward as a diagnostic and cannot become a
candidate.

`experiments/EXP-023-target-shape-sweep/target-shape.csv` and `heatmaps.md` contain the full
controlled grid.

## 9. Existing-state and rare-event analysis

The sentinel target is grouped into all eligible, TP-first, SL-first, timeout, high-MFE, and
high-MAE populations. The report includes feature distributions, strongest feature
interactions, class balance, and standardized TP-vs-SL mean differences. No observed
relationship is automatically promoted to a feature.

The rare-event report decomposes the frozen Phase 2 gate in order:

```text
valid model observation
→ explicit direction
→ positive gross return
→ positive net after cost
→ uncertainty-adjusted positive
→ 2× risk/economic hurdle
```

It also reports target opportunity prevalence, learned opportunity selection, and final
trade count without loosening any gate.

## 10. Regime, uncertainty, and cost robustness

Regime cutpoints are estimated only from each fold's training data. No regime-specific
strategy is created. Each regime reports observations, trades, expectancy, net return,
drawdown, and 2× positive fraction.

Ensemble EV dispersion is tested against absolute economic forecast error and adverse
outcome. It is not used as a penalty unless monotonic reliability is observed. Phase 3
therefore reports it honestly but does not force a monotonic result.

Cost confirmation distinguishes:

- frozen base selections repriced under stress; and
- selections re-evaluated under stressed predicted costs.

Both zero-trade outcomes and positive surviving populations are reported explicitly.

## 11. Microstructure scope

The local admissible dataset contains OHLCV, forward-visible funding, and open interest
only. It has no point-in-time BTCUSDT spread/depth/order-book/trade-flow/liquidation
history. EXP-027 through EXP-030 are therefore recorded as:

```text
NOT RUN — no provenance-safe historical microstructure data
```

No family is fabricated, backfilled from 2025+, or introduced simultaneously.

The final full-suite verification is **217 passed, 0 failed** with
`.venv/bin/python -m pytest -q`. All generated JSON/YAML artifacts parse successfully, and
all new runtime modules compile.

## 12. Empirically observed results

### 12.1 Economic measurement audit (EXP-020)

The frozen Phase 2 headline was reproduced exactly: 9 old-accounting trades and
`net_return_sum = 0.0813885`. That number is not promoted. Under exact fill-to-fill
accounting with the same uncertainty penalty, the same-sized population became:

- 9 trades;
- net return sum `-0.0124807`;
- expectancy `-0.0013867`.

Removing the non-monotonic uncertainty penalty produced 11 trades and net return sum
`-0.0064527`. A one-minute delayed sensitivity happened to produce 11 trades and
`0.1278074`, but it changed the selected population and is not a robustness result; the
sample is tiny and the delayed result is explicitly not used for promotion. Accounting
correction therefore strengthens, rather than overturns, the Phase 2 **NO EDGE** boundary.

### 12.2 Target-shape sweep (EXP-021–EXP-023)

All 100 pre-registered cells were constructed and evaluated. The discovery result was:

- base-cost trades: **0 across all 100 cells**;
- hurdle-eligible observations: **0 across all 100 cells**;
- maximum mean maximum predicted EV: `-0.0011617`;
- median probability log-loss: `1.0846990`;
- median probability Brier: `0.5481946`;
- median macro OVR AUC: `0.6048874`;
- stable regions: **none**;
- isolated profitable cells: **none**.

The probability metrics show measurable ranking information in some cells, but no cell
produced a population that cleared the pre-registered cost hurdle. The complete tables
are in `experiments/EXP-023-target-shape-sweep/target-shape.csv` and `heatmaps.md`.

### 12.3 Opportunity/direction decomposition (EXP-024)

The sentinel 20bp/20bp/15m experiment produced:

- direction-only: 34,993 trades, expectancy `-0.0013942`;
- opportunity + naive direction: 34,986 trades, expectancy `-0.0014402`;
- opportunity + learned direction: 34,993 trades, expectancy `-0.0013942`;
- opportunity + learned direction + payoff EV: **0 trades**;
- corrected Phase 2 terminal control: 20 trades, expectancy `-0.0024826`;
- unconditional and naive controls: approximately 35,000 trades, both negative.

The opportunity target was positive for 90.93% of discovery observations, while the
learned opportunity head selected 99.999% at the 0.5 threshold. It was not selective enough
to repair economics. Model D did not improve Model C.

### 12.4 State and rare-event diagnosis (EXP-025–EXP-026)

The corrected state artifact contains 524,895 eligible discovery observations. High-MFE
and high-MAE populations are primarily high-volatility/high-ATR states; descriptive
standardized TP-vs-SL differences are small. No new feature was promoted.

The frozen Phase 2 gate decomposition was:

```text
valid observations       525,395
direction gate            15,128
gross-return gate         10,518
cost gate                    185
uncertainty gate              164
2× risk/economic hurdle         35
```

The scarcity is therefore primarily a cost/hurdle and calibration problem, not a reason to
increase trade frequency or loosen thresholds. The barrier opportunity target was positive
for 90.93% of rows, but the hurdle still selected zero trades.

### 12.5 Walk-forward, uncertainty, and cost confirmation (EXP-031–EXP-032)

No stable discovery region existed, so the pre-registered 20bp/20bp/15m sentinel was walked
forward as a diagnostic only. It selected zero trades in 2022, 2023, and 2024. The
probability/EV uncertainty diagnostic was not monotonic with economic error or adverse
outcome, so it was not used as a penalty. All base, 2×, 3×, extra-slippage, and actual-delay
scenarios either had no trades or negative predicted economics. A zero-trade stress result
is recorded as abstention, never as robustness.

## 13. Final decision

# NO EDGE

Phase 3 does not produce a stable, cost-adjusted economic opportunity representation from
the current 16-feature BTCUSDT 1-minute information set. Predictive ranking and conditional
excursion information do not translate into executable net expectancy. No candidate is
frozen, no fresh holdout is claimed, and Frontier/Laya/Jev/RL/live execution remain blocked.

The next falsifiable hypothesis is narrower: obtain a provenance-safe, point-in-time
historical **M1 spread/liquidity** family and test it alone against the frozen baseline.
Do not add several microstructure families, increase model complexity, or reinterpret the
Phase 2 selected-trade PnL as an edge.

Run from the repository root:

```bash
.venv/bin/python scripts/run_phase3.py --stage all --out experiments
.venv/bin/python -m pytest -q
```

Canonical artifacts:

- `docs/phase3-audit.md`
- `docs/phase3-economic-target-discovery.md`
- `experiments/phase3-verification.json`
- `experiments/phase3-provenance.json`
- `experiments/phase3-registry.json`
- `experiments/EXP-020-*` through `experiments/EXP-032-*`

The exact runtime decision, headline metrics, negative findings, and fresh-holdout status
are taken from the structured artifacts rather than selected prose in this document.

# GLOSSARY — jev-trading

Every technical term and short form used in this repository, defined **as this repo uses it** —
not as a generic dictionary would. If a term appears in `docs/`, `experiments/`, `configs/`,
`src/`, or `AGENTS.md`, it is here.

**What this project is:** a research-first, **paper/shadow-only** BTCUSDT perpetual
market-intelligence system. It has no live-money order path, no profitability claim, and no
autonomous self-modification. As of the current freeze its economic verdict is **NO EDGE**.

---

## 1. System components (the live architecture)

Named in `README.md`'s architecture block, top to bottom.

| Term | Meaning |
|---|---|
| **Data fabric** | Source-aware ingest layer. Preserves source, venue, instrument, market type, event time, receive time, and sequence identity. Freshness is judged on *event time*, not receive time. Bad data fails closed. |
| **Market environment** | Builds completed, causal 1m/5m/15m/1h/4h observations. Carries price, structure, volatility, flow, liquidity, derivatives, events, position, and data-quality state. |
| **Quant** | The typed analytical *toolkit* (not one trained return predictor). Emits `QuantEvidence`. Path samples carry their exact target/stop/horizon identity. |
| **QuantEvidence** | The canonical evidence record handed from Quant to Jev. |
| **Frontier** | The slow, OpenAI-compatible LLM strategist. Receives bounded environment/Quant/research context, returns a validated `StrategyHypothesis`. **Cannot** size, leverage, execute, or override risk. |
| **Jev** | The short-validity, OpenAI-compatible LLM contextual evaluator. Gets `QuantEvidence` + candidate + position + Frontier questions. ENTER answers must pass configured probability, entry-quality, failure-risk, and expiry gates. |
| **Policy** | Deterministic Python. Turns validated evidence into one of the seven `PolicyAction` values. No LLM in this path. |
| **Risk** | Independent deterministic authority with absolute veto. Owns paper account state; enforces kill switch, data health, sizing, daily loss, drawdown, order rate, cooldown, spread, slippage, liquidity, and position limits. |
| **Execution** | PAPER only. Fills at the next one-minute open, side-aware slippage, full fee/PnL accounting, deterministic visible funding, explicit full-fill semantics. |
| **Ledger / hash chain** | Append-only, hash-chained record of every decision and trade. Has semantic intent/fill/trade links. |
| **Checkpoint** | Atomic runtime state snapshot, validated against the ledger hash on startup. |
| **Research memory** | Structured, derived, immutable research reports fed back to Frontier as **read-only** context. Never a self-modification channel. |
| **Replay** | Reconstructs recorded decisions and their versions from the ledger. **Never calls a provider.** |
| **Shadow** | Running the full live pipeline against live data with zero real money. |
| **Paper** | Execution mode where fills are simulated. The only mode that exists. |

### Policy actions (`PolicyAction` in `src/jev_trading/live_intelligence/schemas.py`)

| Value | Meaning |
|---|---|
| `NO_TRADE` | Abstain. The economic gate found no edge. |
| `ENTER_LONG` | Open a long position. |
| `ENTER_SHORT` | Open a short position. |
| `HOLD` | Keep the existing position unchanged. |
| `REDUCE` | Partially close the position. |
| `EXIT` | Fully close the position. |
| `DATA_UNSAFE` | Blocked by data health, not by economics. |

`JevState` carries a parallel set for the evaluator: `ENTER`, `WAIT`, `EXIT`, `REDUCE`, `HOLD`,
`NO_TRADE`. `RiskStatus` is only `APPROVED` / `REJECTED`.

---

## 2. Codenames, models, and providers

| Term | Meaning |
|---|---|
| **JEV** | The bounded contextual evaluator component (also the repo name, `jev-trading`). |
| **Frontier** | The LLM strategist layer. Distinct from Jev: slow, hypothesis-producing, not a gate. |
| **Laya** | A *planned* specialist fine-tuning / LoRA layer. **Stub only** — `frontier/laya.py` holds typed schemas, nothing is implemented or trained. |
| **OpenRouter** | The LLM gateway supplying Frontier/Jev models via an OpenAI-compatible `/chat/completions` API. |
| **Ling** | An OpenRouter model (`inclusionai/ling-3.0-flash-fin:free`). Its model page reports **no** `response_format` / structured-output support, so the client relies on prompt + strict Pydantic validation and fails closed on non-conforming output. |
| **Nemotron** | An OpenRouter model with a documented capability profile alongside Ling. |
| **`openrouter/free` router** | The free auto-router. Explicitly **non-comparable** for model-quality experiments. |
| **Capability profile** | Explicit `ProviderConfig` flags (`supports_response_format`, `supports_tool_calling`, `supports_reasoning`, `supports_vision`) in `configs/openrouter_profiles.json`. Capabilities are **configuration, never inferred from model names**. |
| **Provider modes** | `disabled` (default, fails closed), `replay` (returns recorded outputs, no API call), `openai_compatible`. |
| **MockJev / RandomJev** | Test doubles for the Jev evaluator. `MockJev` has no order or size methods by construction. Results from it are evidence about the *adapter contract*, never about evaluator quality. |
| **`JEV_MODEL` / `FRONTIER_MODEL`** | Env vars that override the JSON model placeholders in `configs/live.json`. |
| **LEAN** | The external QuantConnect backtesting engine, used in `EXP-LEAN-001/002` to cross-check that the local backtester and LEAN agree. |

---

## 3. Experiment arms — the ablation ladder

An **arm** is one named configuration in an ablation. Same data, costs, splits, and execution
contracts; only the decision stack varies.

### Live-system arms (`README.md`, `ShadowRunner`)

| Arm | Aliases | Stack |
|---|---|---|
| **A** | `QUANT_ONLY`, `QUANT_POLICY` | Quant + deterministic Policy + Risk |
| **B** | `QUANT_FRONTIER` | Quant + **Frontier** + Policy + Risk |
| **C** | `QUANT_FRONTIER_JEV` | Quant + **Frontier + Jev** + Policy + Risk |

> Arm A is **not** a profitability claim. The incremental question is `Perf(C) − Perf(B)`, i.e.
> what Jev adds on top of Frontier.

### Phase-4 predictive baselines (`docs/evaluation-protocol.md`)

| Arm | Model |
|---|---|
| A | Naïve — always predict the class prior |
| B | Momentum — `ret_5m > 0 → up else down` |
| C | Trend — `EMA20 > EMA50 → bullish` |
| D | Logistic regression on `FEATURE_COLUMNS` |
| E | LightGBM — **only if D shows OOS signal over A–C** |

### Phase-7 decision-stack arms (`docs/evaluation-protocol.md`, `experiments/EXP-003`)

| Arm | Model |
|---|---|
| A | Random control (seeded) |
| B | Quant-probability threshold rule |
| C | Quant → policy → risk |
| D | Quant → mock-Jev → policy → risk |
| E | Jev-disabled policy (same conditions as D) |

Incremental contribution of Jev = `Perf(D) − Perf(C)`.

### EXP-004 real-Jev arms (`docs/exp-004-real-jev.md`)

`A` Quant baseline · `B` Quant + policy, no Jev · `C` Quant + Jev · `D` Quant + Policy + Jev.
Result: C and D produced **0 trades** vs B's 543 → classified `JEV_NO_INCREMENTAL_VALUE`.

### Phase-3 decomposition models (EXP-024)

**A** learned LONG/FLAT/SHORT direction · **B** opportunity ≥ 0.5 + naive momentum direction ·
**C** opportunity ≥ 0.5 + learned direction · **D** C + side-specific barrier-payoff EV.
**Controls:** A = corrected Phase-2 terminal-return gate · B = unconditional training-mean return ·
C = naive momentum · D = unconditional barrier/payoff EV.

### Stage-9 barrier models (B0/B1/B2)

**B0** trailing-frequency estimator (what the live gate actually uses) · **B1** LightGBM binary ·
**B2** LightGBM 3-class.

---

## 4. Phases, stages, and the experiment registry

Two overlapping numbering schemes exist. Do not mix them.

| Scheme | Meaning |
|---|---|
| **P0–P9** (plan) | Build phases in `IMPLEMENTATION_PLAN.md`: P0 Bootstrap, P1 Data, P2 State, P3 Labels, P4 Quant, P5 Frontier/Laya stub, P6 Risk, P7 Event Simulator, P8 Shadow, P9 Frontier strategist. |
| **Phase 0–3** (research gates) | Evidence phases in `docs/`: Phase 0 baseline freeze, Phase 1 economic target gate, Phase 2 economic engine v2, Phase 3 target discovery. |
| **Stage 1–9** (live-system diagnostics) | `docs/stage-*.md`: stage 1 baseline, stages 4–7 report, stage 8 economic diagnostic, stage 9 barrier model experiment. |
| **Gate** | A pass/fail criterion a phase must clear to continue. A gate that fails ends the phase — it does not get worked around. |
| **Verdict / decision** | The terminal field of an experiment: `PASS`, `NO_EDGE`, `STOP`, `DEFER`, etc. |
| **Pre-registered** | Configurations, grids, and thresholds fixed *before* any result was seen. Written to `configs/phase3.json` and the experiment's own lock file. |
| **Locked config** | `locked_config.json`, written after validation and before OOS. Evidence nothing was retuned. `--final-oos` refuses to run without it. |
| **Provenance** | The record of exactly what code, data, config, model, and prompt produced a result. |
| **Experiment ID** | `EXP-NNN-<slug>`, one experiment = one question. |

### The full experiment registry

| ID | Question it answered |
|---|---|
| `EXP-001` | Data ingest + label sanity (Phase 0) |
| `EXP-002` | P4 predictive baselines A–E |
| `EXP-003` | P7 simulator: does the edge survive costs? |
| `EXP-004` | Real Jev selectivity / quant probability integrity |
| `EXP-004R` | Repair report for EXP-004 |
| `EXP-006` | Metrics full report / economic redesign |
| `EXP-007B` | Historical target comparison (invalid as OOS) |
| `EXP-007C_7E` | Historical feature-family comparison (not a real ablation) |
| `EXP-008-phase0-baseline` | Frozen reproducible current baseline |
| `EXP-009-economic-targets` | Do trained return/MFE/MAE heads give cost-adjusted edge? → **STOP** |
| `EXP-010-economic-return-baseline` | Phase-2 economic v2 baseline |
| `EXP-011-downside-excursion` | Derived features + explicit direction |
| `EXP-012-uncertainty` | Is ensemble dispersion a usable error proxy? |
| `EXP-013-feature-ablation` | Which of the 16 features matter? |
| `EXP-014-momentum` | Momentum specialist — `DEFER` |
| `EXP-015-mean-reversion` | Mean-reversion specialist — `DEFER` |
| `EXP-016-breakout` | Breakout specialist — `DEFER` |
| `EXP-017-specialist-ensemble` | Specialist ensemble / arbitration — `DEFER` |
| `EXP-018-walk-forward` | Three expanding walk-forward folds |
| `EXP-019-cost-stress` | Base / 2× / 3× cost stress |
| `EXP-020-economic-measurement-audit` | Corrected fill-to-fill accounting vs frozen Phase 2 |
| `EXP-021-barrier-target-construction` | Barrier label construction |
| `EXP-022-barrier-probability-model` | Three-class barrier probability head |
| `EXP-023-target-shape-sweep` | The full 100-cell TP × SL × horizon grid |
| `EXP-024-opportunity-direction-decomposition` | Opportunity detection vs direction |
| `EXP-025-existing-state-analysis` | Feature distribution by outcome state |
| `EXP-026-rare-event-gate-decomposition` | Where do the 525k observations die? |
| `EXP-027/028/029-microstructure-family-a/b/c` | Microstructure families — **NOT RUN**, no provenance-safe data |
| `EXP-030-combined-causal-feature-set` | Combined causal set — **NOT RUN** |
| `EXP-031-walk-forward-confirmation` | Walk-forward sentinel as diagnostic |
| `EXP-032-cost-stress-confirmation` | Base / 2× / 3× / slippage / delay confirmation |
| `EXP-LEAN-001` | Local backtester vs LEAN, baseline strategy only |
| `EXP-LEAN-002` | Local backtester vs LEAN, full Quant+Policy+Jev+Risk stack |

---

## 5. Verdict and decision vocabulary

These are the *conclusion tokens*. Note the deliberate distinction: `NO EDGE` and `STOP` are
**successful outcomes of the experiment**, not failures of the experiment.

| Token | Meaning |
|---|---|
| `PASS` | The gate was cleared. |
| `NO EDGE` / `NO_EDGE` | The binding verdict of Phase 2 and Phase 3. The information set does not produce a cost-surviving opportunity. |
| `STOP` | Stop the pipeline. Do not add the next layer (Frontier, Jev, specialists, LLM). |
| `STOP/ITERATE` | Edge did not survive costs; the honest next step is a new hypothesis, not a tweak. |
| `DEFER` / `DEFERRED` | The prerequisite failed, so the experiment was not run. **Neither success nor failure.** |
| `NOT RUN` | Blocked for lack of admissible data (all microstructure families). |
| `FAIL-ECON` | Stage 9's verdict: prediction is real but small, and the *geometry itself* cannot clear costs. |
| `JEV_NO_INCREMENTAL_VALUE` | EXP-004's final classification. Jev suppressed all activity without measurable selectivity gain. |
| `PASS_CURRENT_BASELINE_ONLY` | Reproducible baseline, explicitly not a profitability claim. |
| `EMPIRICALLY VALIDATED` | The process ran as pre-registered and the measurement held. Says nothing about profit. |
| `REJECT` / `REJECTED` | Evidence is not admissible as OOS evidence (see the historical Phase 7 files). |
| `BLOCKED` | Cannot proceed; waiting on credentials, a gate, or data. |
| `NOT SHADOW READY` | The shadow-readiness acceptance gate did not pass. |
| **Abstention, not robustness** | A stress scenario that yields **zero trades** is recorded as abstention. It is never reported as evidence the strategy is robust. |
| **Hurdle** | The cost bar a trade must clear. Here: `min_edge_over_cost = 2.0`, i.e. 2× the round-trip cost. |
| **Fee floor** | The round-trip cost below which no signal in this system can be profitable (~15 bps). |

---

## 6. Data and market-data terms

| Term | Meaning |
|---|---|
| **BTCUSDT perp / PERPETUAL** | The instrument: Binance USDT-margined perpetual future, `venue: binance-futures`. |
| **bar / candle** | One OHLC interval. This system is 1-minute: `BAR_COLUMNS = open, high, low, close, volume, funding, open_interest`. |
| **OHLC** | Open, High, Low, Close. A bar's four price fields. |
| **Mark price / index price** | Venue-derived reference prices, distinct from last-trade price. Both carried in `MarketTick`. |
| **Funding rate / funding** | Perpetual swap funding, quoted **per 8 hours**. Charged only when a held position spans a discrete funding print. The historical code once treated this as a per-minute charge — that was a bug. |
| **Open interest (OI)** | Total open derivative contracts. Tracked as `oi_change_1d`. |
| **Liquidation** | Forced position closures streamed by the venue when they exist; absent from the local pre-2025 dataset. |
| **Microstructure** | Order-book, spread, depth, trade-flow, and liquidity data. Families A/B/C were specified and **not run** — the local dataset has none of it. |
| **Point-in-time** | A value knowable at the bar's own timestamp, never backfilled. The core anti-leakage discipline. |
| **Event time / receive time** | When the event happened vs. when it arrived. Freshness uses event time. |
| **Provenance-safe** | A field admissible for research because it can be reconstructed point-in-time from the pre-2025 data. |
| **Warmup** | The leading bar window (3000 bars) skipped so rolling features are defined before the first decision. |
| **Contiguous** | No gaps in the minute series. Gaps are **rejected**, never silently compressed into a shorter horizon. |
| **Parquet** | Columnar data format for `data/btcusdt_1m.parquet` (2,629,440 rows, 2021-01-01 .. 2025-12-31) and the frozen `pre_oos_2021_2024.parquet`. |
| **JSONL** | Append-only newline-delimited JSON. Used for decision ledger, event logs, and replay input. |
| **Event log** | The canonical per-bar record stream. **Schema v3**: one decision snapshot per bar, `exec_ts` strictly after `ts`, footer with record count and last entry hash. |
| **`verify_log()`** | Rejects missing/tampered records, invalid timing, and incomplete logs. |

---

## 7. The feature set

### The frozen 16 causal features (`FEATURE_COLUMNS` in `src/jev_trading/state/features.py`)

Backward-looking only. No feature may use its own bar's future.

| Feature | Meaning |
|---|---|
| `ret_1m`, `ret_5m`, `ret_15m`, `ret_60m` | Returns over 1/5/15/60 minutes |
| `realized_vol_5m`, `realized_vol_30m` | Realized volatility (dispersion of returns) over 5- and 30-minute windows |
| `atr_14` | **ATR** — Average True Range, 14 periods. The volatility unit barrier distances are scaled to. |
| `ema20`, `ema50`, `ema200` | **EMA** — Exponential Moving Average at 20/50/200 periods |
| `trend_score` | Composite trend-alignment score. Known ceiling: it saturates. |
| `funding` | Raw funding rate |
| `funding_z` | Funding rate's **z-score** against its own history |
| `oi_change_1d` | One-day change in open interest |
| `vol_regime` | Bucketed volatility state. The strategy registry gates on the labels `BULLISH` / `BEARISH` / `MIXED`. |
| `volume_z` | Volume's z-score |

### The 8 derived features (`PHASE2_DERIVED_COLUMNS`)

Tested as an intervention in EXP-013. **Not promoted** — eligible bars fell from 35 to 16 and
mean adjusted edge stayed negative.

`ema20_ema50_gap` · `ema50_ema200_gap` · `ema20_ema200_gap` · `trend_accel_15` ·
`trend_duration_60` · `ret_15_atr` (return normalised by ATR) · `ret_15_rv` (return normalised
by realized vol) · `funding_oi_interaction`.

Combined = `BASE_PLUS_DERIVED_FEATURE_SET` (23 features, reused in Stage 9).

**Causal** — computable from the past only. **Eligible bars** — bars that pass all feature
availability filters (an ablation's sparsity measure).

---

## 8. Labels, targets, and outcomes

| Term | Meaning |
|---|---|
| **H (horizon)** | The forward window in minutes. All labels at row `t` are point-in-time outcomes over exactly `t+1..t+H`. Row `t` never uses its own high/low. |
| **`y_up_15` / `y_dn_15`** | The original binary labels: `1[future_return_15m ≥ threshold]` and `1[future_return_15m ≤ −threshold]`. Threshold frozen at **0.0014**. |
| **Threshold 0.0014** | Cost-derived: `2 × (0.05% taker fee + 0.02% slippage)`. The full round-trip cost. |
| **`future_return_Hm`** | `close[t+H] / close[t] − 1` — close-anchored terminal return. |
| **MFE / `mfe_Hm`** | **Maximum Favorable Excursion** — the best unrealized gain over the horizon: `max(high[t+1..t+H]) / close[t] − 1`. |
| **MAE / `mae_Hm`** | **Maximum Adverse Excursion** — the worst unrealized loss: `min(low[t+1..t+H]) / close[t] − 1`. |
| **Side sign transform** | MFE/MAE and returns are stored long-side-signed. Short-side values are the inverse transform applied at consumption time. |
| **`execution_return_Hm`** | `open[t+H+1] / open[t+1] − 1` — the *execution-aligned* return, anchored to the price the simulator could actually fill. |
| **`execution_mfe/mae_Hm`** | Excursions relative to `open[t+1]`, using the entry bar's high/low through `t+H`. |
| **`direction_class_exec_15`** | Three-class direction: `0=down`, `1=flat`, `2=up`, with the cost hurdle applied. |
| **Path labels** | `time_to_tp`, `time_to_sl`, `tp_before_sl`, `timeout` for one explicitly selected barrier pair. |
| **Path sample** | A single historical outcome path with its own target/stop/horizon identity. In the live system, built by `build_completed_path_samples` and analysed by `analyze_path`. |
| **Path pair** | A `(tp, sl, horizon_bars)` triple defining one label geometry. |
| **Null (not zero)** | A partial window is null. A model using a 30m/60m target must purge/embargo at least that horizon. |
| **`Brier score`** | Mean squared error of a probability forecast. ~0.548 for the 3-class barrier head; 0.5600 for the direction head. |
| **Calibration** | How close stated probabilities are to observed frequencies. |
| **ECE** | **Expected Calibration Error** — the scalar summary of calibration. |

### Barrier / outcome vocabulary

The core of Phase 3. For decision row `t`, entry is `open[t+1]`; barriers are checked on
`t+1..t+H`; timeout exits at `open[t+H+1]`.

| Term | Meaning |
|---|---|
| **TP** | **Take profit** — the favourable exit barrier. |
| **SL** | **Stop loss** — the adverse exit barrier. |
| **TP_FIRST / TARGET_FIRST** | The take-profit barrier was touched before the stop within the horizon. |
| **SL_FIRST / STOP_FIRST** | The stop-loss barrier was touched first. |
| **TIMEOUT / timeout** | Neither barrier was touched; the position exits at the horizon. |
| **three-class / three_class** | The `TP_FIRST / SL_FIRST / TIMEOUT` outcome target. |
| **paired** | Long and short heads trained as a matched pair per bar, preserving side symmetry. |
| **`paired_lightgbm_three_class`** | The model family key for the paired LightGBM 3-class barrier head. |
| **`LONG_SL_FIRST_TIMEOUT`** | Paired long-side head: joint SL-first / timeout classification. |
| **`SHORT_SL_FIRST_TIMEOUT`** | The short-side counterpart. |
| **`CONDITIONAL_TIMEOUT_RETURN`** | The side-specific `E(timeout return | TIMEOUT)` regressor — what a timeout is worth on average. |
| **`CONDITIONAL_TIMEOUT_RETURN` head set** | The three heads above, locked in `configs/phase3.json`. |
| **Barrier geometry** | The `(target, stop)` pair, commonly expressed as a ratio: `1:1`, `2:1`, `3:1`. The live system uses **2:1 ATR-derived**. |
| **Same-bar touch / `SL_FIRST` policy** | If both barriers touch inside one OHLC bar, the outcome is recorded as **SL_FIRST** — conservative, because OHLC cannot establish intrabar ordering. An ambiguity flag is retained. |
| **Ambiguity flag** | `long_ambiguous` / `short_ambiguous` — marks that bar as a both-touch case. |
| **Side symmetry** | Long: high = TP, low = SL. Short: low = TP, high = SL. |
| **Exact touch** | Inclusive, floating-point-safe equality. |
| **Timeout exit** | `open[t+H+1]` — the first price after the monitoring window. |
| **`timeout_rate`** | Fraction of paths that reach the horizon untouched (9.67% at 20bp/20bp/60m). |
| **Barrier grid** | 5 TP × 4 SL × 5 horizon = **100 cells**. The full Phase-3 sweep. |
| **Sentinel** | One pre-registered barrier config (`20bp/20bp/15m`) carried forward as a diagnostic control when no stable region was found. |
| **Stable region** | ≥ 3 connected passing cells, each with ≥ 2 passing orthogonal TP/SL neighbours. An isolated optimum is explicitly rejected as noise. |
| **Base rate at live geometry** | TARGET_FIRST 31.7%, STOP_FIRST 62.6%, TIMEOUT 5.7% (Stage 9, correct ATR-scaled labels). |

---

## 9. Model and ML terms

| Term | Meaning |
|---|---|
| **LightGBM** | Gradient-boosted decision trees. The project's only production model family. Deterministic params: 20 trees, 15 leaves, `learning_rate` 0.05, `random_state=7`. |
| **Head** | One separately-trained model within a bundle, each predicting one target (direction, raw return, execution return, MFE/MAE, holding time, barrier outcome). |
| **Regression head** | A model predicting a *magnitude* (return), not a direction. |
| **Direction head** | The multiclass down/flat/up model. |
| **Binary model** | The original `p_up_15` classifier, preserved as the baseline reference. |
| **Ensemble / three-seed** | Seeds `(7, 17, 27)`, used **only** to diagnose uncertainty via normalized dispersion. |
| **Uncertainty (dispersion)** | Spread across the three seeds' predictions. Intended as an error proxy. **Result: not monotonic** with actual error, so it is *not* used as a penalty. |
| **`predict_proba`** | Probability output. Preserved deliberately — no hard-label regression. |
| **Seed** | Fixed RNG seed (`7`; `17`, `27` for the uncertainty ensemble). |
| **Deterministic** | Same input → same metrics, every run. `deterministic: true`, `force_col_wise: true`. |
| **Opportunity head** | Phase-3 model predicting *whether a tradeable event exists*, separate from direction. Result: positive for 90.93% of rows — not selective enough. |
| **TRADE/NO_TRADE head** | The explicit abstention model. |
| **Specialist** | A strategy-specific model (momentum, mean-reversion, breakout) trained **only after** the base economic prerequisite passes. All currently `DEFER`. |
| **Arbitration** | Expert-ensemble selection among specialists (EXP-017). |
| **Probability-weighted / fixed-excursion fallback** | **Explicitly invalid** economic evidence. Missing heads return an error / `NO_TRADE`. |

---

## 10. Predictive metrics

| Term | Meaning | Current value |
|---|---|---|
| **AUC** | Area under the ROC curve. Ranking quality, independent of any threshold. | LGBM 0.6439 (2024 validation); barrier 3-class macro OVR ~0.60 |
| **OVR** | **One-vs-rest** — the multiclass averaging scheme behind `macro_auc_ovr`. | — |
| **macro OVR AUC** | Macro-averaged one-vs-rest AUC across the three outcome classes. | 0.6593 (direction head) |
| **PR-AUC** | Precision-recall AUC. Baseline-rate sensitive. | 0.32–0.36 (barrier binary) |
| **Log-loss** | Negative log likelihood of the true class under the predicted distribution. The primary P4 gate metric. | 0.5158 (LGBM), 1.0847 (3-class barrier) |
| **Accuracy** | Fraction of correct class predictions. | 0.5561 (direction) |
| **Brier** | Mean squared probability error. | 0.5600 (direction), 0.5482 (barrier median) |
| **ECE** | Expected Calibration Error. | 0.0211 |
| **Correlation** | Prediction vs target. `reg_corr` ≈ 0.003 is no skill. 15m MFE corr ≈ 0.43 is conditional magnitude only. | — |
| **MAE / RMSE (error)** | Mean absolute / root mean squared error of a return prediction. Distinct from the excursion label `MAE`. | — |
| **Prior AUC** | The 0.500 coin-flip reference every model must beat. | 0.500 |
| **Naïve PnL** | PnL of an always-long rule (−$48.16) — shows the baseline is below just holding. | — |

---

## 11. Economic metrics

| Term | Meaning |
|---|---|
| **Gross PnL / `gross_pnl`** | Profit before costs. |
| **Net PnL / `net_pnl`** | Profit after fees, slippage, and funding. The headline number. |
| **Fees / slippage / funding** | The three cost components, always reported separately. |
| **Return %** | Net PnL as a percentage of starting capital. |
| **Expectancy** | Mean PnL per trade. The single most decision-relevant number in this repo — and it is negative in every fold. |
| **Hit rate / win rate** | Fraction of profitable trades. |
| **Sharpe** | Return per unit of variation. *The repo does not state an annualization period, risk-free rate, or whether it is per-trade or per-bar — treat values as ordinal, not absolute.* |
| **Sortino** | Like Sharpe, penalizing only downside deviation. *Same annualization caveat.* |
| **Max drawdown / max DD** | Worst peak-to-trough equity decline. A hard risk limit in `configs/risk.json`. |
| **Profit factor** | Gross gains ÷ gross losses. |
| **Turnover** | Total notional traded. Cost scales with it. |
| **Exposure** | Fraction of bars holding a position (time in market). |
| **Net expected value (EV)** | `EV = P(TP)×TP_net + P(SL)×SL_net + P(TIMEOUT)×E(timeout_net)`. |
| **Predicted net edge** | Predicted return minus fees, slippage, funding. |
| **Adjusted edge** | Predicted net edge minus the uncertainty penalty. |
| **`min_edge_over_cost` = 2.0** | The hurdle: adjusted edge must be ≥ 2× the scenario round-trip cost. |
| **Required `p_target`** | The target-hit probability a geometry needs to break even. Stage 9 measured it at **> 1.0** — arithmetically impossible. |
| **Eligible / selected trades** | Bars that cleared the gate and became trades. Scarcity (5–12 trades) is itself the finding. |
| **Hurdle-eligible observations** | Bars whose realized payoff clears the cost hurdle. |
| **Gate decomposition** | The stage-by-stage census of where observations die: valid 525,395 → direction 15,128 → gross return 10,518 → cost 185 → uncertainty 164 → 2× hurdle 35. |
| **Regime** | A market state bucket (volatility, volume, OI, trend, funding). Cutpoints estimated from training data only. |
| **Regime concentration** | Guard that no single regime holds most of the trades. |

---

## 12. Costs and execution semantics

The cost stack is the binding constraint of this whole project.

| Term | Meaning |
|---|---|
| **bps** | **Basis points**. 1 bp = 0.01%. The unit for every fee, slippage, and barrier distance here. |
| **Taker fee** | 5.0 bps per side (market order). `configs/costs.json`: `taker_fee_pct: 0.05`. |
| **Slippage** | 2.0 bps per side, applied **side-aware**: buys fill higher, sells fill lower. |
| **Latency penalty** | 1.0 bps, the cost of the one-minute entry delay. |
| **Round-trip cost** | `2 × (fee + slippage)` = **0.0014** (14 bps). The frozen `reference_round_trip_cost`. |
| **Full cost stack** | 15.01 bps = fees 10.00 + slippage 4.00 + latency 1.00 + funding 0.01. |
| **Fee multiplier / `fee_mult`** | Hostile-cost multiplier. Every experiment is re-run at **2× and 3×**. |
| **Extra slippage** | Adversarial slippage added on top of base. |
| **Execution delay** | One extra minute of entry delay, as a sensitivity. |
| **Spread** | Assumed bid-ask spread (1.0 bps in EXP-003). The live data carries real `spread_bps`. |
| **Fee floor** | Round-trip cost below which nothing here can be profitable. ~15 bps. |
| **`2× cost` / `3× cost`** | Stress scenarios. A **zero-trade** result is recorded as **abstention**, never as robustness. |
| **Next-bar / next-open execution** | Features from bar `t` → decision at `close[t]` → fill at `open[t+1]` + slippage. |
| **No same-bar fills** | The rule that makes the backtest trustworthy. Filling at `close[t]` is forbidden: the decision already knows that close. |
| **Lookahead** | Using information the decision could not have had. The thing all this prevents. |
| **Side-aware slippage** | Slippage applied against the trade direction. |
| **Full-fill semantics** | Venue partial fills are neither claimed nor silently simulated. |
| **Non-overlapping simulation** | One position at a time; feature nulls split the run into contiguous segments rather than compressing holding periods. |
| **Replay** | Reconstructing a run from the recorded event log. |
| **`verify_log()`** | The schema-v3, hash, sequence, timing, and completion-footer check. |

---

## 13. Risk terms (`configs/risk.json`)

| Key | Meaning |
|---|---|
| `max_position_btc` (0.1) | Maximum absolute position in BTC. |
| `max_leverage` (2.0) | Leverage cap. |
| `daily_loss_limit_usd` ($50) | Realized daily loss limit. |
| `trade_loss_limit_usd` ($20) | All-in per-trade loss limit. |
| `max_spread_bps` (5.0) | Spread cap; wider spread → reject. |
| `stale_data_ms` (15000) | Data staleness limit. |
| `max_orders_per_min` (12) | Order-rate cap. |
| `risk_per_trade_pct` (1.0) | Capital-at-risk sizing fraction. |
| `cooldown_ms` | Post-trade cooldown before a new entry. |
| `max_drawdown_pct` (0.0) | Drawdown limit; `0.0` means disabled in the current config. |
| **Kill switch** | Hard halt of the whole system. |
| **Halt** | Latched stopped state; `reset_manual()` to clear. |
| **Capital at risk** | Sizing basis for a proposed trade. |

Jev ENTER gates (`configs/live.json`): `minimum_target_probability` 0.55, `maximum_stop_probability`
0.45, `minimum_entry_quality` 0.55, `maximum_failure_probability` 0.45, `minimum_liquidity_quality` 0.0,
`validity_seconds` 60. Policy gates (`configs/policy.json`): `p_up_15`/`p_dn_15` ≥ 0.60,
`jev_trade_ok` ≥ 0.75, `jev_failure_max` ≤ 0.35, `min_edge_over_cost` 2.0.

### The five Jev questions (`configs/jev_questions.json`)

`breakout` · `liquidity_ok` · `vol_risk` · `failure_regime` · `trade_ok` — all yes/no. `trade_ok`
blends trend strength against failure risk; it is the effective gate.

---

## 14. Validation, splits, and leakage

| Term | Meaning |
|---|---|
| **Split** | Chronological only: train `[2021-01-01, 2023-12-31)`, valid 2024, test `[2025-01-01, 2026-01-01)`. |
| **OOS** | **Out-of-sample** — data never used for training or selection. |
| **Pre-OOS** | The admissible slice `[2021-01-01, 2025-01-01)` = `pre_oos_2021_2024.parquet`. |
| **Frozen OOS** | The reserved 2025 window, never trained or selected on. |
| **Consumed OOS** | 2025+ was already opened by historical experiments. It is permanently **invalid for selection** and can never become fresh OOS again. |
| **Fresh holdout** | A genuinely untouched future period. Required before any promotion. |
| **Purge** | Drop bars within one target horizon on the far side of a chronological boundary. |
| **Embargo** | A gap after the boundary preventing label-horizon bleed. |
| **`embargo_bars` = 15** | The 15-minute embargo at every boundary for a 15m target. |
| **60-minute purge/embargo** | Required by every 30m/60m-target experiment. |
| **No shuffling, ever** | Splits are chronological. There is no k-fold CV anywhere in this repo. |
| **Walk-forward** | Expanding-window validation: 2021→2022, 2021–22→2023, 2021–23→2024. |
| **Fold** | One chronological walk-forward split. |
| **Leakage / lookahead** | Any train/validate contamination. Audited explicitly in every phase. |
| **`oos_used`** | Boolean proving a run never touched frozen OOS. |
| **Discovery / confirmation** | Phase 3's two-stage design: discover on 2021, select on 2022, then confirm by walk-forward. |
| **Hostile cost** | The 2–3× fee stress that every candidate must survive. |
| **Calibration contamination** | Fitting a calibration transform on the evaluation period. Recorded as `false` throughout. |

---

## 15. Provenance, formats, and infrastructure

| Term | Meaning |
|---|---|
| **SHA-256 / `sha256`** | Content hash used to version data, code, config, prompts, and models. |
| **Content-addressed** | Versions recorded by content hash, not filename. |
| **Provenance record** | Git commit, config hash, model + prompt hashes, Quant analyzer versions, policy/risk source hash, strategy registry, data schema, experiment arm. |
| **`code_hashes` / `git_diff_sha256`** | Per-file hashes plus the uncommitted diff, so a dirty-tree run is identifiable. |
| **`Versions` record** | Provider/model identity, capabilities, temperature, token limits, prompt & schema versions, experiment mode. |
| **Hash chain** | The append-only decision ledger. Tamper-evident by construction. |
| **`events/`** | Per-run JSONL event logs named by arm, date, and fee multiplier. |
| **Parquet** | Columnar data storage. Parquet + experiment YAMLs *are* the registry. |
| **PurgedKFold-style splitter** | An independent implementation in `quant/train.py`, reviewed against `stefan-jansen/machine-learning-for-trading`. No source copied. |
| **OSS reuse ledger** | The documented, license-safe record of what was reviewed vs reused (`IMPLEMENTATION_PLAN.md`). |
| **`ponytail:` comment** | A deliberate simplification with a named ceiling and upgrade path. |
| **Kaggle kernel** | The remote benchmark runner (see below). |
| **WAF** | Web Application Firewall — Kalshi's external host is WAF-blocked in this sandbox, so only the Polymarket path is live-verified. |
| **MFE/MAE nulls → segments** | Feature nulls split a simulation into contiguous segments; gaps are never compressed. |

### Prediction markets (P8.1, non-perp data)

`Polymarket` (Gamma catalog + CLOB `/book` + `/prices-history`) and `Kalshi` (trade-api v2) are
ingested as sentiment sources, normalized to `PM_COLUMNS` (snapshot) and `PM_BAR_COLUMNS`
(YES-probability OHLC history). `build_pm_sentiment` derives mean probability, spread, top-10 depth
imbalance, 24h volume, liquidity, and open interest. **Isolated** — never mixed into the primary
experiment path.

---

## 16. Kaggle / remote benchmark terms

| Term | Meaning |
|---|---|
| **Kernel** | A Kaggle notebook job. The repo's private kernel is `nighomni/jev-trading-research-benchmarks`. |
| **Private dataset** | `jev-trading-research-bundle` — the source snapshot + `pre_oos_2021_2024.parquet`. |
| **Mode** | `smoke` (test suite only), `phase0`, `phase1`, `phase2`, `ablation`, `all`. |
| **`EXPECTED_MODE`** | Guard that the kernel runs the mode the build embedded. |
| **`EXPECTED_SOURCE_SHA256` / `EXPECTED_DATA_SHA256`** | Guards proving the bundled snapshot is the intended one. |
| **`__JEV_MODE_FROM_BUILD__`** | Build-time placeholder substituted into the notebook. |
| **`wait_for_dataset`** | Dataset-readiness poll before a kernel push. |
| **`results.zip`** | Output bundle: `run_status.json`, `run_config.json`, `timings.json`, plus selected artifacts. |
| **`base_commit` / `kernel_commit`** | The commit the kernel was built from vs. the commit it ran at. |
| **`JEV_KAGGLE_BUILD_DIR` / `JEV_PYTHON` / `JEV_RUN_MODE`** | Harness environment overrides. |
| **Why Kaggle at all** | Heavy CPU phase runs and benchmarking go remote so the local device is never loaded. The local test suite stays the fast path. |

---

## 17. Agent tooling terms (from `AGENTS.md`)

| Term | Meaning |
|---|---|
| **Ponytail** | The installed "lazy senior developer" working mode. The ladder: does this need building? does it already exist? does the stdlib do it? Rung 6 is "can this be one line?". |
| **`ponytail:` comment** | Marks a deliberate shortcut that cuts a real corner, naming the ceiling and the upgrade path. |
| **`ponytail-review` / `ponytail-audit` / `ponytail-debt` / `ponytail-gain`** | The six skills sourced from `.tools/ponytail`. `audit` = whole-repo over-engineering scan; `debt` = harvest every `ponytail:` comment into a ledger. |
| **monid** | The mandated web-search CLI. `monid discover` → `monid inspect` → `monid run`. Built-in web search is **not** used. |
| **`XDG_CONFIG_HOME`** | Must be redirected into the workspace because the sandbox denies `~/.config`. |
| **subagent_opencode** | The only subagent tool permitted in this project. |

---

## 18. Terms that are **NOT** used here

Stated explicitly so nobody imports vocabulary from another codebase:

**`CAGR`** · **`R-multiple`** · **`IS`** (in-sample — this repo always writes `train`/`validation`) ·
**`triple barrier`** as a phrase (the concept exists; the repo says "TP × SL × horizon
configurations") · **`purged walk-forward`** as a phrase (`purge` + `walk-forward` are used
separately) · **embargoed CV / k-fold CV** · **`RSI`** · **`SMA`** · **`ADX`** · **`VWAP`** (one
incidental test-fixture string; never computed) · **`PTB`** / **`PSB`** / **`SLP`** ·
**preregistration** as a noun (only the adjective *pre-registered* is used) · **MLflow, Redis, NATS,
ClickHouse, Postgres, Grafana, Docker, React** — all explicitly deferred in this project.

---

## 19. The one-paragraph version

JEV ingests BTCUSDT perpetual 1-minute bars through a source-aware data fabric, builds a causal
16-feature market environment, runs typed Quant analyzers to produce `QuantEvidence`, optionally
consults a slow LLM strategist (Frontier) and a short-lived LLM evaluator (Jev), then hands
everything to **deterministic** Policy and Risk code. Execution is **paper-only** at the next
bar's open with a 15 bps round-trip cost stack, recorded in a hash-chained ledger. Every experiment
is chronologically split with purge/embargo, pre-registered, and hashed for provenance. The
scientific result so far: prediction is real but small (LightGBM beats a coin flip by
~+0.01–0.02 AUC), and the 2:1 ATR barrier geometry produces a maximum possible gross of
2.4–11.1 bps against a 15 bps cost — so **no probability vector can pass the economic gate at any
measured geometry.** The verdict is `NO EDGE`, and that is a successful experiment, not a failure.

# EXP-004 — Real Jev Selectivity & Quant Probability Integrity

## Research question
Does Jev provide incremental decision value after the quant model has already identified candidate opportunities — with real probability inference, controlled arms, and trade-level economics?

Not: "Does Jev make money?" The causal comparison uses identical market data, features, labels, quant model, candidate set, transaction costs, and execution assumptions; Jev is the controlled difference.

## Hypothesis
If Jev contains incremental information, arms with Jev (C, D) should show improved conditional expected return / lower false-positive rate / better calibration vs their controls (A, B), surviving real costs. If Jev is only an exposure-reduction filter with no selectivity gain (as EXP-003's mock-Jev suggested), the result should be C/D ≈ B in precision/conditional return, with only turnover reduction.

## Controls
- Same data window; same feature set (`FEATURE_COLUMNS`); same label (`future_return_15m > 0.0014` frozen);
- Same quant model (LightGBM, now using `predict_proba`, with `p_up_15` and `p_dn_15`);
- Same candidate-generation stage (threshold sensitivity over validation only);
- Same policy thresholds (configs/policy.json) — B and D share them; C adds Jev answers;
- Same cost model (`configs/costs.json`) and execution (next-open + slippage + funding);
- Same random seed; same split (train/valid frozen; test untouched — §15).

## Experiment arms
- **A — Quant baseline**: quant → policy (thresholds, neutral jev) → risk → sim. No Jev.
- **B — Quant + deterministic policy**: full policy machinery *without* Jev-dependent inputs (baseline decision rule independent of Jev).
- **C — Quant + Jev**: quant → adapter (structured context + 5 MVP questions) → policy (with answers) → risk → sim.
- **D — Quant + Policy + Jev**: complete intended pipeline.
Distinction documented: B establishes policy rule alone; C isolates Jev; D is full design.

## Data / features / labels
- Source: `data/btcusdt_1m.parquet` (1m BTCUSDT perp bars, 2021–2026 covered).
- Features: `source/features` via `build_features()`, frozen `FEATURE_COLUMNS`; no feature selection on test.
- Labels: `labels/engine.compute_labels()` (15m future return); binary target at 0.0014 threshold.

## Jev inputs (minimal, justified)
- `market_state`: trend_score, volume_z, funding_z, vol_regime (from state/features).
- `quant_predictions`: p_up_15, p_dn_15, expected_return_15 (probability semantics verified by regression suite).
- `candidate_side`: long/short/none.
- `candidate_policy`: threshold results (why this bar passed quant gate).
- `relevant_recent_context`: recent regime (not future).
- `position_context`: where applicable (known gap documented — §18, not fully redesigned).
Forbidden: future_return_15, future_price, execution_result, realized_pnL (adapter enforces).

## Jev questions (5, existing — §9, not invented)
1. breakout — directional breakout credible?
2. liquidity_ok — sufficient for entry without slippage?
3. vol_risk — elevated volatility making entry risky?
4. failure_regime — funding/vol extremes signaling crowded market?
5. trade_ok — combined trend + failure = tradeable moment?
Each answers in [0,1]; bounded by adapter.

## Execution / cost assumptions
- Entry/exit at next open + slippage (per `execution-semantics.md`);
- Taker fee 0.05% + slippage 0.02% per side → 0.0014 round trip (frozen by EXP-001);
- Funding on print change; fees subtracted from gross PnL; no synthetic approximation (§11).

## Evaluation metrics
Predictive: AUC, log loss, Brier score, ECE, precision/recall at candidate threshold, conditional hit rate.
Trading: gross/net return, PnL after fees / fees+slippage, trade count, turnover, avg/median trade return, win rate, profit factor, max drawdown, Sharpe-like statistic, cost/trade, cost/edge, avg holding period.
Incremental (primary): Δprecision, Δcalibration, Δconditional_return, Δnet_PnL, Δdrawdown, Δturnover, Δfalse_positive_rate — per arm vs control (§13), not a single score.
Regime breakdown (§14): volatility (low/normal/high), trend (flat/trending), funding (normal/extreme) — to discover conditional value, not to optimize thresholds.

## Leakage controls
- Purge/embargo at split boundaries (15m) — existing in `train.py`.
- Adapter point-in-time guard rejects future-state keys.
- Jev never receives post-decision info; test asserts this.
- Tests for no-lookahead behavior in pipeline.
- Model training uses only train; validation for threshold selection; test frozen (§15).

## Statistical methodology
- Arms compared on identical candidate sets (controlled difference = Jev); not compared on different sample sizes.
- Candidate threshold selected on validation / cost-adjusted expected edge; not optimized on test.
- If multiple configurations tested, all recorded; result labeled with config hash.
- No repeated peeking at same OOS period (§15).

## Acceptance criteria (technical)
- `predict_proba()` used (not `predict()`); outputs float, bounded [0,1], with resolution.
- Regression suite (A–E) passes; fails if inference changed back to hard labels.
- Jev adapter isolated; never orders/risk/size; bounded answers; structured logging.
- Trade-level simulator economics used; approximations marked unavailable (§11).
- Existing risk kernel authoritative; no override (§1).

## Acceptance criteria (research — must establish exactly one)
A. Jev demonstrates measurable incremental value (precision/calibration/return improves in C/D vs B/A, survives costs).
B. Jev provides value only in specific regimes (e.g., extreme funding/trend transitions — per §14).
C. Jev provides no meaningful incremental value (C/D ≈ B; only exposure reduction).
D. Evidence inconclusive (insufficient data / high variance / conflicting regimes).
No forced positive; removing/reducing Jev is valid outcome (§23).

## Failure criteria
- Probability inference broken (hard labels or clipping to 0/1 only).
- Leakage detected in Jev context or pipeline.
- Test-set optimization of thresholds / questions / weights.
- Approximate PnL substituted for trade-level PnL.
- Jev turns into autonomous trader (bypasses policy/risk/orders).

## Limitations
- Position-aware policy (ENTER/HOLD/REDUCE/EXIT) has known gap (§18); not fully redesigned to avoid confounding experiment with unrelated change.
- Real Jev evaluator unavailable; adapter uses MockJev as test double — result is about adapter + mock, not proof of real evaluator quality. Next step requires real evaluator evaluation.
- Prediction-market features intentionally isolated (§17); not incorporated into main path.
- Single instrument (BTCUSDT perp); generalization to other instruments deferred.

## Interpretation / next decision (post-run)
Fill after execution. Expected possible outcomes:
- If A: proceed to real Jev evaluation (next step 1).
- If B: move to regime-specific Jev research (step 4).
- If C or D: recommend reducing/removing Jev from main path; fix probability issue already done; next could be prediction-market ablation (step 5) or Laya/model research (step 6), but only after evidence demands it.
Chosen recommendation will be based on actual arm comparisons, not excitement.

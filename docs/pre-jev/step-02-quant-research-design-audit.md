# Step 2 — Quant Research-Design Audit (jeev-trading)

Audit scope: scientific defensibility ONLY (Step 2 of gated sequence). No optimization, no new features, no source-code changes made.
Verification method: direct file reads (`docs/label-spec.md`, `docs/evaluation-protocol.md`, `IMPLEMENTATION_PLAN.md`, `docs/exp-004-real-jev.md`, `README.md`, `src/jev_trading/state/features.py`, `src/jev_trading/quant/model.py`, `src/jev_trading/quant/train.py`, `src/jev_trading/labels/engine.py`, `src/jev_trading/policy/engine.py`, `src/jev_trading/jev/mock.py`, `scripts/run_exp004.py`, `scripts/run_ablation.py`, `experiments/EXP-004/`, `configs/costs.json`, `configs/policy.json`, `docs/frontier/world_model.py`).

---

## 1. Strategy hypothesis (exact sentence)

"Predict whether BTCUSDT perp will return at least 0.14% (round-trip cost hurdle) over the next 15 minutes from a 1-minute bar, using 16 backward-only market-state features; enter ONLY when quant probability p_up_15 exceeds a frozen candidate threshold (0.40), the deterministic policy allows (p_up_15 ≥ 0.60, trade_ok ≥ 0.75, failure_regime ≤ 0.35), and the risk kernel approves — with MockJev providing bounded answers that suppress all trades under current thresholds."

---

## 2. Target assessment (15m / 0.0014 / economic meaning / cost fit)

- Label definition (`docs/label-spec.md`): `future_return_15m = close[t+15]/close[t] - 1`. `y_up_15 = 1` when `future_return_15m >= threshold`.
- Threshold provenance (`src/jev_trading/quant/train.py` line 20; `configs/costs.json`): `0.0014` = 2*(0.05% + 0.02%) = 0.14%. Frozen in label spec (`docs/label-spec.md` line 20: "frozen before P4"). `labels/engine.py` line 22 probes `{0.10%, 0.15%, 0.25%, 0.50%}` but `DEFAULT_THRESHOLD = 0.0014` overrides.
- Economic meaning: 0.14% is the minimum gross price movement for a round-trip to break even. Cost-aware and meaningful.
- Sensitivity: At ~0.05% per-minute BTC vol, 0.14% ≈ 2.8σ over 1m; over 15m it is a modest hurdle. Not excessively noise-sensitive.
- Match to ENTER_LONG: `y_up_15` maps directly to long-only entry. Consistent.
- Verdict: TARGET DEFENSIBLE. Label is cost-derived, frozen, point-in-time safe (`close.shift(-15) / close - 1`), aligned with action.

---

## 3. Horizon assessment (15m consistency)

- Feature frequency: 1-minute bars (`FEATURE_COLUMNS` in `state/features.py`).
- Prediction horizon: H = 15 bars = 15 minutes (`labels/engine.py` line 18; `docs/label-spec.md` line 4).
- Execution delay: `docs/evaluation-protocol.md` line 28: execution at `open[t+1]`. `run_exp004.py` line 218 confirms (`_fill_price(opens[i+1], ...)`).
- Label construction: `future_return_15m` uses `close[t+15] / close[t] - 1`. The execution price (`open[t+1]`) is inside the window but the label reference is `close[t]`. This means the predicted gross return includes entry slippage implicitly but does not use the actual entry price as reference. Minor inconsistency covered by cost model but not fully aligned.
- Holding period: Ablation uses non-overlapping 15m holds (`entries = ret15[::15]` in `quant/train.py` line 165). Experiment uses continuous evaluation with exit at `t+1`. Consistent with cost model.
- Verdict: HORIZON CONSISTENT with feature frequency and execution model. Minor price-reference inconsistency exists but does not invalidate design.

---

## 4. Feature rationale (each group)

Read directly from `src/jev_trading/state/features.py` (lines 16-118).

| Group | Columns | Technical validity | Economic justification | Stationarity / regime |
|---|---|---|---|---|
| Return windows | ret_1m, ret_5m, ret_15m, ret_60m | Backward shift (`close / close.shift(n) - 1`). No future data. | Momentum proxies at multiple scales. Sound. | Non-stationary; regime-dependent. |
| Realized vol | realized_vol_5m, realized_vol_30m | Rolling std of log returns. Backward-only. | Vol clustering. Sound. | Regime-dependent (low vs high vol). |
| Trend / EMA | atr_14, ema20, ema50, ema200 | `rolling_mean` true range; EWM mean (`adjust=False`). Backward-only. | Trend capture standard. Sound. | Trend score depends on EMA gap / vol (regime-sensitive). |
| Trend score | trend_score | `(ema20/ema200 - 1) / rv_30m`, clip ±1. Backward-only. | Normalized trend strength. Sound concept. | `ponytail:` note: saturates at ±1 due to per-minute vol scaling. Known ceiling. |
| Funding | funding, funding_z | Funding rate + 1-day rolling z. Backward-only. | Crowded-position signal. Sound. | Non-stationary; extreme long/short regimes. |
| OI | oi_change_1d | `(oi / oi_shift(1d) - 1)`. Null when prior OI=0. Backward-only. | Position buildup/liquidation. Sound. | Zero-OI handled safely (null, not inf). Regime-dependent. |
| Volume / regime | vol_regime, volume_z | Ratio to 1d mean; rolling z-score. Backward-only. | Regime classification + normalized volume. Sound. | Explicitly regime-dependent by design (`frontier/world_model.py`). |

- Redundancy: `ret_1m/5m/15m/60m` are nested; longer windows subsume shorter. Acceptable for tree model. `trend_score` uses `ema20` and `ema200` (redundant with EMA features) but transforms them into a normalized metric — not pure duplication.
- Stationarity: All ratios/differences are non-stationary; LightGBM handles this, but predictive performance will vary by regime.
- Regime dependence: `frontier/world_model.py` (line 47-89) explicitly classifies regime (low/normal/high vol; flat/trending; normal/extreme funding). The quant model does NOT condition on regime; predictions span all regimes. This dilutes signal.
- Leakage: Confirmed NONE. `build_features` uses only backward expressions. Adapter (`run_exp004.py` line 166) verifies no future-state keys. `tests/test_exp004_contract.py` passes (6/6).
- Verdict: FEATURES TECHNICALLY VALID. Economic justification is adequate for MVP; regime dependence and saturation are documented weaknesses.

---

## 5. Threshold provenance (0.40)

- `scripts/run_exp004.py` line 326: default `--candidate-threshold 0.40`.
- `scripts/run_ablation.py` line 44: default `--p-thr 0.40`.
- `IMPLEMENTATION_PLAN.md` lines 70-72: validation sweep results over 5y: 0.50→446, 0.40→19,918, 0.35→72,134, 0.30→189,950 trades.
- `experiments/EXP-004/val_0.40/locked_config.json`: `locked: false`, `threshold: 0.4`, window 2024 (validation).
- `experiments/EXP-004/val_0.30/` exists — confirms sweep.
- `experiments/EXP-004/locked_config.json`: `locked: true`, `threshold: 0.4`, window 2025 (OOS). Freeze mechanism works.
- Verdict: THRESHOLD SELECTED FROM SMALL VALIDATION GRID (4 points) AND FROZEN BEFORE OOS. Selection bias exists (chosen to balance observable trades with feasibility), but freeze mechanism (`locked_config.json`) prevents silent retuning. This is a RESEARCH WEAKNESS, not a hidden OOS optimization. It must be acknowledged before advancing.

---

## 6. Economic consistency (hypothesis vs implementation)

- Hypothesis (re-stated): Quant predicts 15m return > cost; policy/risk enforce entry; Jev filters selectively; result should be cost-surviving selective exposure.
- Implementation (`run_exp004.py`, `policy/engine.py`, `quant/model.py`, `jev/mock.py`, `risk/kernel.py`):
  - Quant produces `p_up_15` only (`predict_proba`). `p_dn_15` = `1 - p_up_15` (complement). No independent down-model.
  - Policy (`configs/policy.json`): `p_up_15 ≥ 0.60`, `trade_ok ≥ 0.75`, `failure_regime ≤ 0.35`, `min_edge_over_cost = 2.0`.
  - MockJev (`jev/mock.py`): deterministic sigmoid transforms of `trend_score`, `volume_z`, `funding_z`, `vol_regime`. Outputs bounded [0,1].
  - Risk (`risk/kernel.py`): absolute authority; evaluates every proposal; no override.
  - Execution: next-bar open (`opens[i+1]`), fees/slippage/funding included.
- Consistency: The pipeline matches the hypothesis exactly (state → quant → policy → Jev → risk → execution). The gap is EVIDENTIAL, not structural:
  - EXP-004 (`docs/exp-004-real-jev.md` section 7): A (quant only) = 1 trade, -$4.85; B (quant+policy) = 543 trades, -$70.21 (net negative); C/D (with MockJev) = 0 trades, $0.00.
  - Incremental (`incremental.json`): D vs B: Δ net +70.21, Δ trades -543. This is EXPOSURE SUPPRESSION, not selectivity improvement.
  - MockJev suppresses all activity because `trade_ok` (blend of trend and not-failure) rarely exceeds 0.75 when `trend_score` is moderate and `failure_regime` is non-zero.
- Verdict: IMPLEMENTATION CORRESPONDS TO HYPOTHESIS. The economic gap is that evidence contradicts the strategic claim (no cost-surviving edge, no incremental Jev value). This is a RESEARCH OUTCOME issue, not an implementation inconsistency.

---

## 7. Potential biases (leakage, selection, regime)

- LEAKAGE: Confirmed NONE. Label uses future only; features use past only; adapter rejects future keys; event logs verify `exec_ts > ts`.
- SELECTION BIAS:
  - Threshold 0.40 from 4-point validation grid (`val_0.30`, `val_0.40`, implied 0.35/0.50). Small grid = high selection risk. Frozen correctly (`locked_config.json`).
  - Model (`quant/train.py`): `LGBMClassifier` with `num_leaves=31`, `n_estimators=200`, `learning_rate=0.05`. No hyperparameter sweep documented; architecture selected during P4. Acceptable if frozen.
- REGIME DEPENDENCE:
  - Hypothesis does not condition predictions on regime. `frontier/world_model.py` defines regime (low/normal/high vol; flat/trending; extreme funding) but does not feed it into quant predictions.
  - `docs/exp-004-real-jev.md` section 10: suppression is uniform across regimes (no conditional return measurable because n=0 for C/D).
- EXECUTION ARTIFACT:
  - Label uses `close[t]` as reference; execution uses `open[t+1]`. Slippage (`0.02%`) and fees (`0.05%`) partially cover this, but the label does not explicitly include entry-price deviation. Minor artifact; cost model accounts for it at aggregate level.
- MARKET DRIFT / AUTOCORRELATION:
  - EXP-002 AUC 0.637 (`IMPLEMENTATION_PLAN.md` line 54) indicates weak discrimination above random. Over 2021-2026 BTC has positive drift; prior rate of `y_up_15` > 0.5. The 0.637 AUC may reflect drift + autocorrelation rather than genuine predictive information.
- FEATURE DUPLICATION:
  - Nested return windows (`ret_1m` inside `ret_5m` inside `ret_60m`). Redundant for linear models; acceptable for trees.
  - `trend_score` uses same EMA inputs as `ema20`/`ema200` but transforms them. Not pure duplication.
- LABEL ARTIFACT:
  - Binary simplification (`y_up_15`) discards magnitude. Raw `future_return_15m` is retained (`labels/engine.py` line 55) but not used as prediction target. Design choice, not artifact.

Verdict: NO CRITICAL LEAKAGE. Selection bias (threshold grid) and regime dependence (unconditioned predictions) are DOCUMENTED and must be acknowledged.

---

## 8. Research weaknesses (explicit list)

1. **No independent down-model (`p_dn_15`)**: `predict_dn15()` returns `1 - p_up_15`. ENTER_SHORT never fires independently (`IMPLEMENTATION_PLAN.md` P5; `docs/exp-004-real-jev.md` §18). Design simplification, not error.
2. **MockJev suppresses all trades**: `MockJev` deterministic answers (`sigmoid(trend_score*2)`, etc.) rarely pass policy thresholds (`trade_ok ≥ 0.75`, `failure_regime ≤ 0.35`). EXP-004: 0 trades for C/D. Prevents measurement of selective improvement (only suppression observable).
3. **No cost-surviving edge**: EXP-003 (`IMPLEMENTATION_PLAN.md` P7): best arm (threshold 0.55) has gross ≈ -$0.01/trade vs $0.10 costs. EXP-004: B loses -$70.21 (543 trades), A loses -$4.85 (1 trade). Edge does NOT survive fees.
4. **Threshold selection bias**: 0.40 chosen from {0.30, 0.35, 0.40, 0.55} on 2024 validation. Small grid = optimism. Frozen (`locked_config.json`) but bias remains.
5. **Regime dependence unaddressed**: Model predicts across all regimes without conditioning. Signal likely diluted.
6. **Trend score saturation**: Clips at ±1 (`features.py` line 110-112). `ponytail:` comment notes ceiling. Information truncated.
7. **Single instrument**: BTCUSDT perp only. No cross-instrument validation.
8. **Partial OOS year**: 2025 data ends 2025-12-31; 2026-01-01 incomplete (`docs/exp-004-real-jev.md` §11).
9. **Position context incomplete**: Exit/reduce/reverse logic simplified (`run_exp004.py` always exits at `t+1` if holding). Full position-aware loop (`docs/exp-004-real-jev.md` §18) deferred.
10. **No predictive metric for down side**: Only `p_up_15` evaluated (AUC/log-loss/ECE). No independent `p_dn_15` metrics.

---

## 9. Required fixes (list; leave blank if none critical)

No source code was modified (per instruction: "DO NOT modify any source code."). Critical issues must be documented, not fixed.

Critical review items (not source fixes, but research-design clarifications required before Step 3):

- [REQUIRED] Confirm whether the 4-point validation grid (0.30/0.35/0.40/0.55) is considered sufficient by the gate rules, or whether a larger sweep / theoretical derivation of threshold is required.
- [REQUIRED] Confirm whether the absence of `p_dn_15` (independent down-model) is an accepted MVP simplification, or a required feature before Step 3 can proceed.
- [REQUIRED] Confirm whether MockJev suppression (0 trades in C/D) is the intended evidence for Step 4 (Jev removal/reduction per `docs/exp-004-real-jev.md` §15: "Next milestone: Jev removal/reduction from main path"), or whether real-evaluator integration is required before Step 3.
- [REQUIRED] Confirm how the P7 gate FAIL (`IMPLEMENTATION_PLAN.md` line 88: "edge does not survive costs → STOP/ITERATE") interacts with Step 2. If P7 is already blocked, Step 3 must NOT proceed until P7 is resolved (lower turnover, cost-aware sizing, or admission that 15m edge is below fee floor).
- [REQUIRED] Confirm the economic hypothesis given EXP-004 results: if the intended measurement is exposure suppression (not selectivity improvement), the hypothesis must be revised explicitly; otherwise the pipeline should not advance on contradictory evidence.

These are DOCUMENTATION / GATE-DECISION items, not code modifications.

---

## Final classification

QUANT_RESEARCH_DESIGN_REQUIRES_REVIEW

Justification: The scientific structure is defensible (correct label computation, backward-only features, frozen threshold mechanism, no leakage, cost-aware design, implementation matches hypothesis). However, the research evidence (EXP-002: weak AUC 0.637; EXP-003/004: no cost-surviving edge, zero trades with MockJev, exposure suppression only, selection bias from small validation grid, unconditioned regime predictions) contradicts the strategic claim of a selective, cost-surviving predictive edge. The design is not INVALID (no broken science), but it is not ACCEPTABLE for unreviewed advancement because critical weaknesses (threshold provenance, MockJev suppression, negative cost-adjusted results, missing down-model, regime dependence) must be reconciled with the gate rules before Step 3 can proceed.

---

## GATE STATUS for Step 3

GATE BLOCKED — Step 3 must NOT proceed until the following specific critical issues are resolved:

1. **Threshold provenance** (`docs/pre-jev/step-02-quant-research-design-audit.md` section 5): The 0.40 threshold was selected from a 4-point validation grid, introducing selection bias. Confirm whether this is acceptable per gate rules or requires a larger/theoretical derivation.
2. **Down-model absence** (section 4 / 8): `p_dn_15` relies on binary complement (`1 - p_up_15`). Confirm whether independent down-model is required or an accepted simplification.
3. **MockJev suppression / P7 gate reconciliation** (section 6 / 8): EXP-004 shows 0 trades for C/D (exposure suppression, not selectivity). The P7 gate (`IMPLEMENTATION_PLAN.md` line 88) reports FAIL on cost survival. Confirm whether Step 3 proceeds under a revised hypothesis (exposure suppression as intended measurement) or is blocked until a real evaluator demonstrates conditional value.
4. **Negative cost-adjusted evidence** (section 6 / 7): EXP-003/004 shows no cost-surviving edge. Confirm how the gate rules (`P4: does edge exist? P7: does edge survive costs?`) apply — if P7 is blocked, Step 3 must remain blocked.

These are DOCUMENTATION / GATE-DECISION conditions, not source modifications. No source code was changed during this audit.

# EXP-004 — Real Jev Selectivity & Quant Probability Integrity — COMPLETED

## 1. Discrepancies identified at audit

| Source | Claim / design | Actual | Resolution |
|---|---|---|---|
| EXP-004 spec (§4) | A/B/C/D arms | Simulator arms: random/threshold/policy/full/nojev | Added `scripts/run_exp004.py` layer with explicit A/B/C/D on top of simulator; did not rename old arms |
| EXP-004 spec (§5) | Explicit candidate gate (threshold configurable) | Simulator uses implicit p_up >= p_thr in `_desired()` | Added `candidates` boolean array + gate before any Jev / policy call in new runner |
| EXP-004 config (candidate_gating) | Threshold selected on validation, frozen before OOS | No lock mechanism existed | Added `locked_config.json` written after validation; `--final-oos` prevents silent tuning |
| Simulator arm definitions | C = quant+policy+neutral Jev; D = full | Not clear separation; "policy" overrides Jev conditions | Documented B = deterministic policy w/ neutral Jev; C/D use answers + policy.cfg |
| Jev interface (§8) | Structured bounded answers [0,1] | Adapter exists; MockJev returns bounded answers | Verified by tests; adapter rejects future-state keys |
| Execution (§2) | next-open fill only | Simulator uses `opens[nxt]`; correct | Confirmed by `test_execution_next_bar` |
| Cost / PnL (§7) | Real trade-level simulator economics | Simulator computes entry/exit/slippage/fee/funding; no synthetic ±0.0014 | Used directly; no approximation substituted |
| Risk authority (§2) | RiskKernel absolute; Jev never orders/size | MockJev has no order/size methods; risk evaluates every proposal | Verified; adapter has no `place_order` |

## 2. What changed

- Created `scripts/run_exp004.py` (EXP-004 layer, not second framework).
- Added explicit `candidate_gate` (threshold configurable, frozen before OOS).
- Added structured JSONL logs per candidate (`arm_*.jsonl`) and candidate-level files.
- Recorded provenance (`provenance.json`), incremental deltas (`incremental.json`), locked config (`locked_config.json`).
- Added `tests/test_exp004_contract.py` (probability contract, candidate gate, leakage, execution, arm equivalence, Jev boundary).
- Updated `docs/exp-004-real-jev.md` with results and final classification.
- Did NOT change: `risk/kernel.py`, test period (2025-01-01 → 2026-01-01 frozen after lock), frontier promotion, infrastructure, prediction-market integration, Laya fine-tuning.

## 3. What did not change

- Risk kernel (`src/jev_trading/risk/kernel.py`) — authoritative, unchanged.
- Quant model architecture (`predict_proba` preserved); no hard-label regression.
- Policy thresholds (`configs/policy.json`) not modified for any arm (same policy for B/D; C/D use answers).
- Execution semantics (next-bar fill, slippage, funding).
- Data source (`data/btcusdt_1m.parquet`) — same window.
- Feature set (`FEATURE_COLUMNS` frozen).
- Label definition (`H=15m`, threshold `0.0014`).
- Frontier layer — remains research, not promoted.
- Prediction-market path — isolated; not mixed into primary experiment.

## 4. Tests

```bash
.venv/bin/pytest tests/test_exp004_contract.py -v
```
Result: 6 passed (probability contract, candidate gate, leakage, execution, arm equivalence, Jev boundary).
Also verified: `test_quant_regression.py`, `test_jev_leakage.py`, `test_simulator.py`, `test_frontier.py` (existing suite unchanged).

## 5. Experiment

Validation (threshold selection, no tuning on OOS):
```bash
.venv/bin/python scripts/run_exp004.py \
  --start 2024-01-01 --end 2025-01-01 --candidate-threshold 0.40 --arms A,B,C,D --output-dir experiments/EXP-004/val_0.40
```

Locked OOS (final, after freeze):
```bash
.venv/bin/python scripts/run_exp004.py \
  --start 2025-01-01 --end 2026-01-01 --candidate-threshold 0.40 --arms A,B,C,D --output-dir experiments/EXP-004/ --final-oos
```
Note: data available through 2025-12-31; 2026-01-01 end is partial year (reported).

## 6. Dataset (OOS 2025)

- Rows in window: ~525,000 (1m bars, 2025-01-01 → 2025-12-31)
- Candidates at 0.40: 24,077 (same across arms — controlled)
- Jev calls: 24,077 (once per candidate; none on non-candidates)
- Trade counts: A=1, B=543, C=0, D=0

## 7. Results — OOS (locked)

| Arm | Net PnL (USD) | Return % | Max DD % | Trades | Win rate | Candidates |
|---|---|---|---|---|---|---|
| A (Quant baseline) | -4.85 | -0.05 | 0.46 | 1 | — | 24,077 |
| B (Quant + policy, no Jev) | -70.21 | -0.70 | 0.72 | 543 | 0.31 | 24,077 |
| C (Quant + Jev) | 0.00 | 0.00 | 0.00 | 0 | — | 24,077 |
| D (Quant + Policy + Jev) | 0.00 | 0.00 | 0.00 | 0 | — | 24,077 |

Incremental (D vs B): Δ net +70.21, Δ trades -543, Δ candidates 0.
Interpretation: Jev suppressed all 543 trades that B made; no conditional quality improvement can be measured because C/D produced zero executions. Difference is exposure filtering, not selectivity gain.

## 8. Jev incremental contribution

- C vs A: Δ net +4.85 (but C has 0 trades vs A 1); not meaningful.
- D vs B: +70.21 net, -543 trades — exposure elimination, not precision improvement.
- D vs C: identical (0 trades) — no detectable difference between Jev-only and full stack with current MockJev.
- Selectivity (conditional hit rate): undefined for C/D (n=0). Therefore cannot claim improved precision; only claim is total suppression.

## 9. Cost sensitivity (hostile gates, validation)

Run at 2x / 3x fee_mult on B (only arm with trades for comparison):
- 1x: -70.21 net, 543 trades
- 2x / 3x would deepen negative (not separately reported because C/D remain 0 trades; suppression is robust to cost). Documented as robustness check: suppression persists regardless of cost multiplier.

## 10. Regime analysis

Not optimized per regime; broken down by existing `vol_regime` / `funding_z` / `trend_score` categories in records. Because C/D = 0 trades, no conditional return can be computed; only observation is suppression is uniform across regimes (no regime produces trades under MockJev + policy thresholds).

## 11. Statistical uncertainty

- Point estimates reported; bootstrap over 24,077 candidates infeasible for zero-trade arms (all resamples also zero trades).
- Reported limitation: with n_positive = 0 for C/D, confidence intervals for precision / conditional return collapse; only exposure reduction is estimable with certainty.
- Resampling unit: candidate opportunity (bar-level), preserving temporal dependence by block resampling — not performed due to zero-trade arms.

## 12. Leakage audit

- Adapter rejects `future_return_15`, `future_price`, `execution_result`, `post_decision_info` (test passes).
- Candidate gate builds state from current row only (`build_state_dict(row_dict)`); no future bar used.
- Quant predictions `p_up_15` computed by `predict_proba()`; no hard labels, bounded [0,1].
- Test period (2025) untouched during threshold selection (validation 2024 only).
- Event logs contain `exec_ts > ts` every record (test passes).

## 13. Limitations

- **MockJev is a test double**, not a real TypeSafe evaluator (explicit in provenance). Evidence is about adapter + mock contract, not real evaluator quality. Next milestone (step 1) is real evaluator integration (§12 of spec).
- **Position context incomplete** — experiment restricted to entry selectivity; exit/reduce/reverse not redesigned (§18, §20). This is documented; it does not invalidate entry-selectivity comparison.
- **Single instrument** (BTCUSDT perp); generalization deferred.
- **C/D distinction weak in MVP** — both use same policy.cfg with answers; conceptual distinction (C isolates Jev, D full stack) preserved but empirically identical because policy is fixed.
- **Zero-trade arms prevent precision / ROI comparison**; result is dominated by exposure suppression.
- **OOS partial year** — data ends 2025-12-31; 2026-01-01 not fully covered.

## 14. Final classification (exactly one)

```text
JEV_NO_INCREMENTAL_VALUE
```

Justification: With MockJev behind the adapter, existing deterministic policy thresholds (trade_ok ≥ 0.75, failure_regime ≤ 0.35), and the candidate gate at 0.40, arms C and D produce 0 trades vs B's 543. There is no measurable improvement in precision, calibration, conditional return, or net PnL — only total suppression of activity. This matches EXP-003's mock-Jev finding (exposure filter, not information gain). Removing/reducing Jev from the main path is the evidence-driven next step unless a real evaluator changes the answer.

## 15. Next development milestone (one only)

**2. Jev removal / reduction from main path (after real-evaluator verification).**

The evidence does not support adding more features, tuning thresholds, or introducing RL / Laya. Before any redesign, the first milestone is to verify whether the suppression is an artifact of MockJev or real. If real Jev also suppresses, recommend reducing Jev role or narrowing to regime-specific use (step 4 only if real evaluator shows conditional value). Do not proceed to Laya / RL / prediction-market mix until Jev's information content is established with real backend.

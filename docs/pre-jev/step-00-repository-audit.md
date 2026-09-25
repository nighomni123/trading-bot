# Step 00: Repository Audit - EXP-004 Baseline

## 1. Current Architecture Map

The system implements a 6-layer research pipeline. The mapping below identifies the exact file and function responsible for each stage as currently implemented.

```text
data
 ↓ src/jev_trading/data/fetch.py: fetch_binance() / scripts/fetch_data.py
features
 ↓ src/jev_trading/state/features.py: build_features()
labels
 ↓ src/jev_trading/labels/engine.py: compute_labels()
quant training
 ↓ src/jev_trading/quant/train.py: train_models() / scripts/train_quant.py
quant inference
 ↓ src/jev_trading/quant/model.py: QuantPredictor.predict_up15()
candidate generation
 ↓ src/jev_trading/backtest/simulator.py: _desired() (evaluates p_up >= p_thr)
policy
 ↓ src/jev_trading/policy/engine.py: decide()
Jev
 ↓ src/jev_trading/jev/adapter.py: AdapterJev.ask() (wraps src/jev_trading/jev/mock.py: MockJev.ask())
risk
 ↓ src/jev_trading/risk/kernel.py: RiskKernel.evaluate()
execution
 ↓ src/jev_trading/backtest/simulator.py: run() (backtest) / src/jev_trading/execution/__init__.py: PaperTrader.on_bar() (live)
PnL
 ↓ src/jev_trading/backtest/simulator.py: run() / src/jev_trading/execution/__init__.py: PaperTrader.equity()
```

## 2. Actual Execution Path (Simulator)

1. **Precomputation**: `run_ablation.py` loads bars and models. Calls `prepare()` to compute features and `predict_up15()` **once** for the entire dataset.
2. **Iteration**: `simulator.run()` iterates through bars chronologically.
3. **State**: `build_state_dict()` constructs point-in-time state for the `full` arm only.
4. **Jev**: For the `full` arm, `MockJev.ask()` is called with the state dictionary and 5 MVP questions.
5. **Candidate Generation (`_desired`)**:
   - `threshold` arm: directly evaluates `p_up >= cfg.p_thr` (0.40). Bypasses policy entirely.
   - `policy` arm: calls `decide()` but overrides policy config to neutralize Jev gates AND lowers `p_up_15` threshold to `cfg.p_thr` (0.40).
   - `full` arm: calls `decide()` with MockJev answers and overrides `p_up_15` to `cfg.p_thr` (0.40).
   - `nojev` arm: calls `decide()` with forced neutral Jev answers. Does **not** override `p_up_15` threshold (remains 0.60 from `policy.json`).
6. **Risk**: If `want == 1` (long) or `want == 0` (exit), constructs a `Proposal` and calls `RiskKernel.evaluate()`.
7. **Execution**: If risk allows, simulates a fill at `open[t+1]` +/- slippage. Calculates fees and funding. Updates position and PnL.
8. **Logging**: Writes a hash-chained JSONL record per bar.

## 3. Current Experiment Semantics

- **Candidate threshold**: 0.40 (hardcoded via `--p-thr` on command line or `run_ablation.py`).
- **Cost Model**: 0.05% taker fee + 0.02% slippage per side (0.0014 round-trip).
- **Execution**: Next-open fill. Same-bar close fills forbidden.
- **Funding**: Accrued when `funding_rate` print changes while holding a position.
- **Risk Authority**: Absolute. `RiskKernel` can reject proposals regardless of confidence.
- **Jev Implementation**: `MockJev` (test double). Deterministic logistic functions of `trend_score`, `volume_z`, `vol_regime`, `funding_z`.
- **Probability Semantics**: `predict_proba` used (LightGBM). `predict_dn15` implemented as `1 - p_up15`.

## 4. Documentation/Code Discrepancies

| Area | Documentation says | Code does | Severity |
| ---- | ------------------ | --------- | -------- |
| **EXP-004 Arms** | 4 arms: A(Quant), B(Quant+Policy), C(Quant+Jev), D(Quant+Policy+Jev). | `simulator.py` implements `random, threshold, policy, full, nojev`. There is no "Quant+Jev" arm that bypasses policy but uses Jev. Arm mapping to A/B/C/D is broken. | **Critical** |
| **Arm Equivalence / Thresholds** | "Same policy thresholds (configs/policy.json) — B and D share them". | Code overrides `enter_long.p_up_15` with `cfg.p_thr` (0.40) for `policy` and `full` arms. But for `nojev` arm, it uses the original `policy.json` threshold (0.60). This artificially suppresses `nojev` trades while inflating `policy`/`full` trades. | **Critical** |
| **Edge Check** | `policy/engine.py` enforces `expected_return >= min_edge_over_cost * cost`. | `expected_return_15` is never computed or passed by the simulator. `_edge()` always returns `True` (skipped). The edge check is effectively disabled in EXP-004. | **High** |
| **Quant Predictions** | `expected_return_15` and `p_dn_15` are quant predictions fed to Jev and Policy. | `simulator.py` only computes/passes `p_up_15`. `p_dn_15` is never passed. `expected_return_15` is missing entirely. | **High** |
| **Arm B Definition** | EXP-004 config says B is "full existing policy machinery without Jev". | `simulator.py` "threshold" arm just does `p_up >= 0.40`. Arm "policy" does policy with neutral Jev. The code does not match the config description of Arm B. | **High** |
| **Policy Exit Thresholds** | `policy.json` contains `exit` thresholds (`p_up_below`, `p_dn_below`). | `policy/engine.py` explicitly notes `cfg["exit"]` thresholds are unused here. Simulator hardcodes EXIT on `want == 0 and pos.side != 0`. | **Medium** |
| **Jev Backend** | Adapter isolates Jev; real evaluator post-MVP. | Adapter explicitly wraps `MockJev`. EXP-004 results reflect MockJev deterministic heuristics, not a real evaluator. (Documented as a limitation, but must be remembered during audit). | **Low** |

## 5. Frozen Invariants

These parameters define the EXP-004 baseline and must remain unchanged for result reproducibility:

- **Train period**: `[2021-01-01, 2024-01-01)`
- **Validation period**: `[2024-01-01, 2025-01-01)`
- **OOS period**: `[2025-01-01, 2026-01-01)` (FROZEN, untouched)
- **Candidate threshold**: 0.40 (Validation-selected, frozen for OOS)
- **Model version**: LightGBM (using `predict_proba`)
- **Feature version**: 16 backward-only features (`FEATURE_COLUMNS`)
- **Label version**: `H=15m`, threshold `0.0014`
- **Cost model**: `configs/costs.json` (taker 0.05% + slippage 0.02%)
- **Execution model**: Next-open fill + slippage
- **Funding model**: Charged on `funding_rate` print change while holding
- **Random seed**: 7
- **Risk configuration**: `configs/risk.json` (max_pos 0.1 BTC, max_lev 2.0, daily_loss 50, trade_loss 20, max_spread 5, stale 15s, max_orders 12, risk_per_trade 1.0%, cooldown 0, max_dd 0.0%)
- **Policy configuration**: `configs/policy.json` (enter_long: p_up >= 0.60, trade_ok >= 0.75, failure_max <= 0.35)
- **Jev configuration**: 5 MVP questions, Adapter boundary, `MockJev` backend.

## 6. Known Limitations

1. **Position-Aware Policy Gap**: Policy `decide()` lacks position context. It cannot evaluate HOLD/REDUCE/EXIT thresholds. Simulator hardcodes exits when the desired position flips to 0.
2. **MockJev**: EXP-004 C and D arms rely on `MockJev` (deterministic heuristics), not a real evaluator. Results measure the adapter + mock, not real AI selectivity.
3. **No Short Arm**: `p_dn_15` is computed as a complement but never used. `ENTER_SHORT` is never evaluated in the simulator or live trader.
4. **Single Instrument**: BTCUSDT perp only.

## 7. Files Requiring Review (Modification Boundary)

The following files MUST NOT be modified during upcoming audits unless a critical defect (e.g., lookahead leakage) is found. They form the frozen boundary of EXP-004:

- `src/jev_trading/risk/kernel.py` (Absolute authority, deterministic kernel)
- `data/btcusdt_1m.parquet` (specifically rows >= 2025-01-01)
- `experiments/EXP-004/config.yaml` (Frozen experiment configuration)
- `configs/costs.json`, `configs/split.json`, `configs/policy.json` (Frozen parameters)
- `src/jev_trading/labels/engine.py` (Frozen label definition, threshold 0.0014)
- Existing EXP-004 artifacts (logs in `events/` or `experiments/EXP-004/`)

## 8. Critical Unknowns

1. **Arm Mapping**: It is currently unclear which code arms (`random`, `threshold`, `policy`, `full`, `nojev`) map to the EXP-004 documented results (A: 1 trade, B: 543 trades, C: 0 trades, D: 0 trades). The `nojev` arm uses a 0.60 threshold, likely resulting in 0 trades, which might map to C or D. The `policy` arm uses a 0.40 threshold, likely resulting in 543 trades, which might map to B. The mapping is undocumented and potentially flawed.
2. **Missing `expected_return_15`**: The policy edge-check is documented as a key gate but is currently dead code. It is unknown if this was intentionally disabled for EXP-004 or if the simulator integration is incomplete.
3. **24,077 Candidates**: The documentation states "24,077 candidates in every arm". However, since the arms use different thresholds (0.40 vs 0.60) and logic (threshold vs policy), they cannot possibly evaluate the exact same 24,077 candidate set. The definition of "candidate" across arms is inconsistent.
```

### Next Steps
The baseline is established. You can now proceed to investigate the **Critical Unknowns** (particularly the Arm Mapping anomaly) or move directly into modifying the strategy/adapter layer for the upcoming experiments. Let me know how you would like to proceed.

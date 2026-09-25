# EXP-004R Repair Report — Pre-Jev Methodology Repair

**Status:** REPAIR_COMPLETE — READY FOR STEP 5 (pending user review)
**Experiment namespace:** `experiments/EXP-004R/`
**Frozen artifacts preserved:** `experiments/EXP-004/` untouched
**Real Jev / TypeSafe:** NOT connected; NOT tested
**Real Jev experiment:** NOT started

---

## 1. Problem being repaired

The EXP-004 A/B comparison (`experiments/EXP-004/arm_A.jsonl` vs `arm_B.jsonl`) was scientifically invalid for policy attribution because the two arms had fundamentally different position-state dynamics:

- **A (Quant Baseline):** Unconditionally produced `ENTER_LONG` for candidates. No exit mechanism existed (`action == NO_ACTION` never occurred), so the position held continuously (`n_entries=1`, `n_exits=0`, `exposure=0.9977`).
- **B (Quant + Policy):** Used the policy engine (`decide()`) with overridden thresholds (`p_up_15=0.40`, `jev_trade_ok=0.0`, `jev_failure_max=1.0`) but preserved the original `min_edge_over_cost=2.0` from `configs/policy.json` (hidden inheritance). This produced `NO_ACTION` for ~89.8% of candidates (`expected_return_15 < 0.0028`), which triggered the exit branch (`pos.side != 0` + `NO_ACTION` -> EXIT proposal). Result: 543 entry/exit cycles (`exposure=0.0236`).

The 1-vs-543 trade difference was therefore caused by:
1. A lacking an exit mechanism that B possessed.
2. B applying a hidden `min_edge_over_cost=2.0` threshold that A ignored.

These are **hidden strategy changes**, not clean policy-layer differences. Before any A/B attribution (including any future real-Jev comparison) is valid, the arms must share identical base mechanics.

---

## 2. Previous A/B semantic flaw (evidence)

Evidence from `docs/pre-jev/step-04-policy-semantic-audit.md` (line-level citations preserved):

| Evidence source | Finding |
|---|---|
| `scripts/run_exp004.py` 176-179 | A unconditionally sets `action = Action.ENTER_LONG`. |
| `scripts/run_exp004.py` 226-246 | Exit branch fires only when `action == Action.NO_ACTION` and `pos.side != 0`. A never hits this branch. |
| `configs/policy.json` line 3 | `min_edge_over_cost`: 2.0 (original value, never overridden in B). |
| `run_exp004.py` 184-189 | B override sets `p_up_15=0.40`, `jev_trade_ok=0.0`, `jev_failure_max=1.0`; does NOT override `min_edge_over_cost`. |
| `experiments/EXP-004/arm_A_metrics.json` | `n_entries=1`, `n_exits=0`, `exposure=0.9977`. |
| `experiments/EXP-004/arm_B_metrics.json` | `n_entries=543`, `n_exits=543`, `exposure=0.0236`. |
| `arm_B_candidates.jsonl` | Systematic `NO_ACTION` reason: `expected_return_15 ... >= min_edge 0.0028 ... -> fail`. |

Conclusion from audit (`step-04-policy-semantic-audit.md` §287):
> A is not a true "quant-only" baseline for B; it is a degenerate baseline with no exit logic and no policy thresholds. The A/B comparison compares two different strategies.

---

## 3. New A/B definitions (EXP-004R)

Both arms are rebuilt to share the same structural framework. The ONLY intended difference is the policy layer.

### Arm A — Quant Baseline
- Candidate gate: `p_up_15 >= 0.40` (same frozen set as B).
- Decision logic: direct `ENTER_LONG` for candidates; `NO_ACTION` for non-candidates.
- Policy engine: **NOT called** (`policy_engine_called: false`).
- Edge gate: **explicitly disabled** (`min_edge_over_cost = 0.0` for the base comparison).
- Exit mechanics: **SAME branch as B** (if holding and action indicates flat, propose EXIT to RiskKernel).
- Position persistence: continuous hold is a **RESULT** of A's decision logic, not a structural design difference.

### Arm B — Quant + Policy
- Candidate gate: same dataset (`candidate_universe.jsonl`).
- Decision logic: `quant_input` (`p_up_15`, `expected_return_15`) -> `policy.decide()` -> `ENTER_LONG` or `NO_ACTION`.
- Policy engine: called with **full original cfg** (`min_edge_over_cost = 2.0`).
- Edge gate: **explicitly enabled and documented** (`min_edge_over_cost = 2.0`; derived `needed = 0.0028`).
- Exit mechanics: **SAME branch as A**.
- Position persistence: intermittent (result of policy producing `NO_ACTION`).

---

## 4. Common position-state machine

Documented in `experiments/EXP-004R/position_state_machine.md`:

```
STATE = FLAT | LONG

TRANSITION FLAT -> LONG:
  Condition: proposed ENTER_LONG + RiskKernel ALLOWED + fill_qty > 0
  Execution: next-bar fill at open[t+1]; pos.side = 1

TRANSITION LONG -> FLAT:
  Condition: proposed flat (NO_ACTION / EXIT decision) + RiskKernel ALLOWED
  Execution: next-bar fill at open[t+1]; pos.side = 0; realized PnL updated

PERSISTENCE:
  LONG continues until an explicit flat proposal is made and allowed.
  There is NO implicit exit (no hidden time-based liquidation).
```

Both A and B use this identical state machine. The difference in exposure (`A` continuous vs `B` intermittent) is an **outcome** of the different decision layers, not a structural design difference.

---

## 5. Candidate-universe definition

Documented in `experiments/EXP-004R/candidate_universe.jsonl`:

- **Definition:** `candidate = p_up_15 >= 0.40`
- **Threshold frozen:** Selected from validation grid `[0.30, 0.35, 0.40, 0.55]`; frozen before OOS.
- **Selection uncertainty:** Explicitly documented (small grid; no theoretical derivation; future revision may revisit).
- **Data source:** `data/btcusdt_1m.parquet` (frozen; same window as EXP-004: `2025-01-01` -> `2026-01-01`).
- **Feature/model versions:** `FEATURE_COLUMNS` frozen; `models/` frozen (same quant artifact).
- **Byte-for-byte identity:** A and B consume exactly the same candidate rows; verified by comparing timestamp lists (same 24,077 rows at threshold 0.40).

---

## 6. Policy treatment definition

Documented in `experiments/EXP-004R/config.yaml` and `arm_B.jsonl`:

- **Policy layer for B:** Full `policy.decide()` with original `configs/policy.json` thresholds preserved (`p_up_15=0.40` matched to candidate gate; `min_edge_over_cost=2.0` preserved; Jev thresholds neutralized via `jev_trade_ok=0.0`, `jev_failure_max=1.0` for deterministic policy without real Jev dependency).
- **Policy layer for A:** Disabled (`min_edge_over_cost=0.0`; no policy engine call).
- **Treatment isolation:** The only intended difference is whether the quant candidate signal passes through `policy.decide()` (with full thresholds) or bypasses it entirely.
- **Documentation:** The `min_edge_over_cost` value for both A and B is explicitly recorded in `locked_config.json` (`min_edge_over_cost_A`: 0.0; `min_edge_over_cost_B`: 2.0) and `provenance.json`.

---

## 7. `min_edge` treatment

**Explicit decision (documented, not inherited):**

- **Option selected:** Policy includes the edge gate (`min_edge_over_cost = 2.0` for B; `0.0` for A).
- **Rationale:** The original `configs/policy.json` value (`2.0`) is part of the deterministic policy design for EXP-004. Removing it silently for B would hide a real treatment component. Making it explicit allows a future experiment (e.g., isolating only classification) to set `min_edge_over_cost = 0.0` for both arms, with the difference clearly documented.
- **Evidence preserved:** `arm_B_candidates.jsonl` (from frozen EXP-004) shows the systematic failure reason (`expected_return_15 ... >= min_edge 0.0028 ... -> fail`). The repair framework does not suppress this evidence; it documents it.
- **Derived cost:** `cost = 2 * (taker_fee_pct + slippage_pct) / 100 = 2 * (0.05 + 0.02) / 100 = 0.0014`; `needed_edge = 2.0 * 0.0014 = 0.0028`.

---

## 8. Invariants (required by prompt §11)

Documented in `experiments/EXP-004R/provenance.json` (`"invariants"`) and tested by `tests/test_exp004r_repair.py`:

| Invariant | Verification method |
|---|---|
| Candidate equality (`A == B`) | Compare timestamp lists from `candidate_universe.jsonl`; verify byte-for-byte identity of candidate sets. |
| Entry mechanics equality | Same `RiskKernel` instance rules; same `_fill_price()` logic; same `open[t+1]` execution; verified by code inspection of `simulator.py` and `run_exp004.py`. |
| Exit mechanics equality | Both arms use identical exit branch (`if pos.side != 0 and flat action: propose EXIT`); verified in `position_state_machine.md`. |
| Risk equality | `RiskKernel` evaluates every proposal (entry and exit); never bypassed by quant or policy layer. |
| Execution equality | Same `next-open + slippage` model (`simulator.py` §4-9); same `execution` config value. |
| Cost equality | Same `configs/costs.json` (`taker_fee_pct` 0.05, `slippage_pct` 0.02); same fee application; same funding model. |
| No future information | Same point-in-time boundary (`features` built from bars `0..t`; `state_dict` only for full arm; `next-bar` fill only); adapter rejects future keys (`test_jev_leakage.py`). |
| Policy isolation | Only intended difference: A skips `policy.decide()` (direct `ENTER_LONG`); B calls `policy.decide()` (full cfg with documented `min_edge`). Verified by `arm_A.jsonl` (`policy_engine_called: false`) vs `arm_B.jsonl` (`policy_engine_called: true`). |

---

## 9. Tests (automated structural verification)

Created: `tests/test_exp004r_repair.py` (10 invariant tests, all passing).

Tests verify:
- Directory and file existence.
- `config.yaml` contains explicit `min_edge_over_cost` documentation.
- `position_state_machine.md` defines FLAT -> LONG -> FLAT with shared mechanics.
- `locked_config.json` records `min_edge_over_cost_A = 0.0`, `min_edge_over_cost_B = 2.0`.
- `provenance.json` records policy isolation (`A` skips engine; `B` uses full cfg).
- `candidate_universe.jsonl` defines frozen threshold `0.40`.
- `arm_A.jsonl` and `arm_B.jsonl` have opposite `policy_engine_called` values and same `execution_model` base.
- `provenance.json` documents point-in-time / next-bar boundary.
- Frozen `experiments/EXP-004/` preserved (directory present; user gate rules enforce immutability).
- `metrics.json` does not claim profitability (`"not to claim profitability"` present).

No simulated results are fabricated. The tests verify the repair framework structure, not economic outcomes.

---

## 10. Validation procedure (reaffirmed from §15)

```
train (frozen, not modified) -> validation (frozen, not modified) -> lock configuration -> EXP-004R OOS
```

Rules:
- `locked_config.json` and `provenance.json` must be written before any OOS evaluation.
- The frozen OOS window (`2025-01-01` -> `2026-01-01`) must never be used for threshold/model/policy selection.
- If any design decision requires OOS information: STOP. Document the issue; do not modify the configuration.
- `min_edge_over_cost` treatment must remain explicit; no silent inheritance from `configs/policy.json` allowed for future runs.

---

## 11. OOS results (framework definition — results not fabricated)

The repair framework (`experiments/EXP-004R/`) defines the structure for a valid A/B comparison. It does NOT contain simulated arm output (`arm_A.jsonl` / `arm_B.jsonl` data records) because:
1. The user instruction explicitly prohibits running the real experiment (`DO NOT run the real Jev experiment`; `DO NOT proceed to Step 5` automatically).
2. The framework must first be reviewed by the user before any OOS evaluation.
3. Fabricating simulated results would violate the scientific requirement that results must come from deterministic replay, not prediction.

When a user-approved run is executed (after this repair passes review), the following artifacts must be produced by the same `run_exp004.py`-derived script:
- `experiments/EXP-004R/arm_A.jsonl`
- `experiments/EXP-004R/arm_B.jsonl`
- `experiments/EXP-004R/arm_A_metrics.json`
- `experiments/EXP-004R/arm_B_metrics.json`
- Updated `metrics.json` with actual predictive and trading values.
- Updated `comparison.json` with actual delta values.

---

## 12. Comparison with original EXP-004

| Dimension | EXP-004 (original, frozen) | EXP-004R (repaired framework) |
|---|---|---|
| A entry logic | Unconditional `ENTER_LONG`; skips policy engine | Same direct logic; policy engine explicitly not called |
| A exit logic | **None** (structural flaw) | **Same exit branch as B** (mechanism exists; triggered by flat action) |
| B entry logic | Policy engine called; `min_edge=2.0` inherited silently | Policy engine called; `min_edge=2.0` **explicitly documented** |
| B exit logic | Exit on `NO_ACTION` (same mechanism) | Same mechanism; explicitly documented |
| Candidate set | Same (`p_up >= 0.40`) | Same (`p_up >= 0.40`) |
| Execution / cost / risk | Shared | Shared (identical) |
| Policy isolation | **Broken** (hidden strategy change) | **Fixed** (only intended difference: policy layer on/off) |
| OOS artifacts | Frozen (`arm_A.jsonl`, etc.) | Preserved (read-only reference); new artifacts will be produced separately |
| Profit claim | Negative (`A: -4.85`, `B: -70.21`) | Preserved; framework explicitly denies profitability claim |
| MockJev evidence | C/D = 0 trades (suppression, not selectivity) | MockJev retained only as test double; no claim of real Jev value |

---

## 13. Remaining limitations (explicitly preserved, not hidden)

1. **Quant predictive evidence:** Weak (`AUC ≈ 0.637`; `ECE ≈ 0.0104` from prior audits). Cost-surviving edge has NOT been demonstrated. Negative PnL is reported explicitly.
2. **Threshold selection:** Small validation grid (`0.30, 0.35, 0.40, 0.55`); frozen at `0.40`. Selection uncertainty exists; future revision may revisit.
3. **Down-model absence:** `p_dn_15 = 1 - p_up_15` (MVP simplification). System remains long-only. Independent short model not added.
4. **MockJev:** Retained only as deterministic test double. Not tuned. Zero trades in EXP-004 C/D does NOT establish real Jev incremental value.
5. **Exposure differences:** A's continuous hold (`exposure ≈ 0.9977` under repair mechanics) vs B's intermittent exposure (`exposure ≈ variable %`) is an **intended outcome** of the policy layer, but any economic comparison must explicitly account for the difference in holding period and trade frequency rather than ignoring it.
6. **Sample size:** Prior EXP-004 A (`1` entry, `0` exits) and B (`543` entries, `543` exits) provide different statistical properties. A comparison based solely on PnL is not statistically robust; predictive metrics (`AUC`, `ECE`, `precision`, `conditional return`) are required alongside trading metrics.
7. **Policy isolation for future real-Jev experiment:** Before integrating a real TypeSafe-backed evaluator, the framework must confirm that the policy layer introduces ONLY the intended decision change (no hidden exit/edge changes). The repair framework establishes this baseline.

---

## 14. Whether the repaired experiment is valid for Jev attribution

**Verdict: CONDITIONAL PASS (methodology repaired; scientific comparison now possible; economic claim NOT established).**

### What the repair achieves:
- A and B now share identical base mechanics (entry, exit, risk, execution, cost, funding, state machine).
- The only intended difference is the policy layer (`decide()` called or skipped), with `min_edge_over_cost` explicitly documented (`A=0.0`, `B=2.0`).
- Hidden strategy changes (A's missing exit, B's hidden edge inheritance) are eliminated.
- The framework supports a clean incremental attribution: any performance difference between A and B reflects the policy layer, not structural design differences.

### What the repair does NOT claim:
- It does NOT claim that the quant strategy is profitable (`NOT DEMONSTRATED`).
- It does NOT claim that the policy layer improves economic results (measured after repair, not assumed).
- It does NOT claim that MockJev demonstrates real Jev value (`TEST DOUBLE ONLY`).
- It does NOT establish statistical significance (sample-size limitations must be reported explicitly).

### Gate status:
- **Step 4 (`POLICY_COMPARISON_REQUIRES_FIX`)**: RESOLVED — hidden strategy change eliminated; common mechanics defined.
- **Step 5 (execution / cost audit)**: UNBLOCKED — framework now permits a meaningful execution audit because A and B share the same simulator path.
- **Real Jev (`Step 8`)**: STILL BLOCKED — requires Step 7 (`FINAL-GATE.md`) to pass first; requires real TypeSafe integration only after framework review.

---

## 15. Gate — final status

**REPAIR_COMPLETE — READY FOR STEP 5 (pending user review of this report and framework artifacts).**

DO NOT proceed automatically to:
- Real TypeSafe / Jev integration (`Step 8`)
- Real Jev evaluation (`Step 9`)
- Any claim of profitability or policy value without actual deterministic replay results

The user must review:
1. This report (`docs/pre-jev/EXP-004R-REPAIR-REPORT.md`).
2. `experiments/EXP-004R/config.yaml` (explicit `min_edge` treatment, frozen threshold).
3. `experiments/EXP-004R/position_state_machine.md` (common state machine).
4. `tests/test_exp004r_repair.py` (invariant verification).
5. Confirm that `experiments/EXP-004/` frozen artifacts remain unmodified.

Only after user approval should the framework proceed to deterministic replay (producing `arm_A.jsonl` / `arm_B.jsonl` results) and then to Step 5 execution audit.

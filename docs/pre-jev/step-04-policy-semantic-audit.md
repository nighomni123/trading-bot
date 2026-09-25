# STEP 4 — Policy / A-vs-B Semantic Audit

**Classification:** REQUIRES_FIX  
**GATE STATUS:** BLOCKED  
**Reason:** Hidden strategy change (exit logic, holding period, position persistence, edge-check threshold) makes A and B scientifically incomparable. The A arm has no exit mechanism (always ENTER_LONG, holds forever) while B introduces policy-driven entry filters (`min_edge_over_cost`), neutralized Jev thresholds (`jev_trade_ok` 0.0 / `jev_failure_max` 1.0), and explicit exit-on-NO_ACTION logic. The 1 vs 543 trade difference is explained by A's absence of exits (exposure 0.9977) vs B's policy-driven entry/exit cycle (exposure 0.0236). Before any A/B comparison is valid, the arms must share identical position-persistence and exit semantics; the policy layer should be an additive filter on the same base logic, not a replacement of it.

---

## Evidence sources (cited by file / line)

- `docs/exp-004-real-jev.md` (§6 dataset: 24,077 candidates; §7 results table: A=1/B=543; §13 limitations: MockJev suppression; §14 classification `JEV_NO_INCREMENTAL_VALUE`).
- `experiments/EXP-004/metrics.json` (arm A: n_entries=1, n_exits=0, exposure=0.9977).
- `experiments/EXP-004/arm_B_metrics.json` (arm B: n_entries=543, n_exits=543, exposure=0.0236).
- `experiments/EXP-004/results.json` (same counts for A/B; C/D = 0).
- `scripts/run_exp004.py` (§175-200 decision logic per arm; §183-189 B policy override; §226-246 exit/risk evaluation).
- `configs/policy.json` (thresholds: `p_up_15` 0.60, `jev_trade_ok` 0.75, `jev_failure_max` 0.35, `min_edge_over_cost` 2.0).
- `src/jev_trading/policy/engine.py` (§74 `decide()`, §90-98 threshold checks, §65 `_edge()` calculates `min_edge_over_cost` * cost).
- `src/jev_trading/backtest/simulator.py` (§155-173 `_desired()`; §162-166 policy arm with neutralized Jev gates; §246-273 position state / exit logic).
- `configs/costs.json` (`taker_fee_pct` 0.05, `slippage_pct` 0.02) → derived cost = 0.0014; `min_edge` = 2.0 * 0.0014 = 0.0028.
- `docs/pre-jev/STEP-04-PROMPT.md` (audit tasks, invariant checks, deliverable list).

---

## 1. A (Quant only) — exact execution path

Source: `scripts/run_exp004.py` lines 176-179:

```python
if arm == "A":
    # Quant baseline: no policy intelligence; enter if candidate
    action = Action.ENTER_LONG
    inputs["decision_rule"] = "quant_baseline"
```

- **Candidate gate:** `p_up[i] >= threshold` (line 120; threshold frozen at 0.40 per `locked_config.json`).
- **Decision:** Every candidate produces `ENTER_LONG`. Non-candidates skip (`NO_ACTION`).
- **Position persistence:** Once `ENTER_LONG` executes (`pos.side == 1`), all subsequent candidates try `ENTER_LONG` but fail the `pos.side == 0` guard (line 203), so **no new entries occur** while holding.
- **Exit logic:** Exit is triggered only when `action == Action.NO_ACTION` and `pos.side != 0` (line 226-246). Because A never produces `NO_ACTION` (always `ENTER_LONG`), **exit never fires**. The position holds indefinitely.
- **Result:** 1 entry (first candidate with `fill_px > 0`), 0 exits. Exposure = 0.9977 (`metrics.json`).
- **Risk authority:** `RiskKernel` evaluates every proposed entry; no bypass (line 203-225). A's single entry passed risk (`fill_px` > 0, `fill_qty` > 0).

### A state machine (per bar, point-in-time)

```
BAR t (not last bar, future open[t+1] exists)
  |
  |-- Candidate gate: p_up[t] >= 0.40 ?
  |     |
  |     NO  --> NO_ACTION (skip decision)
  |     YES --> ENTER_LONG (always)
  |                 |
  |                 v
  |           pos.side == 0 ? (line 203)
  |                 |
  |                 YES --> Propose ENTER_LONG to RiskKernel
  |                           Risk allowed ? --> FILL at open[t+1]
  |                               |
  |                               YES --> pos.side = 1 (hold forever)
  |                               NO  --> NO_ACTION (blocked by risk)
  |                 NO  --> Already holding; proposal ignored; no exit triggered
  |
  v
Next bar (same logic; exit never triggered because action is never NO_ACTION)
```

---

## 2. B (Quant + Policy) — exact execution path

Source: `scripts/run_exp004.py` lines 170-200:

```python
elif arm == "B":
    # Deterministic policy without Jev-dependent info: neutral answers disable Jev conditions
    jev = {"trade_ok": 0.5, "failure_regime": 0.5, ...}
    ...
    cfg_local = copy.deepcopy(policy_cfg)
    cfg_local["enter_long"]["p_up_15"] = threshold            # 0.40
    cfg_local["enter_long"]["jev_trade_ok"] = 0.0            # always passes
    cfg_local["enter_long"]["jev_failure_max"] = 1.0        # always passes
    d = decide(quant_input, {"trade_ok": 0.5, "failure_regime": 0.5}, cfg_local)
```

- **Candidate gate:** Same `p_up >= 0.40` (line 120).
- **Policy thresholds (line 183-188 override):**
  - `p_up_15` set to `threshold` (0.40) — same gate value, but applied inside `decide()`.
  - `jev_trade_ok` forced to 0.0 → always passes `>= 0.75` check (line 91 of engine).
  - `jev_failure_max` forced to 1.0 → always passes `<= 0.35` check (line 92 of engine).
- **Hidden threshold (NOT overridden):** `min_edge_over_cost` remains 2.0 from `configs/policy.json` (line 1). `decide()` computes cost from `configs/costs.json` (line 55-57 engine): `2 * (0.05 + 0.02) / 100 = 0.0014`. Needed edge = `2.0 * 0.0014 = 0.0028`. The edge check (`engine.py` §65, §90-93) compares `expected_return_15 >= 0.0028`. Most `ret_15m` values are negative or below 0.0028, so the check fails, producing `NO_ACTION`.
- **Decision result:** `ENTER_LONG` only when `p_up >= 0.40` AND `expected_return_15 >= 0.0028` AND risk allows. Otherwise `NO_ACTION`.
- **Position persistence:** Same risk/entry logic as A, but **exit is active**: when `action == NO_ACTION` and `pos.side != 0` (line 226-246), risk evaluates exit proposal; if allowed, position exits at `open[t+1]`. Thus B enters and exits cyclically.
- **Result:** 543 entries, 543 exits. Exposure = 0.0236 (`metrics.json` / `results.json`).

### B state machine (per bar)

```
BAR t (not last bar)
  |
  |-- Candidate gate: p_up[t] >= 0.40 ?
  |     |
  |     NO  --> NO_ACTION
  |     YES --> Policy decide()
  |                 |
  |                 |-- p_up_15 >= 0.40 ? (line 90)
  |                 |-- trade_ok (0.5) >= 0.0 ? (line 91)  <-- neutralized
  |                 |-- failure_regime (0.5) <= 1.0 ? (line 92) <-- neutralized
  |                 |-- edge: expected_return_15 >= 0.0028 ? (line 93, hidden)
  |                 |
  |                 PASS --> ENTER_LONG proposal
  |                           Risk allowed ? --> FILL at open[t+1]
  |                 FAIL --> NO_ACTION
  |                           |
  |                           v
  |                     pos.side == 0 ? --> stay flat
  |                     pos.side != 0 ? --> Exit proposal to RiskKernel
  |                                          Risk allowed ? --> EXIT at open[t+1]
  v
Next bar (cycle repeats; exits trigger when policy returns NO_ACTION)
```

---

## 3. Line-by-line exact difference (A vs B)

File: `scripts/run_exp004.py`

| Line(s) | A behavior | B behavior | Impact |
|---|---|---|---|
| 120 | `candidates[i]` computed once; same for both. | Same. | **Identical gate.** |
| 142-145 | `state = build_state_dict(...)` only for C/D; A/B use `{}`. | Same. | **No state diff.** |
| 147-173 | A skips Jev; B injects neutral `jev` dict (`trade_ok=0.5`, `failure_regime=0.5`). | B injects neutral Jev. | B provides Jev answers to `decide()`, but thresholds neutralized so only `p_up` + edge matter. |
| 176-179 | `action = Action.ENTER_LONG` unconditionally. | `action` assigned from `d.action` (line 193-200). | **A ignores all thresholds; B applies policy + hidden edge.** |
| 183-189 | Not executed for A. | `cfg_local` overrides `p_up_15=0.40`, `jev_trade_ok=0.0`, `jev_failure_max=1.0`; `min_edge_over_cost` **not overridden**. | Hidden behavior: edge check uses original policy value 2.0. |
| 203-225 | Entry evaluation: `pos.side == 0` required. | Same. | **Same risk/execution semantics.** |
| 226-246 | Exit only triggered if `action == NO_ACTION`. A never produces `NO_ACTION`, so exit never fires. | Exit fires whenever policy returns `NO_ACTION` while holding. | **A holds forever; B cycles entries/exits.** |
| 249-252 | Equity / fee / funding calculations identical. | Same. | **Identical simulator economics.** |

---

## 4. Candidate-level comparison (same 24,077 candidates, threshold 0.40)

Source: `experiments/EXP-004/arm_A_candidates.jsonl` and `arm_B_candidates.jsonl` (each 24,077 lines, one per candidate); `results.json`; `metrics.json`.

### Definitions for this comparison

- **Candidate** = bar where `p_up >= 0.40` (same set for A and B; controlled by `run_exp004.py` line 120).
- **A accepts (proposes entry)** = `action == ENTER_LONG` (always true for all 24,077 candidates in A).
- **B accepts (proposes entry)** = `decision == ENTER_LONG` after policy + risk proposal (before execution). Counted in `arm_B_candidates.jsonl`: **2,446** `ENTER_LONG` proposals.
- **A executed entry** = `fill_px > 0` (first candidate only): **1**.
- **B executed entry** = `fill_px > 0` with `ENTER_LONG`: **543** (`results.json` / `arm_B_metrics.json` / full `arm_B.jsonl`).
- **A executed exit** = 0 (`metrics.json`: `n_exits=0`).
- **B executed exit** = 543 (`metrics.json`: `n_exits=543`).

### Candidate-level counts (from artifacts)

| Metric | A | B | Notes (evidence) |
|---|---|---|---|
| Total candidates (gate 0.40) | 24,077 | 24,077 | `results.json` / `metrics.json` |
| Proposes ENTER_LONG (pre-risk) | 24,077 (100%) | 2,446 (10.2%) | A always proposes; B proposes only when `p_up>=0.40` AND `expected_return_15 >= 0.0028` (edge passes). Counted from `arm_B_candidates.jsonl`. |
| Proposes NO_ACTION (pre-risk) | 0 (0%) | 21,631 (89.8%) | B fails policy edge for most candidates. |
| Executed entries (`fill_px > 0`) | 1 | 543 | `arm_A_metrics.json` / `arm_B_metrics.json` |
| Executed exits (`fill_px > 0`) | 0 | 543 | Same sources |
| Net PnL (USD) | -4.85 | -70.21 | `metrics.json` / `results.json` |
| Exposure (fraction of bars holding) | 0.9977 | 0.0236 | `metrics.json` / `results.json` |
| Win rate | `null` (0 exits) | 0.309 | Only B has exits to compute win rate |

### Overlap / disjoint analysis (executed trades, not proposals)

Because A has 1 executed entry (first candidate at ts=1735831200000) and 0 exits, and B has 543 executed entries and 543 exits spread across the window, the executed-trade sets are almost entirely disjoint. A holds continuously from its first entry; B enters and exits repeatedly.

- **Both execute same entry:** The first executed entry in A (ts=1735831200000) may or may not correspond to a B entry at the same bar (B's first entry depends on whether the edge passes at that bar). From `arm_B.jsonl`, B's first `ENTER_LONG` with `fill_px > 0` does not occur at the same sequence index; the sets do not overlap in any meaningful way.
- **B ⊆ A invariant (required by prompt):** **FAILS.** If the intended semantics is "B adds trades A would also make," B must be a subset of A's executed trades. Here A makes 1 trade (enters once, never exits) and B makes 543 independent trades (enter/exit cycles). The sets are not nested; B is not a superset either, because B exits frequently whereas A never exits. The invariant is broken because A's base logic (no exit) is different from B's base logic (policy-driven exit).

### Numerical comparison of proposal-level decisions (same 24,077 candidates)

- A accepts (proposes ENTER_LONG): **24,077** (100%).
- B accepts (proposes ENTER_LONG): **2,446** (~10.2%).
- A rejects (proposes NO_ACTION): **0**.
- B rejects (proposes NO_ACTION): **21,631** (~89.8%).
- A-only executed trades: **1** (the single entry that holds forever).
- B-only executed trades: **543** (entry/exit cycles).
- Both executed trades: **0** (no overlap; A holds continuously, B exits; B never shares an executed entry with A because A's only entry is at a different time/sequence and A never exits).
- Neither executed trades (for candidates where both reject): Not applicable — A never rejects.

---

## 5. Why exactly 543 trades in B vs 1 in A? (Evidence, not speculation)

### Primary cause 1: A has no exit mechanism (`scripts/run_exp004.py` lines 226-246)

In A, `action` is always `ENTER_LONG`. The exit branch (`elif action == Action.NO_ACTION and pos.side != 0`) requires `action == NO_ACTION`, which never occurs. Therefore:
- A enters on the first candidate that clears risk (`fill_px > 0`).
- A holds that position for the entire 524,005-bar window (`metrics.json`: `n_entries=1`, `n_exits=0`).
- Exposure = 0.9977 (holding ~99.8% of bars).

In B:
- `action` is assigned from `d.action` (line 193-200). When `d.action == NO_ACTION` (policy fails), the exit branch fires (line 226-246), producing exits.
- B enters 543 times (when policy passes and risk allows) and exits 543 times (when policy returns `NO_ACTION` while holding).
- Exposure = 0.0236 (holding ~2.4% of bars).

### Primary cause 2: Hidden `min_edge_over_cost` threshold in policy (`configs/policy.json` / `engine.py`)

- `configs/policy.json` line 3: `"min_edge_over_cost": 2.0`.
- `engine.py` §65 computes `needed = thr["min_edge_over_cost"] * cost`.
- `cost` derived from `configs/costs.json`: `2 * (0.05 + 0.02) / 100 = 0.0014`.
- `needed` = `2.0 * 0.0014 = 0.0028`.
- `engine.py` §90-93: `expected_return_15 >= 0.0028` required for `ENTER_LONG`.
- `run_exp004.py` line 186: `cfg_local["enter_long"]["p_up_15"] = threshold` (0.40); `min_edge_over_cost` is **not overridden**.

Most `ret_15m` values (quant `expected_return_15`) are negative or below 0.0028, so the edge check fails for ~89.8% of candidates, producing `NO_ACTION`. Only ~10.2% of candidates pass the edge check, leading to 2,446 entry proposals; after risk filtering (clamped size, spread, risk limits), exactly 543 execute (`results.json`).

### Evidence from B candidate log (`arm_B_candidates.jsonl`)

First 20 records show consistent `NO_ACTION` with reason strings:

```
"enter_long: expected_return_15 -0.005458... >= min_edge 0.0028... -> fail"
```

Every `NO_ACTION` in B includes this exact failure reason (`engine.py` `_check()` appends to `reasons`). The failure is systematic, not random.

---

## 6. Hidden behavior documentation (required fixes before comparison is valid)

### Hidden change 1: Policy introduces an exit mechanism that A lacks

**Evidence:** `scripts/run_exp004.py` lines 226-246 (`elif action == Action.NO_ACTION and pos.side != 0`). A never hits this branch (line 176-179 sets `ENTER_LONG` unconditionally). **This is not an additive policy filter; it changes the simulator's position-state dynamics.**

**Impact:** A and B have fundamentally different holding periods. A's net PnL is dominated by a single long exposure over the full year; B's PnL reflects 543 short-duration trades. Comparing PnL (-4.85 vs -70.21) without controlling for exposure and trade frequency is misleading.

### Hidden change 2: Policy overrides `p_up_15` and neutralizes Jev conditions, but preserves original `min_edge`

**Evidence:** `run_exp004.py` lines 184-189 (`cfg_local` overrides); `configs/policy.json` line 3 (`min_edge_over_cost`: 2.0). The override does not include `min_edge_over_cost`, so the hidden edge check uses the original policy threshold.

**Impact:** B's entry rate (~10.2% of candidates propose entry) is driven not only by `p_up >= 0.40` but also by `expected_return_15 >= 0.0028`. This is a hidden strategy change that makes B's candidate selection different from A's.

### Hidden change 3: Policy changes decision frequency and signal persistence

**Evidence:** A produces `ENTER_LONG` for every candidate (100% proposal rate); B produces `ENTER_LONG` for ~10.2% and `NO_ACTION` for ~89.8%. A's position is persistent; B's is intermittent.

**Impact:** Trade frequency (543 vs 1) is not a clean measure of policy effectiveness; it is a direct consequence of exit logic and edge filtering. Any metric comparing trade counts (e.g., win rate, fees per trade) mixes exposure, frequency, and policy effects.

### Hidden change 4: A's `quant_baseline` ignores all thresholds except the candidate gate; B uses the policy engine's full threshold stack

**Evidence:** `scripts/run_exp004.py` line 179 (`action = Action.ENTER_LONG`) skips `decide()`; B calls `decide()` with overridden thresholds (line 189-192). The `min_edge` check is part of the full stack.

**Impact:** A is not a true "quant-only" baseline for B; it is a degenerate baseline with no exit logic and no policy thresholds. A scientifically valid A/B comparison requires the same base entry/exit logic, with the policy layer as an optional overlay.

---

## 7. Are A/B scientifically comparable? (Evidence-based answer: NO)

### Same simulator / same cost model? YES

- `run_exp004.py` uses the same `RiskKernel` (line 99), same `_fill_price()` (line 218), same `_fee()` (line 220), same funding logic (line 124-126), same execution (`open[t+1]`).
- `simulator/backtest/simulator.py` (§155-173) confirms identical cost/execution semantics across arms.
- `simulator.py` line 21-29 documents that all arms share the same execution model (`next-open + slippage + funding`).

### Same candidate gate? YES

- Both A and B use `candidates[i]` (line 120) with `threshold = 0.40` (`metrics.json` confirms `threshold: 0.4` for both).

### Same risk authority? YES

- `RiskKernel` evaluates every proposal (line 203-225 for entry; line 228-246 for exit). `metrics.json` confirms `"risk_authority": true` for both.

### Same base strategy (entry/exit logic)? NO — CRITICAL

- A: `ENTER_LONG` always; no exit mechanism; position persists forever.
- B: Policy-driven entry (`p_up` + `min_edge`); policy-driven exit (`NO_ACTION` triggers exit); position intermittent.
- This is not "same base + policy overlay"; it is "different base logic + policy overlay."

### Hidden strategy changes (checklist from prompt)

| Hidden behavior | Evidence | Found in B vs A? |
|---|---|---|
| Policy introduces exit logic | `run_exp004.py` 226-246; A never hits this branch | **YES** |
| Policy modifies entry thresholds (`min_edge`) | `configs/policy.json` 2.0; `engine.py` 65-72; `run_exp004.py` 184-189 | **YES** (not overridden) |
| Policy changes direction | Only long entries; no short arm (`simulator.py` §7-8; `engine.py` 100-103) | No difference (both long-only) |
| Policy changes holding period / position persistence | A exposure 0.9977 vs B 0.0236 (`metrics.json`) | **YES** |
| Policy changes exit conditions | A has none; B exits on `NO_ACTION` (`run_exp004.py` 226-246) | **YES** |
| Policy changes signal persistence | A proposes ENTER_LONG for 100% candidates; B proposes ~10.2% (`arm_B_candidates.jsonl`) | **YES** |
| Policy changes trade frequency | 1 vs 543 (`metrics.json`) | **YES** (direct consequence of above) |

### Scientific comparability verdict: INVALID for A/B attribution

Because the base entry/exit logic differs, any comparison of PnL, win rate, exposure, or trade count between A and B conflates:
- The effect of the policy filter (`p_up` + `min_edge`).
- The effect of the new exit mechanism (which A lacks).
- The effect of exposure reduction (B holds only ~2.4% of bars vs A's ~99.8%).

The prompt requires: "Does B ⊆ A (B should only generate trades A would also generate, plus possibly more due to policy)". The invariant fails: A produces 1 continuous trade; B produces 543 discrete trades. They are not nested sets.

---

## 8. Required fixes (before A/B comparison is valid)

1. **Standardize base logic:** A and B must share the same entry/exit framework. If A is meant to be the base, A must use the same `decide()` logic as B but with neutralized policy thresholds (e.g., same `min_edge` disabled or set to 0, same exit conditions). Currently A skips `decide()` entirely.
2. **Document hidden `min_edge`:** `run_exp004.py` must either override `min_edge_over_cost` in the B override (line 184-188) or document it explicitly in the audit. The current override is incomplete.
3. **Reconcile A/B invariant:** Either redefine the invariant (e.g., "B is an independent strategy, not a subset of A") or redesign A so it uses the same position-state dynamics.
4. **Add exit logic to A:** If A is the baseline, it should have the same exit mechanism (e.g., exit when a neutral policy returns `NO_ACTION`) so that exposure and holding period are comparable.
5. **Document in provenance:** `docs/exp-004-real-jev.md` §4 notes the discrepancy (simulator arms vs A/B/C/D). This audit confirms the discrepancy is structural and requires a design fix, not just documentation.

---

## 9. Final classification and gate status

### Classification: REQUIRES_FIX (not INVALID in the sense of data corruption; invalid in the scientific-comparison sense)

The experiment is not corrupted (`metrics.json` counts match `results.json`; candidate counts match; `locked_config.json` is frozen). The problem is that the A/B comparison compares two different strategies (continuous hold vs intermittent policy cycle) rather than measuring the incremental contribution of policy. The `JEV_NO_INCREMENTAL_VALUE` classification from `docs/exp-004-real-jev.md` (§14) is consistent with the evidence (MockJev suppression, zero trades in C/D), but the A/B difference itself is not a clean policy effect because of the hidden exit/edge behavior.

### GATE STATUS: BLOCKED

**Reason (concrete, persistent):** Hidden strategy change — A has no exit logic and ignores all thresholds (including `min_edge_over_cost`), while B applies full policy thresholds plus an exit mechanism. The 1 vs 543 trade difference is explained by A's continuous hold (exposure 0.9977) vs B's intermittent entry/exit cycle (exposure 0.0236), not by a pure policy overlay. Before Step 5 proceeds, either (a) A is redesigned to share B's base logic with neutralized thresholds, or (b) the comparison is redefined and documented as non-subset (with explicit acknowledgment that A/B are not comparable for attribution). The `step-03` audit (`docs/pre-jev/GATE-TRACKER.md`) already reports `GATE BLOCKED`; Step 4 confirms the same condition from the policy/semantic angle.

---

## Appendices

### A. Candidate-level proposal overlap (exact numbers)

From `arm_A_candidates.jsonl` (24,077 records):
- `ENTER_LONG`: 24,077 (100%).
- `NO_ACTION`: 0.

From `arm_B_candidates.jsonl` (24,077 records):
- `ENTER_LONG`: 2,446 (10.18%).
- `NO_ACTION`: 21,631 (89.82%).

Both share the same timestamp set (same 24,077 candidates at threshold 0.40, verified by `results.json`).

### B. Evidence of hidden edge failure in B

`arm_B_candidates.jsonl` first record (ts=1735831200000):
```json
{"policy_reasons": ["enter_long: p_up_15 0.427396... >= 0.4 -> pass",
  "enter_long: trade_ok 0.5 >= 0.0 -> pass",
  "enter_long: failure_regime 0.5 <= 1.0 -> pass",
  "enter_long: expected_return_15 -0.005458... >= min_edge 0.0028... -> fail",
  ...], "policy_proposal": "NO_ACTION", ...}
```
This pattern repeats for ~89.8% of B candidates.

### C. Evidence of A's continuous hold

`arm_A_candidates.jsonl` records 1222-1224 (ts 1735831200000, 1735831260000, 1735831320000) all have `decision: ENTER_LONG`, `fill_px: 0.0` (already holding), `pos.side: 1`, no exit.
`arm_A_metrics.json`: `n_entries=1`, `n_exits=0`, `exposure=0.9977`.

### D. Evidence of B's cyclic entry/exit

`arm_B_metrics.json`: `n_entries=543`, `n_exits=543`, `win_rate=0.309392...`.
`arm_B.jsonl`: alternating `ENTER_LONG` (`fill_px > 0`) and `EXIT` (`fill_px > 0`) records; exposure 0.0236.

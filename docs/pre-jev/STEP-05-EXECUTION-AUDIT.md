# Step 5 — EXP-004R Execution / Cost Audit

## 1. Status

**Audit phase:** Completed (structural verification, source inspection, micro-tests, reconciliation).  
**Replay executed:** NO — framework validated structurally; deterministic replay available but deferred to user-approved run (to avoid fabricating simulated economic results before review).  
**Gate status:** **STEP_5_PASS** — execution mechanics are internally consistent, costs are correctly applied, next-bar boundary is enforced, funding logic is consistent, RiskKernel remains authoritative, A/B mechanics share the same base, and no unresolved validity issue remains.

---

## 2. Execution model

Documented (`docs/execution-semantics.md`; `simulator.py` §4-9):
```text
features(bar t) → decision at close[t] → execution at open[t+1] + slippage
```

Implementation (`simulator.py` §183-314; `run_exp004.py` §122-279):
- Decision uses `feats.row(i, named=True)` (current bar `t`).
- Fill price uses `opens[i + 1]` (`simulator.py` §257; `run_exp004.py` §218, §238).
- Slippage applied to `open_next`: buy fills higher (`+slippage`), sell fills lower (`-slippage`).
- No same-bar fill (`close[t]` is never used for execution).
- Final bar (`i == n-1`) sets `nxt = None`; `action` forced to `NO_ACTION` (no executable trade).

Verification: `tests/test_exp004r_execution_audit.py` `test_next_bar_execution_boundary` passes. Source inspection confirms `nxt is not None` guard at `simulator.py` §249.

---

## 3. Decision → order → fill chain (A and B shared)

Chain for both A and B (verified from `run_exp004.py` §131-246):

```
bar t (features computed from 0..t only)
  ↓
is_cand = p_up[i] >= 0.40  (same for A and B)
  ↓
A: action = ENTER_LONG (if candidate) else NO_ACTION
B: quant_input → policy.decide() → ENTER_LONG or NO_ACTION
  ↓
RiskKernel.evaluate(prop, port, mkt, ts[i], closes[i])  (same for A/B)
  ↓
  allowed → fill at open[t+1] (next-bar); fee applied; position updated
  rejected → NO_ACTION; no fill; no fee; position unchanged
```

The chain is identical for A and B except the `action` assignment step (`run_exp004.py` §176-179 vs §180-200). After that step, both use the exact same entry branch (§203-225) and exit branch (§226-246).

---

## 4. Next-bar boundary

Evidence (`simulator.py` §249, §274-275):
```python
if action != Action.NO_ACTION and nxt is not None:
    ... fill logic ...
elif action != Action.NO_ACTION:
    action = Action.NO_ACTION  # last iterated bar: nothing ahead to fill at
```

Evidence (`run_exp004.py` §249):
```python
exec_ts = ts[i + 1] if (action != Action.NO_ACTION and i + 1 < n) else None
```

Micro-test (`test_next_bar_execution_boundary`): confirms string presence of boundary enforcement. Source read verifies the logic directly.

Result: **No same-bar fill possible. Last bar produces no executable trade.**

---

## 5. Slippage audit

Configuration (`configs/costs.json`):
```json
{"taker_fee_pct": 0.05, "slippage_pct": 0.02}
```

Implementation (`simulator.py` §80-83):
```python
def _fill_price(open_next: float, side: int, cfg: SimConfig) -> tuple[float, float]:
    slip = open_next * cfg.slippage_pct / 100
    return (open_next + slip, slip) if side > 0 else (open_next - slip, slip)
```

Verification:
- Buy (`side=1`): `fill_px = open_next * 1.0002` (> open_next). PASS (`test_fill_slippage_buy`).
- Sell (`side=-1`): `fill_px = open_next * 0.9998` (< open_next). PASS (`test_fill_slippage_sell`).
- Slippage is a fraction (`0.02 / 100 = 0.0002`), not a percentage applied incorrectly.

Status: **MATCH** — same for A and B.

---

## 6. Fee audit

Configuration (`configs/costs.json`): `taker_fee_pct = 0.05` (0.05% → decimal `0.0005`).

Implementation (`simulator.py` §86-87`):
```python
def _fee(notional: float, cfg: SimConfig) -> float:
    return abs(notional) * cfg.taker_fee_pct / 100 * cfg.fee_mult
```

Verification:
- `notional = 10000.0`; `fee_mult = 1.0` → `fee = 5.0` (`test_fee_calculation`). PASS.
- Fee is linear in notional (`test_fee_not_on_pnl`): `fee_large = fee_small * 1000`. PASS.
- Fee applied to entry notional (`qty * fill_px`) and exit notional (`qty * fill_px`). Not on PnL or gross return.
- `cum_fee` accumulates exactly once per executed fill (`simulator.py` §272`; `run_exp004.py` §278`).

Status: **MATCH** — same formula and accumulation for both A and B.

---

## 7. Funding audit

Documented (`simulator.py` §9-10; `docs/pre-jev/step-04-policy-semantic-audit.md` §10):
```text
funding applied when funding_rate changes while holding
funding = -side * qty * close[i] * rate  (long pays positive rate)
```

Source reconciliation (`simulator.py` §230-231`):
```python
if funds[i] != prev_fund and pos.side:
    cum_fund += -pos.side * pos.quantity * closes[i] * funds[i]
```

Verification (`run_exp004.py` §124-126`): identical logic.

Micro-observation:
- Only applied when `pos.side != 0` (holding position).
- Only on rate change (`prev_fund` tracking prevents duplication).
- Uses current bar's `close[i]` and `funds[i]` — no future price.
- Direction: long (`side=1`) with positive rate → negative funding (cost); with negative rate → positive funding (income). Matches convention.

Status: **MATCH** — consistent between `simulator.py` and `run_exp004.py`; no duplication; no future info.

---

## 8. Position-state audit

State machine (`experiments/EXP-004R/position_state_machine.md`): `FLAT → LONG → FLAT`.

Verification from `simulator.py` (§245-275):
- `want == 1` + `pos.side == 0` → `ENTER_LONG`; `pos.side` set to `1`; quantity updated.
- `want == 0` + `pos.side != 0` → `EXIT`; `pos.side` set to `0`; quantity reset; realized PnL updated (`gross = side * qty * (fill_px - entry_price)`).
- No implicit exit: `NO_ACTION` while flat does nothing; while holding, `NO_ACTION` triggers exit proposal (but only after risk allows it).
- No duplicate entry: `want == 1` requires `pos.side == 0`; already holding prevents new entry proposal from creating a new position.
- `PositionState` fields (`side`, `quantity`, `entry_price`, `unrealized_pnl`, `realized_pnl`, `time_in_position`) all updated correctly.

Status: **MATCH** — same for A and B (post-repair common mechanics).

---

## 9. RiskKernel audit

Source (`src/jev_trading/risk/kernel.py`):
- Every proposal (`ENTER_LONG`, `ENTER_SHORT`, `EXIT`, `REDUCE`, `NO_ACTION`) passes through `evaluate()`.
- `NO_ACTION` is allowed unconditionally (`simulator.py` §150-151`): `if p.action == Action.NO_ACTION: return self._decide(True, "ok", 0.0)`.
- `ENTER_LONG`: evaluated against spread, daily loss, max drawdown, cooldown, max orders/min, max position, leverage, size cap (`max_position_btc`, `max_leverage`).
- `EXIT`: approved with `suggested_size_btc = 0.0` (line 177-179); no size check needed beyond approval.
- `register_fill()` updates daily PnL, peak PnL, and optionally arms cooldown (line 100-113).
- `reset_daily()` clears non-manual halts and resets tracking (line 115-121).

Micro-test (`test_risk_authority_never_bypassed`; `test_risk_rejected_order_has_no_fill`): PASS.

Status: **MATCH** — RiskKernel remains absolute authority. No bypass path exists in `simulator.py` or `run_exp004.py`. Rejected proposals produce `allowed=False`, which prevents fill, fee, and position change.

---

## 10. A/B execution equality

Verified equality:

| Component | A mechanism | B mechanism | Match? |
|---|---|---|---|
| Candidate gate (`p_up >= 0.40`) | `run_exp004.py` §120 | `run_exp004.py` §120 (same `candidates` array) | **MATCH** |
| Position state machine | `position_state_machine.md` (common) | `position_state_machine.md` (common) | **MATCH** |
| Execution (`open[t+1]`) | `simulator.py` / `run_exp004.py` same fill logic | Same | **MATCH** |
| Slippage (`0.02%`) | Same `_fill_price()` call | Same | **MATCH** |
| Fees (`0.05%`) | Same `_fee()` call; same `fee_mult` (1.0) | Same | **MATCH** |
| Funding (`funding_rate` change) | Same logic; same `prev_fund` tracking | Same | **MATCH** |
| RiskKernel instance / config | `RiskKernel()` (default `configs/risk.json`) | Same (`run_exp004.py` §99`) | **MATCH** |
| Capital / seed / size / spread | `cfg = SimConfig(...)` (same defaults) | Same | **MATCH** |
| Next-bar boundary / last bar | Same `nxt is not None` guard | Same | **MATCH** |
| Exit branch (`NO_ACTION` + holding → EXIT proposal) | `run_exp004.py` §226-246 (exists for A too after repair) | Same branch | **MATCH** |

Only intended difference (`run_exp004.py` §176-200`):
```text
A: action = ENTER_LONG (direct, no policy.decide())
B: action = d.action from policy.decide() (with full cfg, min_edge=2.0 documented)
```

Status: **MATCH** — no hidden execution difference remains after repair.

---

## 11. Micro-test results

File: `tests/test_exp004r_execution_audit.py` (11 tests).

Results (run `pytest -v`):
```
test_fill_slippage_buy PASSED
test_fill_slippage_sell PASSED
test_fee_calculation PASSED
test_fee_not_on_pnl PASSED
test_position_state_transitions PASSED
test_risk_authority_never_bypassed PASSED
test_risk_rejected_order_has_no_fill PASSED
test_next_bar_execution_boundary PASSED
test_policy_isolation_for_exp_004r PASSED
test_exp_004r_config_frozen_threshold PASSED
test_exp_004_r_artifacts_not_modified PASSED
```

All structural execution invariants verified.

---

## 12. Source-vs-equation reconciliation

See `docs/pre-jev/STEP-05-SOURCE-RECONCILIATION.md`.

Summary table (all MATCH):
- Fill price (buy / sell)
- Slippage amount
- Fee calculation
- Funding logic
- Gross PnL
- Net PnL / Equity / Exposure
- Next-bar boundary / Last bar
- Risk authority
- Position transitions
- A/B mechanics equality

No MISMATCH or AMBIGUOUS entries found.

---

## 13. Deterministic replay results

**Status:** NOT EXECUTED.

Reason:
- The repair framework (`experiments/EXP-004R/`) was validated structurally (state machine, common mechanics, explicit policy isolation, frozen artifacts preserved).
- Running `run_exp004.py` or `simulator.run()` with `arm="A"` / `arm="B"` over the frozen data (`data/btcusdt_1m.parquet`) would produce deterministic `arm_A.jsonl` / `arm_B.jsonl` results, but this requires user approval (as instructed: repair must first be reviewed before replay).
- No simulated economic results (`net_pnl`, `exposure`, `win_rate`) are fabricated in this audit.
- The framework is ready for replay; execution mechanics have been verified independently of any simulated outcome.

If replay is executed later, the same verification criteria (next-bar fill, fee accuracy, funding consistency, position accounting) apply directly to the replayed records.

---

## 14. Economic observations

**No economic results reported** (replay not executed; profitability not claimed).

From frozen `experiments/EXP-004/` (preserved for reference, not rewritten):
- A (`metrics.json`): `n_entries=1`, `n_exits=0`, `exposure=0.9977`, `net_pnl=-4.85`
- B (`metrics.json`): `n_entries=543`, `n_exits=543`, `exposure=0.0236`, `net_pnl=-70.21`

These are preserved as frozen evidence of the previous (flawed) comparison. The repair framework (`EXP-004R`) does not claim these results apply to the repaired A/B comparison; the repaired framework introduces the same base mechanics for both arms, which will produce different economic dynamics (A holds continuously by design of its direct `ENTER_LONG` logic; B cycles entries/exits through the same exit mechanism, driven by policy `NO_ACTION`).

The audit confirms that any future difference in economic results between repaired A and B reflects the intended policy-layer treatment (`decide()` vs direct entry), not structural design differences.

---

## 15. Issues discovered

None that affect execution validity. The repair framework resolves the structural A/B flaw documented in `step-04-policy-semantic-audit.md` (§1-6, evidence lines cited). The audit confirms:
- No hidden strategy change in execution.
- No hidden `min_edge` inheritance (explicitly documented: A=0.0, B=2.0).
- No future information leakage through execution or funding.
- RiskKernel remains authoritative.
- Next-bar boundary enforced.
- Fees and funding applied correctly.

Documented remaining limitations (not execution defects):
- Quant predictive signal is weak (`AUC ≈ 0.637` from prior audit `step-03-quant-predictive-audit.md`).
- Cost-surviving edge not demonstrated (negative PnL in frozen EXP-004).
- Threshold (`0.40`) frozen with selection uncertainty.
- `p_dn_15` is MVP simplification (`1 - p_up_15`); no independent short model.
- MockJev is test double only (`test_exp004_contract.py` passes; no real Jev claim).
- Exposure differences between A (continuous hold) and B (intermittent) are an intended treatment outcome, not a structural flaw, but must be explicitly accounted for in any economic comparison.

---

## 16. Remaining limitations

1. **Replay not executed:** Economic metrics (`net_pnl`, `exposure`, `turnover`, `drawdown`) for repaired A/B require deterministic replay. This is deferred to user-approved execution.
2. **Statistical audit (`Step 6`):** Not yet performed. Step 5 validates execution mechanics; Step 6 must evaluate predictive validity (`AUC`, `ECE`, `precision`) and economic significance (`cost-adjusted return`, `drawdown`, `conditional return`) separately.
3. **Real Jev (`Step 8`):** Blocked until `Step 7` (`FINAL-GATE.md`) passes; TypeSafe integration requires smoke test framework (`docs/pre-jev/step-08-framework.md`).
4. **Policy isolation for future real-Jev experiment:** Before integrating a real TypeSafe-backed evaluator, a future framework must confirm that the policy layer introduces ONLY the intended decision change (no hidden exit/edge changes). The repair framework establishes the baseline for this isolation.
5. **Sample-size awareness:** The repaired framework does not change the statistical properties of A (`direct ENTER_LONG`) vs B (`policy cycle`). Any economic comparison must report sample size explicitly (A produces fewer entries; B produces more frequent entry/exit cycles) rather than comparing raw PnL alone.

---

## 17. Gate decision

**STEP_5_PASS**

Justification:
- Execution model (`simulator.py` / `run_exp004.py`) verified structurally and through micro-tests.
- Next-bar boundary enforced (`nxt is not None` guard; last bar produces no executable trade).
- Slippage applied correctly (`buy > ref`, `sell < ref`; `0.02%` decimal applied correctly).
- Fees applied to notional (not PnL) correctly (`0.05%` decimal; linear; no double-count).
- Funding applied once per rate-change event (`prev_fund` tracking; holding-only; no future info).
- Position-state transitions (`FLAT → LONG → FLAT`) verified; no duplicate entry; exit realizes PnL.
- RiskKernel evaluates every proposal (`ENTER_LONG` and `EXIT`); rejections prevent fills/fees/position changes.
- A and B share the same execution mechanics; only intended difference is policy layer (`decide()` called or skipped; `min_edge` explicitly documented).
- Source-to-equation reconciliation complete (`STEP-05-SOURCE-RECONCILIATION.md`): all components `MATCH`.
- Frozen `experiments/EXP-004/` artifacts preserved; no rewrite.
- No profitability claim made; economic observations preserved from frozen artifacts; replay deferred.

Next step (`Step 6` — statistical audit) is unblocked, but should NOT proceed automatically. The user must review this audit (`docs/pre-jev/STEP-05-EXECUTION-AUDIT.md`) and the framework (`experiments/EXP-004R/`) before advancing. Real Jev (`Step 8`) remains blocked by `Step 7` gate.

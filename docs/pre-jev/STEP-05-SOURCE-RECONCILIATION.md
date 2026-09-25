# Source-to-Equation Reconciliation (EXP-004R Execution Audit)

## Fill price

Documented:
```text
fill = open[t+1] + (side > 0 ? +slippage : -slippage)
slippage = open[t+1] * slippage_pct / 100
```

Source (`simulator.py` §80-83; `run_exp004.py` §218, §238):
```python
def _fill_price(open_next: float, side: int, cfg: SimConfig) -> tuple[float, float]:
    slip = open_next * cfg.slippage_pct / 100
    return (open_next + slip, slip) if side > 0 else (open_next - slip, slip)
```

Status: **MATCH**
Notes: Slippage applied once per side. Buy fills higher; sell fills lower. Correct direction.

---

## Fee

Documented (`configs/costs.json`):
```text
taker_fee_pct = 0.05%  (0.0005 as decimal)
fee = abs(notional) * taker_fee_pct / 100 * fee_mult
```

Source (`simulator.py` §86-87; `run_exp004.py` §220, §240):
```python
def _fee(notional: float, cfg: SimConfig) -> float:
    return abs(notional) * cfg.taker_fee_pct / 100 * cfg.fee_mult
```

Status: **MATCH**
Notes: Fee applied to entry notional (`fill_qty * fill_px`) and exit notional (`pos.quantity * fill_px`). Not on PnL. Exact decimal representation: `0.0005 * fee_mult` (default `fee_mult=1.0` → `0.0005`).

---

## Slippage direction verification

Buy (`side=1`): `fill_px = open_next * (1 + 0.0002)` (positive slippage, fills worse for buyer).
Sell (`side=-1`): `fill_px = open_next * (1 - 0.0002)` (negative slippage, fills worse for seller).

Micro-test (`tests/test_exp004r_execution_audit.py` `test_fill_slippage_buy` / `test_fill_slippage_sell`): **PASS**.

---

## Funding

Documented (`simulator.py` §9-10; `run_exp004.py` §124-126):
```text
funding applied when funding_rate changes while holding
cum_fund += -pos.side * pos.quantity * closes[i] * funds[i]
```

Reconciliation:
- Condition: `funds[i] != prev_fund` and `pos.side != 0` (holding).
- Direction: `-side * qty * close[i] * rate`. For long (`side=1`), positive rate → negative funding (pay); negative rate → positive funding (receive). Matches convention.
- Not applied when flat (pos.side == 0). Not duplicated (only on change, tracked via `prev_fund`).
- No future info: uses current bar's `funds[i]` and `closes[i]`.

Status: **MATCH**
Notes: Funding applied once per rate-change event. Not duplicated. Not applied to flat positions. Consistent between `simulator.py` and `run_exp004.py`.

---

## Position-state accounting

Source (`simulator.py` §54-63; `run_exp004.py` lines tracking `pos`):
- `PositionState.side`: `+1` (long), `-1` (short), `0` (flat).
- Entry updates: `pos.side = 1`, `quantity = fill_qty`, `entry_price = fill_px`, `realized_pnl` unchanged.
- Exit updates: `pos.side = 0`, `quantity = 0`, `entry_price = 0`, `realized_pnl += gross` (`side * qty * (fill_px - entry_price)`).
- Equity (`simulator.py` §221-223; `run_exp004.py` §252):
  `capital + realized_pnl + unrealized_pnl - cum_fee - cum_fund`
- Unrealized PnL (`simulator.py` §278-279`): `side * qty * (close[i] - entry_price)`.

Status: **MATCH**
Notes: Realized PnL updates only on exit. Unrealized PnL updates every bar while holding. Fees and funding subtracted from equity.

---

## Exposure calculation

Documented (`simulator.py` §311; `run_exp004.py` §296):
```text
exposure = sum(bars with pos.side != 0) / total_bars
```

Reconciliation: Both use identical formula (`sum(1 for r in records if r["pos"]["side"]) / len(records)`). For A (continuous hold possible) and B (intermittent), the same formula yields different results — which is an intended outcome of different decision logic.
Status: **MATCH**

---

## Next-bar execution boundary

Source verification:
- `simulator.py` §249: `if action != Action.NO_ACTION and nxt is not None:` (only executes if next bar exists).
- `simulator.py` §274-275: `elif action != Action.NO_ACTION: action = Action.NO_ACTION` (last bar has no executable fill).
- `run_exp004.py` §249: same `nxt is not None` guard; `action` reset to `NO_ACTION` for last bar implicitly via `exec_ts = None`.
- Fill uses `opens[nxt]` (`simulator.py` §257; `run_exp004.py` §218, §238).
- No same-bar fill: no reference to `closes[i]` or `opens[i]` for execution.

Status: **MATCH**
Notes: Last bar (`i == n-1`) produces `nxt = None`; action forced to `NO_ACTION`; no fill possible.

---

## RiskKernel authority

Source (`simulator.py` §255-273; `run_exp004.py` §203-246):
- Every `ENTER_LONG` proposal passes through `risk.evaluate()`.
- Every `EXIT` proposal passes through `risk.evaluate()`.
- `allowed` must be True before any position change or fee is applied.
- If `allowed == False`: `action` reset to `NO_ACTION`; `fill_px = 0.0`; `pos` unchanged; no fee added (`cum_fee` unchanged); no entry/exit counted.

Status: **MATCH**
Notes: RiskKernel evaluates entry (line 203-225) and exit (line 226-246) independently. No bypass path exists.

---

## A/B execution equality

Both A and B use the same:
- `run_arm()` function (`scripts/run_exp004.py` §90-301).
- `SimConfig` defaults (same `seed`, `capital_usd`, `size_btc`, `fee_mult`, `p_thr`).
- `RiskKernel` (`configs/risk.json`).
- `_fill_price()`, `_fee()` implementations.
- Funding logic (`funds[i]` change tracking).
- Position accounting (`PositionState`).
- Equity calculation.

Only intended difference:
- A (`run_exp004.py` §176-179): `action = Action.ENTER_LONG` directly (no `policy.decide()` call).
- B (`run_exp004.py` §181-200): `action` assigned from `policy.decide()` result.
- A does NOT use `min_edge_over_cost` (set to `0.0` for base comparison in EXP-004R framework).
- B uses `min_edge_over_cost = 2.0` (explicit, documented).

Status: **MATCH**
Notes: The framework repair (`position_state_machine.md`) ensures both arms share the same exit branch (`NO_ACTION` + holding → EXIT proposal). Before repair, A had no exit mechanism; after repair, the mechanism exists identically for both.

---

## Source-vs-equation reconciliation summary

| Component | Source reference | Equation reference | Status |
|---|---|---|---|
| Fill price (buy) | simulator.py §82-83 | `open_next * (1 + slippage_pct/100)` | MATCH |
| Fill price (sell) | simulator.py §82-83 | `open_next * (1 - slippage_pct/100)` | MATCH |
| Slippage amount | simulator.py §82 | `open_next * 0.0002` | MATCH |
| Fee | simulator.py §86-87; config/costs.json | `abs(notional) * 0.0005` | MATCH |
| Funding | simulator.py §230-231; run_exp004.py §124-126 | `-side*qty*close*rate` (on change) | MATCH |
| Gross PnL | simulator.py §265 | `side*qty*(fill_px - entry_px)` | MATCH |
| Net PnL | simulator.py §221-223; run_exp004.py §252 | `capital + realized + unrealized - fees - funding` | MATCH |
| Equity | simulator.py §221-223 | same as net + unrealized | MATCH |
| Exposure | simulator.py §311; run_exp004.py §296 | `holding_bars / total_bars` | MATCH |
| Next-bar fill | simulator.py §249, §274-275 | `open[t+1]` only; none at last bar | MATCH |
| Risk authority | simulator.py §255-273; risk/kernel.py | `allowed` gate for every proposal | MATCH |
| Position transition | simulator.py §258-262 (enter), §263-270 (exit) | FLAT→LONG→FLAT | MATCH |
| A/B mechanics equality | config.yaml + arm_A.jsonl / arm_B.jsonl | Only policy layer differs | MATCH |

# Phase 7A — Economic Target Pipeline Audit

## 1. Chain verification (evidence, not assumption)

| Component | Source file | Evidence | Status |
|---|---|---|---|
| Raw forward return 15m | `src/jev_trading/labels/engine.py:52` | `fr15 = close.shift(-15) / close - 1` | Confirmed: point-in-time, uses only future bars |
| Training binary target | `src/jev_trading/quant/train.py:60` | `(j[LABEL_COL] > THRESHOLD).cast(pl.Int8)`; `THRESHOLD = 0.0014`; `LABEL_COL = "future_return_15m"` | Confirmed: derived from `fr15` |
| Training regression target | `src/jev_trading/quant/train.py:158` | `reg_target = jtr[LABEL_COL].to_numpy()` (same `future_return_15m`) | Confirmed: same raw column used |
| Probability inference | `src/jev_trading/quant/model.py:26-42` | `predict_proba` preferred; `Booster.predict()` fallback only for raw Booster; `np.clip(..., 0.0, 1.0)` enforced | Confirmed: continuous [0,1] |
| Expected return inference | `src/jev_trading/quant/model.py:50-60` | `predict_expected_return_15()` uses `reg_model.predict(mat)`; fallback uses `p_up * 0.003 - (1-p_up) * 0.002` when no regression model loaded | Confirmed: regression preferred when available |
| Economic output schema | `src/jev_trading/quant/economic.py` | `EconomicQuantOutput` contains `p_up_15`, `expected_return_15`, `expected_downside_15`, `expected_favorable/adverse_excursion_15`, `uncertainty`, `horizon_min` | Confirmed: common schema defined |
| Cost model (base) | `configs/costs.json` | `{"taker_fee_pct": 0.05, "slippage_pct": 0.02}` | Confirmed |
| Cost derivation (label engine) | `labels/engine.py:29` | `2 * (taker_fee_pct + slippage_pct) / 100` = `0.0014` | Confirmed: single source |
| Cost derivation (simulator) | `simulator.py:86-87` | `_fee()` = `abs(notional) * 0.05 / 100 * fee_mult` | Confirmed: aligns with config |
| Cost derivation (economic layer) | `quant/economic.py:74-80` | `_derive_round_trip_cost()` reads same `configs/costs.json`; applies `fee_mult` | Confirmed: single source |
| Round-trip cost consistency | `train.py`, `engine.py`, `simulator.py`, `economic.py` | All derive or reference `0.0014` (or `2*(0.05+0.02)/100`) | Confirmed: no divergence |
| Funding treatment (labels) | `labels/engine.py` | `funding_rate` column preserved in input bars; label engine does not compute funding cost (execution layer does) | Confirmed: funding rate available for economic evaluation |
| Funding treatment (simulator) | `simulator.py:230-231` | `cum_fund += -pos.side * qty * close[i] * funds[i]` when rate changes while holding | Confirmed: applied per-hold on print change |
| Funding treatment (economic layer) | `quant/economic.py:129-132` | Funding cost = `funding_rate * hold_bars_estimate / 100` (approximation) | Confirmed: approximate, documented, configurable (`funding_rate`, `hold_bars_estimate` parameters) |
| Execution economics (simulator) | `simulator.py:80-83`, `256-272` | Next-bar fill (`open[i+1]`), slippage applied to fill price, fee applied to notional, exit computes `gross - fee`, realized PnL updates equity | Confirmed |
| Execution economics (economic layer) | `quant/economic.py:138-146` | `expected_net_edge = expected_gross_return - fees - slippage - funding - delay_penalty - other`; `min_edge_mult` applied (`default 2.0` from `configs/policy.json`) | Confirmed: deterministic, exposed |
| Timestamp alignment (features) | `state/features.py` (referenced by `build_features`) | Features built from bars `0..t` only; no future keys | Confirmed: point-in-time contract enforced by adapter (`frontier/laya.py`) and simulator |
| Timestamp alignment (labels) | `labels/engine.py` | `shift(-h)` uses only future indices; null at tail where full window doesn't fit | Confirmed: no leakage |
| Notional basis (simulator) | `simulator.py` | `fill_qty = rd.approved_size_btc`; fee = `fill_qty * fill_px * 0.05 / 100` (fraction of notional) | Confirmed: fraction of notional |
| Notional basis (economic layer) | `quant/economic.py` | Fees/slippage expressed as fractions (`/ 100`); funding as fraction (`funding_rate / 100`); no fixed-dollar amounts | Confirmed: consistent with fraction-based economics |
| Sign convention (long) | `simulator.py` | `ENTER_LONG` (`side > 0`); exit computes `gross = qty * (fill_px - entry_price)`; positive = profit | Confirmed: standard long convention |
| Sign convention (short) | `simulator.py` | `ENTER_SHORT` (`side < 0`); exit computes `gross = qty * (fill_px - entry_price)` with negative quantity; sign preserved by `pos.side` | Confirmed: mirror logic preserved |
| Economic output audit trail | `quant/economic.py:146-151` | `decision_reasons` includes `direction`, `edge`, `risk`, `execution`, `FINAL` with explicit pass/fail | Confirmed: full audit trail |

## 2. Findings (no silent bugs)

- **No divergence between label cost (`0.0014`) and simulator cost (`_fee()` with `fee_mult=1` → same `0.0014` base).**
- **No divergence between label funding rate (`funding_rate` column) and simulator funding (`funds[i]`).**
- **No divergence between label target (`future_return_15m`) and economic prediction input (`expected_return_15` from `reg_model` trained on `future_return_15m`).**
- **No sign error**: `expected_return_15` predicts the raw future return; positive = upside, negative = downside; economic layer treats positive gross as potential long, negative gross as potential short (if short arm enabled). Short arm currently limited by policy (`ENTER_SHORT` requires `p_dn_15` and `expected_return_15` negative); economic layer supports both sides.
- **Approximation noted**: Funding cost in economic layer uses `funding_rate * hold_bars_estimate / 100` (approximate) rather than exact per-bar accumulation. This is documented in `evaluate_economic_opportunity()` docstring and `metrics_full_report.md`.
- **Approximation noted**: Excursion estimates (`expected_favorable/adverse_excursion_15`) use fixed approximations (`abs(exp_ret) + 0.001`) rather than dedicated excursion models (`mfe_30`, `mae_30`). Upgrade path: train separate regression heads on `mfe_30` / `mae_30`.

## 3. Consistency check result

`future_return_15` (label) → `expected_return_15` (regression prediction) → `expected_net_edge` (economic evaluation) → simulator PnL (`net_pnl` in `simulator.py`) forms a consistent chain. No hidden threshold shifts, no unrecorded cost components, no future information leakage.

## 4. Evidence artifacts

- This audit file: `docs/phase7/7A_target_validation.md`
- Label engine: `src/jev_trading/labels/engine.py`
- Quant model / economic layer: `src/jev_trading/quant/model.py`, `quant/economic.py`
- Cost config: `configs/costs.json`
- Policy config (min_edge_mult): `configs/policy.json`
- Experiment artifacts (frozen): `experiments/EXP-004/`, `experiments/EXP-006/`

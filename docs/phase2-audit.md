# Phase 2 implementation audit — Economic Quant Engine v2

Audit date: current committed baseline `67be354` (`Freeze baseline and add economic target gate`).
The audit is read-only with respect to runtime code; pre-existing untracked `.local/` and
`docs/pre-jev/` content was not modified.

## Current architecture actually present

```text
bars → backward-only features → binary LightGBM p_up_15 / complementary p_dn_15
     → optional 15m return regressor → threshold policy → deterministic risk
     → next-open event simulator
```

Frontier/Laya/Jev remain isolated research components. They are not on the promoted
quant path and will not be modified in Phase 2.

## Data and boundaries

- Source convention: BTCUSDT perpetual 1-minute bars.
- Current pre-OOS research slice used by the frozen baseline: `[2021-01-01, 2025-01-01)`.
- `configs/split.json` records train 2021–2023, validation 2024, test 2025.
- Historical experiments already consumed 2025 OOS. It is not a fresh holdout and
  must not be used for Phase 2 selection.
- Phase 2 experiments will use pre-2025 data only. Any final promotion requires a
  genuinely untouched future period.

## Current features

`state/features.py` exposes 16 features:

- returns: 1m, 5m, 15m, 60m;
- realized volatility: 5m, 30m;
- ATR14;
- EMA20, EMA50, EMA200;
- saturated `trend_score`;
- funding and funding z-score;
- one-day open-interest change;
- volatility regime;
- volume z-score.

There is no basis, spread, order-book, aggressive-flow, liquidation, or other
microstructure field. The existing `trend_score` is explicitly a clipped proxy and
may discard information; feature-family ablations are required before adding
anything.

## Current labels

`labels/engine.py` already provides point-in-time:

- `future_return_5m/15m/30m/60m`;
- signed forward MFE/MAE for each horizon;
- `y_up_15`/`y_dn_15` and other direction flags;
- explicit first-touch TP/SL labels and offsets when a barrier pair is selected.

Windows are `t+1..t+H`, missing windows are null, and the current entry reference
is `close[t]`. The return target is a close-to-close label; execution research still
uses next-bar open plus side slippage. This price mismatch must be recorded rather
than silently treated as identical.

## Current models

- `train_models()` fits one LightGBM binary classifier for `y_up_15` and one
  LightGBM regressor for `future_return_15m`.
- `QuantPredictor.predict_dn15()` currently returns `1 - p_up15`; it is not an
  independently trained downside/flat model.
- `predict_economic_output()` intentionally raises because dedicated excursion,
  uncertainty, and holding-time heads do not exist.
- No model artifact currently produces a complete `EconomicQuantOutput`.

## Economic gate and simulator

- `EconomicQuantOutput` and `evaluate_economic_opportunity()` calculate
  fee + slippage + scaled funding + delay/other costs, expected net edge, and
  `NO_TRADE` as a first-class result.
- Missing excursion, uncertainty, downside, or holding-time fields fail closed.
- Costs are configured as 0.05% taker fee and 0.02% slippage per side; funding is
  a per-eight-hour fraction. There is no spread or basis model in the current data.
- The event simulator fills a decision at the next available bar open with side
  slippage, charges fees/funding, and sends every proposal through the deterministic
  risk kernel. It currently consumes a probability threshold arm; it does not yet
  consume a complete V2 economic output.
- The policy currently has no active position-economic output path for the V2
  heads. Phase 2 will test economic outputs in a research adapter first and will not
  route them into live execution.

## Experiment methodology

Existing frozen experiments use chronological splits, explicit provenance, and
separate ablation artifacts. `EXP-009-economic-targets` is a prior validation-only
measurement and ended `STOP`: excursion heads had conditional predictability, but
return heads did not clear the cost hurdle. That result is historical context, not
a Phase 2 model selection shortcut.

## Phase 2 implementation boundary

Implement and evaluate, in order:

1. explicit multiclass direction probabilities (`P(up)`, `P(flat)`, `P(down)`);
2. real 5m/15m/30m/60m return heads;
3. real 15m/60m MFE/MAE heads;
4. a documented uncertainty estimate;
5. controlled feature-family ablations;
6. validation, walk-forward, regime, and cost-stress reports.

No Laya, Frontier, Jev, RL, live execution, or new data source will be integrated.
If the heads do not produce repeatable positive net edge after costs, the required
outcome is `NO EDGE`, not a higher-level allocator.

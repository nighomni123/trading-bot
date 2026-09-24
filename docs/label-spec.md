# Label spec (Phase 1 contract)

All labels at row `t` are point-in-time outcomes. The forward window is exactly
`t+1..t+H`; row `t` never uses its own high/low for an excursion, and a partial
window is null rather than zero.

## Returns and excursions

For each `H ∈ {5, 15, 30, 60}` minutes:

- `future_return_Hm = close[t+H] / close[t] - 1`
- `future_max_return_Hm = max(high[t+1..t+H]) / close[t] - 1`
- `future_min_return_Hm = min(low[t+1..t+H]) / close[t] - 1`
- `mfe_Hm` and `mae_Hm` are explicit aliases of the signed max/min excursion.

These are long-side signed values. A short-side excursion is the corresponding
inverse/sign transform at consumption time. Historical `mfe_30`/`mae_30` aliases
remain available.

The baseline label is preserved as `y_up_15 = 1[future_return_15m >= threshold]`
and `y_dn_15 = 1[future_return_15m <= -threshold]`. Equivalent direction labels
are available for 5m, 30m, and 60m. The threshold remains the cost-derived
`0.0014` unless an experiment explicitly locks another validation-only value.

## Path-dependent outcomes

`compute_path_labels(bars, tp_threshold, sl_threshold, horizon=60)` returns one
row per timestamp for one explicitly selected pair:

- `time_to_tp`: first offset where `high >= close[t] * (1 + tp_threshold)`;
- `time_to_sl`: first offset where `low <= close[t] * (1 - sl_threshold)`;
- `tp_before_sl`: `1` when TP is touched first, `0` when SL is touched first,
  and null when neither barrier is touched;
- `timeout`: `1` when neither barrier is touched, `0` otherwise.

Both barriers touching in the same bar is conservatively classified as SL-first
(`tp_before_sl = 0`). The experiment grid is 10/15/20/30 bps for both TP and SL;
`compute_labels(..., path_pairs=...)` materializes only the pairs an experiment
has locked, avoiding an accidental 16× wide frame on millions of rows.

## Validity and causality

- Rows with fewer than `H` future bars are null for every derived label.
- Label code reads only `close`, `high`, `low`, horizon, and thresholds; never
  features, quant output, execution results, or future PnL.
- Any model using a 30m/60m target must purge/embargo at least that target's
  horizon at chronological boundaries.
- Economic predictions must come from trained heads. Missing heads return an
  explicit error/NO_TRADE; probability-weighted or fixed excursion fallbacks are
  not valid economic evidence.

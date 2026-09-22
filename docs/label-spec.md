# Label spec (P3 contract)

## Raw future columns (computed from `close` only, strictly `t+1..t+H`)
With horizon `H = 15` bars:
- `future_return_15m = close[t+H] / close[t] - 1`
- `future_max_return_15m = max(high[t+1..t+H]) / close[t] - 1`
- `future_min_return_15m = min(low[t+1..t+H]) / close[t] - 1`

Raw returns are kept because "did it move?" and "was it enough to beat costs?" are
different questions; max/min support cost/regret analysis later.

## Derived labels
- `y_up_15 = future_return_15m >= threshold`
- `y_dn_15 = future_return_15m <= -threshold`

## Thresholds (cost-aware, never test-optimized)
`threshold = estimated_fee + estimated_slippage + safety_margin`, recorded with its
provenance in the experiment file. Probe `{0.10%, 0.15%, 0.25%, 0.50%}` on validation
only; the test threshold is frozen before P4.

## Validity rules
- Rows `t` with fewer than `H` future bars are null (no partial windows).
- Label code takes only `close/high/low` series + `H` + `threshold` — never features,
  never quant output.
- Gate before P4: label distribution, class balance, future-return histogram, and
  threshold-sensitivity table on real data.

# Execution semantics (normative for P7/P8)

One rule makes the backtest trustworthy: **no same-bar fills**.

## Timestamps
- Bar `t` covers `[t, t+60s)`. All P2 features on row `t` use bars `0..t` only.
- `information timestamp = t`, `decision timestamp = t`,
  `earliest execution = t + latency`.

## MVP rule: next-bar execution
```text
features(bar t) → decision at close[t] → execution at open[t+1] + slippage
```
- Filling at `close[t]` is forbidden: the decision already knows that close (lookahead).
- `open[t+1]` is the first price the decision could not have seen.
- Slippage (bps, `configs/costs.json`) is applied against the side: buys fill higher, sells lower.

## Later (not MVP)
Intrabar execution: decision at `close[t]` → next trade/book event → fill. Requires
tick/book data we don't store yet; do not model it with 1m bars.

## Simulator checklist
- [ ] Order emitted at `t` is fillable only at `≥ open[t+1]`.
- [ ] Test: shifting all decisions one bar later changes fills; same-bar fill path absent.
- [ ] Latency/funding applied before PnL; every fill lands in the JSONL event log.

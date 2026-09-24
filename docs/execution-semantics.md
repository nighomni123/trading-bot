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

## Replay / offline harness (Phase 5 stub)
- `scripts/replay_laya.py`: historical state window → `LayaDecision` artifacts (stub heuristic). Not a live loop; produces JSONL for measuring incremental value (Quant vs Quant+Policy vs Quant+Policy+Laya). Real Laya inference deferred.
- See `frontier/laya.py` (typed schemas) and `frontier/router.py` (stub router).

## Canonical event log (schema v3)
- Each bar has one decision snapshot. `ts` is its decision timestamp and `exec_ts`, when present, is the earliest eligible next-bar timestamp and is strictly later.
- A fill from the prior decision is recorded on the current bar as `executed`, with `fill_decision_ts < ts`, price, quantity, fee, and the post-fill position/equity snapshot. The actual fill time is that record's `ts`.
- A completed log has a footer containing the record count and last entry hash. `verify_log()` rejects missing/tampered records, invalid timing, and incomplete logs.

## Later (not MVP)
Intrabar execution: decision at `close[t]` → next trade/book event → fill. Requires
tick/book data we don't store yet; do not model it with 1m bars.

## Simulator checklist
- [x] Order emitted at `t` is fillable only at `≥ open[t+1]`.
- [x] Tests assert `exec_ts > ts` and `fill_decision_ts < fill ts`; same-bar fill path is absent.
- [x] Funding, fees, slippage, and position state are applied before PnL and logged.
- [x] `verify_log()` validates schema v3, sequence, hashes, timing, and completion footer.

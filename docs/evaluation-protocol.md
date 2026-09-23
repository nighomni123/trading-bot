# Evaluation protocol (P4 + P7)

## Splits: chronological + purge/embargo
Labels with horizon `H = 15m` overlap across adjacent rows, so plain train/valid/test
boundaries leak. Between every split insert **purge/embargo ≥ H** (drop or gap ≥ 15m of
bars at each boundary). No shuffling, ever.

## P4 baselines (in order; stop early if nothing beats dumb)
- A naïve: always predict class prior.
- B momentum: `ret_5m > 0 → up else down`.
- C trend: `EMA20 > EMA50 → bullish`.
- D logistic regression on FEATURE_COLUMNS.
- E LightGBM only if D shows OOS signal over A–C.
- Metrics OOS: log-loss, AUC, calibration curve. Research question: "does ML add
  information?" — "no edge" is a valid verdict that stops the pipeline.

## P7 ablation arms (identical data/costs/splits; only the decision stack varies)
- A random control (seeded). B quant-probability threshold rule. C quant → policy → risk.
  D quant → mock-Jev → policy → risk. E Jev-disabled policy (same conditions as D).
- Jev incremental contribution = `Perf(D) − Perf(C)` on: precision, calibration,
  drawdown, cost-adjusted return, regime robustness.
- Architecture update (audit/replan): Frontier/Laya layer is a strategy allocator (not BUY/SELL). Replay/offline harness (`scripts/replay_laya.py`) produces `LayaDecision` artifacts without real Laya call; real inference deferred until Phase 5 specialist evidence justifies it. See `frontier/laya.py` and `frontier/router.py`.
- Cost gates: base costs, then hostile 2–3× fee multiplier. Survive hostile before P8.
- Position context for exits: `PositionState(side, quantity, entry_price, unrealized_pnl,
  realized_pnl, time_in_position)` joins market+quant+Jev state in the policy input.

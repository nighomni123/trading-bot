# Implementation Plan — Pluggable BacktestEngine + LEAN adapter
# Ponytail: only add abstraction + adapter; do not rewrite simulator or Jev.

1. Existing entry point: backtest/simulator.py (simulator.run / prepare).
2. Existing result/trade schemas: simulator uses JSONL event log + PositionState; no canonical BacktestResult exists yet.
3. Files changed / added:
   - backtest/base.py (NEW: ABC + BacktestResult + Trade + DecisionEvent)
   - backtest/local.py (NEW: adapter around simulator)
   - backtest/lean.py (NEW: optional adapter; graceful if LEAN absent)
   - backtest/comparison.py (NEW: compare + reconciliation)
   - backtest/data_adapter.py (NEW: canonical -> LEAN CSV)
   - backtest/cli.py (NEW: python -m jev_trading.backtest)
   - experiments/EXP-LEAN-001/ (NEW: baseline comparison)
   - experiments/EXP-LEAN-002/ (NEW: Jev replay comparison)
   - tests/test_backtest_engine.py (NEW)
   - README.md, docs/ (UPDATED)
4. LEAN boundary: only lean.py knows LEAN specifics; rest uses BacktestEngine API.
5. Dependencies: optional requirements-lean.txt (quantconnect.lean); base unchanged.
6. EXP-LEAN-001 flow: run baseline strategy through Local then Lean adapter; compare results.
7. EXP-LEAN-002 flow: same Quant+Policy+Jev+Risk inputs, cached Jev answers, compare outputs.

Discrepancies noted:
- Simulator uses next-bar execution (open[t+1]); must preserve in LEAN adapter.
- Simulator event log is JSONL per bar; BacktestResult normalizes to structured model.
- No existing canonical BacktestResult; created new one (no duplication with existing format because none existed at package level).
- Jev interface (jev.adapter / mock) unchanged; backtest engine is infrastructure only.

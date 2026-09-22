# jev-trading

BTC-perp paper-trading MVP. Six-layer fast loop from Conversation 1 of
`chatgpt-conversations.md`, with Jev behind a mock interface (TypeSafe API
swappable post-MVP) and Laya fine-tuning deferred.

```
Live feed → State engine → Quant engine → Jev(mock) → Policy → Risk kernel → Paper execution
```

## Principles (from the conversations)

- The risk kernel is deterministic and has absolute authority; no model can override it.
- No PnL-driven live model updates. Chronological train/valid/test only — no leakage.
- Every live decision is logged to a replayable JSONL event log.
- Gates: baselines must survive hostile costs before live paper; edge verdict may be "no edge".

## Status

Phases tracked in the implementation plan: P0 bootstrap → P9 frontier + P8 shadow live-verified.

## Dev

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

## Quick start (end-to-end)

### 0. Dashboard

```bash
.venv/bin/pip install -e ".[ui]"   # adds streamlit (optional, for dashboard)
.venv/bin/streamlit run scripts/dashboard.py
```

### 1. Fetch data

```bash
.venv/bin/python scripts/fetch_data.py        # BTC perp bars → data/btcusdt_1m.parquet
.venv/bin/python scripts/fetch_pm.py          # Polymarket/Kalshi sentiment → data/pm_history.parquet
```

### 2. Train the quant model

```bash
.venv/bin/python scripts/train_quant.py       # LightGBM on 2021–2023, validate 2024 → models/
```

### 3. Run an experiment (ablation)

```bash
# Base-cost ablation on valid 2024 (test period stays frozen)
.venv/bin/python scripts/run_ablation.py --start 2024-01-01 --end 2025-01-01 \
  --arms random,threshold,policy,full,nojev --fee-mult 1.0

# Hostile-cost gate (2x/3x fees)
.venv/bin/python scripts/run_ablation.py --start 2024-01-01 --end 2025-01-01 \
  --arms threshold,full --fee-mult 2.0,3.0
```

Results print as JSON to stdout and are written to `experiments/EXP-xxx/experiment.yaml`.

### 4. Paper trading on live data (P8 shadow)

```bash
# Zero-real-money shadow trading on live Binance 1m BTCUSDT perp:
#   Binance -> state -> quant(LGBM) -> jev(mock) -> policy -> risk -> paper exec -> JSONL
.venv/bin/python scripts/paper_trade.py --capital 10000 --size-btc 0.01 \
    --p-thr 0.40 --fee-mult 1.0 --duration 0     # Ctrl+C to stop

# Short run for testing:
.venv/bin/python scripts/paper_trade.py --duration 120 --poll-interval 30
```

Event logs (replayable via `simulator.verify_log`) land in `events/shadow_*.jsonl`.

### 5. Frontier strategist (P9)

```bash
# The frontier layer analyzes regime + calibration + PnL and proposes parameter artifacts.
# See: frontier/world_model.py, frontier/strategy.py, frontier/overseer.py, frontier/guardrail.py
# Artifacts are versioned under experiments/EXP-xxx/frontier_strategy.yaml
# and tested as new EXP experiments — never applied to live configs directly.
```

### 6. Run tests

```bash
.venv/bin/python -m pytest -q                     # all tests
.venv/bin/python -m pytest tests/test_frontier.py -v  # frontier only
```

Data lands in `data/` (gitignored), models in `models/`, event logs in `events/`
(both `p7_*.jsonl` backtest logs and `shadow_*.jsonl` live paper-trading logs).
Experiment artifacts in `experiments/EXP-xxx/`.

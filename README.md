# jev-trading

BTC-perp paper-trading MVP. Six-layer fast loop from Conversation 1 of
`chatgpt-conversations.md`, with Jev behind a mock interface (TypeSafe API
swappable post-MVP) and Laya fine-tuning deferred.

```
Market Data → State/Features → Quant Specialists → Expected Return/Edge/Uncertainty → Frontier/Laya (stub) → StrategyProfile → Deterministic Policy → Risk Kernel → Paper Execution
```
Laya integration deferred (Phase 5); replay/offline harness available: `scripts/replay_laya.py`.

## Principles (from the conversations)

- The risk kernel is deterministic and has absolute authority; no model can override it.
- No PnL-driven live model updates. Chronological train/valid/test only — no leakage.
- Every live decision is logged to a replayable JSONL event log.
- Gates: baselines must survive hostile costs before live paper; edge verdict may be "no edge".

## Backtest engines (pluggable)

`BacktestEngine` abstraction (`backtest/base.py`) allows the same strategy to run through multiple implementations:
- `LocalBacktestEngine`: existing simulator (`simulator.py`), primary research engine.
- `LeanBacktestEngine`: optional QuantConnect LEAN adapter; graceful stub if LEAN not installed.
- `both` mode compares results to investigate differences (not to hide them).

```bash
.venv/bin/python -m jev_trading.backtest --engine local --strategy threshold
.venv/bin/python -m jev_trading.backtest --engine lean --strategy threshold
.venv/bin/python -m jev_trading.backtest --engine both --experiment-id EXP-LEAN-001
```

Installation for LEAN (optional): `pip install -r requirements-lean.txt`; see official LEAN docs for binary setup.

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

### 6. Replay / offline Laya harness (Phase 5 stub)

```bash
# Offline replay: historical state → LayaDecision (stub) → JSONL artifacts
# No real Laya call; deterministic heuristic for incremental-value measurement
.venv/bin/python scripts/replay_laya.py --input results/state_window.jsonl --output results/laya_decisions.jsonl
```

Schemas: `frontier/laya.py` (LayaDecision, StrategyProfile, FrontierState); `frontier/router.py` (stub router). Upgrade path: replace stub with real typed-decision inference when specialist layer justifies it.

### 7. Frontier strategist (P9)

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

> **Clone with LFS (seamless)**: This repo uses Git LFS for large `.jsonl` experiment files (`experiments/EXP-*/arm_*.jsonl`, ~200 MB each). To clone and get full files automatically, install [`git-lfs`](https://git-lfs.com) first (`brew install git-lfs` or download), run `git lfs install` once globally, then clone: `git clone https://github.com/nighomni123/trading-bot.git`. Without LFS configured, clone only downloads small pointer files (3 lines) instead of the real data.

# JEV / trading-bot

JEV is a **research-first, paper/shadow-only market-intelligence system** for
BTCUSDT perpetual futures. This repository is not a live-money trading system
and makes no profitability claim.

## Architecture

```text
                     LIVE MARKET DATA
                            │
                            ▼
                   MARKET DATA FABRIC
                            │
                            ▼
                   MARKET ENVIRONMENT
                            │
                            ▼
                      FRONTIER AI
                 (strategist/researcher)
                            │
                 ┌──────────┴──────────┐
                 ▼                     ▼
              QUANT TOOLS          STRATEGY
                 │                     │
                 └──────────┬──────────┘
                            ▼
                    CANDIDATE TRADE
                            │
                            ▼
                      JEV (bounded)
                            │
                            ▼
                   POLICY FINALIZER
                            │
                            ▼
                    RISK KERNEL
                            │
                            ▼
                    PAPER EXECUTION
                            │
                            ▼
                      TRADE LEDGER
                            │
                            ▼
                   RESEARCH MEMORY
                            │
                            └──────────→ FRONTIER
```

- **Data fabric:** preserves source, venue, instrument, market type, event time,
  receive time, and sequence identity. It does not average incompatible prices.
- **Market environment:** builds causal 1m/5m/15m/1h/4h state, volatility,
  structure, flow, derivatives, liquidity, position, and data-quality fields.
- **Quant:** callable analytical instruments, not one return predictor. Tools
  report evidence, limitations, and sample size; they cannot create orders.
- **Frontier:** a slow strategist/researcher. It may generate hypotheses and
  bounded questions, but has no sizing, leverage, order, or risk authority.
- **Jev:** a short-validity evaluator for a supplied candidate. Its output is
  evidence to policy, never an order.
- **Policy:** deterministic translation of validated evidence into an action.
- **Risk:** independent deterministic authority. A rejection cannot be
  overridden by confidence or an AI response.
- **Execution:** paper-only. There is no broker/live-order adapter.
- **Research memory:** structured decisions are canonical; Markdown reports are
  generated views and are immutable once written.

## Current status

The active reset is an integration skeleton under
`src/jev_trading/live_intelligence/`. It is not yet a completed multi-week
shadow experiment. The previous predictive architecture remains preserved in
`legacy-main-phase2-phase3`; see [`docs/legacy-research.md`](docs/legacy-research.md).
Phase 2 and Phase 3 remain **NO EDGE** historical results.

## Install and verify

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q
```

The historical suite is large because several tests read Git-LFS experiment
logs. A fresh clone without Git LFS may not be able to run those tests.

## Configuration

Runtime settings are in [`configs/live.json`](configs/live.json). Secrets are
read only from environment variables named by the provider configuration; no
credentials belong in source or JSON. Frontier and Jev providers default to
`disabled`, which fails closed.

The default execution mode is PAPER. The CLI makes this visible:

```bash
.venv/bin/python -m jev_trading.live_intelligence status
```

Expected output includes:

```text
EXECUTION MODE: PAPER
LIVE ORDERS: DISABLED
```

## Run paper/shadow mode

The public Binance adapter requires network access but no API key:

```bash
.venv/bin/python -m jev_trading.live_intelligence paper \
  --iterations 1 --ledger research/runtime/ledger/decisions.jsonl
```

With providers disabled, the runner remains fail-closed and should not enter a
trade. Configure an explicit OpenAI-compatible Frontier/Jev deployment and
credentials before attempting a meaningful shadow run. The system never enables
live orders through configuration.

## Inspect state and research memory

```bash
.venv/bin/python -m jev_trading.live_intelligence status
wc -l research/runtime/ledger/*.jsonl
find research/hourly research/daily research/weekly -type f
```

The ledger is append-only and hash-chained. Generated reports are written with
exclusive creation, so a correction must be a new artifact rather than an
in-place rewrite.

## Replay

```bash
.venv/bin/python -m jev_trading.replay --help
```

Replay consumes a recorded ledger and verifies its chain without calling
Frontier, Jev, or an exchange.

## Legacy research

Backtesting remains available as a sanity-check, hypothesis-screening, and
replay tool. It is not the new architecture's primary alpha mechanism. The
historical predictive model is not connected to the active runner. Laya and
reinforcement learning remain deferred.

## Safety limitations

- A missing, stale, malformed, or incomplete input defaults to no trade.
- A candidate requires empirical path/economic evidence; the initial live
  analyzers do not fabricate probabilities.
- Historical spot data must not be silently treated as perpetual execution
  data; source and market type remain explicit.
- The current code has no authenticated exchange or live-money execution path.
- Several-week shadow observation and controlled ablations are still required
  before drawing any economic conclusion.

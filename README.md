# JEV Trading Bot

JEV is a research-first, **paper/shadow-only BTCUSDT perpetual market-intelligence system**. It is not a live-money execution system, has no guaranteed profitability or alpha claim, and does not autonomously modify itself.

## Current architecture

```text
LIVE MARKET DATA
      ↓
SOURCE-AWARE DATA FABRIC
      ↓
CAUSAL MARKET ENVIRONMENT
      ↓
QUANT EVIDENCE TOOLKIT
      ↓
FRONTIER STRATEGIST
      ↓
JEV BOUNDED EVALUATOR
      ↓
DETERMINISTIC POLICY
      ↓
DETERMINISTIC RISK
      ↓
NEXT-OPEN PAPER EXECUTION
      ↓
HASH-CHAINED TRADE/DECISION LEDGER
      ↓
STRUCTURED RESEARCH MEMORY
      ↺ read-only context to Frontier
```

### Component boundaries

- **Data fabric:** preserves source, venue, instrument, market type, event time, receive time, and sequence identity. Freshness is event-time based. Duplicate, incoherent, gapped, incomplete, or unsafe data fails closed.
- **Market environment:** builds completed, causal 1m/5m/15m/1h/4h observations. It carries price, structure, volatility, flow, liquidity, derivatives, events, position, and data quality.
- **Quant:** a toolkit of typed analytical instruments, not a single trained return predictor. Path samples carry their exact target/stop/horizon identity; economics include fees, slippage, latency, and visible funding.
- **Frontier:** an OpenAI-compatible, slow strategist. It receives bounded environment/Quant/research context and returns a validated `StrategyHypothesis`. It cannot size, leverage, execute, or override risk.
- **Jev:** an OpenAI-compatible, short-validity contextual evaluator. It receives the canonical `QuantEvidence`, candidate, position, and Frontier questions. ENTER responses must satisfy configured probability, entry-quality, failure-risk, and expiry gates.
- **Policy:** deterministic Python code. It turns validated evidence into `NO_TRADE`, `ENTER_LONG`, `ENTER_SHORT`, `HOLD`, `REDUCE`, or `EXIT`.
- **Risk:** independent deterministic authority. It maintains paper account/execution state, enforces configured kill switch, data health, sizing, daily loss, drawdown, order rate, cooldown, spread, slippage, liquidity, and position limits.
- **Execution:** PAPER only. Fills use the next one-minute open, side-aware slippage, complete fee/PnL accounting, deterministic visible funding, and explicit full-fill semantics. Venue partial fills are not claimed or silently simulated.
- **Ledger/recovery:** append-only hash chain, semantic intent/fill/trade links, atomic runtime checkpoints, and fail-closed startup restoration.
- **Research/replay:** structured observations and derived immutable reports; replay reconstructs recorded decisions and their versions without calling providers.

## Installation and verification

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q
```

The test suite includes historical regression tests and active live-intelligence behavioral tests.

## Configuration

Runtime settings are in `configs/live.json`. Never put API keys in JSON or source.

Provider settings support:

- `disabled`
- `replay`
- `openai_compatible`

For local OpenRouter configuration, copy the tracked template and create an ignored
`.env` at the repository root:

```bash
cp .env.example .env
chmod 600 .env
```

Then fill in:

```dotenv
OPENROUTER_API_KEY=your-key-here
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
FRONTIER_MODEL=provider/frontier-model
JEV_MODEL=provider/jev-model
```

The application loads the project-root `.env` automatically when the CLI or
`load_settings()` runs. Existing process environment variables take precedence
over values in `.env`; `.env` never overrides an already-exported variable.
Blank values, comments, `export KEY=value`, and single/double-quoted values are
supported. The loader never prints or persists the API key.

To enable OpenRouter, also set the provider mode in `configs/live.json`:

```json
"provider": {
  "provider": "openai_compatible",
  "base_url": "https://openrouter.ai/api/v1",
  "model": "configured-at-deployment",
  "api_key_env": "OPENROUTER_API_KEY"
}
```

`OPENROUTER_BASE_URL`, `FRONTIER_MODEL`, and `JEV_MODEL` are used when the JSON
model/base URL is left at its deployment placeholder. The default provider mode
is `disabled` and fails closed. Execution mode is always `PAPER`; live orders are
disabled.

## CLI

```bash
.venv/bin/python -m jev_trading.live_intelligence status

.venv/bin/python -m jev_trading.live_intelligence paper \
  --iterations 1 \
  --arm C \
  --ledger research/runtime/ledger/decisions.jsonl

.venv/bin/python -m jev_trading.live_intelligence replay \
  research/runtime/ledger/decisions.jsonl

.venv/bin/python -m jev_trading.live_intelligence research daily \
  --ledger research/runtime/ledger/decisions.jsonl \
  --root research
```

`--arm` selects a reproducible ablation:

- `A`: Quant + deterministic Policy + Risk
- `B`: Quant + Frontier + Policy + Risk
- `C`: Quant + Frontier + Jev + Policy + Risk

All arms use the same market data, execution, accounting, and ledger contracts. A is not a profitability claim.

## Public market-data smoke

The Binance Futures adapter uses public REST data for closed 1m bars, funding, OI, mark/index, and top-of-book depth. Missing or stale data is not replaced with favorable defaults. A paper run with providers disabled should remain flat.

## Research provenance

Every decision records content-addressed versions for Git commit, configuration, Frontier/Jev models and prompt hashes, Quant analyzers, policy/risk source, strategy registry, data schema, and experiment arm. Runtime state is checkpointed against the ledger hash.

## Historical boundary

The preserved `legacy-main-phase2-phase3` branch and `pre-live-intelligence-reset-2026-09-25` tag retain the pre-reset implementation. Historical Phase 2 and Phase 3 conclusions remain **NO EDGE**. Legacy predictive modules are not imported by the active runner.

## Safety limitations

- No authenticated exchange or live-money order path exists.
- Unknown market measurements remain unknown; they do not become zero.
- Frontier and Jev are advisory only and cannot bypass deterministic safety controls.
- Research memory is read-only context, not an autonomous self-modification channel.
- A multi-week shadow experiment should begin only after the current shadow-readiness report passes and OpenRouter credentials/provider smoke are available.

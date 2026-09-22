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

Phases tracked in the implementation plan: P0 bootstrap → P8 shadow report.

## Dev

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

Data lands in `data/` (gitignored), models in `models/`, event logs in `events/`.

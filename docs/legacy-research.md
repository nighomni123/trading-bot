# Legacy research baseline

The branch `legacy-main-phase2-phase3` preserves the complete pre-reset
implementation at commit `896908f`. The preservation tag
`pre-live-intelligence-reset-2026-09-25` points to the same commit.

The historical Phase 2 and Phase 3 conclusions are intentionally unchanged:

- **Phase 2: NO EDGE** under the tested 1-minute BTCUSDT perpetual formulation.
- **Phase 3: NO EDGE** after executable-entry barrier labels, corrected fill
  accounting, target-shape sweeps, walk-forward checks, and cost stress.

The active branch does not delete or reinterpret those experiments. The old
predictive model, labels, simulator, experiment artifacts, and audit documents
remain historical regression references. The new active code lives under
`src/jev_trading/live_intelligence/` and is explicitly paper/shadow-only.

The reset changes the research question: instead of treating one trained return
model as the system, it builds a source-aware environment, analytical Quant
instruments, a bounded Frontier strategist, a short-validity Jev evaluator,
deterministic policy, independent risk authority, paper execution, a structured
ledger, and immutable research memory. It makes no profitability claim.

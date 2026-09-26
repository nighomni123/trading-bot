"""Research memory exports.

Stage 12 long-horizon modules (data, targets, features, state, study, stability,
firewall, plots, cli) live as submodules of this package and are accessed by
their own module paths (e.g. jev_trading.research.targets). This __init__ must
keep re-exporting the memory/registry symbols the runtime imports.
"""
from .memory import ResearchMemory
from .registry import HypothesisRegistry, StrategyRegistry

__all__ = ["ResearchMemory", "HypothesisRegistry", "StrategyRegistry"]

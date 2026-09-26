"""Stage 12 target-leakage firewall.

Centralized, so a future target column is excluded automatically by prefix rather
than by a developer remembering its name. The Stage 11 incident (MFE/MAE/time_to
columns entering the model feature set) is the regression this prevents.
"""
from __future__ import annotations

import polars as pl

#: Every prefix that marks a column as target-derived.
LABEL_PREFIXES = (
    "target_",
    "forward_",
    "mfe_",
    "mae_",
    "time_to_",
    "future_",
)

#: Columns that are never model inputs regardless of name.
LABEL_EXACT = {
    "outcome", "target_first", "stop_first", "timeout",
    "entry_price", "target_price", "stop_price",
    "target_fraction", "stop_fraction",
}

#: Raw price/volume columns that are not predictive features by themselves.
RAW_PRICE_COLUMNS = {"open", "high", "low", "close", "volume"}


def is_label_column(name: str) -> bool:
    return name in LABEL_EXACT or name.startswith(LABEL_PREFIXES)


def select_model_features(frame: pl.DataFrame, *, exclude: set[str] | None = None) -> list[str]:
    """Numeric, non-label, non-raw-price columns only. Fails closed."""
    blocked = RAW_PRICE_COLUMNS | LABEL_EXACT | {"timestamp"} | (exclude or set())
    cols = [c for c in frame.columns if c not in blocked and not is_label_column(c)]
    return [c for c in cols if frame.schema[c].is_numeric()]

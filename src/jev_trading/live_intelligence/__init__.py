"""Active paper-only live market-intelligence architecture.

The frozen Phase 2/3 research modules remain under their historical packages.
This package contains the active data -> environment -> quant -> Frontier/Jev
-> policy -> risk -> paper execution -> ledger -> research loop.
"""
from .config import LiveSettings, load_settings

__all__ = ["LiveSettings", "load_settings"]

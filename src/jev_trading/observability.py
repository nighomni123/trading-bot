"""Structured component logging and lightweight metrics."""
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from pathlib import Path


class ComponentLogger:
    def __init__(self, path: str | Path, *, component_version: str = "live-intelligence-v1"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.component_version = component_version

    def log(self, component: str, event: str, **fields):
        row = {"component": component, "component_version": self.component_version, "event": event, **fields}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True, default=str, allow_nan=False) + "\n")

    @contextmanager
    def timed(self, component: str, event: str, **fields):
        started = time.perf_counter()
        try:
            yield
        finally:
            self.log(component, event, latency_ms=round((time.perf_counter() - started) * 1000, 3), **fields)

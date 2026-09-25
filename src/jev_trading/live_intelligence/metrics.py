"""Simple counters for calls, latency, decisions, and paper economics."""
from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path


class Metrics:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.counters: Counter[str] = Counter()
        self.latencies_ms: list[float] = []

    def increment(self, name: str, amount: int = 1) -> None:
        self.counters[name] += amount

    def observe_latency(self, name: str, latency_ms: float) -> None:
        self.counters[f"{name}_calls"] += 1
        self.latencies_ms.append(latency_ms)

    def snapshot(self) -> dict:
        result = dict(self.counters)
        if self.latencies_ms:
            result["latency_ms_mean"] = sum(self.latencies_ms) / len(self.latencies_ms)
            result["latency_ms_max"] = max(self.latencies_ms)
        return result

    def flush(self) -> None:
        self.path.write_text(json.dumps(self.snapshot(), sort_keys=True, indent=2) + "\n")

    def timed(self, name: str):
        return _Timer(self, name)


class _Timer:
    def __init__(self, metrics: Metrics, name: str):
        self.metrics = metrics
        self.name = name
        self.started = 0.0

    def __enter__(self):
        self.started = time.perf_counter()
        return self

    def __exit__(self, *_):
        self.metrics.observe_latency(self.name, (time.perf_counter() - self.started) * 1000)

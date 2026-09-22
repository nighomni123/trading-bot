"""Dashboard core logic — no UI framework dependency.

Provides functions to load experiment metadata, model metrics, frontier
artifacts, phase status, and run backtests. All Streamlit-free so the
logic is unit-testable.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import polars as pl

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

ROOT = Path(__file__).resolve().parents[3]

PHASES: list[tuple[str, str]] = [
    ("P0 Bootstrap", "DONE"),
    ("P1 Data pipeline", "DONE"),
    ("P2 State engine", "DONE"),
    ("P3 Labels (EXP-001)", "DONE (PASS)"),
    ("P4 Quant (EXP-002)", "DONE (PASS)"),
    ("P5 Jev + Policy", "MOCK COMPLETE"),
    ("P6 Risk kernel", "DONE"),
    ("P7 Simulator (EXP-003)", "GATE FAIL"),
    ("P8 Shadow", "BLOCKED (P7 gate)"),
    ("P9 Frontier Strategist", "DONE"),
]

GATES: dict[str, dict[str, str]] = {
    "P4": {
        "question": "does a probabilistic edge exist?",
        "verdict": "PASS",
        "detail": "LightGBM AUC 0.637 vs 0.50 prior (2024 OOS)",
    },
    "P7": {
        "question": "does the edge survive costs?",
        "verdict": "FAIL",
        "detail": "gross ~-$0.01/trade vs $0.10 costs; mock-Jev adds no selectivity",
    },
}


def _project_root() -> Path:
    return ROOT


def get_phase_status() -> list[tuple[str, str]]:
    return PHASES


def get_gate_verdict() -> dict[str, dict[str, str]]:
    return GATES


def _parse_yaml(path: Path) -> dict:
    """Parse a YAML config or experiment.yaml file.

    Falls back to a line-by-line parser for informal YAML (values with colons,
    brackets, etc.) that strict YAML parsers reject.
    """
    if yaml is not None:
        try:
            result = yaml.safe_load(path.read_text())
            if result is not None:
                return result
        except yaml.YAMLError:
            pass
    # Fallback: simple line-by-line parser for informal YAML
    result: dict = {}
    current_key: str | None = None
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0 and ":" in stripped:
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()
            if value:
                result[key] = value
                current_key = None
            else:
                current_key = key
                result[current_key] = {}
        elif current_key is not None and indent > 0 and ":" in stripped:
            key, _, value = stripped.partition(":")
            result[current_key][key.strip()] = value.strip()
    return result


def load_experiments() -> list[dict]:
    """Scan experiments/EXP-* and return metadata from experiment.yaml."""
    exp_dir = _project_root() / "experiments"
    if not exp_dir.is_dir():
        return []
    results: list[dict] = []
    for exp in sorted(exp_dir.iterdir()):
        if not exp.is_dir() or not exp.name.startswith("EXP-"):
            continue
        yaml_path = exp / "experiment.yaml"
        if not yaml_path.exists():
            continue
        meta = _parse_yaml(yaml_path)
        if meta is None:
            meta = {}
        meta["exp_id"] = exp.name
        results.append(meta)
    return results


def load_model_metrics() -> dict | None:
    """Read models/metrics.json if it exists."""
    path = _project_root() / "models" / "metrics.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def load_frontier_artifacts() -> dict[str, dict]:
    """Load all frontier_strategy.yaml files (JSON format) from experiments/."""
    exp_dir = _project_root() / "experiments"
    out: dict[str, dict] = {}
    if not exp_dir.is_dir():
        return out
    for exp in sorted(exp_dir.iterdir()):
        if not exp.is_dir() or not exp.name.startswith("EXP-"):
            continue
        fs_path = exp / "frontier_strategy.yaml"
        if fs_path.exists():
            out[exp.name] = json.loads(fs_path.read_text())
    return out


def get_test_status() -> tuple[int, int]:
    """Run pytest and return (passed, failed)."""
    path = _project_root() / "tests"
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(path), "-q", "--tb=no",
             "--ignore=tests/test_dashboard.py", "--ignore=tests/test_simulator.py"],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(_project_root()),
        )
        out = result.stdout + result.stderr
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return (0, 0)
    passed = failed = 0
    m_pass = re.search(r"(\d+) passed", out)
    m_fail = re.search(r"(\d+) failed", out)
    if m_pass:
        passed = int(m_pass.group(1))
    if m_fail:
        failed = int(m_fail.group(1))
    return (passed, failed)


def load_latest_bars() -> pl.DataFrame | None:
    """Load the latest bar data if available."""
    path = _project_root() / "data" / "btcusdt_1m.parquet"
    if not path.exists():
        return None
    return pl.read_parquet(str(path))


def run_backtest(
    start: str = "2024-01-01",
    end: str = "2025-01-01",
    arms: str = "random,threshold,policy,full,nojev",
    fee_mult: str = "1.0",
    p_thr: float = 0.40,
    seed: int = 7,
    no_logs: bool = False,
    stride: int = 1,
) -> list[dict]:
    """Run ablation simulation, return list of result dicts."""
    from datetime import datetime, timezone

    bars_path = _project_root() / "data" / "btcusdt_1m.parquet"
    if not bars_path.exists():
        return []

    from jev_trading.backtest.simulator import prepare, run, summarize_by_year, SimConfig
    from jev_trading.jev.mock import MockJev
    from jev_trading.quant.model import load_models

    def _utc_ms(d: str) -> int:
        dt = datetime.fromisoformat(d)
        return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)

    bars = pl.read_parquet(str(bars_path)).filter(pl.col("timestamp") < _utc_ms(end))
    quant = load_models(str(_project_root() / "models"))
    jev = MockJev()
    feats, p_up = prepare(bars, quant)

    results: list[dict] = []
    for arm in arms.split(","):
        for mult in [float(m) for m in fee_mult.split(",")]:
            cfg = SimConfig(fee_mult=mult, p_thr=p_thr, seed=seed, stride=stride)
            res = run(feats, p_up, jev, arm, cfg, log_path=None, start_ms=_utc_ms(start))
            m = res["metrics"] | {"by_year": summarize_by_year(res)}
            results.append(m)
    return results

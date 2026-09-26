"""Signal-driven shutdown: SIGTERM/SIGINT must stop the loop and persist state."""
from __future__ import annotations

import json
import signal
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


def test_runner_stops_on_signal_and_persists(tmp_path: Path):
    """A signal sets the stop flag; the loop exits and shutdown persists state."""
    from jev_trading.data.normalization import ReplayAdapter
    from jev_trading.live_intelligence.config import load_settings
    from jev_trading.live_intelligence.frontier.client import ReplayFrontierClient
    from jev_trading.live_intelligence.jev.client import ReplayJevClient
    from jev_trading.live_intelligence.runner import ShadowRunner
    from tests.test_stage13_runtime import _frame, _recent, _tick

    clock = [_recent()]
    base = load_settings()
    settings = base.model_copy(update={
        "experiment_id": "SIGNAL-EXP",
        "research": base.research.model_copy(update={"root": str(tmp_path / "research")}),
        "observability": base.observability.model_copy(update={"metrics_file": str(tmp_path / "m.json")}),
        "market": base.market.model_copy(update={"poll_seconds": 0.01}),
    })
    adapter = ReplayAdapter(
        "primary", "binance-futures", "BTCUSDT_PERP", _frame(clock[0]), [_tick(clock[0])],
    )
    runner = ShadowRunner(
        settings, adapter, ReplayFrontierClient(allow_trade=True), ReplayJevClient(),
        arm="A", ledger_path=tmp_path / "decisions.jsonl",
        checkpoint_path=tmp_path / "state.json", clock=lambda: clock[0],
    )
    assert runner.install_signal_handlers() is True
    # A real SIGTERM, delivered by the OS to this process while the loop runs.
    import threading
    timer = threading.Timer(0.5, lambda: signal.raise_signal(signal.SIGTERM))
    timer.start()
    summary = runner.run_forever(iterations=None)
    timer.cancel()
    assert summary["decisions"] >= 1
    assert summary["run_mode"] == "REPLAY"
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["ledger_hash"] == runner.ledger.last_hash


def test_cli_shutdown_on_sigterm(tmp_path: Path):
    """The real CLI process persists a final summary when terminated."""
    script = tmp_path / "driver.py"
    script.write_text(
        "import os, signal, subprocess, sys, time, json\n"
        f"out = subprocess.Popen([sys.executable, '-m', 'jev_trading.live_intelligence', 'shadow', 'run',\n"
        f"    '--config', 'configs/shadow-demo.json', '--arm', 'A', '--no-console',\n"
        f"    '--runtime-root', {str(tmp_path / 'run')!r}],\n"
        "    cwd=os.getcwd(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)\n"
        "time.sleep(25)\n"
        "out.send_signal(signal.SIGTERM)\n"
        "try:\n"
        "    stdout, _ = out.communicate(timeout=60)\n"
        "except subprocess.TimeoutExpired:\n"
        "    out.kill(); stdout = ''\n"
        f"open({str(tmp_path / 'summary.json')!r}, 'w').write(stdout)\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(script)], cwd=PROJECT, capture_output=True, text=True, timeout=200,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    stdout = (tmp_path / "summary.json").read_text()
    start = stdout.find("{")
    assert start >= 0, f"no shutdown summary was printed: {stdout[-500:]}"
    summary = json.loads(stdout[start:])
    assert summary["execution_mode"] == "PAPER"
    assert summary["live_orders"] == "DISABLED"
    assert summary["cancelled_pending_intent"] is None
    runtime = tmp_path / "run"
    assert (runtime / "decisions.jsonl").exists()
    assert (runtime / "runtime-state.json").exists()
    assert (runtime / "metrics.jsonl").exists()
    state = json.loads((runtime / "runtime-state.json").read_text())
    assert state["pending"] is None, "no executable paper order may survive shutdown"

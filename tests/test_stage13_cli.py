"""Stage 13: ledger linkage, crash recovery, doctor, and CLI integration."""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jev_trading.ledger import DecisionLedger
from jev_trading.live_intelligence.config import load_settings
from jev_trading.live_intelligence.schemas import (
    AccountState, DecisionRecord, ExecutionIntent, ExecutionState, PaperFill, PositionState,
    PolicyAction, PolicyDecision, RiskStatus, Side, Versions,
)
from tests.test_live_intelligence import environment, hypothesis, safe_quality
from tests.test_live_intelligence import make_candidate as _unused  # noqa: F401  (import guard)

UTC = timezone.utc
PROJECT = Path(__file__).resolve().parents[1]


def _approved_record():
    from jev_trading.live_intelligence.policy import make_candidate
    from jev_trading.live_intelligence.risk import ActiveRiskKernel
    from jev_trading.live_intelligence.schemas import RiskDecision
    settings = load_settings()
    env = environment(quality=safe_quality()).model_copy(update={
        "liquidity": environment(quality=safe_quality()).liquidity.model_copy(update={"top_level_notional": 1_000_000.0}),
    })
    candidate = make_candidate(env, hypothesis(), settings=settings)
    policy = PolicyDecision(
        decision_id="d", timestamp=env.decision_timestamp, action=PolicyAction.ENTER_LONG,
        reasons=("fixture",), candidate=candidate, policy_version="fixture",
    )
    risk: RiskDecision = ActiveRiskKernel(settings).evaluate(
        policy, env, AccountState(capital_usd=10_000), ExecutionState(), position=PositionState(),
    )
    intent = ExecutionIntent(
        intent_id="i", decision_id="d", mode="PAPER", action=PolicyAction.ENTER_LONG, side=Side.LONG,
        quantity=risk.approved_quantity, reference_price=100.0, stop=candidate.stop, target=candidate.target,
        created_at=env.timestamp, earliest_execution_at=env.timestamp + timedelta(minutes=1),
        strategy_id="momentum", strategy_version="v1",
    )
    record = DecisionRecord(
        decision_id="d", experiment_id=settings.experiment_id, timestamp=env.decision_timestamp,
        market_environment=env, frontier_hypothesis=hypothesis(), policy_decision=policy,
        risk_decision=risk, execution_intent=intent, position_before=PositionState(),
        position_after=PositionState(),
        versions=Versions(
            code_version="test", experiment_id=settings.experiment_id, frontier_model="f",
            frontier_prompt="p", jev_model="j", jev_prompt="p", quant_analyzers={},
            policy="p", risk="r", strategy_registry="s",
        ),
    )
    return record, intent


def _fill(intent, *, intent_id: str | None = None, decision_id: str | None = None) -> PaperFill:
    return PaperFill(
        fill_id="f", intent_id=intent_id or intent.intent_id, decision_id=decision_id or intent.decision_id,
        execution_timestamp=intent.earliest_execution_at, intended_price=100.0, fill_price=100.0,
        quantity=intent.quantity, fee_usd=0.0, slippage_usd=0.0, status="FILLED", mode="PAPER",
        experiment_id="SHADOW-BTCUSDT-001", symbol="BTCUSDT_PERP", side=Side.LONG,
        price_source="primary_1m_open", bar_timestamp=intent.created_at,
    )


def test_fill_requires_intent(tmp_path: Path):
    record, intent = _approved_record()
    ledger = DecisionLedger(tmp_path / "ledger.jsonl")
    ledger.append_decision(record)
    fabricated = _fill(intent, intent_id="fabricated-intent-id")
    with pytest.raises(ValueError, match="execution intent"):
        ledger.append_fill(fabricated)
    # A fabricated decision id is equally rejected.
    other = _fill(intent, decision_id="not-a-decision")
    with pytest.raises(ValueError, match="existing decision"):
        ledger.append_fill(other)
    ledger.append_fill(_fill(intent))
    assert len(ledger.fills()) == 1
    # Reloading re-verifies the semantic links, not just the hash chain.
    assert len(DecisionLedger(tmp_path / "ledger.jsonl").fills()) == 1


def test_trade_requires_fill(tmp_path: Path):
    from jev_trading.live_intelligence.schemas import TradeRecord
    record, intent = _approved_record()
    ledger = DecisionLedger(tmp_path / "ledger.jsonl")
    ledger.append_decision(record)
    trade = TradeRecord(
        trade_id="t", strategy_id="momentum", strategy_version="v1", side=Side.LONG, quantity=0.1,
        entry_price=100.0, exit_price=101.0, opened_at=intent.created_at,
        closed_at=intent.earliest_execution_at, gross_pnl_usd=0.1, fees_usd=0.0, slippage_usd=0.0,
        funding_usd=0.0, net_pnl_usd=0.1, entry_decision_id="d", exit_decision_id="d",
        entry_fill_id="ghost-fill", exit_fill_id="ghost-fill-2",
    )
    with pytest.raises(ValueError, match="existing entry and exit fills"):
        ledger.append_trade(trade)


def test_ledger_tampering_fails_closed(tmp_path: Path):
    record, intent = _approved_record()
    path = tmp_path / "ledger.jsonl"
    ledger = DecisionLedger(path)
    ledger.append_decision(record)
    ledger.append_fill(_fill(intent))
    lines = path.read_text().splitlines()
    entry = json.loads(lines[0])
    entry["experiment_id"] = "TAMPERED"
    lines[0] = json.dumps(entry, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(ValueError, match="hash mismatch"):
        DecisionLedger(path)


def test_shadow_config_is_paper_only_and_explicit():
    settings = load_settings("configs/shadow-demo.json")
    assert settings.experiment_id == "SHADOW-BTCUSDT-DEMO-001"
    assert settings.execution_mode == "PAPER"
    assert settings.market.instrument == "BTCUSDT_PERP"
    assert settings.market.primary_source == "binance"
    assert settings.market.secondary_source == "bybit"
    assert settings.paper.funding_model == "DISABLED"
    assert settings.market.poll_seconds > 0
    assert settings.frontier.periodic_seconds > 0
    assert settings.ledger_root == "research/runtime/shadow-demo"


def test_shadow_doctor_reports_every_check(tmp_path: Path):
    from jev_trading.live_intelligence.shadow import run_doctor
    from tests.test_stage13_runtime import _frame, _recent
    from jev_trading.data.normalization import ReplayAdapter
    settings = load_settings("configs/shadow-demo.json")
    now = datetime.now(UTC)
    adapter = ReplayAdapter("primary", "binance-futures", "BTCUSDT_PERP", _frame(now), [
        __import__("tests.test_stage13_runtime", fromlist=["_tick"])._tick(now),
    ])
    report = run_doctor(
        settings, adapter, arm="A", ledger_path=tmp_path / "decisions.jsonl",
        checkpoint_path=tmp_path / "state.json", feed_wait_seconds=0.0,
    )
    names = {check["name"] for check in report["checks"]}
    assert "execution mode is PAPER" in names
    assert "live order capability absent" in names
    assert "ledger path writable" in names and "checkpoint path writable" in names
    assert "1m bar continuity" in names
    assert "public Binance 1m data" in names
    assert report["verdict"] in {"SHADOW RUN READY", "SHADOW RUN NOT READY"}
    assert report["ready"] is (report["verdict"] == "SHADOW RUN READY")


def test_doctor_requires_providers_for_arm_c(tmp_path: Path, monkeypatch):
    from jev_trading.live_intelligence import shadow
    from jev_trading.live_intelligence.shadow import run_doctor
    from tests.test_stage13_runtime import _frame, _tick, _recent
    from jev_trading.data.normalization import ReplayAdapter
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    now = datetime.now(UTC)
    adapter = ReplayAdapter("primary", "binance-futures", "BTCUSDT_PERP", _frame(now), [_tick(now)])
    report = run_doctor(
        load_settings("configs/shadow-demo.json"), adapter, arm="C",
        ledger_path=tmp_path / "l.jsonl", checkpoint_path=tmp_path / "s.json", feed_wait_seconds=0.0,
    )
    provider_checks = [c for c in report["checks"] if "provider" in c["name"]]
    assert provider_checks, "arm C must be checked for providers"
    assert all(c["status"] == "FAIL" for c in provider_checks), "arm C is never silently downgraded"
    assert report["ready"] is False


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "jev_trading.live_intelligence", *args],
        cwd=PROJECT, capture_output=True, text=True, timeout=180,
    )


def test_cli_status_and_shadow_help():
    status = _cli("status", "--config", "configs/shadow-demo.json")
    assert status.returncode == 0, status.stderr
    payload = json.loads(status.stdout)
    assert payload["execution_mode"] == "PAPER"
    assert payload["live_orders"] == "DISABLED"
    assert payload["run_mode"] == "LIVE_DATA_PAPER"

    doctor = _cli("shadow", "doctor", "--help")
    assert doctor.returncode == 0
    run_help = _cli("shadow", "run", "--help")
    assert run_help.returncode == 0
    assert "--arm" in run_help.stdout
    inspect_help = _cli("shadow", "inspect", "--help")
    assert inspect_help.returncode == 0
    status_cmd = _cli("shadow", "status", "--config", "configs/shadow-demo.json")
    assert status_cmd.returncode == 0
    assert json.loads(status_cmd.stdout)["live_orders"] == "DISABLED"


def test_cli_paper_run_constructs_configured_components(tmp_path: Path, monkeypatch):
    """`paper --demo` must build the configured runner, not a hard-coded one.

    The feed is faked here: this test proves CLI wiring, not connectivity.
    """
    from jev_trading.data.normalization import ReplayAdapter
    from jev_trading.live_intelligence import cli as cli_module
    from jev_trading.live_intelligence.frontier.client import ReplayFrontierClient
    from jev_trading.live_intelligence.jev.client import ReplayJevClient
    from tests.test_stage13_runtime import _frame, _recent, _tick

    built: dict = {}

    def fake_components(settings, arm):
        built["arm"] = arm
        built["frontier"] = settings.frontier.provider.provider
        built["jev"] = settings.jev.provider.provider
        adapter = ReplayAdapter(
            "primary", "binance-futures", "BTCUSDT_PERP", _frame(_recent()), [_tick(_recent())],
        )
        adapter.start = lambda: None
        adapter.close = lambda: None
        return adapter, ReplayFrontierClient(allow_trade=True), ReplayJevClient()

    monkeypatch.setattr(cli_module, "_build_live_components", fake_components)
    settings = load_settings("configs/shadow-demo.json")
    monkeypatch.setattr(
        cli_module, "load_settings",
        lambda path: settings.model_copy(update={
            "ledger_root": str(tmp_path),
            "research": settings.research.model_copy(update={"root": str(tmp_path / "research")}),
        }),
    )
    result = cli_module.main([
        "paper", "--config", "configs/shadow-demo.json", "--arm", "A", "--iterations", "1", "--demo",
    ])
    assert result == 0
    assert built["arm"] == "A"
    assert built["frontier"] == settings.frontier.provider.provider
    assert built["jev"] == settings.jev.provider.provider
    assert (tmp_path / "metrics.jsonl").exists()
    assert (tmp_path / "manifest.json").exists()
    assert (tmp_path / "decisions.jsonl").exists()
    assert (tmp_path / "runtime-state.json").exists()
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["execution_mode"] == "PAPER"
    assert manifest["live_orders"] == "DISABLED"


def test_shadow_inspect_verifies_the_ledger(tmp_path: Path):
    from jev_trading.live_intelligence.shadow import inspect_run
    record, intent = _approved_record()
    path = tmp_path / "decisions.jsonl"
    ledger = DecisionLedger(path)
    ledger.append_decision(record)
    ledger.append_fill(_fill(intent))
    report = inspect_run(path)
    assert report["integrity"] == "VERIFIED"
    assert report["decisions"] == 1
    assert report["fills"] == 1
    assert report["trades"] == 0

"""Content-addressed experiment provenance tests."""
from __future__ import annotations

import json
from pathlib import Path

from jev_trading.live_intelligence.config import load_settings
from jev_trading.live_intelligence.experiment import config_hash, file_hash, freeze_experiment, make_versions


def test_versions_record_git_config_prompt_policy_risk_and_strategy_hashes():
    settings = load_settings()
    versions = make_versions(
        settings, frontier_model="frontier-test", frontier_prompt_hash="a" * 64,
        jev_model="jev-test", jev_prompt_hash="b" * 64,
        quant_versions={"trend": "trend-v1"}, arm="B",
    )
    assert len(versions.git_commit) == 40
    assert len(versions.config_hash) == 64
    assert len(versions.frontier_prompt_hash) == 64
    assert len(versions.jev_prompt_hash) == 64
    assert len(versions.policy_hash) == 64
    assert len(versions.risk_hash) == 64
    assert len(versions.strategy_registry_hash) == 64
    assert versions.experiment_arm == "B"
    assert versions.quant_analyzers == {"trend": "trend-v1"}


def test_config_hash_changes_when_runtime_configuration_changes(tmp_path: Path):
    settings = load_settings()
    original = config_hash(settings)
    changed = settings.model_copy(update={"experiment_id": "OTHER"})
    assert config_hash(changed) != original
    target = freeze_experiment(
        settings, tmp_path / "manifest.json", frontier_model="f", jev_model="j",
        quant_versions={"trend": "trend-v1"},
    )
    payload = json.loads(target.read_text())
    assert payload["versions"]["git_commit"]
    assert payload["versions"]["config_hash"] == original

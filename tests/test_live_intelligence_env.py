"""Project-root .env parsing, precedence, and secret-safety tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from jev_trading.live_intelligence.config import load_project_env


def test_env_parser_loads_comments_export_and_quoted_values(tmp_path: Path, monkeypatch):
    for key in ("JEV_TEST_KEY", "JEV_TEST_MODEL", "JEV_TEST_BASE"):
        monkeypatch.delenv(key, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        "export JEV_TEST_KEY='secret-value'\n"
        'JEV_TEST_MODEL="provider/model"\n'
        "JEV_TEST_BASE=https://example.invalid/v1\n"
    )
    loaded = load_project_env(env_file)
    assert loaded == {
        "JEV_TEST_KEY": "secret-value",
        "JEV_TEST_MODEL": "provider/model",
        "JEV_TEST_BASE": "https://example.invalid/v1",
    }


def test_existing_process_environment_wins(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JEV_TEST_KEY", "already-exported")
    env_file = tmp_path / ".env"
    env_file.write_text("JEV_TEST_KEY=from-file\n")
    loaded = load_project_env(env_file)
    assert loaded == {}
    assert __import__("os").environ["JEV_TEST_KEY"] == "already-exported"


def test_invalid_env_line_does_not_echo_value(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("OPENROUTER_API_KEY=do-not-print-this\nnot-an-assignment\n")
    with pytest.raises(ValueError) as error:
        load_project_env(env_file)
    assert "not-an-assignment" not in str(error.value)
    assert "do-not-print-this" not in str(error.value)


def test_load_settings_automatically_loads_project_env(tmp_path: Path, monkeypatch):
    from jev_trading.live_intelligence import config

    monkeypatch.delenv("JEV_TEST_KEY", raising=False)
    monkeypatch.setattr(config, "project_root", lambda: tmp_path)
    (tmp_path / ".env").write_text("JEV_TEST_KEY=loaded-by-settings\n")
    config_file = tmp_path / "configs" / "live.json"
    config_file.parent.mkdir()
    config_file.write_text('{"experiment_id":"ENV-LOAD-TEST"}\n')
    settings = config.load_settings(config_file)
    assert settings.experiment_id == "ENV-LOAD-TEST"
    assert __import__("os").environ["JEV_TEST_KEY"] == "loaded-by-settings"


def test_provider_environment_overrides_are_applied_to_settings(tmp_path: Path, monkeypatch):
    from jev_trading.live_intelligence import config

    monkeypatch.setattr(config, "project_root", lambda: tmp_path)
    monkeypatch.setenv("FRONTIER_PROVIDER", "openai_compatible")
    monkeypatch.setenv("JEV_PROVIDER", "openai_compatible")
    monkeypatch.setenv("FRONTIER_BASE_URL", "https://frontier.example/v1")
    monkeypatch.setenv("JEV_BASE_URL", "https://jev.example/v1")
    monkeypatch.setenv("FRONTIER_MODEL", "frontier/model")
    monkeypatch.setenv("JEV_MODEL", "jev/model")
    monkeypatch.setenv("FRONTIER_SUPPORTS_RESPONSE_FORMAT", "true")
    monkeypatch.setenv("JEV_SUPPORTS_RESPONSE_FORMAT", "false")
    config_file = tmp_path / "configs" / "live.json"
    config_file.parent.mkdir()
    config_file.write_text('{"experiment_id":"ENV-LOAD-TEST"}\n')
    settings = config.load_settings(config_file)
    assert settings.frontier.provider.provider == "openai_compatible"
    assert settings.jev.provider.provider == "openai_compatible"
    assert settings.frontier.provider.base_url == "https://frontier.example/v1"
    assert settings.jev.provider.base_url == "https://jev.example/v1"
    assert settings.frontier.provider.model == "frontier/model"
    assert settings.jev.provider.model == "jev/model"
    assert settings.frontier.provider.supports_response_format is True
    assert settings.jev.provider.supports_response_format is False

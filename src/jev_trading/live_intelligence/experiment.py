"""Content-addressed experiment versions and manifest freeze."""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .config import LiveSettings, project_root
from .schemas import Versions


def content_hash(value: str | bytes) -> str:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def file_hash(path: str | Path) -> str:
    candidate = Path(path)
    if not candidate.exists():
        candidate = project_root() / candidate
    return content_hash(candidate.read_bytes()) if candidate.exists() else ""


def git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=project_root(), check=True,
            capture_output=True, text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def config_hash(settings: LiveSettings) -> str:
    return content_hash(settings.model_dump_json())


def make_versions(
    settings: LiveSettings,
    *,
    frontier_model: str,
    frontier_prompt_hash: str,
    jev_model: str,
    jev_prompt_hash: str,
    quant_versions: dict[str, str],
    arm: str = "C",
) -> Versions:
    root = Path(__file__).resolve().parent
    return Versions(
        code_version=f"{settings.code_version}@{git_commit()[:12]}",
        experiment_id=settings.experiment_id,
        frontier_model=frontier_model,
        frontier_prompt=settings.frontier.prompt_version,
        jev_model=jev_model,
        jev_prompt=settings.jev.prompt_version,
        quant_analyzers=quant_versions,
        policy="policy-v1",
        risk="active-risk-v1",
        strategy_registry=settings.strategy_registry_version,
        frontier_prompt_hash=frontier_prompt_hash,
        jev_prompt_hash=jev_prompt_hash,
        experiment_arm=arm,
        git_commit=git_commit(),
        config_hash=config_hash(settings),
        policy_hash=file_hash(root / "policy/finalizer.py"),
        risk_hash=file_hash(root / "risk.py"),
        strategy_registry_hash=file_hash(settings.research.strategy_registry),
    )


def freeze_experiment(settings: LiveSettings, path: str | Path, *, frontier_model: str, jev_model: str, quant_versions: dict[str, str]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(f"experiment manifest already exists: {target}")
    versions = make_versions(
        settings, frontier_model=frontier_model,
        frontier_prompt_hash=file_hash(Path(__file__).parent / settings.frontier.prompt_file),
        jev_model=jev_model,
        jev_prompt_hash=file_hash(Path(__file__).parent / settings.jev.prompt_file),
        quant_versions=quant_versions,
    )
    payload = {"frozen_at": datetime.now(tz=timezone.utc).isoformat(), "versions": versions.model_dump(mode="json"), "execution_mode": settings.execution_mode}
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return target

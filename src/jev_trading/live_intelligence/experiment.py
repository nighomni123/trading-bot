"""Experiment manifest freeze for a shadow run."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .config import LiveSettings
from .schemas import Versions


def freeze_experiment(settings: LiveSettings, path: str | Path, *, frontier_model: str, jev_model: str, quant_versions: dict[str, str]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(f"experiment manifest already exists: {target}")
    versions = Versions(
        code_version=settings.code_version, experiment_id=settings.experiment_id,
        frontier_model=frontier_model, frontier_prompt=settings.frontier.prompt_version,
        jev_model=jev_model, jev_prompt=settings.jev.prompt_version,
        quant_analyzers=quant_versions, policy="policy-v1", risk="active-risk-v1",
        strategy_registry=settings.strategy_registry_version,
    )
    payload = {"frozen_at": datetime.now(tz=timezone.utc).isoformat(), "versions": versions.model_dump(mode="json"), "execution_mode": settings.execution_mode}
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return target

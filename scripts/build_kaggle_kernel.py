#!/usr/bin/env python3
"""Build a reproducible Kaggle kernel plus its private input dataset.

Kaggle kernels do not expose arbitrary auxiliary files from the kernel upload.
The source snapshot and pre-OOS parquet therefore go into a small private
Kaggle dataset; the notebook reads that dataset from /kaggle/input.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "kaggle" / "notebook.ipynb"
METADATA = ROOT / "kaggle" / "kernel-metadata.json"
DEFAULT_DATA = ROOT / "artifacts" / "phase0-baseline-final-d" / "pre_oos_2021_2024.parquet"
DATASET_SLUG = "jev-trading-research-bundle"
MODES = ("smoke", "phase0", "phase1", "phase2", "ablation", "all")


def git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=ROOT, text=True, stderr=subprocess.STDOUT
    ).strip()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def included(rel: Path) -> bool:
    parts = rel.parts
    if not parts or parts[0] in {".local", ".venv", "kaggle"}:
        return False
    if parts[:2] == ("docs", "pre-jev"):
        return False
    if parts[0] == "experiments" and rel.suffix == ".jsonl":
        return False
    if parts[0] in {"src", "tests", "scripts", "configs", "docs", "experiments"}:
        return True
    return rel.name in {"README.md", "pyproject.toml", ".gitignore"}


def source_files() -> list[Path]:
    tracked_raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    untracked_raw = subprocess.check_output(
        ["git", "ls-files", "-o", "--exclude-standard", "-z"], cwd=ROOT
    )
    tracked = {Path(os.fsdecode(item)) for item in tracked_raw.split(b"\0") if item}
    untracked = {Path(os.fsdecode(item)) for item in untracked_raw.split(b"\0") if item}
    paths = []
    for rel in tracked | untracked:
        if rel.parts and rel.parts[0] == "experiments" and rel not in tracked:
            continue
        path = ROOT / rel
        if path.is_file() and included(rel):
            paths.append(rel)
    return sorted(set(paths))


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build(out: Path, data: Path, mode: str, trees: int, leaves: int, skip_derived: bool) -> None:
    if not NOTEBOOK.is_file() or not METADATA.is_file():
        raise FileNotFoundError("kaggle/notebook.ipynb and kaggle/kernel-metadata.json are required")
    if not data.is_file():
        raise FileNotFoundError(f"pre-OOS parquet not found: {data}")
    if out.exists():
        shutil.rmtree(out)
    kernel_dir = out / "kernel"
    dataset_dir = out / "dataset"
    staging = out / ".source"
    kernel_dir.mkdir(parents=True)
    dataset_dir.mkdir(parents=True)
    staging.mkdir()

    copied = []
    for rel in source_files():
        source = ROOT / rel
        target = staging / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append({"path": rel.as_posix(), "sha256": sha256(source)})

    base_commit = git("rev-parse", "HEAD")
    status = git("status", "--porcelain=v1", "--untracked-files=all")
    source_manifest = {
        "base_commit": base_commit,
        "worktree_dirty": bool(status),
        "source_files": copied,
        "excluded": ["artifacts/", "data/", "experiments/**/*.jsonl", ".git/", ".venv/", ".local/"],
    }
    write_json(staging / "KAGGLE_SOURCE_MANIFEST.json", source_manifest)

    archive = dataset_dir / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                tar.add(path, arcname=path.relative_to(staging).as_posix())
    shutil.rmtree(staging)

    data_target = dataset_dir / "pre_oos_2021_2024.parquet"
    shutil.copy2(data, data_target)
    run_config = {
        "mode": mode,
        "trees": trees,
        "leaves": leaves,
        "skip_derived": skip_derived,
        "dataset_slug": DATASET_SLUG,
        "data_file": data_target.name,
        "base_commit": base_commit,
        "source_archive_sha256": sha256(archive),
        "data_sha256": sha256(data_target),
        "created_utc": datetime.now(timezone.utc).isoformat(),
    }
    write_json(dataset_dir / "run_config.json", run_config)
    write_json(
        dataset_dir / "build_manifest.json",
        {
            "kernel_source": base_commit,
            "source_archive": archive.name,
            "source_archive_sha256": run_config["source_archive_sha256"],
            "data_file": data_target.name,
            "data_sha256": run_config["data_sha256"],
            "mode": mode,
        },
    )
    write_json(
        dataset_dir / "dataset-metadata.json",
        {
            "title": "JEV Trading Research Bundle",
            "id": f"nighomni/{DATASET_SLUG}",
            "licenses": [{"name": "other"}],
        },
    )

    notebook_text = NOTEBOOK.read_text(encoding="utf-8")
    for marker, value in {
        "__JEV_MODE_FROM_BUILD__": mode,
        "__JEV_SOURCE_HASH_FROM_BUILD__": run_config["source_archive_sha256"],
        "__JEV_DATA_HASH_FROM_BUILD__": run_config["data_sha256"],
    }.items():
        notebook_text = notebook_text.replace(marker, value)
    (kernel_dir / "notebook.ipynb").write_text(notebook_text, encoding="utf-8")
    shutil.copy2(METADATA, kernel_dir / "kernel-metadata.json")
    print(json.dumps({
        "out": str(out),
        "kernel_dir": str(kernel_dir),
        "dataset_dir": str(dataset_dir),
        "mode": mode,
        "source_archive_bytes": archive.stat().st_size,
        "data_bytes": data_target.stat().st_size,
        "source_files": len(copied),
    }, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="build directory to create")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--mode", choices=MODES, default="smoke")
    parser.add_argument("--trees", type=int, default=60)
    parser.add_argument("--leaves", type=int, default=15)
    parser.add_argument("--skip-derived", action="store_true")
    args = parser.parse_args()
    build(args.out, args.data, args.mode, args.trees, args.leaves, args.skip_derived)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env bash
set -euo pipefail

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
MODE=${1:-smoke}
if [[ $# -gt 0 ]]; then shift; fi
BUILD_DIR=${JEV_KAGGLE_BUILD_DIR:-$(mktemp -d "${TMPDIR:-/tmp}/jev-kaggle-build.XXXXXX")}
PYTHON=${JEV_PYTHON:-"$ROOT/.venv/bin/python"}

"$PYTHON" "$ROOT/scripts/build_kaggle_kernel.py" \
  --out "$BUILD_DIR" --mode "$MODE" "$@"

DATASET_DIR="$BUILD_DIR/dataset"
KERNEL_DIR="$BUILD_DIR/kernel"
DATASET_REF="nighomni/jev-trading-research-bundle"

wait_for_dataset() {
  for _ in {1..60}; do
    status=$(kaggle datasets status "$DATASET_REF" --format json 2>/dev/null || true)
    if [[ "$status" == *'"status": "ready"'* ]]; then
      return 0
    fi
    sleep 5
  done
  printf 'Kaggle dataset did not become ready: %s\n' "$DATASET_REF" >&2
  return 1
}

if kaggle datasets status "$DATASET_REF" --format json >/dev/null 2>&1; then
  kaggle datasets version -p "$DATASET_DIR" \
    -m "Run $MODE benchmark" -r skip -t
else
  kaggle datasets create -p "$DATASET_DIR" -r skip -t
fi
wait_for_dataset

kaggle kernels push -p "$KERNEL_DIR" -t "${JEV_KAGGLE_TIMEOUT:-900}"
printf 'Submitted %s; build=%s\n' "$MODE" "$BUILD_DIR"

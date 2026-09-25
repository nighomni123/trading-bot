# Kaggle benchmark runner

`notebook.ipynb` is a thin wrapper around the existing repository experiment scripts. The private dataset contains the current source snapshot and `pre_oos_2021_2024.parquet`; the notebook verifies the data boundary before running anything. The build records the base commit and per-file hashes, so commit local changes before comparing separate benchmark runs.

## Build a run

```bash
.venv/bin/python scripts/build_kaggle_kernel.py \
  --out /tmp/jev-kaggle-build \
  --mode smoke
```

Modes: `smoke`, `phase0`, `phase1`, `phase2`, `ablation`, `all`.

## One-command submission

For an existing private dataset, the helper builds, versions, and submits in one step:

```bash
scripts/run_kaggle_benchmark.sh phase1
```

Use `JEV_KAGGLE_BUILD_DIR=/tmp/my-build` to retain the generated bundle. The helper submits the kernel but does not wait for it.

## Submit

The first input dataset upload is:

```bash
kaggle datasets create -p /tmp/jev-kaggle-build/dataset -r skip -t
```

For later runs, version the private dataset and push the kernel:

```bash
kaggle datasets version -p /tmp/jev-kaggle-build/dataset \
  -m "Run <mode> benchmark" -r skip -t
kaggle kernels push -p /tmp/jev-kaggle-build/kernel -t 900
```

## Observe and collect

```bash
kaggle kernels status nighomni/jev-trading-research-benchmarks
kaggle kernels logs nighomni/jev-trading-research-benchmarks
kaggle kernels output nighomni/jev-trading-research-benchmarks \
  -p /tmp/jev-kaggle-results --file-pattern 'results.zip'
```

The notebook writes `results.zip` with `run_status.json`, `run_config.json`, `timings.json`, and the selected experiment artifacts. Keep the local test suite as the fast path; use Kaggle for CPU-heavy phase runs and benchmarking.

# Experiment protocol (reproducibility without MLflow)

Every P4/P7 result lives under `experiments/EXP-NNN/` with an `experiment.yaml`:

```yaml
experiment_id: EXP-001
data_version: btcusdt_1m sha256 + [start, end)
feature_version: git sha of state/features.py
label_version: threshold + horizon H
model_version: baseline id (A–E) + seed
policy_version: git sha of policy/engine.py + configs
cost_model: fee_bps, slippage_bps, funding, latency rule
train_period: [..]  validation_period: [..]  test_period: [..]  embargo_bars: 15
parameters: {}
results: {log_loss, auc, cost_adjusted_return, max_drawdown, jev_delta_vs_C}
```

Rules: one experiment = one question; test period is written before training and never
edited; re-running the directory reproduces the numbers. Migrate to MLflow only when
manual directories hurt.

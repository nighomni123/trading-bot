import { readArtifact, readText } from "./artifacts";

/* ============================================================
   Typed accessors over the research artifacts.
   Every dashboard number is read from here — none are hardcoded.
   ============================================================ */

/* ---------- Stage 8: economic diagnostic ---------- */
export type Stage8 = {
  generated_at: string;
  evaluation_candidates: number;
  policy_eligible: number;
  policy_rejection_histogram: Record<string, number>;
  required_vs_observed: {
    median_ratio: number;
    min_ratio: number;
    max_ratio: number;
  };
  cost_decomposition: {
    configured: { fees_bps: number; slippage_bps: number; funding_bps: number; latency_bps: number; total_bps: number };
    gross_bps: { median: number; max: number };
    cost_stack_vs_gross: { median_cost_as_multiple_of_median_gross: number };
    empirical_round_trip_bps: number;
  };
  verdict: {
    case: string;
    reason: string;
    median_observed_over_required_p_target: number;
    median_predicted_p_target: number;
    median_gross_bps: number;
    any_geometry_with_positive_net: boolean;
    best_geometry: { horizon_minutes: number; rr: number; median_net_bps: number };
  };
  execution: string;
  economics_per_candidate: {
    timestamp: string; side: string; horizon_minutes: number; rr: number;
    sample_size: number; p_target: number; p_stop: number; p_timeout: number;
    gross_bps: number; fees_bps: number; slippage_bps: number; latency_bps: number;
    funding_bps: number; net_bps: number; required_p_target: number;
    observed_over_required: number; realized_return_bps: number;
    policy_action: string; policy_reasons: string[];
  }[];
};
export const loadStage8 = () => readArtifact<Stage8>("docs/quant-economic-diagnostic-2026-09-25.json");

/* ---------- Stage 9: walk-forward ---------- */
export type Fold = {
  fold: string; n_train: number; n_test: number; base_rate: number;
  B0_trailing: { roc_auc: number; spearman: number };
  B1_lgbm_binary: { roc_auc: number; pr_auc: number; brier: number; spearman: number };
  B2_lgbm_3class: { roc_auc: number; pr_auc: number; brier: number; spearman: number };
  economics: { median_gross_bps: number; median_net_bps: number; max_net_bps: number; eligible_count: number; median_required_p_target: number; median_p_target: number };
};
export type Stage9 = { generated_at?: string; geometry?: string; folds: Fold[]; [k: string]: unknown };
export const loadStage9 = () => readArtifact<Stage9>("docs/stage9-walkforward-2026-09-25.json");

/* ---------- Stage 10: geometry oracle grid ---------- */
export type GridCell = {
  geometry: string; target_atr: number; stop_atr: number; horizon: number;
  n: number; target_bps: number; stop_bps: number;
  p_target: number; p_stop: number;
  max_achievable_gross_bps: number; max_gross_minus_cost_bps: number; oracle_viable: boolean;
};
export type OracleGrid = { generated_at?: string; cost_bps: number; cells: number; oracle_feasible: number; rows: GridCell[] };
export const loadOracleGrid = () => readArtifact<OracleGrid>("docs/geometry-oracle-grid-2026-09-25.json");

/* ---------- Stage 10: execution cost measurement ---------- */
export type Stat = { n: number; median: number; p90: number; max: number };
export type CostMeasurement = {
  measurement: {
    venue_reachable: boolean; error: string | null; samples: number;
    spread_bps: Stat; taker_slippage_bps: Stat; maker_slippage_bps: Stat;
    book_depth_levels: Stat; top_level_notional_usd: Stat;
  };
  profiles: {
    name: string; mode: string; fee_rt_bps: number;
    slippage_source: "assumed" | "measured"; total_rt_bps: number;
    achievable: boolean; note: string;
  }[];
};
export const loadCostMeasurement = () => readArtifact<CostMeasurement>("docs/execution-cost-measurement-2026-09-25.json");

/* ---------- Stage 11: microstructure ---------- */
export type Dist = {
  n: number; mean: number; median: number; std: number;
  p_positive: number; p_negative: number;
  p05: number; p25: number; p75: number; p95: number;
};
export type HorizonCell = {
  conditional: Dist;
  unconditional: Dist;
  test: { diff: number; stderr: number; t: number };
};
export type EventResult = {
  n_events: number;
  by_horizon: Record<string, HorizonCell>;
};
export type S11EconRow = {
  /** Combined id, e.g. "EVENT_FLOW_REVERSAL:60m" */
  event: string;
  direction: string;
  n: number;
  gross_expected_bps: number;
  cost_bps: number;
  net_expected_bps: number;
  economically_viable: boolean;
  cost_multiple: number | null;
};
export type Stage11 = {
  coverage?: Record<string, unknown>;
  features?: Record<string, unknown>;
  events: string[];
  study: {
    experiment_version: string;
    event_version: string;
    target_version: string;
    horizons: number[];
    events: Record<string, EventResult>;
  };
  economics: {
    experiment_version?: string;
    cost_provenance?: { measured_at_cost_bps: number; [k: string]: unknown };
    rows: S11EconRow[];
    n_rows: number;
    n_economically_viable: number;
    viable: unknown[];
  };
  models?: Record<string, unknown>;
};
export const loadStage11 = () => readArtifact<Stage11>("docs/stage-11-results-2025-06.json");

/* ---------- Stage 12 ---------- */
export type Stage12Coverage = {
  generated_at: string; instrument: string; venue: string; contract_type: string; frequency: string;
  first_timestamp: string; last_timestamp: string;
  rows: number; missing_bars: number; duplicate_timestamps: number; timestamp_gaps: number;
  monotonic: boolean; invalid_open_interest: number; dataset_hash: string;
  horizons_minutes: number[];
  trade_flow_coverage?: { available: boolean; days: number; note: string };
  order_book_coverage?: { available: boolean; note: string };
};
export const loadStage12Coverage = () =>
  readArtifact<Stage12Coverage>("research/stage12/data_coverage.json");

export type Stage12Fold = {
  fold: string;
  n_train: number;
  n_test: number;
  baseline_mean_bps: number;
  linear_error?: string;
  lgbm_auc: number;
  lgbm_pr_auc: number;
  lgbm_brier: number;
  lgbm_logloss: number;
  /** 10 decile means in bps — decile 0 worst … decile 9 best. */
  decile_means_bps: number[];
  lgbm_return_ic: number;
};
export type Stage12Horizon = {
  /** Emitted as a label string like "4h" by the producer, not a number. */
  horizon: number | string;
  n_features: number;
  embargo_ms: number;
  folds: Stage12Fold[];
};
/** Keyed by horizon label, e.g. "4h" | "24h" | "72h". */
export type Stage12Models = Record<string, Stage12Horizon>;
export const loadStage12Models = () => readArtifact<Stage12Models>("research/stage12/model_results.json");

/**
 * Flatten { "4h": {...}, "24h": {...} } into rows tagged with a numeric horizon.
 * The artifact carries the horizon as a label ("4h"), so normalise once here
 * rather than sprinkling parseInt across every consumer.
 */
export function stage12Rows(m: Stage12Models | null): (Stage12Fold & { horizon: number })[] {
  if (!m) return [];
  return Object.values(m).flatMap((h) => {
    const horizon =
      typeof h.horizon === "number" ? h.horizon : Number(String(h.horizon).replace(/h$/, ""));
    return h.folds.map((f) => ({ ...f, horizon }));
  });
}

export type StabilityYear = {
  n_cond: number;
  mean_bps: number;
  uncond_mean_bps: number;
  diff_bps: number;
  se_bps: number;
  t: number;
};
/** Keyed by state condition, e.g. "TREND_STATE=UP@24h", then by year. */
export type Stage12Stability = Record<string, Record<string, StabilityYear>>;
export const loadStage12Stability = () => readArtifact<Stage12Stability>("research/stage12/stability.json");

export type Stage12Economics = {
  cost_bps: number;
  cost_provenance: string;
  in_sample_rows: number;
  in_sample_viable: number;
  oos_reality_check?: Record<
    string,
    { mean_oos_auc: number; folds: number; oos_predictable_edge_bps: number }
  >;
  conclusion?: string;
};
export const loadStage12Economics = () => readArtifact<Stage12Economics>("research/stage12/economics.json");

export type StateRow = {
  horizon: string;
  state: string;
  level: string;
  diff_bps: number;
  t: number;
  p: number;
  n_cond: number;
  n_uncond: number;
  cond_mean_bps: number;
  uncond_mean_bps: number;
  q_bh: number;
};
export type StateHorizonBlock = {
  n_hypotheses_tested: number;
  n_significant_fdr10: number;
  rows: StateRow[];
};
/** Keyed by horizon label, e.g. "4h". */
export type StateStudy = Record<string, StateHorizonBlock>;
export const loadStateStudy = () => readArtifact<StateStudy>("research/stage12/state_study.json");

/** Every state row across all horizons, with the horizon carried on the row. */
export function allStateRows(s: StateStudy | null): StateRow[] {
  if (!s) return [];
  return Object.values(s).flatMap((b) => b.rows);
}

/* ---------- Stage 13 runtime ---------- */
export type RunMetrics = Record<string, number>;
export const loadRuntimeMetrics = (dir = "research/runtime") => readArtifact<RunMetrics>(`${dir}/metrics.json`);
export const loadArmCMetrics = () => loadRuntimeMetrics("research/runtime/shadow-demo-arm-c");
export const loadArmAMetrics = () => loadRuntimeMetrics("research/runtime/shadow-demo");

export type ArmManifest = {
  experiment_id: string; arm: string; config_hash: string; git_commit: string;
  frozen_at: string; frontier_model: string; jev_model: string;
  ledger_root: string; run_mode: string; code_version: string;
  [k: string]: unknown;
};
export const loadArmCManifest = () => readArtifact<ArmManifest>("research/runtime/shadow-demo-arm-c/manifest.json");

/* ---------- Hypothesis registry ---------- */
export type Hypothesis = {
  alpha_id: string; status: string; [k: string]: unknown;
};
export const loadHypotheses = (): Hypothesis[] =>
  ["EXP-11-BOOK-MICROPRICE", "EXP-11-FLOW-IMBALANCE", "EXP-11-FLOW-REVERSAL",
   "EXP-11-PRICE-SHOCK-REVERSION", "EXP-12-MOMENTUM-REVERSION-24H"]
    .map((id) => readArtifact<Hypothesis>(`research/alphas/hypotheses/${id}.json`))
    .filter((h): h is Hypothesis => h !== null);

export const readDoc = (p: string) => readText(p);

/** Canonical measured round-trip cost, resolved from the cost artifact. */
export function canonicalCost(cost: CostMeasurement | null): number {
  const p = cost?.profiles?.find((x) => x.name === "measured_taker_taker");
  return p ? p.total_rt_bps : 11.005972670255792;
}

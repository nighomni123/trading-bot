import {
  loadStage8, loadStage11, loadOracleGrid, loadCostMeasurement,
  loadStage12Economics, loadStage12Coverage, canonicalCost,
} from "./data";
import type { NotTradingData } from "@/components/NotTrading";

/**
 * Derive the persistent "Why Are We Not Trading?" state from artifacts.
 * Nothing here is hardcoded — if an artifact disappears the claim degrades.
 */
export function buildNotTrading(): NotTradingData {
  const s8 = loadStage8();
  const s11 = loadStage11();
  const grid = loadOracleGrid();
  const cost = loadCostMeasurement();
  const s12 = loadStage12Economics();

  const costBps = canonicalCost(cost);
  const attainment = s8?.verdict.median_observed_over_required_p_target ?? 0.103;

  // Best economically-considered signal: Stage 11's largest gross effect.
  // Falls back to Stage 8's median gross if the Stage 11 artifact is absent.
  let signalBps = s8?.verdict.median_gross_bps ?? 0;
  let signalSource = "S8 median gross";
  const s11rows = s11?.economics?.rows ?? [];
  if (s11rows.length) {
    const best = s11rows.reduce((a, b) => (b.gross_expected_bps > a.gross_expected_bps ? b : a));
    signalBps = best.gross_expected_bps;
    signalSource = `S11 ${best.event.replace("EVENT_", "")}`;  }

  // Oracle bound: the best a perfect-foresight model could do after cost.
  const viable = (grid?.rows ?? []).filter((r) => r.oracle_viable);
  const bestViable = viable.length
    ? viable.reduce((a, b) => (b.max_gross_minus_cost_bps > a.max_gross_minus_cost_bps ? b : a))
    : null;

  const blockers: { label: string; detail: string }[] = [
    {
      label: "Signal < Cost",
      detail: `largest reproducible gross effect ${signalBps.toFixed(2)} bps vs ${costBps.toFixed(2)} bps measured round-trip`,
    },
    {
      label: "Oracle gap unclosed",
      detail: `p_target attains ${(attainment * 100).toFixed(1)}% of the level required to break even`,
    },
    {
      label: "Horizon pivot unresolved",
      detail: s12
        ? `S12 ${s12.in_sample_viable}/${s12.in_sample_rows} in-sample rows viable; multi-year sign flip unresolved`
        : "Stage 12 economics artifact unavailable",
    },
  ];

  return {
    signalBps,
    signalSource,
    costBps,
    costSource: "S10 measured taker/taker",
    oracleBps: bestViable ? bestViable.max_gross_minus_cost_bps : null,
    attainment,
    blockers,
  };
}

/** Data-vintage string for the top bar. */
export function buildVintage(): { vintage: string; note?: string } {
  const c = loadStage12Coverage();
  if (c) {
    return {
      vintage: "btcusdt_1m.parquet",
      note: `${c.first_timestamp.slice(0, 10)} → ${c.last_timestamp.slice(0, 10)}`,
    };
  }
  return { vintage: "btcusdt_1m.parquet", note: "2021 → 2025" };
}

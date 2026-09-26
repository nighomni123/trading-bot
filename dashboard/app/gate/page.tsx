import PageHead from "@/components/PageHead";
import PipelineGraph from "@/components/PipelineGraph";
import ArtifactTrace from "@/components/ArtifactTrace";
import { Card, Stat, KV, Badge, Principle } from "@/components/UI";
import { loadStage8, loadStage9, loadCostMeasurement, loadStage12Coverage, canonicalCost } from "@/lib/data";
import { nCompact, num, bps, pct, daysSince } from "@/lib/format";
import type { PipelineNode } from "@/lib/gate";

export const dynamic = "force-dynamic";

export default function GatePage() {
  const s8 = loadStage8();
  const s9 = loadStage9();
  const cost = loadCostMeasurement();
  const cov = loadStage12Coverage();
  const costBps = canonicalCost(cost);
  const s9folds = s9?.folds ?? [];
  const eligibleTotal = s9folds.reduce((a, f) => a + (f.economics?.eligible_count ?? 0), 0);

  const nodes: PipelineNode[] = [
    {
      id: "ingest",
      label: "Data Ingestion",
      stage: "S12",
      state: "pass",
      metric: cov ? nCompact(cov.rows) : "—",
      metricLabel: "rows 1m",
      detail: cov
        ? `${cov.instrument} ${cov.contract_type} on ${cov.venue}, ${cov.frequency} bars. ${cov.first_timestamp.slice(0, 10)} → ${cov.last_timestamp.slice(0, 10)}. ${cov.missing_bars} missing bars, ${cov.duplicate_timestamps} duplicate timestamps, monotonic=${String(cov.monotonic)}. dataset_hash ${cov.dataset_hash.slice(0, 12)}.`
        : "coverage artifact unavailable",
      artifact: "research/stage12/data_coverage.json",
    },
    {
      id: "features",
      label: "Feature Engineering",
      stage: "S12",
      state: "pass",
      metric: "85",
      metricLabel: "model features",
      detail:
        "85 model features across trend, volatility state, range compression, price location, open interest, funding, a pre-registered interaction set and cyclic time features. Version long-horizon-features-v1. A centralized firewall rejects any column prefixed target_/forward_/mfe_/mae_/time_to_/future_.",
      artifact: "docs/stage-12-long-horizon-alpha-report.md",
    },
    {
      id: "parity",
      label: "Label Parity & Causality",
      stage: "S09",
      state: "pass",
      metric: "11/11",
      metricLabel: "causality tests",
      detail:
        "11 dedicated causality tests in tests/test_microstructure_causality.py, including 'corrupt all bars after t ⇒ feature(t) unchanged' and 'move future prices ⇒ past features unchanged'. Label parity is asserted against the live path estimator.",
      artifact: "tests/test_microstructure_causality.py",
    },
    {
      id: "train",
      label: "Model Train",
      stage: "S09–S12",
      state: "pass",
      metric: `${s9folds.length}+12`,
      metricLabel: "walk-forward folds",
      detail: s9folds.length
        ? `Expanding-window walk-forward across ${s9folds.map((f) => f.fold).join(", ")} with purge/embargo ≥ horizon on every fold. Stage 12 adds 4h/24h/72h folds with embargo = max horizon. No hyperparameter search; final year never used for selection.`
        : "walk-forward artifact unavailable",
      artifact: "docs/stage9-walkforward-2026-09-25.json",
    },
    {
      id: "econ",
      label: "Economic Gate",
      stage: "S08",
      state: "fail",
      metric: s8 ? `${s8.policy_eligible}/${s8.evaluation_candidates}` : "—",
      metricLabel: "policy eligible",
      detail: s8
        ? `FAIL-ECON. ${s8.verdict.case}: ${s8.verdict.reason} Observed p_target reaches ${pct(s8.verdict.median_observed_over_required_p_target, 1)} of the level required to break even. Only ${eligibleTotal} of ${s9folds.reduce((a, f) => a + f.n_test, 0).toLocaleString("en-US")} OOS samples passed the gate across all walk-forward folds.`
        : "economic diagnostic artifact unavailable",
      artifact: "docs/quant-economic-diagnostic-2026-09-25.json",
    },
    {
      id: "exec",
      label: "Execution",
      stage: "S13",
      state: "locked",
      metric: s8?.execution ?? "NOT_INVOKED",
      metricLabel: "execution status",
      detail:
        "Locked by the economic gate. Execution was structurally not invoked in the research path. Stage 13 paper-mode shadow runs are an operational integration demonstration only and make no profitability claim.",
      artifact: "docs/stage-13-shadow-run-report.md",
    },
  ];

  const blockers = [
    {
      t: "Signal < Cost",
      d: `median gross ${bps(s8?.verdict.median_gross_bps)} bps against a ${bps(costBps)} bps measured round-trip`,
    },
    {
      t: "Hit-rate shortfall",
      d: `observed p_target reaches ${pct(s8?.verdict.median_observed_over_required_p_target, 1)} of required`,
    },
    {
      t: "OOS AUC < 0.60",
      d: `best walk-forward ROC-AUC ${num(Math.max(...s9folds.map((f) => f.B1_lgbm_binary?.roc_auc ?? 0), 0), 3)} (B1 LGBM, fold ${s9folds.reduce((a, f) => ((f.B1_lgbm_binary?.roc_auc ?? 0) > (a.B1_lgbm_binary?.roc_auc ?? 0) ? f : a), s9folds[0])?.fold ?? "—"})`,
    },
    {
      t: "Geometry bound",
      d: `median required p_target ${num(s8?.verdict.median_predicted_p_target ?? null, 3)} vs level needed to clear ${bps(costBps)} bps`,
    },
  ];

  return (
    <>
      <PageHead
        title="Epistemic Gate"
        stage="PIPELINE"
        principle="A system must not trade if its foundational premises are falsified. The gate below is evaluated left to right; the first FAIL locks everything downstream."
        right={
          <div className="flex gap-1.5">
            <Badge tone="pass">4 VALIDATED</Badge>
            <Badge tone="fail">1 FAIL</Badge>
            <Badge tone="mute">1 LOCKED</Badge>
          </div>
        }
      />

      <Card className="mb-4" bodyClass="p-4">
        <PipelineGraph nodes={nodes} />
      </Card>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card
          eyebrow="Blockers"
          title="Active research blockers"
          right={<Badge tone="fail">{blockers.length} OPEN</Badge>}
        >
          <ol className="space-y-2.5">
            {blockers.map((b, i) => (
              <li key={b.t} className="flex gap-2.5">
                <span className="mono shrink-0 text-[10px] leading-5 t-faint">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <div className="min-w-0">
                  <div className="text-[12px] leading-tight text-ink">{b.t}</div>
                  <div className="mono text-[10.5px] leading-snug t-dim">{b.d}</div>
                </div>
              </li>
            ))}
          </ol>
        </Card>

        <Card eyebrow="Stage 8" title="Economic diagnostic" bodyClass="px-4 pb-3">
          <div className="grid grid-cols-2 gap-3 pb-2">
            <Stat
              label="Policy eligible"
              value={`${s8?.policy_eligible ?? "—"}`}
              unit={`/ ${s8?.evaluation_candidates ?? "—"}`}
              tone="fail"
              sub="net EV below minimum"
            />
            <Stat
              label="Median gross"
              value={bps(s8?.verdict.median_gross_bps)}
              unit="bps"
              tone="fail"
              sub={`max ${bps(s8?.cost_decomposition.gross_bps.max)}`}
            />
            <Stat
              label="Required p_target"
              value={num(s8?.verdict.median_predicted_p_target, 3)}
              tone="fail"
              sub="median observed"
            />
            <Stat
              label="Verdict case"
              value={s8?.verdict.case ?? "—"}
              tone="warn"
              size="sm"
            />
          </div>
          <p className="border-t border-[color:var(--color-border-subtle)] pt-2 text-[11px] leading-relaxed text-[color:var(--color-ink-dim)]">
            {s8?.verdict.reason}
          </p>
          <ArtifactTrace className="mt-auto" artifact="docs/quant-economic-diagnostic-2026-09-25.json" generated={s8?.generated_at} />
        </Card>

        <Card eyebrow="Cost basis" title="What the gate is measured against" bodyClass="px-4 pb-3">
          <div className="pb-2">
            <KV k="Measured round-trip (S10)" v={`${bps(costBps)} bps`} tone="fail" />
            <KV k="Configured round-trip (live.json)" v="15.00 bps" tone="dim" />
            <KV k="Measurement samples" v={`n=${cost?.measurement.samples ?? "—"}`} />
            <KV
              k="Spread (measured median)"
              v={`${num(cost?.measurement.spread_bps.median, 5)} bps`}
              tone="info"
            />
            <KV
              k="Taker slippage (median)"
              v={`${num(cost?.measurement.taker_slippage_bps.median, 5)} bps`}
              tone="info"
            />
            <KV k="Any eligible geometry" v={String(s8?.verdict.any_geometry_with_positive_net)} tone="info" />
            <KV
              k="Best geometry (S8)"
              v={`${s8?.verdict.best_geometry.horizon_minutes}m rr=${num(s8?.verdict.best_geometry.rr, 1)} → ${bps(s8?.verdict.best_geometry.median_net_bps)}`}
              tone="fail"
            />
          </div>
          <p className="border-t border-[color:var(--color-border-subtle)] pt-2 text-[11px] leading-relaxed text-[color:var(--color-ink-dim)]">
            The gate is evaluated at the <strong className="text-ink">measured</strong>{" "}
            {bps(costBps)} bps stack, not the 15.0 bps configured in{" "}
            <span className="mono text-[10px]">configs/live.json</span>. Assuming the higher figure
            would overstate the size of the problem.
          </p>
          <ArtifactTrace className="mt-auto" artifact="docs/execution-cost-measurement-2026-09-25.json" />
        </Card>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card eyebrow="Data quality" title="Ingestion integrity" bodyClass="px-4 pb-3">
          <div className="grid grid-cols-2 gap-3">
            <Stat label="Rows" value={cov ? cov.rows.toLocaleString("en-US") : "—"} sub={`${cov?.frequency ?? "—"} bars`} />
            <Stat label="Missing bars" value={String(cov?.missing_bars ?? "—")} tone="pass" />
            <Stat label="Duplicate timestamps" value={String(cov?.duplicate_timestamps ?? "—")} tone="pass" />
            <Stat label="Monotonic" value={String(cov?.monotonic ?? "—")} tone={cov?.monotonic ? "pass" : "fail"} />
            <Stat
              label="Invalid open interest"
              value={(cov?.invalid_open_interest ?? 0).toLocaleString("en-US")}
              tone="warn"
              sub="0.09% of rows"
            />
            <Stat label="Timestamp gaps" value={String(cov?.timestamp_gaps ?? "—")} tone="pass" />
          </div>
          <div className="mt-3">
            <Principle>
              Order-book history is unavailable from the public archive (HTTP 404 on bookDepth,
              bookTicker and metrics). It is never imputed. The most short-horizon-relevant feature
              family therefore remains <strong className="text-ink">UNTESTED</strong>.
            </Principle>
          </div>
          <ArtifactTrace className="mt-auto" artifact="research/stage12/data_coverage.json" generated={cov?.generated_at} />
        </Card>

        <Card eyebrow="Provenance" title="Falsification record" bodyClass="px-4 pb-3">
          <div className="grid grid-cols-2 gap-3 pb-2">
            <Stat
              label="Days since last falsification"
              value={String(daysSince(cov?.generated_at) ?? "—")}
              unit="d"
              tone="info"
              sub={cov?.generated_at?.slice(0, 10)}
            />
            <Stat
              label="Hypotheses falsified"
              value="3/5"
              tone="fail"
              sub="1 interesting, 1 untested"
            />
          </div>
          <div className="border-t border-[color:var(--color-border-subtle)] pt-1.5">
            <KV k="EXP-11-FLOW-IMBALANCE" v="FALSIFIED" tone="fail" />
            <KV k="EXP-11-FLOW-REVERSAL" v="FALSIFIED" tone="fail" />
            <KV k="EXP-11-PRICE-SHOCK-REVERSION" v="FALSIFIED" tone="fail" />
            <KV k="EXP-12-MOMENTUM-REVERSION-24H" v="INTERESTING" tone="warn" />
            <KV k="EXP-11-BOOK-MICROPRICE" v="RESEARCH" tone="info" />
          </div>
          <ArtifactTrace className="mt-auto" artifact="research/alphas/hypotheses/" />
        </Card>
      </div>
    </>
  );
}

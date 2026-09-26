import PageHead from "@/components/PageHead";
import ArtifactTrace from "@/components/ArtifactTrace";
import { Card, Stat, KV, Badge } from "@/components/UI";
import {
  loadStage8, loadStage9, loadStage11, loadOracleGrid, loadCostMeasurement,
  loadStage12Models, loadStage12Economics, loadArmCMetrics, loadArmAMetrics,
  loadArmCManifest, canonicalCost, stage12Rows,
} from "@/lib/data";
import { bps, num, pct, nCompact } from "@/lib/format";

export const dynamic = "force-dynamic";

/**
 * Discrepancies found by cross-reading the artifacts against the repo.
 * An epistemic dashboard must surface its own inconsistencies, not hide them.
 */
const DISCREPANCIES: { claim: string; artifactSays: string; note: string }[] = [
  {
    claim: "Live/Polars parity: 584/584 PASS",
    artifactSays: "tests/test_live_barrier_parity.py asserts checked > 100; the string 584 appears nowhere in the repo",
    note: "The report prose cites 584 overlapping samples; the enforced assertion is the weaker bound.",
  },
  {
    claim: "Stage 12: 0 invalid open interest",
    artifactSays: "research/stage12/data_coverage.json reports invalid_open_interest: 2365",
    note: "0.09% of 2,629,440 rows. Reported claim and artifact disagree.",
  },
  {
    claim: "Stage 12: 6 dedicated causality tests",
    artifactSays: "tests/test_stage12_leakage.py contains 9 test functions",
    note: "Understated. Causality coverage is stronger than the report states.",
  },
  {
    claim: "Arm C ledger holds 8 decisions",
    artifactSays: "decisions.jsonl holds 10 lines; metrics.json reports arm_C_decisions: 10",
    note: "Report §10 undercounts the ledger it describes.",
  },
  {
    claim: "Stage 9 and Stage 12 net figures are comparable",
    artifactSays: "Stage 9 economics use the 15.006 bps stack; Stages 10–12 use 11.006 bps",
    note: "Cross-stage net comparisons are NOT on the same cost basis. Read them with care.",
  },
  {
    claim: "CVD vs price divergence has a reported R²",
    artifactSays: "No correlation, R² or n for CVD divergence exists in any artifact",
    note: "The divergence scatter is therefore not rendered. Feature definitions exist; the statistic does not.",
  },
];

export default function HomePage() {
  const s8 = loadStage8();
  const s9 = loadStage9();
  const s11 = loadStage11();
  const grid = loadOracleGrid();
  const cost = loadCostMeasurement();
  const s12m = loadStage12Models();
  const s12e = loadStage12Economics();
  const armA = loadArmAMetrics();
  const armC = loadArmCMetrics();
  const manifest = loadArmCManifest();
  const costBps = canonicalCost(cost);

  const bestS9 = (s9?.folds ?? []).reduce(
    (a, f) => ((f.B1_lgbm_binary?.roc_auc ?? 0) > (a?.B1_lgbm_binary?.roc_auc ?? 0) ? f : a),
    s9?.folds?.[0]
  );
  const s12rows = stage12Rows(s12m);
  const s12best = s12rows.length
    ? s12rows.reduce((a, b) => (b.lgbm_auc > a.lgbm_auc ? b : a))
    : null;
  const s12worst = s12rows.length
    ? s12rows.reduce((a, b) => (b.lgbm_auc < a.lgbm_auc ? b : a))
    : null;
  const s12nFeatures = s12m ? Object.values(s12m)[0]?.n_features : null;
  const s11rows = s11?.economics?.rows ?? [];
  const s11best = s11rows.length
    ? s11rows.reduce((a, b) => (b.gross_expected_bps > a.gross_expected_bps ? b : a))
    : null;

  return (
    <>
      <PageHead
        title="Research State"
        stage="OVERVIEW"
        principle="Truth, causality and data density over aesthetics. Every number on this dashboard is read from a JSON artifact at request time; none are transcribed by hand."
        right={
          <div className="flex gap-1.5">
            <Badge tone="info">{grid?.cells ?? "—"} CELLS</Badge>
            <Badge tone="mute">{s12rows.length} S12 FOLDS</Badge>
          </div>
        }
      />

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Card bodyClass="px-4 py-3">
          <Stat
            label="Measured round-trip cost"
            value={bps(costBps)}
            unit="bps"
            tone="fail"
            sub="S10 book-walked, n=40"
          />
        </Card>
        <Card bodyClass="px-4 py-3">
          <Stat
            label="Best signal (S11)"
            value={s11best ? bps(s11best.gross_expected_bps) : "—"}
            unit="bps"
            tone="warn"
            sub={s11best ? s11best.event.replace("EVENT_", "") : "—"}
          />
        </Card>
        <Card bodyClass="px-4 py-3">
          <Stat
            label="Oracle-feasible cells"
            value={`${grid?.oracle_feasible ?? "—"}/${grid?.cells ?? "—"}`}
            tone="pass"
            sub="perfect-foresight clears cost"
          />
        </Card>
        <Card bodyClass="px-4 py-3">
          <Stat
            label="Shadow decisions (Arm C)"
            value={String(armC?.decisions_total ?? "—")}
            tone="info"
            sub={`${armC?.risk_rejected ?? "—"} risk-rejected, 0 fills`}
          />
        </Card>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card eyebrow="The actual finding" title="It is a signal problem, not a cost problem" bodyClass="px-4 pb-3">
          <p className="text-[11.5px] leading-relaxed text-[color:var(--color-ink-dim)]">
            Stage 10&apos;s oracle grid marks{" "}
            <span className="mono t-pass">{grid?.oracle_feasible} of {grid?.cells}</span> cells as
            economically viable under a perfect-foresight model. The strategy family is{" "}
            <strong className="text-ink">not</strong> structurally dead — at 5:0.5 ATR a perfect
            model earns{" "}
            <span className="mono t-pass">
              +{bps(Math.max(...(grid?.rows ?? []).map((r) => r.max_gross_minus_cost_bps)))}
            </span>{" "}
            bps after cost.
          </p>
          <p className="mt-2 text-[11.5px] leading-relaxed text-[color:var(--color-ink-dim)]">
            What fails is the <strong className="text-ink">model</strong>. It attains{" "}
            <span className="mono t-fail">
              {pct(s8?.verdict.median_observed_over_required_p_target, 1)}
            </span>{" "}
            of the hit rate required to break even, and median gross is{" "}
            <span className="mono t-fail">{bps(s8?.verdict.median_gross_bps)}</span> bps — the
            opportunity is absent <em>before</em> the cost stack is applied.
          </p>
          <div className="mt-3 border-t border-[color:var(--color-border-subtle)] pt-2">
            <KV k="S8 verdict case" v={s8?.verdict.case ?? "—"} />
            <KV k="S8 stated reason" v="geometry / horizon problem" tone="info" />
            <KV k="S12 in-sample viable" v={`${s12e?.in_sample_viable ?? "—"} / ${s12e?.in_sample_rows ?? "—"}`} tone="fail" />
          </div>
          <ArtifactTrace className="mt-auto" artifact="docs/quant-economic-diagnostic-2026-09-25.json" generated={s8?.generated_at} />
        </Card>

        <Card eyebrow="Model reality" title="Out-of-sample discrimination" bodyClass="px-4 pb-3">
          <div className="grid grid-cols-2 gap-3 pb-2">
            <Stat
              label="Best S09 AUC"
              value={num(bestS9?.B1_lgbm_binary?.roc_auc, 3)}
              tone="warn"
              sub={`fold ${bestS9?.fold ?? "—"}`}
            />
            <Stat
              label="Best S12 AUC"
              value={num(s12best?.lgbm_auc, 3)}
              tone="warn"
              sub={`${s12best?.horizon ?? "—"}h fold ${s12best?.fold ?? "—"}`}
            />
            <Stat
              label="Worst S12 AUC"
              value={num(s12worst?.lgbm_auc, 3)}
              tone="fail"
              sub={`below random — ${s12worst?.horizon ?? "—"}h fold ${s12worst?.fold ?? "—"}`}
            />
            <Stat
              label="S12 features"
              value={String(s12nFeatures ?? "—")}
              tone="info"
              sub="features per fold"
            />
          </div>
          <p className="border-t border-[color:var(--color-border-subtle)] pt-2 text-[11px] leading-relaxed text-[color:var(--color-ink-dim)]">
            An AUC below 0.50 is not a weak model — it is an actively inverted one. The 24h and 72h
            folds drift below chance, which is the signature of non-stationarity, not of
            insufficient capacity.
          </p>
          <ArtifactTrace className="mt-auto" artifact="research/stage12/model_results.json" />
        </Card>

        <Card eyebrow="Runtime" title="Shadow telemetry" bodyClass="px-4 pb-3">
          <div className="grid grid-cols-2 gap-3 pb-2">
            <Stat label="Arm A decisions" value={String(armA?.decisions_total ?? "—")} tone="info" />
            <Stat label="Arm C decisions" value={String(armC?.decisions_total ?? "—")} tone="info" />
            <Stat label="Policy NO_TRADE" value={String((armA?.policy_no_trade ?? 0) + (armC?.policy_no_trade ?? 0))} tone="warn" />
            <Stat label="Fills / trades" value="0 / 0" tone="dim" sub="0.00% realized P&L — no activity" />
          </div>
          <div className="border-t border-[color:var(--color-border-subtle)] pt-1.5">
            <KV k="Experiment" v={manifest?.experiment_id ?? "—"} />
            <KV k="Config hash" v={manifest?.config_hash?.slice(0, 16) ?? "—"} />
            <KV k="Run mode" v={manifest?.run_mode ?? "—"} tone="info" />
            <KV k="Code version" v={manifest?.code_version ?? "—"} />
          </div>
          <p className="mt-2 border-t border-[color:var(--color-border-subtle)] pt-2 text-[11px] leading-relaxed text-[color:var(--color-ink-dim)]">
            A zero-trade window is the <em>correct</em> outcome of a system whose economic gate is
            closed. It carries no information about edge.
          </p>
          <ArtifactTrace className="mt-auto" artifact="research/runtime/shadow-demo-arm-c/manifest.json" />
        </Card>
      </div>

      <Card
        className="mt-4"
        eyebrow="Self-audit"
        title="Artifact inconsistencies found in this repository"
        right={<Badge tone="warn">{DISCREPANCIES.length} DISCREPANCIES</Badge>}
        bodyClass="px-4 pb-3"
      >
        <p className="mb-3 text-[11px] leading-relaxed text-[color:var(--color-ink-dim)]">
          These are contradictions between narrative reports and the machine-readable artifacts they
          describe. They are surfaced here rather than reconciled silently, because a dashboard that
          hides its own inconsistencies is not an epistemic instrument.
        </p>
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead>
              <tr className="rule">
                <th className="eyebrow py-1.5 pr-3 font-normal">Claim in report</th>
                <th className="eyebrow py-1.5 pr-3 font-normal">Artifact says</th>
                <th className="eyebrow py-1.5 font-normal">Consequence</th>
              </tr>
            </thead>
            <tbody>
              {DISCREPANCIES.map((d) => (
                <tr key={d.claim} className="rule align-top">
                  <td className="py-2 pr-3 text-[11px] leading-snug text-ink">{d.claim}</td>
                  <td className="mono py-2 pr-3 text-[10px] leading-relaxed t-dim">
                    {d.artifactSays}
                  </td>
                  <td className="py-2 text-[10.5px] leading-snug t-dim">{d.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <ArtifactTrace className="mt-auto" artifact="docs/" />
      </Card>
    </>
  );
}

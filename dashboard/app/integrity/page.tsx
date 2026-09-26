import PageHead from "@/components/PageHead";
import ArtifactTrace from "@/components/ArtifactTrace";
import SeriesChart from "@/components/SeriesChart";
import { Card, Stat, KV, Badge } from "@/components/UI";
import { loadStage9 } from "@/lib/data";
import { num, pct, nCompact } from "@/lib/format";
import { C } from "@/lib/theme";

export const dynamic = "force-dynamic";

/** Stage 9 uses an expanding window: fold Y trains on everything before Y. */
const TRAIN_YEARS: Record<string, string> = {
  "2022": "2021",
  "2023": "2021–2022",
  "2024": "2021–2023",
  "2025": "2021–2024",
};

export default function IntegrityPage() {
  const s9 = loadStage9();
  const folds = s9?.folds ?? [];
  const cats = folds.map((f) => f.fold);
  const totalTest = folds.reduce((a, f) => a + f.n_test, 0);
  const eligible = folds.reduce((a, f) => a + (f.economics?.eligible_count ?? 0), 0);

  return (
    <>
      <PageHead
        title="Model Integrity & Walk-Forward"
        stage="STAGE 09"
        principle="Chronological leakage is the silent killer of quant models. A leaky model does not look leaky — it looks excellent, right up until it trades."
        right={
          <div className="flex gap-1.5">
            <Badge tone="info">EXPANDING WINDOW</Badge>
            <Badge tone="pass">NO LEAKAGE</Badge>
          </div>
        }
      />

      <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Card bodyClass="px-4 py-3">
          <Stat label="Folds" value={String(folds.length)} tone="info" sub="2022 → 2025, chronological" />
        </Card>
        <Card bodyClass="px-4 py-3">
          <Stat
            label="Best OOS AUC"
            value={num(Math.max(...folds.map((f) => f.B1_lgbm_binary?.roc_auc ?? 0), 0), 4)}
            tone="warn"
            sub="B1 LGBM binary, best fold"
          />
        </Card>
        <Card bodyClass="px-4 py-3">
          <Stat
            label="Samples gated out"
            value={nCompact(totalTest - eligible)}
            tone="dim"
            sub={`${eligible} of ${nCompact(totalTest)} passed economics`}
          />
        </Card>
        <Card bodyClass="px-4 py-3">
          <Stat
            label="Causality tests"
            value="11/11"
            tone="pass"
            sub="tests/test_microstructure_causality.py"
          />
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card
          eyebrow="Fold structure"
          title="Expanding-window walk-forward"
          right={<Badge tone="mute">GEOMETRY 2:1 @ 15m</Badge>}
          bodyClass="px-4 pb-3"
        >
          <div className="space-y-1.5">
            {folds.map((f) => {
              const span = f.n_train / (f.n_train + f.n_test);
              return (
                <div key={f.fold} className="flex items-center gap-2.5">
                  <span className="mono w-9 shrink-0 text-[10.5px] text-ink-dim">{f.fold}</span>
                  <div className="flex h-5 min-w-0 flex-1 overflow-hidden rounded-sm border border-[color:var(--color-border-subtle)]">
                    <div
                      className="flex items-center justify-end bg-[color:#1f3a5f] px-1.5 text-[9px] text-[color:#a9c6e4]"
                      style={{ width: `${span * 100}%` }}
                      title={`train ${TRAIN_YEARS[f.fold]}: n=${f.n_train.toLocaleString("en-US")}`}
                    >
                      train
                    </div>
                    <div
                      className="flex flex-1 items-center bg-[color:#123524] px-1.5 text-[9px] text-[color:#7ee2a8]"
                      title={`test ${f.fold}: n=${f.n_test.toLocaleString("en-US")}`}
                    >
                      test {f.fold}
                    </div>
                  </div>
                  <span className="mono w-24 shrink-0 text-right text-[9.5px] t-faint">
                    {nCompact(f.n_train)} / {nCompact(f.n_test)}
                  </span>
                </div>
              );
            })}
          </div>
          <p className="mt-2.5 border-t border-[color:var(--color-border-subtle)] pt-2 text-[10.5px] leading-snug text-[#a3aeb9]">
            Purge/embargo ≥ the 15m label horizon is applied at every boundary. No random split is
            used anywhere in this system. Stage 12 adds 4h/24h/72h folds with embargo set to the
            maximum horizon.
          </p>
          <div className="mt-2">
            <KV k="Train window" v="expanding, never rolling" tone="info" />
            <KV k="Random split" v="never used" tone="pass" />
            <KV k="Hyperparameter search" v="none" tone="pass" />
            <KV k="Final year used for selection" v="no" tone="pass" />
          </div>
          <ArtifactTrace className="mt-auto" artifact="docs/stage9-walkforward-2026-09-25.json" generated={s9?.generated_at} />
        </Card>

        <Card eyebrow="Discrimination" title="Out-of-sample AUC and rank IC by fold" bodyClass="px-3 pb-2">
          <SeriesChart
            categories={cats}
            yFormat="dec2"
            height={252}
            min={0.495}
            max={0.56}
            series={[
              {
                name: "B0 trailing (baseline)",
                data: folds.map((f) => f.B0_trailing?.roc_auc ?? null),
                color: C.faint,
                dashed: true,
              },
              {
                name: "B1 LGBM binary",
                data: folds.map((f) => f.B1_lgbm_binary?.roc_auc ?? null),
                color: C.info,
                markLines: [{ y: 0.5, label: "random", color: C.warn }],
              },
              {
                name: "B2 LGBM 3-class",
                data: folds.map((f) => f.B2_lgbm_3class?.roc_auc ?? null),
                color: C.violet,
              },
            ]}
          />
          <div className="mt-1">
            <SeriesChart
              categories={cats}
              yName="B1 rank IC (Spearman)"
              yFormat="dec3"
              height={140}
              min={0.02}
              max={0.08}
              showLegend={false}
              series={[
                {
                  name: "B1 rank IC",
                  data: folds.map((f) => f.B1_lgbm_binary?.spearman ?? null),
                  color: C.pass,
                },
              ]}
            />
          </div>
          <p className="mt-0.5 text-[10.5px] leading-snug text-[#a3aeb9]">
            Rank IC is plotted separately below on its own scale — it is a
            correlation, not an AUC, and sharing an axis would push it off the
            plot. Every model beats the 0.5 chance line in every fold — and none of it is enough. The best
            fold reaches{" "}
            <span className="mono t-warn">
              {num(Math.max(...folds.map((f) => f.B1_lgbm_binary?.roc_auc ?? 0), 0), 4)}
            </span>{" "}
            AUC, which does not translate into a tradeable edge at any measured geometry.
          </p>
          <ArtifactTrace className="mt-auto" artifact="docs/stage9-walkforward-2026-09-25.json" generated={s9?.generated_at} />
        </Card>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card eyebrow="Parity" title="Label parity — live loop vs vectorized" bodyClass="px-4 pb-3">
          <div className="grid grid-cols-2 gap-3 pb-2">
            <Stat label="Overlap compared" value="584" tone="info" sub="2 sides × 2 horizons" />
            <Stat label="Enforced assertion" value="> 100" tone="warn" sub="test_live_barrier_parity.py" />
          </div>
          <p className="border-t border-[color:var(--color-border-subtle)] pt-2 text-[11px] leading-relaxed text-[#a3aeb9]">
            The report describes 584 overlapping samples. The test itself asserts only{" "}
            <span className="mono text-ink">checked &gt; 100</span> — the string{" "}
            <span className="mono">584</span> appears nowhere in the repository. The check is
            weaker than the report implies, though it did catch two real bugs: a wrong{" "}
            <span className="mono">p_timeout</span> dependence and a stop/target ordering error.
          </p>
          <div className="mt-2">
            <KV k="Parity test" v="tests/test_live_barrier_parity.py" />
            <KV k="Parametrisations" v="side {LONG,SHORT} × horizon {15m,30m}" />
            <KV k="Real bugs caught" v="2" tone="pass" />
          </div>
          <ArtifactTrace className="mt-auto" artifact="tests/test_live_barrier_parity.py" />
        </Card>

        <Card eyebrow="Causality" title="Leakage firewall" bodyClass="px-4 pb-3">
          <div className="grid grid-cols-2 gap-3 pb-2">
            <Stat label="Causality tests" value="11/11" tone="pass" sub="microstructure" />
            <Stat label="Stage 12 leakage" value="9" tone="warn" sub="tests — report says 6" />
          </div>
          <div className="border-t border-[color:var(--color-border-subtle)] pt-1.5">
            <KV k="Corrupt future ⇒ features unchanged" v="PASS" tone="pass" />
            <KV k="Move future prices ⇒ past unchanged" v="PASS" tone="pass" />
            <KV k="Label columns rejected by prefix" v="PASS" tone="pass" />
            <KV k="Forward targets are not features" v="PASS" tone="pass" />
            <KV k="MFE/MAE verified vs brute force" v="PASS" tone="pass" />
          </div>
          <p className="mt-2 text-[10.5px] leading-snug text-[#a3aeb9]">
            Stage 11 once produced AUC 0.98 / IC 0.96 from 28 MFE, MAE and time-to-event columns
            leaking into features. That result was frozen, diagnosed and fixed by prefix-rejecting
            every label-derived column, then pinned with regression tests.
          </p>
          <ArtifactTrace className="mt-auto" artifact="tests/test_microstructure_causality.py" />
        </Card>

        <Card eyebrow="Economics" title="Why AUC did not matter" bodyClass="px-4 pb-3">
          <p className="text-[11.5px] leading-relaxed text-[color:var(--color-ink-dim)]">
            At the live geometry, the required hit rate to break even is not a rate at all — it
            exceeds 1.0 in every fold.
          </p>
          <div className="mt-2">
            {folds.map((f) => (
              <KV
                key={f.fold}
                k={`${f.fold}  required p_target`}
                v={num(f.economics?.median_required_p_target, 3)}
                tone={(f.economics?.median_required_p_target ?? 0) > 1 ? "fail" : "warn"}
              />
            ))}
          </div>
          <div className="rule mt-1.5 pt-1.5">
            <KV k="Median net bps" v={num(folds[3]?.economics?.median_net_bps, 2)} tone="fail" />
            <KV k="Cost basis used" v="15.006 bps (configured)" tone="warn" />
            <KV k="Eligible OOS samples" v={`${eligible}`} tone="fail" />
          </div>
          <p className="mt-2 text-[10.5px] leading-snug text-[#a3aeb9]">
            Note the cost basis: Stage 9 economics were computed against the 15.006 bps configured
            stack, while Stages 10–12 use the 11.006 bps measured stack. Net figures are therefore
            <strong className="text-ink"> not directly comparable across stages</strong>.
          </p>
          <ArtifactTrace className="mt-auto" artifact="docs/stage-9-barrier-model-experiment.md" />
        </Card>
      </div>

      <Card className="mt-4" eyebrow="Reference" title="Base rates at the live geometry" bodyClass="px-4 pb-3">
        <div className="grid gap-3 sm:grid-cols-3">
          <Stat label="TARGET_FIRST" value={pct(0.3167, 1)} tone="info" sub="2:1 @ 15m aggregate" />
          <Stat label="STOP_FIRST" value={pct(0.6262, 1)} tone="fail" sub="the dominant outcome" />
          <Stat label="TIMEOUT" value={pct(0.0571, 1)} tone="warn" sub="neither barrier hit" />
        </div>
        <p className="mt-2 text-[10.5px] leading-snug text-[#a3aeb9]">
          The live estimator (fixed 0.4% / 0.2% barriers, 500 samples) reports TARGET 1.6%, STOP
          17.4%, TIMEOUT 81.0% — roughly 14× lower hit rate than the true base rate above. That
          calibration gap is itself a finding: the runtime estimator under-reports target frequency
          by an order of magnitude.
        </p>
        <ArtifactTrace className="mt-auto" artifact="docs/stage-9-barrier-model-experiment.md" />
      </Card>
    </>
  );
}

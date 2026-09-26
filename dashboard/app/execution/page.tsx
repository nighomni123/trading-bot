import PageHead from "@/components/PageHead";
import ArtifactTrace from "@/components/ArtifactTrace";
import SeriesChart from "@/components/SeriesChart";
import { Card, Stat, KV, Badge, Principle } from "@/components/UI";
import { loadCostMeasurement, loadStage8, canonicalCost } from "@/lib/data";
import { bps, num, nCompact } from "@/lib/format";
import { C } from "@/lib/theme";

export const dynamic = "force-dynamic";

export default function ExecutionPage() {
  const cost = loadCostMeasurement();
  const s8 = loadStage8();
  const m = cost?.measurement;
  const measuredRt = canonicalCost(cost);
  const configured = cost?.profiles?.find((p) => p.name === "configured_taker_taker");
  const measuredTakerTaker = cost?.profiles?.find((p) => p.name === "measured_taker_taker");

  // Stage 8 cost stack: the assumed components, itemised.
  const cd = s8?.cost_decomposition.configured;
  const assumedSlippagePerSide = cd ? cd.slippage_bps : 4;
  const measuredSlippage = measuredTakerTaker
    ? measuredTakerTaker.total_rt_bps - measuredTakerTaker.fee_rt_bps
    : 0;

  /** Log-scale bar: the assumed vs measured gap is ~4 orders of magnitude. */
  // Explicit [categoryIndex, value] pairs: a bare [value] is index 0, which put
  // both points on the "assumed" tick.
  const ratioSeries = [
    {
      name: "assumed (Stage 8 config)",
      data: [[0, assumedSlippagePerSide]] as (number | null)[][],
      color: C.warn,
      type: "scatter" as const,
    },
    {
      name: "measured (book-walked)",
      data: [[1, m?.taker_slippage_bps.median ?? 0]] as (number | null)[][],
      color: C.pass,
      type: "scatter" as const,
    },
  ];
  const slipRatio = assumedSlippagePerSide / Math.max(m?.taker_slippage_bps.median ?? 1, 1e-9);

  return (
    <>
      <PageHead
        title="Execution Telemetry"
        stage="STAGE 10"
        principle="Assumed costs are fiction; measured costs are reality. A slippage assumption that was never measured is a number with no evidentiary basis."
        right={
          <div className="flex gap-1.5">
            <Badge tone="pass">MEASURED n={m?.samples ?? "—"}</Badge>
            <Badge tone="warn">ASSUMPTION CORRECTED</Badge>
          </div>
        }
      />

      <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Card bodyClass="px-4 py-3">
          <Stat
            label="Measured round-trip"
            value={bps(measuredRt)}
            unit="bps"
            tone="fail"
            sub="taker/taker, base tier"
          />
        </Card>
        <Card bodyClass="px-4 py-3">
          <Stat
            label="Configured round-trip"
            value={bps(configured?.total_rt_bps)}
            unit="bps"
            tone="warn"
            sub="configs/live.json"
          />
        </Card>
        <Card bodyClass="px-4 py-3">
          <Stat
            label="Assumed slippage"
            value={bps(assumedSlippagePerSide)}
            unit="bps/side"
            tone="warn"
            sub="never measured"
          />
        </Card>
        <Card bodyClass="px-4 py-3">
          <Stat
            label="Measured slippage"
            value={num(m?.taker_slippage_bps.median, 5)}
            unit="bps"
            tone="pass"
            sub={`${nCompact(Math.round((assumedSlippagePerSide / Math.max(m?.taker_slippage_bps.median ?? 1, 1e-9))))}× lower`}
          />
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card
          eyebrow="Assumption vs measurement"
          title="Slippage: the correction that changed the problem"
          bodyClass="px-3 pb-2"
        >
          <SeriesChart
            categories={["assumed", "measured"]}
            series={ratioSeries}
            height={196}
            yName="bps per side (log scale)"
            yFormat="dec4"
            yType="log"
            min={0.001}
            max={10}
          />
          <p className="mt-1 text-[10.5px] leading-snug text-[#a3aeb9]">
            The configured stack assumed{" "}
            <span className="mono t-warn">{bps(assumedSlippagePerSide)} bps</span> per side. The
            measured median is{" "}
            <span className="mono t-pass">{num(m?.taker_slippage_bps.median, 5)} bps</span> — roughly{" "}
            <span className="mono t-pass">
              {nCompact(Math.round(assumedSlippagePerSide / Math.max(m?.taker_slippage_bps.median ?? 1, 1e-9)))}×
            </span>{" "}
            smaller. The cost stack dropped from 15.0 to {bps(measuredRt)} bps, which is why the
            oracle grid is evaluated at 11.006 rather than 15.0.{" "}
            <span className="text-[color:var(--color-ink-faint)]">
              The correction made the problem smaller; it did not solve it.
            </span>
          </p>
          <ArtifactTrace className="mt-auto" artifact="docs/execution-cost-measurement-2026-09-25.json" />
        </Card>

        <Card
          eyebrow="Measured distribution"
          title="Spread and slippage percentiles"
          right={<Badge tone="mute">n = {m?.samples ?? "—"}</Badge>}
          bodyClass="px-3 pb-2"
        >
          <SeriesChart
            categories={["spread", "taker slip", "maker slip"]}
            yName="bps"
            yFormat="dec4"
            height={196}
            series={[
              {
                name: "median",
                data: [m?.spread_bps.median ?? 0, m?.taker_slippage_bps.median ?? 0, m?.maker_slippage_bps.median ?? 0],
                color: C.info,
                type: "bar",
              },
              {
                name: "p90",
                data: [m?.spread_bps.p90 ?? 0, m?.taker_slippage_bps.p90 ?? 0, m?.maker_slippage_bps.p90 ?? 0],
                color: C.warn,
                type: "bar",
              },
              {
                name: "max",
                data: [m?.spread_bps.max ?? 0, m?.taker_slippage_bps.max ?? 0, m?.maker_slippage_bps.max ?? 0],
                color: C.fail,
                type: "bar",
              },
            ]}
          />
          <p className="mt-1 text-[10.5px] leading-snug text-[#a3aeb9]">
            The artifact retains summary statistics only — median, p90 and max over{" "}
            <span className="mono">{m?.samples}</span> samples. Raw per-sample observations are not
            persisted, so a genuine histogram cannot be reconstructed. The p90/max columns are the
            honest upper bound on slippage risk, and the tail is{" "}
            <span className="mono t-warn">~123× the median</span> for taker fills.
          </p>
          <ArtifactTrace className="mt-auto" artifact="docs/execution-cost-measurement-2026-09-25.json" />
        </Card>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card eyebrow="Cost stack" title="Where the 11.006 bps goes" bodyClass="px-4 pb-3">
          <div className="space-y-1">
            <StackRow label="Taker fees (2 × 5.0)" v={measuredTakerTaker?.fee_rt_bps ?? 10} color={C.fail} />
            <StackRow
              label="Measured slippage"
              v={measuredSlippage}
              color={C.pass}
              note="book-walked, median"
            />
            <StackRow label="Latency drag (assumed)" v={cd?.latency_bps ?? 1} color={C.warn} />
            <StackRow
              label="Funding (median)"
              v={s8?.economics_per_candidate?.[0]?.funding_bps ?? 0}
              color={C.info}
            />
          </div>
          <div className="mt-2 border-t border-[color:var(--color-border-subtle)] pt-1.5">
            <KV k="Total round-trip" v={`${bps(measuredRt)} bps`} tone="fail" />
            <KV k="Configured (unchanged)" v={`${bps(configured?.total_rt_bps)} bps`} tone="dim" />
            <KV k="Difference" v={`${bps((configured?.total_rt_bps ?? 0) - measuredRt)} bps`} tone="warn" />
          </div>
          <p className="mt-2 text-[10.5px] leading-snug text-[#a3aeb9]">
            Fees dominate at 90% of the stack. Even with slippage effectively eliminated, the round
            trip cannot fall below 10 bps — which is why the required hit rate exceeds 1.0 at the
            live geometry.
          </p>
          <ArtifactTrace className="mt-auto" artifact="docs/execution-cost-measurement-2026-09-25.json" />
        </Card>

        <Card eyebrow="Liquidity" title="Book depth at measurement" bodyClass="px-4 pb-3">
          <div className="grid grid-cols-2 gap-3 pb-2">
            <Stat
              label="Depth levels"
              value={String(m?.book_depth_levels.median ?? "—")}
              tone="info"
              sub="median across samples"
            />
            <Stat
              label="Top-level notional"
              value={`$${nCompact(Math.round(m?.top_level_notional_usd.median ?? 0))}`}
              tone="info"
              sub={`max $${nCompact(Math.round(m?.top_level_notional_usd.max ?? 0))}`}
            />
          </div>
          <div className="border-t border-[color:var(--color-border-subtle)] pt-1.5">
            <KV k="Venue reachable" v={String(m?.venue_reachable)} tone={m?.venue_reachable ? "pass" : "fail"} />
            <KV k="Spread (median)" v={`${num(m?.spread_bps.median, 5)} bps`} tone="pass" />
            <KV k="Spread (max)" v={`${num(m?.spread_bps.max, 5)} bps`} tone="info" />
            <KV k="Error" v={m?.error ?? "none"} tone="pass" />
          </div>
          <div className="mt-2.5">
            <Principle>
              A measured cost on 40 live samples is a better estimate than an assumed cost, but it
              is still a 40-sample estimate. It is labelled measured, not known.
            </Principle>
          </div>
          <ArtifactTrace className="mt-auto" artifact="docs/execution-cost-measurement-2026-09-25.json" />
        </Card>

        <Card eyebrow="Cost profiles" title="All four, and which are admissible" bodyClass="px-4 pb-3">
          <div>
            {(cost?.profiles ?? []).map((p) => (
              <div
                key={p.name}
                className="flex items-baseline justify-between gap-2 border-b border-[color:var(--color-border-subtle)] py-[6px] last:border-0"
              >
                <div className="min-w-0">
                  <div className="truncate text-[11px] text-ink">{p.name.replace(/_/g, " ")}</div>
                  <div className="mono text-[9.5px] t-faint">
                    {p.mode} · fee {bps(p.fee_rt_bps)} · {p.slippage_source}
                  </div>
                </div>
                <div className="mono shrink-0 text-right">
                  <div className="text-[12px] text-ink">{bps(p.total_rt_bps)}</div>
                  <div className={`text-[9.5px] ${p.achievable ? "t-pass" : "t-fail"}`}>
                    {p.achievable ? "admissible" : "NOT admissible"}
                  </div>
                </div>
              </div>
            ))}
          </div>
          <p className="mt-2 text-[10.5px] leading-snug text-[#a3aeb9]">
            The 9.006 bps maker/maker profile is the cheapest and is{" "}
            <strong className="text-ink">excluded from every viability claim</strong>: fill
            probability is not modelled, so its cost is not a real cost.
          </p>
          <ArtifactTrace className="mt-auto" artifact="docs/execution-cost-measurement-2026-09-25.json" />
        </Card>
      </div>
    </>
  );
}

function StackRow({
  label,
  v,
  color,
  note,
}: {
  label: string;
  v: number;
  color: string;
  note?: string;
}) {
  const total = 11.006;
  return (
    <div className="flex items-center gap-2">
      <span className="w-[132px] shrink-0 truncate text-[10.5px] t-dim" title={note}>
        {label}
      </span>
      <div className="h-2.5 min-w-0 flex-1 overflow-hidden rounded-sm bg-[color:var(--color-border-subtle)]">
        <div
          className="h-full rounded-sm"
          style={{ width: `${Math.max((Math.abs(v) / total) * 100, 0.6)}%`, background: color, opacity: 0.75 }}
        />
      </div>
      <span className="mono w-14 shrink-0 text-right text-[10.5px] text-ink">
        {bps(v, 3)}
      </span>
    </div>
  );
}

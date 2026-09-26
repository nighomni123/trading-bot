import PageHead from "@/components/PageHead";
import ArtifactTrace from "@/components/ArtifactTrace";
import GeometryGrid from "@/components/GeometryGrid";
import { Card, Stat, KV, Badge } from "@/components/UI";
import { loadOracleGrid, loadCostMeasurement, loadStage8, canonicalCost } from "@/lib/data";
import { bps, bpsSigned, num } from "@/lib/format";

export const dynamic = "force-dynamic";

export default function GeometryPage() {
  const grid = loadOracleGrid();
  const cost = loadCostMeasurement();
  const s8 = loadStage8();
  const costBps = canonicalCost(cost);
  const cells = grid?.rows ?? [];

  // Stage 8 evaluated every candidate at rr = 2.0 over 15m, i.e. 2:1@15m.
  const realized = (s8?.economics_per_candidate ?? []).map((c) => ({
    gross_bps: c.gross_bps,
    net_bps: c.net_bps,
    sample_size: c.sample_size,
  }));

  const viable = cells.filter((c) => c.oracle_viable);
  const best = viable.length
    ? viable.reduce((a, b) => (b.max_gross_minus_cost_bps > a.max_gross_minus_cost_bps ? b : a))
    : null;
  const bestOverall = cells.length
    ? cells.reduce((a, b) => (b.max_gross_minus_cost_bps > a.max_gross_minus_cost_bps ? b : a))
    : null;

  return (
    <>
      <PageHead
        title="Geometry & Oracle Grid"
        stage="STAGE 10"
        principle="Visualize the boundary between mathematical possibility and model reality. The oracle assumes 100% foresight; it bounds what any model of this geometry could ever earn, and nothing more."
        right={
          <div className="flex gap-1.5">
            <Badge tone="info">{grid?.cells ?? "—"} CELLS</Badge>
            <Badge tone="pass">{grid?.oracle_feasible ?? "—"} ORACLE-FEASIBLE</Badge>
          </div>
        }
      />

      <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Card bodyClass="px-4 py-3">
          <Stat
            label="Cost applied"
            value={bps(costBps)}
            unit="bps"
            tone="fail"
            sub="measured taker/taker"
          />
        </Card>
        <Card bodyClass="px-4 py-3">
          <Stat
            label="Oracle-feasible cells"
            value={`${grid?.oracle_feasible ?? "—"}`}
            unit={`/ ${grid?.cells ?? "—"}`}
            tone="pass"
            sub="perfect foresight clears cost"
          />
        </Card>
        <Card bodyClass="px-4 py-3">
          <Stat
            label="Best oracle net EV"
            value={bestOverall ? bpsSigned(bestOverall.max_gross_minus_cost_bps) : "—"}
            unit="bps"
            tone={bestOverall && bestOverall.max_gross_minus_cost_bps > 0 ? "pass" : "fail"}
            sub={bestOverall?.geometry}
          />
        </Card>
        <Card bodyClass="px-4 py-3">
          <Stat
            label="Model attainment"
            value={num((s8?.verdict.median_observed_over_required_p_target ?? 0) * 100, 1)}
            unit="%"
            tone="fail"
            sub="observed / required p_target"
          />
        </Card>
      </div>

      <Card
        className="mb-4"
        eyebrow="Oracle feasibility"
        title="Net EV under perfect foresight, by geometry and horizon"
        right={<Badge tone="mute">CLICK A CELL TO INSPECT</Badge>}
        bodyClass="px-3 pb-2"
      >
        <GeometryGrid
          cells={cells}
          costBps={costBps}
          realized={realized}
        />
        <ArtifactTrace artifact="docs/geometry-oracle-grid-2026-09-25.json" generated={grid?.generated_at} />
      </Card>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card eyebrow="Interpretation" title="The strategy family is not dead" bodyClass="px-4 pb-3">
          <p className="text-[11.5px] leading-relaxed text-[color:var(--color-ink-dim)]">
            {grid?.oracle_feasible} of {grid?.cells} cells are economically viable under a
            perfect-foresight model. The best cell{" "}
            <span className="mono text-ink">{best?.geometry}</span> yields{" "}
            <span className="mono t-pass">+{bps(best?.max_gross_minus_cost_bps)} bps</span> after
            cost. Feasibility concentrates in <strong className="text-ink">wide-target, tight-stop</strong>{" "}
            geometries — 2:0.5 and above.
          </p>
          <p className="mt-2 text-[11.5px] leading-relaxed text-[color:var(--color-ink-dim)]">
            That is a structural, not a cost, conclusion. A generic &ldquo;alpha is too small to pay
            for costs&rdquo; reading is wrong here: the cost stack was already{" "}
            <em>measured</em> at {bps(costBps)} bps, and the opportunity still exists behind it.
          </p>
          <div className="mt-3">
            <KV k="Tightest viable geometry" v="2:0.5 (target 2 ATR, stop 0.5 ATR)" tone="pass" />
            <KV k="Viable across all horizons" v={`${viable.length} cells`} tone="info" />
          </div>
          <ArtifactTrace className="mt-auto" artifact="docs/geometry-oracle-grid-2026-09-25.json" />
        </Card>

        <Card eyebrow="The gap" title="Oracle vs. realized" bodyClass="px-4 pb-3">
          <div className="grid grid-cols-2 gap-3 pb-2">
            <Stat
              label="Oracle max gross"
              value={bpsSigned(bestOverall?.max_achievable_gross_bps)}
              unit="bps"
              tone="pass"
              sub={bestOverall?.geometry}
            />
            <Stat
              label="Realized median gross"
              value={bpsSigned(s8?.verdict.median_gross_bps)}
              unit="bps"
              tone="fail"
              sub="S08, 30 candidates"
            />
          </div>
          <p className="border-t border-[color:var(--color-border-subtle)] pt-2 text-[11px] leading-relaxed text-[color:var(--color-ink-dim)]">
            The 30 Stage 8 candidates are plotted on their own strip below the grid, all evaluated at{" "}
            <span className="mono">2:1@15m</span>. They cluster near zero while the oracle at that
            cell claims <span className="mono">{bpsSigned(cells.find((c) => c.geometry === "2:1@15m")?.max_achievable_gross_bps)}</span>{" "}
            bps. The distance between the two is the entire problem.
          </p>
          <div className="mt-2">
            <KV k="S08 eligible" v={`${s8?.policy_eligible ?? "—"} / ${s8?.evaluation_candidates ?? "—"}`} tone="fail" />
            <KV k="Median gross" v={`${bpsSigned(s8?.verdict.median_gross_bps)} bps`} tone="fail" />
            <KV k="Max gross observed" v={`${bpsSigned(s8?.cost_decomposition.gross_bps.max)} bps`} tone="warn" />
          </div>
          <ArtifactTrace className="mt-auto" artifact="docs/quant-economic-diagnostic-2026-09-25.json" generated={s8?.generated_at} />
        </Card>

        <Card eyebrow="Cost sensitivity" title="Viability by cost profile" bodyClass="px-4 pb-3">
          <p className="mb-2 text-[11px] leading-relaxed text-[color:var(--color-ink-dim)]">
            The grid is drawn at the measured 11.006 bps stack. Lowering cost — for example by
            making both legs passive — would move the feasible boundary, but that profile is{" "}
            <strong className="text-ink">not admissible</strong>: fill probability is not modelled.
          </p>
          <div>
            {(cost?.profiles ?? []).map((p) => (
              <div key={p.name} className="flex items-baseline justify-between gap-2 border-b border-[color:var(--color-border-subtle)] py-[5px] last:border-0">
                <span className="min-w-0 truncate text-[10.5px] t-dim" title={p.note}>
                  {p.name.replace(/_/g, " ")}
                </span>
                <span className="mono flex shrink-0 items-center gap-1.5 text-[10.5px]">
                  <span className={p.slippage_source === "measured" ? "t-info" : "t-warn"}>
                    {bps(p.total_rt_bps)}
                  </span>
                  {!p.achievable && <span className="t-fail">✕</span>}
                </span>
              </div>
            ))}
          </div>
          <p className="mt-2 text-[10px] leading-snug t-faint">
            ✕ = reported but never usable to declare viability.
          </p>
          <ArtifactTrace className="mt-auto" artifact="docs/execution-cost-measurement-2026-09-25.json" />
        </Card>
      </div>
    </>
  );
}

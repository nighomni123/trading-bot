import PageHead from "@/components/PageHead";
import ArtifactTrace from "@/components/ArtifactTrace";
import EventWaterfall, { type Series } from "@/components/EventWaterfall";
import { Card, Stat, KV, Badge, Principle } from "@/components/UI";
import { loadStage11, loadCostMeasurement, canonicalCost } from "@/lib/data";
import { bps, bpsSigned, num, sigma } from "@/lib/format";

export const dynamic = "force-dynamic";

/** Order events by their largest absolute effect, strongest first. */
function buildSeries(s11: ReturnType<typeof loadStage11>): Series[] {
  const events = s11?.study?.events ?? {};
  return Object.entries(events)
    .map(([event, e]) => ({
      event,
      label: event.replace("EVENT_", ""),
      n: e.n_events,
      points: Object.entries(e.by_horizon).map(([h, cell]) => ({
        horizon: h,
        bps: cell.test.diff * 10_000,
        t: cell.test.t,
        n: cell.conditional.n,
      })),
    }))
    .sort((a, b) => {
      const mx = (s: Series) => Math.max(...s.points.map((p) => Math.abs(p.bps)));
      return mx(b) - mx(a);
    });
}

export default function MicrostructurePage() {
  const s11 = loadStage11();
  const cost = loadCostMeasurement();
  const costBps = canonicalCost(cost);
  const half = costBps / 2;
  const series = buildSeries(s11);
  const econ = s11?.economics?.rows ?? [];
  const best = econ.length
    ? econ.reduce((a, b) => (b.gross_expected_bps > a.gross_expected_bps ? b : a))
    : null;
  const events = s11?.study?.events ?? {};
  const flowRev = events.EVENT_FLOW_REVERSAL;
  const priceShock = events.EVENT_PRICE_SHOCK;
  const flowPeak = (flowRev?.by_horizon["60m"]?.test.diff ?? 0) * 10_000;

  return (
    <>
      <PageHead
        title="Microstructure Alpha Lab"
        stage="STAGE 11"
        principle="Separate statistical significance from economic significance. An effect with t = 3.5 that cannot pay for the spread is not an alpha; it is a curiosity."
        right={
          <div className="flex gap-1.5">
            <Badge tone="info">{Object.keys(events).length} EVENTS</Badge>
            <Badge tone="fail">{s11?.economics?.n_economically_viable ?? 0} VIABLE</Badge>
          </div>
        }
      />

      <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Card bodyClass="px-4 py-3">
          <Stat
            label="Round-trip cost"
            value={bps(costBps)}
            unit="bps"
            tone="fail"
            sub={`${bps(half)} bps needed per side`}
          />
        </Card>
        <Card bodyClass="px-4 py-3">
          <Stat
            label="Best gross effect"
            value={best ? bpsSigned(best.gross_expected_bps) : "—"}
            unit="bps"
            tone="warn"
            sub={best?.event.replace("EVENT_", "")}
          />
        </Card>
        <Card bodyClass="px-4 py-3">
          <Stat
            label="Best net after cost"
            value={best ? bpsSigned(best.net_expected_bps) : "—"}
            unit="bps"
            tone="fail"
            sub={best?.cost_multiple ? `${num(best.cost_multiple, 2)}× below cost` : undefined}
          />
        </Card>
        <Card bodyClass="px-4 py-3">
          <Stat
            label="Signal / horizon pairs"
            value={String(econ.length)}
            tone="info"
            sub={`${s11?.economics?.n_economically_viable ?? 0} economically viable`}
          />
        </Card>
      </div>

      <Card
        className="mb-4"
        eyebrow="Event study"
        title="Conditional forward returns by event and horizon"
        right={<Badge tone="mute">GREY BAR = LOSING · AMBER = BELOW COST</Badge>}
        bodyClass="px-3 pb-2"
      >
        <EventWaterfall series={series} costBps={costBps} height={320} />
        <p className="mt-1 text-[10.5px] leading-snug text-[#a3aeb9]">
          Bars are conditional-minus-unconditional forward returns in bps. The dashed lines mark
          ±{bps(half)} bps — the per-side share of the {bps(costBps)} bps round trip.{" "}
          <span className="text-[color:var(--color-ink-faint)]">
            No bar in any event, at any horizon, crosses either threshold.
          </span>
        </p>
        <ArtifactTrace artifact="docs/stage-11-results-2025-06.json" />
      </Card>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card eyebrow="Statistically real" title="FLOW_REVERSAL — significant but untradeable" bodyClass="px-4 pb-3">
          <p className="text-[11.5px] leading-relaxed text-[color:var(--color-ink-dim)]">
            The strongest statistical result in Stage 11. Conditional returns after a flow reversal
            are positive at four of six horizons, with |t| above 2.58 at 5m, 20m, 30m and 60m.
          </p>
          <div className="rule mt-2.5 pt-1.5">
            {Object.entries(flowRev?.by_horizon ?? {}).map(([h, c]) => (
              <KV
                key={h}
                k={`${h}  (n=${c.conditional.n.toLocaleString("en-US")})`}
                v={`${bpsSigned(c.test.diff * 10_000, 1)}  ${sigma(c.test.t)}`}
                tone={Math.abs(c.test.diff * 10_000) > half ? "pass" : c.test.diff > 0 ? "warn" : "fail"}
              />
            ))}
          </div>
          <p className="mt-2 border-t border-[color:var(--color-border-subtle)] pt-2 text-[11px] leading-relaxed text-[#a3aeb9]">
            The peak is <span className="mono text-ink">{bpsSigned(flowPeak)} bps</span> at 60m.
            Reaching the {bps(half)} bps breakeven would require roughly{" "}
            <span className="mono t-fail">{num(half / Math.max(flowPeak, 1e-9), 1)}×</span> the
            observed effect.
          </p>
          <ArtifactTrace className="mt-auto" artifact="docs/stage-11-results-2025-06.json" />
        </Card>

        <Card eyebrow="Decay, not edge" title="PRICE_SHOCK — real, but a decay signature" bodyClass="px-4 pb-3">
          <p className="text-[11.5px] leading-relaxed text-[color:var(--color-ink-dim)]">
            Price shocks show a large, highly significant negative conditional return that grows
            monotonically with horizon. This is the signature of volatility decay, not a tradeable
            directional edge of usable size.
          </p>
          <div className="rule mt-2.5 pt-1.5">
            {Object.entries(priceShock?.by_horizon ?? {}).map(([h, c]) => (
              <KV
                key={h}
                k={`${h}  (n=${c.conditional.n.toLocaleString("en-US")})`}
                v={`${bpsSigned(c.test.diff * 10_000, 1)}  ${sigma(c.test.t)}`}
                tone={c.test.diff < 0 ? "fail" : "dim"}
              />
            ))}
          </div>
          <p className="mt-2 border-t border-[color:var(--color-border-subtle)] pt-2 text-[11px] leading-relaxed text-[#a3aeb9]">
            At 240m the effect is{" "}
            <span className="mono t-fail">
              {bpsSigned((priceShock?.by_horizon["240m"]?.test.diff ?? 0) * 10_000)} bps
            </span>{" "}
            at |t| {num(Math.abs(priceShock?.by_horizon["240m"]?.test.t ?? 0), 2)} — but it is short
            volatility, not a position you can hold and win with.
          </p>
          <ArtifactTrace className="mt-auto" artifact="research/alphas/hypotheses/EXP-11-PRICE-SHOCK-REVERSION.json" />
        </Card>

        <Card eyebrow="Not measured" title="CVD divergence and the book" bodyClass="px-4 pb-3">
          <Principle>
            A dashboard must not render a statistic that was never computed.
          </Principle>
          <p className="mt-2.5 text-[11.5px] leading-relaxed text-[#a3aeb9]">
            The design brief asked for a CVD-vs-price divergence scatter with a regression line and
            R². <strong className="text-ink">No such statistic exists</strong> in any artifact in
            this repository. The features{" "}
            <span className="mono text-[10.5px] text-ink-dim">price_up_cvd_down</span> and{" "}
            <span className="mono text-[10.5px] text-ink-dim">price_down_cvd_up</span> are defined;
            no correlation, R² or sample size was ever reported for them.
          </p>
          <div className="rule mt-2.5 pt-1.5">
            <KV k="CVD divergence R²" v="NOT MEASURED" tone="warn" />
            <KV k="Correlation" v="NOT MEASURED" tone="warn" />
            <KV k="Order-book history" v="UNAVAILABLE (404)" tone="fail" />
            <KV k="Microprice features" v="UNTESTED" tone="warn" />
          </div>
          <p className="mt-2 text-[11px] leading-relaxed text-[#a3aeb9]">
            The Binance public archive serves no BTCUSDT <span className="mono">bookDepth</span>,{" "}
            <span className="mono">bookTicker</span> or <span className="mono">metrics</span>{" "}
            endpoint. Book-derived features are never imputed, so the most short-horizon-relevant
            family remains unmeasured.
          </p>
          <ArtifactTrace className="mt-auto" artifact="research/alphas/hypotheses/EXP-11-BOOK-MICROPRICE.json" />
        </Card>
      </div>
    </>
  );
}

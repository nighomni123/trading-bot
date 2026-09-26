import PageHead from "@/components/PageHead";
import ArtifactTrace from "@/components/ArtifactTrace";
import SeriesChart from "@/components/SeriesChart";
import { Card, Stat, KV, Badge } from "@/components/UI";
import {
  loadStage12Models, loadStage12Stability, loadStage12Economics,
  loadStateStudy, allStateRows, stage12Rows, loadStage12Coverage,
} from "@/lib/data";
import { bps, bpsSigned, num, sigma, nCompact } from "@/lib/format";
import { C } from "@/lib/theme";

export const dynamic = "force-dynamic";

const HORIZON_COLOR: Record<number, string> = { 4: C.info, 24: C.warn, 72: C.violet };

export default function HorizonPage() {
  const models = loadStage12Models();
  const stability = loadStage12Stability();
  const econ = loadStage12Economics();
  const study = loadStateStudy();
  const cov = loadStage12Coverage();

  const rows = stage12Rows(models);
  const horizons = [...new Set(rows.map((r) => r.horizon))].sort((a, b) => a - b);
  const years = ["2021", "2022", "2023", "2024", "2025"];

  const stateRows = allStateRows(study);
  const topStates = [...stateRows].sort((a, b) => b.diff_bps - a.diff_bps).slice(0, 6);

  // The one genuinely economic-sized structural effect: 24h momentum reversion.
  const momentum = stability?.["TREND_STATE=UP@24h"];
  const fundLow = stability?.["FUNDING_STATE=LOW@4h"];

  const bestOosAuc = rows.length
    ? rows.reduce((a, b) => (b.lgbm_auc > a.lgbm_auc ? b : a))
    : null;
  const decile24 = rows.filter((r) => r.horizon === 24);

  // Stability axis domain, derived from the values AND their +/-1 SE bands.
  const stabBounds = (() => {
    const vals: number[] = [];
    for (const key of ["TREND_STATE=UP@24h", "TREND_STATE=NEUTRAL@24h"] as const) {
      for (const y of years) {
        const r = stability?.[key]?.[y];
        if (!r) continue;
        vals.push(r.diff_bps - r.se_bps, r.diff_bps + r.se_bps);
      }
    }
    if (!vals.length) return { lo: -50, hi: 150 };
    const lo = Math.min(...vals);
    const hi = Math.max(...vals);
    const pad = (hi - lo) * 0.08;
    return { lo: lo - pad, hi: hi + pad };
  })();

  return (
    <>
      <PageHead
        title="Long-Horizon Structural Alpha"
        stage="STAGE 12"
        principle="Does BTCUSDT contain large, stable, predictable structure at 1h–72h horizons that makes execution costs manageable? Stage 12 changed only the horizon and feature families."
        right={
          <div className="flex gap-1.5">
            <Badge tone="warn">UNSTABLE</Badge>
            <Badge tone="info">{horizons.length} HORIZONS</Badge>
          </div>
        }
      />

      <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Card bodyClass="px-4 py-3">
          <Stat
            label="Best in-sample state edge"
            value={bpsSigned(topStates[0]?.diff_bps)}
            unit="bps"
            tone="warn"
            sub={`${topStates[0]?.state}=${topStates[0]?.level}@${topStates[0]?.horizon}`}
          />
        </Card>
        <Card bodyClass="px-4 py-3">
          <Stat
            label="Best OOS AUC"
            value={num(bestOosAuc?.lgbm_auc, 4)}
            tone="fail"
            sub={`${bestOosAuc?.horizon}h fold ${bestOosAuc?.fold}`}
          />
        </Card>
        <Card bodyClass="px-4 py-3">
          <Stat
            label="In-sample viable"
            value={`${econ?.in_sample_viable ?? "—"}/${econ?.in_sample_rows ?? "—"}`}
            tone="warn"
            sub="state rows clearing cost"
          />
        </Card>
        <Card bodyClass="px-4 py-3">
          <Stat
            label="OOS predictable edge"
            value="0.00"
            unit="bps"
            tone="fail"
            sub="all three horizons"
          />
        </Card>
      </div>

      <Card className="mb-4" eyebrow="Verdict" title="Interesting structural effects found — economically unstable" bodyClass="px-4 pb-3">
        <p className="text-[12px] leading-relaxed text-[color:var(--color-ink-dim)]">
          At longer horizons BTC shows{" "}
          <span className="mono t-warn">apparent</span> large conditional edges — up to{" "}
          <span className="mono text-ink">{bpsSigned(topStates[0]?.diff_bps)} bps</span> — that
          clear the {bps(econ?.cost_bps)} bps cost. Every one of them failed a stricter test.
          Behind the state framing, one genuinely economically-sized structural effect was isolated:{" "}
          <strong className="text-ink">24h momentum mean reversion</strong>. Its 2023 sign flip
          marks it UNSTABLE under the pre-declared Gate 2, so it is not promoted.
        </p>
        <p className="mt-2 border-l-2 border-[color:var(--color-border)] pl-3 text-[11.5px] leading-relaxed text-[#a3aeb9]">
          {econ?.conclusion}
        </p>
        <div className="mt-3 grid gap-3 sm:grid-cols-3">
          {horizons.map((h) => {
            const oos = (econ?.oos_reality_check as Record<string, { mean_oos_auc: number; oos_predictable_edge_bps: number }> | undefined)?.[`${h}h`];
            return (
              <div key={h} className="card-tight px-3 py-2">
                <div className="flex items-baseline justify-between">
                  <span className="mono text-[12px]" style={{ color: HORIZON_COLOR[h] }}>
                    {h}h
                  </span>
                  <span className="mono text-[11px] t-fail">edge {bps(oos?.oos_predictable_edge_bps)}</span>
                </div>
                <div className="mono mt-1 text-[15px] text-ink">{num(oos?.mean_oos_auc, 4)}</div>
                <div className="mono text-[9.5px] t-faint">mean OOS AUC · 4 folds</div>
              </div>
            );
          })}
        </div>
        <ArtifactTrace className="mt-auto" artifact="research/stage12/economics.json" />
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card eyebrow="Model" title="Out-of-sample AUC by fold and horizon" bodyClass="px-3 pb-2">
          <SeriesChart
            categories={["2022", "2023", "2024", "2025"]}
            yFormat="auc"
            height={250}
            min={0.455}
            max={0.56}
            series={horizons.map((h) => ({
              name: `${h}h AUC`,
              data: rows.filter((r) => r.horizon === h).sort((a, b) => a.fold.localeCompare(b.fold)).map((r) => r.lgbm_auc),
              color: HORIZON_COLOR[h],
              markLines: h === horizons[0] ? [{ y: 0.5, label: "random", color: C.warn }] : undefined,
            }))}
          />
          <p className="mt-0.5 text-[10.5px] leading-snug text-[#a3aeb9]">
            The 24h and 72h series fall <span className="mono t-fail">below 0.50</span> in later
            folds. A sub-chance AUC is not a weak model — it is an inverted one, the signature of
            non-stationarity rather than insufficient capacity. Every fold also recorded a{" "}
            <span className="mono">linear_error: ValueError</span>: the Ridge baseline failed to fit
            on all 12 folds.
          </p>
          <ArtifactTrace className="mt-auto" artifact="research/stage12/model_results.json" />
        </Card>

        <Card
          eyebrow="Ranking"
          title="24h decile means — does the model rank anything?"
          bodyClass="px-3 pb-2"
        >
          <SeriesChart
            categories={["D0", "D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8", "D9"]}
            yName="mean forward return (bps)"
            yFormat="bps1"
            height={250}
            min={-40}
            max={40}
            series={[
              {
                name: "2022",
                data: decile24.find((r) => r.fold === "2022")?.decile_means_bps ?? [],
                color: C.info,
                type: "bar",
              },
              {
                name: "2023",
                data: decile24.find((r) => r.fold === "2023")?.decile_means_bps ?? [],
                color: C.warn,
                type: "bar",
              },
              {
                name: "2024",
                data: decile24.find((r) => r.fold === "2024")?.decile_means_bps ?? [],
                color: C.fail,
                type: "bar",
              },
              {
                name: "2025",
                data: decile24.find((r) => r.fold === "2025")?.decile_means_bps ?? [],
                color: C.pass,
                type: "bar",
              },
            ]}
          />
          <p className="mt-0.5 text-[10.5px] leading-snug text-[#a3aeb9]">
            A working ranker produces a monotonic D0 → D9 ramp. These do not: the sign of the
            spread changes between years, which is the same instability the state study found.
          </p>
          <ArtifactTrace className="mt-auto" artifact="research/stage12/model_results.json" />
        </Card>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card
          eyebrow="Stability"
          title="24h trend-up edge by year, ±1 SE"
          bodyClass="px-3 pb-2"
        >
          <SeriesChart
            categories={years}
            yName="conditional − unconditional (bps)"
            yFormat="bps1"
            height={224}
            min={stabBounds.lo}
            max={stabBounds.hi}
            series={[
              {
                name: "TREND_STATE=UP@24h",
                data: years.map((y) => momentum?.[y]?.diff_bps ?? null),
                se: years.map((y) => momentum?.[y]?.se_bps ?? null),
                color: C.warn,
                markLines: [
                  { y: econ?.cost_bps ?? 11.006, label: `cost ${bps(econ?.cost_bps)}`, color: C.fail },
                ],
              },
              {
                name: "TREND_STATE=NEUTRAL@24h",
                data: years.map((y) => stability?.["TREND_STATE=NEUTRAL@24h"]?.[y]?.diff_bps ?? null),
                se: years.map((y) => stability?.["TREND_STATE=NEUTRAL@24h"]?.[y]?.se_bps ?? null),
                color: C.info,
              },
            ]}
          />
          <p className="mt-0.5 text-[10.5px] leading-snug text-[#a3aeb9]">
            Shaded ribbons are ±1 standard error. The per-year sample is small —{" "}
            {nCompact(momentum?.["2021"]?.n_cond ?? 0)}–{nCompact(momentum?.["2024"]?.n_cond ?? 0)}{" "}
            conditioned observations per year — so the ribbon is wide and the estimate is weak.
          </p>
          <ArtifactTrace className="mt-auto" artifact="research/stage12/stability.json" />
        </Card>

        <Card eyebrow="Per-year detail" title="TREND_STATE=UP@24h" bodyClass="px-4 pb-3">
          <div>
            {years.map((y) => (
              <KV
                key={y}
                k={`${y}  (n=${nCompact(momentum?.[y]?.n_cond ?? 0)})`}
                v={`${bpsSigned(momentum?.[y]?.diff_bps)} bps  ${sigma(momentum?.[y]?.t)}`}
                tone={(momentum?.[y]?.diff_bps ?? 0) > (econ?.cost_bps ?? 0) ? "pass" : "warn"}
              />
            ))}
          </div>
          <div className="rule mt-2 pt-1.5">
            <KV k="Cost to clear" v={`${bps(econ?.cost_bps)} bps`} tone="fail" />
            <KV k="Years above cost" v="4 of 5" tone="warn" />
            <KV k="FUNDING_STATE=LOW@4h" v={`${bpsSigned(fundLow?.["2021"]?.diff_bps)} bps (2021)`} tone="warn" />
          </div>
          <p className="mt-2 text-[10.5px] leading-snug text-[#a3aeb9]">
            Gate 2 was pre-declared: a significant sign flip in any year marks the candidate
            UNSTABLE. It is applied here, and the candidate is not promoted.
          </p>
          <ArtifactTrace className="mt-auto" artifact="research/stage12/stability.json" />
        </Card>

        <Card eyebrow="Coverage" title="What is missing" bodyClass="px-4 pb-3">
          <div className="grid grid-cols-2 gap-3 pb-2">
            <Stat
              label="Rows"
              value={cov ? nCompact(cov.rows) : "—"}
              tone="info"
              sub={`${cov?.first_timestamp.slice(0, 7)} → ${cov?.last_timestamp.slice(0, 7)}`}
            />
            <Stat label="Gaps" value={String(cov?.missing_bars ?? "—")} tone="pass" sub="0 duplicate timestamps" />
          </div>
          <div className="border-t border-[color:var(--color-border-subtle)] pt-1.5">
            <KV k="Trade flow" v="Jun 2025 only (30d)" tone="warn" />
            <KV k="Order book" v="UNAVAILABLE (404)" tone="fail" />
            <KV k="Model features" v="85" tone="info" />
            <KV k="Horizons" v="8 (1h → 72h)" tone="info" />
            <KV k="Dataset hash" v={cov?.dataset_hash.slice(0, 12) ?? "—"} />
          </div>
          <p className="mt-2 text-[10.5px] leading-snug text-[#a3aeb9]">
            Non-overlapping decision grids are used per horizon — at 72h a 4320-bar overlap would
            inflate raw t-stats enormously, so effective n is measured on-grid, not raw. Order-book
            structure is never imputed, and trade flow is not multi-year.
          </p>
          <ArtifactTrace className="mt-auto" artifact="research/stage12/data_coverage.json" generated={cov?.generated_at} />
        </Card>
      </div>

      <Card className="mt-4" eyebrow="FDR" title="Largest in-sample state candidates" right={<Badge tone="warn">IN-SAMPLE ONLY — NOT TRADEABLE</Badge>} bodyClass="px-4 pb-3">
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead>
              <tr className="rule">
                <th className="eyebrow py-1.5 pr-3 font-normal">State</th>
                <th className="eyebrow py-1.5 pr-3 font-normal">Horizon</th>
                <th className="eyebrow py-1.5 pr-3 text-right font-normal">Edge bps (in-sample)</th>
                <th className="eyebrow py-1.5 pr-3 text-right font-normal">t</th>
                <th className="eyebrow py-1.5 pr-3 text-right font-normal">q (BH)</th>
                <th className="eyebrow py-1.5 pr-3 text-right font-normal">n cond</th>
                <th className="eyebrow py-1.5 text-right font-normal">vs cost</th>
              </tr>
            </thead>
            <tbody>
              {topStates.map((s, i) => {
                const cost = econ?.cost_bps ?? 11.006;
                const clears = s.diff_bps > cost;
                return (
                  <tr key={`${s.state}-${s.level}-${s.horizon}-${i}`} className="rule">
                    <td className="mono py-1.5 pr-3 text-[10.5px] text-ink">
                      {s.state}={s.level}
                    </td>
                    <td className="mono py-1.5 pr-3 text-[10.5px] t-dim">{s.horizon}</td>
                    <td className={`mono py-1.5 pr-3 text-right text-[10.5px] ${s.q_bh < 0.1 ? "t-info" : "t-dim"}`}>
                      {bpsSigned(s.diff_bps)}
                    </td>
                    <td className="mono py-1.5 pr-3 text-right text-[10.5px] t-dim">{sigma(s.t)}</td>
                    <td className={`mono py-1.5 pr-3 text-right text-[10.5px] ${s.q_bh < 0.1 ? "t-pass" : "t-faint"}`}>
                      {num(s.q_bh, 3)}
                    </td>
                    <td className="mono py-1.5 pr-3 text-right text-[10.5px] t-dim">{nCompact(s.n_cond)}</td>
                    <td className={`mono py-1.5 text-right text-[10.5px] ${clears ? "t-warn" : "t-fail"}`}>
                      {bpsSigned(s.diff_bps - cost)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-[10.5px] leading-snug text-[#a3aeb9]">
          20 hypotheses per horizon were tested. Only 3 survive FDR at 8h–72h where the raw edges
          are largest — the largest apparent edges carry the weakest significance, which is what
          multiple-testing correction exists to surface.
        </p>
        <ArtifactTrace className="mt-auto" artifact="research/stage12/state_study.json" />
      </Card>
    </>
  );
}

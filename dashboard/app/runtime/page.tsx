import PageHead from "@/components/PageHead";
import ArtifactTrace from "@/components/ArtifactTrace";
import SeriesChart from "@/components/SeriesChart";
import { Card, Stat, KV, Badge } from "@/components/UI";
import {
  loadArmAMetrics, loadArmCMetrics, loadArmCManifest, readDoc,
} from "@/lib/data";
import { num, nCompact } from "@/lib/format";
import { C } from "@/lib/theme";

export const dynamic = "force-dynamic";

/** Pre-flight doctor checks, verbatim from the Stage 13 shadow run report §3. */
const DOCTOR: { check: string; result: "PASS" | "WARN" }[] = [
  { check: "execution mode is PAPER", result: "PASS" },
  { check: "live order capability absent", result: "PASS" },
  { check: "paper executor / policy / risk operational", result: "PASS" },
  { check: "ledger + checkpoint writable", result: "PASS" },
  { check: "public Binance 1m data", result: "PASS" },
  { check: "public WebSocket connectivity", result: "PASS" },
  { check: "system clock sanity", result: "PASS" },
  { check: "1m bar continuity", result: "PASS" },
  { check: "primary source health", result: "PASS" },
  { check: "book freshness / depth notional", result: "PASS" },
  { check: "Frontier provider configured and reachable", result: "PASS" },
  { check: "Jev provider configured and reachable", result: "PASS" },
  { check: "open interest / funding", result: "PASS" },
  { check: "secondary Bybit source", result: "WARN" },
];

/** Mean stage latency, from the Stage 13 shadow run report §13. */
const LATENCY: { stage: string; mean: number; max: number }[] = [
  { stage: "data fetch", mean: 380, max: 7300 },
  { stage: "environment", mean: 45, max: 180 },
  { stage: "ledger write", mean: 2, max: 4 },
  { stage: "quant", mean: 0.4, max: 0.9 },
  { stage: "risk", mean: 0.08, max: 0.1 },
  { stage: "policy", mean: 0.05, max: 0.1 },
];

/** Failure modes F1–F9 observed during the shadow run. */
const FAILURES: { id: string; text: string }[] = [
  { id: "F1", text: "decision timestamp read before ingestion" },
  { id: "F2", text: "retransmitted events fail-closed" },
  { id: "F3", text: "one missing historical kline disables trading" },
  { id: "F4", text: "incomplete bucket materialized as complete" },
  { id: "F5", text: "slippage estimate overwritten with configured cost" },
  { id: "F6", text: "kill switch blocks risk-reducing exits" },
  { id: "F7", text: "15 s poll produced 4 decisions per minute" },
  { id: "F8", text: "heartbeat freshness reports a lagging venue as primary" },
  { id: "F9", text: "ledger and checkpoint paths could diverge" },
];

export default function RuntimePage() {
  const armA = loadArmAMetrics();
  const armC = loadArmCMetrics();
  const manifest = loadArmCManifest();
  const report = readDoc("docs/stage-13-shadow-run-report.md") ?? "";
  const readiness = report.match(/NOT READY for live capital/i) ? "NOT READY" : "UNKNOWN";

  const decisionsA = armA?.decisions_total ?? 0;
  const decisionsC = armC?.decisions_total ?? 0;
  return (
    <>
      <PageHead
        title="Shadow Runtime Telemetry"
        stage="STAGE 13"
        principle="The experiment asks whether the system behaves correctly. It does not ask whether the strategy makes money — and a zero-trade window proves nothing about edge."
        right={
          <div className="flex gap-1.5">
            <Badge tone="pass">PAPER</Badge>
            <Badge tone="mute">LIVE ORDERS DISABLED</Badge>
            <Badge tone="fail">{readiness} FOR CAPITAL</Badge>
          </div>
        }
      />

      <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Card bodyClass="px-4 py-3">
          <Stat
            label="Decisions (Arm A + C)"
            value={String(decisionsA + decisionsC)}
            tone="info"
            sub={`${decisionsA} arm A · ${decisionsC} arm C`}
          />
        </Card>
        <Card bodyClass="px-4 py-3">
          <Stat
            label="Risk rejections"
            value={String((armA?.risk_rejected ?? 0) + (armC?.risk_rejected ?? 0))}
            tone="warn"
            sub="no approved action to size"
          />
        </Card>
        <Card bodyClass="px-4 py-3">
          <Stat label="Fills / trades" value="0 / 0" tone="dim" sub="0.00% realized P&L" />
        </Card>
        <Card bodyClass="px-4 py-3">
          <Stat
            label="Doctor verdict"
            value="READY"
            tone="pass"
            sub="13 PASS · 1 WARN"
          />
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card
          eyebrow="Pre-flight"
          title="Shadow doctor"
          right={<Badge tone="pass">SHADOW RUN READY</Badge>}
          bodyClass="px-4 pb-3"
        >
          <div>
            {DOCTOR.map((d) => (
              <div key={d.check} className="flex items-center justify-between gap-2 border-b border-[color:var(--color-border-subtle)] py-[3.5px] last:border-0">
                <span className="min-w-0 truncate text-[10.5px] t-dim">{d.check}</span>
                <span className={`mono shrink-0 text-[9.5px] ${d.result === "PASS" ? "t-pass" : "t-warn"}`}>
                  {d.result}
                </span>
              </div>
            ))}
          </div>
          <p className="mt-2 text-[10.5px] leading-snug text-[#a3aeb9]">
            <span className="t-warn">WARN</span> on the secondary Bybit source is expected and
            optional: it occasionally runs tens of seconds behind the primary venue.
          </p>
          <ArtifactTrace className="mt-auto" artifact="docs/stage-13-shadow-run-report.md" />
        </Card>

        <Card
          eyebrow="Latency"
          title="Stage timings (ms)"
          right={<Badge tone="mute">MEAN / MAX</Badge>}
          bodyClass="px-3 pb-2"
        >
          <SeriesChart
            categories={LATENCY.map((l) => l.stage)}
            yName="milliseconds (log)"
            yFormat="int"
            yType="log"
            min={0.05}
            max={10000}
            height={228}
            series={[
              // Scatter, not bars: a log-scaled bar invents a false zero baseline.
              { name: "mean", data: LATENCY.map((l) => l.mean), color: C.info, type: "scatter" },
              { name: "max (risk bound)", data: LATENCY.map((l) => l.max), color: C.violet, type: "scatter" },
            ]}
          />
          <p className="mt-0.5 text-[10.5px] leading-snug text-[#a3aeb9]">
            The real provider call dominates everything else: Frontier observed at{" "}
            <span className="mono t-warn">~12.4 s</span> mean against a 30 s configured bound.
            Deterministic components — quant, policy, risk — total under 0.6 ms.
          </p>
          <div className="mt-2">
            <KV k="Poll interval" v="15 s" tone="info" />
            <KV k="Frontier cadence" v="900 s floor, 4/h cap" tone="info" />
            <KV k="Jev validity" v="60 s, 60/h cap" tone="info" />
          </div>
          <ArtifactTrace className="mt-auto" artifact="docs/stage-13-shadow-run-report.md" />
        </Card>

        <Card eyebrow="Ledger" title="Hash-chained audit trail" bodyClass="px-4 pb-3">
          <div className="grid grid-cols-2 gap-3 pb-2">
            <Stat label="Integrity" value="VERIFIED" tone="pass" sub="chain intact" />
            <Stat
              label="Duplicate ids"
              value="0"
              tone="pass"
              sub="unsafe decisions: 0"
            />
          </div>
          <div className="border-t border-[color:var(--color-border-subtle)] pt-1.5">
            <KV k="Linkage" v="Decision → Intent → Fill → Trade" tone="info" />
            <KV k="Checkpoint" v="temp + fsync + atomic replace" />
            <KV k="Cross-experiment restore" v="hard fail" tone="pass" />
            <KV k="Restart recovery" v="12 decisions restored, FLAT, $10,000" tone="pass" />
          </div>
          <p className="mt-2 text-[10.5px] leading-snug text-[#a3aeb9]">
            Fills cannot exist without an intent; trades cannot exist without fills. Tampering
            fails closed. The report cites 8 decisions for Arm C while{" "}
            <span className="mono">decisions.jsonl</span> holds{" "}
            <span className="mono text-ink">{nCompact(decisionsC)}</span> — the ledger is the
            authority, and the report undercounts it.
          </p>
          <ArtifactTrace className="mt-auto" artifact="research/runtime/shadow-demo-arm-c/manifest.json" generated={manifest?.frozen_at} />
        </Card>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card eyebrow="Provenance" title="Frozen run manifest" bodyClass="px-4 pb-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <KV k="Experiment" v={manifest?.experiment_id ?? "—"} tone="info" />
              <KV k="Arm" v={manifest?.arm ?? "—"} />
              <KV k="Run mode" v={manifest?.run_mode ?? "—"} tone="pass" />
              <KV k="Code version" v={manifest?.code_version ?? "—"} />
              <KV k="Config hash" v={manifest?.config_hash?.slice(0, 16) ?? "—"} />
            </div>
            <div>
              <KV k="Frontier model" v={(manifest?.frontier_model ?? "—").split(":").pop() ?? "—"} />
              <KV k="Jev model" v={(manifest?.jev_model ?? "—").split(":").pop() ?? "—"} />
              <KV k="Ledger root" v={manifest?.ledger_root ?? "—"} />
              <KV k="Frozen at" v={manifest?.frozen_at?.slice(0, 19).replace("T", " ") ?? "—"} />
              <KV k="Git commit" v={manifest?.git_commit?.slice(0, 12) ?? "—"} />
            </div>
          </div>
          <p className="mt-2.5 border-t border-[color:var(--color-border-subtle)] pt-2 text-[10.5px] leading-snug text-[#a3aeb9]">
            The manifest is written at run start and never rewritten for the life of the run. Both
            arms use separate experiment ids and separate ledgers because they are different
            configurations, and they cover different market windows —{" "}
            <strong className="text-ink">no A/B performance comparison is claimed</strong>.
          </p>
          <ArtifactTrace className="mt-auto" artifact="research/runtime/shadow-demo-arm-c/manifest.json" />
        </Card>

        <Card eyebrow="Hardening" title="Failure modes exercised" bodyClass="px-4 pb-3">
          <div className="grid gap-x-4 gap-y-1 sm:grid-cols-2">
            {FAILURES.map((f) => (
              <div key={f.id} className="flex gap-2 py-[3px]">
                <span className="mono shrink-0 text-[9.5px] t-warn">{f.id}</span>
                <span className="text-[10.5px] leading-snug t-dim">{f.text}</span>
              </div>
            ))}
          </div>
          <p className="mt-2.5 border-t border-[color:var(--color-border-subtle)] pt-2 text-[10.5px] leading-snug text-[#a3aeb9]">
            F5 is the important one: the runner overwrites{" "}
            <span className="mono">estimated_slippage</span> with the configured cost assumption,
            so the risk slippage gate could never observe the market. That was classified{" "}
            <span className="mono t-fail">U1</span> in the pre-live audit and fixed before this run.
          </p>
          <div className="mt-2">
            <KV k="Unsafe findings fixed" v="8 (U1–U8)" tone="pass" />
            <KV k="Live-intelligence tests" v="134 green" tone="pass" />
            <KV k="Full suite" v="404 passed" tone="pass" />
          </div>
          <ArtifactTrace className="mt-auto" artifact="docs/stage-13-runtime-audit.md" />
        </Card>
      </div>

      <Card className="mt-4" eyebrow="What this does not show" title="Remaining risks" bodyClass="px-4 pb-3">
        <ol className="grid gap-2 sm:grid-cols-2">
          {[
            "Provider calls block the poll thread — Frontier at 12.4 s against a 15 s poll interval leaves no headroom.",
            "Book gaps are not detected: depth5@100ms carries no per-update sequence, so a proper @depth diff stream with pu/U validation is still needed.",
            "Funding is DISABLED and explicitly reported; an overnight run crossing 00:00/08:00/16:00 UTC will understate costs.",
            "Arm A and Arm C never ran over the same market window, so no ablation comparison exists.",
            "The demo arm may produce no trades for a long time without any signal failure.",
            "One process, one thread — no supervision, no watchdog, no restart-on-crash.",
          ].map((r, i) => (
            <li key={i} className="flex gap-2.5 text-[10.5px] leading-snug">
              <span className="mono shrink-0 text-[9.5px] t-faint">{String(i + 1).padStart(2, "0")}</span>
              <span className="text-[#a3aeb9]">{r}</span>
            </li>
          ))}
        </ol>
        <p className="mt-3 border-l-2 border-[color:var(--color-fail)] pl-3 text-[11px] leading-relaxed text-[#a3aeb9]">
          <strong className="text-ink">DEMONSTRATION SAMPLE — NOT PERFORMANCE VALIDATION.</strong>{" "}
          One session of ten minutes proves integration, not safety under prolonged failure, nor any
          economic property. Readiness: ready for a longer frozen shadow run;{" "}
          <span className="t-fail">not ready for live capital</span>.
        </p>
        <ArtifactTrace className="mt-auto" artifact="docs/stage-13-shadow-run-report.md" />
      </Card>
    </>
  );
}
